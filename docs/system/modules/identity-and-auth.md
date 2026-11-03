# Identity and Authentication

People sign in at an OpenID Connect issuer, never at Property Focus itself. The backend only verifies the access tokens that issuer signs, then maps each token's issuer and subject to an internal user and the tenant that user owns. (A person calls their tenant their account; the code says tenant.) Today the issuer is a Keycloak realm defined as code (amendment A4); a Cognito stack is kept for phase 9.

## Key files

| File | Role |
|---|---|
| `backend/src/property_focus/adapters/auth/oidc.py` | `OidcVerifier`, `verifier_for()`, `fixture_verifier()`, `authenticate()`, `InvalidTokenError` |
| `backend/src/property_focus/application/context.py` | `VerifiedIdentity`, `ExecutionContext`, `provision_tenant()`, `resolve_context()` |
| `infrastructure/keycloak/realm-property-focus.json` | The `property-focus` realm: settings, clients, client scopes, registration and client policies |
| `backend/scripts/keycloak_realm.py` | `apply`, `check`, `ensure-user` against a running Keycloak's admin API (standard library only) |
| `infrastructure/docker/keycloak.Dockerfile` | Dogfood Keycloak image (same pinned version as root `compose.yaml`) |
| `infrastructure/cdk/lib/identity-stack.ts` | Cognito user pool with web and MCP clients (not used by any running environment) |
| `fixtures/synthetic/accounts.json` | Synthetic identities for `cli seed`; they cannot sign in |
| `backend/tests/unit/test_auth.py`, `contract/test_token_rejection.py`, `integration/test_key_rotation.py`, `integration/test_keycloak_realm.py`, `tests/support/keycloak.py` | Verifier checks, rejection matrix, key rotation, realm behavior |
| `backend/tests/contract/test_issuer_failures.py` | Unknown-`kid` floods stay `401` without a key-set download each; an unreachable key set or a mismatched discovery document gives `503` on REST and MCP |
| `backend/tests/integration/test_security_dependencies.py` | `pyjwt` and `mcp` ranges exclude advisory releases and the next incompatible line; `uv.lock` resolves inside them |

## Token verification

`verifier_for(settings)` builds the verifier for `PF_OIDC_ISSUER`. It reads `<issuer>/.well-known/openid-configuration` on first use (its `issuer` must match exactly) to find `jwks_uri`, unless `PF_OIDC_JWKS_URL` overrides it, then creates one PyJWT `PyJWKClient` for that URL, which fetches and caches the key set for every request to the issuer (amendment A6). It requests nothing else from the provider. A discovery document that cannot be read or names another issuer raises `CodedError("dependency_transient")`.

`OidcVerifier.verify(token)` checks, in order:

1. The header `alg` is in `PF_OIDC_ALGORITHMS` and a `kid` is present.
2. The signature, against the key with that `kid`.
3. Through `jwt.decode`, given an options dictionary built inside each call so no decode can change another's checks (GHSA-gvp8-978c-rx2q): `iss` equals the issuer; `exp`, `iss` and `sub` are present; `exp` and `nbf` hold within `PF_OIDC_CLOCK_SKEW_SECONDS`.
4. The `PF_OIDC_AUDIENCE_CLAIM` claim (string or list) holds at least one accepted audience.
5. The token-type claim equals the configured value (`typ=Bearer` for Keycloak), so ID tokens are refused.

It returns `VerifiedIdentity(issuer, subject, email, email_verified)`; `email_verified` is true only when the claim is literally `true`. Any failed check raises `InvalidTokenError`, whose message never includes the token.

Key-set caching: the `PyJWKClient` fetches the key set, and a small refresh guard (`_IssuerKeys`) keeps the signing keys of the last fetched set and decides when to fetch again, on the verifier's clock. The set loads on first use and expires after 600 seconds (`MAX_KEY_SET_AGE_SECONDS`, also the client's cache lifespan); an expired set is refetched before use and never trusted again (a change under A6). A `kid` found in a current set is looked up in memory, with no lock, thread hop or I/O. Fetch attempts, including discovery on first use, run one at a time under an `asyncio.Lock`, each in a worker thread (`asyncio.to_thread`) because the client blocks, and at most one attempt, successful or not, starts per 60 seconds (`REFETCH_INTERVAL_SECONDS`). Requests that queued behind an attempt, or that arrive within that interval after it, take its outcome instead of fetching: after a success, a `kid` the new set lacks fails as an unknown key; after a failure, any request that needs a fetch (no set yet, an expired set, or an unknown `kid`) fails at once with `CodedError("dependency_transient")`, so an issuer outage costs one fetch a minute and every request gets a prompt `503`. Errors: a failed attempt (discovery, `PyJWKClientConnectionError`, a broken connection or a non-JSON body) raises `CodedError("dependency_transient")`; a failed refetch for an unknown `kid` leaves the current set serving its known keys. A key still missing after any allowed refetch, and every other PyJWT error, raises `InvalidTokenError`. Encryption keys and keys without `kid` are ignored. Each fetch times out after 10 seconds.

`fixture_verifier(settings, key_set)` trusts `FIXTURE_ISSUER` (`https://fixture-issuer.invalid/realms/property-focus`) with a caller-supplied key set, served by a `PyJWKClient` subclass whose `fetch_data` calls the supplier instead of the network. Only unit tests use it.

## From token to execution context

`authenticate(verifier, engine, token, correlation_id=...)` is the one function both adapters call. It turns `InvalidTokenError` into `CodedError("unauthorized")` and then calls `resolve_context()` in a worker thread; a database that cannot be reached there (connection failure or pool timeout) becomes `CodedError("dependency_transient")`, a retryable `503`.

`resolve_context()` opens one transaction on the runtime role with no tenant scope (the identity tables carry no RLS) and:

- `provision_tenant()`: finds the `users` row by (`issuer`, `subject`) or inserts it, then finds or inserts the `tenants` row it owns. Inserts use `ON CONFLICT DO NOTHING` and read back, so concurrent first requests create one user and one tenant. The email is stored only when the token marks it verified; a newly verified or changed email replaces the stored one.
- reads the user's system roles from `user_roles`.

It returns:

```python
ExecutionContext(
    principal_id=<users.id>,
    tenant_id=<tenants.id owned by that user>,
    entitlement_ids=(),          # licensed shared intelligence arrives later
    correlation_id="4b9e1f0c...",
    roles=frozenset({"operator"}),
)
```

`principal_id` is the caller's internal user ID (`users.id`), never the token's subject. `context.scope` is the caller's `TenantScope(tenant_id, user_id=principal_id)`, the scope their own reads and writes run in. The tenant is never read from a path, query or body.

## The Keycloak realm

The realm file is the source of truth. Keycloak imports it on first start (`--import-realm`); after that `keycloak_realm.py apply` reconciles a running server with it, and `check` exits 1 on any difference. `apply` never deletes users or clients, so dynamically registered MCP clients survive. `${NAME}` and `${NAME:default}` placeholders are filled from the environment the same way Keycloak does (`PF_WEB_ORIGIN`, `PF_KEYCLOAK_MAX_CLIENTS`, `PF_KEYCLOAK_SMTP_*`).

| Element | Configuration |
|---|---|
| Realm | `property-focus`; self-registration on, email verification required; password policy `length(12) and notUsername and notEmail`; password reset by verified email |
| `property-focus-web` | Public client, standard flow only, PKCE `S256`, redirect `${PF_WEB_ORIGIN}/auth/callback`, post-logout `${PF_WEB_ORIGIN}/` |
| `property-focus-api` client scope | Default scope with an audience mapper adding `property-focus-api` to access tokens |
| Client policy `client-rules` (all clients) | PKCE enforced; URIs a browser opens (redirect, post-logout, front-channel logout, root, base, web origins, logo, policy, terms) must be `https`, or `http` on `localhost`, `127.0.0.1` or `[::1]`; URIs Keycloak itself would fetch or call (key set, request URIs, sector identifier, back-channel logout, admin URL, CIBA notification, SAML metadata and SOAP endpoints) are refused outright, so registration never makes Keycloak connect anywhere; implicit and password grants rejected |
| Client policy `public-dynamic-clients-only` | Dynamic registration cannot create or convert to a confidential client |
| Client policy `no-client-credentials-grant` | No client may use the client-credentials grant |
| Anonymous registration policies | Consent required, allowed client scopes, full scope disabled, allowed mapper types (no pairwise subject mapper), max clients (`PF_KEYCLOAK_MAX_CLIENTS`, default 100), allowed web origins |

Client ID Metadata Documents and RFC 8707 resource indicators stay off (option C, CP-9.D); MCP clients use anonymous dynamic client registration.

Locally, Compose runs Keycloak 26.8.1 on `http://localhost:8080` with Mailpit as SMTP. On dogfood, Caddy exposes only the realm's protocol paths on the auth host; the admin console is reachable only through an SSH tunnel ([operations](../../operations.md)).

## The Cognito stack (retained)

`IdentityStack` (`PropertyFocus-Identity-<env>`) defines a user pool with verified-email self sign-up, a hosted UI domain and two public PKCE clients: one for the web app, one shared by every MCP client (Cognito needs an allowlist of MCP callback URLs in `cdk.json`). The pool is retained and deletion-protected. `make deploy-identity ENV=dogfood` is the only deploy path and the account in `cdk.json` is still `null`, so nothing runs against it. Pointing the backend at it needs only `PF_OIDC_ISSUER`, `PF_OIDC_AUDIENCE_CLAIM=client_id` and `PF_OIDC_TOKEN_TYPE=token_use=access`.

## Consumers

The [REST API](./rest-api.md) (`execution_context` dependency), the [MCP adapter](./mcp-adapter.md) (bearer check and protected-resource metadata), the [web app](./web-app.md) (sign-in against the same issuer), and the CLI `seed` command (calls `provision_tenant()` for fixture identities).

## Design decisions

- One generic verifier with provider-neutral settings, so Keycloak and Cognito differ only in configuration and no adapter knows which client is calling (`test_client_neutrality.py`).
- Tenants are created on first authenticated request rather than through a sign-up API (A1).
- Unverified emails are never stored, so an email address cannot be claimed without proof.
- PyJWT's `PyJWKClient` fetches and parses the key set (A6); our code keeps only the refresh policy (`_IssuerKeys`), because the client's own refetch cooldown counts only successful fetches (so an issuer outage would cost a fetch per request), does not share one attempt's outcome with the requests waiting on it, and reads the system clock rather than an injectable one; the guard limits attempts, successful or failed, shares each outcome, and runs on the verifier's clock. `pyjwt[crypto]` is pinned to `>=2.15.1,<3`: 2.15.1 is the first release without the shared-options advisory (GHSA-gvp8-978c-rx2q).

## Related docs

- [Configuration and secrets](./configuration-and-secrets.md) for `PF_OIDC_*`
- [Database and tenancy](./database-and-tenancy.md) for `users`, `tenants`, `user_roles`
- [MCP adapter](./mcp-adapter.md) for OAuth discovery
