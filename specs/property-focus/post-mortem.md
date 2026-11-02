# Post-mortem: property-focus — Phase 0 (Foundation)

Branch `sdlc/property-focus-p0` (worktree `property-focus-worktrees/property-focus-p0`), base `origin/main` at `b6a6a82`, HEAD `71694cd`. Diff: 151 files, +35,304/−1 (includes the plan, decision register and task spec).

**Scope.** This retro covers phase 0 only. The user chose one PR per phase (status.md, 02:17), so phases 1–9 (tasks 1.x–9.x, still unticked in `task-spec.md`) are planned future work, not drift. Phase 0 tasks are 0.1–0.14 (0.13 and 0.14 were added mid-phase).

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

## Action Items

- [ ] Run 0.12.1: clean-clone bootstrap on macOS arm64 and save `gates/foundation-ready/bootstrap-macos-arm64.log` (`foundation-ready.md`, 0.12.1 section).
- [ ] Run 0.12.3 after the user approves a push: record the backend PR run and a docs-only PR run, and link the CI Linux bootstrap job (`foundation-ready.md`, 0.12.3 section).
- [ ] Run 0.12.5 after approval: deploy dogfood to `propertyfocus.treb.tech` / `auth.propertyfocus.treb.tech` with Mailgun credentials set as deployment secrets, then record `make smoke-dogfood` (`task-spec.md:701`).
- [ ] Record the CP-0.B answer in `gates/foundation-ready.md:140` and `docs/operations.md`, and refresh the gate's `make verify` evidence at HEAD (recorded at `3eae973`).
- [ ] Complete the user's personal Tier 1 review of the MCP wire and OAuth discovery surface (`adapters/mcp/server.py`, `infrastructure/dogfood/Caddyfile`) before release.
- [ ] P49: add a task, in phase 5 before self-sign-up opens to other users, for automatic cleanup of unused anonymous registrations and/or registration rate-limiting at Caddy (`realm-property-focus.json:524-533`, `docs/operations.md:135`).
- [ ] Phase 9: add a sub-task for Cognito sign-out (`/logout` with `client_id` and `logout_uri`, `PF_WEB_OIDC_END_SESSION_URL`) next to 9.7.x (`web-app.md:67`, `session.ts:264`).
- [ ] CP-9.D: when revisiting CIMD, include the SSRF executor added in `bd1eae5` (`realm-property-focus.json:607-622`) in the redirect-rule analysis (`task-spec.md:3543`).
- [ ] Update tasks 3.4, 4.2, 4.4, 4.5 to add RLS policies in their own migrations instead of modifying `rls.sql` (`task-spec.md:1419,1997,2099,2148`; `unit/test_migrations.py`).
- [ ] Align Task 9.2.1 and `master-plan.md:1806` with the per-launch idempotency key (`runtime/launcher.py:187`).
- [ ] Fix N6 wording in `docs/system/modules/telemetry.md:47`, and widen or relativize the timing bound in `unit/test_redaction.py:121`.
- [ ] Carry review observations P46 (API 401 re-sign-in loop) into phase 1's first query-issuing page, and P43 (`attempt_count` never resets on requeue) into the first requeue use case (`review.md`, Observations).
- [ ] Confirm provenance of dev dependency `httpx2==2.16.0` before merge (`review.md`, Warnings).

## What Went Well

Every scope change mid-phase (A4, Tasks 0.13/0.14, option C, P47–P49) reached the register or task spec before or with its code. The critical sign-out race (P1) was reproduced in jsdom, and its regression test (`frontend/tests/sign-out.test.tsx`) was handed to the fixer, who made it pass.

<!-- DRIFT-SUMMARY v1.0
{ "rubricVersion": "1.0", "drift": { "high": 2, "medium": 9, "low": 2 }, "actionItems": 13 }
-->
