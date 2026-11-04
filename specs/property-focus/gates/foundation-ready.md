# Foundation Ready Gate

> **Status: OPEN, not closed.** The local evidence (0.12.2, 0.12.4, 0.12.6, 0.12.7) is recorded below and passes. The gate closes only after the items marked **PENDING** are done: 0.12.1 (clean-clone bootstrap on macOS arm64, plus the CI Linux x86_64 log), 0.12.3 (recorded path-aware CI run; needs a push), 0.12.5 (`make smoke-dogfood` through Cloudflare; needs the Cloudflare setup, the Mailgun domain, the deployment secrets and a dogfood deployment), 0.12.8 (Cloudflare dashboard settings, `firewall.sh` and `deploy.sh` preflight output) and the user's Tier 1 review of the MCP SDK adapter (0.12.7). Each needs the user's action or explicit approval.

- **Task:** 0.12 in `specs/property-focus/tasks/task-spec.md`; plan section "Foundation ready gate" artifacts in `specs/property-focus/master-plan.md`.
- **Recorded:** 2026-11-02, branch `sdlc/property-focus-p0`. **Latest run (0.12.7):** `make verify` and the verbose backend run were rerun at `e8f8982`, after Tasks 0.15 (PyJWT `PyJWKClient`), 0.16 (official MCP SDK adapter) and 0.17 (Cloudflare edge) and the tests added for them; the commit that records them changes only this gate. The two log files now hold that run. **Earlier runs:** `make verify` and the verbose backend run were rerun at `71694cd`, the final code commit after the review fixes (`c734f96`, `6c2fdf5`, `bd1eae5`, `c188e47`, `71694cd`); the commit that records them changes only documentation and this gate. The metadata logs (0.12.6) were recorded at `3eae973`, before the review fixes. The PKCE smoke (0.12.4) was first recorded at `3eae973` and rerun at `dcd06fd`, after the review fixes changed sign-in and sign-out, with a sign-out check added.
- **Machine for the local evidence:** Linux x86_64, Docker 29.9.2, Keycloak `26.8.1` pinned by digest in the root `compose.yaml`.
- **Scope (A4):** phase 0 depends on no cloud account. Every check below runs against the local Compose services; the CDK step inside `make verify` only tests and synthesizes, with no credentials, and deploys nothing.

## Artifacts

| Plan artifact | Evidence | Result |
|---|---|---|
| Clean-bootstrap log, macOS arm64 | [0.12.1](#0121-clean-clone-bootstrap--pending) | **PENDING (user)** |
| Clean-bootstrap log, CI Linux x86_64 | [0.12.1](#0121-clean-clone-bootstrap--pending) | **PENDING (first CI run; needs a push)** |
| `make verify` | [make-verify.log](foundation-ready/make-verify.log) | pass, exit 0 at `e8f8982` |
| Every backend test by name | [backend-tests-verbose.log](foundation-ready/backend-tests-verbose.log) | 627 passed, 0 failed, 0 skipped at `e8f8982` |
| Two-account isolation and account-provisioning tests | `test_tenancy.py`, [table](#named-suites) | 19 / 19 |
| No-long-running-worker check | `test_runtime_manifests.py`, [table](#named-suites) | 11 / 11 |
| Keycloak realm import and policy tests | `test_keycloak_realm.py`, [table](#named-suites) | 41 / 41 |
| Dynamic-registration policy tests | `test_keycloak_realm.py` registration cases | pass |
| Generic client's dynamic-registration flow; discovery without `client_id_metadata_document_supported` | `test_mcp.py::test_dynamically_registered_generic_client_signs_in_and_calls_mcp`; [as-metadata.log](foundation-ready/as-metadata.log) | pass; `registration_endpoint` present, flag absent |
| REST and MCP validation of Keycloak-issued tokens | `test_auth.py`, `test_api.py`, `test_mcp.py`, `test_token_rejection.py`, `test_issuer_failures.py` | 59 / 59, 12 / 12, 45 / 45, 10 / 10, 5 / 5 |
| MCP 401/metadata and authenticated-call contract test | `test_mcp.py` challenge and sign-in cases | pass |
| Dual-era MCP contract tests | `test_mcp.py` dual-era cases, [table](#named-suites) | pass |
| PKCE sign-in smoke against local Keycloak, with sign-out | [pkce-smoke.log](foundation-ready/pkce-smoke.log), [make-dev.log](foundation-ready/make-dev.log) | pass (rerun at `dcd06fd`) |
| Path-aware CI run | [0.12.3](#0123-path-aware-ci-run--pending) | **PENDING (push; user approval)** |
| Dogfood smoke against the dogfood Keycloak | [0.12.5](#0125-dogfood-smoke--pending) | **PENDING (DNS, Mailgun, secrets, deployment; user approval)** |
| Same contract suite green on the official MCP SDK adapter with PyJWT's JWKS client (A6) | [0.12.7](#0127-rerun-on-the-mcp-sdk-adapter-and-pyjwkclient) | pass; `mcp` 2.3.0, PyJWT 2.15.1 |
| User's Tier 1 review of the MCP SDK adapter | [0.12.7](#0127-rerun-on-the-mcp-sdk-adapter-and-pyjwkclient) | **PENDING (user)** |
| Cloudflare edge: dashboard settings, `firewall.sh`, `deploy.sh` preflight (A5) | [0.12.8](#0128-cloudflare-edge--pending) | **PENDING (Cloudflare and host changes; user approval)** |
| Compatibility inventory | [`docs/compatibility.md`](../../../docs/compatibility.md) | attached |
| Keycloak mechanisms (0.13.3, 0.5.7) and option C findings (0.14) | [0.12.6](#0126-compatibility-inventory-and-keycloak-mechanisms) | attached |

## 0.12.1 Clean-clone bootstrap — PENDING

**PENDING — requires user action.** No macOS arm64 machine is available to the agent, and the CI log needs a push.

macOS arm64 (Apple silicon, Docker Desktop running, Node 22 and uv 0.11 installed). From an empty directory:

```sh
uname -sm                                   # must print: Darwin arm64
git clone https://github.com/robsgreen/property-focus.git
cd property-focus
git checkout sdlc/property-focus-p0         # or main once the phase 0 PR is merged
{
  corepack enable
  pnpm install --frozen-lockfile
  uv sync --all-packages --frozen
  cp .env.example .env
  docker compose up -d --wait postgres keycloak ministack mailpit otel
  uv run --package property-focus python -m property_focus.cli migrate
  uv run --package property-focus python -m property_focus.cli seed --dataset synthetic
} 2>&1 | tee bootstrap-macos-arm64.log
```

The ontology load and publish steps join the bootstrap with ontology 1.0.0 in phase 3. Save the log as `specs/property-focus/gates/foundation-ready/bootstrap-macos-arm64.log` after checking it holds no secret (the `.env` values are placeholders).

CI Linux x86_64: the `bootstrap` job in `.github/workflows/ci.yml` runs the same commands on `ubuntu-24.04` and fails unless `uname -sm` is `Linux x86_64`. Link its run URL here after the first CI run (which needs the push in 0.12.3).

- macOS arm64 log: _pending_
- CI Linux x86_64 bootstrap job: _pending_

## 0.12.2 `make verify`

> The tables in this section record the run at `71694cd`, before Tasks 0.15–0.17. The log files linked here were replaced by the rerun at `e8f8982`; [0.12.7](#0127-rerun-on-the-mcp-sdk-adapter-and-pyjwkclient) has its counts and the `test_mcp.py` cases as they are named now.

`make verify` (install, `be-check`, `be-unit`, `be-int`, `be-replay`, `check-generated`, `fe`, `infra`) exited 0 in 82 s at `71694cd`: [make-verify.log](foundation-ready/make-verify.log), run through `sdlc-run.sh`. The Compose services were already running, so `be-int` reused them. At `3eae973`, before the review fixes, the same run passed with 333 unit, 100 integration and contract, 12 web and 13 CDK tests.

| Step | Result |
|---|---|
| `pnpm install --frozen-lockfile`, `uv lock --check`, `uv sync --all-packages --frozen` | pass |
| `make check-layout`, `ruff check`, `ruff format --check` (68 files), `mypy backend/src` (29 files), `check_boundaries.py`, `check_fixture_secrets.py` | pass |
| `pytest backend/tests/unit` | 405 passed |
| `docker compose up -d --wait postgres keycloak ministack mailpit otel`; `pytest backend/tests/integration backend/tests/contract` | 172 passed |
| `be-replay` | skipped by design: `backend/tests/replay` and `fixtures/golden/manifest.json` arrive in phase 1 (Task 1.7) |
| OpenAPI export, RTK Query generation, `check-generated` | pass, no diff |
| Web app lint, typecheck, Vitest, build | pass, 33 tests |
| CDK `tsc --noEmit`, Vitest and `cdk synth --quiet` | pass, 15 tests |

The same three backend suites, rerun with `uv run pytest backend/tests/unit backend/tests/integration backend/tests/contract -v -p no:cacheprovider`, list every test by name: [backend-tests-verbose.log](foundation-ready/backend-tests-verbose.log) (577 passed at `71694cd`). Five parameter IDs of `test_cookie_key_and_password_patterns_are_detected` and four of `test_credentials_in_text_are_redacted` echo the synthetic values those tests plant; they read `<planted synthetic value redacted>` so the repository's own credential scan passes on the log. Repository paths read `<repo>` in every log.

### Named suites

| Suite | Passed | What the gate needs from it |
|---|---|---|
| `integration/test_tenancy.py` | 19 / 19 | Two-account isolation: raw-SQL reads and writes across accounts refused by row-level security, no scope leak between pooled transactions, the runtime role cannot disable or bypass row security, every account-private table forces it, another account's workflow run looks exactly like a missing one. Account provisioning: the first authenticated request creates one user and account, a repeat and concurrent first requests create no more. |
| `unit/test_runtime_manifests.py` | 11 / 11 | No long-running worker in either Compose file; the crontab starts only registered `run-task` kinds; the dogfood Keycloak image matches the local pin without experimental features; `make dev` refuses non-development configuration. |
| `integration/test_keycloak_realm.py` | 41 / 41 | Realm import (discovery names the issuer and `registration_endpoint`; the file holds no literal secret; `apply` is idempotent, `check` detects a manual change, `apply` keeps users and registered clients). PKCE enforcement (no code challenge and `plain` both rejected). Verified-email sign-up (tokens only after the emailed link). Audience (`aud` `property-focus-api`, `typ` `Bearer`). Dynamic-registration policy: a public loopback client is accepted; non-loopback `http`, wildcard, custom-scheme, fragment, confidential (`client_secret_basic`) and `private_key_jwt` registrations are rejected; password and implicit grants are disabled; no client gets client-credentials tokens; registration stops at the client cap; a registered client requires PKCE and consent; registration refuses client URIs Keycloak would fetch or call (sector identifier, request URIs, back-channel logout, JWKS) without fetching them, and a rejected registration leaves no client. Password policy: a weak password is refused; the sign-in page offers password reset. |
| `unit/test_auth.py` | 50 / 50 | The OIDC verifier: issuer, audience, token type, expiry and clock skew, required claims, algorithm allowlist, unsigned and HS256-confusion tokens, key rotation with one refetch, cached keys on a failed refresh. |
| `contract/test_api.py` | 12 / 12 | REST accepts a Keycloak access token (`test_keycloak_access_token_is_accepted_by_the_workflow_run_route`); missing or invalid credentials are `401` problems; problem bodies, correlation IDs, health probes and the OpenAPI bearer scheme. |
| `contract/test_mcp.py` | 34 / 34 | See below. |

`test_mcp.py` cases the gate names:

- **401 and metadata:** `test_unauthenticated_request_gets_a_challenge_naming_the_resource_metadata`, `test_missing_or_malformed_credential_is_challenged` (4 cases).
- **Dynamic registration and authenticated call:** `test_dynamically_registered_generic_client_signs_in_and_calls_mcp` (discovery from the challenge, anonymous registration at the advertised `registration_endpoint`, PKCE sign-in, MCP call with the token); `test_expired_keycloak_token_is_rejected`; `test_keycloak_token_without_the_api_audience_is_rejected`.
- **2025-06-18 and 2025-11-25 clients:** `test_initialize_based_client_lists_and_calls_tools[2025-06-18]` and `[2025-11-25]`; `test_initialize_answers_at_most_the_newest_initialize_based_revision`.
- **2026-07-28 client with and without `server/discover`:** `test_modern_client_gets_the_same_results_with_and_without_discovery`, `test_modern_tool_call_accepts_a_base64_encoded_tool_name`, `test_modern_client_falls_back_to_initialize_on_an_initialize_based_server`, `test_parity_harness_compares_rest_and_mcp_results[2026-07-28]`.
- **`-32020` header mismatch:** `test_modern_request_with_missing_or_mismatched_header_is_refused` (8 cases: no or other `Mcp-Method`, no or other `Mcp-Name`, no or other protocol version, no client capabilities, a 2026-07-28 header without `_meta`).
- **`-32022` unsupported version:** `test_unsupported_protocol_version_is_refused_on_both_paths[2024-11-05]` and `[2027-01-01]`.
- **Transport:** `test_transport_is_stateless_json`, `test_cross_origin_browser_request_is_forbidden`, `test_modern_notification_is_accepted_without_a_body`, `test_methods_2026_07_28_removed_or_not_served_are_not_found` (3 cases).

### Authorization-server metadata

[as-metadata.log](foundation-ready/as-metadata.log): both `/.well-known/oauth-authorization-server/realms/property-focus` and the realm's `/.well-known/openid-configuration` show `registration_endpoint` `http://localhost:8080/realms/property-focus/clients-registrations/openid-connect` and have no `client_id_metadata_document_supported` key (`has(...)` is `false`). They also list `plain` beside `S256` in `code_challenge_methods_supported`; the realm's `pkce-enforcer` executor rejects `plain`, which `test_web_client_rejects_authorization_without_s256_pkce[plain-method]` proves.

## 0.12.7 Rerun on the MCP SDK adapter and PyJWKClient

`make verify` exited 0 at `e8f8982` (90 s, through `sdlc-run.sh`; the Compose services were already running): [make-verify.log](foundation-ready/make-verify.log). The verbose backend run, `uv run pytest backend/tests/unit backend/tests/integration backend/tests/contract -v -p no:cacheprovider`, lists every test by name: [backend-tests-verbose.log](foundation-ready/backend-tests-verbose.log), 627 passed. Both logs use the same redaction as before (`<repo>` for paths; the nine planted synthetic values in test IDs read `<planted synthetic value redacted>`), and `check_fixture_secrets.py` passes on the repository with them.

| Step | Result |
|---|---|
| `pnpm install --frozen-lockfile`, `uv lock --check`, `uv sync --all-packages --frozen` | pass |
| `make check-layout`, `ruff check`, `ruff format --check` (71 files), `mypy backend/src` (29 files), `check_boundaries.py`, `check_fixture_secrets.py` | pass |
| `pytest backend/tests/unit` | 434 passed |
| `pytest backend/tests/integration backend/tests/contract` | 193 passed |
| `be-replay` | skipped by design (phase 1, Task 1.7) |
| OpenAPI export, RTK Query generation, `check-generated` | pass, no diff |
| Web app lint, typecheck, Vitest, build | pass, 33 tests |
| CDK `tsc --noEmit`, Vitest and `cdk synth --quiet` | pass, 15 tests |

**Resolved versions** (`uv.lock`): `mcp` **2.3.0** (with `mcp-types` 2.3.0), within `mcp>=2.3,<2.4`; `pyjwt` **2.15.1**, within `pyjwt[crypto]>=2.15.1,<3`. `integration/test_security_dependencies.py` (5 / 5) checks that both ranges exclude the vulnerable and incompatible releases and that the lock resolves inside them.

**PyJWKClient adoption (Task 0.15).** `adapters/auth/oidc.py` holds no hand-written key-set cache: one PyJWT `PyJWKClient` per issuer fetches and caches the JWKS, every call runs in a worker thread, and a small guard limits unknown-`kid` refetches to one a minute. `unit/test_auth.py` (59 / 59) covers it: `TestKeySet::test_many_unknown_kids_within_a_minute_cause_at_most_one_fetch`, `test_unknown_kid_refetches_at_most_once_a_minute`, `test_rotated_in_key_validates_after_one_refetch_and_a_removed_key_fails`, `test_concurrent_requests_share_one_fetch`, `test_the_key_set_is_fetched_off_the_event_loop_thread`, `test_expired_key_set_whose_refetch_fails_is_a_transient_dependency_failure`, `test_failed_refresh_within_the_maximum_age_keeps_serving_cached_keys`; `TestDecodeOptions::test_each_decode_gets_its_own_options_so_one_cannot_weaken_the_next`; `TestIssuers::test_one_key_set_client_serves_every_request`, `test_discovery_naming_another_issuer_is_refused`, the unreachable discovery and key-set cases; `TestAuthenticate::test_rest_answers_a_retryable_503_problem`, `test_mcp_answers_503_and_still_challenges_an_invalid_token`. Against the real realm: `integration/test_key_rotation.py` (1 / 1, rotation with one refetch and removal), `contract/test_token_rejection.py` (10 / 10, both adapters) and `contract/test_issuer_failures.py` (5 / 5): 100 tokens naming unknown `kid` values, alternating REST and MCP, all `401` with at most one key-set fetch while realm tokens keep working; an unreachable key set, an unreachable discovery document and a discovery document naming another issuer each give `503` on REST and MCP with no challenge; with the provider up, a valid token is served and a tampered one gets `401`.

**MCP SDK adapter (Task 0.16).** `unit/test_mcp_adapter.py` (11 / 11): the `TokenVerifier` returns an access token carrying the caller for a valid token, none for an invalid one, and raises the `503` exception when the identity provider or database is unreachable; `validate_token_resource` is false; transport security allows exactly the configured hosts and origins; duplicate tool names are refused; a `CodedError` becomes the structured tool error. `contract/test_mcp.py` (45 / 45) runs on the SDK adapter with the generic client:

- **401 and metadata:** `test_unauthenticated_request_gets_a_challenge_naming_the_resource_metadata` (the challenge's `resource_metadata` URL, `/.well-known/oauth-protected-resource/mcp` and the root form all name the Keycloak issuer), `test_missing_or_malformed_credential_is_challenged` (4 cases).
- **Dynamic registration and sign-in:** `test_dynamically_registered_generic_client_signs_in_and_calls_mcp`, `test_metadata_offers_registration_without_metadata_documents_and_resource_keeps_the_audience`, `test_expired_keycloak_token_is_rejected`, `test_keycloak_token_without_the_api_audience_is_rejected` (`401` although the SDK's resource check is off).
- **Both eras:** `test_initialize_based_client_lists_and_calls_tools[2025-06-18]` and `[2025-11-25]`, `test_initialize_answers_at_most_the_newest_initialize_based_revision`, `test_modern_client_gets_the_same_results_with_and_without_discovery`, `test_modern_tool_call_accepts_a_base64_encoded_tool_name`, `test_modern_client_falls_back_to_initialize_on_an_initialize_based_server`, `test_parity_harness_compares_rest_and_mcp_results[2025-11-25]` and `[2026-07-28]`.
- **`-32020`:** `test_modern_request_with_missing_or_mismatched_header_is_refused` (no or other `Mcp-Method`, no or other `Mcp-Name`, protocol-version header differing from `_meta`).
- **`-32022`:** `test_unsupported_protocol_version_is_refused_with_the_supported_versions` (`2027-01-01`, `data.supported` and `data.requested`).
- **`-32601`:** `test_methods_2026_07_28_removed_or_not_served_are_not_found` (`ping`, `logging/setLevel`, `resources/list`).
- **Transport:** `test_transport_is_stateless_json` (no `Mcp-Session-Id`, `application/json`, `202` for a notification, `405` on `GET` and `DELETE`), `test_modern_notification_is_accepted_without_a_body`, `test_unknown_method_unknown_tool_and_bad_bodies_are_errors`, `test_request_from_a_disallowed_host_or_origin_is_rejected_and_runs_no_tool` (foreign `Origin` and foreign `Host`), `test_issuer_outage_gives_503_not_a_challenge`.
- **Tool errors:** `test_invalid_tool_arguments_are_an_invalid_input_tool_error_in_every_era` (9 cases: malformed, missing and null arguments in 2025-06-18, 2025-11-25 and 2026-07-28 give an `isError` result with `{"code": "invalid_input", "detail"}`); the parity harness compares a use case's `CodedError` (`not_found`) through REST and both eras.

**SDK behaviors accepted under A6** (each pinned by a test above): `2024-11-05` `initialize` gets `401` without a token and is served with one (`test_revision_2024_11_05_is_served_only_with_a_valid_token`); a 2026-07-28 request without client capabilities, without `_meta`, or whose `_meta` names no protocol version gets `400` with the specification's `-32602`, not `-32020` (`test_modern_request_without_its_meta_envelope_has_invalid_params`, 3 cases); `data.supported` is the SDK's list; the `401` challenge is the SDK's format.

**Cloudflare edge, deterministic part (Task 0.17).** `unit/test_dogfood_edge.py` (18 / 18): both Caddy sites serve the origin certificate and no site obtains one by ACME; `trusted_proxies` and the client-IP headers come only from `cloudflare-ips.txt`, `CF-Connecting-IP` first; `deploy.sh` passes exactly the committed ranges; the ranges are valid IPv4 and IPv6 networks; only Caddy publishes ports, over TCP, with the origin directory read-only; the smoke check accepts a response with `cf-ray`, rejects one without, names Bot Fight Mode for a challenge page, and fails when the origin accepts a direct connection.

**User's Tier 1 review of the SDK adapter: PENDING — requires the user.** The wire differences from the hand-written adapter are listed in the Task 0.16 report and above (SDK behaviors accepted under A6); record the user's review here and in `status.md`.

- Tier 1 review: _pending_

## 0.12.3 Path-aware CI run — PENDING

**PENDING — requires a push; ask the user first.** Nothing has been pushed.

After the user approves pushing `sdlc/property-focus-p0`:

1. Push the branch and open the phase 0 pull request as a draft. It changes `backend/**`, so the `changes` job selects the backend jobs (`backend`, `integration`, `replay`, `generation`) plus the secret scan. Record the run URL and the job list.
2. Open a second draft pull request from a branch off it that changes only one line under `docs/**` or `specs/**`. Its run must show only `secret-scan` and `markdown` running, with every other job skipped. Record the run URL and the job list, then close that pull request without merging.

- Backend pull request run: _pending_
- Docs-only pull request run: _pending_

## 0.12.4 PKCE sign-in smoke against local Keycloak

[pkce-smoke.log](foundation-ready/pkce-smoke.log): pass, rerun on 2026-11-02 at `dcd06fd`, the head when it ran (the commit that records it changes only this gate). The first run was at `3eae973`; the review fixes since then changed the web app's sign-in and sign-out (`c734f96`), the realm (`bd1eae5`) and the `0001` migration, so the smoke was run again and now also signs out. With `make dev` running (API on 127.0.0.1:8000, MCP on 127.0.0.1:8001, Vite on localhost:5173, scheduler) against the local realm, [pkce-smoke.py](foundation-ready/pkce-smoke.py):

1. creates a synthetic verified `@example.test` user through `backend/tests/support/keycloak.py`;
2. runs [pkce-smoke-sign-in.mjs](foundation-ready/pkce-smoke-sign-in.mjs) in headless Chromium (Playwright): the web app sends the signed-out visitor to Keycloak with `client_id=property-focus-web`, `redirect_uri=http://localhost:5173/auth/callback`, `response_type=code`, `code_challenge_method=S256`; the script submits the login form; the app exchanges the code with its `code_verifier` (token endpoint `200`) and shows its signed-in page. The access token, taken from the app's own token response, carries `iss` `http://localhost:8080/realms/property-focus`, `aud` `property-focus-api`, `azp` `property-focus-web`, `typ` `Bearer`. The browser stays open;
3. with that token, `GET /v1/workflow-runs/{random id}` through the web app's origin (Vite forwards `/v1` to the API) is an authenticated `application/problem+json` `404`, not a `401` (the REST check of `backend/scripts/smoke_dogfood.py`);
4. calls the MCP endpoint: `initialize` (requested `2025-11-25`, answered `2025-11-25`), `notifications/initialized` and `tools/list` on the initialize-based path; `server/discover` (`supportedVersions` `2026-07-28`, `2025-11-25`, `2025-06-18`, `2025-03-26`; `resultType` `complete`; `cacheScope` `private`) and `tools/list` on the 2026-07-28 path with no fallback;
5. signs out in the same browser. Keycloak's admin API lists one session for the user. The script clicks "Sign out": the app sends the browser to Keycloak's end-session endpoint with `id_token_hint` and `post_logout_redirect_uri=http://localhost:5173/`; Keycloak ends the session and answers `302` to the app; the app, now signed out, starts a new sign-in, and the browser ends on Keycloak's sign-in form, with no token request and no signed-in page. No `KEYCLOAK_IDENTITY` or `KEYCLOAK_SESSION` cookie is left. Loading `http://localhost:5173/` again also ends on the sign-in form without a token request. Keycloak then lists no session for the user;
6. deletes the user.

After the sign-out the log shows two authorization requests: React's development StrictMode starts the sign-in twice and the second navigation supersedes the first.

[make-dev.log](foundation-ready/make-dev.log) is the server side of the run, through to the clean shutdown after `make dev` was stopped with SIGINT to its process group (as Ctrl-C does). The servers no longer write uvicorn access lines (`access_log=False` since the review fixes), so only the REST `404` lines appear; the log holds two of them because the smoke ran twice on this `make dev`, both passing (the second, recorded run only changed how the log prints a superseded request). The local `property_focus` database had the `0001` migration applied before it was rewritten in the review fixes, so for this run `PF_DATABASE_URL` and `PF_MIGRATION_DATABASE_URL` named a new database migrated at `dcd06fd`, dropped afterwards. The token appears in no log.

To rerun, from the repository root with `docker compose up -d --wait postgres keycloak mailpit otel`, `cli migrate` done (on a database created by the current `0001`) and `make dev` running in another terminal:

```sh
NODE_PATH=/opt/node-tools/node_modules \
  uv run --package property-focus python specs/property-focus/gates/foundation-ready/pkce-smoke.py
```

`NODE_PATH` points at a directory with the `playwright` package; set `PF_SMOKE_CHROMIUM` to a Chromium binary when it is not at `/opt/pw-browsers/chromium`.

## 0.12.5 Dogfood smoke — PENDING

**PENDING — requires user setup, a dogfood deployment and explicit user approval.** Live; nothing has run against dogfood. Since Task 0.17 the hostnames are proxied by Cloudflare: step 2 below is replaced by the Cloudflare runbook in `docs/operations.md` (proxied records, Origin CA certificate as `DOGFOOD_ORIGIN_CERT` and `DOGFOOD_ORIGIN_KEY`, `firewall.sh`), the smoke also needs `PF_DOGFOOD_ORIGIN_ADDRESS`, and it additionally checks `cf-ray` on every response, that a direct connection to the origin's port 443 fails, and that no Bot Fight Mode challenge appears.

> **CP-0.B (answered by the user, 2026-11-02):**
>
> - App host (`PF_DOGFOOD_APP_HOST`): `propertyfocus.treb.tech`.
> - Auth host (`PF_DOGFOOD_AUTH_HOST`): `auth-propertyfocus.treb.tech` (user decision 2026-11-02, replacing the first answer `auth.propertyfocus.treb.tech`: Cloudflare Universal SSL covers only first-level subdomains, [limitations](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/)).
> - SMTP relay: Mailgun, `PF_KEYCLOAK_SMTP_HOST=smtp.mailgun.org`, `PF_KEYCLOAK_SMTP_PORT=2525`, STARTTLS (`PF_KEYCLOAK_SMTP_STARTTLS=true`, `PF_KEYCLOAK_SMTP_SSL=false`, the defaults). The user verifies the sending domain (for example `mg.treb.tech`) in Mailgun.
> - Credentials: set only as `dogfood` GitHub environment secrets (`DOGFOOD_ENV_FILE`, `PF_SMOKE_USER_PASSWORD`) and in the host's environment file the workflow writes from them; never in Git, a log or chat.

Steps, in order (`docs/operations.md`, "The host" and "Mail relay"):

1. **User:** provision the DigitalOcean host and do the one-time host setup in `docs/operations.md` (deploy user, directories).
2. **User:** in the `treb.tech` DNS zone, add `A` records for `propertyfocus.treb.tech` and `auth-propertyfocus.treb.tech` pointing at the host's public IPv4 address, and wait until both resolve.
3. **User:** add the sending domain in Mailgun, add its SPF and DKIM `TXT` records, wait for Mailgun to show it verified, and create an SMTP user for it.
4. **User:** on the `dogfood` GitHub environment, set the variables `PF_DOGFOOD_APP_HOST=propertyfocus.treb.tech`, `PF_DOGFOOD_AUTH_HOST=auth-propertyfocus.treb.tech`, `PF_SMOKE_USER_EMAIL`, `DOGFOOD_SSH_TARGET` and `DOGFOOD_SSH_KNOWN_HOSTS`, and the secrets `DOGFOOD_SSH_PRIVATE_KEY`, `PF_SMOKE_USER_PASSWORD` and `DOGFOOD_ENV_FILE`. `DOGFOOD_ENV_FILE` holds the variables in `docs/operations.md`, "Environment file", including `PF_KEYCLOAK_SMTP_HOST=smtp.mailgun.org`, `PF_KEYCLOAK_SMTP_PORT=2525`, `PF_KEYCLOAK_SMTP_FROM` on the verified domain, and the Mailgun SMTP user and password.
5. **After the user approves the deploy:** run the `deploy-dogfood` workflow for the phase 0 commit. It deploys with `infrastructure/dogfood/deploy.sh` and then runs `make smoke-dogfood` from the runner.
6. **Or, after the user approves the smoke,** run it from a machine outside the dogfood host:

```sh
PF_DOGFOOD_APP_HOST=propertyfocus.treb.tech \
PF_DOGFOOD_AUTH_HOST=auth-propertyfocus.treb.tech \
PF_SMOKE_USER_EMAIL=<smoke user email> \
PF_SMOKE_USER_PASSWORD=<smoke user password> \
  make smoke-dogfood 2>&1 | tee smoke-dogfood.log
```

It checks API readiness, `404` on `/admin/` and `/realms/master/` on the auth host, the smoke user's PKCE sign-in through the web client, the REST call with that token and the MCP endpoint's `401` for it; then it registers a public loopback client anonymously (dynamic client registration, as a generic MCP client does), signs the smoke user in through it with PKCE and consent, checks both MCP protocol paths and the REST API's `401` with that MCP-audience token, and always deletes the client with its registration access token, and prints one `ok:` line per check. Save the output (from the workflow log or the command above) as `specs/property-focus/gates/foundation-ready/smoke-dogfood.log`; it prints no token or password.

- `make smoke-dogfood` output: _pending_

## 0.12.8 Cloudflare edge — PENDING

**PENDING — requires Cloudflare dashboard changes, `firewall.sh` on the host and a dogfood deploy, each with explicit user approval.** Nothing has been changed in Cloudflare or on a host. Follow the Cloudflare runbook in `docs/operations.md`, then record here: the dashboard settings (proxied records for both hostnames, SSL mode Full (strict), the free managed ruleset, the rate-limiting rule's expression and threshold over registration, sign-in and `/mcp`, the Bot Fight Mode state), the `firewall.sh` output, the `deploy.sh` edge preflight output from the deploy that precedes 0.12.5, and Caddy's access log showing the real client address while a forged `CF-Connecting-IP` from a non-Cloudflare address is ignored.

User decisions recorded 2026-11-02: sign-in moved to the first-level `auth-propertyfocus.treb.tech`, so Universal SSL covers both hostnames and the Origin CA certificate lists exactly `propertyfocus.treb.tech` and `auth-propertyfocus.treb.tech`; Bot Fight Mode stays off (the free plan cannot exempt paths from it, and it would challenge non-browser MCP clients and token requests); the rate-limiting rule allows 20 requests per 10 seconds per IP on the sign-in, registration and `/mcp` paths, then blocks with `429`.

- Dashboard settings: _pending_
- `firewall.sh` output: _pending_
- `deploy.sh` preflight output: _pending_
- Trusted-proxy check on the host: _pending_

## 0.12.6 Compatibility inventory and Keycloak mechanisms

### Compatibility inventory

[`docs/compatibility.md`](../../../docs/compatibility.md), generated by `make compatibility-local` in Task 0.10 against the pinned MiniStack 1.5.35: Secrets Manager `GetSecretValue`, Parameter Store `GetParameter`, Lambda `Invoke`, Step Functions `StartExecution` and ECS `RunTask` all pass locally, with no unsupported operation. Its column for a real cloud account stays "not run" until phase 9.

### Dynamic client registration controls (0.13.3)

Measured against Keycloak 26.8.1; configured in `infrastructure/keycloak/realm-property-focus.json` and covered by `test_keycloak_realm.py`.

| Control | Keycloak mechanism |
|---|---|
| Consent required | Anonymous client registration policy `consent-required` ("Consent Required") |
| Only realm default client scopes | Anonymous registration policy `allowed-client-templates` with `allow-default-scopes` `true` |
| Full scope off | Anonymous registration policy `scope` ("Full Scope Disabled") |
| Protocol-mapper allowlist | Anonymous registration policy `allowed-protocol-mappers` with Keycloak's default list |
| Client cap | Anonymous registration policy `max-clients`, `${PF_KEYCLOAK_MAX_CLIENTS:100}` |
| Redirect URIs: `https`, or `http` on `localhost`, `127.0.0.1` or `[::1]`; no wildcard or fragment | Realm-wide client policy `client-rules` (condition `any-client`), executor `secure-client-uris-pattern` on `redirectUris`. The Trusted Hosts registration policy is removed: it cannot accept any `https` host while limiting `http` to loopback, so the client policy replaces it and the requesting host is not restricted. |
| PKCE `S256` | Same client policy, executor `pkce-enforcer` (auto-configure) |
| No implicit or password grant | Same client policy, executors `reject-implicit-grant` and `reject-ropc-grant` |
| Reject a registration that asks for a confidential client | Client policy `public-dynamic-clients-only`: conditions `client-updater-context` (`ByAnonymous`, `ByInitialAccessToken`, `ByRegistrationAccessToken`) and `client-access-type` `confidential`, profile `reject` (executor `reject-request`). A client policy, not a registration policy, answers the plan's research question. |
| No client-credentials grant | Client policy `no-client-credentials-grant`: condition `grant-type` `client_credentials`, executor `reject-request` |

### Authorization-server metadata URL forms (0.5.7)

[metadata-url-forms.log](foundation-ready/metadata-url-forms.log), for the issuer `http://localhost:8080/realms/property-focus`:

| URL form | Keycloak 26.8.1 |
|---|---|
| `/.well-known/oauth-authorization-server/realms/property-focus` (RFC 8414, issuer path inserted) | served, `200` |
| `/.well-known/openid-configuration/realms/property-focus` (OpenID Connect discovery, issuer path inserted) | not served, `404`; `infrastructure/dogfood/Caddyfile` maps it to the realm's discovery document (Task 0.10) |
| `/realms/property-focus/.well-known/openid-configuration` (OpenID Connect discovery, path appended) | served, `200` |
| `/realms/property-focus/.well-known/oauth-authorization-server` | served, `200` |

### Client ID Metadata Documents deferred (0.14, option C)

User decision of 2026-11-02, recorded in the master plan's resolved list. Measured against Keycloak 26.8.1:

1. The realm-wide `secure-client-uris-pattern` executor rejects every CIMD sign-in ("not allowed input format." outside admin and registration contexts), and `secure-redirect-uris-enforcer` cannot allow `http` only on loopback, so enabling CIMD would weaken or narrow the registration redirect rule.
2. CIMD accepts client-ID and redirect hosts only from a hand-listed trusted-domain list (`*` matches nothing): allowlist-only.
3. With `resource-indicators` on, a token requested with `resource` gets `aud` equal to the resource URL alone, dropping `property-focus-api`, and dynamic registration fails with `invalid_target` unless a client has a matching `resource_url`.

Both features stay off; dynamic registration is the only MCP registration path, and the metadata above confirms it. CIMD-only clients cannot connect meanwhile (plan Risks; CP-9.D).
