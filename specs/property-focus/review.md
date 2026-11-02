# Review: property-focus — Phase 0 (Foundation)

Panel: A=claude/opus, B=claude/opus (config: review.claude_models [opus, opus]; user chose Opus for every seat)
Frozen diff: b6a6a82...HEAD on sdlc/property-focus-p0 (144 files, +33,576/−1)
Fan-out: per seat — backend passes 1–2, backend passes 3–4, frontend (all passes), infrastructure + CI (all passes)

## Initial mechanical checks

Check: make be-check → pass
Check: make be-unit → pass
Check: make be-int → pass
Check: make be-replay → pass
Check: make check-generated → pass
Check: make fe → pass
Check: make infra → pass
Check: pnpm --filter @property-focus/infra build → pass
Check: pnpm lint:md → pass

## Contract tier

Overall: **Tier 1** (most restrictive supported). Seat A (backend 1–2): the MCP endpoint (`POST /mcp`, dual-era wire behavior), `/.well-known/oauth-protected-resource` and the 401 `WWW-Authenticate resource_metadata` challenge are consumed by third-party MCP clients acting for self-registered account holders (A1, A3). Seat B (backend 1–2) rated Tier 3 (owner's own clients, greenfield). All other surfaces Tier 3.
Tier 1/2 gate: OPEN — requires the user's recorded direction for human review before PR progression.

## Findings (round 1)

Severity / origin / class / seats. "X" = raised independently by both seats (crossLens).

### Selected for Fix Once
- P1 critical, introduced, logic — `frontend/src/auth/session.ts:219-226`, `router.tsx:37-43`: signOut clears tokens and notifies subscribers before navigating to the end-session endpoint; SignInBoundary starts a sign-in whose authorize navigation lands last, so the issuer's SSO session signs the user back in (B; jsdom repro run). Merged with A's sign-out-failure finding (P2).
- P2 medium, introduced, logic — `session.ts:214-221`: signOut throws when discovery lacks `end_session_endpoint` (tokens already cleared, rejection discarded) → silent re-sign-in; provider-neutral claim in web-app.md false for Cognito (A). Escalated (same user-visible defect as P1).
- P3 medium, introduced, logic, X — `frontend/src/api/baseApi.ts`: non-JSON error bodies (PARSING_ERROR) lose HTTP status; non-JSON 401 never ends the session (both seats ran repros). Escalated.
- P4 medium, introduced, logic — no tests for router/SignInBoundary/SignInCallback/baseApi (B). Escalated.
- P5 low, introduced, X — health routes lack `operation_id` → generated hooks `useLiveHealthLiveGetQuery` etc.; plus `result_refs: list[Any]` → `any[]` (A,B; Concepts and vocabulary / Red flag — Vague Name). Selected (vocabulary/red flag).
- P6 medium, introduced, logic, X — runtime engine recipe and URL↔password-secret pairing repeated in 5 places (`cli.py`, `run_task.py`, `rest/app.py`, `mcp/server.py`, `migrations/env.py`) (Red flag — Repetition / Information Leakage). Red-flag finding.
- P7 medium, introduced, logic, X — `playwright` runtime dependency unused until phase 2; ships in every image. Escalated.
- P8 medium, introduced, logic, X — AGENTS.md/architecture say application reaches infrastructure only through ports; `workflows.py`/`context.py` import `adapters.db`/`adapters.telemetry`; checker enforces only rest/mcp. Escalated (fix: make docs and checker match the code).
- P9 medium, introduced, logic, X — lease exhaustion (max_attempts → internal_defect), past-deadline (timeout) and stranded-QUEUED repair paths untested. Escalated.
- P10 medium, introduced, logic — telemetry redaction: structured-key list and free-text `key=value` list disagree (`code_verifier` attribute kept; `ssn`/`iban`/`credit_card`/`net_worth` in text kept) (B; repro run; Red flag — Repetition). Red-flag finding.
- P11 medium, introduced, logic — workflow-run read use case lives in the REST route; MCP parity test re-implements it, so parity is vacuous (B; Red flag — Repetition). Red-flag finding. Includes P20 (ExecutionContext→scope).
- P12 medium, introduced, logic — vocabulary: account vs tenant and principal vs user name the same concepts (B; Concepts and vocabulary). Escalated vocabulary finding.
- P13 medium, introduced, logic — vocabulary: `duplicate` means three things; FAILED/WAITING_FOR_USER runs report "duplicate" (B; Concepts and vocabulary). Escalated vocabulary finding.
- P14 medium, introduced, logic — `rls.sql` lives in the adapter package but is executed by released-at-merge migration 0001; an edit silently rewrites it (B; Information Leakage). Red-flag finding.
- P15 medium, introduced (latent), logic, X — Step Functions execution name = `run.id.fencing_token`; a failure before lease acquisition makes every relaunch hit ExecutionAlreadyExists and report success → run stuck until deadline. Escalated.
- P16 medium, introduced, mechanical — `create_database_engine` lacks `hide_parameters=True`; SQL parameters (emails) appear in exception logs (A; repro run). Gating-mechanical.
- P17 low, introduced, mechanical — uvicorn access log enabled (logs query strings, MCP too) `cli.py:105` (A). Gating-mechanical.
- P18 low, introduced, mechanical — `dispose_failure`/`FailureDisposition` vague name (B; Concepts and vocabulary). Gating-mechanical.
- P19 low, introduced, X — REST Literals not pinned to model state/tier constants (Red flag — Repetition; A mechanical, B logic → logic). Red-flag finding.
- P20 low, introduced, logic, X — `Account`/`AccountScope`/`ExecutionContext` repeat the same ID pair; add a scope property (Red flag — Repetition). Red-flag finding (fixed with P11/P12).
- P21 low, introduced, logic, X — `Lease.tenant_id` duplicates `Lease.scope` (Red flag — Repetition). Red-flag finding.
- P22 low, introduced, mechanical — dead guard `check_transition("QUEUED","RUNNING")` `workflows.py:596-598` (A). Gating-mechanical.
- P23 low, introduced, logic — `deadline_at` nullable though always written; `_NO_DEADLINE` handles an impossible NULL (B; Avoidable Error Case). Red-flag finding.
- P24 low, introduced, logic — repair query has no supporting index (B). Escalated (0001 unreleased; cheap now).
- P25 low, introduced, logic — concurrent `admit_run` raises raw IntegrityError → non-retryable internal_defect (A). Escalated.
- P26 low, introduced, logic — DB failure in `authenticate` → MCP plain 500 / REST non-retryable 500; should be `dependency_transient` 503 (B). Escalated.
- P27 low, introduced, logic — retries actually bounded by the 5-minute repair cadence; docs claim 2s/4s/8s (B). Escalated (docs).
- P28 low, introduced, logic — health probes logged at INFO every 5 s (A). Escalated.
- P29 low, introduced, logic — MCP rejects JSON-RPC batches for 2025-03-26 (A). Escalated (document as unsupported).
- P30 medium, introduced, logic — SSRF: anonymous DCR validates only `redirectUris`; Keycloak fetches `sector_identifier_uri`/`request_uri` and accepts `backchannel_logout_uri` to any host incl. metadata IP (A; repro run against local Keycloak). Escalated (security).
- P31 medium, introduced, logic — realm has no password policy; 1-char password accepted on public self-sign-up (A; repro run). Escalated (security).
- P32 medium, introduced, logic, X — Caddyfile and collector config single-file bind mounts never refresh on redeploy (both seats repro'd locally).
- P33 medium, introduced, logic, X — env file parsed three ways; values with `$` diverge (SMTP password written as `$$`; operator compose gets different secrets) (both seats repro'd).
- P34 medium, introduced, logic, X — new web build goes live before deploy.sh runs; rollback can't restore web; `--delete` breaks open sessions; rollback leaves old compose for Keycloak.
- P35 medium, introduced, logic, X — deploy workflow writes the host SSH key before running tag-pinned actions/installs; checkout lacks persist-credentials:false; setup-uv unpinned. Escalated (security).
- P36 medium, introduced, mechanical — planning IDs (CP-0.B, Task 5.10) in operator-facing messages and docs (B; Concepts and vocabulary). Escalated vocabulary.
- P37 low, introduced, logic — Caddy SPA fallback answers `/.well-known/*`, `/authorize`, `/register` with 200 HTML (B). Escalated.
- P38 low, introduced, X — CI never type-checks CDK, never builds images or validates dogfood compose (A mechanical, B logic → logic). Escalated.
- P39 low, introduced, mechanical, X — `.env.example` references nonexistent `make test-live`. Gating-mechanical.
- P40 low, introduced, logic — CI filter: keycloak/docker/dogfood changes select `cdk` (reads none) and skip `integration` (client-neutrality scan reads dogfood) (A mechanical, B logic → logic). Escalated.
- P41 low, introduced, logic — dogfood containers lack no-new-privileges/cap_drop/limits (A). Escalated (defense in depth with P30).
- P42 low, introduced, logic — CDK account guard runs only at synth; a synthesized template deploys to any account (A). Escalated.

### Observations (not selected)
- P43 low, introduced (latent), logic — FAILED/PARTIAL→QUEUED transitions exist but `attempt_count` never resets (B). No requeue use case yet; revisit when one lands.
- P44 low, introduced, logic — `PF_DATA_MODE` parsed but unread (A). Plan defines it; keep.
- P45 low, introduced, logic — `smoke_dogfood.py` imports test helpers (A). Accepted: reuse is intentional; revisit if it breaks.
- P46 low, introduced (latent), logic — API 401 → re-sign-in loop if API rejects every fresh token (A). No query-issuing page in phase 0; carried to phase 1.

## Needs user decision
- P47 `backend/src/property_focus/application/ports.py:229` — Does `TaskInput.task_version` mean the task handler's logic version or the application build version?
- P48 `infrastructure/dogfood/Caddyfile` — Should 2025-03-26 MCP clients be able to sign in on dogfood (needs `/.well-known/oauth-authorization-server` on the app host), or is 2025-03-26 support protocol-level only?
- P49 `infrastructure/keycloak/realm-property-focus.json:524-533` — The anonymous-registration client cap (100, counting built-ins) can be exhausted by unauthenticated spam, blocking new MCP clients. Rate-limit registration at Caddy, auto-expire unused registrations, or accept?

## Warnings
- Repro not confirmed: P1 frontend/src/auth/session.ts:219 — B — navigation order confirmed in jsdom; real-browser abort not run.
- Repro not confirmed: P15 backend/src/property_focus/runtime/launcher.py:174 — A,B — needs Step Functions emulator.
- Repro not confirmed: P34/P35 — live host / supply-chain condition.
- Supply chain: dev dependency `httpx2==2.16.0` — PyPI project URLs point to github.com/pydantic/httpx2 (22 releases); GitHub repo not reachable from this session.

## Panel reproductions
- P1: reviewer-right — `npx vitest run tests/sign-out.test.tsx` (copy of frontend/) failed 4/4: navigations `["/realms/synthetic/logout","/realms/synthetic/authorize"]`, expected only logout. Regression test passed to the Fix Once coder.

## Fix Once (cycle 1)
Fix commits: c734f96 (frontend: P1–P4), 6c2fdf5 (backend: P5–P29), bd1eae5 (infrastructure: P30–P42).
- Mechanical fixes machine-verified: P16 (hide_parameters in engine + migration env), P17 (access_log=False), P18 (no dispose_failure/FailureDisposition left), P22 (guard removed), P36 (no CP-0/Task 5.10 in infra/docs), P39 (.env.example test-live reference gone).
- P13 partially declined: `duplicate` ERROR_CODE kept — declined: Contract — the plan's stable-code table (master-plan.md:1817) and task spec define it; now documented with one meaning (idempotent replay). Both re-review seats accept the decline.

## Scoped re-review (round 2)
Seat A: 37/38 in-scope logic findings resolved; P10 regressed (N1). Seat B: all in-scope findings resolved.
- N1 high, introduced, logic — backend/src/property_focus/adapters/telemetry.py:114,149: the P10 single-rule free-text redaction consumes any `key:value` span, so a sensitive pair after a URL scheme or a `prefix:` is never examined (`https://x/cb?token=t1`, `failed: password=hunter2`, `postgresql://…?password=x` now leak; they were redacted at 616d15e). Seat A ran old vs new side by side; orchestrator re-ran and confirmed. UNRESOLVED — gating at the two-round cap.
- N2 low, introduced, logic — run_task.py:153-155: runtime_from_settings builds the secret provider twice (extra AWS clients at cold start). Observation (surfaced at two-round cap).
- N3 low, introduced, logic — frontend/src/app/router.tsx:106: "Sign in again" after a failed sign-out drops a sign-in rejection silently. Observation (surfaced at two-round cap).
- Seat A gap (not verified): browser back-forward cache could restore "Signing out…" after leaving for the issuer's logout page.

## Final checks

Check: make be-check → pass
Check: make be-unit → pass
Check: make be-int → pass
Check: make be-replay → pass
Check: make check-generated → pass
Check: make fe → pass
Check: make infra → pass
Check: pnpm lint:md → pass
Final checks: pass (mechanical); review blocked by unresolved high finding N1 at the two-round hard cap.

## User direction (2026-11-02 09:03)
- Two-round cap: user authorized one additional targeted fix pass for N1 (plus N2, N3) with a scoped re-review of that change only.
- Tier 1 gate (MCP wire + OAuth discovery surface): the user will perform the human review personally before release. Recorded; gate satisfied for PR progression (review scheduled with the user, not replaced).
- P47 resolved by user: `TaskInput.task_version` holds the application build version → rename to `application_version` (bump the TaskInput schema version; no external consumers yet).
- P48 resolved by user: yes — 2025-03-26 MCP clients should sign in on dogfood; Caddy routes the app host's `/.well-known/oauth-authorization-server` to the realm metadata (added to the extra fix pass).
- P49 resolved by user: accept the anonymous-registration client-cap risk for dogfood; track it and add automatic cleanup of unused registrations before other users can sign up (carry to phase 5 / wrap-up action items).

## Scoped review of extra pass (c188e47)
N1 leaks, N2, N3, P47, P48: resolved (reviewer probed 44 shapes; Caddy routing run in docker; no open proxy/host-header injection).
- N4 high, introduced (6c2fdf5/c188e47), logic — backend/src/property_focus/adapters/telemetry.py:113,119-122,160: redaction regexes do quadratic work on crafted input; Starlette OTel middleware records client-controlled User-Agent/path/query and the redacting span processor runs synchronously on the request path. Reviewer: unauthenticated GET with `User-Agent: "a-"*7000` blocked the event loop 3.1 s. Orchestrator confirmed `redact_text("a-"*7000)` = 2.4 s. UNRESOLVED.
- N5 low, introduced (6c2fdf5), logic — is_sensitive_key misses `passWord`/`PassWord` after the camelCase split. Observation.
- Pre-existing notes: quoted values with spaces partly redacted; `_password=x` not redacted; percent-encoded nested `token` passes; SignInFailure retry drops a second rejection.
- User direction (2026-11-02 13:38): one more targeted fix pass for N4 (+N5) with a scoped check.

## Scoped check of 71694cd (N4, N5)
- N4 resolved: linear on ~70 adversarial shapes (1 MB/250 KB ratio 2.2–6.2; worst 0.87 s/MB); full middleware path ≤1.03 s/MB; h11 caps headers at 16 KB so real requests cost ≤~16 ms. Orchestrator timed 1 MB hostile lines at 0.07–0.24 s.
- N5 resolved; no realistic false positives across 9,363 repo identifiers.
- Leak corpus (113 strings) vs c188e47: 8 newly redacted, 1 intentionally unredacted (`x-eyJ…` JWT glued after a hyphen; documented; JWTs still caught after separators and as sensitive-key values) — accepted.
- N6 low, introduced, mechanical — docs/system/modules/telemetry.md:47 says "well under a second"; measured worst ≈0.87 s/MB. Observation (surfaced at two-round cap).
- Observation: test_redaction_takes_linear_time_on_long_crafted_text uses a 1.0 s bound vs 0.30 s measured; could be flaky on a ~3× slower runner.

## Final checks (HEAD 71694cd)
Check: make verify (be-check, be-unit 405, be-int 172, be-replay, check-generated, fe 33, infra 15 + tsc + synth) → pass
Check: pnpm lint:md → pass
Final checks: pass

## Review complete
No unresolved critical/high or gating-mechanical findings. Tier 1 gate: user will review personally (recorded). Observations to present at the PR gate: P43–P46, N2-era observations resolved, N3/N5 resolved, N6, flaky-bound note, P49 accepted risk.
