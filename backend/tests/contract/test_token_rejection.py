"""Both adapters, REST and MCP, refuse every kind of bad bearer token the same way.

Each bad token differs from an accepted one in exactly one respect. The accepted token
copies the claims of a real access token from the local Keycloak realm (the web client's
for REST, a dynamically registered client's for MCP) and is signed by an extra key added to
the realm's published key set as the applications fetch it, so the expiry, issuer, audience
and token-type cases can be built; the algorithm cases use the realm's own published key.
A token for the other surface's audience is refused too. A refused token provisions no
tenant.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt import PyJWKClient
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import Engine, select
from support.keycloak import ISSUER, LocalKeycloak

from contract.mcp_client import McpClient, challenge_parameters
from property_focus.adapters.auth.oidc import fetch_json, verifier_for
from property_focus.adapters.db.models import User
from property_focus.adapters.db.repositories import ServiceScope, UnitOfWork
from property_focus.adapters.mcp.server import create_app as create_mcp_app
from property_focus.adapters.rest.app import create_app as create_rest_app
from property_focus.config import Settings

ADDED_KEY_ID = "added-signing-key"
TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
HOUR_SECONDS = 3600


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unsigned_part(header: Mapping[str, Any], claims: Mapping[str, Any]) -> str:
    return f"{b64url(json.dumps(header).encode())}.{b64url(json.dumps(claims).encode())}"


def new_rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


ADDED_KEY = new_rsa_key()
UNPUBLISHED_KEY = new_rsa_key()


def added_public_jwk() -> dict[str, Any]:
    jwk: dict[str, Any] = RSAAlgorithm.to_jwk(ADDED_KEY.public_key(), as_dict=True)
    return {**jwk, "kid": ADDED_KEY_ID, "use": "sig", "alg": "RS256"}


class ClientWithAddedKey(PyJWKClient):
    """The realm's key set as served, with ``ADDED_KEY`` appended."""

    def fetch_data(self) -> Any:
        document = super().fetch_data()
        return {**document, "keys": [*document["keys"], added_public_jwk()]}


@pytest.fixture(scope="module")
def realm_claims(keycloak: LocalKeycloak) -> dict[str, Any]:
    """The claims of a real access token from the realm's web client, for REST."""
    return keycloak.pkce_sign_in(keycloak.create_user()).access_claims


@pytest.fixture(scope="module")
def realm_mcp_claims(keycloak: LocalKeycloak) -> dict[str, Any]:
    """The claims of a real access token from a dynamically registered client, for MCP."""
    return keycloak.mcp_sign_in(keycloak.create_user()).access_claims


@pytest.fixture(scope="module")
def realm_public_key_pem(keycloak: LocalKeycloak) -> bytes:
    """The realm's published RS256 signing key, as PEM."""
    certs = asyncio.run(fetch_json(keycloak.realm.discovery["jwks_uri"]))
    entry = next(key for key in certs["keys"] if key.get("use") == "sig" and key["kty"] == "RSA")
    public_key = RSAAlgorithm.from_jwk(entry)
    assert isinstance(public_key, rsa.RSAPublicKey)
    return public_key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )


@pytest.fixture(scope="module")
def adapters(
    settings: Settings, runtime_engine: Engine, keycloak: LocalKeycloak
) -> Iterator[tuple[TestClient, TestClient]]:
    """The REST and MCP applications, sharing one verifier that also trusts ``ADDED_KEY``."""
    verifier = verifier_for(settings, jwks_client_type=ClientWithAddedKey)
    rest_app = create_rest_app(settings, engine=runtime_engine, verifier=verifier)
    mcp_app = create_mcp_app(settings, engine=runtime_engine, verifier=verifier)
    with (
        TestClient(rest_app, raise_server_exceptions=False) as rest,
        TestClient(mcp_app, raise_server_exceptions=False) as mcp,
    ):
        yield rest, mcp


def sign(
    claims: Mapping[str, Any], *, key: rsa.RSAPrivateKey = ADDED_KEY, kid: str = ADDED_KEY_ID
) -> str:
    return jwt.encode(dict(claims), key, algorithm="RS256", headers={"kid": kid})


type TokenMaker = Callable[[dict[str, Any], bytes], str]


def changed_claims(**changes: Any) -> TokenMaker:
    return lambda claims, _: sign({**claims, **changes})


def unsigned(claims: dict[str, Any], _: bytes) -> str:
    return unsigned_part({"alg": "none", "typ": "JWT", "kid": ADDED_KEY_ID}, claims) + "."


def hs256_with_the_realm_public_key(claims: dict[str, Any], realm_key_pem: bytes) -> str:
    signing_input = unsigned_part({"alg": "HS256", "typ": "JWT", "kid": ADDED_KEY_ID}, claims)
    signature = hmac.new(realm_key_pem, signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64url(signature)}"


def signed_by_an_unpublished_key(claims: dict[str, Any], _: bytes) -> str:
    return sign(claims, key=UNPUBLISHED_KEY, kid="unpublished-key")


def unpublished_key_claiming_a_published_kid(claims: dict[str, Any], _: bytes) -> str:
    return sign(claims, key=UNPUBLISHED_KEY, kid=ADDED_KEY_ID)


NOW = int(time.time())
BAD_TOKENS: dict[str, TokenMaker] = {
    "expired": changed_claims(iat=NOW - 2 * HOUR_SECONDS, exp=NOW - HOUR_SECONDS),
    "not-yet-valid": changed_claims(nbf=NOW + HOUR_SECONDS, exp=NOW + 2 * HOUR_SECONDS),
    "wrong-audience": changed_claims(aud="some-other-api"),
    "wrong-issuer": changed_claims(iss="http://localhost:8080/realms/master"),
    "id-token": changed_claims(typ="ID"),
    "alg-none": unsigned,
    "hs256-with-the-realm-public-key": hs256_with_the_realm_public_key,
    "unknown-key": signed_by_an_unpublished_key,
    "forged-signature-for-a-published-kid": unpublished_key_claiming_a_published_kid,
}


def fresh_claims(realm_claims: dict[str, Any]) -> dict[str, Any]:
    """``realm_claims`` for a subject nobody has used, valid for the next hour."""
    return {
        **realm_claims,
        "sub": str(uuid.uuid4()),
        "iat": int(time.time()),
        "exp": int(time.time()) + HOUR_SECONDS,
    }


def users_with_subject(engine: Engine, subject: str) -> int:
    with UnitOfWork(engine, ServiceScope()) as uow:
        return len(
            uow.session.execute(
                select(User.id).where(User.issuer == ISSUER, User.subject == subject)
            ).all()
        )


def test_token_with_the_realm_claims_signed_by_a_published_key_is_accepted_by_both(
    adapters: tuple[TestClient, TestClient],
    mcp_client_for: Callable[[TestClient], McpClient],
    realm_claims: dict[str, Any],
    realm_mcp_claims: dict[str, Any],
    runtime_engine: Engine,
) -> None:
    rest, mcp = adapters
    rest_claims, mcp_claims = fresh_claims(realm_claims), fresh_claims(realm_mcp_claims)

    rest_response = rest.get(
        f"/v1/workflow-runs/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {sign(rest_claims)}"},
    )
    mcp_response = mcp_client_for(mcp).post(TOOLS_LIST, token=sign(mcp_claims))

    assert rest_response.status_code == 404, rest_response.text
    assert mcp_response.status_code == 200, mcp_response.text
    assert users_with_subject(runtime_engine, rest_claims["sub"]) == 1
    assert users_with_subject(runtime_engine, mcp_claims["sub"]) == 1


@pytest.mark.parametrize("make_token", BAD_TOKENS.values(), ids=BAD_TOKENS.keys())
def test_bad_token_is_refused_by_rest_and_mcp_without_provisioning(
    adapters: tuple[TestClient, TestClient],
    mcp_client_for: Callable[[TestClient], McpClient],
    realm_claims: dict[str, Any],
    realm_mcp_claims: dict[str, Any],
    realm_public_key_pem: bytes,
    runtime_engine: Engine,
    make_token: TokenMaker,
) -> None:
    rest, mcp = adapters
    rest_claims, mcp_claims = fresh_claims(realm_claims), fresh_claims(realm_mcp_claims)

    rest_response = rest.get(
        f"/v1/workflow-runs/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {make_token(rest_claims, realm_public_key_pem)}"},
    )
    mcp_response = mcp_client_for(mcp).post(
        TOOLS_LIST, token=make_token(mcp_claims, realm_public_key_pem)
    )

    assert rest_response.status_code == 401, rest_response.text
    assert rest_response.headers["content-type"] == "application/problem+json"
    assert rest_response.json()["code"] == "unauthorized"
    assert mcp_response.status_code == 401, mcp_response.text
    challenge = challenge_parameters(mcp_response.headers["www-authenticate"])
    assert challenge["error"] == "invalid_token"
    assert users_with_subject(runtime_engine, rest_claims["sub"]) == 0
    assert users_with_subject(runtime_engine, mcp_claims["sub"]) == 0


def test_a_token_for_the_other_surface_is_refused_by_each_without_provisioning(
    adapters: tuple[TestClient, TestClient],
    mcp_client_for: Callable[[TestClient], McpClient],
    realm_claims: dict[str, Any],
    realm_mcp_claims: dict[str, Any],
    runtime_engine: Engine,
) -> None:
    rest, mcp = adapters
    rest_claims, mcp_claims = fresh_claims(realm_claims), fresh_claims(realm_mcp_claims)

    # A web client token at MCP, and an MCP client token at REST.
    mcp_response = mcp_client_for(mcp).post(TOOLS_LIST, token=sign(rest_claims))
    rest_response = rest.get(
        f"/v1/workflow-runs/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {sign(mcp_claims)}"},
    )

    assert mcp_response.status_code == 401, mcp_response.text
    challenge = challenge_parameters(mcp_response.headers["www-authenticate"])
    assert challenge["error"] == "invalid_token"
    assert rest_response.status_code == 401, rest_response.text
    assert rest_response.json()["code"] == "unauthorized"
    assert users_with_subject(runtime_engine, rest_claims["sub"]) == 0
    assert users_with_subject(runtime_engine, mcp_claims["sub"]) == 0
