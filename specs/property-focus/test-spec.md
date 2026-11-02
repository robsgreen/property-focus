# Test Spec: property-focus — Phase 0 (Foundation ready gate)

> **Source:** `specs/property-focus/tasks/test-plan.md` (Phase 0: TP-0.1–TP-0.37 and the gate line), `specs/property-focus/tasks/task-spec.md` (tasks 0.1–0.14)
> **Created:** 2026-11-02
> **Last Updated:** 2026-11-02 (Round 2 appended)

## Approach

Phase 0 scope only. Most TP-0.x behaviors were already covered by tests written during implementation (`backend/tests/{unit,integration,contract}`, `frontend/tests`, `infrastructure/cdk/test`) and by the gate evidence in `specs/property-focus/gates/foundation-ready/`. Each TP item is mapped below to the tests that prove it. New black-box tests were added only where a TP item had no adequate coverage: token rejection through both adapters, real Keycloak key rotation, client neutrality, REST `409` for idempotency conflicts, dynamic-registration gaps, the metadata-documents-off check, CI path selection and lockfile drift. Integration tests run against the local Compose services (Postgres, Keycloak, MiniStack, Mailpit, OTel). Live items (macOS bootstrap, recorded CI run, dogfood smoke) are recorded as pending and were not run. TP-0.35 and TP-0.36 are deferred by the user's 2026-11-02 option C decision.

## TP Coverage Map

| TP | Proven by | State |
|---|---|---|
| TP-0.1 | CI `bootstrap` job (needs a push); macOS arm64 log (manual); local Linux x86_64 fresh-clone bootstrap run (IT-6) | Linux local: pass. macOS + CI log: **pending — user action/approval required** |
| TP-0.2 | `unit/test_repo_checks.py::TestLayoutCheck` (runs `make check-layout`) | pass |
| TP-0.3 | `unit/test_repo_checks.py::TestBoundaryCheck` | pass |
| TP-0.4 | CT-2 | pass |
| TP-0.5 | IT-7 (each module built and tested alone) | pass |
| TP-0.6 | CT-1 (selection logic, deterministic); recorded CI run | Det: pass. Recorded run: **pending — push; user approval required** |
| TP-0.7 | `integration/test_tenancy.py::test_first_request_provisions_one_user_and_account_and_a_repeat_creates_nothing`, `::test_concurrent_first_requests_provision_one_user_and_account` | pass |
| TP-0.8 | `integration/test_tenancy.py::test_another_accounts_workflow_run_looks_exactly_like_a_missing_one`; `contract/test_mcp.py::test_parity_harness_compares_rest_and_mcp_results` (hidden == missing on MCP) | pass |
| TP-0.9 | CT-3; `unit/test_auth.py` | pass |
| TP-0.10 | `contract/test_mcp.py::test_unauthenticated_request_gets_a_challenge_naming_the_resource_metadata`, `::test_missing_or_malformed_credential_is_challenged` | pass |
| TP-0.11 | `contract/test_mcp.py::test_dynamically_registered_generic_client_signs_in_and_calls_mcp` | pass |
| TP-0.12 | CT-4 | pass |
| TP-0.13 | `integration/test_workflows.py::test_dogfood_process_and_lambda_adapter_commit_identical_results` | pass |
| TP-0.14 | `unit/test_runtime_manifests.py` (`test_flags_a_looping_worker_and_a_restarted_task`, `test_flags_crontab_entries_that_are_not_registered_run_task_kinds`, live-manifest cases) | pass |
| TP-0.15 | `integration/test_workflows.py::test_crash_between_commit_and_launch_is_launched_once_by_the_next_sweep`, `::test_crash_after_launch_before_acknowledge_keeps_one_result` | pass |
| TP-0.16 | `integration/test_workflows.py::test_duplicate_delivery_and_idempotent_admission`; CT-5 (REST `409`) | pass |
| TP-0.17 | `integration/test_workflows.py::test_expired_lease_is_recovered_and_the_stale_holder_is_rejected` | pass |
| TP-0.18 | `unit/test_failures.py` (unmapped code fails, flagged codes not retried, at most four attempts with backoff); `integration/test_workflows.py::test_failed_step_is_persisted_and_retried_through_the_policy` | pass |
| TP-0.19 | `integration/test_telemetry.py::test_seeded_secret_values_never_appear_in_logs_or_spans`, `::test_logs_are_json_lines_with_trace_and_correlation_ids` | pass |
| TP-0.20 | `make check-generated` inside `make verify` | pass |
| TP-0.21 | `integration/test_keycloak_realm.py::test_discovery_names_the_realm_issuer_and_registration_endpoint`; IT-5 (fresh volume, existing volume, restart persistence) | pass |
| TP-0.22 | Gate evidence `gates/foundation-ready/pkce-smoke.log` (manual log, Task 0.12.4) | pass (recorded) |
| TP-0.23 | IT-8 (`make compatibility-local`, no diff to `docs/compatibility.md`) | pass |
| TP-0.24 | `integration/test_keycloak_realm.py::test_self_registered_user_gets_tokens_only_after_verifying_email`, `::test_access_token_carries_api_audience_and_bearer_type` | pass |
| TP-0.25 | `integration/test_keycloak_realm.py::test_web_client_rejects_authorization_without_s256_pkce`, `::test_registered_client_requires_pkce_and_asks_for_consent`; IT-2 (registered client, `plain`) | pass |
| TP-0.26 | `integration/test_keycloak_realm.py` registration cases; IT-3 (no client created on rejection) | pass |
| TP-0.27 | `integration/test_keycloak_realm.py::test_realm_file_holds_no_literal_secret`, `::test_no_client_allows_password_or_implicit_grant_or_service_account` | pass |
| TP-0.28 | IT-1 (real Keycloak rotation); `unit/test_auth.py` rotation and rate-limit cases | pass |
| TP-0.29 | `integration/test_keycloak_realm.py::test_second_realm_apply_changes_nothing`, `::test_realm_check_detects_a_manual_change`, `::test_realm_apply_keeps_users_and_registered_clients` | pass |
| TP-0.30 | `make smoke-dogfood` | **pending — user action/approval required (CP-0.B, dogfood deployment)** |
| TP-0.31 | `contract/test_mcp.py::test_initialize_based_client_lists_and_calls_tools[2025-06-18/2025-11-25]`, `::test_transport_is_stateless_json`, `::test_initialize_answers_at_most_the_newest_initialize_based_revision` | pass |
| TP-0.32 | `contract/test_mcp.py::test_modern_client_gets_the_same_results_with_and_without_discovery`, `::test_parity_harness_compares_rest_and_mcp_results[2026-07-28]` | pass |
| TP-0.33 | `contract/test_mcp.py::test_modern_request_with_missing_or_mismatched_header_is_refused`, `::test_modern_tool_call_accepts_a_base64_encoded_tool_name` | pass |
| TP-0.34 | `contract/test_mcp.py::test_unsupported_protocol_version_is_refused_on_both_paths`, `::test_methods_2026_07_28_removed_or_not_served_are_not_found` | pass |
| TP-0.35 | — | **deferred** (user decision 2026-11-02, option C; re-evaluate at CP-9.D) |
| TP-0.36 | — | **deferred** (user decision 2026-11-02, option C; re-evaluate at CP-9.D) |
| TP-0.37 | IT-4; `contract/test_mcp.py::test_dynamically_registered_generic_client_signs_in_and_calls_mcp`; TP-0.26 cases | pass |

**Foundation gate line:** TP-0.1–0.34 and TP-0.37 are green locally except the live items TP-0.1 (macOS arm64 log, CI Linux log), TP-0.6 (recorded CI run) and TP-0.30 (dogfood smoke), which need user action or approval. TP-0.35/0.36 excluded (deferred). Gate stays open until those are recorded in `specs/property-focus/gates/foundation-ready.md`.

## Component Tests

- [x] **CT-1** — CI path selection: docs-only and specs-only pull requests run only the secret scan and markdown lint; a backend pull request runs backend, integration, replay, generation and bootstrap; root tooling selects everything; a move out of `backend/` still runs the backend jobs; pushes run everything (TP-0.6, deterministic part)
  - File: `backend/tests/integration/test_ci_workflow.py`
  - Modules exercised: `.github/workflows/ci.yml` `changes` script run by bash over a real git diff, job `if` conditions
- [x] **CT-2** — Lockfile drift: every CI check job installs with `make install` before its checks, and `make install` fails on a Python member, web app or root manifest change without its lock update (TP-0.4)
  - File: `backend/tests/integration/test_ci_workflow.py`
  - Modules exercised: `.github/workflows/ci.yml`, root `Makefile` `install`, `uv`, `pnpm`, committed manifests and lockfiles
- [x] **CT-3** — REST and MCP both reject expired, not-yet-valid, wrong-audience, wrong-issuer and ID (wrong `typ`) tokens, `alg: none`, HS256 signed with the issuer's public key, an unknown key and a forged known `kid`, with no account provisioned; an otherwise identical valid token is accepted by both (TP-0.9)
  - File: `backend/tests/contract/test_token_rejection.py`
  - Modules exercised: REST app, MCP app, `OidcVerifier` (`verifier_for`) on the local Keycloak key set, account provisioning, test database
- [x] **CT-4** — No client-specific behavior: challenge, protected-resource metadata, `tools/list` and REST answers are identical for different `User-Agent` values; adapters, auth, configuration and the realm file name no MCP client or user agent (TP-0.12)
  - File: `backend/tests/contract/test_client_neutrality.py`
  - Modules exercised: REST app, MCP app, verifier, Keycloak tokens; source tree
- [x] **CT-5** — REST maps a real idempotent admission conflict (same key, different input) to a `409` `idempotency_conflict` problem, and a duplicate to the same run (TP-0.16)
  - File: `backend/tests/contract/test_api.py`
  - Modules exercised: REST app error handling, `admit_run`, `UnitOfWork`, test database

## Integration Tests

- [x] **IT-1** — Keycloak signing key rotation: a token signed by a newly activated realm key validates after one key-set refetch, a second unknown key within the minute triggers no refetch, and once the key is removed its tokens fail after the next refetch (TP-0.28)
  - File: `backend/tests/integration/test_key_rotation.py`
  - Infrastructure: local Keycloak (admin API key providers), real JWKS endpoint
- [x] **IT-2** — A dynamically registered client's authorization request with PKCE method `plain` is rejected (TP-0.25)
  - File: `backend/tests/integration/test_keycloak_realm.py`
  - Infrastructure: local Keycloak
- [x] **IT-3** — Each rejected dynamic registration (unsafe redirect, confidential, beyond the client cap) leaves no client in the realm (TP-0.26)
  - File: `backend/tests/integration/test_keycloak_realm.py`
  - Infrastructure: local Keycloak
- [x] **IT-4** — Authorization-server metadata advertises `registration_endpoint` and no `client_id_metadata_document_supported`; a generic client sending the MCP URL as `resource` gets a token whose `aud` is exactly `property-focus-api` (TP-0.37)
  - File: `backend/tests/contract/test_mcp.py`
  - Infrastructure: local Keycloak, MCP app, test database
- [x] **IT-5** — Keycloak realm import on a fresh volume and on an existing one, and data persisting across a restart (TP-0.21). Scripted run, not a suite test: it needs a separate Compose project
  - File: scripted run (commands below), output summarized in this spec
  - Infrastructure: Docker Compose project `pf-tp-fresh` (fresh volume), local `property-focus` project
- [x] **IT-6** — Local Linux x86_64 clean bootstrap from a fresh clone on a fresh volume: frozen installs, services, migrate, seed (TP-0.1, local part)
  - File: scripted run
  - Infrastructure: fresh clone in scratch, Compose project `pf-tp-fresh`
- [x] **IT-7** — Each module built and tested alone: backend unit tests; web app lint/typecheck/test/build; CDK test/synth (TP-0.5)
  - File: commands below
  - Infrastructure: none beyond installs
- [x] **IT-8** — `make compatibility-local` regenerates `docs/compatibility.md` with no diff (TP-0.23)
  - File: command below
  - Infrastructure: MiniStack

## How to Run

```bash
# Prerequisites: Docker running, .env from .env.example
docker compose up -d --wait postgres keycloak ministack mailpit otel

# Component tests (contract + CI checks)
uv run pytest backend/tests/contract backend/tests/integration/test_ci_workflow.py -q

# Integration tests
uv run pytest backend/tests/integration -q

# Each module alone (IT-7)
uv run pytest backend/tests/unit -q
make fe
make infra

# Compatibility inventory (IT-8)
make compatibility-local && git diff --exit-code docs/compatibility.md

# All tests
make verify
```

## Results

- New tests committed in `9cdc843` on `sdlc/property-focus-p0`: 49 test cases (CT-1/CT-2: 25, CT-3: 10, CT-4: 4, CT-5: 1, IT-1: 1, IT-2/IT-3: 7, IT-4: 1).
- **IT-5 / IT-6 (scripted, 2026-11-02, Linux x86_64):** fresh clone of `sdlc/property-focus-p0` at `92b2fe2` into scratch; `corepack enable`, `pnpm install --frozen-lockfile`, `uv sync --all-packages --frozen`, `cp .env.example .env` (template values only) passed. With the shared `property-focus` stack stopped, `docker compose -p pf-tp-fresh up -d --wait postgres keycloak ministack mailpit otel` on a new volume: all healthy; Keycloak logged `Realm 'property-focus' imported`; discovery issuer `http://localhost:8080/realms/property-focus`, `registration_endpoint` present, no `client_id_metadata_document_supported`. `cli migrate` ran `0001`; `cli seed --dataset synthetic` reported 3 accounts. A synthetic user created and signed in (PKCE, `aud` `property-focus-api`); after `docker compose restart keycloak` the log read `Realm 'property-focus' already exists. Import skipped` and the user was still there. Fresh project removed with `down -v`; the shared stack restarted on its existing volume with `Import skipped` and the same issuer.
- **IT-7:** with `frontend/dist` and `cdk.out` removed, CDK test + synth passed (13 tests) without building the web app; web app lint, typecheck, test (12) and build passed; backend unit tests passed (333) alone.
- **IT-8:** `make compatibility-local` exit 0; `docs/compatibility.md` unchanged (pinned MiniStack digest, five operations pass, no unsupported operation).
- **`make verify`:** exit 0 — unit 333 passed; integration + contract 149 passed (was 100); `be-replay` skipped by design (no `backend/tests/replay` or golden manifest until Task 1.7); `check-generated` no diff; web 12; CDK 13 + synth.

## Validation

- [x] All component tests pass
- [x] All integration tests pass (or N/A if not supported)
- [x] All existing unit tests still pass (no regressions)
- [x] Build passes

---

## Round 2 (tasks 0.15–0.17)

> **Source:** `test-plan.md` TP-0.33, TP-0.34 (changed), TP-0.38–TP-0.50 (new) and the Foundation gate line; `task-spec.md` tasks 0.15, 0.16, 0.17 and sub-task 0.12.7
> **Date:** 2026-11-02 — code at `a1fa80a`; new tests `e8f8982`; gate evidence `a8035e9` (branch `sdlc/property-focus-p0`)

### Approach (Round 2)

Most behaviors were already covered by the tests written with the tasks (`unit/test_auth.py`, `unit/test_mcp_adapter.py`, `unit/test_dogfood_edge.py`, the rewritten `contract/test_mcp.py`). New black-box tests were added where a TP item lacked coverage at the adapter boundary against the real Keycloak realm: the unknown-`kid` flood and the `503`/`401` split through both REST and MCP (TP-0.38, TP-0.39), the dependency ranges and lock (TP-0.40), `invalid_input` tool errors in every era (TP-0.45), and a probe of the TP-0.33 "no `_meta` version" case. Live Cloudflare, host-firewall and dogfood items were not run.

### TP Coverage Map (Round 2)

| TP | Proven by | State |
|---|---|---|
| TP-0.33 | `contract/test_mcp.py::test_modern_request_with_missing_or_mismatched_header_is_refused` (5 cases, `-32020`), `::test_modern_tool_call_accepts_a_base64_encoded_tool_name`, `::test_modern_request_without_its_meta_envelope_has_invalid_params` (no client capabilities, no `_meta`, **new:** `_meta` without protocol version → `-32602`) | pass, **with a deviation**: a 2026-07-28 header with no `_meta` version gets `-32602` from the SDK, not the `-32020` the TP row lists (see Decisions) |
| TP-0.34 | `contract/test_mcp.py::test_unsupported_protocol_version_is_refused_with_the_supported_versions` (`2027-01-01`: `400`, `-32022`, `data.supported`, `data.requested`), `::test_methods_2026_07_28_removed_or_not_served_are_not_found[ping/logging/setLevel/resources/list]`; `2024-11-05` moved to TP-0.42 | pass |
| TP-0.38 | **CT-6** (100 unknown `kid` tokens through REST and MCP: all `401`, ≤1 fetch; mutation with the refetch limit disabled fails it); `unit/test_auth.py::TestKeySet::test_many_unknown_kids_within_a_minute_cause_at_most_one_fetch`, `::test_unknown_kid_refetches_at_most_once_a_minute`, `::test_rotated_in_key_validates_after_one_refetch_and_a_removed_key_fails`; `integration/test_key_rotation.py` (real Keycloak rotation and removal) | pass |
| TP-0.39 | **CT-7** (key set unreachable, discovery unreachable, discovery naming another issuer → `503` on REST and MCP, no challenge), **CT-8** (provider up: valid token served, tampered `401` on both); `contract/test_mcp.py::test_issuer_outage_gives_503_not_a_challenge`; `unit/test_auth.py::TestAuthenticate::test_rest_answers_a_retryable_503_problem`, `::test_mcp_answers_503_and_still_challenges_an_invalid_token` | pass |
| TP-0.40 | **CT-9** (`integration/test_security_dependencies.py`: ranges admit/refuse the right versions, `crypto` extra, lock inside range — `mcp` 2.3.0, `pyjwt` 2.15.1) | pass |
| TP-0.41 | `contract/test_mcp.py::test_unauthenticated_request_gets_a_challenge_naming_the_resource_metadata` (`401` naming `resource_metadata`; the challenge URL, `/.well-known/oauth-protected-resource/mcp` and the root form all name the Keycloak issuer and are equal) | pass |
| TP-0.42 | `contract/test_mcp.py::test_revision_2024_11_05_is_served_only_with_a_valid_token` | pass |
| TP-0.43 | `contract/test_mcp.py::test_keycloak_token_without_the_api_audience_is_rejected` (real Keycloak token lacking the audience → `401`, no tenant); `unit/test_mcp_adapter.py::TestConfiguration::test_sdk_resource_check_is_off_because_oidc_checks_the_audience` | pass |
| TP-0.44 | `contract/test_mcp.py::test_request_from_a_disallowed_host_or_origin_is_rejected_and_runs_no_tool[foreign-origin/foreign-host]`; `unit/test_mcp_adapter.py::TestConfiguration::test_transport_security_allows_exactly_the_configured_hosts_and_origins` | pass |
| TP-0.45 | **CT-10** (`invalid_input` `{"code","detail"}` in 2025-06-18, 2025-11-25, 2026-07-28); `contract/test_mcp.py::test_parity_harness_compares_rest_and_mcp_results[2025-11-25/2026-07-28]` (`CodedError` `not_found` parity); `unit/test_mcp_adapter.py::TestTools::test_failed_call_is_a_structured_tool_error` | pass |
| TP-0.46 | `make smoke-dogfood` through Cloudflare; deterministic parts in `unit/test_dogfood_edge.py` (`cf-ray` required, Bot Fight Mode named, direct origin connection must fail) | Det parts: pass. Live: **pending — user action/approval required** (Cloudflare setup, dogfood deploy and smoke) |
| TP-0.47 | `deploy.sh` preflight on the host; `unit/test_dogfood_edge.py::test_deploy_passes_exactly_the_committed_ranges_to_caddy` (det part) | **pending — user action/approval required** (firewall and deploy on the real host) |
| TP-0.48 | `unit/test_dogfood_edge.py` (18 cases: origin cert on both sites, no ACME, `trusted_proxies` only from `cloudflare-ips.txt`, `CF-Connecting-IP` first, valid v4/v6 ranges, only Caddy publishes ports over TCP, origin dir read-only) | pass |
| TP-0.49 | Caddy access log through Cloudflare and a forged `CF-Connecting-IP` on the host | **pending — user action/approval required** (real host) |
| TP-0.50 | Cloudflare dashboard against the `docs/operations.md` runbook | **pending — user action/approval required** (live Cloudflare dashboard) |

**Foundation gate line (Round 2):** TP-0.33–0.34, TP-0.38–0.45 and TP-0.48 are green locally on the SDK adapter with `PyJWKClient`; TP-0.31, TP-0.32 and TP-0.37 also rerun green on the SDK adapter (`test_mcp.py` both eras, parity, dynamic registration). Still open: TP-0.46, 0.47, 0.49, 0.50 (live Cloudflare/host/dogfood), plus the Round 1 live items TP-0.1, TP-0.6 and TP-0.30, and the user's Tier 1 review of the SDK adapter (0.12.7). TP-0.35/0.36 excluded (deferred). Recorded in `specs/property-focus/gates/foundation-ready.md` (sections 0.12.7 and 0.12.8). Gate stays open.

### Component Tests (Round 2)

- [x] **CT-6** — A flood of 100 tokens naming unknown `kid` values, alternating REST and MCP, is refused with `401` with at most one key-set fetch, and realm tokens keep working (TP-0.38)
  - File: `backend/tests/contract/test_issuer_failures.py::test_a_flood_of_unknown_key_ids_is_refused_with_at_most_one_key_set_fetch`
  - Modules exercised: REST app, MCP app, `verifier_for` with a counting `PyJWKClient` on the real Keycloak JWKS, `authenticate`, test database
- [x] **CT-7** — Unreachable key set, unreachable discovery, and discovery naming another issuer each give `503` (no challenge) on REST and MCP (TP-0.39)
  - File: `backend/tests/contract/test_issuer_failures.py::test_provider_that_cannot_vouch_for_a_token_gives_503_on_rest_and_mcp` (3 cases)
  - Modules exercised: REST app, MCP app (SDK transport, `SharedTokenVerifier`, `_RequestScope`), `verifier_for`, `fetch_json`, Keycloak token
- [x] **CT-8** — With the provider up, a valid token is served and a tampered one gets `401` on both adapters (TP-0.39)
  - File: `backend/tests/contract/test_issuer_failures.py::test_with_the_provider_up_a_valid_token_is_served_and_an_invalid_one_gets_401`
  - Modules exercised: REST app, MCP app, verifier, Keycloak
- [x] **CT-9** — `pyjwt` and `mcp` ranges admit the intended versions and refuse vulnerable/incompatible ones; PyJWT has `crypto`; `uv.lock` resolves inside the ranges (TP-0.40)
  - File: `backend/tests/integration/test_security_dependencies.py` (5 cases)
  - Modules exercised: `backend/pyproject.toml`, `uv.lock`, `packaging`
- [x] **CT-10** — Malformed, missing and null tool arguments give an `isError` result `{"code": "invalid_input", "detail"}` naming the field, in 2025-06-18, 2025-11-25 and 2026-07-28 (TP-0.45)
  - File: `backend/tests/contract/test_mcp.py::test_invalid_tool_arguments_are_an_invalid_input_tool_error_in_every_era` (9 cases)
  - Modules exercised: MCP app on the SDK, tool registry, generic client, Keycloak token
- [x] **CT-11** — A 2026-07-28 request whose `_meta` lacks only the protocol version is refused with `400` (TP-0.33 probe; pins the SDK's `-32602`)
  - File: `backend/tests/contract/test_mcp.py::test_modern_request_without_its_meta_envelope_has_invalid_params[modern-header-without-meta-protocol-version]`
  - Modules exercised: MCP app on the SDK, generic client

### Integration Tests (Round 2)

No new integration infrastructure was needed: CT-6–CT-8, CT-10 and CT-11 run against the local Compose Keycloak and Postgres. Existing `integration/test_key_rotation.py` (real Keycloak rotation, IT-1) reruns green on `PyJWKClient`.

### How to Run (Round 2)

```bash
docker compose up -d --wait postgres keycloak ministack mailpit otel
uv run pytest backend/tests/contract/test_issuer_failures.py backend/tests/contract/test_mcp.py backend/tests/integration/test_security_dependencies.py -q
uv run pytest backend/tests/unit/test_auth.py backend/tests/unit/test_mcp_adapter.py backend/tests/unit/test_dogfood_edge.py -q
make verify
```

### Results (Round 2)

- In-scope baseline before new tests: 203 passed (unit auth/MCP adapter/dogfood edge/config, all contract, key rotation).
- New tests: 20 cases (CT-6 1, CT-7 3, CT-8 1, CT-9 5, CT-10 9, CT-11 1), committed in `e8f8982`. CT-6 verified to fail when `REFETCH_INTERVAL_SECONDS` is set to 0 (mutation reverted).
- **`make verify` at `e8f8982`:** exit 0 — unit 434 passed; integration + contract 193 passed; `be-replay` skipped by design; `check-generated` no diff; web 33; CDK 15 + synth. Verbose backend run: 627 passed, 0 failed, 0 skipped.
- **Gate 0.12.7** recorded in `a8035e9`: `foundation-ready.md` section 0.12.7 (mcp 2.3.0, PyJWT 2.15.1, `PyJWKClient` adoption, SDK adapter suite, A6 behaviors), new section 0.12.8 (pending), refreshed `make-verify.log` and `backend-tests-verbose.log` (paths `<repo>`, nine planted values redacted; `check_fixture_secrets.py` exit 0; markdown lint clean). The user's Tier 1 review is recorded there as pending.

### Validation (Round 2)

- [x] All component tests pass
- [x] All integration tests pass
- [x] All existing unit tests still pass (no regressions)
- [x] Build passes (`make verify` exit 0)
