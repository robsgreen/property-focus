"""Access-token verification: signature, claims, algorithm allowlist and key-set caching.

Tokens come from the signed fixture issuer, with keys generated per test and served from an
in-test JWKS, so no identity provider runs.
"""

import asyncio
import base64
import json
import threading
import time
import uuid
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient
from jwt import PyJWKClient
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from sqlalchemy import Engine

from property_focus.adapters.auth.oidc import (
    FIXTURE_ISSUER,
    MAX_KEY_SET_AGE_SECONDS,
    REFETCH_INTERVAL_SECONDS,
    InvalidTokenError,
    OidcVerifier,
    authenticate,
    fetch_json,
    fixture_verifier,
    verifier_for,
)
from property_focus.adapters.db import create_database_engine
from property_focus.adapters.mcp.server import create_app as create_mcp_app
from property_focus.adapters.rest.app import create_app as create_rest_app
from property_focus.application.context import VerifiedIdentity
from property_focus.config import LOCAL_OIDC_ISSUER, ConfigurationError, Settings
from property_focus.domain.errors import CodedError

AUDIENCE = "property-focus-api"


@dataclass(frozen=True)
class SigningKey:
    """A key pair the test signs with, and its public JWK."""

    kid: str
    algorithm: str
    private_key: Any

    @classmethod
    def rsa(cls) -> "SigningKey":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return cls(uuid.uuid4().hex, "RS256", key)

    @classmethod
    def ec(cls) -> "SigningKey":
        return cls(uuid.uuid4().hex, "ES256", ec.generate_private_key(ec.SECP256R1()))

    @property
    def jwk(self) -> dict[str, Any]:
        to_jwk = RSAAlgorithm.to_jwk if self.algorithm == "RS256" else ECAlgorithm.to_jwk
        public = to_jwk(self.private_key.public_key(), as_dict=True)
        return {**public, "kid": self.kid, "use": "sig", "alg": self.algorithm}

    def sign(self, **overrides: Any) -> str:
        """A token with valid claims for the fixture issuer, changed by ``overrides``.

        An override whose value is None removes that claim.
        """
        now = int(time.time())
        claims = {
            "iss": FIXTURE_ISSUER,
            "sub": "subject-1",
            "aud": AUDIENCE,
            "typ": "Bearer",
            "iat": now,
            "exp": now + 300,
            "email": "buyer@example.test",
            "email_verified": True,
        }
        claims.update(overrides)
        payload = {name: value for name, value in claims.items() if value is not None}
        return jwt.encode(
            payload, self.private_key, algorithm=self.algorithm, headers={"kid": self.kid}
        )


@dataclass
class KeySetServer:
    """The in-test JWKS: the keys currently published, and how often they were fetched."""

    keys: list[SigningKey] = field(default_factory=list)
    fetches: int = 0
    unavailable: bool = False
    # How long each fetch takes.
    delay_seconds: float = 0.0
    # The thread each fetch ran on.
    fetch_threads: list[threading.Thread] = field(default_factory=list)

    def key_set(self) -> Mapping[str, Any]:
        self.fetches += 1
        self.fetch_threads.append(threading.current_thread())
        time.sleep(self.delay_seconds)
        if self.unavailable:
            raise CodedError("dependency_transient", "The identity provider is unavailable.")
        return {"keys": [key.jwk for key in self.keys]}


class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def fixture_settings(**overrides: Any) -> Settings:
    return Settings.model_validate(
        {"environment": "development", "fixture_issuer_enabled": True, **overrides}
    )


def verify(verifier: OidcVerifier, token: str) -> VerifiedIdentity:
    return asyncio.run(verifier.verify(token))


def unsigned_token(header: Mapping[str, Any], claims: Mapping[str, Any]) -> str:
    def encode(part: Mapping[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()

    return f"{encode(header)}.{encode(claims)}."


@pytest.fixture
def key() -> SigningKey:
    return SigningKey.rsa()


@pytest.fixture
def server(key: SigningKey) -> KeySetServer:
    return KeySetServer([key])


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def verifier(server: KeySetServer, clock: FakeClock) -> OidcVerifier:
    return fixture_verifier(fixture_settings(), server.key_set, clock=clock)


class TestClaims:
    def test_valid_access_token_yields_the_callers_identity(
        self, verifier: OidcVerifier, key: SigningKey
    ) -> None:
        identity = verify(verifier, key.sign())

        assert identity == VerifiedIdentity(
            issuer=FIXTURE_ISSUER,
            subject="subject-1",
            email="buyer@example.test",
            email_verified=True,
        )

    @pytest.mark.parametrize("email_verified", [False, None, "true"])
    def test_email_counts_as_verified_only_when_the_token_says_true(
        self, verifier: OidcVerifier, key: SigningKey, email_verified: Any
    ) -> None:
        identity = verify(verifier, key.sign(email_verified=email_verified))

        assert identity.email == "buyer@example.test"
        assert not identity.email_verified

    def test_expired_token_fails(self, verifier: OidcVerifier, key: SigningKey) -> None:
        with pytest.raises(InvalidTokenError, match="expired"):
            verify(verifier, key.sign(exp=int(time.time()) - 61 - 5))

    def test_expiry_within_the_clock_skew_passes(
        self, verifier: OidcVerifier, key: SigningKey
    ) -> None:
        verify(verifier, key.sign(exp=int(time.time()) - 30))

    def test_not_yet_valid_beyond_the_clock_skew_fails(
        self, verifier: OidcVerifier, key: SigningKey
    ) -> None:
        with pytest.raises(InvalidTokenError, match="not yet valid"):
            verify(verifier, key.sign(nbf=int(time.time()) + 120))

    def test_not_before_within_the_clock_skew_passes(
        self, verifier: OidcVerifier, key: SigningKey
    ) -> None:
        verify(verifier, key.sign(nbf=int(time.time()) + 30))

    @pytest.mark.parametrize("audience", ["another-api", ["account", "another-api"], None])
    def test_wrong_audience_fails(
        self, verifier: OidcVerifier, key: SigningKey, audience: Any
    ) -> None:
        with pytest.raises(InvalidTokenError, match="audience"):
            verify(verifier, key.sign(aud=audience))

    def test_audience_list_naming_the_api_passes(
        self, verifier: OidcVerifier, key: SigningKey
    ) -> None:
        verify(verifier, key.sign(aud=["account", AUDIENCE]))

    @pytest.mark.parametrize("issuer", [LOCAL_OIDC_ISSUER, f"{FIXTURE_ISSUER}/", None])
    def test_wrong_issuer_fails(
        self, verifier: OidcVerifier, key: SigningKey, issuer: str | None
    ) -> None:
        with pytest.raises(InvalidTokenError):
            verify(verifier, key.sign(iss=issuer))

    @pytest.mark.parametrize("token_type", ["ID", "Refresh", None])
    def test_token_that_is_not_an_access_token_fails(
        self, verifier: OidcVerifier, key: SigningKey, token_type: str | None
    ) -> None:
        with pytest.raises(InvalidTokenError, match="not an access token"):
            verify(verifier, key.sign(typ=token_type))

    @pytest.mark.parametrize("claim", ["sub", "exp"])
    def test_token_without_a_required_claim_fails(
        self, verifier: OidcVerifier, key: SigningKey, claim: str
    ) -> None:
        with pytest.raises(InvalidTokenError):
            verify(verifier, key.sign(**{claim: None}))

    @pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c"])
    def test_malformed_token_fails(self, verifier: OidcVerifier, token: str) -> None:
        with pytest.raises(InvalidTokenError):
            verify(verifier, token)

    def test_audience_and_token_type_claims_follow_configuration(
        self, server: KeySetServer, key: SigningKey
    ) -> None:
        settings = fixture_settings(
            oidc_audience=("web-client", "mcp-client"),
            oidc_audience_claim="client_id",
            oidc_token_type="token_use=access",
        )
        verifier = fixture_verifier(settings, server.key_set)

        verify(verifier, key.sign(aud=None, typ=None, client_id="mcp-client", token_use="access"))
        with pytest.raises(InvalidTokenError, match="client_id"):
            verify(verifier, key.sign(typ=None, token_use="access"))
        with pytest.raises(InvalidTokenError, match="not an access token"):
            verify(verifier, key.sign(aud=None, client_id="web-client", token_use="id"))


class TestAlgorithms:
    def test_unsigned_token_fails(self, verifier: OidcVerifier, key: SigningKey) -> None:
        claims = jwt.decode(key.sign(), options={"verify_signature": False})
        token = unsigned_token({"alg": "none", "kid": key.kid, "typ": "JWT"}, claims)

        with pytest.raises(InvalidTokenError, match="'none' is not allowed"):
            verify(verifier, token)

    def test_hs256_token_signed_with_the_public_key_fails(
        self, verifier: OidcVerifier, key: SigningKey
    ) -> None:
        # The classic confusion attack: the public key, which anyone can read, as an HMAC secret.
        public_pem = key.private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        claims = jwt.decode(key.sign(), options={"verify_signature": False})
        header = {"alg": "HS256", "kid": key.kid, "typ": "JWT"}
        signing_input = unsigned_token(header, claims).rstrip(".")
        signature = jwt.algorithms.HMACAlgorithm(jwt.algorithms.HMACAlgorithm.SHA256).sign(
            signing_input.encode(), public_pem
        )
        token = f"{signing_input}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"

        with pytest.raises(InvalidTokenError, match="'HS256' is not allowed"):
            verify(verifier, token)

    def test_es256_token_is_accepted_by_default(
        self, server: KeySetServer, verifier: OidcVerifier
    ) -> None:
        ec_key = SigningKey.ec()
        server.keys.append(ec_key)

        verify(verifier, ec_key.sign())

    def test_algorithm_outside_the_configured_allowlist_fails(self, server: KeySetServer) -> None:
        ec_key = SigningKey.ec()
        server.keys.append(ec_key)
        verifier = fixture_verifier(fixture_settings(oidc_algorithms="RS256"), server.key_set)

        with pytest.raises(InvalidTokenError, match="'ES256' is not allowed"):
            verify(verifier, ec_key.sign())

    def test_header_algorithm_must_match_the_key(
        self, verifier: OidcVerifier, key: SigningKey
    ) -> None:
        ec_key = SigningKey.ec()
        # Signed with an EC key but naming the RSA key's kid.
        token = jwt.encode(
            {"iss": FIXTURE_ISSUER}, ec_key.private_key, algorithm="ES256", headers={"kid": key.kid}
        )

        with pytest.raises(InvalidTokenError):
            verify(verifier, token)


class TestKeySet:
    def test_token_signed_by_a_key_absent_from_the_jwks_fails(self, verifier: OidcVerifier) -> None:
        with pytest.raises(InvalidTokenError, match="no signing key"):
            verify(verifier, SigningKey.rsa().sign())

    def test_key_set_is_cached_between_tokens(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey
    ) -> None:
        for _ in range(3):
            verify(verifier, key.sign())

        assert server.fetches == 1

    def test_rotated_in_key_validates_after_one_refetch_and_a_removed_key_fails(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey, clock: FakeClock
    ) -> None:
        verify(verifier, key.sign())
        new_key = SigningKey.rsa()
        server.keys = [new_key]
        clock.advance(REFETCH_INTERVAL_SECONDS)

        verify(verifier, new_key.sign())
        assert server.fetches == 2

        with pytest.raises(InvalidTokenError, match="no signing key"):
            verify(verifier, key.sign())

    def test_unknown_kid_refetches_at_most_once_a_minute(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey, clock: FakeClock
    ) -> None:
        verify(verifier, key.sign())
        clock.advance(REFETCH_INTERVAL_SECONDS)
        strangers = [SigningKey.rsa() for _ in range(5)]

        for stranger in strangers:
            with pytest.raises(InvalidTokenError):
                verify(verifier, stranger.sign())
            clock.advance(1)
        assert server.fetches == 2

        clock.advance(REFETCH_INTERVAL_SECONDS)
        with pytest.raises(InvalidTokenError):
            verify(verifier, strangers[0].sign())
        assert server.fetches == 3

    def test_new_key_published_within_the_rate_limit_waits_for_the_next_refetch(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey, clock: FakeClock
    ) -> None:
        verify(verifier, key.sign())
        new_key = SigningKey.rsa()
        server.keys.append(new_key)

        with pytest.raises(InvalidTokenError):
            verify(verifier, new_key.sign())
        clock.advance(REFETCH_INTERVAL_SECONDS)
        verify(verifier, new_key.sign())

    def test_old_key_set_is_refetched_so_a_removed_key_stops_validating(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey, clock: FakeClock
    ) -> None:
        verify(verifier, key.sign())
        server.keys = [SigningKey.rsa()]
        clock.advance(MAX_KEY_SET_AGE_SECONDS)

        with pytest.raises(InvalidTokenError, match="no signing key"):
            verify(verifier, key.sign())

    def test_expired_key_set_whose_refetch_fails_is_a_transient_dependency_failure(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey, clock: FakeClock
    ) -> None:
        verify(verifier, key.sign())
        server.unavailable = True
        clock.advance(MAX_KEY_SET_AGE_SECONDS)

        with pytest.raises(CodedError) as raised:
            verify(verifier, key.sign())
        assert raised.value.code == "dependency_transient"
        assert server.fetches == 2

        server.unavailable = False
        clock.advance(REFETCH_INTERVAL_SECONDS)
        verify(verifier, key.sign())

    def test_failed_refresh_within_the_maximum_age_keeps_serving_cached_keys(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey, clock: FakeClock
    ) -> None:
        verify(verifier, key.sign())
        server.unavailable = True
        clock.advance(REFETCH_INTERVAL_SECONDS)

        with pytest.raises(CodedError) as raised:
            verify(verifier, SigningKey.rsa().sign())
        assert raised.value.code == "dependency_transient"
        verify(verifier, key.sign())
        assert server.fetches == 2

    def test_many_unknown_kids_within_a_minute_cause_at_most_one_fetch(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey, clock: FakeClock
    ) -> None:
        verify(verifier, key.sign())
        clock.advance(REFETCH_INTERVAL_SECONDS)
        stranger = SigningKey.rsa()
        tokens = [
            jwt.encode(
                {"iss": FIXTURE_ISSUER, "sub": "s"},
                stranger.private_key,
                algorithm="RS256",
                headers={"kid": uuid.uuid4().hex},
            )
            for _ in range(100)
        ]

        for token in tokens:
            with pytest.raises(InvalidTokenError, match="no signing key"):
                verify(verifier, token)
            clock.advance(0.5)
        assert server.fetches == 2

        clock.advance(REFETCH_INTERVAL_SECONDS)
        with pytest.raises(InvalidTokenError, match="no signing key"):
            verify(verifier, tokens[0])
        assert server.fetches == 3

    def test_concurrent_requests_share_one_fetch(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey
    ) -> None:
        async def verify_many() -> None:
            await asyncio.gather(*(verifier.verify(key.sign()) for _ in range(20)))

        asyncio.run(verify_many())

        assert server.fetches == 1

    def test_concurrent_requests_for_a_rotated_in_key_share_one_refetch(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey, clock: FakeClock
    ) -> None:
        verify(verifier, key.sign())
        new_key = SigningKey.rsa()
        server.keys = [new_key]
        server.delay_seconds = 0.2
        clock.advance(REFETCH_INTERVAL_SECONDS)
        tokens = [new_key.sign() for _ in range(20)]

        async def verify_many() -> list[VerifiedIdentity]:
            return await asyncio.gather(*(verifier.verify(token) for token in tokens))

        identities = asyncio.run(verify_many())

        assert len(identities) == 20
        assert server.fetches == 2

    def test_concurrent_requests_while_the_issuer_is_down_share_one_failed_fetch(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey
    ) -> None:
        server.unavailable = True
        server.delay_seconds = 0.2

        async def verify_many() -> list[BaseException | VerifiedIdentity]:
            return await asyncio.gather(
                *(verifier.verify(key.sign()) for _ in range(20)), return_exceptions=True
            )

        outcomes = asyncio.run(verify_many())

        assert server.fetches == 1
        assert all(
            isinstance(outcome, CodedError) and outcome.code == "dependency_transient"
            for outcome in outcomes
        )

    def test_expired_key_set_with_the_issuer_down_fails_fast_until_the_interval_passes(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey, clock: FakeClock
    ) -> None:
        verify(verifier, key.sign())
        server.unavailable = True
        clock.advance(MAX_KEY_SET_AGE_SECONDS)

        for _ in range(5):
            with pytest.raises(CodedError) as raised:
                verify(verifier, key.sign())
            assert raised.value.code == "dependency_transient"
            clock.advance(REFETCH_INTERVAL_SECONDS / 10)
        assert server.fetches == 2

        clock.advance(REFETCH_INTERVAL_SECONDS)
        server.unavailable = False
        verify(verifier, key.sign())
        assert server.fetches == 3

    def test_cached_key_is_found_without_waiting_for_a_busy_default_executor(
        self, verifier: OidcVerifier, key: SigningKey
    ) -> None:
        verify(verifier, key.sign())
        token = key.sign()
        release = threading.Event()

        async def verify_with_executor_busy() -> VerifiedIdentity:
            loop = asyncio.get_running_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            busy = loop.run_in_executor(None, release.wait)
            try:
                return await asyncio.wait_for(verifier.verify(token), timeout=0.5)
            finally:
                release.set()
                await busy

        identity = asyncio.run(verify_with_executor_busy())

        assert identity.subject == "subject-1"

    def test_the_key_set_is_fetched_off_the_event_loop_thread(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey
    ) -> None:
        loop_threads: list[threading.Thread] = []

        async def verify_on_loop() -> None:
            loop_threads.append(threading.current_thread())
            await verifier.verify(key.sign())

        asyncio.run(verify_on_loop())

        assert server.fetch_threads
        assert loop_threads[0] not in server.fetch_threads

    def test_unreachable_key_set_is_a_transient_dependency_failure(
        self, verifier: OidcVerifier, server: KeySetServer, key: SigningKey
    ) -> None:
        server.unavailable = True

        with pytest.raises(CodedError) as raised:
            verify(verifier, key.sign())
        assert raised.value.code == "dependency_transient"

    def test_encryption_keys_are_not_signing_keys(
        self, verifier: OidcVerifier, server: KeySetServer
    ) -> None:
        encryption_key = SigningKey.rsa()
        server.keys = [encryption_key]
        original = server.key_set

        def with_enc_use() -> Mapping[str, Any]:
            document = original()
            return {"keys": [{**jwk, "use": "enc"} for jwk in document["keys"]]}

        verifier = fixture_verifier(fixture_settings(), with_enc_use)

        with pytest.raises(InvalidTokenError, match="no signing key"):
            verify(verifier, encryption_key.sign())


class TestDecodeOptions:
    def test_each_decode_gets_its_own_options_so_one_cannot_weaken_the_next(
        self, verifier: OidcVerifier, key: SigningKey, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        received: list[dict[str, Any]] = []
        decode = jwt.decode

        def recording_decode(*args: Any, options: dict[str, Any], **kwargs: Any) -> Any:
            received.append(options)
            try:
                return decode(*args, options=options, **kwargs)
            finally:
                # What a caller that mutates the shared options would do to later decodes.
                options["verify_signature"] = False
                options["require"] = []

        monkeypatch.setattr(jwt, "decode", recording_decode)
        expired = key.sign(exp=int(time.time()) - 3600)

        for _ in range(2):
            with pytest.raises(InvalidTokenError, match="expired"):
                verify(verifier, expired)

        assert len(received) == 2
        assert received[0] is not received[1]


class TestIssuers:
    @pytest.fixture
    def provider(self, key: SigningKey) -> dict[str, Mapping[str, Any]]:
        """Documents a configured issuer publishes, by URL."""
        return {
            f"{LOCAL_OIDC_ISSUER}/.well-known/openid-configuration": {
                "issuer": LOCAL_OIDC_ISSUER,
                "jwks_uri": f"{LOCAL_OIDC_ISSUER}/protocol/openid-connect/certs",
                "token_endpoint": f"{LOCAL_OIDC_ISSUER}/protocol/openid-connect/token",
            },
            f"{LOCAL_OIDC_ISSUER}/protocol/openid-connect/certs": {"keys": [key.jwk]},
            "http://keycloak:8080/certs": {"keys": [key.jwk]},
        }

    def configured_verifier(
        self,
        provider: Mapping[str, Mapping[str, Any]],
        requested: list[str],
        clients: list[PyJWKClient] | None = None,
        **settings: Any,
    ) -> OidcVerifier:
        """A verifier whose discovery and key-set requests are answered from ``provider``.

        Each requested URL is appended to ``requested``, and each key-set client created to
        ``clients``.
        """

        async def fetch(url: str) -> Mapping[str, Any]:
            requested.append(url)
            return provider[url]

        class ProviderClient(PyJWKClient):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                if clients is not None:
                    clients.append(self)

            def fetch_data(self) -> Any:
                requested.append(self.uri)
                return provider[self.uri]

        return verifier_for(
            Settings.model_validate({"environment": "development", **settings}),
            fetch=fetch,
            jwks_client_type=ProviderClient,
        )

    def test_configured_issuer_keys_come_from_discovery_and_nothing_else_is_requested(
        self, provider: dict[str, Mapping[str, Any]], key: SigningKey
    ) -> None:
        requested: list[str] = []
        verifier = self.configured_verifier(provider, requested)

        identity = verify(verifier, key.sign(iss=LOCAL_OIDC_ISSUER))

        assert identity.issuer == LOCAL_OIDC_ISSUER
        assert requested == [
            f"{LOCAL_OIDC_ISSUER}/.well-known/openid-configuration",
            f"{LOCAL_OIDC_ISSUER}/protocol/openid-connect/certs",
        ]

    def test_failed_discovery_is_not_retried_within_the_refetch_interval(
        self, key: SigningKey
    ) -> None:
        requested: list[str] = []
        clock = FakeClock()

        async def fetch(url: str) -> Mapping[str, Any]:
            requested.append(url)
            raise CodedError("dependency_transient", "The identity provider could not be reached.")

        verifier = verifier_for(
            Settings.model_validate({"environment": "development"}), fetch=fetch, clock=clock
        )

        for _ in range(3):
            with pytest.raises(CodedError) as raised:
                verify(verifier, key.sign(iss=LOCAL_OIDC_ISSUER))
            assert raised.value.code == "dependency_transient"
            clock.advance(1)
        assert len(requested) == 1

        clock.advance(REFETCH_INTERVAL_SECONDS)
        with pytest.raises(CodedError):
            verify(verifier, key.sign(iss=LOCAL_OIDC_ISSUER))
        assert len(requested) == 2

    def test_key_set_url_override_skips_discovery(
        self, provider: dict[str, Mapping[str, Any]], key: SigningKey
    ) -> None:
        requested: list[str] = []
        verifier = self.configured_verifier(
            provider, requested, oidc_jwks_url="http://keycloak:8080/certs"
        )

        verify(verifier, key.sign(iss=LOCAL_OIDC_ISSUER))

        assert requested == ["http://keycloak:8080/certs"]

    def test_discovery_naming_another_issuer_is_refused(
        self, provider: dict[str, Mapping[str, Any]], key: SigningKey
    ) -> None:
        discovery_url = f"{LOCAL_OIDC_ISSUER}/.well-known/openid-configuration"
        provider[discovery_url] = {**provider[discovery_url], "issuer": "https://elsewhere.test"}
        verifier = self.configured_verifier(provider, [])

        with pytest.raises(CodedError) as raised:
            verify(verifier, key.sign(iss=LOCAL_OIDC_ISSUER))
        assert raised.value.code == "dependency_transient"

    def test_one_key_set_client_serves_every_request(
        self, provider: dict[str, Mapping[str, Any]], key: SigningKey
    ) -> None:
        requested: list[str] = []
        clients: list[PyJWKClient] = []
        verifier = self.configured_verifier(provider, requested, clients)

        for _ in range(5):
            verify(verifier, key.sign(iss=LOCAL_OIDC_ISSUER))

        assert len(clients) == 1
        assert len(requested) == 2

    def test_unreachable_discovery_is_a_transient_dependency_failure(self, key: SigningKey) -> None:
        # Nothing listens on port 1.
        issuer = "http://127.0.0.1:1/realms/property-focus"
        settings = Settings.model_validate({"environment": "development", "oidc_issuer": issuer})
        verifier = verifier_for(settings, fetch=fetch_json)

        with pytest.raises(CodedError) as raised:
            verify(verifier, key.sign(iss=issuer))
        assert raised.value.code == "dependency_transient"

    def test_unreachable_key_set_is_a_transient_dependency_failure(self, key: SigningKey) -> None:
        # The real PyJWT client, pointed where nothing listens.
        settings = Settings.model_validate(
            {"environment": "development", "oidc_jwks_url": "http://127.0.0.1:1/certs"}
        )
        verifier = verifier_for(settings)

        with pytest.raises(CodedError) as raised:
            verify(verifier, key.sign(iss=LOCAL_OIDC_ISSUER))
        assert raised.value.code == "dependency_transient"

    def test_configured_verifier_rejects_fixture_issuer_tokens(
        self, provider: dict[str, Mapping[str, Any]], key: SigningKey
    ) -> None:
        verifier = self.configured_verifier(provider, [])

        with pytest.raises(InvalidTokenError):
            verify(verifier, key.sign(iss=FIXTURE_ISSUER))

    def test_fixture_issuer_needs_the_fixture_flag(self, server: KeySetServer) -> None:
        settings = Settings.model_validate({"environment": "development"})

        with pytest.raises(ValueError, match="PF_AUTH_FIXTURE_ISSUER"):
            fixture_verifier(settings, server.key_set)

    @pytest.mark.parametrize("environment", ["dogfood", "staging", "production"])
    def test_fixture_issuer_is_refused_without_the_development_flag(self, environment: str) -> None:
        environ = {
            "PF_ENVIRONMENT": environment,
            "PF_OIDC_ISSUER": "https://auth.example.test/realms/property-focus",
            "PF_AUTH_FIXTURE_ISSUER": "true",
        }

        with pytest.raises(ConfigurationError, match="PF_AUTH_FIXTURE_ISSUER is development-only"):
            Settings.from_environment(environ)


class TestAuthenticate:
    """``authenticate`` with a database that cannot be reached (nothing listens on port 1)."""

    @pytest.fixture
    def unreachable_engine(self) -> Iterator[Engine]:
        engine = create_database_engine("postgresql+psycopg://pf_runtime@127.0.0.1:1/pf")
        yield engine
        engine.dispose()

    def test_unreachable_database_is_a_transient_dependency_failure(
        self, verifier: OidcVerifier, key: SigningKey, unreachable_engine: Engine
    ) -> None:
        with pytest.raises(CodedError) as raised:
            asyncio.run(
                authenticate(verifier, unreachable_engine, key.sign(), correlation_id="c-1")
            )
        assert raised.value.code == "dependency_transient"

    def test_an_invalid_token_is_unauthorized_before_the_database_is_used(
        self, verifier: OidcVerifier, unreachable_engine: Engine
    ) -> None:
        with pytest.raises(CodedError) as raised:
            asyncio.run(authenticate(verifier, unreachable_engine, "nonsense", correlation_id="c"))
        assert raised.value.code == "unauthorized"

    def test_a_token_from_an_unknown_key_is_unauthorized(
        self, verifier: OidcVerifier, key: SigningKey, unreachable_engine: Engine
    ) -> None:
        verify(verifier, key.sign())

        with pytest.raises(CodedError) as raised:
            asyncio.run(
                authenticate(
                    verifier, unreachable_engine, SigningKey.rsa().sign(), correlation_id="c"
                )
            )
        assert raised.value.code == "unauthorized"

    def test_rest_answers_a_retryable_503_problem(
        self, verifier: OidcVerifier, key: SigningKey, unreachable_engine: Engine
    ) -> None:
        app = create_rest_app(fixture_settings(), engine=unreachable_engine, verifier=verifier)
        response = TestClient(app).get(
            f"/v1/workflow-runs/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {key.sign()}"},
        )

        assert response.status_code == 503
        assert (response.json()["code"], response.json()["retryable"]) == (
            "dependency_transient",
            True,
        )

    def test_mcp_answers_503_and_still_challenges_an_invalid_token(
        self, verifier: OidcVerifier, key: SigningKey, unreachable_engine: Engine
    ) -> None:
        app = create_mcp_app(fixture_settings(), engine=unreachable_engine, verifier=verifier)
        client = TestClient(app)
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

        unavailable = client.post(
            "/mcp", json=message, headers={"Authorization": f"Bearer {key.sign()}"}
        )
        rejected = client.post("/mcp", json=message, headers={"Authorization": "Bearer nonsense"})

        assert unavailable.status_code == 503
        assert rejected.status_code == 401
        assert 'error="invalid_token"' in rejected.headers["www-authenticate"]
