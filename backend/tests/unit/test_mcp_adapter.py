"""The MCP adapter's own decisions on top of the MCP SDK: the token check it gives the SDK,
the transport's allowed hosts and origins, the tool registry and structured tool errors.

Tokens come from the signed fixture issuer with an in-test key set, and tenant resolution is
replaced by a fixed caller, so no identity provider or database runs.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError

from property_focus.adapters.auth import oidc
from property_focus.adapters.auth.oidc import FIXTURE_ISSUER, OidcVerifier, fixture_verifier
from property_focus.adapters.db import create_database_engine
from property_focus.adapters.mcp.server import (
    CallerAccessToken,
    IdentityUnavailableError,
    SharedTokenVerifier,
    Tool,
    auth_settings,
    create_app,
    transport_security,
)
from property_focus.application.context import ExecutionContext
from property_focus.config import Settings
from property_focus.domain.errors import CodedError

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY_ID = "fixture-key"
SKEW_SECONDS = 60
CALLER = ExecutionContext(
    principal_id=uuid.uuid4(),
    tenant_id=uuid.uuid4(),
    entitlement_ids=(),
    correlation_id="c-1",
    roles=frozenset(),
)
MCP_URL = "http://localhost:8001/mcp"


def settings(**overrides: Any) -> Settings:
    return Settings.model_validate(
        {"environment": "development", "fixture_issuer_enabled": True, **overrides}
    )


def sign(**overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": FIXTURE_ISSUER,
        "sub": "subject-1",
        "aud": "property-focus-api",
        "typ": "Bearer",
        "scope": "openid email",
        "iat": now,
        "exp": now + 300,
        **overrides,
    }
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": KEY_ID})


def key_set() -> dict[str, Any]:
    jwk = RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
    return {"keys": [{**jwk, "kid": KEY_ID, "use": "sig", "alg": "RS256"}]}


def unavailable_key_set() -> dict[str, Any]:
    raise CodedError("dependency_transient", "The identity provider is unavailable.")


@pytest.fixture
def verifier() -> OidcVerifier:
    return fixture_verifier(settings(), key_set)


@pytest.fixture
def engine() -> Iterator[Engine]:
    """A pool that is never connected: tenant resolution is replaced below."""
    engine = create_database_engine("postgresql+psycopg://pf_runtime@127.0.0.1:1/pf")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def fixed_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every verified token resolves to ``CALLER``, without a database."""
    monkeypatch.setattr(oidc, "resolve_context", lambda engine, identity, **_: CALLER)


def verify_token(token_verifier: SharedTokenVerifier, token: str) -> CallerAccessToken | None:
    return asyncio.run(token_verifier.verify_token(token))


class TestSharedTokenVerifier:
    def test_valid_token_gives_an_access_token_carrying_the_caller(
        self, verifier: OidcVerifier, engine: Engine
    ) -> None:
        token = sign()
        token_verifier = SharedTokenVerifier(
            verifier, lambda: engine, clock_skew_seconds=SKEW_SECONDS
        )

        access = verify_token(token_verifier, token)

        assert access is not None
        assert access.context == CALLER
        assert access.token == token
        assert access.scopes == ["openid", "email"]
        exp = jwt.decode(token, options={"verify_signature": False})["exp"]
        assert access.expires_at == exp + SKEW_SECONDS

    @pytest.mark.parametrize(
        "token",
        [
            pytest.param("not-a-token", id="malformed"),
            pytest.param(sign(aud="some-other-api"), id="wrong-audience"),
            pytest.param(sign(exp=int(time.time()) - 3600), id="expired"),
        ],
    )
    def test_invalid_token_gives_none(
        self, verifier: OidcVerifier, engine: Engine, token: str
    ) -> None:
        token_verifier = SharedTokenVerifier(
            verifier, lambda: engine, clock_skew_seconds=SKEW_SECONDS
        )

        assert verify_token(token_verifier, token) is None

    def test_unreachable_identity_provider_raises_identity_unavailable(
        self, engine: Engine
    ) -> None:
        verifier = fixture_verifier(settings(), unavailable_key_set)
        token_verifier = SharedTokenVerifier(
            verifier, lambda: engine, clock_skew_seconds=SKEW_SECONDS
        )

        with pytest.raises(IdentityUnavailableError):
            verify_token(token_verifier, sign())

    def test_unreachable_database_raises_identity_unavailable(
        self, verifier: OidcVerifier, engine: Engine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unreachable(*args: Any, **kwargs: Any) -> ExecutionContext:
            raise OperationalError("SELECT 1", None, Exception("connection refused"))

        monkeypatch.setattr(oidc, "resolve_context", unreachable)
        token_verifier = SharedTokenVerifier(
            verifier, lambda: engine, clock_skew_seconds=SKEW_SECONDS
        )

        with pytest.raises(IdentityUnavailableError):
            verify_token(token_verifier, sign())


class TestConfiguration:
    def test_sdk_resource_check_is_off_because_oidc_checks_the_audience(self) -> None:
        configured = settings()

        auth = auth_settings(configured, configured.mcp_endpoint())

        assert auth.validate_token_resource is False
        assert str(auth.issuer_url) == configured.oidc_issuer
        assert str(auth.resource_server_url) == MCP_URL

    def test_transport_security_allows_exactly_the_configured_hosts_and_origins(self) -> None:
        endpoint = settings(
            mcp_allowed_hosts="app.example.test",
            mcp_allowed_origins="https://app.example.test",
        ).mcp_endpoint()

        security = transport_security(endpoint)

        assert security.enable_dns_rebinding_protection is True
        assert security.allowed_hosts == ["app.example.test"]
        assert security.allowed_origins == ["https://app.example.test"]


class TestLogging:
    def test_sdk_transport_logs_only_warnings_and_errors(
        self, verifier: OidcVerifier, engine: Engine, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.INFO)
        app = create_app(settings(), engine=engine, verifier=verifier)
        listing = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}

        with TestClient(app, base_url="http://localhost:8001") as client:
            response = client.post(
                "/mcp",
                json=listing,
                headers={"Authorization": f"Bearer {sign()}", "Accept": "application/json"},
            )

        assert response.status_code == 200
        transport = logging.getLogger("mcp.server.streamable_http")
        assert not [
            record.getMessage()
            for record in caplog.records
            if record.name == transport.name and record.levelno < logging.WARNING
        ]
        assert transport.getEffectiveLevel() == logging.WARNING


class _Arguments(BaseModel):
    run_id: uuid.UUID


class _Result(BaseModel):
    ok: bool


def _fail(context: ExecutionContext, engine: Engine, arguments: _Arguments) -> _Result:
    raise CodedError("not_found", "No workflow run has this ID.")


FAILING_TOOL = Tool(
    name="get_workflow_run",
    description="Always fails.",
    arguments=_Arguments,
    result=_Result,
    run=_fail,
)


class TestTools:
    def test_duplicate_tool_names_are_refused(self, verifier: OidcVerifier) -> None:
        with pytest.raises(ValueError, match="unique"):
            create_app(settings(), verifier=verifier, tools=[FAILING_TOOL, FAILING_TOOL])

    @pytest.mark.parametrize(
        ("arguments", "expected"),
        [
            pytest.param(
                {"run_id": str(uuid.uuid4())},
                {"code": "not_found", "detail": "No workflow run has this ID."},
                id="coded-error",
            ),
            pytest.param(
                {"run_id": "x"},
                {"code": "invalid_input", "detail": "Invalid arguments: run_id."},
                id="invalid-arguments",
            ),
        ],
    )
    def test_failed_call_is_a_structured_tool_error(
        self,
        verifier: OidcVerifier,
        engine: Engine,
        arguments: dict[str, Any],
        expected: dict[str, str],
    ) -> None:
        app = create_app(settings(), engine=engine, verifier=verifier, tools=[FAILING_TOOL])
        call = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "get_workflow_run", "arguments": arguments},
        }

        with TestClient(app, base_url="http://localhost:8001") as client:
            response = client.post(
                "/mcp",
                json=call,
                headers={"Authorization": f"Bearer {sign()}", "Accept": "application/json"},
            )

        result = response.json()["result"]
        assert result["isError"] is True
        assert result["content"] == [{"type": "text", "text": json.dumps(expected)}]

    @pytest.mark.parametrize("protocol_version", ["2025-06-18", "2025-11-25", "2026-07-28"])
    def test_unexpected_tool_failure_reaches_the_client_only_as_a_generic_internal_error(
        self,
        verifier: OidcVerifier,
        engine: Engine,
        protocol_version: str,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        app = create_app(settings(), engine=engine, verifier=verifier, tools=[CRASHING_TOOL])
        params: dict[str, Any] = {
            "name": "get_workflow_run",
            "arguments": {"run_id": str(uuid.uuid4())},
        }
        headers = {
            "Authorization": f"Bearer {sign()}",
            "Accept": "application/json",
            "MCP-Protocol-Version": protocol_version,
        }
        if protocol_version == "2026-07-28":
            params["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": protocol_version,
                "io.modelcontextprotocol/clientCapabilities": {},
            }
            headers |= {"Mcp-Method": "tools/call", "Mcp-Name": "get_workflow_run"}
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}

        with TestClient(app, base_url="http://localhost:8001") as client:
            response = client.post("/mcp", json=call, headers=headers)

        assert LEAKED_VALUE not in response.text
        error = response.json()["error"]
        assert error["code"] == -32603
        assert error["message"] == "Internal server error"
        (logged,) = [r for r in caplog.records if r.name == "property_focus.adapters.mcp.server"]
        assert logged.levelname == "ERROR"
        assert logged.exc_info is not None
        assert logged.exc_info[0] is RuntimeError


# A value the failure message carries that must never reach the client.
LEAKED_VALUE = "not-for-the-wire"


def _crash(context: ExecutionContext, engine: Engine, arguments: _Arguments) -> _Result:
    raise RuntimeError(f"could not connect to postgresql://pf:{LEAKED_VALUE}@db:5432/pf")


CRASHING_TOOL = Tool(
    name="get_workflow_run",
    description="Always crashes.",
    arguments=_Arguments,
    result=_Result,
    run=_crash,
)
