"""The one generic OpenID Connect access-token verifier, shared by the REST and MCP adapters.

``OidcVerifier`` checks a bearer token against the configured issuer (``PF_OIDC_*``
settings): its signature against the issuer's published key set (JWKS), then ``iss``, the
configured audience claim against the audiences the calling surface accepts (REST
``PF_OIDC_AUDIENCE``, MCP ``PF_MCP_AUDIENCE``), ``exp`` and ``nbf`` within the clock skew,
the configured token-type claim, and the signing algorithm against the allowlist. It
contacts the identity provider only for its discovery document and its JWKS, so any
standard provider works by configuration alone.

PyJWT's ``PyJWKClient`` fetches the key set, one client per issuer. A token naming an
unknown ``kid`` triggers a refetch, and fetch attempts, successful or failed, start at most
once per ``REFETCH_INTERVAL_SECONDS``, so a rotated-in key validates after one refetch and a
key removed from the JWKS stops validating once the next refetch replaces the set. The set
expires after ``MAX_KEY_SET_AGE_SECONDS``, so a removed key cannot stay trusted for long
without traffic that names a new one; when an expired set cannot be refetched, requests fail
as a transient dependency failure rather than trusting the old keys, and until the next
attempt is allowed they fail at once without contacting the issuer.

``fixture_verifier`` trusts the signed fixture issuer instead, with a key set the caller
supplies. Only unit tests use it, and only with ``PF_AUTH_FIXTURE_ISSUER`` set, which
configuration refuses outside development.

``authenticate`` is the one token check both adapters bind to: it verifies the token for the
adapter's own audiences, then resolves the caller's tenant.
"""

import asyncio
import json
import time
import urllib.request
from collections.abc import Awaitable, Callable, Collection, Mapping
from typing import Any
from urllib.parse import urlsplit

import jwt
from jwt import PyJWK, PyJWKClient, PyJWKSet
from jwt.exceptions import (
    PyJWKClientError,
    PyJWKSetError,
    PyJWTError,
)
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from property_focus.application.context import ExecutionContext, VerifiedIdentity, resolve_context
from property_focus.config import Settings
from property_focus.domain.errors import CodedError

# The `iss` of tokens the fixture issuer signs. The .invalid domain never resolves.
FIXTURE_ISSUER = "https://fixture-issuer.invalid/realms/property-focus"

# Unknown-kid refetches happen at most this often (seconds on the verifier's clock).
REFETCH_INTERVAL_SECONDS = 60.0
# A key set older than this has expired: it is refetched before use and never trusted again.
MAX_KEY_SET_AGE_SECONDS = 600.0
# Timeout for each discovery or JWKS request.
_FETCH_TIMEOUT_SECONDS = 10.0

# Fetches a JSON document by URL. Raises CodedError dependency_transient when unavailable.
type FetchJson = Callable[[str], Awaitable[Mapping[str, Any]]]
# Returns the issuer's JWKS client, creating it on first use.
type JwksClientSource = Callable[[], Awaitable[PyJWKClient]]


class InvalidTokenError(Exception):
    """The bearer token is not a valid access token for this application.

    The message says which check failed, for logs; it never contains the token.
    """


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Hands a redirect back as an error instead of following it, so a document is read only
    from the URL asked for, never from another scheme or host."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


# Fetches identity-provider documents without following redirects.
_OPENER = urllib.request.build_opener(_RefuseRedirects())


async def fetch_json(url: str) -> Mapping[str, Any]:
    """GET ``url`` and parse its JSON body, without blocking the event loop.

    A redirect is not followed. Raises ``CodedError`` ``dependency_transient`` when the
    request fails or is redirected, or the body is not a JSON object.
    """

    def get() -> Any:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with _OPENER.open(request, timeout=_FETCH_TIMEOUT_SECONDS) as response:
            return json.load(response)

    try:
        document = await asyncio.to_thread(get)
    except (OSError, ValueError) as error:
        raise CodedError(
            "dependency_transient", "The identity provider could not be reached."
        ) from error
    if not isinstance(document, dict):
        raise CodedError("dependency_transient", "The identity provider returned no JSON object.")
    return document


class _IssuerKeys:
    """Looks up the issuer's signing keys through its ``PyJWKClient``, under our refresh policy.

    The client fetches the key set; this guard keeps the signing keys of the last fetched
    set and decides, on the verifier's clock, when to fetch again: on first use, whenever the
    set is older than ``MAX_KEY_SET_AGE_SECONDS`` (so a removed key cannot stay trusted for
    long), and when a token names a ``kid`` the set lacks.

    A lookup in a current set touches no I/O, lock or thread. Fetch attempts, successful or
    not, run one at a time in a worker thread (``PyJWKClient`` blocks), and at most one
    starts per ``REFETCH_INTERVAL_SECONDS``: requests that queued behind an attempt, or that
    arrive within the interval after it, take its outcome. After a failed attempt (key set or
    discovery) they fail at once as a transient dependency failure instead of fetching again.
    """

    def __init__(self, client: JwksClientSource, clock: Callable[[], float]) -> None:
        self._client = client
        self._clock = clock
        self._lock = asyncio.Lock()
        # The signing keys of the last fetched set, by kid; empty before the first fetch.
        self._signing_keys: dict[str, PyJWK] = {}
        # When the current set was fetched; None before the first successful fetch.
        self._loaded_at: float | None = None
        # When the last fetch attempt started; None before the first.
        self._attempted_at: float | None = None
        # The failure of the last attempt; None when it succeeded, was cancelled or none was made.
        self._failure: CodedError | None = None
        # Whether an attempt is running now.
        self._fetching = False

    async def key(self, kid: str) -> PyJWK:
        """Return the signing key named ``kid``.

        Raises ``InvalidTokenError`` when the key set lacks it even after any refresh the rate
        limit allows, and ``CodedError`` ``dependency_transient`` when the issuer cannot be
        reached and no current key set can answer: before the first fetch, once the cached set
        has expired, or for a ``kid`` the cached set lacks.
        """
        if self._needs_fetch(kid):
            async with self._lock:
                # An attempt that finished while this request waited has answered it.
                if self._needs_fetch(kid):
                    await self._fetch()
        key = self._signing_keys.get(kid)
        if key is None:
            raise InvalidTokenError(f"no signing key {kid!r} in the issuer's key set")
        return key

    def _needs_fetch(self, kid: str) -> bool:
        """Whether answering for ``kid`` needs a fetch attempt (or the one running now).

        False when the current set answers: it holds ``kid``, or it lacks ``kid`` and was
        fetched within ``REFETCH_INTERVAL_SECONDS``. Raises ``CodedError``
        ``dependency_transient`` when a fetch is needed but the last attempt, within that
        interval, failed.
        """
        now = self._clock()
        expired = self._loaded_at is None or now - self._loaded_at >= MAX_KEY_SET_AGE_SECONDS
        if not expired and kid in self._signing_keys:
            return False
        if self._fetching:
            return True
        attempted_recently = (
            self._attempted_at is not None and now - self._attempted_at < REFETCH_INTERVAL_SECONDS
        )
        if attempted_recently and self._failure is not None:
            raise CodedError(self._failure.code, self._failure.safe_explanation)
        return expired or not attempted_recently

    async def _fetch(self) -> None:
        """Fetch the key set and make it current, recording the attempt and its outcome.

        Raises ``CodedError`` ``dependency_transient`` when discovery or the fetch fails, or
        when the response is not a key set holding at least one signing key: not a JSON
        object, no usable key, or only encryption keys. A well-formed but empty set
        (``{"keys": []}``) is such a failure too, not an instruction to trust nothing: an
        issuer retires a key by publishing a set without it, and a set with no signing key
        at all is far likelier a fault. On any failure the current set is kept, until it
        expires.
        """
        self._attempted_at = self._clock()
        self._failure = None
        self._fetching = True
        try:
            client = await self._client()
            key_set = await asyncio.to_thread(client.get_jwk_set, True)
            signing_keys = _signing_keys(key_set)
        except CodedError as error:
            self._failure = error
            raise
        except (PyJWKClientError, PyJWKSetError, OSError, ValueError) as error:
            # PyJWKClientError: a failed request (PyJWKClientConnectionError), a body that is
            # not a JSON object, or a client that cannot be built for the URL. PyJWKSetError:
            # no usable key. ValueError: the body is not JSON. OSError: the connection broke
            # mid-read.
            self._failure = CodedError(
                "dependency_transient", "The identity provider's key set could not be fetched."
            )
            raise self._failure from error
        finally:
            self._fetching = False
        self._loaded_at = self._attempted_at
        self._signing_keys = signing_keys


def _signing_keys(key_set: PyJWKSet) -> dict[str, PyJWK]:
    """The signing keys of ``key_set`` by ``kid``; encryption keys are not signing keys.

    Raises ``PyJWKSetError`` when it holds none.
    """
    signing_keys: dict[str, PyJWK] = {}
    for key in key_set.keys:
        if key.key_id is not None and key.public_key_use != "enc":
            signing_keys.setdefault(key.key_id, key)
    if not signing_keys:
        raise PyJWKSetError("The key set holds no signing key")
    return signing_keys


class OidcVerifier:
    """Validates bearer access tokens from one issuer and returns the caller's identity."""

    def __init__(
        self,
        settings: Settings,
        *,
        issuer: str,
        jwks_client: JwksClientSource,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Verify tokens from ``issuer`` with keys from the client ``jwks_client`` returns.

        Every other check follows ``settings``. ``clock`` (seconds, monotonic) drives only
        the key-set refetch limits; token times are checked against the wall clock.
        """
        self.issuer = issuer
        self._audience_claim = settings.oidc_audience_claim
        self._token_type_claim, self._token_type = settings.oidc_token_type
        self._algorithms = list(settings.oidc_algorithms)
        self._skew_seconds = settings.oidc_clock_skew_seconds
        self._keys = _IssuerKeys(jwks_client, clock)

    async def verify(self, token: str, *, audiences: Collection[str]) -> VerifiedIdentity:
        """Return the identity ``token`` proves to a resource that accepts ``audiences``.

        The audience claim must hold at least one of ``audiences``. Raises
        ``InvalidTokenError`` for a token that fails any check, and ``CodedError``
        ``dependency_transient`` when the issuer's keys cannot be fetched.
        """
        try:
            header = jwt.get_unverified_header(token)
        except PyJWTError as error:
            raise InvalidTokenError(f"malformed token: {error}") from error
        algorithm = header.get("alg")
        if algorithm not in self._algorithms:
            raise InvalidTokenError(f"signing algorithm {algorithm!r} is not allowed")
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise InvalidTokenError("the token names no signing key")

        key = await self._keys.key(kid)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=self._algorithms,
                issuer=self.issuer,
                leeway=self._skew_seconds,
                # A new dictionary on every call, so no decode can change another's checks
                # (GHSA-gvp8-978c-rx2q).
                options={"require": ["exp", "iss", "sub"], "verify_aud": False},
            )
        except PyJWTError as error:
            raise InvalidTokenError(f"token rejected: {error}") from error

        self._check_audience(claims.get(self._audience_claim), audiences)
        if claims.get(self._token_type_claim) != self._token_type:
            raise InvalidTokenError(f"not an access token: {self._token_type_claim} mismatch")
        subject = claims["sub"]
        if not isinstance(subject, str) or not subject:
            raise InvalidTokenError("the token has no subject")
        email = claims.get("email")
        return VerifiedIdentity(
            issuer=self.issuer,
            subject=subject,
            email=email if isinstance(email, str) else None,
            email_verified=claims.get("email_verified") is True,
        )

    def _check_audience(self, value: Any, accepted: Collection[str]) -> None:
        audiences = [value] if isinstance(value, str) else value
        if not isinstance(audiences, list) or not any(
            isinstance(item, str) and item in accepted for item in audiences
        ):
            raise InvalidTokenError(f"the {self._audience_claim} claim names no accepted audience")


def verifier_for(
    settings: Settings,
    *,
    fetch: FetchJson = fetch_json,
    jwks_client_type: type[PyJWKClient] = PyJWKClient,
    clock: Callable[[], float] = time.monotonic,
) -> OidcVerifier:
    """The verifier for the issuer ``settings`` configure (``PF_OIDC_ISSUER``).

    Keys come from ``PF_OIDC_JWKS_URL`` when set, otherwise from the ``jwks_uri`` of the
    issuer's discovery document, which ``fetch`` reads on first use and which must name the
    same issuer; outside development that ``jwks_uri`` must also be https on the issuer's
    host and port. One ``jwks_client_type`` client, created once that URL is known, fetches
    and caches the key set for every request; neither request follows a redirect. Nothing
    else is ever requested from the provider.
    """
    issuer = settings.oidc_issuer
    client: PyJWKClient | None = None

    async def issuer_client() -> PyJWKClient:
        nonlocal client
        if client is None:
            jwks_url = settings.oidc_jwks_url or await _discovered_jwks_url(
                fetch, issuer, on_issuer_host=not settings.development
            )
            client = _jwks_client(jwks_client_type, jwks_url)
        return client

    return OidcVerifier(settings, issuer=issuer, jwks_client=issuer_client, clock=clock)


async def _discovered_jwks_url(fetch: FetchJson, issuer: str, *, on_issuer_host: bool) -> str:
    """The ``jwks_uri`` of ``issuer``'s discovery document.

    With ``on_issuer_host``, the URI must be https on the issuer's own host and port, so a
    discovery document cannot send key-set requests anywhere else. Raises ``CodedError``
    ``dependency_transient`` when the document cannot be read, does not name exactly
    ``issuer`` and a key-set URL, or names a URL that breaks that rule.
    """
    discovery = await fetch(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
    jwks_url = discovery.get("jwks_uri")
    if discovery.get("issuer") != issuer or not isinstance(jwks_url, str):
        raise CodedError(
            "dependency_transient",
            "The identity provider's discovery document does not match its issuer.",
        )
    if on_issuer_host and not _is_https_on_host_of(jwks_url, issuer):
        raise CodedError(
            "dependency_transient",
            "The identity provider's key set is not served over https from its own host.",
        )
    return jwks_url


def _is_https_on_host_of(url: str, issuer: str) -> bool:
    """Whether ``url`` is https on the same host and port as ``issuer``."""
    try:
        target, origin = urlsplit(url), urlsplit(issuer)
        return (
            target.scheme == "https"
            and target.hostname is not None
            and target.hostname == origin.hostname
            and (target.port or 443) == (origin.port or 443)
        )
    except ValueError:  # an invalid port
        return False


def _jwks_client(client_type: type[PyJWKClient], jwks_url: str) -> PyJWKClient:
    """A ``client_type`` client for ``jwks_url``.

    It caches the whole key set for ``MAX_KEY_SET_AGE_SECONDS``, the age at which
    ``_IssuerKeys`` refetches it anyway, and gives each request ``_FETCH_TIMEOUT_SECONDS``.
    """
    return client_type(
        jwks_url,
        cache_jwk_set=True,
        lifespan=MAX_KEY_SET_AGE_SECONDS,
        headers={"Accept": "application/json"},
        timeout=_FETCH_TIMEOUT_SECONDS,
    )


def fixture_verifier(
    settings: Settings,
    key_set: Callable[[], Mapping[str, Any]],
    *,
    clock: Callable[[], float] = time.monotonic,
) -> OidcVerifier:
    """A verifier that trusts only ``FIXTURE_ISSUER``, whose JWKS ``key_set`` returns.

    ``key_set`` runs in a worker thread wherever the real client would fetch over the
    network. For unit tests. Raises ``ValueError`` unless ``settings`` enable
    ``PF_AUTH_FIXTURE_ISSUER``, which configuration allows only in development.
    """
    if not settings.fixture_issuer_enabled:
        raise ValueError("the fixture issuer needs PF_AUTH_FIXTURE_ISSUER=true (development only)")

    class SuppliedKeySetClient(PyJWKClient):
        def fetch_data(self) -> Any:
            return key_set()

    client = _jwks_client(SuppliedKeySetClient, f"{FIXTURE_ISSUER}/protocol/openid-connect/certs")

    async def fixture_client() -> PyJWKClient:
        return client

    return OidcVerifier(settings, issuer=FIXTURE_ISSUER, jwks_client=fixture_client, clock=clock)


async def authenticate(
    verifier: OidcVerifier,
    engine: Engine,
    token: str,
    *,
    audiences: Collection[str],
    correlation_id: str,
) -> ExecutionContext:
    """Verify the bearer access ``token`` for a surface accepting ``audiences`` and return
    the caller's execution context.

    Provisions the caller's user and tenant on their first request (``resolve_context``).
    Raises ``CodedError`` ``unauthorized`` when the token fails any check, and
    ``dependency_transient`` when the issuer's keys cannot be fetched or the database cannot
    be reached.
    """
    try:
        identity = await verifier.verify(token, audiences=audiences)
    except InvalidTokenError as error:
        raise CodedError("unauthorized", "The access token is invalid or expired.") from error
    try:
        return await asyncio.to_thread(
            resolve_context, engine, identity, correlation_id=correlation_id
        )
    except (OperationalError, PoolTimeoutError) as error:
        raise CodedError("dependency_transient", "The database is unavailable.") from error
