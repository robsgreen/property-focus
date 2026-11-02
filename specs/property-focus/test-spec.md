# Test Spec: property-focus — Phase 0 (Foundation ready gate)

> **Source:** `specs/property-focus/tasks/test-plan.md` (Phase 0: TP-0.1–TP-0.37 and the gate line), `specs/property-focus/tasks/task-spec.md` (tasks 0.1–0.14)
> **Created:** 2026-11-02
> **Last Updated:** 2026-11-02

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
