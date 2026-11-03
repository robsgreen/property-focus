# Property Focus, Phase 0: Foundation

Completed: 2026-11-02

Phase 0 of the Property Focus spec (`specs/property-focus/`), shipped as the first of one pull request per phase. It touches every module: `backend/` (settings, domain errors, database and tenancy, auth, REST, MCP, telemetry, task runtime), `frontend/`, `infrastructure/` (Keycloak realm, Docker images, dogfood runtime, CDK identity stack), `.github/workflows/`, and the living docs under `docs/`.

## Problem

Property Focus will ingest MLS listings, build evidence-backed intelligence about each home and rank homes against each search's requirements. None of that can start until the system can sign a person in, give them a private tenant, keep their rows apart from everyone else's, run background work without an always-on worker, and show what happened through logs and traces. Phase 0 builds that base and the pipeline that proves it: local Compose services, a dogfood host, path-aware CI and the Foundation ready gate. Phase 0 ships no listing, property or search feature.

The plan assumed an AWS account. The user has none yet, so phase 0 had to run end to end with no cloud account (amendment A4).

## Alternatives Considered

The full table is in `specs/property-focus/master-plan.md`, "Alternatives and trade-offs". The rows phase 0 acted on:

| Approach | Pros | Cons | Why Rejected |
|----------|------|------|--------------|
| Cognito everywhere (original D28) | One provider from day one; matches the AWS target | Needs an AWS account | The user has no AWS account; Keycloak runs locally and on dogfood, and Cognito waits for phase 9 (A4) |
| Auth0 or Clerk | No identity server to operate | Another external account; free tiers cap users, features or clients | Same reason as Cognito, with worse limits |
| A signed mock issuer in tests | No Keycloak container in CI | Never exercises real sign-in, PKCE enforcement or dynamic registration | The registration policy is a security control, so tests run against the real realm |
| Client ID Metadata Documents first, dynamic registration as fallback | Matches MCP 2026-07-28's preferred flow | On Keycloak 26.8.1, the realm-wide redirect executor rejects every CIMD sign-in, CIMD trusts only hand-listed hosts, and `resource-indicators` drops our token audience | Measured, not assumed; the user chose option C: dynamic registration only, both experimental features off |
| Official `mcp` Python SDK 2.x | Tracks the protocol for us | Replaces the transport and auth binding in phase 0 for no behavior the contract tests need | The hand-rolled Starlette adapter already passed its contract suite |
| Always-on worker fleet | Simple to reason about | Idle cost in a mostly idle system | On-demand `run-task` invocations with leases and a transactional outbox (D26) |

## Decisions & Trade-offs

- **Decision:** Self-hosted Keycloak 26.8.1 for development, tests and dogfood, with the realm kept as code (`infrastructure/keycloak/realm-property-focus.json`). The CDK Cognito stack is synthesized and tested but not deployed.
  **Trade-off:** You operate Keycloak (upgrades, backups) and two providers exist. Cognito sign-out does not work until phase 9, because Cognito publishes no `end_session_endpoint`.
  **Rationale:** No cloud account needed. The backend reads only the discovery document and key set, so application code stays provider-neutral except for web sign-out.

- **Decision:** One stateless `/mcp` endpoint serves the initialize-based revisions (2025-03-26 to 2025-11-25) and 2026-07-28 side by side (Task 0.14).
  **Trade-off:** We follow each revision's wire changes ourselves.
  **Rationale:** Third-party MCP clients upgrade on their own schedule; one endpoint avoids a second URL for clients to discover.

- **Decision:** Dynamic client registration is the only MCP registration path (option C). The realm refuses confidential clients, non-loopback `http`, wildcards and custom schemes, requires PKCE and consent, and caps clients at `PF_KEYCLOAK_MAX_CLIENTS`.
  **Trade-off:** CIMD-only clients cannot connect. Unauthenticated spam can fill the cap and block new clients (P49, accepted for dogfood only).
  **Rationale:** Enabling CIMD on 26.8.1 would have weakened or narrowed the redirect rule. The user will add automatic cleanup or rate limiting before other people can sign up.

- **Decision:** The MCP wire and OAuth discovery surface is a Tier 1 contract. The user reviews it in person before release.
  **Trade-off:** Release waits on a human review.
  **Rationale:** Third-party clients acting for self-registered account holders consume it, so a break reaches people outside the project.

- **Decision:** `rls.sql` is frozen with released migration 0001 and pinned by `unit/test_migrations.py`. Later tasks add row-security policies in their own migrations.
  **Trade-off:** The planned tasks 3.4, 4.2, 4.4 and 4.5 that say `rls.sql (modify)` need rewording.
  **Rationale:** Editing the file would silently rewrite a released migration (P14).

- **Decision:** A Step Functions execution name comes from the per-launch idempotency key (run ID plus `launch_count`), not the run revision.
  **Trade-off:** One more column and a partial repair index in migration 0001.
  **Rationale:** With the revision name, a failure before lease acquisition made every relaunch hit `ExecutionAlreadyExists` and report success, stranding the run until its deadline (P15).

- **Decision:** Dogfood deploys into a release directory per commit and switches a `current` symlink only after health checks. Compose is the only parser of the environment file.
  **Trade-off:** Old releases take disk space until an operator removes them; Keycloak and the schema never roll back.
  **Rationale:** Review found the web build going live before `deploy.sh` ran, stale bind mounts and three disagreeing env-file parsers (P32 to P34).

## What Changed

How each part works is in `docs/system/`; start at `AGENTS.md` and `docs/system/architecture.md`.

- `backend/src/property_focus/config.py`, `adapters/secrets.py`, `domain/errors.py`: typed `PF_*` settings, the `SecretProvider` port, stable error codes and retry policy. See `docs/system/modules/configuration-and-secrets.md` and `failures-and-retries.md`.
- `backend/migrations/versions/0001_foundation.py`, `adapters/db/`: schema, roles and forced row-level security per tenant. See `database-and-tenancy.md`.
- `adapters/auth/oidc.py`, `adapters/rest/`, `adapters/mcp/server.py`: token verification with tenant provisioning, the REST skeleton and the dual-era MCP adapter. See `identity-and-auth.md`, `rest-api.md`, `mcp-adapter.md`.
- `application/workflows.py`, `runtime/`: workflow runs, transactional outbox, fenced leases, launchers and the scheduler. See `workflows-and-tasks.md`.
- `adapters/telemetry.py`: OpenTelemetry traces and metrics, JSON logs, linear-time redaction. See `telemetry.md`.
- `frontend/`: React web shell with OIDC PKCE sign-in and a generated RTK Query client. See `web-app.md`.
- `infrastructure/`, `compose.yaml`, `.github/workflows/`: Keycloak realm, images, dogfood Compose, Caddy and `deploy.sh`, CDK identity stack, path-aware CI and the manual dogfood deploy. See `infrastructure-and-delivery.md` and `docs/operations.md`.
- `specs/property-focus/gates/foundation-ready.md`: the Foundation ready gate. It stays open until three live items run: the macOS arm64 bootstrap (0.12.1), a recorded CI run (0.12.3) and the dogfood smoke (0.12.5).

Dogfood runs at `propertyfocus.treb.tech` with sign-in at `auth-propertyfocus.treb.tech` (first answered as `auth.propertyfocus.treb.tech`, then moved to a first-level name because Cloudflare's free Universal SSL covers only first-level subdomains), and Keycloak sends mail through Mailgun (`smtp.mailgun.org:2525`, STARTTLS). The user answered this checkpoint (CP-0.B) during review.

## Lessons Learned

- **Write the plan change before the code.** A4, Task 0.13, Task 0.14 and option C each landed in the register or task spec before or with their code (`37b3eb1`, `afbad70`, `f22c1a4`), so reviewers could check code against a current plan. Review fixes skipped that step, and several downstream tasks now disagree with the code.
- **Spike experimental features before planning on them.** CIMD and resource indicators were planned from documentation. Measuring them against Keycloak 26.8.1 found three blockers and cost one planner and one coder round that a short spike would have saved.
- **Build the leak corpus before touching a redaction regex.** One fix for redaction (P10) caused a leak regression (N1), and the fix for that caused a quadratic-time denial of service (N4). The fixed version runs against 113 leak strings and about 70 adversarial timing shapes. Run that corpus first next time.
- **Start the Docker daemon at session start.** The cloud container needed manual `dockerd` starts before integration runs.
- **Tick tasks per commit.** An API spend limit stopped work mid-task; per-commit ticks made the resume clean.

## Round 2: maintained libraries and edge protection

After the first review, the user confirmed two amendments on 2026-11-02 and phase 0 took three more tasks (0.15 to 0.17) before its gate rerun. Two rows and one decision above record round 1 and no longer describe the code: the "Official `mcp` Python SDK 2.x" row was rejected then and adopted now, and the stateless `/mcp` endpoint now follows each revision through the SDK, not through code we wrote.

### Round 2 decisions

- **Decision:** Prefer maintained libraries for security and protocol code (A6). The verifier fetches and caches keys through PyJWT's `PyJWKClient` (Task 0.15); the MCP adapter runs on the official MCP Python SDK's low-level `Server` and streamable HTTP transport (Task 0.16). `pyjwt[crypto]` is pinned to `>=2.15.1,<3` and `mcp` to `>=2.3,<2.4`, and `test_security_dependencies.py` holds both ranges.
  **Trade-off:** The SDK now defines wire details the hand-rolled adapter chose: it serves revision 2024-11-05 (sign-in still required), answers a 2026-07-28 request without its `_meta` envelope with `-32602`, and writes the `401` challenge in its own format. Each `mcp` minor upgrade needs its own change with `test_mcp.py` as the gate, and the user's Tier 1 review restarts on the new adapter.
  **Rationale:** The user wants protocol revisions and security fixes to arrive from upstream. We kept only what no library supplies: discovery with an exact issuer match, the algorithm allowlist, the per-provider audience and token-type claims, the tenant mapping and the `503`/`401` split.

- **Decision:** Keep a small refresh guard (`_IssuerKeys`) around `PyJWKClient`.
  **Trade-off:** The refresh policy stays ours to maintain.
  **Rationale:** The client's own cooldown counts only successful fetches and reads its own clock, so an issuer outage would mean a fetch attempt per request, and tests could not drive time. The guard counts every attempt on the verifier's injected clock, allows one unknown-`kid` refetch a minute, expires the set after ten minutes and answers `503` for an expired set it cannot refetch. `test_auth.py` and `test_issuer_failures.py` cover both limits.

- **Decision:** Cloudflare in front of both dogfood hostnames (A5, Task 0.17): proxied records, Full (strict) TLS with an Origin CA certificate, a host firewall admitting only Cloudflare's ranges, the Free managed WAF ruleset and one rate-limiting rule (20 requests per 10 seconds per IP, Block) over sign-in, registration and `/mcp`.
  **Trade-off:** Dogfood depends on a third party for reachability. Cloudflare's range list and the origin certificate need upkeep, and `deploy.sh` refuses to deploy when either has drifted. Bot Fight Mode stays off, so automated abuse meets only the WAF and the rate limit.
  **Rationale:** Anonymous registration, sign-in and `/mcp` face the internet, and the rate limit blunts registration spam (P49) until phase 5 adds per-account limits and client pruning. The Free plan cannot exempt paths from Bot Fight Mode, and it would challenge MCP clients, token requests and the smoke check.

- **Decision:** Sign-in moved from `auth.propertyfocus.treb.tech` to `auth-propertyfocus.treb.tech`.
  **Trade-off:** The hostname reads less naturally.
  **Rationale:** Cloudflare's free Universal SSL covers only first-level subdomains, so the second-level name needed a paid certificate. Only `PF_DOGFOOD_AUTH_HOST` changed; no code names the host.

### Round 2 changes

- `backend/src/property_focus/adapters/auth/oidc.py`: `PyJWKClient` replaces the hand-written key-set cache. See `docs/system/modules/identity-and-auth.md`.
- `backend/src/property_focus/adapters/mcp/server.py`, `config.py`: the SDK server, `SharedTokenVerifier` over `oidc.authenticate`, and the new `PF_MCP_PUBLIC_URL`, `PF_MCP_ALLOWED_HOSTS` and `PF_MCP_ALLOWED_ORIGINS` settings. See `mcp-adapter.md` and `configuration-and-secrets.md`.
- `infrastructure/dogfood/` (`Caddyfile`, `compose.yaml`, `deploy.sh`, new `firewall.sh`, `cloudflare-ips.txt`, `cloudflare-origin-ca.pem`), `deploy-dogfood.yml`, `smoke_dogfood.py`: the edge, its deploy preflight and the smoke check through Cloudflare. See `infrastructure-and-delivery.md` and the Cloudflare edge runbook in `docs/operations.md`.
- New tests: `contract/test_issuer_failures.py`, `unit/test_mcp_adapter.py`, `unit/test_dogfood_edge.py`, `integration/test_security_dependencies.py`; `unit/test_mcp_protocol.py` went with the hand-rolled protocol code. The gate rerun at `e8f8982` passed 627 backend tests (0.12.7).

### Round 2 lessons

- **Check a library before writing protocol code.** Round 1 rejected the SDK because the hand-rolled adapter already passed; round 2 replaced the hand-written JSON-RPC, transport and era code with the SDK and kept the contract suite. Write contract tests at the specification level (codes and fields, not message texts) so a library swap does not rewrite them.
- **A library default is not a security policy.** `PyJWKClient` covers fetching and parsing, not refetch limits or stale-key expiry. Read what a library does on failure before deleting the code it replaces.
- **Check plan limits before naming hostnames and settings.** Universal SSL coverage and the Free plan's Bot Fight Mode rules each reversed a planned choice on the day. A look at the provider's limits page while planning would have caught both.

## Post-Mortem Findings

- **Finding:** Plan text drifted from review fixes: tasks 3.4, 4.2, 4.4 and 4.5 still modify `rls.sql`, and Task 9.2.1 and the plan's workflow contract still name executions by run revision.
  **Impact:** A later coder following the plan will fail `test_migrations.py` or reintroduce the P15 stuck-run bug.
  **Resolution:** Action items in the post-mortem; the planning documents are being updated separately.
- **Finding:** The gate evidence was recorded at `3eae973` (333 unit, 100 integration), before five fix commits.
  **Impact:** The gate described code that no longer existed.
  **Resolution:** `make verify` rerun at the final commit and the gate updated (see `specs/property-focus/gates/foundation-ready.md`, 0.12.2).
- **Finding:** Cognito sign-out fails by design until phase 9, and no phase 9 task covers it.
  **Impact:** Switching the issuer to Cognito without that task leaves users unable to sign out.
  **Resolution:** Action item to add a phase 9 sub-task (`/logout` with `client_id` and `logout_uri`, via `PF_WEB_OIDC_END_SESSION_URL`).
- **Finding:** The SSRF fix added a second `secure-client-uris-pattern` executor (`bd1eae5`).
  **Impact:** Revisiting CIMD (CP-9.D) now has one more executor to reconcile.
  **Resolution:** Recorded against CP-9.D.
- **Finding:** `test_redaction_takes_linear_time_on_long_crafted_text` uses a 1.0 s bound against about 0.3 s measured.
  **Impact:** A slow CI runner could fail it.
  **Resolution:** Open action item to widen or relativize the bound. The telemetry doc now states the measured worst case.
- **Finding:** Dev dependency `httpx2==2.16.0` points at a GitHub repository the review could not reach.
  **Impact:** Unverified supply-chain provenance.
  **Resolution:** Confirm before merge.

## Review Highlights

- **Sign-out raced sign-in (P1, critical).** Clearing tokens before navigating to the issuer let the sign-in boundary start a new authorize request that landed last, so the issuer's SSO session signed the user straight back in. A jsdom reproduction became the regression test (`frontend/tests/sign-out.test.tsx`). Any future auth-state change should order navigation before state notification.
- **Anonymous registration was an SSRF path (P30).** Keycloak validated only `redirectUris`, yet fetches or calls `sector_identifier_uri`, `request_uris`, back-channel logout and JWKS URIs. Open registration needs every client URI field checked, not only redirects.
- **Redaction on the request path is a performance contract (N4).** Telemetry redacts client-controlled values synchronously, so a quadratic regex blocked the event loop for 3 s on one crafted `User-Agent`. Treat any pattern there as attacker-facing.
- **Planning IDs leaked into operator text (P36).** Messages and docs named checkpoints like CP-0.B. Keep planning vocabulary out of runtime output.
- **The two-round review cap worked as an escalation point.** The user authorized two extra fix passes (`c188e47`, `71694cd`), each scoped to named findings with a scoped re-review.
