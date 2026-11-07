"""A generic MCP client that signs in the way the MCP authorization specification describes.

``McpClient`` knows only the MCP server's URL. It has no client ID, client name or other
configuration for this application or its identity provider; everything it needs it
discovers:

1. ``discover()`` sends an MCP request without a token, reads the ``401`` challenge's
   ``resource_metadata`` URL, fetches the protected-resource metadata, and resolves the
   authorization server's metadata by trying the well-known URL forms in the
   specification's order (OAuth authorization-server metadata, then OpenID Connect
   discovery), accepting the first document whose ``issuer`` matches.
2. ``register()`` registers a public client at the advertised dynamic client registration
   endpoint.
3. ``authorize(user)`` runs authorization code with PKCE ``S256`` and the ``resource``
   indicator through the scripted sign-in form, granting consent when asked, and checks the
   ``iss`` of the authorization response.

``sign_in(user)`` runs all three. Requests to the MCP server go through ``http`` (an
in-process test client); requests to the authorization server go over the network.

The client speaks the protocol revision in ``protocol_version``. Revisions 2025-06-18 and
2025-11-25 open with ``initialize``. Revision 2026-07-28 sends each request on its own, with
the version and client capabilities in ``params._meta`` and the version, method and target
repeated in headers; ``discover_server()`` asks for the server's details. Like a dual-era
client, it falls back to ``initialize`` when a 2026-07-28 request gets a ``400`` without a
2026-07-28 error, which identifies a server of the earlier era.
"""

import base64
import re
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

import httpx2
from support.keycloak import Browser, Pkce, TestUser, Tokens, authorization_code

# Where the client says it receives the authorization code. Nothing listens there: the
# scripted sign-in reads the code from the redirect.
LOOPBACK_REDIRECT_URI = "http://127.0.0.1:43118/callback"
# The revision without a handshake, and the initialize-based one the client falls back to.
MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSION = "2025-11-25"
# JSON-RPC errors that only a 2026-07-28 server sends: header mismatch, missing client
# capability, unsupported protocol version.
MODERN_ERROR_CODES = frozenset({-32020, -32021, -32022})
# 2026-07-28 methods whose target the Mcp-Name header repeats, with the parameter naming it.
_NAMED_TARGET_PARAMETERS = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}

_CHALLENGE_PARAMETER = re.compile(r'(\w+)="([^"]*)"')


@dataclass(frozen=True)
class AuthorizationServer:
    """An authorization server's metadata and how the client found it.

    ``attempts`` lists each well-known URL tried, in order, with its HTTP status.
    """

    issuer: str
    metadata: dict[str, Any]
    metadata_url: str
    attempts: list[tuple[str, int]]


@dataclass(frozen=True)
class Discovery:
    """What an unauthenticated request taught the client: the resource and its issuer."""

    challenge: dict[str, str]
    resource_metadata: dict[str, Any]
    authorization_server: AuthorizationServer


def challenge_parameters(header: str) -> dict[str, str]:
    """The auth-params of a ``Bearer`` ``WWW-Authenticate`` challenge, by name.

    Raises ``AssertionError`` when the challenge is not for the ``Bearer`` scheme.
    """
    scheme, _, parameters = header.partition(" ")
    assert scheme.lower() == "bearer", f"not a Bearer challenge: {header!r}"
    return dict(_CHALLENGE_PARAMETER.findall(parameters))


def header_value(value: str) -> str:
    """``value`` as an ``Mcp-Name`` header carries it: as is when it is plain printable ASCII
    without surrounding spaces, otherwise in the ``=?base64?…?=`` form."""
    plain = (
        value.isascii()
        and value.isprintable()
        and value == value.strip()
        and not value.startswith("=?base64?")
    )
    return value if plain else f"=?base64?{base64.b64encode(value.encode()).decode()}?="


def is_modern_error(response: httpx2.Response) -> bool:
    """Whether ``response`` carries a JSON-RPC error that only a 2026-07-28 server sends."""
    try:
        code = response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return False
    return code in MODERN_ERROR_CODES


def authorization_server_metadata_urls(issuer: str) -> list[str]:
    """The well-known metadata URLs for ``issuer`` in the order the MCP specification gives.

    For an issuer with a path: OAuth metadata with the path inserted, OpenID Connect
    discovery with the path inserted, then OpenID Connect discovery with the path appended.
    Without a path: OAuth metadata, then OpenID Connect discovery.
    """
    parts = urllib.parse.urlsplit(issuer)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    if not path:
        return [
            f"{origin}/.well-known/oauth-authorization-server",
            f"{origin}/.well-known/openid-configuration",
        ]
    return [
        f"{origin}/.well-known/oauth-authorization-server{path}",
        f"{origin}/.well-known/openid-configuration{path}",
        f"{origin}{path}/.well-known/openid-configuration",
    ]


@dataclass
class McpClient:
    """An MCP client for the server at ``mcp_url``, reached through ``http``."""

    http: httpx2.Client
    mcp_url: str
    redirect_uri: str = LOOPBACK_REDIRECT_URI
    protocol_version: str = LEGACY_PROTOCOL_VERSION
    # The client IDs this client registered, so a test can delete them.
    registered_client_ids: list[str] = field(default_factory=list)

    def post(
        self, message: dict[str, Any], *, token: str | None = None, **headers: str
    ) -> httpx2.Response:
        """POST one JSON-RPC ``message`` to the MCP endpoint; ``headers`` add to the defaults."""
        sent = {"Accept": "application/json, text/event-stream", **headers}
        if token is not None:
            sent["Authorization"] = f"Bearer {token}"
        return self.http.post(self.mcp_url, json=message, headers=sent)

    @property
    def is_modern(self) -> bool:
        """Whether the client speaks 2026-07-28, which has no ``initialize`` handshake."""
        return self.protocol_version == MODERN_PROTOCOL_VERSION

    def message(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """The JSON-RPC request ``method``; in 2026-07-28 its ``params._meta`` carries the
        protocol version and the client's (empty) capabilities."""
        params = dict(params or {})
        if self.is_modern:
            params["_meta"] = {
                "io.modelcontextprotocol/protocolVersion": self.protocol_version,
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        return {
            "jsonrpc": "2.0",
            "id": secrets.randbelow(10**9),
            "method": method,
            "params": params,
        }

    def headers(self, message: dict[str, Any]) -> dict[str, str]:
        """The protocol headers for ``message``.

        2026-07-28 repeats the version, the method and, where the method names a target, that
        target (``Mcp-Name``, Base64-encoded unless plain ASCII). Earlier revisions send the
        version on every request but ``initialize``.
        """
        if not self.is_modern:
            if message["method"] == "initialize":
                return {}
            return {"MCP-Protocol-Version": self.protocol_version}
        headers = {"MCP-Protocol-Version": self.protocol_version, "Mcp-Method": message["method"]}
        target_parameter = _NAMED_TARGET_PARAMETERS.get(message["method"])
        if target_parameter is not None:
            headers["Mcp-Name"] = header_value(str(message["params"][target_parameter]))
        return headers

    def request(
        self, method: str, params: dict[str, Any] | None = None, *, token: str
    ) -> dict[str, Any]:
        """Send the JSON-RPC request ``method`` and return its result.

        A 2026-07-28 request that gets a ``400`` without a 2026-07-28 error switches the
        client to ``LEGACY_PROTOCOL_VERSION``, runs ``initialize`` and sends the request again.
        Raises ``AssertionError`` on any other HTTP or JSON-RPC error.
        """
        message = self.message(method, params)
        response = self.post(message, token=token, **self.headers(message))
        if self.is_modern and response.status_code == 400 and not is_modern_error(response):
            self.protocol_version = LEGACY_PROTOCOL_VERSION
            self.initialize(token)
            return self.request(method, params, token=token)
        assert response.status_code == 200, f"{method}: {response.status_code} {response.text}"
        assert response.headers["content-type"] == "application/json"
        body: dict[str, Any] = response.json()
        assert body["id"] == message["id"]
        assert "error" not in body, f"{method}: {body['error']}"
        result: dict[str, Any] = body["result"]
        return result

    def discover_server(self, token: str) -> dict[str, Any]:
        """Ask a 2026-07-28 server for its versions, capabilities and identity."""
        return self.request("server/discover", token=token)

    def initialize(self, token: str) -> dict[str, Any]:
        """Open the conversation with ``initialize`` and acknowledge it; return its result."""
        result = self.request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "generic-test-client", "version": "0"},
            },
            token=token,
        )
        acknowledged = self.post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            token=token,
            **{"MCP-Protocol-Version": result["protocolVersion"]},
        )
        assert acknowledged.status_code == 202, acknowledged.text
        return result

    def call_tool(self, name: str, arguments: dict[str, Any], *, token: str) -> dict[str, Any]:
        """Call the tool ``name`` and return its ``CallToolResult``."""
        return self.request("tools/call", {"name": name, "arguments": arguments}, token=token)

    def discover(self) -> Discovery:
        """Find the resource's authorization server from an unauthenticated request."""
        response = self.post(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert response.status_code == 401, response.text
        challenge = challenge_parameters(response.headers["www-authenticate"])
        metadata_response = self.http.get(challenge["resource_metadata"])
        assert metadata_response.status_code == 200, metadata_response.text
        resource_metadata: dict[str, Any] = metadata_response.json()
        assert resource_metadata["resource"] == self.mcp_url
        issuer = resource_metadata["authorization_servers"][0]
        return Discovery(challenge, resource_metadata, self._authorization_server(issuer))

    def _authorization_server(self, issuer: str) -> AuthorizationServer:
        attempts = []
        for url in authorization_server_metadata_urls(issuer):
            response = Browser().get(url)
            attempts.append((url, response.status))
            if response.status == 200 and response.json().get("issuer") == issuer:
                metadata: dict[str, Any] = response.json()
                assert "S256" in metadata.get("code_challenge_methods_supported", []), (
                    "the authorization server does not advertise PKCE S256"
                )
                return AuthorizationServer(issuer, metadata, url, attempts)
        raise AssertionError(f"no authorization-server metadata for {issuer}: {attempts}")

    def register(self, discovery: Discovery) -> str:
        """Register as a public client at the advertised endpoint; return the client ID."""
        endpoint = discovery.authorization_server.metadata["registration_endpoint"]
        response = Browser().request(
            "POST",
            endpoint,
            json_body={
                "client_name": "Generic MCP test client",
                "redirect_uris": [self.redirect_uri],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
        )
        assert response.status == 201, f"registration failed: {response.status} {response.body}"
        client_id: str = response.json()["client_id"]
        self.registered_client_ids.append(client_id)
        return client_id

    def authorize(self, discovery: Discovery, client_id: str, user: TestUser) -> Tokens:
        """Sign ``user`` in with authorization code and PKCE ``S256``; return the tokens.

        Asks for the scopes the challenge names, else the metadata's ``scopes_supported``,
        else none, and names the MCP server as the ``resource`` in both requests.
        """
        server = discovery.authorization_server
        scope = discovery.challenge.get("scope") or " ".join(
            discovery.resource_metadata.get("scopes_supported", [])
        )
        pkce = Pkce.new()
        state = secrets.token_urlsafe(16)
        query = {
            "client_id": client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": state,
            "code_challenge": pkce.challenge,
            "code_challenge_method": "S256",
            "resource": self.mcp_url,
        } | ({"scope": scope} if scope else {})

        browser = Browser()
        login_page = browser.get(
            f"{server.metadata['authorization_endpoint']}?{urllib.parse.urlencode(query)}"
        )
        login = login_page.form("login-actions/authenticate")
        response = browser.follow(
            browser.post(
                login.action, {**login.fields, "username": user.email, "password": user.password}
            ),
            self.redirect_uri,
        )
        if response.status == 200 and "login-actions/consent" in response.body:
            consent = response.form("login-actions/consent")
            response = browser.follow(
                browser.post(consent.action, {**consent.fields, "accept": "Yes"}),
                self.redirect_uri,
            )
        code = authorization_code(response, self.redirect_uri)
        self._check_authorization_response(server, response.location or "", state)

        started = time.monotonic()
        token_response = browser.post(
            server.metadata["token_endpoint"],
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": pkce.verifier,
                "resource": self.mcp_url,
            },
        )
        assert token_response.status == 200, (
            f"token request failed: {token_response.status} {token_response.body}"
        )
        body = token_response.json()
        return Tokens(
            body["access_token"], body.get("refresh_token", ""), started + body["expires_in"]
        )

    @staticmethod
    def _check_authorization_response(
        server: AuthorizationServer, location: str, state: str
    ) -> None:
        """Check the redirect's ``state`` and, per RFC 9207, its ``iss``."""
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
        assert query.get("state") == [state], "the authorization response has the wrong state"
        issuer = query.get("iss")
        if issuer is not None or server.metadata.get(
            "authorization_response_iss_parameter_supported"
        ):
            assert issuer == [server.issuer], f"unexpected authorization response issuer {issuer}"

    def sign_in(self, user: TestUser) -> Tokens:
        """Discover, register and authorize in one go; return ``user``'s tokens."""
        discovery = self.discover()
        return self.authorize(discovery, self.register(discovery), user)
