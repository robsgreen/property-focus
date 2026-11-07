"""Keep a running Keycloak realm in step with its realm file in Git, through the admin API.

Keycloak imports the realm file only when the realm does not exist yet. For every later
change, ``deploy.sh`` runs this script against the server's loopback port:

- ``apply`` updates the realm to match the file: realm settings, default client scopes,
  client scopes and their protocol mappers, the file's clients (with exactly the default
  client scopes a file client lists), client registration policies, client profiles and
  client policies. It creates what is missing and changes what differs. It never deletes
  users or clients; dynamically registered MCP clients, and every user, are left as they
  are, except that a client scope the file grants only by listing it on a client (one that
  is not a realm default, such as the REST API audience scope) is removed from every other
  client, which also gets any realm default client scope it lacks (so an MCP client
  registered while the API scope was a realm default gets the MCP scope instead).
  Registration policies and scope mappers missing from the file are removed, because the
  file lists them completely. A second ``apply`` with an unchanged file changes nothing.
- ``check`` reports each difference between the running realm and the file and exits 1
  when there is any, else 0.
- ``ensure-user --email EMAIL`` makes sure a user with that verified email exists, enabled,
  with the password in ``PF_SMOKE_USER_PASSWORD`` (the dogfood smoke user).

The file's environment placeholders, ``${NAME}`` or ``${NAME:default}`` with an upper-case
name, are filled from the process environment the way Keycloak's import fills them, so the
script must see the same values the Keycloak container got. Placeholders with lower-case
names (Keycloak message keys such as ``${profileScopeConsentText}``) stay as they are.

Only fields the file names are compared; values Keycloak adds on its own are ignored.
Lists of plain values compare as sets; lists of objects compare in order. Stored secrets
(the SMTP password) cannot be compared, because Keycloak returns them masked; a changed
secret is set through the admin console (docs/operations.md). The built-in
clients ``account``, ``account-console`` and ``security-admin-console`` are never updated:
the realm's ``client-rules`` policy rejects their relative redirect URIs on any update.

The admin API is reached as the bootstrap or operator admin of the ``master`` realm
(``PF_KEYCLOAK_ADMIN_USERNAME`` and ``PF_KEYCLOAK_ADMIN_PASSWORD``) through its
``admin-cli`` client. Only the standard library is used, so the script runs on a host
with a plain ``python3``.

Usage: ``python3 backend/scripts/keycloak_realm.py {apply,check,ensure-user} [--url URL]``
Exit codes: 0 done (``check``: no differences), 1 differences found or the server refused a
request, 2 invalid usage or a missing variable.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REALM_FILE = REPOSITORY_ROOT / "infrastructure" / "keycloak" / "realm-property-focus.json"
DEFAULT_URL = "http://localhost:8080"

REGISTRATION_POLICY = "org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy"
# Built-in clients whose relative redirect URIs the realm's client-rules policy rejects.
UNMANAGED_CLIENTS = frozenset({"account", "account-console", "security-admin-console"})
# Realm file sections reconciled on their own rather than as realm settings.
_SECTIONS = frozenset(
    {
        "clientScopes",
        "defaultDefaultClientScopes",
        "clients",
        "components",
        "clientProfiles",
        "clientPolicies",
    }
)
# How many clients one admin API page lists.
_CLIENT_PAGE_SIZE = 100
# How Keycloak returns a stored secret it will not reveal.
_MASKED_SECRET = "**********"
_PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?::([^}]*))?\}")
# Admin tokens are renewed this long before they expire.
_TOKEN_MARGIN_SECONDS = 10
# How long admin sign-in waits out a 503, which a server that has just started answers
# until it has created its bootstrap admin.
_STARTUP_WAIT_SECONDS = 60


class MissingVariableError(LookupError):
    """The realm file or a command needs environment variables that are not set."""


class AdminRequestError(RuntimeError):
    """The admin API refused a request."""


def load_realm(path: Path, environ: Mapping[str, str]) -> dict[str, Any]:
    """The realm file at ``path`` with its environment placeholders filled from ``environ``.

    Raises ``MissingVariableError`` naming every placeholder with no value and no default.
    """
    missing: set[str] = set()

    def fill(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        value = environ.get(name)
        if value is not None:
            return value
        if default is None:
            missing.add(name)
            return match.group(0)
        return default

    def walk(node: Any) -> Any:
        if isinstance(node, str):
            return _PLACEHOLDER.sub(fill, node)
        if isinstance(node, list):
            return [walk(item) for item in node]
        if isinstance(node, dict):
            return {key: walk(value) for key, value in node.items()}
        return node

    realm: dict[str, Any] = walk(json.loads(path.read_text(encoding="utf-8")))
    if missing:
        raise MissingVariableError(f"set {', '.join(sorted(missing))} (realm file placeholders)")
    return realm


def differences(desired: Any, actual: Any, path: str = "") -> list[str]:
    """The paths at which ``actual`` does not hold what ``desired`` names.

    Mappings compare only the keys ``desired`` has. Lists of plain values compare as sets,
    lists of mappings item by item in order. A masked secret matches any value, because
    Keycloak never reveals what it stores.
    """
    if isinstance(desired, Mapping):
        if not isinstance(actual, Mapping):
            return [path or "."]
        found = []
        for key, value in desired.items():
            child = f"{path}.{key}" if path else str(key)
            if key not in actual:
                if value not in ("", [], {}):  # Keycloak omits empty values
                    found.append(child)
                continue
            found.extend(differences(value, actual[key], child))
        return found
    if isinstance(desired, list):
        if not isinstance(actual, list) or len(desired) != len(actual):
            return [path or "."]
        if all(not isinstance(item, Mapping | list) for item in desired):
            return [] if sorted(map(str, desired)) == sorted(map(str, actual)) else [path]
        return [
            found
            for index, (want, have) in enumerate(zip(desired, actual, strict=True))
            for found in differences(want, have, f"{path}[{index}]")
        ]
    if actual == _MASKED_SECRET:
        return []
    return [] if desired == actual else [path or "."]


def merged(actual: Mapping[str, Any], desired: Mapping[str, Any]) -> dict[str, Any]:
    """``actual`` with ``desired`` laid over it; nested mappings merge, other values replace."""
    result = dict(actual)
    for key, value in desired.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = merged(result[key], value)
        else:
            result[key] = value
    return result


class KeycloakAdmin:
    """The admin REST API of one Keycloak server, called as a ``master`` realm admin."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._credentials = {"username": username, "password": password}
        self._token = ""
        self._token_expires_at = 0.0

    def request(self, method: str, path: str, body: Any = None) -> Any:
        """Call ``/admin/realms`` + ``path``; return the decoded JSON body, or None.

        Raises ``AdminRequestError`` for a 4xx or 5xx answer.
        """
        url = f"{self._base_url}/admin/realms{path}"
        data = None if body is None else json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self._access_token()}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, data=data, headers=headers, method=method), timeout=30
            ) as response:
                text = response.read().decode()
        except HTTPError as error:
            raise AdminRequestError(
                f"{method} {path}: {error.code} {error.read().decode()[:500]}"
            ) from None
        return json.loads(text) if text else None

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def _access_token(self) -> str:
        if time.monotonic() < self._token_expires_at - _TOKEN_MARGIN_SECONDS:
            return self._token
        form = urllib.parse.urlencode(
            {"grant_type": "password", "client_id": "admin-cli", **self._credentials}
        ).encode()
        url = f"{self._base_url}/realms/master/protocol/openid-connect/token"
        deadline = time.monotonic() + _STARTUP_WAIT_SECONDS
        while True:
            started = time.monotonic()
            try:
                with urllib.request.urlopen(url, form, timeout=30) as response:
                    token = json.loads(response.read())
                break
            except HTTPError as error:
                if error.code != 503 or started > deadline:
                    detail = error.read()[:200]
                    raise AdminRequestError(f"admin sign-in: {error.code} {detail!r}") from None
                time.sleep(float(error.headers.get("Retry-After") or 2))
        self._token = token["access_token"]
        self._token_expires_at = started + token["expires_in"]
        return self._token


@dataclass(frozen=True)
class Change:
    """One difference between the running realm and the file, and how to remove it."""

    description: str
    apply: Callable[[], None]


class RealmReconciler:
    """Compares one running realm with its desired state and brings it in line."""

    def __init__(self, admin: KeycloakAdmin, desired: Mapping[str, Any]) -> None:
        self._admin = admin
        self._desired = desired
        self._realm_path = f"/{urllib.parse.quote(desired['realm'])}"

    def changes(self) -> list[Change]:
        """Every change ``apply`` would make, in the order it makes them."""
        return [
            *self._realm_settings(),
            *self._client_scopes(),
            *self._default_client_scopes(),
            *self._clients(),
            *self._registration_policies(),
            *self._client_policies("clientProfiles", "profiles"),
            *self._client_policies("clientPolicies", "policies"),
        ]

    def _call(self, method: str, path: str, body: Any = None) -> Callable[[], None]:
        def call() -> None:
            self._admin.request(method, f"{self._realm_path}{path}", body)

        return call

    def _get(self, path: str) -> Any:
        return self._admin.get(f"{self._realm_path}{path}")

    def _realm_settings(self) -> Iterator[Change]:
        desired = {key: value for key, value in self._desired.items() if key not in _SECTIONS}
        found = differences(desired, self._get(""))
        if found:
            changed = {path.split(".", 1)[0] for path in found}
            body = {key: desired[key] for key in changed}
            yield Change(f"realm settings: {', '.join(sorted(found))}", self._call("PUT", "", body))

    def _client_scopes(self) -> Iterator[Change]:
        actual = {scope["name"]: scope for scope in self._get("/client-scopes")}
        for scope in self._desired.get("clientScopes", []):
            name = scope["name"]
            mappers = scope.get("protocolMappers", [])
            current = actual.get(name)
            if current is None:
                yield Change(
                    f"client scope {name}: create", self._call("POST", "/client-scopes", scope)
                )
                continue
            settings = {key: value for key, value in scope.items() if key != "protocolMappers"}
            found = differences(settings, current)
            if found:
                body = merged(current, settings)
                body.pop("protocolMappers", None)  # mappers are reconciled one by one below
                yield Change(
                    f"client scope {name}: {', '.join(found)}",
                    self._call("PUT", f"/client-scopes/{current['id']}", body),
                )
            yield from self._mappers(
                f"client scope {name}",
                f"/client-scopes/{current['id']}/protocol-mappers/models",
                mappers,
                current.get("protocolMappers", []),
            )

    def _mappers(
        self, owner: str, path: str, desired: list[dict[str, Any]], actual: list[dict[str, Any]]
    ) -> Iterator[Change]:
        current = {mapper["name"]: mapper for mapper in actual}
        for mapper in desired:
            existing = current.pop(mapper["name"], None)
            if existing is None:
                yield Change(
                    f"{owner}: create mapper {mapper['name']}", self._call("POST", path, mapper)
                )
            elif found := differences(mapper, existing):
                yield Change(
                    f"{owner}: mapper {mapper['name']}: {', '.join(found)}",
                    self._call("PUT", f"{path}/{existing['id']}", merged(existing, mapper)),
                )
        for name, extra in current.items():
            yield Change(
                f"{owner}: remove mapper {name}", self._call("DELETE", f"{path}/{extra['id']}")
            )

    def _default_client_scopes(self) -> Iterator[Change]:
        if "defaultDefaultClientScopes" not in self._desired:
            return
        desired = set(self._desired["defaultDefaultClientScopes"])
        actual = {
            scope["name"]: scope["id"] for scope in self._get("/default-default-client-scopes")
        }
        for name in sorted(desired - actual.keys()):

            def add(name: str = name) -> None:
                scopes = {scope["name"]: scope["id"] for scope in self._get("/client-scopes")}
                self._call("PUT", f"/default-default-client-scopes/{scopes[name]}")()

            yield Change(f"default client scopes: add {name}", add)
        for name in sorted(actual.keys() - desired):
            yield Change(
                f"default client scopes: remove {name}",
                self._call("DELETE", f"/default-default-client-scopes/{actual[name]}"),
            )

    def _clients(self) -> Iterator[Change]:
        """Changes to the file's clients, then removals of restricted client scopes.

        A file client's ``defaultClientScopes`` are reconciled through their own endpoints,
        because a client update ignores them. A restricted scope is one of the file's client
        scopes that is not a realm default: only a file client that lists it may hold it, and
        any other client holding one is given the realm default scopes in its place.
        """
        listed_scopes: dict[str, set[str]] = {}
        for client in self._desired.get("clients", []):
            client_id = client["clientId"]
            if client_id in UNMANAGED_CLIENTS:
                continue
            settings = {key: value for key, value in client.items() if key != "defaultClientScopes"}
            if "defaultClientScopes" in client:
                listed_scopes[client_id] = set(client["defaultClientScopes"])
            query = urllib.parse.urlencode({"clientId": client_id})
            matches = self._get(f"/clients?{query}")
            if not matches:
                # A created client takes the default scopes it names.
                yield Change(f"client {client_id}: create", self._call("POST", "/clients", client))
                continue
            current = matches[0]
            if found := differences(settings, current):
                yield Change(
                    f"client {client_id}: {', '.join(found)}",
                    self._call("PUT", f"/clients/{current['id']}", merged(current, settings)),
                )
            if client_id in listed_scopes:
                yield from self._client_default_scopes(current, listed_scopes[client_id])

        realm_defaults = set(self._desired.get("defaultDefaultClientScopes", []))
        restricted = {
            scope["name"] for scope in self._desired.get("clientScopes", [])
        } - realm_defaults
        for current in self._all_clients():
            client_id = current["clientId"]
            if client_id in listed_scopes or client_id in UNMANAGED_CLIENTS:
                continue
            held = set(current.get("defaultClientScopes", []))
            if held & restricted:
                # Registered before the scope was restricted, when it was a realm default:
                # the client moves to today's realm defaults, as a new registration gets.
                yield from self._client_default_scopes(
                    current, (held - restricted) | realm_defaults
                )

    def _client_default_scopes(
        self, client: Mapping[str, Any], desired: set[str]
    ) -> Iterator[Change]:
        """Changes that give ``client`` exactly the default client scopes ``desired``."""
        actual = set(client.get("defaultClientScopes", []))
        path = f"/clients/{client['id']}/default-client-scopes"

        def scope_call(method: str, name: str) -> Callable[[], None]:
            # Looked up when applied, so a scope created earlier in the same run is found.
            def call() -> None:
                scopes = {scope["name"]: scope["id"] for scope in self._get("/client-scopes")}
                self._call(method, f"{path}/{scopes[name]}")()

            return call

        for name in sorted(desired - actual):
            yield Change(
                f"client {client['clientId']}: add default scope {name}", scope_call("PUT", name)
            )
        for name in sorted(actual - desired):
            yield Change(
                f"client {client['clientId']}: remove default scope {name}",
                scope_call("DELETE", name),
            )

    def _all_clients(self) -> Iterator[dict[str, Any]]:
        """Every client of the realm, built-in and dynamically registered ones included."""
        first = 0
        while True:
            query = urllib.parse.urlencode({"first": first, "max": _CLIENT_PAGE_SIZE})
            page = self._get(f"/clients?{query}")
            yield from page
            if len(page) < _CLIENT_PAGE_SIZE:
                return
            first += _CLIENT_PAGE_SIZE

    def _registration_policies(self) -> Iterator[Change]:
        desired_policies = self._desired.get("components", {}).get(REGISTRATION_POLICY)
        if desired_policies is None:
            return
        query = urllib.parse.urlencode({"type": REGISTRATION_POLICY})
        actual = {
            (policy.get("subType"), policy["name"]): policy
            for policy in self._get(f"/components?{query}")
        }
        realm_id = self._get("")["id"]
        for policy in desired_policies:
            key = (policy.get("subType"), policy["name"])
            label = f"registration policy {policy['name']} ({key[0]})"
            existing = actual.pop(key, None)
            if existing is None:
                body = {**policy, "providerType": REGISTRATION_POLICY, "parentId": realm_id}
                yield Change(f"{label}: create", self._call("POST", "/components", body))
            elif found := differences(policy, existing):
                yield Change(
                    f"{label}: {', '.join(found)}",
                    self._call("PUT", f"/components/{existing['id']}", merged(existing, policy)),
                )
        for (sub_type, name), extra in actual.items():
            yield Change(
                f"registration policy {name} ({sub_type}): remove",
                self._call("DELETE", f"/components/{extra['id']}"),
            )

    def _client_policies(self, section: str, kind: str) -> Iterator[Change]:
        if section not in self._desired:
            return
        desired = self._desired[section]
        found = differences(desired, self._get(f"/client-policies/{kind}"))
        if found:
            yield Change(
                f"client {kind}: {', '.join(found)}",
                self._call("PUT", f"/client-policies/{kind}", desired),
            )


def ensure_user(admin: KeycloakAdmin, realm: str, email: str, password: str) -> str:
    """Create or update ``email``'s user: enabled, email verified, with ``password``.

    Returns ``"created"`` or ``"updated"``.
    """
    realm_path = f"/{urllib.parse.quote(realm)}"
    query = urllib.parse.urlencode({"email": email, "exact": "true"})
    matches = admin.get(f"{realm_path}/users?{query}")
    credential = {"type": "password", "value": password, "temporary": False}
    if not matches:
        admin.request(
            "POST",
            f"{realm_path}/users",
            {
                "username": email,
                "email": email,
                "emailVerified": True,
                "enabled": True,
                "firstName": "Smoke",
                "lastName": "Check",
                "credentials": [credential],
            },
        )
        return "created"
    user_id = matches[0]["id"]
    admin.request(
        "PUT",
        f"{realm_path}/users/{user_id}",
        {**matches[0], "emailVerified": True, "enabled": True, "requiredActions": []},
    )
    admin.request("PUT", f"{realm_path}/users/{user_id}/reset-password", credential)
    return "updated"


def _required(environ: Mapping[str, str], *names: str) -> list[str]:
    missing = [name for name in names if not environ.get(name)]
    if missing:
        raise MissingVariableError(f"set {', '.join(missing)}")
    return [environ[name] for name in names]


def main(argv: list[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["apply", "check", "ensure-user"])
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Keycloak base URL ({DEFAULT_URL})")
    parser.add_argument("--realm-file", type=Path, default=DEFAULT_REALM_FILE)
    parser.add_argument("--email", help="ensure-user: the user's email address")
    arguments = parser.parse_args(argv)

    try:
        username, password = _required(
            environ, "PF_KEYCLOAK_ADMIN_USERNAME", "PF_KEYCLOAK_ADMIN_PASSWORD"
        )
        desired = load_realm(arguments.realm_file, environ)
        if arguments.command == "ensure-user":
            if not arguments.email:
                parser.error("ensure-user needs --email")
            (user_password,) = _required(environ, "PF_SMOKE_USER_PASSWORD")
    except MissingVariableError as error:
        print(f"keycloak_realm: {error}", file=sys.stderr)
        return 2

    admin = KeycloakAdmin(arguments.url, username, password)
    try:
        if arguments.command == "ensure-user":
            outcome = ensure_user(admin, desired["realm"], arguments.email, user_password)
            print(f"keycloak_realm: user {arguments.email} {outcome}")
            return 0
        changes = RealmReconciler(admin, desired).changes()
        verb = "differs" if arguments.command == "check" else "applying"
        for change in changes:
            print(f"{verb}: {change.description}")
            if arguments.command == "apply":
                change.apply()
    except (AdminRequestError, OSError) as error:  # OSError: the server cannot be reached
        print(f"keycloak_realm: {error}", file=sys.stderr)
        return 1
    if arguments.command == "check":
        print(f"keycloak_realm: {len(changes)} difference(s) from {arguments.realm_file.name}")
        return 1 if changes else 0
    print(f"keycloak_realm: {len(changes)} change(s) applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
