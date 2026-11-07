"""Fixtures for the adapter contract suites: the MCP application, its generic client and
synthetic users of the local Keycloak realm.

Every fixture here needs the local Keycloak container (through the shared ``keycloak``
fixture), which issues every token these suites use.
"""

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from support.keycloak import LocalKeycloak, TestUser, Tokens

from contract.mcp_client import McpClient
from property_focus.adapters.auth.oidc import OidcVerifier
from property_focus.adapters.mcp.server import MCP_PATH, create_app
from property_focus.config import Settings


@pytest.fixture(scope="session")
def mcp(settings: Settings, runtime_engine: Engine, verifier: OidcVerifier) -> Iterator[TestClient]:
    """An HTTP client for the MCP application, served on the test database."""
    app = create_app(settings, engine=runtime_engine, verifier=verifier)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def user(keycloak: LocalKeycloak) -> TestUser:
    """A new synthetic realm user with a verified email."""
    return keycloak.create_user()


@pytest.fixture
def web_tokens(keycloak: LocalKeycloak, user: TestUser) -> Tokens:
    """``user``'s tokens from the web client: the REST API's audience."""
    return keycloak.pkce_sign_in(user)


@pytest.fixture
def mcp_tokens(keycloak: LocalKeycloak, user: TestUser) -> Tokens:
    """``user``'s tokens from a dynamically registered client: the MCP server's audience, for
    tests about the transport rather than sign-in."""
    return keycloak.mcp_sign_in(user)


@pytest.fixture
def mcp_client_for(keycloak: LocalKeycloak) -> Iterator[Callable[[TestClient], McpClient]]:
    """Make generic MCP clients for the MCP application behind a test client; the realm
    clients they register are deleted afterwards."""
    made: list[McpClient] = []

    def make(http: TestClient) -> McpClient:
        made.append(McpClient(http, f"{str(http.base_url).rstrip('/')}{MCP_PATH}"))
        return made[-1]

    yield make
    for client in made:
        for client_id in client.registered_client_ids:
            keycloak.delete_client(client_id)


@pytest.fixture
def mcp_client(mcp: TestClient, mcp_client_for: Callable[[TestClient], McpClient]) -> McpClient:
    """A generic MCP client for ``mcp`` with no preconfigured client registration."""
    return mcp_client_for(mcp)
