"""The ``property-focus`` Keycloak realm as committed in Git, running in local Compose.

Covers sign-up with a verified email, the password policy and password reset, PKCE
enforcement, the API audience, the absence of password, implicit and service-account
clients, the realm file's lack of secrets, the dynamic client registration rules that MCP
clients go through (including that Keycloak fetches no URI an anonymous registration names),
and ``backend/scripts/keycloak_realm.py``, which keeps a running realm in step with the file.
"""

import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from support.keycloak import (
    ISSUER,
    REPOSITORY_ROOT,
    WEB_CLIENT_ID,
    Browser,
    LocalKeycloak,
    NoAuthorizationCodeError,
    Pkce,
    Response,
    authorization_code,
    latest_mail_text,
    local_setting,
    web_redirect_uri,
)

REALM_FILE = REPOSITORY_ROOT / "infrastructure" / "keycloak" / "realm-property-focus.json"
REALM_SCRIPT = REPOSITORY_ROOT / "backend" / "scripts" / "keycloak_realm.py"
REGISTRATION_POLICY = "org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy"
LOOPBACK_REDIRECT_URI = "http://127.0.0.1:43117/callback"
HTTPS_REDIRECT_URI = "https://mcp-client.example.test/callback"

# Keys whose values would be credentials if they held anything but an environment placeholder.
SECRET_KEY = re.compile(r"secret|password|credential|private.?key", re.IGNORECASE)
# Keys that match SECRET_KEY but hold a rule about passwords, never a password.
PASSWORD_RULE_KEYS = frozenset({"passwordPolicy"})
# An environment placeholder with no default, or an empty default: ${NAME} or ${NAME:}.
EMPTY_PLACEHOLDER = re.compile(r"\$\{[A-Z][A-Z0-9_]*:?\}")


def public_client_metadata(**overrides: Any) -> dict[str, Any]:
    """Registration metadata of a public loopback client, as an MCP client would send it."""
    return {
        "client_name": "Synthetic MCP client",
        "redirect_uris": [LOOPBACK_REDIRECT_URI],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        **overrides,
    }


def redirect_error(response: Response, redirect_uri: str) -> str:
    """Return the OAuth ``error`` of a redirect back to the client that carries no code."""
    assert response.location is not None, f"expected a redirect, got {response.status}"
    assert response.location.startswith(redirect_uri)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(response.location).query)
    assert "code" not in query
    return query["error"][0]


def test_discovery_names_the_realm_issuer_and_registration_endpoint(
    keycloak: LocalKeycloak,
) -> None:
    discovery = keycloak.realm.discovery

    assert discovery["issuer"] == "http://localhost:8080/realms/property-focus"
    assert discovery["registration_endpoint"] == f"{ISSUER}/clients-registrations/openid-connect"


def test_self_registered_user_gets_tokens_only_after_verifying_email(
    keycloak: LocalKeycloak,
) -> None:
    browser = Browser()
    redirect_uri = web_redirect_uri()
    pkce = Pkce.new()
    login_page = browser.get(
        keycloak.realm.authorization_url(
            WEB_CLIENT_ID,
            redirect_uri,
            code_challenge=pkce.challenge,
            code_challenge_method="S256",
        )
    )
    registration_page = browser.get(login_page.link("login-actions/registration"))
    form = registration_page.form("login-actions/registration")
    email = f"signup-{uuid.uuid4().hex[:12]}@example.test"
    # With email verification on, Keycloak asks for the password only after verification.
    registered = browser.follow(
        browser.post(
            form.action,
            {**form.fields, "email": email, "firstName": "Synthetic", "lastName": "Buyer"},
        ),
        redirect_uri,
    )
    (user,) = keycloak.admin.get(f"/users?email={urllib.parse.quote(email)}&exact=true")
    keycloak.track_user(user["id"])

    assert user["emailVerified"] is False
    assert user["requiredActions"] == ["VERIFY_EMAIL", "UPDATE_PASSWORD"]
    with pytest.raises(NoAuthorizationCodeError):
        authorization_code(registered, redirect_uri)

    match = re.search(r"https?://\S+/login-actions/action-token\?\S+", latest_mail_text(email))
    assert match is not None, "the verification email carries no verification link"
    password_page = browser.follow(browser.get(match.group(0)), redirect_uri)
    password_form = password_page.form("login-actions/required-action")
    password = secrets.token_urlsafe(18)
    signed_in = browser.follow(
        browser.post(
            password_form.action,
            {**password_form.fields, "password-new": password, "password-confirm": password},
        ),
        redirect_uri,
    )
    tokens = keycloak.realm.exchange_code(
        authorization_code(signed_in, redirect_uri),
        client_id=WEB_CLIENT_ID,
        redirect_uri=redirect_uri,
        pkce=pkce,
    )

    assert tokens.access_claims["email"] == email
    assert tokens.access_claims["email_verified"] is True


def test_sign_in_page_offers_password_reset(keycloak: LocalKeycloak) -> None:
    redirect_uri = web_redirect_uri()
    pkce = Pkce.new()
    login_page = Browser().get(
        keycloak.realm.authorization_url(
            WEB_CLIENT_ID,
            redirect_uri,
            code_challenge=pkce.challenge,
            code_challenge_method="S256",
        )
    )

    assert login_page.link("login-actions/reset-credentials")


@pytest.mark.parametrize(
    "password",
    [
        pytest.param(lambda email: "Short-pw1!", id="shorter-than-12"),
        pytest.param(lambda email: email, id="the-email-address"),
    ],
)
def test_realm_refuses_a_weak_password(
    keycloak: LocalKeycloak, password: Callable[[str], str]
) -> None:
    user = keycloak.create_user()
    credential = {"type": "password", "value": password(user.email), "temporary": False}

    with pytest.raises(AssertionError, match=r": 400 .*invalidPassword"):
        keycloak.admin.request("PUT", f"/users/{user.id}/reset-password", credential)


@pytest.mark.parametrize(
    "pkce_parameters",
    [
        pytest.param({}, id="no-code-challenge"),
        pytest.param(
            {"code_challenge": "a" * 43, "code_challenge_method": "plain"}, id="plain-method"
        ),
    ],
)
def test_web_client_rejects_authorization_without_s256_pkce(
    keycloak: LocalKeycloak, pkce_parameters: dict[str, str]
) -> None:
    redirect_uri = web_redirect_uri()
    url = keycloak.realm.authorization_url(WEB_CLIENT_ID, redirect_uri, **pkce_parameters)

    assert redirect_error(Browser().get(url), redirect_uri) == "invalid_request"


def test_access_token_carries_api_audience_and_bearer_type(keycloak: LocalKeycloak) -> None:
    claims = keycloak.pkce_sign_in(keycloak.create_user()).access_claims

    assert "property-focus-api" in claims["aud"]
    assert claims["typ"] == "Bearer"
    assert claims["iss"] == ISSUER


def test_no_client_allows_password_or_implicit_grant_or_service_account(
    keycloak: LocalKeycloak,
) -> None:
    clients = keycloak.admin.get("/clients")

    assert WEB_CLIENT_ID in {client["clientId"] for client in clients}
    for client in clients:
        assert not client["directAccessGrantsEnabled"], client["clientId"]
        assert not client["implicitFlowEnabled"], client["clientId"]
        assert not client["serviceAccountsEnabled"], client["clientId"]
    web = next(client for client in clients if client["clientId"] == WEB_CLIENT_ID)
    assert web["publicClient"] is True
    assert web["standardFlowEnabled"] is True
    assert web["attributes"]["pkce.code.challenge.method"] == "S256"
    assert web["redirectUris"] == [web_redirect_uri()]


def test_realm_file_holds_no_literal_secret() -> None:
    realm = json.loads(REALM_FILE.read_text())

    def literal_secrets(node: Any, path: str) -> Iterator[str]:
        if isinstance(node, dict):
            for key, value in node.items():
                # Flags such as resetPasswordAllowed name a secret but cannot hold one.
                if SECRET_KEY.search(key) and not (
                    key in PASSWORD_RULE_KEYS
                    or isinstance(value, bool)
                    or value in ("", None, [], {})
                    or (isinstance(value, str) and EMPTY_PLACEHOLDER.fullmatch(value))
                ):
                    yield f"{path}.{key}"
                else:
                    yield from literal_secrets(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                yield from literal_secrets(item, f"{path}[{index}]")

    assert realm["realm"] == "property-focus"
    assert list(literal_secrets(realm, "realm")) == []
    assert "users" not in realm


@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({}, id="loopback-redirect"),
        pytest.param({"redirect_uris": [HTTPS_REDIRECT_URI]}, id="https-redirect"),
        # Keycloak only shows these to the user; it never fetches them.
        pytest.param(
            {
                "client_uri": "https://mcp-client.example.test/",
                "logo_uri": "https://mcp-client.example.test/logo.png",
                "policy_uri": "https://mcp-client.example.test/privacy",
                "tos_uri": "https://mcp-client.example.test/terms",
            },
            id="display-uris",
        ),
    ],
)
def test_registration_accepts_a_public_client(
    keycloak: LocalKeycloak, metadata: dict[str, Any]
) -> None:
    registration = keycloak.register_client(public_client_metadata(**metadata))

    assert registration.status == 201, registration.metadata
    assert registration.metadata["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in registration.metadata
    (client,) = keycloak.admin.get(f"/clients?clientId={registration.metadata['client_id']}")
    assert client["publicClient"] is True
    assert client["consentRequired"] is True
    assert client["fullScopeAllowed"] is False
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"


UNSAFE_REGISTRATIONS = [
    pytest.param({"redirect_uris": ["http://app.example.test/callback"]}, id="http-non-loopback"),
    pytest.param({"redirect_uris": ["https://app.example.test/*"]}, id="wildcard"),
    pytest.param({"redirect_uris": ["com.example.app:/callback"]}, id="custom-scheme"),
    pytest.param({"redirect_uris": ["https://app.example.test/cb#done"]}, id="fragment"),
    pytest.param({"token_endpoint_auth_method": "client_secret_basic"}, id="confidential"),
    pytest.param(
        {
            "token_endpoint_auth_method": "private_key_jwt",
            "jwks_uri": "https://app.example.test/jwks",
        },
        id="private-key-jwt",
    ),
]


def client_ids(keycloak: LocalKeycloak) -> set[str]:
    return {client["clientId"] for client in keycloak.admin.get("/clients")}


@pytest.mark.parametrize("metadata", UNSAFE_REGISTRATIONS)
def test_registration_rejects_unsafe_client(
    keycloak: LocalKeycloak, metadata: dict[str, Any]
) -> None:
    registration = keycloak.register_client(public_client_metadata(**metadata))

    assert registration.status == 403, registration.metadata


@pytest.mark.parametrize("metadata", UNSAFE_REGISTRATIONS)
def test_rejected_registration_leaves_no_client_behind(
    keycloak: LocalKeycloak, metadata: dict[str, Any]
) -> None:
    before = client_ids(keycloak)

    registration = keycloak.register_client(public_client_metadata(**metadata))

    assert registration.status == 403, registration.metadata
    assert "client_id" not in registration.metadata
    assert client_ids(keycloak) == before


class FetchListener:
    """A TCP listener in its own container on the local Compose network, which records every
    connection made to it. Keycloak reaches it as ``http(s)://<host>:8000``."""

    PORT = 8000
    _SCRIPT = """
import socket
server = socket.create_server(("0.0.0.0", 8000))
print("listening", flush=True)
while True:
    connection, _ = server.accept()
    print("connection", flush=True)
    connection.close()
"""

    def __init__(self) -> None:
        self.host = f"pf-fetch-listener-{uuid.uuid4().hex[:8]}"

    def start(self) -> None:
        """Start the container (from the pinned MiniStack image, which has Python) and wait
        until the Keycloak container can connect to it."""
        compose(
            "run",
            "--detach",
            "--rm",
            "--no-deps",
            "--name",
            self.host,
            "--entrypoint",
            "python",
            "ministack",
            "-u",
            "-c",
            self._SCRIPT,
        )
        deadline = time.monotonic() + 30
        probe = f"exec 3<>/dev/tcp/{self.host}/{self.PORT}"
        while compose("exec", "-T", "keycloak", "bash", "-c", probe, check=False).returncode:
            assert time.monotonic() < deadline, f"Keycloak cannot reach {self.host}"
            time.sleep(0.5)

    def connections(self) -> int:
        """How many connections the listener has accepted so far."""
        logs = subprocess.run(
            ["docker", "logs", self.host], capture_output=True, text=True, check=True
        )
        return logs.stdout.splitlines().count("connection")

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "--force", self.host], capture_output=True, check=False)


def compose(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose`` for the root ``compose.yaml``."""
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture(scope="module")
def fetch_listener() -> Iterator[FetchListener]:
    listener = FetchListener()
    try:
        listener.start()
        yield listener
    finally:
        listener.stop()


# Client metadata naming a URI that Keycloak itself would request: at registration (the
# sector identifier), at authorization (request URIs), at token time (the key set) or at
# sign-out (back-channel logout). Each takes the base URL of the listener.
SERVER_FETCHED_URIS = [
    pytest.param(
        lambda base: {"subject_type": "pairwise", "sector_identifier_uri": f"{base}/sector"},
        id="sector_identifier_uri",
    ),
    pytest.param(lambda base: {"request_uris": [f"{base}/request"]}, id="request_uris"),
    pytest.param(lambda base: {"jwks_uri": f"{base}/jwks"}, id="jwks_uri"),
    pytest.param(
        lambda base: {"backchannel_logout_uri": f"{base}/logout"}, id="backchannel_logout_uri"
    ),
]


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("server_fetched_uri", SERVER_FETCHED_URIS)
def test_registration_refuses_uris_keycloak_would_fetch_without_fetching_them(
    keycloak: LocalKeycloak,
    fetch_listener: FetchListener,
    server_fetched_uri: Callable[[str], dict[str, Any]],
    scheme: str,
) -> None:
    before = client_ids(keycloak)
    connections = fetch_listener.connections()
    base = f"{scheme}://{fetch_listener.host}:{FetchListener.PORT}"

    registration = keycloak.register_client(public_client_metadata(**server_fetched_uri(base)))

    assert registration.status == 403, registration.metadata
    assert client_ids(keycloak) == before
    assert fetch_listener.connections() == connections


def test_registration_disables_password_and_implicit_grants(keycloak: LocalKeycloak) -> None:
    registration = keycloak.register_client(
        public_client_metadata(
            grant_types=["authorization_code", "password", "implicit"],
            response_types=["code", "token"],
        )
    )

    assert registration.status == 201, registration.metadata
    (client,) = keycloak.admin.get(f"/clients?clientId={registration.metadata['client_id']}")
    assert not client["directAccessGrantsEnabled"]
    assert not client["implicitFlowEnabled"]


def test_no_client_gets_tokens_through_the_client_credentials_grant(
    keycloak: LocalKeycloak,
) -> None:
    # Keycloak still marks a registered public client that asks for client_credentials as
    # having a service account; the realm refuses that grant to every client instead.
    registration = keycloak.register_client(
        public_client_metadata(grant_types=["authorization_code", "client_credentials"])
    )
    assert registration.status == 201, registration.metadata
    client_id = registration.metadata["client_id"]
    try:
        response = Browser().post(
            keycloak.realm.discovery["token_endpoint"],
            {"grant_type": "client_credentials", "client_id": client_id},
        )
        assert response.status in (400, 401, 403)
        assert "access_token" not in response.json()
    finally:
        keycloak.delete_client(client_id)


def test_registration_stops_at_the_client_cap(keycloak: LocalKeycloak) -> None:
    (policy,) = [
        component
        for component in keycloak.admin.get(f"/components?type={REGISTRATION_POLICY}")
        if component["providerId"] == "max-clients" and component["subType"] == "anonymous"
    ]
    original_cap = policy["config"]["max-clients"]
    before = client_ids(keycloak)
    client_count = len(before)
    keycloak.admin.request(
        "PUT",
        f"/components/{policy['id']}",
        {**policy, "config": {**policy["config"], "max-clients": [str(client_count)]}},
    )
    try:
        registration = keycloak.register_client(public_client_metadata())
    finally:
        keycloak.admin.request(
            "PUT",
            f"/components/{policy['id']}",
            {**policy, "config": {**policy["config"], "max-clients": original_cap}},
        )

    assert registration.status == 403, registration.metadata
    assert client_ids(keycloak) == before


def test_registered_client_refuses_the_plain_pkce_method(keycloak: LocalKeycloak) -> None:
    registration = keycloak.register_client(public_client_metadata())
    assert registration.status == 201, registration.metadata
    url = keycloak.realm.authorization_url(
        registration.metadata["client_id"],
        LOOPBACK_REDIRECT_URI,
        code_challenge="a" * 43,
        code_challenge_method="plain",
    )

    assert redirect_error(Browser().get(url), LOOPBACK_REDIRECT_URI) == "invalid_request"


def test_registered_client_requires_pkce_and_asks_for_consent(keycloak: LocalKeycloak) -> None:
    registration = keycloak.register_client(public_client_metadata())
    assert registration.status == 201, registration.metadata
    client_id = registration.metadata["client_id"]
    realm = keycloak.realm

    without_pkce = Browser().get(realm.authorization_url(client_id, LOOPBACK_REDIRECT_URI))
    assert redirect_error(without_pkce, LOOPBACK_REDIRECT_URI) == "invalid_request"

    user = keycloak.create_user()
    browser = Browser()
    pkce = Pkce.new()
    login_page = browser.get(
        realm.authorization_url(
            client_id,
            LOOPBACK_REDIRECT_URI,
            code_challenge=pkce.challenge,
            code_challenge_method="S256",
        )
    )
    consent_page = realm.submit_login(
        browser, login_page, user.email, user.password, LOOPBACK_REDIRECT_URI
    )
    consent = consent_page.form("login-actions/consent")
    assert "Synthetic MCP client" in consent_page.body

    granted = browser.follow(
        browser.post(consent.action, {**consent.fields, "accept": "Yes"}), LOOPBACK_REDIRECT_URI
    )
    tokens = realm.exchange_code(
        authorization_code(granted, LOOPBACK_REDIRECT_URI),
        client_id=client_id,
        redirect_uri=LOOPBACK_REDIRECT_URI,
        pkce=pkce,
    )
    assert "property-focus-api" in tokens.access_claims["aud"]


def realm_script_environment() -> dict[str, str]:
    """The environment ``keycloak_realm.py`` needs for the local server: the admin
    credentials and the realm file's placeholder values exactly as the local Keycloak
    container got them, read from the resolved root ``compose.yaml``, which writes a literal
    ``$`` as ``$$``."""
    config = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    container = json.loads(config.stdout)["services"]["keycloak"]["environment"]
    return {
        **os.environ,
        **{
            name: value.replace("$$", "$")
            for name, value in container.items()
            if name.startswith("PF_")
        },
        "PF_KEYCLOAK_ADMIN_USERNAME": local_setting("PF_KEYCLOAK_ADMIN_USERNAME"),
        "PF_KEYCLOAK_ADMIN_PASSWORD": local_setting("PF_KEYCLOAK_ADMIN_PASSWORD"),
    }


def realm_script(command: str) -> subprocess.CompletedProcess[str]:
    """Run ``keycloak_realm.py <command>`` against the local server."""
    return subprocess.run(
        [sys.executable, REALM_SCRIPT, command, "--url", "http://localhost:8080"],
        env=realm_script_environment(),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def realm_restored() -> Iterator[None]:
    """Put the realm back in line with the file after a test that changes it by hand."""
    yield
    restored = realm_script("apply")
    assert restored.returncode == 0, restored.stderr


def test_second_realm_apply_changes_nothing() -> None:
    first = realm_script("apply")
    second = realm_script("apply")
    check = realm_script("check")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "keycloak_realm: 0 change(s) applied"
    assert check.returncode == 0, check.stdout


@pytest.mark.usefixtures("realm_restored")
def test_realm_check_detects_a_manual_change(keycloak: LocalKeycloak) -> None:
    (policy,) = [
        component
        for component in keycloak.admin.get(f"/components?type={REGISTRATION_POLICY}")
        if component["providerId"] == "max-clients" and component["subType"] == "anonymous"
    ]
    keycloak.admin.request(
        "PUT",
        f"/components/{policy['id']}",
        {**policy, "config": {**policy["config"], "max-clients": ["1000"]}},
    )

    check = realm_script("check")

    assert check.returncode == 1
    assert "registration policy Max Clients Limit (anonymous): config.max-clients" in check.stdout
    assert realm_script("apply").returncode == 0
    assert realm_script("check").returncode == 0


@pytest.mark.usefixtures("realm_restored")
def test_realm_apply_keeps_users_and_registered_clients(keycloak: LocalKeycloak) -> None:
    user = keycloak.create_user()
    registration = keycloak.register_client(public_client_metadata())
    assert registration.status == 201, registration.metadata
    client_id = registration.metadata["client_id"]
    # Give apply something to change, so it really runs its updates.
    keycloak.admin.request("PUT", "", {"displayName": "Changed by hand"})

    applied = realm_script("apply")

    assert applied.returncode == 0, applied.stderr
    assert "realm settings: displayName" in applied.stdout
    assert keycloak.admin.get(f"/users/{user.id}")["email"] == user.email
    assert len(keycloak.admin.get(f"/clients?clientId={client_id}")) == 1
