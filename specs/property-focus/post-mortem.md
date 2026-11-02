# Post-mortem: property-focus — Phase 0 (Foundation)

Branch `sdlc/property-focus-p0` (worktree `property-focus-worktrees/property-focus-p0`), base `origin/main` at `b6a6a82`, HEAD `6e77af1`. Diff: 158 files, +38,775/−1 (includes the plan, decision register and task spec). Round 1 ended at `71694cd`; round 2 is `c9e474f..6e77af1` (45 files, +4,363/−1,365).

**Scope.** This retro covers phase 0 only. The user chose one PR per phase (status.md, 02:17), so phases 1–9 (tasks 1.x–9.x, still unticked in `task-spec.md`) are planned future work, not drift. Phase 0 tasks are 0.1–0.17 (0.13 and 0.14 were added mid-phase; 0.15–0.17 in round 2).

**Rounds.** Round 1 (tasks 0.1–0.14) reached the PR gate at `351b6e5`. The user then asked to replace hand-written security and protocol code with maintained libraries and to add edge protection, which reopened phase 0 for round 2 (status.md, 14:32–14:40). Action Items at the end are the current open list for both rounds.

# Round 1 — Foundation (tasks 0.1–0.14)

## Summary

Phase 0 built the planned foundation: the D47 monorepo and checks, typed settings and `SecretProvider`, Postgres with forced row-level security, on-demand task runtime with leases and outbox, an OIDC verifier with account provisioning, REST and MCP skeletons, telemetry with redaction, the web app with PKCE sign-in and generated RTK Query client, the CDK Cognito identity stack (synth only), dogfood Compose/Caddy/deploy, and path-aware CI. `make verify` is green at HEAD (405 unit, 172 integration/contract, 33 web, 15 CDK).

The plan changed twice mid-phase, both through the user and recorded in the register and task spec before code followed: A4 (self-hosted Keycloak for dev, tests and dogfood, because the user has no AWS account) added Task 0.13, and MCP research added Task 0.14 (dual-era MCP 2025-03-26 to 2026-07-28), from which Keycloak CIMD was then deferred (option C). Review then reshaped several internals and added security controls. The Foundation ready gate is **open**: three live items (0.12.1, 0.12.3, 0.12.5) need the user's action or approval.

**Assessment:** significant but well-governed drift. Every High row was a user-directed plan amendment. The Medium rows come mostly from review fixes that left downstream plan text out of step.

## Planned vs. Built

| Planned outcome (task) | Evidence in source |
|---|---|
| Monorepo, repository checks, boundaries (0.1) | `Makefile`, `backend/scripts/check_boundaries.py:42`, `check_fixture_secrets.py` |
| Settings, `SecretProvider`, failure taxonomy (0.2) | `backend/src/property_focus/config.py`, `domain/errors.py` |
| DB, migrations, RLS, two-account isolation (0.3) | `backend/migrations/versions/0001_foundation.py:332`, `adapters/db/rls.sql`, `integration/test_tenancy.py` (19/19) |
| Keycloak realm as code and local Compose (0.13, A4) | `infrastructure/keycloak/realm-property-focus.json`, `compose.yaml`, `integration/test_keycloak_realm.py` |
| OIDC verifier, provisioning, REST (0.4) | `adapters/auth/oidc.py`, `adapters/rest/app.py`, `unit/test_auth.py`, `contract/test_api.py` |
| MCP skeleton with OAuth discovery and DCR (0.5) | `adapters/mcp/server.py`, `contract/test_mcp.py::test_dynamically_registered_generic_client_signs_in_and_calls_mcp` |
| Task runtime, launcher, workflow runs, outbox (0.6) | `runtime/launcher.py:187`, `application/workflows.py`, `runtime/run_task.py` |
| Telemetry baseline (0.7) | `adapters/telemetry.py:174,204`, `unit/test_redaction.py` |
| Web app and generated client (0.8) | `frontend/src/auth/session.ts:264`, `frontend/src/api/generated.ts`, `frontend/tests/sign-out.test.tsx` |
| CDK identity stack, built not deployed (0.9) | `infrastructure/cdk/lib/`, `infrastructure/cdk/test/identity.test.ts` |
| Dual-era MCP (0.14, CIMD removed) | `adapters/mcp/server.py`, `unit/test_mcp_protocol.py`, `test_mcp.py` dual-era cases |
| Dogfood manifests, images, MiniStack inventory (0.10) | `infrastructure/dogfood/{compose.yaml,Caddyfile,deploy.sh}`, `infrastructure/docker/*.Dockerfile`, `docs/compatibility.md` |
| Path-aware CI (0.11) | `.github/workflows/ci.yml`, `integration/test_ci_workflow.py` |
| Foundation ready gate (0.12) | `specs/property-focus/gates/foundation-ready.md`: 0.12.2/.4/.6 recorded; 0.12.1/.3/.5 **PENDING** |

## Plan Drift

| Area | Planned | Actual | Impact | Evidence |
|---|---|---|---|---|
| Identity provider (A4) | Cognito as issuer from phase 0; CP-0.A asked about Cognito DCR | Self-hosted Keycloak 26.8.1 for dev, tests and dogfood; new Task 0.13 (realm as code); CDK Cognito stack synthesized only, deploy moved to phase 9; CP-0.A withdrawn, moved to CP-9.B | High | `architecture-decisions.md:1404`; commit `37b3eb1`; `task-spec.md:56,204`; `infrastructure/keycloak/realm-property-focus.json` |
| MCP protocol surface (Task 0.14) | `/mcp` serving the initialize-based revisions | Added Task 0.14 after research: one stateless endpoint also serves 2026-07-28 (`_meta` version, header checks, `server/discover`, `-32020`/`-32022`) | High | commits `afbad70`, `2397030`; `task-spec.md:528`; `adapters/mcp/server.py`; `master-plan.md:202` |
| MCP client registration | Task 0.14 as first written: enable Keycloak `cimd` + `resource-indicators` | Option C: both features off; DCR is the only path; CIMD-only clients cannot connect (accepted risk, CP-9.D) | Medium | commit `f22c1a4`; `task-spec.md:530-538,3543`; `master-plan.md:816,829` |
| Foundation ready gate | Gate closed with all artifacts in phase 0 (0.12) | Gate open: macOS bootstrap (0.12.1), recorded CI run (0.12.3) and dogfood smoke (0.12.5) pending user action; local evidence recorded at `3eae973`, before the review fixes | Medium | `task-spec.md:697-712`; `gates/foundation-ready.md:3,140` |
| Realm security policy | Registration controls per 0.13.3 (redirect rules, PKCE, cap, consent) | Added after review P30/P31: second `secure-client-uris-pattern` executor refusing every fetched/called URI (sector identifier, request URIs, back-channel logout, JWKS, admin URL); password policy `length(12) and notUsername and notEmail`; reset by verified email | Medium | `realm-property-focus.json:12,607-622`; commit `bd1eae5` |
| Step Functions execution name | Deterministic name "derived from the admitted workflow run revision" | Name from the per-launch idempotency key (run id + `launch_count`), plus `launch_count`, `deadline_at NOT NULL` and a partial repair index in migration 0001 (P15, P23, P24) | Medium | `runtime/launcher.py:187-189`; `0001_foundation.py`; `master-plan.md:1806`; `task-spec.md:3604` |
| Task input envelope (P47) | `TaskInput.task_version` | Renamed `application_version` (build version), `TASK_INPUT_SCHEMA_VERSION = 2` | Medium | `application/ports.py:33,55`; commit `c188e47`; `master-plan.md:1501` |
| Dogfood OAuth discovery (P48, P37) | Discovery on the auth host only | App host serves `/.well-known/oauth-authorization-server` from the realm for 2025-03-26 clients; other OAuth/well-known paths 404 instead of the SPA fallback; `deploy.sh` checks the issuer | Medium | `infrastructure/dogfood/Caddyfile:31-42,70`; `deploy.sh` |
| `rls.sql` mutability (P14) | Later tasks append policies by modifying `rls.sql` | `rls.sql` is frozen as part of released migration 0001 and pinned by a test; later tasks must add policies in their own migration | Medium | `adapters/db/rls.sql:1-5`; `unit/test_migrations.py:6-14`; `task-spec.md:1419,1997,2099,2148` still list `rls.sql (modify)` |
| Web sign-out (P1, P2) | Provider-neutral sign-out from discovery, "no provider-specific code" (0.8.2) | Sign-out navigates to the issuer before clearing state; new optional build setting `PF_WEB_OIDC_END_SESSION_URL`; Cognito sign-out documented as needing a phase 9 change | Medium | `frontend/src/auth/session.ts:264`; `frontend/src/main.tsx:29`; `docs/system/modules/web-app.md:67`; `task-spec.md:466` |
| Dogfood deploy model (P32–P35, P41) | `deploy.sh` per 0.10.6 (build, migrate, realm apply, health) | Release directory per commit with `current` symlink switched after health checks; rollback restores images, web build, Caddyfile and collector config; Compose is the only env parser; containers `no-new-privileges`, `cap_drop: ALL`, memory limits; actions pinned by SHA | Medium | commit `bd1eae5`; `infrastructure/dogfood/deploy.sh`; `docs/operations.md:88`; `.github/workflows/deploy-dogfood.yml` |
| Domain vocabulary (P12, P13, P18) | `AccountScope`, `dispose_failure`, "duplicate" for several outcomes | `TenantScope`, `ExecutionContext.scope`, `failure_effect`; distinct decline outcomes; `duplicate` error code kept (plan contract, `master-plan.md:1817`) | Low | commit `6c2fdf5`; `application/context.py` |
| Dependencies (P7) | Playwright in the backend package | Removed until phase 2 needs it | Low | `backend/pyproject.toml` (commit `6c2fdf5`) |

## Critical Decisions

- **A4 Keycloak (user, mid-phase).** Keycloak issues every token outside AWS; Cognito returns only in phase 9 (`architecture-decisions.md:1404`). The application stays provider-neutral except for sign-out (see `web-app.md:67`).
- **Option C (user).** CIMD and resource indicators stay off; DCR is the only registration path. Measured incompatibilities with Keycloak 26.8.1 are recorded at `task-spec.md:538` and `master-plan.md:829`.
- **Review cap exceptions (user).** The user authorized two targeted fix passes beyond the two-round cap: `c188e47` (N1 redaction regression, plus N2, N3, P47, P48) and `71694cd` (N4 ReDoS, N5) (`review.md`, "User direction").
- **Tier 1 contract gate.** The MCP wire and OAuth discovery surface is Tier 1. The user will review it personally before release (`review.md`, "Contract tier").
- **P47/P48/P49 (user).** `task_version` became `application_version`. 2025-03-26 clients sign in on dogfood. The anonymous-registration cap risk is accepted for dogfood only, with cleanup required before other users.
- **CP-0.B answered (user, 09:07).** App `propertyfocus.treb.tech`, Keycloak `auth.propertyfocus.treb.tech`, SMTP Mailgun `smtp.mailgun.org:2525`, credentials only as deployment secrets. The answer is not yet in `gates/foundation-ready.md:140` or `docs/operations.md`.
- **`duplicate` code kept (P13 partial decline).** It is a plan stable code (`master-plan.md:1817`), now documented with one meaning.

## Risks and Concerns

- **Registration cap exhaustion (P49).** Unauthenticated spam can fill `max-clients` (`realm-property-focus.json:524-533`) and block new MCP clients. Mitigation today is the manual pruning runbook (`docs/operations.md:135`). No task automates it.
- **Cognito sign-out is broken by design until phase 9.** Cognito lists no `end_session_endpoint` and expects `client_id`/`logout_uri`. Sign-out there ends in `signOutFailed` (`web-app.md:67`), and no phase 9 task covers it (9.7.x has only callback/logout URL config).
- **CIMD revisit got harder.** The SSRF fix added a second `secure-client-uris-pattern` executor (`realm-property-focus.json:607-622`). CP-9.D already lists that executor as a CIMD blocker (`task-spec.md:3543`).
- **Plan text out of step with code.** Tasks 3.4, 4.2, 4.4, 4.5 list `rls.sql (modify)`, and `test_migrations.py` will fail them. Task 9.2.1 and `master-plan.md:1806` still describe execution names from the run revision.
- **Stale gate evidence.** `foundation-ready.md` was recorded at `3eae973` (333 unit / 100 integration) before five fix commits. HEAD has 405 / 172.
- **Timing-test flakiness.** `test_redaction_takes_linear_time_on_long_crafted_text` (`unit/test_redaction.py:121`) uses a 1.0 s bound against 0.30 s measured. A slow CI runner can fail it. `docs/system/modules/telemetry.md:47` says "well under a second" against a measured worst of ≈0.87 s/MB (N6).
- **Unverified dev dependency.** `httpx2==2.16.0` points at `github.com/pydantic/httpx2`, which the review could not reach (`review.md`, Warnings).

## Learnings

- **Fixes for one leak caused the next one.** One redaction rule (P10) was the right direction. The single-rule rewrite caused a regression (N1), and the fix for that caused a ReDoS (N4). A fixed leak corpus plus adversarial timing (now 113 strings and ~70 shapes) should come before any redaction regex change, not after.
- **Rewrite the plan before writing code.** A4, Task 0.13 and Task 0.14 were written into the register and task spec first (`37b3eb1`, `afbad70`), and option C was recorded before the code changed (`f22c1a4`). This kept the mid-phase changes reviewable. Review fixes did not get the same treatment, which is why the downstream rows above are out of step.
- **Measure an experimental feature before committing to it.** CIMD and resource indicators were planned from documentation. Measurement against 26.8.1 showed three incompatibilities. A short spike before adding Task 0.14 would have saved one planner and one coder round.
- **Environment friction.** An API spend limit interrupted Task 0.7 and the MCP doc update mid-run (status 04:29). Resuming worked because tasks tick per commit. The cloud container's Docker daemon needed manual starts before integration runs. Check the daemon at session start, before any `be-int` dispatch.
- **The two-round cap worked as an escalation point.** Each extra pass was scoped to named findings with a scoped re-review, and the user decided each time.

## What Went Well

Every scope change mid-phase (A4, Tasks 0.13/0.14, option C, P47–P49) reached the register or task spec before or with its code. The critical sign-out race (P1) was reproduced in jsdom, and its regression test (`frontend/tests/sign-out.test.tsx`) was handed to the fixer, who made it pass.

# Round 2 — Maintained libraries and edge protection (tasks 0.15–0.17)

## Summary

After the first PR-gate stop, the user asked whether the hand-written OIDC and MCP security code should come from maintained libraries. Research (explorer plus two researchers) led to four user decisions, recorded as A5 and A6 with Tasks 0.15–0.17, 5.13 and 9.8 before any code (`febafea`). Round 2 built three tasks. The verifier now uses PyJWT's `PyJWKClient` (Task 0.15, `884b178`). The MCP adapter is rebuilt on the official MCP Python SDK, `mcp` 2.3.0 low-level `Server` (Task 0.16, `0716270`). Cloudflare now fronts both dogfood hostnames (Task 0.17, `e7645f2`). During testing the user renamed the auth host, turned Bot Fight Mode off and confirmed the initial limits (`b90d40c`). Before round 2, the user also chose manual dogfood deploys and a staging-gated AWS pipeline (`c9e474f`). The round-1 retro did not cover that decision.

Review of `c9e474f..ce7de04` found 0 critical or high findings, plus 4 medium and 4 low. One fix pass (`6e77af1`) resolved R1–R8 with no regressions. Three low observations (S1–S3) were left at the two-round cap. `make verify` is green at `6e77af1` (451 unit, 193 integration/contract, 33 web, 15 CDK, plus tsc and synth). The Foundation ready gate stays **open**. It now has five pending items: 0.12.1, 0.12.3, 0.12.5, 0.12.8, and the user's Tier 1 review in 0.12.7 (`gates/foundation-ready.md:3`).

**Assessment:** significant, user-directed drift, recorded in the plan before the code changed. The SDK swap reverses a plan alternative and changes Tier 1 wire behavior, so the user's personal review is the main remaining gate.

## Planned vs. Built

| Planned outcome (task) | Evidence in source |
|---|---|
| `PyJWKClient` per issuer, off the event loop, refetch guard, expired-set `503` (0.15, A6) | `adapters/auth/oidc.py:35,54,97-196`; `backend/pyproject.toml:16` (`pyjwt[crypto]>=2.15.1,<3`); `unit/test_auth.py`; `contract/test_issuer_failures.py` |
| MCP on the official SDK: stateless JSON streamable HTTP, shared token check, explicit transport security, metadata at both URL forms (0.16, A6) | `adapters/mcp/server.py:270,279`; `backend/pyproject.toml:10` (`mcp>=2.3,<2.4`); `unit/test_mcp_adapter.py`; `contract/test_mcp.py`; `integration/test_security_dependencies.py` |
| Cloudflare edge: Origin CA cert from secrets, Full (strict), Cloudflare-only firewall, real client IP, smoke through Cloudflare (0.17, A5) | `infrastructure/dogfood/{Caddyfile:22-33,53, firewall.sh, cloudflare-ips.txt, deploy.sh}`; `backend/scripts/smoke_dogfood.py`; `unit/test_dogfood_edge.py`; runbook in `docs/operations.md` |
| Gate rerun on the SDK adapter and `PyJWKClient`; Cloudflare edge evidence (0.12.7, 0.12.8) | `gates/foundation-ready.md:108-142` (recorded at `e8f8982`, 627 backend tests); 0.12.8 **PENDING** (`:214`) |
| Per-account REST/MCP limits (5.13) and AWS WAF plus throttling (9.8) | Plan and tasks only (`task-spec.md:2978,3984`, `9.1.5` at `:3804`), as intended |

## Plan Drift

Baseline: the plan as of `c9e474f`'s parent (phase 0 as confirmed for round 1).

| Area | Planned | Actual | Impact | Evidence |
|---|---|---|---|---|
| MCP adapter (A6, Task 0.16) | Hand-rolled Starlette adapter; Task 0.14 required "`pyproject.toml` and `uv.lock` gain no `mcp` package". The SDK was a considered and rejected alternative | Official SDK `mcp` 2.3.0 low-level `Server` owns JSON-RPC, protocol eras and transport. Our code keeps the tool registry, the shared `TokenVerifier` over `oidc.authenticate` and `CodedError` bodies. `validate_token_resource=False` keeps our audience check. Wire behavior changes (listed under Critical Decisions) | High | commit `0716270`; `adapters/mcp/server.py:270,279`; `task-spec.md:547`; `master-plan.md:180,223-232,1243` |
| Dogfood edge (A5, Task 0.17) | Caddy obtains ACME certificates and the origin is reached directly | Both hostnames are proxied by Cloudflare with Full (strict). Caddy serves a Cloudflare Origin CA cert from deployment secrets, with automation off. `firewall.sh` admits 80/443 only from `cloudflare-ips.txt`. `deploy.sh` preflights ranges, firewall and cert. The smoke requires `cf-ray` and a failed direct-origin connection. New gate item 0.12.8 | High | commit `e7645f2`; `Caddyfile:22-24,53`; `firewall.sh:77-79,116`; `deploy.sh:221-258`; `task-spec.md:592,796,871`; `master-plan.md:710,1279-1284` |
| Deployment triggers (D48 refinement) | D48 deploy model; dogfood via `deploy-dogfood.yml` | Dogfood stays manual (`workflow_dispatch` plus `dogfood` environment approval). From phase 9, a merge to `main` deploys AWS staging, runs the staging checks and auto-promotes the same digest to production only if all pass. Production exists only after the AWS ready gate. Tasks 9.4.5–9.4.6, 9.5.6, 9.6.3; TP-9.15–9.17 | High | commit `c9e474f`; `master-plan.md:655-663,892,1515-1522` |
| Token verifier key set (A6, Task 0.15) | Hand-written `_KeySet` cache and refetch (0.4.2) | One `PyJWKClient` per issuer behind `asyncio.to_thread`, with a guard for the once-a-minute unknown-`kid` refetch and 600 s age. An expired set whose refetch fails gives `503` (planned in 0.15). Cached keys stay in use within max age (orchestrator call, status 14:54) | Medium | commit `884b178`; `oidc.py:35,54,97-196`; `task-spec.md:252,692-730` |
| Key-set refresh after review (R2, R3) | 0.15.3: refreshes serialized by one `asyncio.Lock`; an unknown `kid` inside the interval is rejected (`401`) without a fetch | Cache hits take no lock or thread hop. Fetch attempts, including discovery, run one at a time, at most one per interval, and share their outcome. After a failed attempt, requests fail fast with `dependency_transient`, so an unknown `kid` within 60 s of a failed fetch gives `503` instead of `401` (a Tier 1 wire change) | Medium | commit `6e77af1`; `oidc.py:111-172`; `review.md` "Scoped re-review of 6e77af1" |
| MCP tool errors (R1) | Tool errors keep structured `CodedError` bodies (A6) | A non-`CodedError` tool exception is logged and answered with JSON-RPC `-32603` "Internal server error" in every era. Before the fix, initialize-era clients received the raw exception text with code 0 | Medium | commit `6e77af1`; `server.py:82-84`; `review.md` R1 |
| Auth hostname | CP-0.B answer: `auth.propertyfocus.treb.tech` | `auth-propertyfocus.treb.tech`, because Cloudflare's free Universal SSL covers only first-level subdomains. The Origin CA cert lists both hostnames. Only `PF_DOGFOOD_AUTH_HOST` changes, and the issuer URL changes with it | Medium | commit `b90d40c`; `master-plan.md:891`; `task-spec.md:61`; `docs/operations.md:10,20` |
| Edge settings and limits | A5 runbook included Bot Fight Mode. Initial rate-limit values were pending confirmation (`e7645f2` body) | Bot Fight Mode is off, because the free plan cannot exempt paths and it would challenge MCP and token clients. Confirmed limits: Cloudflare 20 req/10 s per IP on sign-in, registration and `/mcp`; per-account 120/min REST and 60/min MCP; AWS WAF 1000/5 min per IP, 300 on `/mcp`. The smoke names Bot Fight Mode on a challenge | Medium | commit `b90d40c`; `master-plan.md:724,867-868,894`; `task-spec.md:800` |
| Forwarded headers and origin cert install (R4, R5) | A5: Caddy trusts `CF-Connecting-IP`/`X-Forwarded-For` from Cloudflare; the workflow writes the origin pair | Caddy sends upstreams only `{client_ip}` and `{host}` in XFF/XFH, so client-forged values no longer reach uvicorn or Keycloak. The workflow writes `*.pem.new`, and `deploy.sh` installs the pair only after its preflight | Medium | commit `6e77af1`; `Caddyfile:12-14,32-33`; `deploy-dogfood.yml:107-118`; `deploy.sh:246-258` |
| Per-account limits (A5, Task 5.13) | No per-account limits | Phase 5 adds REST and MCP limits per tenant with `429` and `Retry-After` and Postgres-backed counters (plan recommendation) | Medium | `task-spec.md:2474,2978`; `architecture-decisions.md:1436-1460` |
| AWS API edge (A5, Task 9.8) | API Gateway (type unspecified), no WAF | Regional REST API, so AWS WAF can attach (9.1.5 verifies this first; CloudFront with WAF is the fallback). WAF managed rules, per-IP rate rules, stage and `/mcp` throttling. `edge.test.ts` fails on `AWS::ApiGatewayV2::Api` | Medium | `task-spec.md:3769,3804,3984`; `master-plan.md:813,870,1129-1135,1539` |

## Critical Decisions

- **A6 (user).** Prefer maintained libraries for security and protocol code. Hand-written code stays only for provider token profiles and the issuer/subject-to-tenant mapping (`architecture-decisions.md:1462`). Pins are `pyjwt[crypto]>=2.15.1,<3` and `mcp>=2.3,<2.4`, enforced by `integration/test_security_dependencies.py`.
- **A5 (user).** Cloudflare for dogfood, AWS WAF and throttling in phase 9, per-account limits in phase 5 (`architecture-decisions.md:1436`). Bot Fight Mode is off and the limits are user-confirmed (`master-plan.md:894`).
- **Auth host rename (user, 15:39).** `auth-propertyfocus.treb.tech` replaces the CP-0.B answer (`master-plan.md:891`).
- **Deployment triggers (user, 14:18).** Dogfood deploys are manual. AWS uses merge → staging → checks → automatic promotion. Status 14:22 records the auto-promotion "as written; awaiting user confirmation". The plan states it as a user decision (`master-plan.md:892`).
- **Tier 1 MCP wire differences for the user's review** (`master-plan.md:225-232`; `review.md` round 2; `gates/foundation-ready.md:142`):
  - Revision 2024-11-05 is served (bearer still required); it was `-32022` before.
  - A 2026-07-28 request whose `_meta` lacks the protocol version or client capabilities gets `-32602`, not `-32020`. TP-0.33 is reworded to match.
  - The `401` challenge uses the SDK format. `resource_metadata` names the path-inserted `/.well-known/oauth-protected-resource/mcp`, and both URL forms serve the same document.
  - `2027-01-01` still gets `400`/`-32022`, but `data.supported` is the SDK's list. The review notes that `supportedVersions` lists only 2026-07-28 and that era routing is header-only (an SDK TODO).
  - An unexpected tool failure gives `-32603` "Internal server error" in every era (R1 fix).
  - An unknown-`kid` token within 60 s of a failed key-set fetch gives `503`, not `401` (R2 fix).
  - The `Origin` check is now the SDK's transport security, with explicit `PF_MCP_ALLOWED_HOSTS`/`PF_MCP_ALLOWED_ORIGINS` (`server.py:279`).
- **Dependency check.** An OSV batch query on the locked versions (mcp 2.3.0, mcp-types 2.3.0, sse-starlette 3.6.0, python-multipart 0.0.33, jsonschema 4.26.0, referencing 0.37.0, rpds-py 2026.9.1, attrs 26.1.0, pyjwt 2.15.1, starlette 1.10.0, uvicorn 0.54.0, httpx2 2.16.0) found no known vulnerabilities (`review.md`, O3).
- **R5 resolved by the orchestrator under A5** ("logs and later limits see the real client IP"). It was not escalated to the user.

## Risks and Concerns

- **Slow fetch lets waiters fetch (S1).** An attempt lasting 60 s or more (slow DNS or a trickling response) lets queued waiters start their own fetches. The probe saw 10 fetches (`oidc.py:158-172`).
- **Malformed JWKS empties the trusted set (S2).** A non-object refetch, one with no usable keys, or a client-construction error replaces the trusted set with an empty one. Valid tokens then get `401` for up to 60 s (`oidc.py:186-196`). Separately, an issuer that publishes an empty key set bypasses the unknown-`kid` refetch limit (status 15:05; `review.md` "Known").
- **Fail-fast on real connection errors is unit-untested (S3).** The "issuer down" fixture raises `CodedError`, not `PyJWKClientConnectionError` (`test_auth.py:106-112`).
- **Docker's nftables backend is not handled (O1).** `firewall.sh` writes iptables `DOCKER-USER` and `INPUT` rules (`firewall.sh:77-79,116,141`). On a host where Docker uses nftables, Docker-published 443 may bypass these rules. The origin would then be reachable around Cloudflare.
- **Smoke probes IPv4 only (O2).** The direct-origin smoke probe checks IPv4 port 443 only (`smoke_dogfood.py`), so an open IPv6 path would go unnoticed.
- **`X-Forwarded-Proto` passes through.** Caddy passes Cloudflare's `X-Forwarded-Proto` through (`Caddyfile:12`), and the app services trust every forwarder (`FORWARDED_ALLOW_IPS: "*"`, `infrastructure/dogfood/compose.yaml:189,209`). Both seats judged this acceptable, and pinning `https` is optional hardening.
- **Unreviewed Tier 1 changes.** The behaviors above changed without the user's sign-off. They hold for 2025-03-26 through 2026-07-28 clients, and the release depends on that review (0.12.7).
- **Unconfirmed auto-promotion.** The staging-gated AWS auto-promotion has no human approval step and lacks explicit user confirmation (status 14:22). It is enforced by `test_deploy_workflows.py` (`master-plan.md:1519`).

## Learnings

- **A library moves the risk to the glue.** Every round-2 medium finding sat in our code around a library. R2/R3 came from a lock around the synchronous `PyJWKClient`, R1 from the SDK putting a raw exception on the wire, and R4 from the cert install order. Adopting a library still needs adversarial review of the adapter seam, including concurrency, error mapping and partial failure.
- **Check vendor plan limits before naming infrastructure.** The CP-0.B host name lasted about six hours. Universal SSL coverage and the fact that Bot Fight Mode cannot be scoped to paths are both documented limits. Checking them during Task 0.17 planning would have saved a rename pass across plan, docs and gate.
- **Research before deciding, register before code.** The user decided after a research pass (14:32–14:40), and A5/A6 reached the register and tasks first (`febafea`). Sequencing the hostname change after the tester (status 15:39–15:49) avoided gate-file conflicts.

## What Went Well

Round 2 stayed inside one review cycle: 0 critical or high findings, and all eight R findings closed in one pass (`6e77af1`) with real Caddy and outage probes. The new dependency set came back clean from OSV.

# Open Work (both rounds)

Closed since round 1:
- CP-0.B was recorded and N6 docs fixed (`2c65638`).
- Plan follow-ups landed in `dcd06fd`: the frozen `rls.sql` references, 9.2.1 naming, P49 cleanup in 5.10.6–7, Cognito sign-out in 9.7.7, the CP-9.D SSRF executor, P46 into 5.5.6 and P43 into 2.2.8.
- Gate evidence was refreshed (`351b6e5`, `a8035e9`).

## Action Items

- [ ] Run 0.12.1: clean-clone bootstrap on macOS arm64, plus the CI Linux x86_64 log (`gates/foundation-ready.md:35`).
- [ ] Run 0.12.3 after the user approves a push: record the path-aware CI run (`foundation-ready.md:146`).
- [ ] Run 0.12.8 with approval. Apply the Cloudflare runbook in `docs/operations.md`: proxied records, Full (strict), managed ruleset, rate rule at 20/10 s, Bot Fight Mode off. Then run `firewall.sh` and record the `deploy.sh` edge preflight and the forged-`CF-Connecting-IP` log check (`foundation-ready.md:214`).
- [ ] Run 0.12.5 with approval: `make smoke-dogfood` through Cloudflare against `propertyfocus.treb.tech` / `auth-propertyfocus.treb.tech`. This needs Mailgun and the `DOGFOOD_ORIGIN_CERT`/`DOGFOOD_ORIGIN_KEY` secrets (`foundation-ready.md:182`).
- [ ] The user reviews the Tier 1 MCP wire and OAuth discovery differences listed under Round 2 Critical Decisions. Record the review in `foundation-ready.md:142` and `status.md` (`adapters/mcp/server.py`, `adapters/auth/oidc.py`, `Caddyfile`).
- [ ] The user confirms or amends automatic AWS production promotion with no human approval step (status 14:22; `master-plan.md:663,892,1519`).
- [ ] S1: bound fetch-attempt duration, or stop queued waiters from starting their own fetch, when an attempt exceeds the interval (`oidc.py:158-172`).
- [ ] S2: keep the last trusted key set when a refetch returns a malformed or empty JWKS (`oidc.py:186-196`). Also cover the empty-set bypass of the unknown-`kid` limit.
- [ ] S3: make the "issuer down" fixture raise `PyJWKClientConnectionError` so the real fail-fast path is unit-tested (`test_auth.py:106-112`).
- [ ] O1: make `firewall.sh` handle Docker's nftables backend, or have the `deploy.sh` preflight fail on it, before 0.12.8 runs on the host (`firewall.sh:77-79,116,141`). Extend the smoke's direct-origin probe to IPv6 (O2).
- [ ] XFP hardening: add `header_up X-Forwarded-Proto https` to the forwarded snippet (`Caddyfile:12,32-33`).
- [ ] Widen or relativize the 1.0 s timing bound in `unit/test_redaction.py:126` (the N6 docs part is done).
- [ ] Confirm provenance of dev dependency `httpx2==2.16.0`. OSV shows no known vulnerabilities, but the source repository is still unverified (`review.md`, Warnings; `gates/foundation-ready/pkce-smoke.py:33`).

<!-- DRIFT-SUMMARY v1.0
{ "rubricVersion": "1.0", "drift": { "high": 5, "medium": 17, "low": 2 }, "actionItems": 13 }
-->
