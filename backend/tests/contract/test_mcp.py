"""The MCP adapter's contract: OAuth discovery, sign-in by a dynamically registered generic
client against the local Keycloak realm, the shared token check, the stateless transport for
both protocol eras (the initialize-based revisions and 2026-07-28), and result parity with
REST for the same principal.

The MCP SDK defines the wire details, so these tests assert what the MCP specification
requires (status codes, JSON-RPC error codes, the fields a client relies on), not message
texts or exact header strings.

``assert_rest_mcp_parity`` is the parity harness: each MCP tool gets one case that runs its
use case through REST and MCP and compares the results.
"""

import base64
import json
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import Engine, select
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from support.keycloak import ISSUER, LocalKeycloak, TestUser, Tokens

from contract.mcp_client import (
    MODERN_PROTOCOL_VERSION,
    McpClient,
    authorization_server_metadata_urls,
    challenge_parameters,
)
from property_focus.adapters.auth.oidc import OidcVerifier, verifier_for
from property_focus.adapters.db.models import Tenant, User, WorkflowRun, WorkflowStep
from property_focus.adapters.db.repositories import ServiceScope, TenantScope, UnitOfWork
from property_focus.adapters.mcp.server import Tool, create_app
from property_focus.adapters.rest import schemas
from property_focus.application.context import ExecutionContext
from property_focus.application.workflows import read_workflow_run, utc_now
from property_focus.config import Settings

API_AUDIENCE = "property-focus-api"
MCP_AUDIENCE = "property-focus-mcp"
# What an initialize request names besides the protocol version, as every revision requires.
CLIENT_HELLO = {"capabilities": {}, "clientInfo": {"name": "generic-test-client", "version": "0"}}


def realm_client(keycloak: LocalKeycloak, client_id: str) -> dict[str, Any]:
    """The admin representation of the realm client ``client_id``."""
    (client,) = keycloak.admin.get(f"/clients?clientId={client_id}")
    representation: dict[str, Any] = client
    return representation


def tenant_of(engine: Engine, tokens: Tokens) -> TenantScope | None:
    """The tenant provisioned for the subject of ``tokens``, or None."""
    with UnitOfWork(engine, ServiceScope()) as uow:
        row = uow.session.execute(
            select(User.id, Tenant.id)
            .join(Tenant, Tenant.owner_user_id == User.id)
            .where(User.issuer == ISSUER, User.subject == tokens.access_claims["sub"])
        ).one_or_none()
    return None if row is None else TenantScope(tenant_id=row[1], user_id=row[0])


def test_unauthenticated_request_gets_a_challenge_naming_the_resource_metadata(
    mcp: TestClient, mcp_client: McpClient
) -> None:
    response = mcp_client.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    challenge = challenge_parameters(response.headers["www-authenticate"])
    metadata = mcp.get(challenge["resource_metadata"])
    root_metadata = mcp.get("/.well-known/oauth-protected-resource")

    assert response.status_code == 401
    assert challenge["resource_metadata"] == (
        "http://testserver/.well-known/oauth-protected-resource/mcp"
    )
    assert metadata.status_code == root_metadata.status_code == 200
    assert metadata.json()["resource"] == "http://testserver/mcp"
    assert metadata.json()["authorization_servers"] == [ISSUER]
    assert metadata.json()["bearer_methods_supported"] == ["header"]
    assert root_metadata.json() == metadata.json()


@pytest.mark.parametrize(
    "authorization", ["Bearer not-a-token", "Basic dXNlcjpwYXNz", "Bearer ", "Bearer"]
)
def test_missing_or_malformed_credential_is_challenged(
    mcp_client: McpClient, authorization: str
) -> None:
    response = mcp_client.post(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, Authorization=authorization
    )

    assert response.status_code == 401
    challenge = challenge_parameters(response.headers["www-authenticate"])
    assert challenge["resource_metadata"].endswith("/.well-known/oauth-protected-resource/mcp")


def test_dynamically_registered_generic_client_signs_in_and_calls_mcp(
    keycloak: LocalKeycloak, mcp_client: McpClient, user: TestUser, runtime_engine: Engine
) -> None:
    discovery = mcp_client.discover()
    server = discovery.authorization_server
    client_id = mcp_client.register(discovery)
    tokens = mcp_client.authorize(discovery, client_id, user)

    initialized = mcp_client.initialize(tokens.access_token)
    listed = mcp_client.request("tools/list", token=tokens.access_token)

    # Keycloak serves OAuth metadata with the issuer path inserted, the first form tried.
    assert server.issuer == ISSUER
    assert server.metadata_url == authorization_server_metadata_urls(ISSUER)[0]
    assert server.metadata["registration_endpoint"] == (
        f"{ISSUER}/clients-registrations/openid-connect"
    )
    assert realm_client(keycloak, client_id)["publicClient"] is True
    assert tokens.access_claims["azp"] == client_id
    assert MCP_AUDIENCE in tokens.access_claims["aud"]
    assert API_AUDIENCE not in tokens.access_claims["aud"]
    assert initialized["protocolVersion"] == "2025-11-25"
    assert initialized["capabilities"] == {"tools": {"listChanged": False}}
    assert initialized["serverInfo"]["name"] == "property-focus"
    assert listed == {"tools": []}
    # The MCP call went through the shared tenant resolution.
    assert tenant_of(runtime_engine, tokens) is not None


def test_metadata_offers_registration_without_metadata_documents_and_resource_keeps_the_audience(
    keycloak: LocalKeycloak, mcp_client: McpClient, user: TestUser
) -> None:
    discovery = mcp_client.discover()
    client_id = mcp_client.register(discovery)
    tokens = mcp_client.authorize(discovery, client_id, user)

    for metadata in (discovery.authorization_server.metadata, keycloak.realm.discovery):
        assert metadata["registration_endpoint"] == f"{ISSUER}/clients-registrations/openid-connect"
        assert "client_id_metadata_document_supported" not in metadata
    assert discovery.authorization_server.metadata_url == (
        "http://localhost:8080/.well-known/oauth-authorization-server/realms/property-focus"
    )
    assert tokens.access_claims["aud"] == MCP_AUDIENCE


@pytest.fixture
def strict_clock_mcp_client(
    settings: Settings,
    runtime_engine: Engine,
    mcp_client_for: Callable[[TestClient], McpClient],
) -> Iterator[McpClient]:
    """A generic client for an MCP application that allows no clock skew."""
    strict = settings.model_copy(update={"oidc_clock_skew_seconds": 0})
    app = create_app(strict, engine=runtime_engine, verifier=verifier_for(strict))
    with TestClient(app, raise_server_exceptions=False) as http:
        yield mcp_client_for(http)


def test_expired_keycloak_token_is_rejected(
    keycloak: LocalKeycloak, strict_clock_mcp_client: McpClient, user: TestUser
) -> None:
    client = strict_clock_mcp_client
    discovery = client.discover()
    client_id = client.register(discovery)
    representation = realm_client(keycloak, client_id)
    representation["attributes"]["access.token.lifespan"] = "1"
    keycloak.admin.request("PUT", f"/clients/{representation['id']}", representation)
    tokens = client.authorize(discovery, client_id, user)
    claims = tokens.access_claims
    assert claims["exp"] - claims["iat"] == 1
    time.sleep(2.5)

    response = client.post(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=tokens.access_token
    )

    assert response.status_code == 401
    assert challenge_parameters(response.headers["www-authenticate"])["error"] == "invalid_token"


def test_keycloak_token_without_the_mcp_audience_is_rejected(
    keycloak: LocalKeycloak, mcp_client: McpClient, user: TestUser, runtime_engine: Engine
) -> None:
    discovery = mcp_client.discover()
    client_id = mcp_client.register(discovery)
    internal_id = realm_client(keycloak, client_id)["id"]
    (mcp_scope,) = [
        scope for scope in keycloak.admin.get("/client-scopes") if scope["name"] == MCP_AUDIENCE
    ]
    keycloak.admin.request(
        "DELETE", f"/clients/{internal_id}/default-client-scopes/{mcp_scope['id']}"
    )
    tokens = mcp_client.authorize(discovery, client_id, user)
    audience = tokens.access_claims.get("aud", [])
    assert MCP_AUDIENCE not in ([audience] if isinstance(audience, str) else audience)

    response = mcp_client.post(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=tokens.access_token
    )

    assert response.status_code == 401
    assert challenge_parameters(response.headers["www-authenticate"])["error"] == "invalid_token"
    assert tenant_of(runtime_engine, tokens) is None


def test_web_client_token_is_rejected_without_provisioning(
    mcp_client: McpClient, web_tokens: Tokens, runtime_engine: Engine
) -> None:
    assert API_AUDIENCE in web_tokens.access_claims["aud"]

    response = mcp_client.post(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=web_tokens.access_token
    )

    assert response.status_code == 401
    assert challenge_parameters(response.headers["www-authenticate"])["error"] == "invalid_token"
    assert tenant_of(runtime_engine, web_tokens) is None


def test_transport_is_stateless_json(mcp_client: McpClient, mcp_tokens: Tokens) -> None:
    token = mcp_tokens.access_token
    version = {"MCP-Protocol-Version": "2025-06-18"}

    initialized = mcp_client.post(
        {
            "jsonrpc": "2.0",
            "id": "a",
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", **CLIENT_HELLO},
        },
        token=token,
    )
    # No session: a request on a fresh connection needs nothing from the initialize above.
    pinged = mcp_client.post({"jsonrpc": "2.0", "id": 7, "method": "ping"}, token=token, **version)
    notified = mcp_client.post(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}, token=token, **version
    )
    stream = mcp_client.http.get(mcp_client.mcp_url, headers={"Authorization": f"Bearer {token}"})
    ended = mcp_client.http.delete(mcp_client.mcp_url, headers={"Authorization": f"Bearer {token}"})

    assert initialized.status_code == 200
    assert initialized.headers["content-type"] == "application/json"
    assert "mcp-session-id" not in initialized.headers
    assert initialized.json()["result"]["protocolVersion"] == "2025-06-18"
    assert pinged.json() == {"jsonrpc": "2.0", "id": 7, "result": {}}
    assert (notified.status_code, notified.content) == (202, b"")
    assert stream.status_code == ended.status_code == 405


def test_unknown_method_unknown_tool_and_bad_bodies_are_errors(
    mcp_client: McpClient, mcp_tokens: Tokens
) -> None:
    token = mcp_tokens.access_token
    unknown = mcp_client.post({"jsonrpc": "2.0", "id": 2, "method": "resources/list"}, token=token)
    unknown_tool = mcp_client.post(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope"}},
        token=token,
    )
    not_json = mcp_client.http.post(
        mcp_client.mcp_url,
        content=b"{",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    batch = mcp_client.http.post(
        mcp_client.mcp_url,
        json=[{"jsonrpc": "2.0", "id": 4, "method": "ping"}],
        headers={"Authorization": f"Bearer {token}"},
    )
    modern_batch = mcp_client.http.post(
        mcp_client.mcp_url,
        json=[{"jsonrpc": "2.0", "id": 5, "method": "tools/list"}],
        headers={"Authorization": f"Bearer {token}", "MCP-Protocol-Version": "2026-07-28"},
    )

    assert unknown.json()["error"]["code"] == -32601
    assert unknown_tool.json()["error"]["code"] == -32602
    assert (not_json.status_code, not_json.json()["error"]["code"]) == (400, -32700)
    assert (batch.status_code, batch.json()["id"]) == (400, None)
    assert "error" in batch.json()
    assert (modern_batch.status_code, modern_batch.json()["error"]["code"]) == (400, -32600)


def test_initialize_answers_at_most_the_newest_initialize_based_revision(
    mcp_client: McpClient, mcp_tokens: Tokens
) -> None:
    response = mcp_client.post(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": MODERN_PROTOCOL_VERSION, **CLIENT_HELLO},
        },
        token=mcp_tokens.access_token,
    )

    assert response.status_code == 200
    assert response.json()["result"]["protocolVersion"] == "2025-11-25"


def test_unsupported_protocol_version_is_refused_with_the_supported_versions(
    mcp_client: McpClient, mcp_tokens: Tokens
) -> None:
    mcp_client.protocol_version = MODERN_PROTOCOL_VERSION
    message = mcp_client.message("tools/list")
    message["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = "2027-01-01"
    headers = mcp_client.headers(message) | {"MCP-Protocol-Version": "2027-01-01"}

    response = mcp_client.post(message, token=mcp_tokens.access_token, **headers)

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == -32022
    assert error["data"]["requested"] == "2027-01-01"
    assert MODERN_PROTOCOL_VERSION in error["data"]["supported"]


def test_revision_2024_11_05_is_served_only_with_a_valid_token(
    mcp_client: McpClient, mcp_tokens: Tokens
) -> None:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", **CLIENT_HELLO},
    }
    version = {"MCP-Protocol-Version": "2024-11-05"}
    token = mcp_tokens.access_token

    anonymous = mcp_client.post(initialize)
    initialized = mcp_client.post(initialize, token=token)
    listed = mcp_client.post(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token=token, **version
    )

    assert anonymous.status_code == 401
    assert initialized.json()["result"]["protocolVersion"] == "2024-11-05"
    assert listed.json()["result"] == {"tools": []}


def _drop(mapping: dict[str, Any], key: str) -> None:
    del mapping[key]


def _set_meta_version(message: dict[str, Any], version: str) -> None:
    message["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = version


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(lambda message, headers: _drop(headers, "Mcp-Method"), id="no-mcp-method"),
        pytest.param(
            lambda message, headers: headers.update({"Mcp-Method": "tools/list"}),
            id="other-mcp-method",
        ),
        pytest.param(
            lambda message, headers: headers.update({"Mcp-Name": "some_other_tool"}),
            id="other-mcp-name",
        ),
        pytest.param(lambda message, headers: _drop(headers, "Mcp-Name"), id="no-mcp-name"),
        pytest.param(
            lambda message, headers: _set_meta_version(message, "2025-11-25"),
            id="protocol-version-header-differs-from-meta",
        ),
    ],
)
def test_modern_request_with_missing_or_mismatched_header_is_refused(
    mcp_client: McpClient,
    mcp_tokens: Tokens,
    tamper: Callable[[dict[str, Any], dict[str, str]], None],
) -> None:
    mcp_client.protocol_version = MODERN_PROTOCOL_VERSION
    message = mcp_client.message("tools/call", {"name": "get_workflow_run", "arguments": {}})
    headers = mcp_client.headers(message)
    tamper(message, headers)

    response = mcp_client.post(message, token=mcp_tokens.access_token, **headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020
    assert response.json()["id"] == message["id"]


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(
            lambda message: _drop(
                message["params"]["_meta"], "io.modelcontextprotocol/clientCapabilities"
            ),
            id="no-client-capabilities",
        ),
        pytest.param(
            lambda message: _drop(message["params"], "_meta"), id="modern-header-without-meta"
        ),
        pytest.param(
            lambda message: _drop(
                message["params"]["_meta"], "io.modelcontextprotocol/protocolVersion"
            ),
            id="modern-header-without-meta-protocol-version",
        ),
    ],
)
def test_modern_request_without_its_meta_envelope_has_invalid_params(
    mcp_client: McpClient, mcp_tokens: Tokens, tamper: Callable[[dict[str, Any]], None]
) -> None:
    mcp_client.protocol_version = MODERN_PROTOCOL_VERSION
    message = mcp_client.message("tools/call", {"name": "get_workflow_run", "arguments": {}})
    headers = mcp_client.headers(message)
    tamper(message)

    response = mcp_client.post(message, token=mcp_tokens.access_token, **headers)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602
    assert response.json()["id"] == message["id"]


@pytest.mark.parametrize("method", ["ping", "logging/setLevel", "resources/list"])
def test_methods_2026_07_28_removed_or_not_served_are_not_found(
    mcp_client: McpClient, mcp_tokens: Tokens, method: str
) -> None:
    mcp_client.protocol_version = MODERN_PROTOCOL_VERSION
    message = mcp_client.message(method, {"level": "info"} if method == "logging/setLevel" else {})

    response = mcp_client.post(
        message, token=mcp_tokens.access_token, **mcp_client.headers(message)
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == -32601


def test_modern_notification_is_accepted_without_a_body(
    mcp_client: McpClient, mcp_tokens: Tokens
) -> None:
    mcp_client.protocol_version = MODERN_PROTOCOL_VERSION
    notification = mcp_client.message("notifications/cancelled", {"requestId": 1})
    del notification["id"]

    response = mcp_client.post(
        notification, token=mcp_tokens.access_token, **mcp_client.headers(notification)
    )

    assert (response.status_code, response.content) == (202, b"")


def test_modern_client_falls_back_to_initialize_on_an_initialize_based_server() -> None:
    methods: list[str] = []

    async def initialize_based_server(request: Request) -> Response:
        """Serves only the earlier era: a request with modern metadata is a bad request."""
        message = await request.json()
        methods.append(message["method"])
        if "_meta" in message.get("params", {}):
            error = {"code": -32600, "message": "Bad Request: no valid session ID"}
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": error}, status_code=400)
        if "id" not in message:
            return Response(status_code=202)
        result = {"protocolVersion": "2025-11-25"} if message["method"] == "initialize" else {}
        return JSONResponse({"jsonrpc": "2.0", "id": message["id"], "result": result})

    app = Starlette(routes=[Route("/mcp", initialize_based_server, methods=["POST"])])
    with TestClient(app) as http:
        client = McpClient(http, "http://testserver/mcp", protocol_version=MODERN_PROTOCOL_VERSION)
        result = client.request("tools/list", token="synthetic-token")

    assert result == {}
    assert client.protocol_version == "2025-11-25"
    assert methods == ["tools/list", "initialize", "notifications/initialized", "tools/list"]


class _NoArguments(BaseModel):
    pass


class _Recorded(BaseModel):
    calls: int


@pytest.fixture
def recording_mcp_client(
    settings: Settings,
    runtime_engine: Engine,
    verifier: OidcVerifier,
    mcp_client_for: Callable[[TestClient], McpClient],
) -> Iterator[tuple[McpClient, list[ExecutionContext]]]:
    """A generic client for an MCP application whose one tool, ``record_call``, records each
    caller that runs it in the returned list."""
    calls: list[ExecutionContext] = []

    def record(context: ExecutionContext, engine: Engine, arguments: _NoArguments) -> _Recorded:
        calls.append(context)
        return _Recorded(calls=len(calls))

    tool = Tool(
        name="record_call",
        description="Records that it ran.",
        arguments=_NoArguments,
        result=_Recorded,
        run=record,
    )
    app = create_app(settings, engine=runtime_engine, verifier=verifier, tools=[tool])
    with TestClient(app, raise_server_exceptions=False) as http:
        yield mcp_client_for(http), calls


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"Origin": "http://attacker.example.test"}, id="foreign-origin"),
        pytest.param({"Host": "attacker.example.test"}, id="foreign-host"),
    ],
)
def test_request_from_a_disallowed_host_or_origin_is_rejected_and_runs_no_tool(
    recording_mcp_client: tuple[McpClient, list[ExecutionContext]],
    mcp_tokens: Tokens,
    runtime_engine: Engine,
    headers: dict[str, str],
) -> None:
    client, calls = recording_mcp_client
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "record_call", "arguments": {}},
    }
    token = mcp_tokens.access_token

    refused = client.post(call, token=token, **headers)
    # Refused before the token check, so the new user was not provisioned.
    provisioned_by_refused = tenant_of(runtime_engine, mcp_tokens)
    own_origin = client.post(call, token=token, Origin="http://testserver")

    assert refused.status_code == (403 if "Origin" in headers else 421)
    assert provisioned_by_refused is None
    assert own_origin.status_code == 200
    assert own_origin.json()["result"]["structuredContent"] == {"calls": 1}
    assert len(calls) == 1


def test_issuer_outage_gives_503_not_a_challenge(
    settings: Settings,
    runtime_engine: Engine,
    mcp_client_for: Callable[[TestClient], McpClient],
    mcp_tokens: Tokens,
) -> None:
    # Nothing listens on port 1, so the issuer's key set cannot be fetched.
    unreachable = settings.model_copy(update={"oidc_jwks_url": "http://127.0.0.1:1/certs"})
    app = create_app(unreachable, engine=runtime_engine, verifier=verifier_for(unreachable))
    with TestClient(app, raise_server_exceptions=False) as http:
        response = mcp_client_for(http).post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token=mcp_tokens.access_token
        )

    assert response.status_code == 503
    assert "www-authenticate" not in response.headers
    assert response.json()["jsonrpc"] == "2.0"
    assert response.json()["error"]["code"] == -32603


# --- Parity harness -------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """One user's access tokens for each surface: the web client's for REST, a dynamically
    registered client's for MCP. Both name the same subject, so the same tenant."""

    rest_token: str
    mcp_token: str

    @classmethod
    def signed_in(cls, keycloak: LocalKeycloak, user: TestUser) -> "Principal":
        return cls(
            keycloak.pkce_sign_in(user).access_token, keycloak.mcp_sign_in(user).access_token
        )


def assert_rest_mcp_parity(
    api: TestClient,
    mcp_client: McpClient,
    principal: "Principal",
    *,
    rest: tuple[str, str],
    tool: str,
    arguments: dict[str, Any],
    json_body: Any = None,
) -> Any:
    """Run one use case through REST (``rest`` is the method and path) and the MCP ``tool``
    for the same ``principal``, assert both give the same canonical result, and return it.

    A REST success must equal the tool's structured result. A REST problem must be a tool
    error with the same code; the problem's ``code`` and ``detail`` are returned then.
    """
    method, path = rest
    response = api.request(
        method, path, json=json_body, headers={"Authorization": f"Bearer {principal.rest_token}"}
    )
    result = mcp_client.call_tool(tool, arguments, token=principal.mcp_token)

    if response.status_code >= 400:
        assert result["isError"] is True, result
        problem = response.json()
        expected = {"code": problem["code"], "detail": problem["detail"]}
        assert result["content"] == [{"type": "text", "text": json.dumps(expected)}]
        return expected
    assert result["isError"] is False, result
    assert result["structuredContent"] == response.json()
    return response.json()


class _RunArguments(BaseModel):
    run_id: uuid.UUID


def _get_workflow_run(
    context: ExecutionContext, engine: Engine, arguments: _RunArguments
) -> schemas.WorkflowRun:
    """The REST workflow-run route's use case and wire form, bound as a tool."""
    return schemas.WorkflowRun.from_rows(*read_workflow_run(engine, context, arguments.run_id))


@pytest.fixture
def harness_mcp_client(
    settings: Settings,
    runtime_engine: Engine,
    verifier: OidcVerifier,
    mcp_client_for: Callable[[TestClient], McpClient],
) -> Iterator[McpClient]:
    """A generic client for an MCP application serving one tool defined by this suite."""
    tool = Tool(
        name="get_workflow_run",
        description="A workflow run of the caller's tenant.",
        arguments=_RunArguments,
        result=schemas.WorkflowRun,
        run=_get_workflow_run,
    )
    app = create_app(settings, engine=runtime_engine, verifier=verifier, tools=[tool])
    with TestClient(app, raise_server_exceptions=False) as http:
        yield mcp_client_for(http)


@pytest.fixture
def workflow_run_id(
    harness_mcp_client: McpClient, mcp_tokens: Tokens, runtime_engine: Engine
) -> uuid.UUID:
    """A workflow run with one step, ``load``, in the tenant of ``mcp_tokens``' user."""
    harness_mcp_client.initialize(mcp_tokens.access_token)  # provisions the tenant
    tenant = tenant_of(runtime_engine, mcp_tokens)
    assert tenant is not None
    with UnitOfWork(runtime_engine, tenant) as uow:
        run = uow.repository(WorkflowRun).add(
            WorkflowRun(kind="test", input_digest="d", deadline_at=utc_now())
        )
        uow.session.flush()
        uow.repository(WorkflowStep).add(WorkflowStep(workflow_run_id=run.id, name="load"))
        return run.id


def workflow_run_parity(
    api: TestClient, mcp_client: McpClient, principal: Principal, run_id: uuid.UUID
) -> Any:
    """``assert_rest_mcp_parity`` for reading the workflow run ``run_id``."""
    return assert_rest_mcp_parity(
        api,
        mcp_client,
        principal,
        rest=("GET", f"/v1/workflow-runs/{run_id}"),
        tool="get_workflow_run",
        arguments={"run_id": str(run_id)},
    )


@pytest.mark.parametrize("protocol_version", ["2025-11-25", MODERN_PROTOCOL_VERSION])
def test_parity_harness_compares_rest_and_mcp_results(
    api: TestClient,
    harness_mcp_client: McpClient,
    keycloak: LocalKeycloak,
    user: TestUser,
    mcp_tokens: Tokens,
    workflow_run_id: uuid.UUID,
    protocol_version: str,
) -> None:
    token = mcp_tokens.access_token
    principal = Principal.signed_in(keycloak, user)
    other = Principal.signed_in(keycloak, keycloak.create_user())
    harness_mcp_client.protocol_version = protocol_version

    found = workflow_run_parity(api, harness_mcp_client, principal, workflow_run_id)
    hidden = workflow_run_parity(api, harness_mcp_client, other, workflow_run_id)
    missing = workflow_run_parity(api, harness_mcp_client, principal, uuid.uuid4())

    assert found["id"] == str(workflow_run_id)
    assert [step["name"] for step in found["steps"]] == ["load"]
    assert hidden == missing == {"code": "not_found", "detail": "No workflow run has this ID."}

    tools = harness_mcp_client.request("tools/list", token=token)["tools"]
    assert [tool["name"] for tool in tools] == ["get_workflow_run"]
    assert tools[0]["inputSchema"]["required"] == ["run_id"]
    invalid = harness_mcp_client.call_tool("get_workflow_run", {"run_id": "x"}, token=token)
    assert invalid["isError"] is True


@pytest.mark.parametrize("protocol_version", ["2025-06-18", "2025-11-25"])
def test_initialize_based_client_lists_and_calls_tools(
    api: TestClient,
    harness_mcp_client: McpClient,
    keycloak: LocalKeycloak,
    user: TestUser,
    mcp_tokens: Tokens,
    workflow_run_id: uuid.UUID,
    protocol_version: str,
) -> None:
    token = mcp_tokens.access_token
    harness_mcp_client.protocol_version = protocol_version

    initialized = harness_mcp_client.initialize(token)
    listed = harness_mcp_client.request("tools/list", token=token)
    found = workflow_run_parity(
        api, harness_mcp_client, Principal.signed_in(keycloak, user), workflow_run_id
    )

    assert initialized["protocolVersion"] == protocol_version
    assert [tool["name"] for tool in listed["tools"]] == ["get_workflow_run"]
    assert "resultType" not in listed
    assert found["id"] == str(workflow_run_id)


def test_modern_client_gets_the_same_results_with_and_without_discovery(
    harness_mcp_client: McpClient, mcp_tokens: Tokens, workflow_run_id: uuid.UUID
) -> None:
    client = harness_mcp_client
    client.protocol_version = MODERN_PROTOCOL_VERSION
    token = mcp_tokens.access_token
    arguments = {"run_id": str(workflow_run_id)}

    listed = client.request("tools/list", token=token)
    called = client.call_tool("get_workflow_run", arguments, token=token)
    discovered = client.discover_server(token)
    listed_after_discovery = client.request("tools/list", token=token)
    called_after_discovery = client.call_tool("get_workflow_run", arguments, token=token)
    message = client.message("tools/list")
    raw = client.post(message, token=token, **client.headers(message))

    assert discovered["resultType"] == "complete"
    assert MODERN_PROTOCOL_VERSION in discovered["supportedVersions"]
    assert discovered["capabilities"] == {"tools": {"listChanged": False}}
    assert discovered["_meta"]["io.modelcontextprotocol/serverInfo"] == {
        "name": "property-focus",
        "version": version("property-focus"),
    }
    assert (discovered["ttlMs"], discovered["cacheScope"]) == (300000, "private")
    assert (listed, called) == (listed_after_discovery, called_after_discovery)
    assert [tool["name"] for tool in listed["tools"]] == ["get_workflow_run"]
    assert (listed["resultType"], listed["ttlMs"], listed["cacheScope"]) == (
        "complete",
        300000,
        "private",
    )
    assert called["resultType"] == "complete"
    assert called["isError"] is False
    assert called["structuredContent"]["id"] == str(workflow_run_id)
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "application/json"
    assert "mcp-session-id" not in raw.headers


def test_modern_tool_call_accepts_a_base64_encoded_tool_name(
    harness_mcp_client: McpClient, mcp_tokens: Tokens, workflow_run_id: uuid.UUID
) -> None:
    client = harness_mcp_client
    client.protocol_version = MODERN_PROTOCOL_VERSION
    message = client.message(
        "tools/call",
        {"name": "get_workflow_run", "arguments": {"run_id": str(workflow_run_id)}},
    )
    headers = client.headers(message)
    headers["Mcp-Name"] = f"=?base64?{base64.b64encode(b'get_workflow_run').decode()}?="

    response = client.post(message, token=mcp_tokens.access_token, **headers)

    assert response.status_code == 200
    assert response.json()["result"]["structuredContent"]["id"] == str(workflow_run_id)


@pytest.mark.parametrize("protocol_version", ["2025-06-18", "2025-11-25", MODERN_PROTOCOL_VERSION])
@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({"run_id": "not-a-uuid"}, id="malformed-argument"),
        pytest.param({}, id="missing-argument"),
        pytest.param({"run_id": None}, id="null-argument"),
    ],
)
def test_invalid_tool_arguments_are_an_invalid_input_tool_error_in_every_era(
    harness_mcp_client: McpClient,
    mcp_tokens: Tokens,
    protocol_version: str,
    arguments: dict[str, Any],
) -> None:
    harness_mcp_client.protocol_version = protocol_version
    if protocol_version != MODERN_PROTOCOL_VERSION:
        harness_mcp_client.initialize(mcp_tokens.access_token)

    result = harness_mcp_client.call_tool(
        "get_workflow_run", arguments, token=mcp_tokens.access_token
    )

    assert result["isError"] is True
    (content,) = result["content"]
    body = json.loads(content["text"])
    assert set(body) == {"code", "detail"}
    assert body["code"] == "invalid_input"
    assert "run_id" in body["detail"]
