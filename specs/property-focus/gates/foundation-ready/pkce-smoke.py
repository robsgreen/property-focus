"""Foundation ready gate, sub-task 0.12.4: PKCE sign-in smoke against local Keycloak on
``make dev``.

1. Creates a synthetic verified user in the local ``property-focus`` realm
   (``tests/support/keycloak.py``).
2. Runs ``pkce-smoke-sign-in.mjs``: headless Chromium opens the web app on
   http://localhost:5173, is redirected to Keycloak, signs in with authorization code and
   PKCE ``S256``, and returns to the app's signed-in page; the app's access token is handed
   over in a mode-0600 temporary file and never printed. The browser stays open.
3. With that token, reuses the dogfood smoke's REST check (``backend/scripts/smoke_dogfood.py``)
   through the web app's origin (Vite forwards ``/v1`` to the API on 8000): ``GET
   /v1/workflow-runs/{random id}`` must be an authenticated ``application/problem+json``
   ``404``. Then calls the MCP adapter on 8001: ``initialize`` and ``tools/list`` on the
   initialize-based path, ``server/discover`` and ``tools/list`` on the 2026-07-28 path.
4. Signs out in the same browser: the script clicks "Sign out" and checks that the browser
   goes through Keycloak's end-session endpoint and comes back to Keycloak's sign-in form,
   not to the signed-in page, also when the app is loaded again. Keycloak's admin API must
   list one session for the user before the sign-out and none after.
5. Deletes the user.

Usage, from the repository root with ``make dev`` running, ``NODE_PATH`` naming a directory
that holds the ``playwright`` package, and Chromium at ``PF_SMOKE_CHROMIUM`` (default
``/opt/pw-browsers/chromium``):
``uv run --package property-focus python specs/property-focus/gates/foundation-ready/pkce-smoke.py``
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx2

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "backend" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "backend" / "tests"))

from contract.mcp_client import LEGACY_PROTOCOL_VERSION, MODERN_PROTOCOL_VERSION, McpClient
from smoke_dogfood import check_rest
from support.keycloak import LocalKeycloak, TestUser

HERE = Path(__file__).resolve().parent
WEB_ORIGIN = "http://localhost:5173"
MCP_URL = "http://127.0.0.1:8001/mcp"


def relay_until(browser: subprocess.Popen[str], marker: str) -> None:
    """Print the browser script's output up to the line ``marker``."""
    assert browser.stdout is not None
    for line in browser.stdout:
        if line.rstrip("\n") == marker:
            return
        print(line, end="")
    raise AssertionError(f"the browser script stopped (exit {browser.wait()}) before {marker}")


def sign_in_use_and_sign_out(keycloak: LocalKeycloak, user: TestUser) -> None:
    """Sign ``user`` in through the web app, call REST and MCP with the app's access token,
    then sign out through the app and check that the sign-out holds."""
    with tempfile.TemporaryDirectory() as directory:
        token_file = Path(directory) / "access-token"
        browser = subprocess.Popen(
            ["node", str(HERE / "pkce-smoke-sign-in.mjs")],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                "PF_SMOKE_USER_EMAIL": user.email,
                "PF_SMOKE_USER_PASSWORD": user.password,
                "PF_SMOKE_TOKEN_FILE": str(token_file),
            },
        )
        try:
            relay_until(browser, "TOKEN-READY")
            call_rest_and_mcp(token_file.read_text())
            sessions = keycloak.admin.get(f"/users/{user.id}/sessions")
            assert len(sessions) == 1, f"expected one Keycloak session, found {len(sessions)}"
            print(f"Keycloak sessions for the user before sign-out: {len(sessions)}")

            assert browser.stdin is not None
            browser.stdin.write("signed in and used\n")
            browser.stdin.close()
            relay_until(browser, "SIGNED-OUT")
            assert browser.wait(timeout=60) == 0, "the browser script failed after sign-out"
        finally:
            if browser.poll() is None:
                browser.kill()
    sessions = keycloak.admin.get(f"/users/{user.id}/sessions")
    assert not sessions, f"Keycloak still has {len(sessions)} session(s) for the user"
    print("Keycloak sessions for the user after sign-out: 0")


def call_rest_and_mcp(token: str) -> None:
    """Call REST through the web app's origin and MCP on both protocol paths with ``token``;
    raise ``AssertionError`` on any unexpected answer."""
    check_rest(WEB_ORIGIN, token)
    print(f"ok: GET {WEB_ORIGIN}/v1/workflow-runs/{{random id}} -> authenticated problem 404")

    with httpx2.Client(timeout=30) as http:
        legacy = McpClient(http, MCP_URL, protocol_version=LEGACY_PROTOCOL_VERSION)
        initialized = legacy.initialize(token)
        tools = legacy.request("tools/list", token=token)["tools"]
        print(
            f"ok: {MCP_URL} initialize (requested {LEGACY_PROTOCOL_VERSION}) -> protocolVersion "
            f"{initialized['protocolVersion']}, serverInfo {initialized['serverInfo']}; "
            f"tools/list -> {len(tools)} tools"
        )

        modern = McpClient(http, MCP_URL, protocol_version=MODERN_PROTOCOL_VERSION)
        discovered = modern.discover_server(token)
        assert modern.is_modern, "the 2026-07-28 path fell back to initialize"
        tools = modern.request("tools/list", token=token)["tools"]
        print(
            f"ok: {MCP_URL} server/discover ({MODERN_PROTOCOL_VERSION}) -> supportedVersions "
            f"{discovered['supportedVersions']}, resultType {discovered.get('resultType')!r}, "
            f"cacheScope {discovered.get('cacheScope')!r}; tools/list -> {len(tools)} tools, "
            "no fallback to initialize"
        )


def main() -> None:
    keycloak = LocalKeycloak()
    try:
        user = keycloak.create_user()
        print(f"created synthetic user {user.email} in the local property-focus realm")
        sign_in_use_and_sign_out(keycloak, user)
    finally:
        keycloak.close()
        print("deleted the synthetic user")
    print("pkce-smoke: all checks passed")


if __name__ == "__main__":
    main()
