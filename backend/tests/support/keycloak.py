"""Drive the local Keycloak realm (the Compose ``keycloak`` service) the way people and clients do.

``LocalKeycloak`` is the entry point:

- ``create_user()`` creates a synthetic user with a verified email through the admin API;
- ``pkce_sign_in(user)`` runs the authorization code flow with PKCE ``S256`` by submitting
  Keycloak's login form over HTTP and exchanging the code, and caches the tokens for the
  session; its tokens come from the web client, so they carry the REST API's audience;
- ``mcp_sign_in(user)`` does the same through one dynamically registered public client,
  so its tokens carry the MCP server's audience, as an MCP client's do;
- ``register_client(metadata)`` calls the dynamic client registration endpoint that the
  realm's discovery document names.

Nothing here uses the direct-access (password) grant, which no client in the realm allows.
The admin API is reached as the bootstrap admin, signed in through the master realm's admin
console client with the same PKCE form flow. Its username and password come from
``PF_KEYCLOAK_ADMIN_USERNAME`` and ``PF_KEYCLOAK_ADMIN_PASSWORD`` in the environment or the
repository's ``.env``. ``LocalKeycloak.close()`` deletes every user and client it created.
"""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import Message
from functools import cached_property
from html.parser import HTMLParser
from http.cookiejar import Cookie, CookieJar, DefaultCookiePolicy
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
KEYCLOAK_URL = "http://localhost:8080"
REALM = "property-focus"
ISSUER = f"{KEYCLOAK_URL}/realms/{REALM}"
WEB_CLIENT_ID = "property-focus-web"
# Where the session's dynamically registered MCP client receives its authorization code.
# Nothing listens there: the scripted sign-in reads the code from the redirect.
MCP_REDIRECT_URI = "http://127.0.0.1:43119/callback"
MAILPIT_URL = "http://localhost:8025"

# Tokens this close to expiry are replaced rather than handed out.
_EXPIRY_MARGIN_SECONDS = 15


def local_setting(name: str, default: str | None = None) -> str:
    """Return ``name`` from the environment, else from the repository's ``.env``, else ``default``.

    Raises ``LookupError`` naming the variable when none of them has a value.
    """
    value = os.environ.get(name) or _dotenv().get(name) or default
    if not value:
        raise LookupError(f"set {name} in the environment or in .env (see .env.example)")
    return value


def _dotenv() -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines of the repository's ``.env``; a missing file is empty."""
    path = REPOSITORY_ROOT / ".env"
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and not key.startswith("#"):
            values[key.strip()] = value.strip().strip("'\"")
    return values


def web_redirect_uri() -> str:
    """The web client's registered redirect URI for this machine's web origin."""
    return f"{local_setting('PF_WEB_ORIGIN', 'http://localhost:5173')}/auth/callback"


@dataclass(frozen=True)
class Response:
    """One HTTP response, redirects included (the browser never follows them by itself)."""

    status: int
    url: str
    headers: Message
    body: str

    @property
    def location(self) -> str | None:
        """The absolute redirect target of a 3xx response, otherwise None."""
        location = self.headers.get("Location")
        return urllib.parse.urljoin(self.url, location) if location else None

    def json(self) -> Any:
        return json.loads(self.body)

    def form(self, action_contains: str = "") -> "Form":
        """Return the page's first form whose action contains ``action_contains``.

        Raises ``AssertionError`` showing the page when there is none.
        """
        for form in self._page.forms:
            if action_contains in form.action:
                return form
        raise AssertionError(
            f"no form with action ~ {action_contains!r} at {self.url}:\n{self.body}"
        )

    def link(self, href_contains: str) -> str:
        """Return the absolute URL of the page's first link whose href contains ``href_contains``.

        Raises ``AssertionError`` showing the page when there is none.
        """
        for link in self._page.links:
            if href_contains in link:
                return link
        raise AssertionError(f"no link with href ~ {href_contains!r} at {self.url}:\n{self.body}")

    @cached_property
    def _page(self) -> "_PageParser":
        parser = _PageParser(self.url)
        parser.feed(self.body)
        return parser


@dataclass
class Form:
    """An HTML form: where it posts and the values its inputs already hold."""

    action: str
    fields: dict[str, str] = field(default_factory=dict)


class _PageParser(HTMLParser):
    """Collect absolute link targets, and each form's action and named inputs' values."""

    def __init__(self, page_url: str) -> None:
        super().__init__()
        self._page_url = page_url
        self.forms: list[Form] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and attributes.get("href"):
            self.links.append(urllib.parse.urljoin(self._page_url, attributes["href"] or ""))
        elif tag == "form":
            action = urllib.parse.urljoin(self._page_url, attributes.get("action") or "")
            self.forms.append(Form(action))
        elif tag == "input" and self.forms and attributes.get("name"):
            name = attributes["name"] or ""
            self.forms[-1].fields[name] = attributes.get("value") or ""


class _KeepRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class _LoopbackIsSecure(DefaultCookiePolicy):
    """Send ``Secure`` cookies to loopback hosts over plain HTTP, as browsers do."""

    def return_ok_secure(self, cookie: Cookie, request: Request) -> bool:
        host = urllib.parse.urlsplit(request.get_full_url()).hostname
        return host in {"localhost", "127.0.0.1", "::1"} or super().return_ok_secure(
            cookie, request
        )


class Browser:
    """An HTTP client with its own cookie jar that hands every redirect back to the caller."""

    def __init__(self) -> None:
        cookies = HTTPCookieProcessor(CookieJar(_LoopbackIsSecure()))
        self._opener = build_opener(cookies, _KeepRedirects())

    def request(
        self,
        method: str,
        url: str,
        *,
        form: Mapping[str, str] | None = None,
        json_body: Any = None,
        bearer: str | None = None,
    ) -> Response:
        headers = {"Accept": "application/json, text/html"}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=30) as reply:
                return Response(reply.status, reply.url, reply.headers, reply.read().decode())
        except HTTPError as error:
            return Response(error.code, url, error.headers, error.read().decode())

    def get(self, url: str) -> Response:
        return self.request("GET", url)

    def post(self, url: str, form: Mapping[str, str]) -> Response:
        return self.request("POST", url, form=form)

    def follow(self, response: Response, stop_prefix: str) -> Response:
        """Follow redirects until one points under ``stop_prefix`` (returned) or a page loads."""
        while response.location and not response.location.startswith(stop_prefix):
            response = self.get(response.location)
        return response


@dataclass(frozen=True)
class Pkce:
    """A PKCE code verifier and its ``S256`` code challenge."""

    verifier: str
    challenge: str

    @classmethod
    def new(cls) -> "Pkce":
        verifier = secrets.token_urlsafe(48)
        digest = hashlib.sha256(verifier.encode()).digest()
        return cls(verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode())


@dataclass(frozen=True)
class Tokens:
    """Tokens from one sign-in. ``expires_at`` is on the ``time.monotonic()`` clock."""

    access_token: str
    refresh_token: str
    expires_at: float

    @property
    def access_claims(self) -> dict[str, Any]:
        """The access token's payload, decoded without checking its signature."""
        payload = self.access_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))

    @property
    def is_fresh(self) -> bool:
        return time.monotonic() < self.expires_at - _EXPIRY_MARGIN_SECONDS


@dataclass(frozen=True)
class TestUser:
    """A synthetic realm user. The password is generated per run and never stored."""

    __test__ = False  # not a pytest test class

    id: str
    email: str
    password: str


class OidcRealm:
    """One Keycloak realm as an OpenID provider: discovery, browser sign-in and token refresh."""

    def __init__(self, issuer: str) -> None:
        self.issuer = issuer

    @cached_property
    def discovery(self) -> dict[str, Any]:
        """The realm's OpenID Connect discovery document."""
        response = Browser().get(f"{self.issuer}/.well-known/openid-configuration")
        assert response.status == 200, f"discovery failed: {response.status} {response.body}"
        document: dict[str, Any] = response.json()
        return document

    def authorization_url(self, client_id: str, redirect_uri: str, **parameters: str) -> str:
        """An authorization code request URL; ``parameters`` add to or replace the defaults."""
        query = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid",
            "state": secrets.token_urlsafe(16),
            **parameters,
        }
        return f"{self.discovery['authorization_endpoint']}?{urllib.parse.urlencode(query)}"

    def sign_in(
        self,
        email: str,
        password: str,
        *,
        client_id: str,
        redirect_uri: str,
        scope: str = "openid",
    ) -> Tokens:
        """Sign in through the login form with PKCE ``S256`` asking for ``scope``, granting
        consent if asked.

        Raises ``NoAuthorizationCodeError`` showing the last page when no authorization code
        arrives, for example while the user's email is unverified.
        """
        browser = Browser()
        pkce = Pkce.new()
        url = self.authorization_url(
            client_id,
            redirect_uri,
            scope=scope,
            code_challenge=pkce.challenge,
            code_challenge_method="S256",
        )
        response = self.submit_login(browser, browser.get(url), email, password, redirect_uri)
        if response.status == 200 and "login-actions/consent" in response.body:
            consent = response.form("login-actions/consent")
            response = browser.follow(
                browser.post(consent.action, {**consent.fields, "accept": "Yes"}), redirect_uri
            )
        code = authorization_code(response, redirect_uri)
        return self.exchange_code(code, client_id=client_id, redirect_uri=redirect_uri, pkce=pkce)

    def submit_login(
        self, browser: Browser, login_page: Response, email: str, password: str, redirect_uri: str
    ) -> Response:
        """Submit the login form and follow redirects up to ``redirect_uri`` or the next page."""
        form = login_page.form("login-actions/authenticate")
        submitted = browser.post(
            form.action, {**form.fields, "username": email, "password": password}
        )
        return browser.follow(submitted, redirect_uri)

    def exchange_code(self, code: str, *, client_id: str, redirect_uri: str, pkce: Pkce) -> Tokens:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": pkce.verifier,
            }
        )

    def refresh(self, tokens: Tokens, client_id: str) -> Tokens:
        return self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": tokens.refresh_token,
            }
        )

    def _token_request(self, form: Mapping[str, str]) -> Tokens:
        started = time.monotonic()
        response = Browser().post(self.discovery["token_endpoint"], form)
        assert response.status == 200, f"token request failed: {response.status} {response.body}"
        body = response.json()
        return Tokens(body["access_token"], body["refresh_token"], started + body["expires_in"])


class NoAuthorizationCodeError(AssertionError):
    """A sign-in ended somewhere other than a redirect carrying an authorization code."""


def authorization_code(response: Response, redirect_uri: str) -> str:
    """Return the code from a redirect to ``redirect_uri``.

    Raises ``NoAuthorizationCodeError`` showing the response for anything else.
    """
    location = response.location or ""
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(location).query)
    if not location.startswith(redirect_uri) or "code" not in query:
        raise NoAuthorizationCodeError(
            f"no authorization code: {response.status} {location or response.url}\n{response.body}"
        )
    return query["code"][0]


class KeycloakAdmin:
    """The Keycloak admin REST API for one realm, called as the bootstrap admin."""

    _CONSOLE_CLIENT_ID = "security-admin-console"

    def __init__(self, base_url: str, realm: str) -> None:
        self._base_url = base_url
        self._realm_url = f"{base_url}/admin/realms/{realm}"
        self._master = OidcRealm(f"{base_url}/realms/master")
        self._tokens: Tokens | None = None

    def request(self, method: str, path: str, json_body: Any = None) -> Response:
        """Call ``path`` under the realm's admin URL; raise ``AssertionError`` on a 4xx or 5xx."""
        response = Browser().request(
            method, f"{self._realm_url}{path}", json_body=json_body, bearer=self._access_token()
        )
        assert response.status < 400, f"{method} {path}: {response.status} {response.body}"
        return response

    def get(self, path: str) -> Any:
        return self.request("GET", path).json()

    def _access_token(self) -> str:
        if self._tokens is None:
            self._tokens = self._master.sign_in(
                local_setting("PF_KEYCLOAK_ADMIN_USERNAME"),
                local_setting("PF_KEYCLOAK_ADMIN_PASSWORD"),
                client_id=self._CONSOLE_CLIENT_ID,
                redirect_uri=f"{self._base_url}/admin/master/console/",
            )
        elif not self._tokens.is_fresh:
            self._tokens = self._master.refresh(self._tokens, self._CONSOLE_CLIENT_ID)
        return self._tokens.access_token


@dataclass(frozen=True)
class Registration:
    """A dynamic client registration response: ``status`` 201 with client metadata, or an error."""

    status: int
    metadata: dict[str, Any]


class LocalKeycloak:
    """The ``property-focus`` realm on the local Keycloak, for one test session."""

    def __init__(self) -> None:
        self.realm = OidcRealm(ISSUER)
        self.admin = KeycloakAdmin(KEYCLOAK_URL, REALM)
        self._user_ids: list[str] = []
        self._client_ids: list[str] = []
        self._tokens: dict[tuple[str, str], Tokens] = {}

    def create_user(self) -> TestUser:
        """Create a synthetic user with a unique verified ``@example.test`` email and password."""
        email = f"buyer-{uuid.uuid4().hex[:12]}@example.test"
        password = secrets.token_urlsafe(18)
        response = self.admin.request(
            "POST",
            "/users",
            {
                "username": email,
                "email": email,
                "emailVerified": True,
                "enabled": True,
                "firstName": "Synthetic",
                "lastName": "Buyer",
                "credentials": [{"type": "password", "value": password, "temporary": False}],
            },
        )
        user_id = (response.location or "").rsplit("/", 1)[-1]
        self.track_user(user_id)
        return TestUser(user_id, email, password)

    def track_user(self, user_id: str) -> None:
        """Delete this user at ``close()``, for users created another way (self-registration)."""
        self._user_ids.append(user_id)

    def pkce_sign_in(
        self, user: TestUser, *, client_id: str = WEB_CLIENT_ID, redirect_uri: str | None = None
    ) -> Tokens:
        """Return ``user``'s tokens from ``client_id``, signing in only when none are fresh."""
        key = (user.email, client_id)
        cached = self._tokens.get(key)
        if cached is None or not cached.is_fresh:
            cached = self.realm.sign_in(
                user.email,
                user.password,
                client_id=client_id,
                redirect_uri=redirect_uri or web_redirect_uri(),
            )
            self._tokens[key] = cached
        return cached

    def mcp_sign_in(self, user: TestUser) -> Tokens:
        """Return ``user``'s tokens from the session's dynamically registered MCP client,
        signing in (and granting consent) only when none are fresh."""
        return self.pkce_sign_in(user, client_id=self._mcp_client_id, redirect_uri=MCP_REDIRECT_URI)

    @cached_property
    def _mcp_client_id(self) -> str:
        """A public loopback client registered anonymously, as MCP clients register."""
        registration = self.register_client(
            {
                "client_name": "Synthetic MCP client",
                "redirect_uris": [MCP_REDIRECT_URI],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            }
        )
        assert registration.status == 201, registration.metadata
        client_id: str = registration.metadata["client_id"]
        return client_id

    def register_client(self, metadata: Mapping[str, Any]) -> Registration:
        """Register a client anonymously at the realm's advertised registration endpoint."""
        response = Browser().request(
            "POST", self.realm.discovery["registration_endpoint"], json_body=dict(metadata)
        )
        body = response.json() if response.body.startswith("{") else {"error": response.body}
        if response.status == 201:
            self._client_ids.append(body["client_id"])
        return Registration(response.status, body)

    def delete_client(self, client_id: str) -> None:
        """Delete a client by its OAuth ``client_id``."""
        for client in self.admin.get(f"/clients?clientId={urllib.parse.quote(client_id)}"):
            self.admin.request("DELETE", f"/clients/{client['id']}")
        if client_id in self._client_ids:
            self._client_ids.remove(client_id)

    def close(self) -> None:
        """Delete the users and clients this session created."""
        for client_id in list(self._client_ids):
            self.delete_client(client_id)
        for user_id in self._user_ids:
            self.admin.request("DELETE", f"/users/{user_id}")
        self._user_ids.clear()


def latest_mail_text(recipient: str, *, timeout_seconds: float = 15) -> str:
    """Return the plain-text body of the newest message Mailpit holds for ``recipient``.

    Waits up to ``timeout_seconds`` for one to arrive; raises ``AssertionError`` if none does.
    """
    query = urllib.parse.urlencode({"query": f'to:"{recipient}"'})
    deadline = time.monotonic() + timeout_seconds
    browser = Browser()
    while time.monotonic() < deadline:
        messages = browser.get(f"{MAILPIT_URL}/api/v1/search?{query}").json()["messages"]
        if messages:
            message = browser.get(f"{MAILPIT_URL}/api/v1/message/{messages[0]['ID']}").json()
            text: str = message["Text"]
            return text
        time.sleep(0.5)
    raise AssertionError(f"no mail for {recipient} within {timeout_seconds} s")
