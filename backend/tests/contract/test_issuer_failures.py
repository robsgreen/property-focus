"""REST and MCP against the real identity provider when tokens or the provider misbehave.

A flood of tokens naming signing keys the realm never published must be refused with
``401`` without turning each request into a key-set download, while the realm's own tokens
keep working. When the provider cannot vouch for a token at all (its key set is unreachable
or its discovery document names another issuer), both adapters answer ``503`` rather than
``401``, so a client retries instead of discarding a good token. The same applications
still accept a valid token and refuse an invalid one with ``401`` when the provider is up.
"""

import time
import uuid
from collections.abc import Iterator, Mapping
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt import PyJWKClient
from sqlalchemy import Engine
from support.keycloak import LocalKeycloak, TestUser, Tokens

from contract.mcp_client import McpClient
from property_focus.adapters.auth.oidc import OidcVerifier, fetch_json, verifier_for
from property_focus.adapters.mcp.server import MCP_PATH
from property_focus.adapters.mcp.server import create_app as create_mcp_app
from property_focus.adapters.rest.app import create_app as create_rest_app
from property_focus.config import Settings

TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
UNKNOWN_KID_TOKENS = 100


class CountingClient(PyJWKClient):
    """The real key-set client, counting how often it downloads the realm's key set."""

    fetches = 0

    def fetch_data(self) -> Any:
        type(self).fetches += 1
        return super().fetch_data()


@pytest.fixture(scope="module")
def realm_user(keycloak: LocalKeycloak) -> TestUser:
    return keycloak.create_user()


@pytest.fixture(scope="module")
def realm_tokens(keycloak: LocalKeycloak, realm_user: TestUser) -> Tokens:
    """The user's tokens from the web client, for REST."""
    return keycloak.pkce_sign_in(realm_user)


@pytest.fixture(scope="module")
def realm_mcp_tokens(keycloak: LocalKeycloak, realm_user: TestUser) -> Tokens:
    """The same user's tokens from a dynamically registered client, for MCP."""
    return keycloak.mcp_sign_in(realm_user)


def serve(
    settings: Settings, engine: Engine, verifier: OidcVerifier
) -> tuple[TestClient, TestClient]:
    rest = TestClient(
        create_rest_app(settings, engine=engine, verifier=verifier), raise_server_exceptions=False
    )
    mcp = TestClient(
        create_mcp_app(settings, engine=engine, verifier=verifier), raise_server_exceptions=False
    )
    return rest, mcp


@pytest.fixture
def adapters_for(settings: Settings, runtime_engine: Engine) -> Iterator[Any]:
    """Start REST and MCP applications sharing ``verifier``; both are stopped afterwards."""
    started: list[TestClient] = []

    def start(verifier: OidcVerifier) -> tuple[TestClient, McpClient]:
        rest, mcp = serve(settings, runtime_engine, verifier)
        for client in (rest, mcp):
            client.__enter__()
            started.append(client)
        return rest, McpClient(mcp, f"{str(mcp.base_url).rstrip('/')}{MCP_PATH}")

    yield start
    for client in started:
        client.__exit__(None, None, None)


def rest_status(rest: TestClient, token: str) -> int:
    response = rest.get(
        f"/v1/workflow-runs/{uuid.uuid4()}", headers={"Authorization": f"Bearer {token}"}
    )
    return response.status_code


def signed_with_unknown_kid(claims: Mapping[str, Any], key: rsa.RSAPrivateKey) -> str:
    fresh = {**claims, "iat": int(time.time()), "exp": int(time.time()) + 3600}
    return jwt.encode(fresh, key, algorithm="RS256", headers={"kid": uuid.uuid4().hex})


def test_a_flood_of_unknown_key_ids_is_refused_with_at_most_one_key_set_fetch(
    settings: Settings, adapters_for: Any, realm_tokens: Tokens, realm_mcp_tokens: Tokens
) -> None:
    CountingClient.fetches = 0
    rest, mcp_client = adapters_for(verifier_for(settings, jwks_client_type=CountingClient))
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert rest_status(rest, realm_tokens.access_token) == 404
    fetches_before_flood = CountingClient.fetches

    statuses = [
        rest_status(rest, signed_with_unknown_kid(realm_tokens.access_claims, attacker_key))
        if index % 2
        else mcp_client.post(
            TOOLS_LIST,
            token=signed_with_unknown_kid(realm_mcp_tokens.access_claims, attacker_key),
        ).status_code
        for index in range(UNKNOWN_KID_TOKENS)
    ]

    assert statuses == [401] * UNKNOWN_KID_TOKENS
    assert CountingClient.fetches - fetches_before_flood <= 1
    assert rest_status(rest, realm_tokens.access_token) == 404
    assert mcp_client.post(TOOLS_LIST, token=realm_mcp_tokens.access_token).status_code == 200


async def _discovery_naming_another_issuer(url: str) -> Mapping[str, Any]:
    document = dict(await fetch_json(url))
    return {**document, "issuer": "http://localhost:8080/realms/master"}


def unreachable_key_set(settings: Settings) -> OidcVerifier:
    # Nothing listens on port 1.
    return verifier_for(settings.model_copy(update={"oidc_jwks_url": "http://127.0.0.1:1/certs"}))


def discovery_naming_another_issuer(settings: Settings) -> OidcVerifier:
    return verifier_for(
        settings.model_copy(update={"oidc_jwks_url": None}), fetch=_discovery_naming_another_issuer
    )


def unreachable_discovery(settings: Settings) -> OidcVerifier:
    return verifier_for(
        settings.model_copy(
            update={
                "oidc_issuer": "http://127.0.0.1:1/realms/property-focus",
                "oidc_jwks_url": None,
            }
        )
    )


@pytest.mark.parametrize(
    "broken_provider",
    [unreachable_key_set, discovery_naming_another_issuer, unreachable_discovery],
    ids=["key-set-unreachable", "discovery-names-another-issuer", "discovery-unreachable"],
)
def test_provider_that_cannot_vouch_for_a_token_gives_503_on_rest_and_mcp(
    settings: Settings,
    adapters_for: Any,
    realm_tokens: Tokens,
    realm_mcp_tokens: Tokens,
    broken_provider: Any,
) -> None:
    rest, mcp_client = adapters_for(broken_provider(settings))

    rest_response = rest.get(
        f"/v1/workflow-runs/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {realm_tokens.access_token}"},
    )
    mcp_response = mcp_client.post(TOOLS_LIST, token=realm_mcp_tokens.access_token)

    assert rest_response.status_code == 503, rest_response.text
    assert rest_response.headers["content-type"] == "application/problem+json"
    assert mcp_response.status_code == 503, mcp_response.text
    assert "www-authenticate" not in mcp_response.headers
    assert "www-authenticate" not in rest_response.headers


def test_with_the_provider_up_a_valid_token_is_served_and_an_invalid_one_gets_401(
    settings: Settings, adapters_for: Any, realm_tokens: Tokens, realm_mcp_tokens: Tokens
) -> None:
    rest, mcp_client = adapters_for(verifier_for(settings))

    def tampered(token: str) -> str:
        return token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")

    assert rest_status(rest, realm_tokens.access_token) == 404
    assert mcp_client.post(TOOLS_LIST, token=realm_mcp_tokens.access_token).status_code == 200
    assert rest_status(rest, tampered(realm_tokens.access_token)) == 401
    invalid = mcp_client.post(TOOLS_LIST, token=tampered(realm_mcp_tokens.access_token))
    assert invalid.status_code == 401
    assert "resource_metadata" in invalid.headers["www-authenticate"]
