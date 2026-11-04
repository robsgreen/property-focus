"""Smoke-check the deployed dogfood environment from outside its host: ``make smoke-dogfood``.

Both hostnames sit behind Cloudflare (A5), and every check goes through it. Checks, in
order, printing one line each and stopping at the first failure:

1. the API is ready (``/health/ready`` through Cloudflare and the TLS proxy);
2. ``/admin/`` and ``/realms/master/`` on the Keycloak hostname answer ``404``;
3. a direct TCP connection to the origin's port 443 fails: the host admits only Cloudflare;
4. the synthetic smoke user signs in through the dogfood Keycloak with authorization code
   and PKCE ``S256``, using the web app's client and redirect URI;
5. with that token, ``GET /v1/workflow-runs/{random id}`` is an authenticated
   ``application/problem+json`` ``404``, not a ``401``;
6. with that token, the MCP endpoint answers ``initialize`` and ``tools/list`` on the
   initialize-based protocol path, and ``server/discover`` and ``tools/list`` on the
   2026-07-28 path.

Every response the checks read themselves, and every MCP response, must carry Cloudflare's
``cf-ray`` header and must not be a Cloudflare challenge page. A ``525`` (Cloudflare could not
complete the TLS handshake with the origin) fails with a message naming Authenticated Origin
Pulls: the origin requires Cloudflare's client certificate, which Cloudflare sends only while
that setting is on. The origin's refusal of a client without that certificate cannot be
checked from here, because the firewall drops direct connections first; ``deploy.sh`` checks it
on the host. A challenge fails the check
with a message naming Bot Fight Mode, which challenges automated clients such as this one
and MCP clients; the sign-in's responses, read by the shared sign-in client, are checked
for a challenge page when the sign-in fails.

The sign-in and MCP calls reuse the clients the contract tests drive the local stack with
(``tests/support/keycloak.py`` and ``tests/contract/mcp_client.py``). Nothing is created:
``deploy.sh`` keeps the smoke user in the realm, and no MCP client is registered.

Environment: ``PF_DOGFOOD_APP_HOST`` and ``PF_DOGFOOD_AUTH_HOST`` (the dogfood hostnames),
``PF_DOGFOOD_ORIGIN_ADDRESS`` (the host's public IP address), ``PF_SMOKE_USER_EMAIL`` and
``PF_SMOKE_USER_PASSWORD``. Exit codes: 0 every check passed,
1 a check failed, 2 a variable is missing.
"""

import ipaddress
import os
import socket
import sys
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

import httpx2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from contract.mcp_client import LEGACY_PROTOCOL_VERSION, MODERN_PROTOCOL_VERSION, McpClient
from support.keycloak import WEB_CLIENT_ID, Browser, OidcRealm, Response

REALM = "property-focus"
_VARIABLES = (
    "PF_DOGFOOD_APP_HOST",
    "PF_DOGFOOD_AUTH_HOST",
    "PF_DOGFOOD_ORIGIN_ADDRESS",
    "PF_SMOKE_USER_EMAIL",
    "PF_SMOKE_USER_PASSWORD",
)
# How long a direct connection to the origin may take to fail; the firewall drops the packets,
# so the attempt times out rather than being refused.
ORIGIN_CONNECT_TIMEOUT_SECONDS = 5.0
# A challenge page's body loads Cloudflare's challenge script from this path. It identifies a
# challenge where only the body is known: the failure message of the shared sign-in client.
_CHALLENGE_SCRIPT_PATH = "/cdn-cgi/challenge-platform/"
# Cloudflare's status for a TLS handshake with the origin that failed.
_ORIGIN_HANDSHAKE_FAILED = 525
_ORIGIN_PULLS_HINT = (
    "Cloudflare could not complete the TLS handshake with the origin (525). The origin requires "
    "Cloudflare's client certificate, so Authenticated Origin Pulls must be on: check SSL/TLS > "
    "Origin Server > Authenticated Origin Pulls (docs/operations.md, Authenticated Origin Pulls)"
)
_BOT_FIGHT_MODE_HINT = (
    "Cloudflare answered with a challenge page. Bot Fight Mode (or another Cloudflare "
    "challenge) is challenging automated clients; Bot Fight Mode must stay off, so check "
    "that setting (docs/operations.md, Cloudflare edge)"
)


class SmokeCheckError(AssertionError):
    """A dogfood check failed; the message says what was expected and what came back."""


class _Headers(Protocol):
    """Response headers with case-insensitive lookup, as ``email.message.Message`` and
    ``httpx2.Headers`` both provide."""

    def get(self, name: str, /) -> Any: ...


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeCheckError(message)


def expect_through_cloudflare(url: str, headers: _Headers) -> None:
    """Raise ``SmokeCheckError`` when the response to ``url`` is a Cloudflare challenge page
    (``cf-mitigated: challenge``) or did not pass through Cloudflare (no ``cf-ray``)."""
    if str(headers.get("cf-mitigated") or "").lower() == "challenge":
        raise SmokeCheckError(f"{url}: {_BOT_FIGHT_MODE_HINT}")
    expect(
        bool(headers.get("cf-ray")),
        f"{url}: no cf-ray header, so the response did not come through Cloudflare; "
        "the hostname's DNS record must be proxied",
    )


def edge_request(method: str, url: str, bearer: str | None = None) -> Response:
    """One request that must come back through Cloudflare (``expect_through_cloudflare``)
    from an origin Cloudflare could reach; a ``525`` raises ``SmokeCheckError`` naming
    Authenticated Origin Pulls."""
    response = Browser().request(method, url, bearer=bearer)
    expect_through_cloudflare(url, response.headers)
    if response.status == _ORIGIN_HANDSHAKE_FAILED:
        raise SmokeCheckError(f"{url}: {_ORIGIN_PULLS_HINT}")
    return response


def check_ready(app_url: str) -> None:
    response = edge_request("GET", f"{app_url}/health/ready")
    expect(response.status == 200, f"/health/ready: {response.status} {response.body[:200]}")


def check_admin_paths_hidden(auth_url: str) -> None:
    for path in ("/admin/", "/realms/master/"):
        response = edge_request("GET", f"{auth_url}{path}")
        expect(response.status == 404, f"{auth_url}{path}: {response.status}, expected 404")


def check_origin_unreachable(address: str, port: int = 443) -> None:
    """Raise ``SmokeCheckError`` unless a TCP connection to ``address`` and ``port`` fails within
    ``ORIGIN_CONNECT_TIMEOUT_SECONDS``. ``address`` must be an IP address, so that a name
    that does not resolve cannot pass for a refused connection."""
    try:
        ipaddress.ip_address(address)
    except ValueError:
        raise SmokeCheckError(
            f"PF_DOGFOOD_ORIGIN_ADDRESS {address!r} is not an IP address"
        ) from None
    try:
        with socket.create_connection((address, port), timeout=ORIGIN_CONNECT_TIMEOUT_SECONDS):
            pass
    except OSError:
        return
    raise SmokeCheckError(
        f"{address}:{port} accepted a direct connection; the host firewall must admit ports 80 "
        "and 443 only from Cloudflare's ranges (infrastructure/dogfood/firewall.sh)"
    )


def sign_in(app_url: str, auth_url: str, email: str, password: str) -> str:
    """The smoke user's access token from the web client's PKCE sign-in.

    Raises ``SmokeCheckError`` naming Bot Fight Mode when the sign-in fails on a Cloudflare
    challenge page, and the sign-in client's own ``AssertionError`` for any other failure.
    """
    try:
        tokens = OidcRealm(f"{auth_url}/realms/{REALM}").sign_in(
            email, password, client_id=WEB_CLIENT_ID, redirect_uri=f"{app_url}/auth/callback"
        )
    except AssertionError as error:
        if _CHALLENGE_SCRIPT_PATH in str(error):
            raise SmokeCheckError(f"sign-in: {_BOT_FIGHT_MODE_HINT}") from error
        raise
    return tokens.access_token


def check_rest(app_url: str, token: str) -> None:
    response = edge_request("GET", f"{app_url}/v1/workflow-runs/{uuid.uuid4()}", bearer=token)
    content_type = response.headers.get("Content-Type", "")
    expect(
        response.status == 404 and content_type.startswith("application/problem+json"),
        f"GET /v1/workflow-runs/{{id}}: {response.status} {content_type}, "
        "expected an authenticated problem 404",
    )


def _mcp_response_through_cloudflare(response: httpx2.Response) -> None:
    expect_through_cloudflare(str(response.request.url), response.headers)


def check_mcp(app_url: str, token: str) -> None:
    hooks = {"response": [_mcp_response_through_cloudflare]}
    with httpx2.Client(timeout=30, event_hooks=hooks) as http:
        legacy = McpClient(http, f"{app_url}/mcp", protocol_version=LEGACY_PROTOCOL_VERSION)
        legacy.initialize(token)
        expect(isinstance(legacy.request("tools/list", token=token).get("tools"), list), "tools")

        modern = McpClient(http, f"{app_url}/mcp", protocol_version=MODERN_PROTOCOL_VERSION)
        modern.discover_server(token)
        expect(modern.is_modern, "the 2026-07-28 path fell back to initialize")
        expect(isinstance(modern.request("tools/list", token=token).get("tools"), list), "tools")


def run_checks(environ: Mapping[str, str]) -> None:
    """Run every check against the hosts ``environ`` names, stopping at the first failure:
    ``SmokeCheckError`` or another ``AssertionError`` from the shared clients for a wrong
    answer, ``OSError`` or ``httpx2.HTTPError`` for a host that cannot be reached."""
    app_url = f"https://{environ['PF_DOGFOOD_APP_HOST']}"
    auth_url = f"https://{environ['PF_DOGFOOD_AUTH_HOST']}"
    token = ""

    def signed_in() -> None:
        nonlocal token
        token = sign_in(
            app_url, auth_url, environ["PF_SMOKE_USER_EMAIL"], environ["PF_SMOKE_USER_PASSWORD"]
        )

    checks: list[tuple[str, Callable[[], None]]] = [
        ("API ready", lambda: check_ready(app_url)),
        ("Keycloak admin paths return 404", lambda: check_admin_paths_hidden(auth_url)),
        (
            "the origin refuses direct connections",
            lambda: check_origin_unreachable(environ["PF_DOGFOOD_ORIGIN_ADDRESS"]),
        ),
        ("smoke user signs in with PKCE", signed_in),
        ("REST accepts the token", lambda: check_rest(app_url, token)),
        ("MCP accepts the token on both protocol paths", lambda: check_mcp(app_url, token)),
    ]
    for name, check in checks:
        check()
        print(f"ok: {name}")


def main() -> int:
    missing = [name for name in _VARIABLES if not os.environ.get(name)]
    if missing:
        print(f"smoke-dogfood: set {', '.join(missing)}", file=sys.stderr)
        return 2
    try:
        run_checks(os.environ)
    except (AssertionError, OSError, httpx2.HTTPError) as error:
        # Assertions are failed checks; the others are hosts that could not be reached.
        print(f"smoke-dogfood: FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print("smoke-dogfood: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
