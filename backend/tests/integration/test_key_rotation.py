"""Signing key rotation in the local Keycloak realm, seen by the application's token verifier.

A new RS256 key provider with a higher priority makes Keycloak sign with a new key, as a
rotation does; deleting it removes the key from the published key set. The verifier runs on
a manual clock so the refetch rate limit and the key-set age are exercised without waiting.
"""

import asyncio
import base64
import json
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from jwt import PyJWKClient
from support.keycloak import LocalKeycloak, TestUser, Tokens, web_redirect_uri

from property_focus.adapters.auth.oidc import InvalidTokenError, OidcVerifier, verifier_for
from property_focus.config import Settings

KEY_PROVIDER_TYPE = "org.keycloak.keys.KeyProvider"
# Above the realm's own generated key (priority 100), so Keycloak signs with the new key.
ROTATED_KEY_PRIORITY = "1000"
# Seconds on the verifier's clock: past the one-minute refetch limit, and past the age at
# which a cached key set is replaced.
AFTER_REFETCH_LIMIT = 61.0
AFTER_KEY_SET_EXPIRY = 700.0


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class RecordingVerifier:
    """A verifier for the configured realm that counts its key-set fetches."""

    def __init__(self, settings: Settings) -> None:
        self.clock = ManualClock()
        self.key_set_fetches = 0
        recorder = self

        class CountingClient(PyJWKClient):
            def fetch_data(self) -> Any:
                recorder.key_set_fetches += 1
                return super().fetch_data()

        self._verifier: OidcVerifier = verifier_for(
            settings, jwks_client_type=CountingClient, clock=self.clock
        )

    def accepts(self, token: str) -> bool:
        try:
            asyncio.run(self._verifier.verify(token))
        except InvalidTokenError:
            return False
        return True


def key_id(token: str) -> str:
    header = token.split(".")[0]
    kid: str = json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))["kid"]
    return kid


def token_naming_key(kid: str) -> str:
    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256', 'kid': kid})}.{part({'sub': 'nobody'})}.c2lnbmF0dXJl"


def sign_in(keycloak: LocalKeycloak, user: TestUser) -> Tokens:
    """A fresh sign-in, bypassing the session's token cache."""
    return keycloak.realm.sign_in(
        user.email, user.password, client_id="property-focus-web", redirect_uri=web_redirect_uri()
    )


@contextmanager
def rotated_realm_key(keycloak: LocalKeycloak) -> Iterator[Callable[[], None]]:
    """Add a higher-priority RS256 key provider to the realm and yield a callable that
    removes it. The provider is gone when the block exits, however it exits."""
    realm_id = keycloak.admin.get("")["id"]
    response = keycloak.admin.request(
        "POST",
        "/components",
        {
            "name": f"rotation-{uuid.uuid4().hex[:8]}",
            "providerId": "rsa-generated",
            "providerType": KEY_PROVIDER_TYPE,
            "parentId": realm_id,
            "config": {
                "priority": [ROTATED_KEY_PRIORITY],
                "enabled": ["true"],
                "active": ["true"],
                "keySize": ["2048"],
                "algorithm": ["RS256"],
            },
        },
    )
    component_id = (response.location or "").rsplit("/", 1)[-1]
    removed = False

    def remove() -> None:
        nonlocal removed
        if not removed:
            keycloak.admin.request("DELETE", f"/components/{component_id}")
            removed = True

    try:
        yield remove
    finally:
        remove()


def test_rotated_key_validates_after_one_refetch_and_a_removed_key_stops_validating(
    settings: Settings, keycloak: LocalKeycloak
) -> None:
    user = keycloak.create_user()
    verifier = RecordingVerifier(settings)
    old_token = sign_in(keycloak, user).access_token
    assert verifier.accepts(old_token)
    assert verifier.key_set_fetches == 1

    with rotated_realm_key(keycloak) as remove_key:
        new_token = sign_in(keycloak, user).access_token
        assert key_id(new_token) != key_id(old_token)

        verifier.clock.now = AFTER_REFETCH_LIMIT - 31
        within_the_limit = verifier.accepts(new_token)
        fetches_within_the_limit = verifier.key_set_fetches

        verifier.clock.now = AFTER_REFETCH_LIMIT
        after_the_limit = verifier.accepts(new_token)
        fetches_after_the_limit = verifier.key_set_fetches

        verifier.clock.now = AFTER_REFETCH_LIMIT + 1
        another_unknown_key = verifier.accepts(token_naming_key("never-published"))
        fetches_after_another_unknown_key = verifier.key_set_fetches

        remove_key()
        verifier.clock.now = AFTER_REFETCH_LIMIT + AFTER_KEY_SET_EXPIRY
        removed_key = verifier.accepts(new_token)
        old_key = verifier.accepts(old_token)

    assert (within_the_limit, fetches_within_the_limit) == (False, 1)
    assert (after_the_limit, fetches_after_the_limit) == (True, 2)
    assert (another_unknown_key, fetches_after_another_unknown_key) == (False, 2)
    assert (removed_key, old_key) == (False, True)
    assert verifier.key_set_fetches == 3
