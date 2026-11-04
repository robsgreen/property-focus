# Task Spec: property-focus

> **Source:** `specs/property-focus/master-plan.md`
> **Decision register:** `specs/property-focus/architecture-decisions.md` (D1–D56, A1–A6; the register wins on conflict)
> **Created:** 2026-11-02
> **Last Updated:** 2026-11-02
> **Progress:** 111 / 506 sub-tasks complete

---

## How to Read This Spec

- Phases use the plan's numbering, 0–9. Task `0.1` is the first Foundation task.
- Each task lists `Modules`, `Depends on` and `Decisions` (register IDs it implements).
- A **Checkpoint** blockquote under a phase heading is a blocking question for the user or a named owner. The orchestrator asks it and records the answer in `status.md` before the blocked task starts. Implementers never answer a checkpoint themselves.
- The last task of each phase is its release gate (or its contribution to a multi-phase gate). Gate evidence goes to `specs/property-focus/gates/<gate-name>.md`.
- A validation line marked **Live (explicit user approval required)** needs cloud credentials, a push, or paid calls. The orchestrator asks the user before running it. It is never an automatic check.

### Rules for Every Task

- Add one appended Alembic migration per task that needs tables, named `backend/migrations/versions/<NNNN>_<task>.py`. Create only that task's tables. Never rewrite a released migration.
- Never edit `backend/src/property_focus/adapters/db/rls.sql`. It belongs to released migration 0001, and `backend/tests/unit/test_migrations.py` pins its SHA-256. A task that adds account-private or search-scoped tables creates their runtime-role grants and forced RLS policies in its own appended migration.
- Do not edit `frontend/src/api/generated.ts` by hand. After any REST schema change, run the `GEN` command set.
- Each MCP tool lands in the same task as its REST counterpart, with a `backend/tests/contract/test_mcp.py` parity case.
- Every task with logic changes adds unit tests. Tests protect domain and operational behavior; they do not mirror method bodies.
- Use synthetic fixtures only. Never commit credentials, browser profile or session state, unredacted private email, licensed source corpora, production data or another account's private records. Do not create `/.pvpd/` paths.
- Name the first implementation and a measurable need before adding another abstraction (plan SHOULD). Unused placeholder services do not enter the release.
- Data rules: UUID primary keys, UTC `timestamptz`, integer money minor units with ISO currency, decimal ratios, explicit unit codes, WGS84 coordinates, `created_at` and schema version on versioned records. No floating-point money.
- Errors: REST uses `application/problem+json` with the status rules in the plan's API section. An inaccessible ID returns `404` with the same body as a nonexistent ID.

### Command Sets

Validation lines refer to these sets by name. A set member that does not exist yet (before the task that creates it) is skipped and noted in the coder's report.

| Set | Exact commands (repository root) |
|---|---|
| `INSTALL` | `pnpm install --frozen-lockfile`; `uv sync --all-packages --frozen` |
| `BE-CHECK` | `make check-layout`; `uv run ruff check backend`; `uv run ruff format --check backend`; `uv run mypy backend/src`; `uv run python backend/scripts/check_boundaries.py`; `uv run python backend/scripts/check_fixture_secrets.py` |
| `BE-UNIT` | `uv run pytest backend/tests/unit -q` |
| `BE-INT` | `docker compose up -d postgres keycloak ministack mailpit otel`; `uv run pytest backend/tests/integration backend/tests/contract -q` |
| `BE-REPLAY` | `uv run pytest backend/tests/replay -q`; `uv run --package property-focus python -m property_focus.cli replay --manifest fixtures/golden/manifest.json --offline` |
| `EVAL` | `uv run --package property-focus python -m property_focus.cli evaluate --config backend/evals/config/baseline.yaml --offline --fail-on-regression`; `uv run --package property-focus python -m property_focus.cli evaluate --config backend/evals/config/baseline.yaml --mode shadow --no-publish` |
| `GEN` | `uv run python backend/scripts/export_openapi.py --out frontend/src/api/openapi.json`; `pnpm --filter @property-focus/web generate:api`; `make check-generated` |
| `FE` | `pnpm --filter @property-focus/web lint`; `pnpm --filter @property-focus/web typecheck`; `pnpm --filter @property-focus/web test --run`; `pnpm --filter @property-focus/web build` |
| `INFRA` | `pnpm --filter @property-focus/infra test --run`; `pnpm --filter @property-focus/infra synth` |
| `E2E` | `make test-e2e` |
| `RECOVERY` | `make restore-check`; `make compatibility-local` |
| `VERIFY` | `make verify` (runs `INSTALL`, `BE-CHECK`, `BE-UNIT`, `BE-INT`, `BE-REPLAY`, `GEN`, `FE`, `INFRA`) |
| `LIVE` (explicit user approval required) | `make test-live` (needs `PF_LIVE=1`); `make deploy-identity ENV=beta` (phase 9 only); `pnpm --filter @property-focus/infra diff --context environment=beta`; `make smoke-aws ENV=beta`; `make smoke-dogfood`; `make storage-check ENV=dogfood` |

---

## Phase 0: Foundation

**Goal:** A locked, bootstrappable D47 monorepo with OIDC auth against a self-hosted Keycloak realm kept as code (A4), per-account isolation, migrations, on-demand tasks, outbox, failure taxonomy, telemetry, an MCP skeleton serving protocol revisions 2025-03-26 through 2026-07-28 with dynamic client registration (Client ID Metadata Documents deferred by user decision 2026-11-02; see CP-9.D), and path-aware CI. Tasks 0.15–0.17 (user decisions 2026-11-02, A5 and A6) move token-key handling to PyJWT's `PyJWKClient`, rebuild the MCP adapter on the official MCP Python SDK, and put Cloudflare in front of dogfood. Phase 0 has no AWS dependency: the Cognito CDK identity stack (Task 0.9) is built and synthesized but not deployed until phase 9. **Deliverable:** the Foundation ready gate.

> **Checkpoint CP-0.A — withdrawn 2026-11-02 (A4).** The Cognito dynamic client registration question no longer applies to phase 0; it moved to phase 9 as CP-9.B.

> **Checkpoint CP-0.B (blocking for the live dogfood steps of Tasks 0.10 and 0.12 only) — dogfood domain and SMTP relay.** Owner: user, before the first dogfood deployment. Questions: which domain serves dogfood (the application hostname and `auth.<dogfood domain>` for Keycloak), and which outbound SMTP relay Keycloak uses for verification emails? Local work, including every non-live sub-task, proceeds with Mailpit and `localhost`.
>
> **Answered 2026-11-02 (user):** the application runs at `propertyfocus.treb.tech` and Keycloak at `auth-propertyfocus.treb.tech`. (The first answer named `auth.propertyfocus.treb.tech`; the user moved sign-in to the first-level name the same day because Cloudflare's free Universal SSL covers only first-level subdomains, see [Universal SSL limitations](https://developers.cloudflare.com/ssl/edge-certificates/universal-ssl/limitations/).) Keycloak sends verification email through Mailgun at `smtp.mailgun.org` port 2525. SMTP credentials exist only as deployment secrets.

### Task 0.1: Monorepo Root, Backend Package and Repository Checks

Create the D47 top-level layout, root tooling, the `property-focus` Python distribution, and the layout, import-boundary and secret-scan checks.

**Modules:** root tooling, `backend`, `ontology`
**Depends on:** none
**Decisions:** D18, D20, D47

**Files:**

```
README.md                                        (modify)
AGENTS.md                                        (create)
.gitignore                                       (create)
.env.example                                     (create)
.markdownlint-cli2.yaml                          (create)
pyproject.toml                                   (create)
uv.lock                                          (create)
package.json                                     (create)
pnpm-workspace.yaml                              (create)
pnpm-lock.yaml                                   (create)
tsconfig.base.json                               (create)
Makefile                                         (create)
ontology/CHANGELOG.md                            (create)
backend/pyproject.toml                           (create)
backend/src/property_focus/__init__.py           (create)
backend/scripts/check_boundaries.py              (create)
backend/scripts/check_fixture_secrets.py         (create)
backend/tests/unit/test_repo_checks.py           (create)
```

**Sub-tasks:**

- [x] **0.1.1** — Create root `pyproject.toml` as the uv workspace root (member `backend`, Python 3.13+, shared ruff, mypy and pytest settings) and `backend/pyproject.toml` (distribution `property-focus`, package `property_focus`, CLI entry point `property_focus.cli`). Use bounded version ranges.
- [x] **0.1.2** — Create `package.json` (root commands), `pnpm-workspace.yaml` (workspaces `frontend`, `infrastructure/cdk`) and strict `tsconfig.base.json`. Select and pin a current supported Node LTS and the pnpm version through corepack.
- [x] **0.1.3** — Create `.gitignore` (secrets, `.env`, browser sessions and profiles, local evidence, caches) and `.env.example` with placeholder values only and synthetic mode as the default.
- [x] **0.1.4** — Create `Makefile` with `check-layout` (fails when root `apps/`, `packages/`, `infra/` or `evals/` exist) and a `verify` target that later tasks extend to the full `VERIFY` set.
- [x] **0.1.5** — Write `backend/scripts/check_boundaries.py`: fail when `property_focus.domain` imports `adapters`, `runtime`, FastAPI, boto3 or Playwright, or when `property_focus.application` imports `adapters.rest` or `adapters.mcp`.
- [x] **0.1.6** — Write `backend/scripts/check_fixture_secrets.py`: scan committed files, `/fixtures` and evidence fixtures for credential patterns (keys, tokens, cookies, private keys) and exit non-zero on a hit.
- [x] **0.1.7** — Write `backend/tests/unit/test_repo_checks.py`: a temporary tree with a forbidden import fails the boundary check; a planted fake token fails the secret check; a root `evals/` directory fails `check-layout`.
- [x] **0.1.8** — Run the first bootstrap without locks (`pnpm install`, `uv sync --all-packages`), review resolved versions, commit `pnpm-lock.yaml` and `uv.lock`. Write `README.md` (bootstrap and commands), `AGENTS.md` (boundaries, verification, generated-file rules, no `/.pvpd/`), `.markdownlint-cli2.yaml` for `docs/**` and `specs/**`, and `ontology/CHANGELOG.md` (states that v1.0.0 lands in phase 3).

**Validation:**

- [x] **Build:** `INSTALL` and `BE-CHECK` exit 0.
- [x] **Tests:** `BE-UNIT` passes, including `test_repo_checks.py` cases for forbidden domain import, forbidden application import of REST/MCP, planted secret, and forbidden root directory.
- [x] **Requirements:**
    - [x] "The repository has top-level `/backend`, `/frontend`, `/infrastructure`, `/ontology`, `/fixtures`, `/specs` and `/docs`; `make check-layout` fails if `apps/`, `packages/`, `infra/` or `evals/` exist at the root." (layout part completed by Tasks 0.4, 0.8, 0.9, 0.10; confirmed at Task 0.12)
    - [x] "`backend/scripts/check_boundaries.py` fails if domain imports an adapter or runtime module, or application imports REST/MCP."
    - [x] "Generate lockfiles during bootstrap and commit them."
- [x] **Verify:** `mkdir apps && make check-layout; echo $?` prints a non-zero code; remove `apps/` afterwards. `grep -rn "\.pvpd" --include='*' .` outside `specs/` returns nothing.

---

### Task 0.2: Typed Configuration, SecretProvider and Failure Taxonomy

Add typed settings, the `SecretProvider` port with AWS and deployment-injected providers, and the D55 failure classes with stable codes and the retry policy.

**Modules:** `backend`
**Depends on:** 0.1
**Decisions:** D49, D55

**Files:**

```
backend/src/property_focus/config.py                  (create)
backend/src/property_focus/application/__init__.py    (create)
backend/src/property_focus/application/ports.py       (create)
backend/src/property_focus/adapters/__init__.py       (create)
backend/src/property_focus/adapters/secrets.py        (create)
backend/src/property_focus/domain/__init__.py         (create)
backend/src/property_focus/domain/errors.py           (create)
backend/tests/unit/test_config.py                     (create)
backend/tests/unit/test_failures.py                   (create)
```

**Sub-tasks:**

- [x] **0.2.1** — Implement `config.py`: typed settings with environment validation, an explicit `development` flag, and refusal of development-only features (fixture issuer, `make dev`) in non-development configuration.
- [x] **0.2.2** — Define `SecretProvider` in `application/ports.py` (`get(logical_name) -> str`).
- [x] **0.2.3** — Implement `adapters/secrets.py`: an AWS provider (Secrets Manager for secrets, Parameter Store or environment for configuration) and a deployment-injected provider for dogfood, selected by configuration.
- [x] **0.2.4** — Implement `domain/errors.py`: every stable code from the plan's error table (`invalid_input`, `unsupported_source`, `unauthorized`, `forbidden`, `duplicate`, `idempotency_conflict`, `material_change_requires_confirmation`, `owner_cannot_leave`, `identity_ambiguous`, `auth_required`, `source_blocked`, `source_changed`, `source_removed`, `capability_unavailable`, `rate_limited`, `dependency_transient`, `timeout`, `schema_invalid`, `unsupported_capability`, `budget_exceeded`, `insufficient_data`, `evidence_integrity`, `entitlement_revoked`, `conflicting_claims`, `stale_input`, `notification_unknown`, `internal_defect`, `provider_outcome_unknown`) mapped to exactly one class or marked non-failure, plus the `requires_reconciliation` flag.
- [x] **0.2.5** — Implement the retry policy: only `TRANSIENT` retries automatically, exponential backoff with jitter, honors Retry-After, at most four attempts including the first, bounded by the workflow deadline; flagged codes never retry automatically.
- [x] **0.2.6** — Write `test_config.py` (validation, development-flag refusal) and `test_failures.py` (an unmapped code fails; flagged codes schedule no retry; attempt cap; Retry-After honored; non-`TRANSIENT` classes never retry).

**Validation:**

- [x] **Build:** `BE-CHECK` exits 0.
- [x] **Tests:** `BE-UNIT` passes with the new `test_config.py` and `test_failures.py` cases listed above.
- [x] **Requirements:**
    - [x] "Every stable code in `domain/errors.py` maps to exactly one class in {`TRANSIENT`, `AUTH_REQUIRED`, `DATA_UNAVAILABLE`, `INVALID_INPUT`, `PERMANENT`, `BUG_UNKNOWN`} or is marked non-failure; `test_failures.py` fails on an unmapped code."
    - [x] "Retry only `TRANSIENT` failures automatically, with exponential backoff and jitter, honoring provider Retry-After, capped at four attempts including the initial attempt and by the workflow deadline. Exception: codes flagged `requires_reconciliation` … never retry automatically … `test_failures.py` asserts no automatic retry is scheduled for a flagged code."
    - [x] "Application code reads secrets only through the `SecretProvider` port by logical name; AWS uses Secrets Manager for secrets and Parameter Store or environment for configuration; dogfood uses deployment-injected values."
- [x] **Verify:** `grep -rnE "boto3|os\.environ" backend/src/property_focus/domain backend/src/property_focus/application` returns nothing.

---

### Task 0.3: Database Foundation, Local Compose, Migrations and RLS

Create local services, the foundation migration (tenants, users, roles, workflow, idempotency, outbox, audit tables), runtime-role separation, forced RLS and the scoped unit of work.

**Modules:** `backend`, root tooling
**Depends on:** 0.2
**Decisions:** D5 (surviving parts), D24, D25, A1

**Files:**

```
compose.yaml                                                   (create)
backend/alembic.ini                                            (create)
backend/migrations/env.py                                      (create)
backend/migrations/versions/0001_foundation.py                 (create)
backend/src/property_focus/adapters/db/__init__.py             (create)
backend/src/property_focus/adapters/db/models.py               (create)
backend/src/property_focus/adapters/db/repositories.py         (create)
backend/src/property_focus/adapters/db/rls.sql                 (create)
backend/src/property_focus/cli.py                              (create)
backend/tests/integration/conftest.py                          (create)
backend/tests/integration/test_tenancy.py                      (create)
```

**Sub-tasks:**

- [x] **0.3.1** — Create `compose.yaml` with `postgres`, `ministack` (image pinned by digest), `mailpit` and `otel` services and health checks.
- [x] **0.3.2** — Configure `alembic.ini` and `migrations/env.py` from `config.py`.
- [x] **0.3.3** — Write `0001_foundation.py`: `tenants` (unique `owner_user_id`), `users` (issuer/sub unique, verified email), `user_roles` (only `reviewer`, `operator`), `workflow_runs`, `workflow_steps`, `step_attempts`, `idempotency_records` (unique scope/operation/key), `domain_events`, `outbox_events`, `processed_events` (consumer/event unique) and `audit_events`, with the plan's columns and states.
- [x] **0.3.4** — Create database roles: a migration owner, a runtime role that owns no tables and has no `BYPASSRLS`, and documented maintenance and backup roles. Install `rls.sql` from the migration with forced RLS on account-private tables keyed by `tenant_id NOT NULL`.
- [x] **0.3.5** — Implement `repositories.py`: a unit of work that sets tenant and user context per transaction (`SET LOCAL`) and clears it at transaction end; scoped repositories; SQLAlchemy models in `models.py`.
- [x] **0.3.6** — Implement `cli.py migrate`.
- [x] **0.3.7** — Write integration tests in `test_tenancy.py`: the runtime role cannot read another account's rows with raw SQL; context does not leak to the next pooled transaction; the runtime role cannot disable RLS.

**Validation:**

- [x] **Build:** `BE-CHECK` exits 0; `uv run --package property-focus python -m property_focus.cli migrate` on an empty database exits 0.
- [x] **Tests:** `BE-INT` passes with the three RLS and context tests above.
- [x] **Requirements:**
    - [x] "Account-private tables MUST have `tenant_id NOT NULL`, scoped foreign keys where appropriate, and forced RLS."
    - [x] "Runtime roles neither own tables nor bypass row security. Pool users set account (tenant) and user context per transaction and clear it at transaction end."
    - [x] "Database maintenance roles and backup roles require a documented separation from runtime roles."
    - [x] "Use UUID primary keys, UTC `timestamptz` … Include `created_at` and schema version on persisted versioned records."
- [x] **Verify:** Query `select relname from pg_class where relforcerowsecurity` and confirm every account-private table from `0001_foundation.py` is listed.

---

### Task 0.13: Keycloak Realm as Code and Local Compose Service

Run Keycloak in local Compose on its own database and role, and define the `property-focus` realm in Git: verified-email sign-up, the public PKCE web client, the API audience, and the dynamic client registration controls for MCP clients (A4). Numbered 0.13 so existing task numbers stay stable; it runs after Task 0.3 and before Task 0.4.

**Modules:** root tooling, `infrastructure/keycloak`, `backend`
**Depends on:** 0.3
**Decisions:** A4, D28, A3, D49

**Files:**

```
compose.yaml                                             (modify)
.env.example                                             (modify)
infrastructure/keycloak/realm-property-focus.json        (create)
backend/tests/support/__init__.py                        (create)
backend/tests/support/keycloak.py                        (create)
backend/tests/integration/test_keycloak_realm.py         (create)
```

**Sub-tasks:**

- [x] **0.13.1** — Add to root `compose.yaml` a one-shot `keycloak-db-init` service (the pinned Postgres image running `psql` against `postgres`) that creates role `keycloak` and database `keycloak` owned by it when absent, so existing local volumes gain them too; and a `keycloak` service from `quay.io/keycloak/keycloak:26.8.1@sha256:3f3c5f8e03c614774b4bd9173b88391fefa4153bfc010d8012d59ad825377332` (re-check the digest when implementing) running `start-dev --import-realm` with `KC_DB=postgres` on the `keycloak` database, `KC_HOSTNAME=http://localhost:8080`, a dynamic backchannel hostname, health checks enabled, port `127.0.0.1:8080`, the realm file mounted read-only under `/opt/keycloak/data/import/`, a health check on `/health/ready`, and a dependency on `keycloak-db-init` completing. The bootstrap admin username and password come from `.env`; `.env.example` holds placeholders only.
- [x] **0.13.2** — Write `infrastructure/keycloak/realm-property-focus.json` (realm `property-focus`; no secret; `${...}` placeholders for hostnames, web redirect URIs and SMTP settings, which point at Mailpit locally): registration allowed, email verification required, login with email, no duplicate emails, brute-force detection on; client `property-focus-web` public, authorization code only, PKCE method `S256`, exact redirect URIs and web origins; a client scope `property-focus-api` with an audience mapper that adds `property-focus-api` to the web client's access tokens, and a realm default client scope `property-focus-mcp` that adds `property-focus-mcp` to dynamically registered clients' tokens (split 2026-11-04, owner review of PR #2, item 7); no client with the direct-access grant, the implicit flow or service accounts.
- [x] **0.13.3** — Add the dynamic client registration controls to the realm: anonymous client registration policies (Consent Required, only realm default client scopes, Full Scope off, the default protocol-mapper allowlist, Max Clients `${PF_KEYCLOAK_MAX_CLIENTS}` with default 100, and Trusted Hosts configured to check redirect URIs without restricting the requesting host, or replaced by the client policy below); a realm-wide client policy whose executors require PKCE `S256` and accept only `https` or `http` loopback redirect URIs (`localhost`, `127.0.0.1`, `[::1]`) with no wildcards; and rejection of a registration that asks for a confidential client. Report the Keycloak mechanism used for each control; this closes the plan's matching Research still required items.
- [x] **0.13.4** — Write `backend/tests/support/keycloak.py`: an admin client for the local Keycloak (bootstrap admin from `.env`) that creates synthetic users with verified email; `pkce_sign_in(user)`, which runs authorization code with PKCE `S256` by submitting the Keycloak login form over HTTP and exchanging the code; `register_client(metadata)`, which calls the registration endpoint named in the realm's discovery document; per-session token caching. It never uses the direct-access grant.
- [x] **0.13.5** — Write `test_keycloak_realm.py`: discovery returns issuer `http://localhost:8080/realms/property-focus` and a `registration_endpoint`; a self-registered user gets no tokens before verifying, the verification email arrives in Mailpit, and after verifying the access token carries `email` and `email_verified: true`; the web client rejects an authorization request without `code_challenge` and one with method `plain`; the web client's access tokens carry `aud` `property-focus-api` and `typ` `Bearer`, and a dynamically registered client's carry `property-focus-mcp`; no realm client enables the direct-access grant, the implicit flow or service accounts (read through the admin API); no secret-bearing field in the realm file holds a literal value; dynamic registration accepts a public client with a loopback redirect and rejects a non-loopback `http` redirect, a wildcard redirect, a custom scheme, `client_secret_basic`, and a registration beyond the cap (the test lowers the cap through the admin API and restores it); an accepted client's authorization request fails without PKCE and shows a consent screen.

**Validation:**

- [x] **Build:** `BE-CHECK` exits 0; `docker compose up -d postgres keycloak` reports `keycloak` healthy.
- [x] **Tests:** `BE-INT` passes with every `test_keycloak_realm.py` case in 0.13.5.
- [x] **Requirements:**
    - [x] "`infrastructure/keycloak/realm-property-focus.json` defines realm `property-focus` and holds no secret: no client secret, password, SMTP credential or private key. Environment-specific values (public hostnames, redirect URIs, SMTP host and user) use Keycloak's `${...}` environment placeholders."
    - [x] "Root `compose.yaml` runs a `keycloak` service from `quay.io/keycloak/keycloak:26.8.1` pinned by digest, in development mode with `--import-realm` and health checks, storing its data in its own `keycloak` database and `keycloak` role on the local Postgres … After `docker compose up -d postgres keycloak`, the realm's `/.well-known/openid-configuration` returns issuer `http://localhost:8080/realms/property-focus` (`test_keycloak_realm.py`)."
    - [x] "Self-registration is on and requires a verified email. A new user who has not followed the verification link, which Mailpit receives locally, gets no tokens; after verifying, the access token carries `email` and `email_verified: true` (`test_keycloak_realm.py`)."
    - [x] "The web client `property-focus-web` is public (no secret), allows only the authorization code grant, and requires PKCE method `S256`: an authorization request without `code_challenge`, or with method `plain`, fails."
    - [x] "Access tokens for every client carry the API audience (`PF_OIDC_AUDIENCE`, value `property-focus-api`; plan recommendation) through an audience mapper on a realm default client scope, so dynamically registered clients receive it too." Superseded 2026-11-04 (owner review of PR #2, item 7): the web client gets `property-focus-api` and dynamically registered clients get `property-focus-mcp` (`PF_MCP_AUDIENCE`); each surface refuses the other's token.
    - [x] "No client in the realm enables the direct-access (password) grant, the implicit flow or service accounts, in any environment. Integration and contract tests obtain user tokens through a scripted authorization code flow with PKCE against the local realm (`backend/tests/support/keycloak.py`)."
    - [x] "Dynamic client registration is a security control (plan recommendation). … `test_keycloak_realm.py` asserts each: a registration with a non-loopback `http` redirect URI, a wildcard redirect, a custom scheme, a confidential authentication method, or one beyond the cap fails; an accepted registration yields a public client whose authorization request fails without PKCE and shows a consent screen."
- [x] **Verify:** `curl -s http://localhost:8080/realms/property-focus/.well-known/openid-configuration` shows the issuer and a `registration_endpoint`. After `docker compose restart keycloak`, a user created before the restart still signs in (data lives in Postgres, not the development H2 store). `uv run python backend/scripts/check_fixture_secrets.py` exits 0 with the realm file committed.

---

### Task 0.4: OIDC Token Verifier, Account Provisioning and REST Skeleton

> **Note (2026-11-02, A6):** Task 0.15 replaces the hand-written JWKS cache and refetch from 0.4.2 with PyJWT's `PyJWKClient`; every other check stays. The ticks below stand.

Validate access tokens from the configured OIDC issuer (the local Keycloak realm) with one generic verifier, provision one user and one tenant on the first authenticated request, and stand up the FastAPI app with problem responses and health routes.

**Modules:** `backend`, `fixtures`
**Depends on:** 0.3, 0.13
**Decisions:** D28, A4, D5 (surviving parts), A1

**Files:**

```
backend/src/property_focus/config.py                      (modify)
backend/src/property_focus/adapters/auth/__init__.py      (create)
backend/src/property_focus/adapters/auth/oidc.py          (create)
backend/src/property_focus/application/context.py         (create)
backend/src/property_focus/adapters/rest/__init__.py      (create)
backend/src/property_focus/adapters/rest/app.py           (create)
backend/src/property_focus/adapters/rest/schemas.py       (create)
backend/src/property_focus/adapters/rest/routes.py        (create)
backend/src/property_focus/cli.py                         (modify)
.env.example                                              (modify)
fixtures/synthetic/accounts.json                          (create)
backend/tests/unit/test_config.py                         (modify)
backend/tests/unit/test_auth.py                           (create)
backend/tests/integration/test_tenancy.py                 (modify)
backend/tests/contract/__init__.py                        (create)
backend/tests/contract/test_api.py                        (create)
```

**Sub-tasks:**

- [x] **0.4.1** — Add the OIDC settings to `config.py` with the plan's Authentication configuration defaults: `PF_OIDC_ISSUER`, `PF_OIDC_JWKS_URL` (optional), `PF_OIDC_AUDIENCE`, `PF_OIDC_AUDIENCE_CLAIM`, `PF_OIDC_TOKEN_TYPE`, `PF_OIDC_ALGORITHMS` (only `RS256` and `ES256` accepted) and `PF_OIDC_CLOCK_SKEW_SECONDS`; require an `https` issuer outside development. Put local Keycloak values in `.env.example` and add validation cases to `test_config.py`.
- [x] **0.4.2** — Implement the generic verifier in `adapters/auth/oidc.py`: load the issuer's discovery document (or the `PF_OIDC_JWKS_URL` override), cache the JWKS and refetch at most once a minute on an unknown `kid`, then check the signature, `iss`, the configured audience claim, `exp` and `nbf` with the configured skew, the configured token-type claim, and the algorithm allowlist (reject `none` and every `HS*`). Call no identity-provider API beyond discovery and JWKS. Accept the signed fixture issuer only when `PF_AUTH_FIXTURE_ISSUER` is set, which `config.py` already refuses outside development; only unit tests use it.
- [x] **0.4.3** — Implement account resolution: map issuer and `sub` to `users`; on the first authenticated request create the `users` row and its owned tenant in one transaction; copy the verified email from the token only when `email_verified` is true; concurrent first requests create one pair.
- [x] **0.4.4** — Implement `ExecutionContext` in `context.py` (principal, tenant, entitlement IDs, correlation ID, roles from `user_roles`); the tenant comes only from the validated token.
- [x] **0.4.5** — Implement `rest/app.py` factory and middleware (correlation ID, `application/problem+json` errors with the `Problem` schema, `404` body identical for absent and inaccessible IDs), `/health/live`, `/health/ready`, and `GET /v1/workflow-runs/{id}` as the first authenticated route.
- [x] **0.4.6** — Implement `cli seed --dataset synthetic` loading `fixtures/synthetic/accounts.json` (synthetic users only, no credentials).
- [x] **0.4.7** — Tests: `test_auth.py` (unit; fixture issuer and an in-test JWKS): expired, `nbf` beyond the skew, wrong audience, wrong issuer and wrong token type (an ID token with `typ` `ID`) fail; `alg` `none`, `HS256` and an unknown `kid` fail; after key rotation the new key validates after one refetch and a removed key fails; unknown-`kid` refetches are rate limited; the audience claim and token-type claim follow configuration; the fixture issuer is rejected without the development flag. `test_tenancy.py` (tokens from the local Keycloak through `tests/support/keycloak.py`): the first request provisions exactly one user and tenant, a repeat creates nothing, concurrent first requests create one pair; account B reading account A's workflow run gets the same `404` body as a random ID. `test_api.py`: problem shape, `401` without a token, a Keycloak-issued token accepted by `GET /v1/workflow-runs/{id}`.

**Validation:**

- [x] **Build:** `BE-CHECK` exits 0.
- [x] **Tests:** `BE-UNIT` and `BE-INT` pass with the cases in 0.4.7.
- [x] **Requirements:**
    - [x] "Phase 0: the first authenticated request from a new subject (issuer and `sub`) of the configured identity provider creates one `users` row and one tenant owned by that user; repeating the request creates nothing (`test_tenancy.py`)."
    - [x] "Both adapters validate access tokens with one generic OIDC verifier in `adapters/auth/oidc.py`, configured per environment … Expired, not-yet-valid, wrong-audience, wrong-issuer and wrong-token-use tokens fail, as do tokens signed with `none` or `HS256` or by a key absent from the JWKS (`backend/tests/unit/test_auth.py`). No module under `adapters/` calls an identity-provider API beyond the issuer's discovery document and JWKS." (REST part; Task 0.5 binds MCP)
    - [x] "The verifier caches the JWKS, refetches it at most once a minute when a token names an unknown `kid`, and rejects a key removed from the JWKS after the next refetch (`test_auth.py`)."
    - [x] "A token for account A cannot read, infer existence of, mutate, export or obtain signed URLs for account B's private records … each attempt returns `404` with the same body as a nonexistent ID (`test_tenancy.py`)." (phase 0 scope: workflow runs; extended in later phases)
    - [x] "Unit tests may use the signed fixture issuer that `config.py` already supports (`PF_AUTH_FIXTURE_ISSUER`, refused outside development). `make dev` and the integration, contract and E2E suites use the local Keycloak realm."
- [x] **Verify:** Run `uv run --package property-focus python -m property_focus.cli seed --dataset synthetic` twice on a migrated database; the user and tenant counts do not change on the second run. `grep -rniE "cognito|keycloak" backend/src/property_focus/adapters/auth` returns nothing.

---

### Task 0.5: MCP Adapter Skeleton with OAuth Discovery

> **Note (2026-11-02):** This task built the legacy, initialize-based protocol path and dynamic registration only. Task 0.14 adds the MCP 2026-07-28 path on the same endpoint; registration stays dynamic (Client ID Metadata Documents deferred, CP-9.D). The ticks below stand.
>
> **Note (2026-11-02, A6):** Task 0.16 rebuilds this adapter on the official MCP Python SDK; the SDK then owns the transport, the `401` challenge and protected-resource metadata routes.

Serve MCP over streamable HTTP at `/mcp` with protected-resource metadata naming the Keycloak issuer, the shared token check, and a client-agnostic contract harness whose generic client registers itself dynamically. No product tools ship yet.

**Modules:** `backend`
**Depends on:** 0.4, 0.13
**Decisions:** D28, D29, A3, A4

**Files:**

```
backend/src/property_focus/adapters/mcp/__init__.py       (create)
backend/src/property_focus/adapters/mcp/server.py         (create)
backend/src/property_focus/adapters/auth/oidc.py          (modify)
backend/tests/contract/conftest.py                        (create)
backend/tests/contract/mcp_client.py                      (create)
backend/tests/contract/test_mcp.py                        (create)
```

**Sub-tasks:**

- [x] **0.5.1** — Implement `mcp/server.py`: streamable HTTP transport at `/mcp`, stateless mode, JSON responses, no product tools, calling application services directly (never FastAPI over HTTP).
- [x] **0.5.2** — Publish `/.well-known/oauth-protected-resource` naming `PF_OIDC_ISSUER` as the authorization server (the Keycloak realm locally); return `401` with a `WWW-Authenticate` header naming that metadata URL for unauthenticated MCP requests.
- [x] **0.5.3** — Bind MCP authentication to the same `oidc.py` checks and account resolution as REST.
- [x] **0.5.4** — Write `tests/contract/conftest.py`, which requires the local Keycloak container and provides synthetic users through `tests/support/keycloak.py`, and `mcp_client.py`, a generic client that follows the MCP authorization specification with no preconfigured client ID: it reads the challenge, fetches the protected-resource metadata, resolves authorization-server metadata by trying the URL forms in the specification's order, registers through the dynamic registration endpoint, and completes authorization code with PKCE `S256` through the scripted sign-in.
- [x] **0.5.5** — Write the parity harness in `test_mcp.py`: a helper that runs one use case through REST and MCP for the same principal and compares canonical results; later phases add one case per tool.
- [x] **0.5.6** — Write the `test_mcp.py` flow test against local Keycloak: read the `401` challenge, fetch protected-resource and authorization-server metadata, register dynamically, complete authorization code with PKCE, then make an authenticated MCP call (`initialize` and `tools/list`). Add rejection cases for an expired token and a token without the `property-focus-mcp` audience (including the web client's `property-focus-api` token). Phase 1 extends the flow to a tool call.
- [x] **0.5.7** — Record in the coder's report which authorization-server metadata URL forms Keycloak 26.8 serves natively for the realm issuer; Task 0.10 maps any missing form in the Caddyfile.

**Validation:**

- [x] **Build:** `BE-CHECK` exits 0.
- [x] **Tests:** `BE-INT` passes with the 401/metadata test, the registration and PKCE flow test, and the expired-token and wrong-audience MCP rejections.
- [x] **Requirements:**
    - [x] "MCP serves streamable HTTP at `/mcp`. An unauthenticated MCP request returns 401 with a `WWW-Authenticate` header naming `/.well-known/oauth-protected-resource`, whose metadata names the configured issuer: the Keycloak realm in development, tests and dogfood. A Keycloak access token issued through authorization code with PKCE to a dynamically registered MCP client succeeds, validated by the same `adapters/auth/oidc.py` checks as REST (`backend/tests/contract/test_mcp.py`)."
    - [x] "MCP access is client-agnostic (A3). `test_mcp.py` drives the whole flow against the local Keycloak container with a generic test client … registers itself at the realm's dynamic client registration endpoint, completes authorization code with PKCE, and calls a tool. No module under `adapters/mcp/` or `adapters/auth/` reads a client name, user agent or other client identity, and no configuration value names an MCP client."
    - [x] "Phase 0 ships the MCP adapter skeleton: `adapters/mcp/server.py` with no product tools, … protected-resource metadata naming the Keycloak issuer, the realm's dynamic registration policies and the `test_mcp.py` parity harness."
    - [x] "Transport modules contain validation/serialization and authentication binding; domain calculations remain outside adapters."
- [x] **Verify:** `grep -rniE "user.agent|client_name|client-name" backend/src/property_focus/adapters/mcp backend/src/property_focus/adapters/auth backend/src/property_focus/config.py` returns nothing.

---

### Task 0.6: Task Runtime, Launcher, Workflow Runs and Outbox

Make every background unit of work an on-demand task: `TaskInput`, the single entrypoint, launchers, workflow state with leases and fencing, idempotency, the `OutboxPublisher` and the `outbox-sweep` and `repair` scheduled kinds.

**Modules:** `backend`
**Depends on:** 0.3
**Decisions:** D26, D25, D54, D55

**Files:**

```
backend/src/property_focus/runtime/__init__.py             (create)
backend/src/property_focus/runtime/run_task.py             (create)
backend/src/property_focus/runtime/lambda_handler.py       (create)
backend/src/property_focus/runtime/launcher.py             (create)
backend/src/property_focus/runtime/scheduled.py            (create)
backend/src/property_focus/application/workflows.py        (create)
backend/src/property_focus/application/ports.py            (modify)
backend/src/property_focus/cli.py                          (modify)
backend/tests/unit/test_workflow_runs.py                   (create)
backend/tests/integration/test_workflows.py                (create)
```

**Sub-tasks:**

- [x] **0.6.1** — Define `TaskInput` and the task registry; `run_task.py` loads tenant and scope from the trusted workflow run, never from queue fields. Tasks use scoped workload credentials, never a user's browser token.
- [x] **0.6.2** — Implement workflow state transitions (`QUEUED`, `RUNNING`, `COMPLETE`, `PARTIAL`, `WAITING_FOR_USER`, `FAILED`, `CANCELLED`) per the plan's state diagram, leases with expiry and fencing tokens that reject stale completion.
- [x] **0.6.3** — Implement idempotency records: a duplicate key with the same input returns the existing result (`duplicate`); the same key with a different input returns `409 idempotency_conflict`.
- [x] **0.6.4** — Persist step failures in `workflow_steps` (class, stable code, retryability, safe explanation, attempt count, correlation ID) and schedule retries through the Task 0.2 policy.
- [x] **0.6.5** — Implement `TaskLauncher` (`launcher.py`): dogfood subprocess launcher, Lambda invoke, Step Functions start and Fargate RunTask; and `lambda_handler.py` delegating to `run_task.py`.
- [x] **0.6.6** — Implement `OutboxPublisher` in `workflows.py`: after commit, launch each outbox event the task wrote and mark it published. Register scheduled kinds `outbox-sweep` (launch events pending longer than 30 seconds) and `repair` (expired leases, stranded outbox events) in `scheduled.py`; add `cli run-task <kind>`.
- [x] **0.6.7** — Unit tests in `test_workflow_runs.py`: allowed and rejected transitions, fencing, idempotency conflict.
- [x] **0.6.8** — Integration tests in `test_workflows.py`: one handler through the dogfood launcher and the Lambda adapter gives identical committed results; a crash between commit and launch yields exactly one downstream result launched by the next sweep; a crash after publish before acknowledge; duplicate delivery; lease expiry recovery and stale completion rejection.

**Validation:**

- [x] **Build:** `BE-CHECK` exits 0.
- [x] **Tests:** `BE-UNIT` and `BE-INT` pass with the cases in 0.6.7 and 0.6.8.
- [x] **Requirements:**
    - [x] "Service tasks use scoped workload credentials rather than a user's browser token." and `TaskInput`: "Tenant/scope MUST be loaded from the trusted workflow run, not queue fields."
    - [x] "Every background unit of work enters through `runtime/run_task.py` with a `TaskInput`; the same handler runs under `runtime/lambda_handler.py`, a Fargate task and a dogfood process. `backend/tests/integration/test_workflows.py` runs one handler through the dogfood launcher and the Lambda adapter and asserts identical committed results."
    - [x] "`OutboxPublisher` in `application/workflows.py`: after commit, the committing task calls `TaskLauncher.launch` for each outbox event it wrote and marks it published. `outbox-sweep` … runs once a minute and launches events pending longer than 30 seconds. A crash injected between commit and launch leads to exactly one committed downstream result, launched by the next sweep (`test_workflows.py`)."
    - [x] "Crash after database commit but before publish, after publish but before acknowledge … retries preserve one committed domain result."
    - [x] "Duplicate queue delivery uses a unique idempotency key; same key/different input returns conflict. Lease expiry permits recovery and fencing tokens reject stale completion."
    - [x] "Each failed step persists failure class, stable code, retryability, safe explanation, attempt count and correlation ID in `workflow_steps`."
- [x] **Verify:** `uv run --package property-focus python -m property_focus.cli run-task outbox-sweep` exits 0 and returns within a few seconds on an empty outbox.

---

### Task 0.7: Telemetry Baseline

Install OpenTelemetry tracing and structured JSON logs with redaction, and propagate trace context across task launches.

**Modules:** `backend`
**Depends on:** 0.6
**Decisions:** D50, D49

**Files:**

```
backend/src/property_focus/adapters/telemetry.py          (create)
backend/src/property_focus/adapters/rest/app.py           (modify)
backend/src/property_focus/adapters/mcp/server.py         (modify)
backend/src/property_focus/runtime/run_task.py            (modify)
backend/src/property_focus/application/workflows.py       (modify)
backend/tests/integration/test_telemetry.py               (create)
```

**Sub-tasks:**

- [x] **0.7.1** — Implement `telemetry.py`: OTel tracer and meter setup with OTLP export configured by settings; JSON log formatter carrying trace and correlation IDs.
- [x] **0.7.2** — Implement redaction: email bodies, cookies, tokens, financial details and every value returned by `SecretProvider` never reach logs or span attributes.
- [x] **0.7.3** — Propagate trace context through outbox events and workflow runs; link spans across async hops with span links.
- [x] **0.7.4** — Instrument the REST app, MCP server and `run_task.py`.
- [x] **0.7.5** — Tests in `test_telemetry.py`: seeded secret values never appear in captured logs; logs are JSON with trace and correlation IDs; a REST request that launches a task produces linked spans.

**Validation:**

- [x] **Build:** `BE-CHECK` exits 0.
- [x] **Tests:** `BE-INT` passes with the redaction, log-format and span-link tests.
- [x] **Requirements:**
    - [x] "Logs are structured JSON with trace and correlation IDs; email bodies, cookies, tokens and financial details are redacted."
    - [x] "A test seeds known secret values and asserts they never appear in logs, model request payloads, analysis run records or stored evidence." (logs part; Task 1.1 adds evidence, Task 3.7 adds model payloads and analysis runs)
    - [x] "Secrets never enter prompts, logs, fixtures, evidence or analysis records." (logs part)
- [x] **Verify:** Start the API with `docker compose up -d otel`, send one request, and confirm one JSON log line contains `trace_id` and `correlation_id`.

---

### Task 0.8: Frontend Skeleton and OpenAPI/RTK Query Generation

Create the `@property-focus/web` package with the store, PKCE session, sign-in boundary and the deterministic schema export and client generation pipeline.

**Modules:** `frontend`, `backend`
**Depends on:** 0.4
**Decisions:** D20, D29

**Files:**

```
frontend/package.json                       (create)
frontend/index.html                         (create)
frontend/vite.config.ts                     (create)
frontend/tsconfig.json                      (create)
frontend/eslint.config.js                   (create)
frontend/src/main.tsx                       (create)
frontend/src/app/store.ts                   (create)
frontend/src/app/router.tsx                 (create)
frontend/src/auth/session.ts                (create)
frontend/src/api/openapi.json               (create)
frontend/src/api/codegen.ts                 (create)
frontend/src/api/baseApi.ts                 (create)
frontend/src/api/generated.ts               (create)
frontend/src/styles.css                     (create)
frontend/tests/session.test.ts              (create)
backend/scripts/export_openapi.py           (create)
Makefile                                    (modify)
pnpm-lock.yaml                              (modify)
```

**Sub-tasks:**

- [x] **0.8.1** — Create `frontend/package.json` with scripts `dev`, `build`, `lint`, `typecheck`, `test`, `generate:api` and dependencies including `@rtk-query/codegen-openapi`; Vite, strict TypeScript and ESLint configs.
- [x] **0.8.2** — Implement `auth/session.ts`: authorization code with PKCE `S256` against the configured OIDC issuer, discovered from its `/.well-known/openid-configuration` (the local Keycloak realm and client `property-focus-web` in development), state check, token refresh and sign-out. No provider-specific code.
- [x] **0.8.3** — Implement `app/store.ts`, `api/baseApi.ts` (auth header, problem error mapping, cache tags) and `app/router.tsx` with a sign-in boundary.
- [x] **0.8.4** — Write `backend/scripts/export_openapi.py`: build the FastAPI schema without live services and write it with stable key order.
- [x] **0.8.5** — Configure `api/codegen.ts`; generate `generated.ts`; add `make check-generated` that regenerates both files and fails on any diff.
- [x] **0.8.6** — Write `frontend/tests/session.test.ts` (PKCE verifier and challenge, state mismatch rejection, expiry refresh).

**Validation:**

- [x] **Build:** `INSTALL`, `GEN` and `FE` exit 0.
- [x] **Tests:** `pnpm --filter @property-focus/web test --run` passes `session.test.ts`.
- [x] **Requirements:**
    - [x] "Regeneration of the OpenAPI schema and RTK Query client produces no uncommitted diff after a clean build."
    - [x] "Engineers generate the frontend's RTK Query client from FastAPI's OpenAPI schema (D29)." and "Do not edit `/frontend/src/api/generated.ts` by hand."
    - [x] "`backend`, `frontend` and `infrastructure/cdk` each build and test with their own commands without building the others." (frontend part)
- [x] **Verify:** Run `GEN` twice in a clean checkout; `git status --porcelain frontend/src/api` is empty after both runs.

---

### Task 0.9: CDK Identity Stack and `deploy-identity`

Create the `@property-focus/infra` CDK app with only the Cognito identity stack, deployable alone to the dogfood AWS account.

> **Note (A4, 2026-11-02):** Completed before A4. The identity stack and its tests are retained for AWS, but its deployment moved to phase 9 (Task 9.7). Phase 0, local development and dogfood use Keycloak (Task 0.13), and the live `deploy-identity` check below is not run in phase 0. Task 0.10 replaces the identity deploy step in `deploy-dogfood.yml`.

**Modules:** `infrastructure/cdk`
**Depends on:** 0.1
**Decisions:** D20, D28, A3, D48

**Files:**

```
infrastructure/cdk/package.json                 (create)
infrastructure/cdk/tsconfig.json                (create)
infrastructure/cdk/cdk.json                     (create)
infrastructure/cdk/bin/property-focus.ts        (create)
infrastructure/cdk/lib/identity-stack.ts        (create)
infrastructure/cdk/test/identity.test.ts        (create)
.github/workflows/deploy-dogfood.yml            (create)
Makefile                                        (modify)
pnpm-lock.yaml                                  (modify)
```

**Sub-tasks:**

- [x] **0.9.1** — Create the CDK package with `build`, `synth`, `test`, `diff` scripts; `bin/property-focus.ts` selects the environment from context and enforces an account allowlist.
- [x] **0.9.2** — Implement `identity-stack.ts` (`PropertyFocus-Identity-<env>`): user pool with self sign-up and verified email, hosted UI domain, web app client (authorization code with PKCE, no secret), and one public MCP app client (PKCE) shared by all MCP clients, with a configurable callback-URL allowlist.
- [x] **0.9.3** — Write `identity.test.ts`: self sign-up with email verification, exactly two app clients, no client secret, PKCE code flow only.
- [x] **0.9.4** — Add `make deploy-identity ENV=dogfood` running `pnpm --filter @property-focus/infra exec cdk deploy PropertyFocus-Identity-dogfood --exclusively` against the allowlisted dogfood account only.
- [x] **0.9.5** — Create `deploy-dogfood.yml`: GitHub OIDC, protected environment, runs `deploy-identity` (application deployment steps arrive in Task 0.10).

**Validation:**

- [x] **Build:** `INFRA` exits 0 and synthesizes only `PropertyFocus-Identity-*` stacks.
- [x] **Tests:** `pnpm --filter @property-focus/infra test --run` passes `identity.test.ts`.
- [x] **Requirements:**
    - [x] "Phase 0: `make deploy-identity ENV=dogfood` deploys only `infrastructure/cdk/lib/identity-stack.ts` (user pool, web app client, MCP app client) to the dogfood AWS account; `cdk diff` for that command lists no resource outside the identity stack."
    - [x] "`identity-stack.ts` defines one pre-registered public MCP app client, and every MCP client uses its client ID; no code, configuration or test targets a particular client."
    - [x] "`backend`, `frontend` and `infrastructure/cdk` each build and test with their own commands without building the others." (infra part)
- [x] **Verify:** `pnpm --filter @property-focus/infra exec cdk ls` prints only identity stacks. **Live (explicit user approval required):** `make deploy-identity ENV=dogfood`.

---

### Task 0.14: Dual-era MCP Protocol

> **Deferred (user decision 2026-11-02, option C).** Keycloak's Client ID Metadata Documents (`cimd`) and `resource-indicators` features stay off, and dynamic client registration stays exactly as Task 0.13 built it. This task therefore holds only the protocol work. Removed from it:
>
> - 0.14.5: enabling `--features=cimd,resource-indicators` in root `compose.yaml`, the realm's `client-id-metadata-document` client policy and the `${PF_KEYCLOAK_CIMD_RESOURCES}` placeholder in the realm file and `.env.example`.
> - 0.14.6: `backend/tests/support/client_metadata.py` and its fixture in `tests/contract/conftest.py`.
> - The metadata-document sign-in in 0.14.7.
> - The metadata-document sign-in case and the "features on" rerun of dynamic registration in 0.14.8.
> - 0.14.9: the `test_keycloak_realm.py` metadata-document cases.
>
> Why, measured against Keycloak 26.8.1: the realm-wide `secure-client-uris-pattern` executor rejects every CIMD sign-in, and no executor can allow `http` only on loopback for CIMD clients without changing the registration redirect rule; CIMD accepts only hand-listed trusted domains; and `resource-indicators` sets the token's `aud` to the resource URL alone, which drops `property-focus-api` and breaks dynamic registration with `invalid_target`. The plan records the decision (Resolved list) and the accepted risk that CIMD-only clients cannot connect. The remaining sub-tasks keep their numbers. Re-evaluation: checkpoint CP-9.D.

> **Note (2026-11-02, A6):** Task 0.16 replaces this task's hand-rolled protocol code with the official MCP Python SDK. The requirement "The adapter stays hand-rolled on Starlette: `backend/pyproject.toml` and `uv.lock` gain no `mcp` package" and this task's `mcp` grep in Verify are superseded by A6. The SDK serves 2024-11-05 (with sign-in) and may answer some garbled modern requests with other specification codes. The ticks below stand as the record of what this task delivered.

Extend the hand-rolled MCP adapter so the one stateless `/mcp` endpoint also serves MCP 2026-07-28 clients beside the initialize-based revisions. Every era signs in through the existing dynamic-registration flow. Numbered 0.14 so existing task numbers stay stable; it runs after Task 0.7, which also edits `server.py`, and before Task 0.10.

**Modules:** `backend`
**Depends on:** 0.5, 0.7, 0.13
**Decisions:** A3, A4, D28, D29 (the dual-era design is a plan recommendation under A3; dynamic registration as the only registration path is the user's option C decision of 2026-11-02)

**Files:**

```
backend/src/property_focus/adapters/mcp/server.py        (modify)
backend/tests/contract/mcp_client.py                     (modify)
backend/tests/contract/test_mcp.py                       (modify)
backend/tests/unit/test_mcp_protocol.py                  (create)
```

**Sub-tasks:**

- [x] **0.14.1** — In `server.py`, after the `Origin` and bearer-token checks, classify each POST: an `initialize` request, or one whose `params._meta` has no `io.modelcontextprotocol/protocolVersion`, takes the legacy path exactly as Task 0.5 built it; any other request takes the modern path. Hold the legacy set (`2025-11-25`, `2025-06-18`, `2025-03-26`) apart from the full supported set (`2026-07-28` first). `initialize` still answers at most `2025-11-25`. Keep the stateless invariants: no `Mcp-Session-Id`, one `application/json` response, `202` for notifications, `405` on `GET` and `DELETE`.
- [x] **0.14.2** — Answer a request whose `MCP-Protocol-Version` header or `_meta` version is outside the four supported revisions (including `2024-11-05`) with HTTP `400` and JSON-RPC `-32022`, `data` `{"supported": [...four, newest first], "requested": "<version sent>"}`, on both paths, replacing the current `-32600`; keep the supported versions in the message.
- [x] **0.14.3** — Validate modern requests: `_meta` must carry `io.modelcontextprotocol/clientCapabilities`; `MCP-Protocol-Version` must equal the `_meta` version; `Mcp-Method` must equal `method`; for `tools/call`, `resources/read` and `prompts/get`, `Mcp-Name` must equal the target the body names (`params.name` for `tools/call`), after decoding the `=?base64?…?=` sentinel. A legacy-path request that sends `MCP-Protocol-Version: 2026-07-28` is a mismatch. Each failure is HTTP `400` with JSON-RPC `-32020`.
- [x] **0.14.4** — Serve modern methods: `server/discover` (`supportedVersions` newest first, `capabilities` `{"tools": {"listChanged": false}}`, `serverInfo` with `name` `property-focus` and the package version in the result's `_meta`, `ttlMs` `300000`, `cacheScope` `"private"`); `tools/list` with `ttlMs` `300000` and `cacheScope` `"private"`; `tools/call` through the existing `_call_tool`. Every modern result carries `resultType` with the 2026-07-28 value for a complete result. `ping`, `logging/setLevel` and every other method return `-32601`. Add no `mcp` SDK dependency.
- [x] **0.14.7** — Extend `mcp_client.py`, keeping it free of any client-specific configuration, with a protocol choice; it keeps registering dynamically. `2025-06-18` and `2025-11-25` keep the `initialize` handshake. `2026-07-28` sends the `_meta` version and capabilities with matching `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` headers, has an optional `discover()` call, and falls back to `initialize` only on a `400` whose JSON-RPC code is not `-32020`, `-32021` or `-32022`.
- [x] **0.14.8** — Add `test_mcp.py` cases: a 2025-06-18 handshake client and a 2025-11-25 client (`initialize`, `notifications/initialized`, `tools/list`, a parity-harness tool call) pass; `initialize` requesting `2026-07-28` answers `2025-11-25`; a 2026-07-28 client gets identical `tools/list` and tool-call results with and without a prior `server/discover`, with the `server/discover` fields, `resultType`, `ttlMs`, `cacheScope` `"private"` and no session ID; a missing `Mcp-Method`, a differing `Mcp-Method`, a differing `Mcp-Name` and a `2026-07-28` header without `_meta` each give `400` `-32020`, while a Base64-sentinel `Mcp-Name` that decodes to the right name passes; `2024-11-05` and `2027-01-01` give `400` `-32022` with `data.supported` and `data.requested`; modern `ping` and `logging/setLevel` give `-32601`; the parity harness gives the same result through both paths; the existing dynamic-registration flow still passes. Update the existing unsupported-version case to expect `-32022`. Add unit cases in `tests/unit/test_mcp_protocol.py` for era classification, the supported-version order, Base64 `Mcp-Name` decoding, and the `-32020` and `-32022` refusals.

**Validation:**

- [x] **Build:** `BE-CHECK` exits 0; `docker compose up -d postgres keycloak` reports `keycloak` healthy with no `--features` flag.
- [x] **Tests:** `BE-UNIT` and `BE-INT` pass with the cases in 0.14.8 and every existing `test_mcp.py` and `test_keycloak_realm.py` case.
- [x] **Requirements:**
    - [x] "MUST: Serve MCP protocol revisions 2025-03-26, 2025-06-18, 2025-11-25 and 2026-07-28 on the one stateless `/mcp` endpoint (A3; the dual-era design is a plan recommendation)."
    - [x] "Legacy path: an `initialize` request, or a request whose `params._meta` carries no `io.modelcontextprotocol/protocolVersion`, is served as the initialize-based revisions define … A 2025-06-18 client … and a 2025-11-25 client both pass (`test_mcp.py`)."
    - [x] "Modern path: a request whose `params._meta` carries `io.modelcontextprotocol/protocolVersion` is served statelessly per 2026-07-28, with no prior `initialize`. It must also carry `io.modelcontextprotocol/clientCapabilities` in `_meta`, an `MCP-Protocol-Version` header equal to the `_meta` version, an `Mcp-Method` header equal to the body's `method`, and, for `tools/call`, an `Mcp-Name` header equal to `params.name`, compared after decoding the Base64 sentinel form `=?base64?…?=` …"
    - [x] "A modern request with a missing or mismatched header, or a request that sends `MCP-Protocol-Version: 2026-07-28` without the `_meta` version, gets HTTP `400` with JSON-RPC error `-32020` (HeaderMismatch)."
    - [x] "A request whose `MCP-Protocol-Version` header or `_meta` version names a revision outside the four, including `2024-11-05`, gets HTTP `400` with JSON-RPC error `-32022` (UnsupportedProtocolVersionError) and `data` `{"supported": ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"], "requested": "<version sent>"}`."
    - [x] "Modern `server/discover` returns `supportedVersions` (the four revisions, newest first), `capabilities` `{"tools": {"listChanged": false}}`, `serverInfo` … in the result's `_meta`, `ttlMs` `300000` and `cacheScope` `"private"`. … Modern `ping`, `logging/setLevel` and every other method the revision removed or the adapter does not serve return JSON-RPC `-32601`. A 2026-07-28 client calls `tools/list` and a tool once after `server/discover` and once without it, with identical results (`test_mcp.py`)."
    - [x] "Both paths keep the transport invariants … and the same parity-harness result for a tool through either path (`test_mcp.py`)." and "The adapter stays hand-rolled on Starlette: `backend/pyproject.toml` and `uv.lock` gain no `mcp` package."
    - [x] "Client ID Metadata Documents are deferred (user decision, 2026-11-02, option C; see Risks). The realm's authorization-server metadata … does not advertise `client_id_metadata_document_supported` and does advertise `registration_endpoint`, so the generic test client registers dynamically. It sends the MCP server's URL as `resource`, and its access token carries `aud` `property-focus-api` and passes the shared check (`test_mcp.py`)." Superseded 2026-11-04 (owner review of PR #2, item 7): the token carries `aud` `property-focus-mcp`.
- [x] **Verify:** `curl -s http://localhost:8080/.well-known/oauth-authorization-server/realms/property-focus` shows a `registration_endpoint`, and piping it to `grep -c client_id_metadata_document_supported` prints `0`. The dynamic-registration flow test in `test_mcp.py` passes in `BE-INT`. `grep -nE 'cimd|resource-indicators' compose.yaml infrastructure/keycloak/realm-property-focus.json` returns nothing. `grep -nE '^name = "mcp"$' uv.lock` and `grep -n '"mcp' backend/pyproject.toml` return nothing. The Task 0.5 verify grep (`user.agent|client_name|client-name` under `adapters/mcp`, `adapters/auth` and `config.py`) still returns nothing.

---

### Task 0.10: Dogfood Runtime Manifests, Images and MiniStack Inventory

> **Note (2026-11-02, A5):** Task 0.17 replaces Caddy's ACME certificates with a Cloudflare Origin CA certificate, limits the origin to Cloudflare's ranges and runs the smoke check through Cloudflare. The ticks below stand.

Create the backend image, `make dev`, the dogfood Compose override with Keycloak, scheduler crontab, Caddy routes, OTel collector, deploy script with realm import and apply, the no-long-running-worker test, the smoke script and the MiniStack compatibility inventory.

**Modules:** `infrastructure/docker`, `infrastructure/dogfood`, `infrastructure/keycloak`, `backend`, `docs`
**Depends on:** 0.5, 0.6, 0.7, 0.8, 0.13, 0.14; CP-0.B (live dogfood steps only)
**Decisions:** D19, D26, D27, D50, A4, D49, A3

**Files:**

```
compose.yaml                                         (modify)
infrastructure/docker/backend.Dockerfile             (create)
infrastructure/docker/keycloak.Dockerfile            (create)
infrastructure/dogfood/compose.yaml                  (create)
infrastructure/dogfood/scheduler.crontab             (create)
infrastructure/dogfood/Caddyfile                     (create)
infrastructure/dogfood/otel-collector.yaml           (create)
infrastructure/dogfood/deploy.sh                     (create)
backend/scripts/smoke_dogfood.py                     (create)
backend/scripts/compatibility_report.py              (create)
backend/scripts/keycloak_realm.py                    (create)
backend/tests/unit/test_runtime_manifests.py         (create)
backend/tests/integration/test_keycloak_realm.py     (modify)
docs/compatibility.md                                (create)
docs/operations.md                                   (create)
.github/workflows/deploy-dogfood.yml                 (modify)
Makefile                                             (modify)
```

**Sub-tasks:**

- [x] **0.10.1** — Write `backend.Dockerfile` with API, MCP, scheduler and task stages; add `api`, `mcp` and `scheduler` services to root `compose.yaml`, configured with the local Keycloak issuer and the in-Compose JWKS URL.
- [x] **0.10.2** — Add `make dev`: API on loopback 8000, MCP on loopback 8001, Vite on 5173 and the local scheduler, with the local Keycloak as issuer; refuse non-development configuration.
- [x] **0.10.3** — Write `infrastructure/dogfood/compose.yaml` (api, mcp, scheduler, keycloak, otel stack), `scheduler.crontab` (each line runs `property_focus.cli run-task <kind>`: `outbox-sweep` each minute, `repair` every 5 minutes; later phases add `imap-poll`, `invalidation-batch`, `backup`), `Caddyfile` (TLS; web, `/v1` and `/mcp` on the application hostname; on `auth.<dogfood domain>` forward only `/realms/property-focus/*`, `/resources/*` and the authorization-server metadata mappings recorded in 0.5.7, and return `404` for `/admin/*` and `/realms/master/*`) and `otel-collector.yaml`.
- [x] **0.10.4** — Write `keycloak.Dockerfile`: from the image digest pinned in root `compose.yaml`, run `kc.sh build` for Postgres with health checks, with no `--features` flag (`cimd` and `resource-indicators` stay off by user decision 2026-11-02), and copy the realm file into `/opt/keycloak/data/import/`. The dogfood `keycloak` service runs `start --optimized --import-realm` with forwarded proxy headers and hostname `https://auth.<dogfood domain>`, binds its HTTP and management ports to `127.0.0.1` only, uses its own `keycloak` database and role on the dogfood Postgres, and takes its bootstrap admin, database and SMTP credentials from deployment-injected secrets.
- [x] **0.10.5** — Write `backend/scripts/keycloak_realm.py` with `apply` (substitute the realm file's placeholders from the environment, then update realm settings, clients, client scopes, client policies and registration policies through the admin API; never delete users or registered clients) and `check` (exit non-zero when the running realm differs from the file). Extend `test_keycloak_realm.py`: a second `apply` changes nothing; `check` detects a manual change made through the admin API; users and registered clients survive `apply`.
- [x] **0.10.6** — Write `deploy.sh`: build once, tag with commit and digest, pull pinned digests, run migrations as one controlled task, let Keycloak import the realm on first start, then run `keycloak_realm.py apply` and `check` over the host's loopback port, ensure the synthetic smoke user exists (admin API, credentials from deployment secrets), then health checks. Replace the AWS identity-stack deploy step and AWS credential configuration in `deploy-dogfood.yml` with a call to `deploy.sh` on the protected environment; dogfood needs no AWS account.
- [x] **0.10.7** — Write `test_runtime_manifests.py`: parse both compose files and the crontab; fail on a service command that loops over queues or mailboxes, or a crontab entry that is not `run-task <kind>` for a kind registered in `runtime/scheduled.py`. One-shot services such as `keycloak-db-init` and long-running servers such as `keycloak` are not workers.
- [x] **0.10.8** — Write `smoke_dogfood.py` and `make smoke-dogfood`: sign the synthetic smoke user in through the dogfood Keycloak with a scripted authorization code flow with PKCE, check API readiness, call `GET /v1/workflow-runs/{id}` (authenticated, expects a problem `404`, not `401`) and the MCP endpoint (`initialize` and `tools/list` on the legacy path, `server/discover` and `tools/list` on the 2026-07-28 path), and assert that `/admin/` and `/realms/master/` on the `auth.` hostname return `404` from outside the host. Later phases extend it to a scheduled `imap-poll`, launched tasks and authorized evidence access.
- [x] **0.10.9** — Write `compatibility_report.py` and `make compatibility-local`: exercise each AWS API the application uses against the pinned MiniStack and write `docs/compatibility.md` (operation, pinned emulator version, local result, AWS result, fallback; unsupported operations listed). Seed `docs/operations.md` with deploy, rollback and replay runbook sections, plus Keycloak sections: reaching the admin console through an SSH tunnel, replacing the temporary bootstrap admin with a named operator account, the upgrade procedure (dump the `keycloak` database first until Task 5.10 automates backups, bump the pinned version in root `compose.yaml` and `keycloak.Dockerfile`, rerun the realm tests and the MCP dynamic-registration flow), and pruning unused registered MCP clients.

**Validation:**

- [x] **Build:** `BE-CHECK` exits 0; `docker build -f infrastructure/docker/backend.Dockerfile .` succeeds for every stage; `docker build -f infrastructure/docker/keycloak.Dockerfile .` succeeds.
- [x] **Tests:** `BE-UNIT` passes `test_runtime_manifests.py`, including a negative case with a looping worker command and an unregistered crontab kind; `BE-INT` passes the `keycloak_realm.py` cases in 0.10.5.
- [x] **Requirements:**
    - [x] "`infrastructure/dogfood/compose.yaml` defines no service whose command loops over queues or mailboxes; the scheduler service runs only `infrastructure/dogfood/scheduler.crontab` entries, each a `property_focus.cli run-task <kind>` process that exits. `backend/tests/unit/test_runtime_manifests.py` parses both compose files and the crontab and fails on a long-running worker command or on a crontab entry that is not `run-task <kind>` for a kind registered in `runtime/scheduled.py`."
    - [x] "Build once, tag with commit and digest, promote the same image, run migrations as one controlled task, then run smoke checks. Roll back application images without reversing destructive schema changes; use expand/contract migrations."
    - [x] "Engineers pin the MiniStack image digest and test the specific services used" and "`docs/compatibility.md` records operation, pinned emulator version, local result, AWS result and fallback. An unsupported emulator operation cannot count as an AWS pass."
    - [x] "dogfood exports through `infrastructure/dogfood/otel-collector.yaml` to a self-hosted stack."
    - [x] "`make dev` starts API on loopback port 8000, MCP on loopback 8001, Vite on 5173 and the local scheduler; it refuses non-development configuration."
    - [x] "`infrastructure/dogfood/compose.yaml` runs `keycloak` from `infrastructure/docker/keycloak.Dockerfile`, an optimized build (`kc.sh build` for Postgres with health checks; no experimental features) of the same pinned image, started with `start --optimized --import-realm` behind forwarded proxy headers. It uses its own `keycloak` database and role on the dogfood Postgres server."
    - [x] "Keycloak's experimental `cimd` and `resource-indicators` features stay off in root `compose.yaml` and in the dogfood Keycloak image (user decision, 2026-11-02, option C)." (dogfood image part)
    - [x] "`infrastructure/dogfood/Caddyfile` serves Keycloak only on `https://auth.<dogfood domain>` and forwards only `/realms/property-focus/*`, `/resources/*` and the discovery mappings above; `/admin/*` and `/realms/master/*` return `404` there. Keycloak's HTTP and management ports bind to the host's loopback interface, so operators reach the admin console only through an SSH tunnel."
    - [x] "The bootstrap admin, database and SMTP credentials are deployment-injected secrets (D49). None appears in Git, the image, the realm file or logs."
    - [x] "`deploy.sh` lets Keycloak import the realm on first start. … for later deploys `deploy.sh` runs `backend/scripts/keycloak_realm.py apply`, which … never deletes users or registered MCP clients. `keycloak_realm.py check` exits non-zero when the running realm differs from the file; running `apply` twice changes nothing the second time."
    - [x] "Phase 0: `make smoke-dogfood` signs a synthetic smoke user in through the dogfood Keycloak with authorization code and PKCE, calls one REST endpoint and the MCP endpoint with the issued token, and asserts that `/admin/` and `/realms/master/` on the `auth.` hostname return `404` from outside the host."
- [x] **Verify:** `make compatibility-local` regenerates `docs/compatibility.md` with a pinned MiniStack digest and at least one row per AWS service used. `grep -n "aws-actions\|AWS_DEPLOY_ROLE_ARN" .github/workflows/deploy-dogfood.yml` returns nothing. The Keycloak image digest in `keycloak.Dockerfile` equals the one in root `compose.yaml`, and `grep -nE 'cimd|resource-indicators' infrastructure/docker/keycloak.Dockerfile compose.yaml` returns nothing. **Live (explicit user approval required, after CP-0.B):** dogfood deployment and `make smoke-dogfood`.

---

### Task 0.11: Path-Aware CI

Create `ci.yml` with frozen-lock installs, the D48 check set, path filters and the docs-only path.

**Modules:** root tooling
**Depends on:** 0.8, 0.9, 0.10, 0.13
**Decisions:** D48, D49, D46, A4

**Files:**

```
.github/workflows/ci.yml        (create)
Makefile                        (modify)
```

**Sub-tasks:**

- [x] **0.11.1** — Add an install step running `pnpm install --frozen-lockfile` and `uv sync --all-packages --frozen` before any check, so lock drift fails the build.
- [x] **0.11.2** — Add jobs: Python lint/typecheck/tests, frontend lint/typecheck/tests/build, OpenAPI and RTK Query generation check, replay/golden tests, CDK test and synth, and selected integration tests with Compose services. The integration and contract job starts `postgres` and `keycloak` (realm import) with the other `BE-INT` services and waits for Keycloak's health check before running tests, so Keycloak-issued tokens back the auth, tenancy, realm and MCP tests.
- [x] **0.11.3** — Add path filters: `backend/**` runs backend, generation and replay jobs; `frontend/**` runs frontend and generation-check jobs; `infrastructure/**` runs CDK test and synth, and `infrastructure/keycloak/**` also runs the backend integration job; `ontology/**` and `fixtures/**` run backend ontology, replay and golden jobs.
- [x] **0.11.4** — Add the docs-only path: a PR changing only `docs/**` or `specs/**` runs only the secret scan and `markdownlint-cli2` with `/.markdownlint-cli2.yaml`.
- [x] **0.11.5** — Add a Linux x86_64 bootstrap job running the plan's bootstrap commands (migrate and seed in synthetic mode); complete `make verify` with the full `VERIFY` set.

**Validation:**

- [x] **Build:** `make verify` exits 0 locally.
- [x] **Tests:** All existing suites pass inside `make verify`.
- [x] **Requirements:**
    - [x] "`ci.yml` runs Python lint/typecheck/tests, frontend lint/typecheck/tests, the OpenAPI generation check, the RTK Query generation check, replay/golden tests, CDK synth and the selected integration tests listed under Test strategy."
    - [x] "Path filters: `backend/**` runs backend, generation and replay jobs; … A pull request that changes only `docs/**` or `specs/**` runs only the secret scan and markdown lint (`markdownlint-cli2` with `/.markdownlint-cli2.yaml`)."
    - [x] "CI runs `pnpm install --frozen-lockfile` and `uv sync --all-packages --frozen` before any check; a manifest change without a matching `pnpm-lock.yaml` or `uv.lock` update fails the build."
    - [x] "CI scans committed files, `/fixtures` and evidence fixtures for secrets."
    - [x] "`ci.yml` runs the bootstrap on a Linux x86_64 runner."
    - [x] "`make dev` and the integration, contract and E2E suites use the local Keycloak realm." (CI integration part)
- [x] **Verify:** Validate the workflow syntax with `actionlint` or an equivalent local check and read the path filter block. A recorded CI run is produced in Task 0.12.

---

### Task 0.15: PyJWT JWKS Client

Replace the hand-written JWKS fetch, cache and refetch in `adapters/auth/oidc.py` with PyJWT's `PyJWKClient` (A6). Discovery with the exact issuer match, the provider profiles, the tenant mapping and the `503`/`401` split stay ours. No behavior a test pins today changes, except that an expired key set whose refetch fails now gives `503`.

**Modules:** `backend`
**Depends on:** 0.4
**Decisions:** A6, D28, A4

**Files:**

```
backend/pyproject.toml                                    (modify)
uv.lock                                                   (modify)
backend/src/property_focus/adapters/auth/oidc.py          (modify)
backend/tests/unit/test_auth.py                           (modify)
docs/system/modules/identity-and-auth.md                  (modify)
```

**Sub-tasks:**

- [x] **0.15.1** — Change the PyJWT pin in `backend/pyproject.toml` to the bounded range `pyjwt[crypto]>=2.15.1,<3` and update `uv.lock` in the same change.
- [x] **0.15.2** — In `verifier_for`, keep the discovery read through `fetch_json` with its exact `issuer` match, then build one `PyJWKClient` for the issuer from the discovery `jwks_uri` (or `PF_OIDC_JWKS_URL`), with the JWK-set cache on, a lifespan equal to `MAX_KEY_SET_AGE_SECONDS` and the existing 10-second timeout. Reuse that client for every request to the issuer. Call it only through `asyncio.to_thread`, never on the event loop. Delete `_KeySet` and `_signing_keys`.
- [x] **0.15.3** — Keep the unknown-`kid` limit with a small guard around the client: a `kid` missing from the cached set triggers at most one refreshed fetch per `REFETCH_INTERVAL_SECONDS` on the verifier's injected clock; inside the interval the token is rejected as an unknown key without a fetch. Serialize refreshes with one `asyncio.Lock`, so concurrent requests cause one fetch.
- [x] **0.15.4** — In `OidcVerifier.verify`, keep the header checks (algorithm in `PF_OIDC_ALGORITHMS`, a `kid` present) before key lookup. Pass `jwt.decode` an options dictionary built inside each call (GHSA-gvp8-978c-rx2q), with `require` `exp`, `iss`, `sub` and `verify_aud` false. Keep the audience-claim and token-type checks and the `VerifiedIdentity` result unchanged.
- [x] **0.15.5** — Map errors: a `PyJWKClientConnectionError`, an unreachable discovery document or a discovery `issuer` mismatch raises `CodedError` `dependency_transient` (`503` on both adapters); a key missing after any allowed refresh and every `PyJWTError` raise `InvalidTokenError` (`401`). When the cached set has expired and the refetch fails, the request gets `503`.
- [x] **0.15.6** — Keep `fixture_verifier` for unit tests only: feed the in-test JWKS to a `PyJWKClient` without network access (for example a subclass whose `fetch_data` returns the supplied key set). It still raises `ValueError` without `PF_AUTH_FIXTURE_ISSUER`.
- [x] **0.15.7** — Extend `test_auth.py`; every existing case keeps passing. New cases: tokens naming 100 random unknown `kid` values in one minute cause at most one fetch; after the interval one more fetch happens; a rotated-in key validates after one refetch and a removed key fails; two consecutive decodes receive distinct options objects and a token rejected by the first is rejected by the second; the client's fetch runs in a worker thread, not on the event-loop thread; one client serves many requests; an unreachable JWKS and a discovery `issuer` mismatch raise `dependency_transient`, while an invalid token raises `unauthorized` through `authenticate`.
- [x] **0.15.8** — Update `docs/system/modules/identity-and-auth.md`: `PyJWKClient`, the refetch guard, the expired-set `503` and the fresh options dictionary.

**Validation:**

- [x] **Build:** `INSTALL` and `BE-CHECK` exit 0.
- [x] **Tests:** `BE-UNIT` passes the cases in 0.15.7; `BE-INT` passes every existing `test_api.py`, `test_tenancy.py` and `test_mcp.py` case with Keycloak-issued tokens.
- [x] **Requirements:**
    - [x] "`backend/pyproject.toml` pins `pyjwt[crypto]>=2.15.1,<3` and `mcp>=2.3,<2.4` …, and `uv.lock` resolves PyJWT 2.15.1 or later …" (PyJWT part)
    - [x] "`adapters/auth/oidc.py` holds no hand-written key-set cache: one `PyJWKClient` per issuer fetches and caches the JWKS … every `PyJWKClient` call runs off the event loop (`asyncio.to_thread`). Discovery still requires an exact `issuer` match. A token naming an unknown `kid` causes at most one refetch a minute …"
    - [x] "Each `jwt.decode` call receives a newly built options dictionary, so no call can change another's checks (GHSA-gvp8-978c-rx2q) …"
    - [x] "The `RS256`/`ES256` allowlist, the configurable audience claim and token-type claim …, and the mapping of issuer and subject to a tenant through `authenticate` stay unchanged …"
    - [x] "An unreachable issuer (discovery or JWKS) or a mismatched discovery `issuer` gives `503` on REST and MCP, and an invalid token gives `401`. When the cached key set has expired and its refetch fails, the request gets `503` …"
    - [x] "The verifier caches the JWKS, refetches it at most once a minute when a token names an unknown `kid`, and rejects a key removed from the JWKS after the next refetch (`test_auth.py`)."
- [x] **Verify:** `grep -nE '_KeySet|_signing_keys|PyJWK\(' backend/src/property_focus/adapters/auth/oidc.py` returns nothing, and `grep -n 'PyJWKClient' backend/src/property_focus/adapters/auth/oidc.py` finds the client. `grep -n 'pyjwt' backend/pyproject.toml` shows `>=2.15.1,<3`. The Task 0.4 verify grep (`cognito|keycloak` under `adapters/auth`) still returns nothing.

---

### Task 0.16: Official MCP SDK Adapter

Rebuild `adapters/mcp/server.py` on the official MCP Python SDK (`mcp` 2.3.x) low-level `Server` (A6). The SDK owns JSON-RPC, protocol eras and the streamable HTTP transport. We keep the tool registry, in-process use-case calls, the shared token check and structured tool errors. Contract tests move to specification-level assertions; the generic test client and both eras stay. This changes a Tier 1 contract (MCP wire behavior and OAuth discovery), so the user reviews it before release.

**Modules:** `backend`, `infrastructure/dogfood`, root tooling, `docs`
**Depends on:** 0.15, 0.14
**Decisions:** A6, A3, D29, D28, A4

**Files:**

```
backend/pyproject.toml                                    (modify)
uv.lock                                                   (modify)
backend/src/property_focus/config.py                      (modify)
backend/src/property_focus/adapters/mcp/server.py         (modify)
backend/src/property_focus/adapters/auth/oidc.py          (modify)
backend/tests/contract/mcp_client.py                      (modify)
backend/tests/contract/test_mcp.py                        (modify)
backend/tests/unit/test_mcp_protocol.py                   (delete)
backend/tests/unit/test_mcp_adapter.py                    (create)
backend/tests/unit/test_config.py                         (modify)
.env.example                                              (modify)
compose.yaml                                              (modify)
infrastructure/dogfood/compose.yaml                       (modify)
infrastructure/dogfood/deploy.sh                          (modify)
docs/system/modules/mcp-adapter.md                        (modify)
docs/system/modules/configuration-and-secrets.md          (modify)
AGENTS.md                                                 (modify)
```

**Sub-tasks:**

- [x] **0.16.1** — Add `mcp>=2.3,<2.4` to `backend/pyproject.toml` and update `uv.lock`. Record in the coder's report the resolved version and the SDK names used for the low-level server, the session manager, `TokenVerifier`, `AccessToken`, `AuthSettings`, the protected-resource routes and `TransportSecuritySettings`, plus any case in this task where the SDK's behavior differs from the plan.
- [x] **0.16.2** — Add settings to `config.py`: `PF_MCP_PUBLIC_URL`, `PF_MCP_ALLOWED_HOSTS` and `PF_MCP_ALLOWED_ORIGINS`, with the plan's Authentication configuration values; refuse empty values outside development. Set them in `.env.example`, root `compose.yaml` and `infrastructure/dogfood/compose.yaml`; add `test_config.py` cases.
- [x] **0.16.3** — Rebuild `server.py` on the SDK's low-level `Server` named `property-focus` with the package version. Register `on_list_tools` and `on_call_tool` handlers that serve the existing `Tool` registry (unique names, input and output schemas). `on_call_tool` validates arguments (`invalid_input` tool error on failure), runs the use case in a worker thread with the caller's `ExecutionContext`, returns `structuredContent` plus the JSON text content, and turns a `CodedError` into an `isError` result with the body `{"code", "detail"}`. Delete the hand-written JSON-RPC parsing, era classification, header checks, version negotiation and `_challenge`.
- [x] **0.16.4** — Serve the SDK's streamable HTTP transport with `stateless_http=True` and `json_response=True`, mounted at `/mcp` in our Starlette app. The app lifespan configures telemetry, creates or receives the engine, and enters the session manager's `run()`. Keep `OpenTelemetryMiddleware` and the per-request correlation ID.
- [x] **0.16.5** — Write the `TokenVerifier` adapter in `server.py`: call `oidc.authenticate`, return an `AccessToken` subclass carrying the `ExecutionContext` (constant `client_id`, never read; scopes from the token; `expires_at` from `exp`), and return none for `unauthorized`, so the SDK answers `401`. Raise a dedicated exception for `dependency_transient` that an outer handler turns into HTTP `503` with a JSON-RPC error body. Configure `AuthSettings` with `PF_OIDC_ISSUER`, `PF_MCP_PUBLIC_URL` and `validate_token_resource=False` set explicitly (audience stays checked in `oidc.py`; GHSA-w4fh-qvv9-3v23). Configure `TransportSecuritySettings` explicitly: DNS-rebinding protection on, allowed hosts and origins from the new settings. Remove `protected_resource_metadata` from `oidc.py` if the SDK's routes replace it.
- [x] **0.16.6** — Serve protected-resource metadata naming `PF_OIDC_ISSUER` at `/.well-known/oauth-protected-resource/mcp` (SDK routes) and the same document at `/.well-known/oauth-protected-resource`. The Caddyfile's `/.well-known/oauth-protected-resource*` route already covers both; add the `/mcp` form to the `deploy.sh` health checks, with the same issuer check as the app host's authorization-server metadata.
- [x] **0.16.7** — Move `test_mcp.py` to specification-level assertions, keeping the generic client in `mcp_client.py` and both eras. Keep: the `401` challenge names `resource_metadata`, and that URL's document names the issuer (no exact header string); the dynamic-registration and PKCE flow; the expired-token and wrong-audience rejections; the 2025-06-18 and 2025-11-25 handshakes; a 2026-07-28 client with and without `server/discover` getting identical results; `-32020` header mismatches; `2027-01-01` giving `400` `-32022` with `data.supported` and `data.requested`; `-32601` for modern `ping`; no `Mcp-Session-Id`, `application/json`, `202` for notifications and `405` on `GET`; the parity harness through both eras; the Base64 `Mcp-Name`. Change: a modern request without client capabilities expects the specification's code as the SDK returns it (for example `-32602`); a `2024-11-05` `initialize` without a token gets `401` and with a valid token is served; a disallowed `Host` or `Origin` is rejected and runs no tool; an issuer outage gives `503`. Delete `test_mcp_protocol.py`, whose functions no longer exist.
- [x] **0.16.8** — Write `tests/unit/test_mcp_adapter.py`: the `TokenVerifier` returns an `AccessToken` carrying the context for a valid fixture-issuer token, none for an invalid one, and raises the `503` exception for `dependency_transient`; `validate_token_resource` is false and the transport-security settings match configuration; duplicate tool names are refused; a `CodedError` becomes the structured tool error. Update `docs/system/modules/mcp-adapter.md`, the MCP settings in `configuration-and-secrets.md`, and AGENTS.md (Tech Stack gains the MCP Python SDK).

**Validation:**

- [x] **Build:** `INSTALL` and `BE-CHECK` exit 0; `docker build -f infrastructure/docker/backend.Dockerfile --target mcp .` succeeds.
- [x] **Tests:** `BE-UNIT` passes `test_mcp_adapter.py`; `BE-INT` passes the cases in 0.16.7 and every `test_keycloak_realm.py`, `test_api.py` and `test_tenancy.py` case.
- [x] **Requirements:**
    - [x] "`backend/pyproject.toml` pins `pyjwt[crypto]>=2.15.1,<3` and `mcp>=2.3,<2.4` …, and `uv.lock` resolves … an `mcp` 2.3.x release." (MCP part)
    - [x] "`adapters/mcp/server.py` builds on the SDK's low-level `Server`, with `on_list_tools` and `on_call_tool` handlers over our `Tool` registry. Tools call application use cases in-process in a worker thread (D29). A `CodedError` becomes a tool result with `isError` true and the structured body … The module holds no hand-written JSON-RPC parsing, protocol-era classification, header checks or version negotiation."
    - [x] "The SDK's streamable HTTP transport runs with `stateless_http=True` and `json_response=True`, mounted at `/mcp` in our Starlette app, whose lifespan enters the session manager's `run()`. Telemetry middleware and correlation IDs stay."
    - [x] "Our `TokenVerifier` adapter calls `oidc.authenticate` and returns an `AccessToken` that carries the caller's `ExecutionContext` … An invalid token gives the SDK's `401` challenge; `dependency_transient` gives `503`, not `401` …"
    - [x] "The SDK's `AuthSettings` names `PF_OIDC_ISSUER` as issuer and `PF_MCP_PUBLIC_URL` as resource server URL, and sets `validate_token_resource=False` explicitly … `test_mcp.py` rejects a Keycloak token without the API audience."
    - [x] "The SDK's `TransportSecuritySettings` are explicit: DNS-rebinding protection on, with `PF_MCP_ALLOWED_HOSTS` and `PF_MCP_ALLOWED_ORIGINS` per environment … A request with another `Host` or `Origin` is rejected and runs no tool (`test_mcp.py`)."
    - [x] "Protected-resource metadata naming `PF_OIDC_ISSUER` answers at `/.well-known/oauth-protected-resource/mcp` (SDK) and at `/.well-known/oauth-protected-resource` … `deploy.sh` checks both."
    - [x] "`backend/tests/contract/test_mcp.py` asserts specification-level behavior through the generic client in `mcp_client.py`, which keeps both eras and dynamic registration …"
    - [x] "MUST: Serve MCP protocol revisions 2025-03-26, 2025-06-18, 2025-11-25 and 2026-07-28 on the one stateless `/mcp` endpoint" with the SDK behaviors accepted under A6 (2024-11-05 served with sign-in; specification codes for garbled modern requests; the SDK's `401` format).
    - [x] "MCP access is client-agnostic (A3) … No module under `adapters/mcp/` or `adapters/auth/` reads a client name, user agent or other client identity, and no configuration value names an MCP client."
- [x] **Verify:** `grep -nE '_PARSE_ERROR|is_modern_request|_HEADER_MISMATCH|_answer_legacy' backend/src/property_focus/adapters/mcp/server.py` returns nothing. `grep -n 'validate_token_resource' backend/src/property_focus/adapters/mcp/server.py` shows an explicit value. `grep -nE '^name = "mcp"$' -A1 uv.lock` shows a `2.3.` version. The Task 0.5 verify grep (`user.agent|client_name|client-name` under `adapters/mcp`, `adapters/auth` and `config.py`) still returns nothing. On `make dev`, `curl -s http://localhost:8001/.well-known/oauth-protected-resource/mcp` and the root form both name the local issuer. The user has reviewed the Tier 1 contract change (record in `status.md`).

---

### Task 0.17: Cloudflare Edge for Dogfood

Put Cloudflare in front of both dogfood hostnames (A5): Caddy serves a Cloudflare Origin CA certificate from deployment secrets, the host firewall admits 80 and 443 only from Cloudflare's published ranges, Caddy trusts client-IP headers only from those ranges, and the operations runbook holds the Cloudflare dashboard settings. The smoke check runs through Cloudflare.

> **User decisions 2026-11-02, after 0.17.8 was written:** Bot Fight Mode stays off, not on (the free plan cannot exempt paths from it, and it would challenge non-browser MCP clients and token requests); the rate-limiting rule's threshold is confirmed at 20 requests per 10 seconds per IP on the sign-in, registration and `/mcp` paths, action Block (`429`); the auth host is `auth-propertyfocus.treb.tech`, so the Origin CA certificate lists `propertyfocus.treb.tech` and `auth-propertyfocus.treb.tech`. `docs/operations.md` holds the current settings.

**Modules:** `infrastructure/dogfood`, root tooling, `backend` (scripts and tests), `docs`
**Depends on:** 0.10
**Decisions:** A5, D48, D49, A4

**Files:**

```
infrastructure/dogfood/cloudflare-ips.txt                 (create)
infrastructure/dogfood/cloudflare-origin-ca.pem           (create)
infrastructure/dogfood/firewall.sh                        (create)
infrastructure/dogfood/Caddyfile                          (modify)
infrastructure/dogfood/compose.yaml                       (modify)
infrastructure/dogfood/deploy.sh                          (modify)
.github/workflows/deploy-dogfood.yml                      (modify)
backend/scripts/smoke_dogfood.py                          (modify)
backend/tests/unit/test_dogfood_edge.py                   (create)
docs/operations.md                                        (modify)
docs/system/modules/infrastructure-and-delivery.md        (modify)
```

**Sub-tasks:**

- [x] **0.17.1** — Write `cloudflare-ips.txt`: one CIDR per line from `https://www.cloudflare.com/ips-v4` and `https://www.cloudflare.com/ips-v6`, with the source URLs and fetch date in `#` comments. Commit `cloudflare-origin-ca.pem`, Cloudflare's public Origin CA root certificate (public data, no key).
- [x] **0.17.2** — In the `Caddyfile`, serve both site blocks with `tls` naming the origin certificate and key files mounted from the host, so no site uses ACME. Add global `servers` options: `trusted_proxies static` with the ranges from `cloudflare-ips.txt` (passed in by `deploy.sh`, for example as an environment variable or a generated snippet; the file stays the single source), `client_ip_headers CF-Connecting-IP X-Forwarded-For`, and strict trusted-proxy parsing. Enable JSON access logs that record `client_ip`. Keep every existing route.
- [x] **0.17.3** — In `infrastructure/dogfood/compose.yaml`, mount `/etc/property-focus/origin/` read-only into `caddy` and remove the `443/udp` port, since Cloudflare reaches the origin over TCP. Keep `api` and `mcp` without published ports, so only Caddy reaches them. In `deploy-dogfood.yml`, write the `dogfood` environment secrets `DOGFOOD_ORIGIN_CERT` and `DOGFOOD_ORIGIN_KEY` to `/etc/property-focus/origin/cert.pem` and `key.pem` on the host (key mode 600, written like the environment file), and list them in the workflow header.
- [x] **0.17.4** — Write `firewall.sh` for operators (run with explicit user approval): admit inbound TCP 22 as today, admit TCP 80 and 443 only from `cloudflare-ips.txt`, and drop other inbound 80 and 443. Put the 80 and 443 rules in the chain that Docker-published ports pass through (`DOCKER-USER`), because Docker bypasses ordinary input rules. Make it idempotent and have it print the rules it applied.
- [x] **0.17.5** — Add a preflight to `deploy.sh`, before it changes anything: fail when `cloudflare-ips.txt` differs from Cloudflare's published lists (`https://api.cloudflare.com/client/v4/ips`), when the live firewall admits 80 or 443 from a source outside the list, or when the origin certificate or key is missing, the key is not mode 600, or the certificate expires within 30 days. Change the `--resolve … 127.0.0.1` health checks to verify TLS with `--cacert cloudflare-origin-ca.pem`.
- [x] **0.17.6** — Extend `smoke_dogfood.py` and `make smoke-dogfood`: every response from both hostnames carries a `cf-ray` header; a direct TCP connection to `PF_DOGFOOD_ORIGIN_ADDRESS` (a new `dogfood` environment variable) on port 443 fails within a timeout; a Cloudflare challenge page instead of the expected JSON or redirect fails the check with a message naming Bot Fight Mode. Keep every existing check.
- [x] **0.17.7** — Write `test_dogfood_edge.py`: both Caddy sites use the origin certificate files and no site sets ACME options; `trusted_proxies` takes its ranges only from `cloudflare-ips.txt`; `client_ip_headers` starts with `CF-Connecting-IP`; `cloudflare-ips.txt` parses as non-empty valid IPv4 and IPv6 networks; the dogfood `caddy` service mounts the origin directory read-only and publishes no UDP port; `api` and `mcp` publish no ports.
- [x] **0.17.8** — Write the runbook in `docs/operations.md`, replacing the `A`-record and ACME step of the one-time setup: proxied `A`/`AAAA` records for both hostnames; SSL mode Full (strict) and Always Use HTTPS; creating the Origin CA certificate for both hostnames and storing it as `DOGFOOD_ORIGIN_CERT` and `DOGFOOD_ORIGIN_KEY`; running `firewall.sh`; the free managed WAF ruleset on; one rate-limiting rule whose expression covers the auth host's registration and sign-in paths and the app host's `/mcp`, with an initial per-IP threshold (plan recommendation) and the free plan's limits; Bot Fight Mode on, its effect on automated clients and what to do if the smoke check is challenged; refreshing `cloudflare-ips.txt`; renewing the origin certificate. Update `infrastructure-and-delivery.md`.

**Validation:**

- [x] **Build:** `BE-CHECK` exits 0; `bash -n infrastructure/dogfood/deploy.sh infrastructure/dogfood/firewall.sh` exits 0; `docker run --rm -v "$PWD/infrastructure/dogfood/Caddyfile:/etc/caddy/Caddyfile:ro" <pinned caddy image> caddy validate --config /etc/caddy/Caddyfile` succeeds with placeholder certificate files and ranges.
- [x] **Tests:** `BE-UNIT` passes `test_dogfood_edge.py` and `test_runtime_manifests.py`.
- [x] **Requirements:**
    - [x] "Both hostnames … are proxied DNS records in the user's Cloudflare account, with SSL mode Full (strict). Responses that `make smoke-dogfood` receives from both hostnames carry a `cf-ray` header."
    - [x] "`infrastructure/dogfood/Caddyfile` serves both hostnames with a Cloudflare Origin CA certificate and key that `deploy-dogfood.yml` writes to the host from the `dogfood` environment's secrets (D49); no site obtains an ACME certificate …"
    - [x] "The host firewall admits inbound 80 and 443 only from the ranges in `infrastructure/dogfood/cloudflare-ips.txt` … `make smoke-dogfood` asserts that a direct TCP connection to the origin's port 443 from outside Cloudflare fails. Before it changes anything, `deploy.sh` fails when the committed list differs from Cloudflare's published lists, when the firewall admits 80 or 443 from another source, or when the origin certificate is missing or expires within 30 days."
    - [x] "Caddy's `trusted_proxies` holds only the Cloudflare ranges, and its client-IP headers are `CF-Connecting-IP` and `X-Forwarded-For` … The `api` and `mcp` services publish no ports, so the application trusts forwarded headers only from Caddy (`test_dogfood_edge.py`)."
    - [x] "`docs/operations.md` holds the Cloudflare dashboard runbook …"
    - [x] "If Bot Fight Mode challenges the smoke check or an MCP client, the smoke check fails on the challenge page …"
- [x] **Verify:** `grep -nE 'acme|email ' infrastructure/dogfood/Caddyfile` returns nothing; `grep -n '443/udp' infrastructure/dogfood/compose.yaml` returns nothing; `grep -n 'ORIGIN_KEY' .github/workflows/deploy-dogfood.yml` finds the secret and no `echo` of it. **Live (explicit user approval required):** Cloudflare dashboard changes, `firewall.sh` on the host, the first deploy behind Cloudflare and `make smoke-dogfood` (recorded in Task 0.12).

---

### Task 0.12: Foundation Ready Gate

Collect the Foundation ready gate artifacts. This task writes evidence; it adds no product behavior. Phase 0 has no Cognito or AWS dependency (A4).

**Modules:** all phase 0 modules, `specs`
**Depends on:** 0.1–0.11, 0.13, 0.14, 0.15, 0.16, 0.17; CP-0.B (live dogfood step only)
**Decisions:** D46, D47, D48, D19, D28, A3, A4, A5, A6

**Files:**

```
specs/property-focus/gates/foundation-ready.md      (create)
```

**Sub-tasks:**

- [ ] **0.12.1** — Run the full bootstrap from a clean clone on macOS arm64 with Docker and save the log; link the CI Linux x86_64 bootstrap log.
- [x] **0.12.2** — Run `make verify` and record results, including the two-account isolation and account-provisioning tests, `test_runtime_manifests.py`, the Keycloak realm tests (`test_keycloak_realm.py`: import, PKCE enforcement, verified-email sign-up, audience, dynamic-registration policy cases), REST and MCP validation of Keycloak-issued tokens (`test_auth.py`, `test_api.py`, `test_mcp.py`), the MCP 401/metadata and authenticated-call contract tests with dynamic registration (with the authorization-server metadata showing a `registration_endpoint` and no `client_id_metadata_document_supported`), and the dual-era MCP contract tests: 2025-06-18 and 2025-11-25 clients, a 2026-07-28 client with and without `server/discover`, the `-32020` header-mismatch and `-32022` unsupported-version cases.
- [ ] **0.12.3** — Record a path-aware CI run: one docs-only PR that runs only the secret scan and markdown lint, and one backend PR that runs the backend jobs. Requires a push: ask the user first.
- [x] **0.12.4** — Record a PKCE sign-in smoke against local Keycloak: on `make dev`, sign a synthetic user in through the web app and the local realm, then call one REST endpoint and the MCP endpoint with the issued token.
- [ ] **0.12.5** — Record `make smoke-dogfood` output against the dogfood Keycloak (live; user approval; after CP-0.B and Task 0.17, so the run goes through Cloudflare and includes the `cf-ray`, direct-origin and Bot Fight Mode checks).
- [x] **0.12.6** — Attach `docs/compatibility.md` (compatibility inventory) and the Keycloak mechanisms reported in 0.13.3 and 0.5.7.
- [ ] **0.12.7** — After Tasks 0.15 and 0.16, run `VERIFY` again and record: the `PyJWKClient` cases in `test_auth.py`, `test_mcp_adapter.py`, `test_dogfood_edge.py`, the full `test_mcp.py` contract suite on the SDK adapter (both eras, dynamic registration, the SDK behaviors accepted under A6), the resolved `mcp` and PyJWT versions from `uv.lock`, and the user's Tier 1 review of the SDK adapter.
- [ ] **0.12.8** — Record the Cloudflare edge (live; user approval; after Task 0.17): the dashboard settings from the `docs/operations.md` runbook (proxied records, Full (strict), managed ruleset, the rate-limiting rule expression and threshold, Bot Fight Mode state), the `firewall.sh` output and the `deploy.sh` edge preflight output from the deploy that precedes 0.12.5.

**Validation:**

- [ ] **Build:** `VERIFY` exits 0.
- [ ] **Tests:** All phase 0 suites pass; results are linked in `foundation-ready.md`.
- [ ] **Requirements:**
    - [ ] "On a clean macOS arm64 or Linux x86_64 machine with Docker, the bootstrap commands below install dependencies, start local services, migrate an empty database and seed synthetic data without private credentials. `ci.yml` runs the bootstrap on a Linux x86_64 runner; the Foundation ready gate's clean-bootstrap log covers macOS arm64."
    - [ ] Foundation ready artifacts: "Clean-bootstrap log, two-account isolation and account-provisioning tests, path-aware CI run, no-long-running-worker check (`test_runtime_manifests.py`), Keycloak realm import and PKCE sign-in smoke against local Keycloak, dynamic-registration policy tests and the generic client's dynamic-registration flow (discovery does not advertise `client_id_metadata_document_supported`), REST and MCP validation of Keycloak-issued tokens, MCP 401/metadata and authenticated-call contract test, dual-era MCP contract tests (2025-06-18, 2025-11-25 and 2026-07-28 clients, the latter with and without `server/discover`; `-32020` and `-32022` cases), dogfood smoke against the dogfood Keycloak, compatibility inventory."
    - [ ] "a recorded CI run demonstrates this [docs-only path] at the Foundation ready gate."
    - [ ] Foundation ready additions (A5, A6): "the same contract suite green on the official MCP SDK adapter with PyJWT's JWKS client (A6), dogfood smoke against the dogfood Keycloak through Cloudflare with direct origin access refused and the Cloudflare settings recorded (A5)".
- [ ] **Verify:** `foundation-ready.md` links every artifact above and names no Cognito or AWS deployment step. **Live (explicit user approval required):** push for the CI run; Cloudflare dashboard changes and `firewall.sh`; dogfood deployment and `make smoke-dogfood`.

---

## Phase 1: MLS Ingestion

**Goal:** Scheduled IMAP intake with raw `.eml` retention, the TheMLS email parser, source observations, identity resolution, listing history with outbox, manual add by URL or address, and the first MCP tools. **Deliverable:** the Intake ready gate.

> **Checkpoint CP-1.A (blocking for live mailbox setup only) — mail host and authentication method; real email formats.** Owner: user, before live mailbox setup and before parser acceptance. Questions: confirm the first mailbox provider (OAuth preferred where supported) and supply representative redacted MLS emails. Tasks 1.1–1.7 proceed with synthetic fixtures; the live mailbox check in `make test-live` and parser acceptance on real formats wait for the answer.

### Task 1.1: Content-Addressed Evidence Store

Store evidence bytes once per SHA-256 in S3-compatible storage, keep provenance in Postgres, and refuse overwrites.

**Modules:** `backend`
**Depends on:** 0.6
**Decisions:** D44, D8

**Files:**

```
backend/src/property_focus/adapters/storage/__init__.py         (create)
backend/src/property_focus/adapters/storage/objects.py          (create)
backend/src/property_focus/application/ports.py                 (modify)
backend/src/property_focus/adapters/db/models.py                (modify)
backend/src/property_focus/runtime/scheduled.py                 (modify)
backend/migrations/versions/0002_evidence_assets.py             (create)
backend/tests/integration/test_evidence_store.py                (create)
```

**Sub-tasks:**

- [ ] **1.1.1** — Define `EvidenceStore` in `ports.py` (`put_immutable`, `read_authorized`) as in the plan's interface block.
- [ ] **1.1.2** — Migration `0002_evidence_assets.py`: `evidence_assets` (SHA-256, object key `sha256/<first2>/<hex>`, media type, size, object version, source URL, provider, captured time, scope/entitlement, retention class; unique SHA-256 per scope) and `evidence_asset_refs` (asset, property, listing, source observation). A trigger rejects updates to locator and digest.
- [ ] **1.1.3** — Implement `objects.py`: put-if-absent at the content key, refuse overwrite of a committed key, verify SHA-256 on every read, raise `evidence_integrity` and quarantine on mismatch.
- [ ] **1.1.4** — Add orphan collection to the `repair` scheduled task: delete content-addressed objects with no `evidence_assets` row only after a configurable safety window.
- [ ] **1.1.5** — Integration tests against MiniStack S3: same bytes twice give one object and two refs; overwrite attempt fails; tampered bytes fail digest check; an orphan inside the safety window survives.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-INT` passes `test_evidence_store.py` with the four cases in 1.1.5.
- [ ] **Requirements:**
    - [ ] "Storing the same bytes twice yields one object at `sha256/<first2>/<hex>` and two `evidence_asset_refs` rows; attempted overwrite of a committed digest/object reference fails; re-fetch verifies SHA-256" (signed downloads in Task 2.4)
    - [ ] "Writers cannot overwrite evidence; corrections create new claims." and `evidence_assets` "locator and digest never updated (D44)".
    - [ ] Error table: "Object stored but DB commit fails | Non-failure | Leave unreferenced content-addressed object | Delayed orphan collector after safety window."
- [ ] **Verify:** After the tests, list the MiniStack bucket and confirm keys match `sha256/<2 hex>/<64 hex>`.

---

### Task 1.2: Scheduled IMAP Intake (`imap-poll`)

Poll the dedicated mailbox inside a once-a-minute short-lived task, store raw MIME as evidence with an intake row and outbox event, and never parse during intake.

**Modules:** `backend`, `fixtures`, `infrastructure/dogfood`
**Depends on:** 1.1
**Decisions:** D22, D26, D54, D2

**Files:**

```
backend/src/property_focus/adapters/sources/__init__.py          (create)
backend/src/property_focus/adapters/sources/imap.py              (create)
backend/src/property_focus/application/ingestion.py              (create)
backend/src/property_focus/application/ports.py                  (modify)
backend/src/property_focus/runtime/scheduled.py                  (modify)
backend/src/property_focus/config.py                             (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/migrations/versions/0003_source_accounts_ingest.py       (create)
infrastructure/dogfood/scheduler.crontab                         (modify)
fixtures/mail/multipart.eml                                      (create)
fixtures/mail/duplicate.eml                                      (create)
fixtures/mail/malformed.eml                                      (create)
fixtures/mail/attachment-bomb.eml                                (create)
backend/tests/integration/imap_fixture_server.py                 (create)
backend/tests/integration/test_ingestion.py                      (create)
backend/tests/live/__init__.py                                   (create)
backend/tests/live/test_providers.py                             (create)
.github/workflows/live-integration.yml                           (create)
Makefile                                                         (modify)
```

**Sub-tasks:**

- [ ] **1.2.1** — Migration `0003`: `source_accounts` (tenant, provider, credential secret reference, entitlement group, browser session state column; no secret values) and `ingest_messages` (account, mailbox, UIDVALIDITY, UID, raw evidence asset ID, digest, parse version, state; unique account/mailbox/UIDVALIDITY/UID).
- [ ] **1.2.2** — Define `EmailIngress` and implement `imap.py`: fetch new UIDs after the checkpoint; start a new UIDVALIDITY namespace on reset with a bounded rescan; credentials through `SecretProvider`.
- [ ] **1.2.3** — Implement the `imap-poll` scheduled kind: take a Postgres advisory lock or exit without fetching; poll every `IMAP_POLL_INTERVAL_SECONDS` (default 20, validated 15–30); start no poll after 45 seconds; exit before 60 seconds; emit the measured gap between polls as a metric.
- [ ] **1.2.4** — Intake use case: per message, store raw MIME through `EvidenceStore` with its digest, insert the `ingest_messages` row and an outbox event in one transaction, then launch the parse task after commit. Advance the checkpoint only after durable intake; on database failure do not advance it.
- [ ] **1.2.5** — Enforce default limits (25 MiB raw email, 100 MIME parts, decompression limits): oversize or bomb messages become `INVALID_INPUT`, are quarantined with an evidence reference, and do not halt the mailbox. Malformed messages get a visible state with an evidence reference.
- [ ] **1.2.6** — Add `imap-poll` (each minute) to `scheduler.crontab`; add synthetic fixtures `multipart.eml`, `duplicate.eml`, `malformed.eml`, `attachment-bomb.eml` and the IMAP fixture server.
- [ ] **1.2.7** — Tests in `test_ingestion.py`: two concurrent invocations, one exits without fetching; in-invocation interval and the 45 s and 60 s bounds; interval outside 15–30 rejected; raw bytes and digest stored before parse; checkpoint not advanced on database failure; UIDVALIDITY reset rescans without duplicate observations; malformed and bomb messages do not halt intake; zero model-provider calls during intake.
- [ ] **1.2.8** — Add `make test-live` (runs `uv run pytest backend/tests/live -q` only when `PF_LIVE=1` and credentials resolve through the secret provider), `backend/tests/live/test_providers.py` with an opt-in mailbox check, and `live-integration.yml` (opt-in and nightly).

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` (`test_runtime_manifests.py` accepts the new crontab kind) and `BE-INT` (`test_ingestion.py` cases in 1.2.7) pass.
- [ ] **Requirements:**
    - [ ] "EventBridge Scheduler (AWS) and the dogfood scheduler start `imap-poll` once per minute. The invocation polls at `IMAP_POLL_INTERVAL_SECONDS` (default 20, validated to 15–30), starts no poll after 45 seconds, and exits before 60 seconds." (EventBridge part in Task 9.2)
    - [ ] "A Postgres advisory lock prevents overlap: a second concurrent invocation exits without fetching (`test_ingestion.py`)."
    - [ ] "`test_ingestion.py` asserts the in-invocation interval and the 45 s and 60 s bounds; no test asserts a worst-case gap of 30 s or less. Dogfood reports the measured poll-gap distribution."
    - [ ] "`imap-poll` stores raw MIME, the `ingest_messages` row and an outbox event, then exits; it does not parse. Parsing runs as a separate task launched from the outbox."
    - [ ] "Store raw MIME bytes with digest before parse success; malformed messages remain visible in operations with an evidence reference. No LLM call occurs during email ingestion (`test_ingestion.py` asserts zero model-provider calls)." (operations screen in Task 5.4)
    - [ ] "Resetting UIDVALIDITY rescans safely; malformed messages do not halt the mailbox; checkpoint advancement requires durable intake for the covered messages."
    - [ ] Error table: MIME attachment bomb, IMAP UID reset, overlapping `imap-poll` invocations and database unavailable rows.
- [ ] **Verify:** `uv run --package property-focus python -m property_focus.cli run-task imap-poll` against the fixture server exits in under 60 seconds and stores one evidence object per fixture message. **Live (explicit user approval required):** `PF_LIVE=1 make test-live` mailbox check, after CP-1.A.

---

### Task 1.3: Property and Listing Identity

Implement non-destructive identity resolution and the property, unit, listing, history and source observation tables.

**Modules:** `backend`
**Depends on:** 0.3
**Decisions:** D4, D8, D24

**Files:**

```
backend/src/property_focus/domain/identity.py                    (create)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/db/repositories.py           (modify)
backend/migrations/versions/0004_properties_listings.py          (create)
backend/tests/unit/test_identity.py                              (create)
```

**Sub-tasks:**

- [ ] **1.3.1** — Migration `0004`: `properties`, `property_aliases` (alias and merge audit, candidate links), `property_units`, `listings`, `listing_aliases` (unique scoped episode key), `listing_history` (append-only; trigger rejects update and delete) and `source_observations` (kinds `EMAIL`, `BROWSER`, `MANUAL`, `DOCUMENT`, `PROVIDER`; unique source/listing/content hash; immutable).
- [ ] **1.3.2** — Implement matching order in `domain/identity.py`: parcel identifier (county plus APN, scoped by jurisdiction), then normalized address plus coordinates, then source-scoped IDs; every link carries a confidence.
- [ ] **1.3.3** — Implement outcomes: strong match links; no match creates a provisional property; ambiguous (including address-only) keeps candidate links and enters identity review.
- [ ] **1.3.4** — Implement sale-scope rules: a whole duplex offered in one sale is one property with two component units; separately offered condos are distinct properties; a unit label on a lease or rent roll cannot create a sale listing; unclear sale scope enters review.
- [ ] **1.3.5** — Implement episode rules: a new marketing episode creates a new listing on the same property; source-ID reuse cannot erase prior episodes.
- [ ] **1.3.6** — Table-driven tests in `test_identity.py`: two condos in one building; whole duplex with two units; relisting with a new external ID; ambiguous address-only match; same parcel with consistent unit evidence links; source-ID reuse keeps the prior episode.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0; migration applies on an empty database.
- [ ] **Tests:** `BE-UNIT` passes `test_identity.py` with the six scenarios in 1.3.6.
- [ ] **Requirements:**
    - [ ] "Identity resolution tries parcel identifier, then normalized address plus coordinates, then source IDs, and stores a confidence on every link."
    - [ ] "A duplex offered in one sale produces one feed item with two child units. A lease or rent roll cannot create another sale listing. Unclear sale scope enters identity review."
    - [ ] "Same parcel/jurisdiction links an existing property when unit/parcel evidence is consistent; different condominium units remain distinct."
    - [ ] "A new marketing episode creates a listing linked to the same property. Ambiguous address-only matches enter review."
    - [ ] Key scenarios: "Two condos in one building, each offered for sale", "Whole duplex offered once, two units on its rent roll" (unit records), "Relisting with new external ID".
- [ ] **Verify:** Attempt `UPDATE listing_history ...` as the runtime role in a psql session; it fails.

---

### Task 1.4: TheMLS Email Parser and Ingestion Pipeline

Parse stored MLS email into `EMAIL` source observations, resolve identity, and commit listing state changes with history, domain event and outbox event in one transaction.

**Modules:** `backend`, `fixtures`
**Depends on:** 1.2, 1.3
**Decisions:** D2, D25, D54, D8

**Files:**

```
backend/src/property_focus/adapters/sources/themls_email.py      (create)
backend/src/property_focus/application/ingestion.py              (modify)
backend/src/property_focus/application/ports.py                  (modify)
backend/tests/unit/test_themls_email.py                          (create)
backend/tests/integration/test_ingestion.py                      (modify)
backend/tests/integration/test_workflows.py                      (modify)
```

**Sub-tasks:**

- [ ] **1.4.1** — Define `PropertySource` in `ports.py`; implement `themls_email.py` (MIME and listing-link parser) emitting `SourceObservationDraft` values with effective source time; no LLM.
- [ ] **1.4.2** — Implement the parse task: write `source_observations` (deduplicated by source/listing/content hash), never insert `properties` directly, then run identity resolution to create or link properties and listings.
- [ ] **1.4.3** — Implement listing state changes: a price or status change updates `listings`, inserts `listing_history`, `domain_events` and `outbox_events` in one transaction, then launches downstream tasks through `OutboxPublisher`.
- [ ] **1.4.4** — Prevent an older observation from replacing a newer status: resolve by effective source time and retain both observations.
- [ ] **1.4.5** — Unit tests for the parser over `multipart.eml` and `duplicate.eml`.
- [ ] **1.4.6** — Integration tests: re-deliver one UID, replay its parsed content, and deliver an equivalent message under another UID (delivery history kept, one observation and one listing episode); the same email delivered three times under two UIDs; price change atomicity with a fault injected before commit leaving none of the four writes; out-of-order arrival.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` (parser) and `BE-INT` (cases in 1.4.6) pass.
- [ ] **Requirements:**
    - [ ] "Re-deliver one UID, replay its parsed content, and deliver an equivalent message under another UID: intake records preserve delivery history while source observations and listing output remain deduplicated."
    - [ ] "The email parser, TheMLS browser, manual add and document upload each write `source_observations` and never insert `properties` directly; identity resolution creates or links properties afterward." (email part)
    - [ ] "A price change updates `listings`, inserts `listing_history`, `domain_events` and `outbox_events` in one transaction; a fault injected before commit leaves none of the four; the publisher sends only committed events (`test_workflows.py`)."
    - [ ] Key scenarios: "Same email delivered three times, two UIDs"; "Price change on an existing listing"; error table "Price/status arrives out of order".
    - [ ] "Merge and split fixtures retain aliases, evidence relationships and a reversible audit trail; source-ID reuse cannot erase prior episodes." (source-ID part; merges in Task 1.6)
- [ ] **Verify:** Run the parse task on `multipart.eml` twice through `cli run-task`; `source_observations` count stays at one per content digest.

---

### Task 1.5: Manual Add, Property Read API and Phase 1 MCP Tools

Add manual property addition by URL or address, the phase 1 property and workflow routes, and their six MCP tools with parity tests.

**Modules:** `backend`, `frontend` (generated client only)
**Depends on:** 1.4, 0.5, 0.8, 0.14
**Decisions:** D17, D30, D29, D2

**Files:**

```
backend/src/property_focus/adapters/sources/manual.py            (create)
backend/src/property_focus/application/ingestion.py              (modify)
backend/src/property_focus/application/context.py                (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/src/property_focus/adapters/mcp/server.py                (modify)
backend/src/property_focus/config.py                             (modify)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/contract/test_mcp.py                               (modify)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **1.5.1** — Implement `manual.py` and the manual-add use case: `{url}` from an allowlisted TheMLS host or `{address}` creates a `MANUAL` source observation and a workflow run; an address resolving to an existing property links to it; a non-allowlisted URL fails with `unsupported_source`.
- [ ] **1.5.2** — Add `POST /v1/properties` (`Idempotency-Key`; `202 {property_id, workflow_run_id, status_url}`; `422 unsupported_source`).
- [ ] **1.5.3** — Add `GET /v1/properties` without a subscription (stable `(updated_at, id)` order, scope-bound opaque cursor, `limit` 1..100), `GET /v1/properties/{id}`, `GET /v1/properties/{id}/listings`, `GET /v1/properties/{id}/history`, and `POST /v1/workflow-runs/{id}/cancel` (cooperative, `202`). Apply entitlement scope to listings and observations.
- [ ] **1.5.4** — Add MCP tools `list_properties`, `get_property`, `get_property_listings`, `get_property_history`, `add_property` (write, idempotency key required) and `get_workflow_run`, with the same schemas and principal context as REST.
- [ ] **1.5.5** — Tests: contract cases for each route and error code; one `test_mcp.py` parity case per tool; the generic-client flow test now ends with a successful tool call; tenancy cases for workflow runs and cursors across accounts.
- [ ] **1.5.6** — Regenerate the OpenAPI schema and RTK Query client.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-INT` passes the contract, parity and tenancy cases in 1.5.5.
- [ ] **Requirements:**
    - [ ] "`POST /properties` with `{url}` from an allowlisted host or `{address}` returns `202 {property_id, workflow_run_id}` and creates a `MANUAL` source observation; an address resolving to an existing property links to it; a non-allowlisted URL returns `422` with code `unsupported_source`."
    - [ ] "Contract tests compare canonical results through both adapters for every tool in the MCP tool list: `list_properties`, `get_property`, `get_property_listings`, `get_property_history`, `add_property` and `get_workflow_run` (phase 1)."
    - [ ] "Mark mutating tools as writes, require idempotency keys for workflow creation."
    - [ ] "`test_mcp.py` drives the whole flow with a generic test client … and calls a tool."
    - [ ] "MCP and REST return equivalent permitted results for the same principal and use case."
- [ ] **Verify:** `curl -X POST /v1/properties -d '{"url":"https://example.com/x"}'` with a development token returns `422` with `code: unsupported_source`.

---

### Task 1.6: Identity Review and Audited Merge and Split

Give reviewers and operators an audited, reversible way to resolve ambiguous identities.

**Modules:** `backend`
**Depends on:** 1.5
**Decisions:** D4, D5 (no RBAC beyond system roles), A1

**Files:**

```
backend/src/property_focus/domain/identity.py                    (modify)
backend/src/property_focus/application/ingestion.py              (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_identity.py                              (modify)
backend/tests/contract/test_api.py                               (modify)
```

**Sub-tasks:**

- [ ] **1.6.1** — Implement merge and split use cases: a merge keeps both identities as aliases, moves listing and evidence relationships, and records an audit event with actor and reason; a split reverses a merge using retained aliases and provenance.
- [ ] **1.6.2** — Add `POST /v1/ops/identities/merges` (`201`, audited) gated to the `reviewer` or `operator` role; other callers get `403`. Do not expose it through MCP.
- [ ] **1.6.3** — Tests: merge then split restores the original aliases and evidence relationships; a call without the role gets `403`; the audit event names actor and reason.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the merge/split and role-gate cases.
- [ ] **Requirements:**
    - [ ] "Merge and split fixtures retain aliases, evidence relationships and a reversible audit trail."
    - [ ] "Ambiguous cases keep their candidate links, and operators can reverse a merge using retained aliases and provenance."
    - [ ] "Operators need an explicit role and scope for retry, merge, source login, and budget changes; audit records identify actor and reason." (merge part)
- [ ] **Verify:** Query `audit_events` after a merge test and confirm `principal` and `metadata.reason` are set.

---

### Task 1.7: Replay Corpus and Intake Ready Gate

Add the replay suite, the golden manifest loader rules, synthetic property seed data and the Intake ready gate evidence.

**Modules:** `backend`, `fixtures`, `specs`
**Depends on:** 1.1–1.6
**Decisions:** D46, D22, D25, D29

**Files:**

```
backend/src/property_focus/cli.py                                (modify)
backend/src/property_focus/application/ingestion.py              (modify)
backend/tests/replay/__init__.py                                 (create)
backend/tests/replay/test_golden_replay.py                       (create)
fixtures/golden/manifest.json                                    (create)
fixtures/golden/golden-properties.jsonl                          (create)
fixtures/synthetic/synthetic-properties.jsonl                    (create)
backend/scripts/smoke_dogfood.py                                 (modify)
specs/property-focus/gates/intake-ready.md                       (create)
```

**Sub-tasks:**

- [ ] **1.7.1** — Implement `cli replay --manifest <path> --offline`: replay retained email fixtures through intake and parse with no network.
- [ ] **1.7.2** — Implement the golden manifest loader: versioned manifest with evidence hashes, labels, reviewer, consent/rights and split assignment; loading fails for a case without a reviewer, `origin: real`, `rights_cleared: true` and `redacted: true`. Commit an empty real set (no synthetic cases).
- [ ] **1.7.3** — Add `fixtures/synthetic/synthetic-properties.jsonl` and extend `cli seed --dataset synthetic` to load synthetic properties through the source-observation path.
- [ ] **1.7.4** — Write `test_golden_replay.py`: the replayed email corpus creates no duplicate listing episode or observation; a synthetic case placed in the golden manifest fails loading.
- [ ] **1.7.5** — Extend `smoke_dogfood.py` to confirm a scheduled `imap-poll` invocation ran and launched a parse task.
- [ ] **1.7.6** — Write `intake-ready.md`: replay report, raw MIME integrity checks, poll-overlap test, ambiguity review fixture, phase 1 MCP parity results.

**Validation:**

- [ ] **Build:** `VERIFY` exits 0.
- [ ] **Tests:** `BE-REPLAY` passes; all phase 1 suites pass.
- [ ] **Requirements:**
    - [ ] Phase 1 exit: "Replayed email corpus creates no duplicate listing episode or observation."
    - [ ] Intake ready artifacts: "Replay report, raw MIME integrity checks, poll-overlap test, ambiguity review fixture, phase 1 MCP parity."
    - [ ] "`fixtures/golden/manifest.json` versions the golden set with evidence hashes, labels, reviewer, consent/rights and split assignment. Loading fails for a case without a reviewer, `origin: real`, `rights_cleared: true` and `redacted: true`."
    - [ ] Key scenario: "Synthetic case added to `fixtures/golden/` | Manifest load fails".
- [ ] **Verify:** `intake-ready.md` links each artifact. **Live (explicit user approval required):** `make smoke-dogfood` with the `imap-poll` check.

---

## Phase 2: TheMLS Enrichment

**Goal:** Capability-based enrichment with a credential-scoped TheMLS browser task, browser profile lifecycle, content-addressed media and failure classification. **Deliverable:** a captured listing with expired-session recovery and immutable evidence; the phase 2 part of the Intelligence ready gate.

> **Checkpoint CP-2.A (blocking for live source capture) — TheMLS automation, media storage and reuse rights.** Owner: user / source account owner, before live source capture. Question: what automation, media storage and cross-account reuse does the TheMLS agreement allow? Tasks 2.1–2.5 use recorded synthetic pages only. No task captures live TheMLS pages, enables immutable retention, or shares cached licensed material across accounts until the user answers and `specs/property-focus/source-rights.md` records the policy.
>
> **Checkpoint CP-2.B (research, phase 2) — TheMLS MFA, session lifetime and source-ID reuse.** Owner: connector engineer. Record the findings in `specs/property-focus/source-rights.md` (session section) before Task 2.5 closes. The design response (credential leases, `AUTH_REQUIRED`, explicit episode policy) does not change.

### Task 2.1: Capability Registry and Enrichment Use Case

Select enrichers by capability with primary, secondary and shadow providers, defer capabilities with no provider, and record enricher runs.

**Modules:** `backend`
**Depends on:** 1.7
**Decisions:** D11, D45, D53

**Files:**

```
backend/src/property_focus/application/enrichment.py             (create)
backend/src/property_focus/application/ports.py                  (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/migrations/versions/0005_analysis_runs.py                (create)
backend/tests/unit/test_enrichment.py                            (create)
```

**Sub-tasks:**

- [ ] **2.1.1** — Define `Enricher` and the `Capability` and `ComponentState` types in `ports.py` as in the plan's interface block.
- [ ] **2.1.2** — Migration `0005`: `analysis_runs` with the plan's columns (kind, name, analysis tier, budget tier, mode `PUBLISHED`/`SHADOW`, provider, model, versions, computation key unique, input evidence IDs, status, confidence, tokens, cost, latency, result JSONB, workflow run ID). `ontology_version` is nullable here because no ontology exists yet; Task 3.1 adds the constraint for new rows. Rows are never updated after completion (trigger).
- [ ] **2.1.3** — Implement the capability registry in `enrichment.py`: configuration names a primary, optional secondary and optional shadow provider per capability.
- [ ] **2.1.4** — A capability with no registered provider marks the component `DEFERRED` with `DATA_UNAVAILABLE` (`capability_unavailable`) and raises no error.
- [ ] **2.1.5** — A configured shadow provider runs beside the primary and records its output as a `SHADOW` analysis run that never publishes observations or claims.
- [ ] **2.1.6** — Unit tests with fake enrichers: selection order, deferral without error, shadow run recorded with `mode=SHADOW` and no published output.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` passes `test_enrichment.py` cases in 2.1.6.
- [ ] **Requirements:**
    - [ ] "`application/enrichment.py` selects an enricher by capability … Requesting a capability with no registered provider marks the component `DEFERRED` with `DATA_UNAVAILABLE` and no error (D11)."
    - [ ] "A capability configured with a shadow provider records the shadow output as a `SHADOW` analysis run that never publishes claims."
    - [ ] `analysis_runs` "never updated after completion (D8, D14, D31)".
- [ ] **Verify:** Attempt `UPDATE analysis_runs SET status='FAILED'` on a completed row as the runtime role; it fails.

---

### Task 2.2: Source Account Session Lifecycle and Credential Leases

Model the TheMLS session states, serialize use of one account with leases, pause dependent work on reauthentication, and let an operator resume.

**Modules:** `backend`
**Depends on:** 2.1
**Decisions:** D23, D49, D55

**Files:**

```
backend/src/property_focus/adapters/sources/themls_browser.py    (create)
backend/src/property_focus/application/enrichment.py             (modify)
backend/src/property_focus/application/workflows.py              (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_source_sessions.py                       (create)
backend/tests/integration/test_browser.py                        (create)
backend/tests/integration/test_workflows.py                      (modify)
```

**Sub-tasks:**

- [ ] **2.2.1** — Implement the session state machine (`HEALTHY`, `SESSION_EXPIRING`, `AUTH_REQUIRED`, `BLOCKED`) with the plan's transitions, stored on `source_accounts`.
- [ ] **2.2.2** — Implement credential leases per source account (concurrency 1) with fencing tokens: concurrent tasks serialize; an expired lease holder cannot overwrite a newer session or task result.
- [ ] **2.2.3** — Store the encrypted browser profile outside the container and outside Git and logs; the encryption key comes through `SecretProvider`. Rotating a source credential invalidates affected sessions and keeps evidence.
- [ ] **2.2.4** — On session rejection: move the account to `AUTH_REQUIRED` (or `BLOCKED` for `source_blocked`) after one detection, set dependent workflow runs to `WAITING_FOR_USER`, and write one operator action event per unresolved episode through the outbox (delivered by the notification dispatcher in Task 5.2). Never loop through login attempts.
- [ ] **2.2.5** — Add `POST /v1/ops/source-accounts/{id}/resume` (operator role, refreshed secret version reference and reason, `202` retry episode, audited; never a raw session in a URL).
- [ ] **2.2.6** — Implement source account deletion: revoke credentials, cancel queued source work, apply the approved evidence policy, keep only permitted audit metadata.
- [ ] **2.2.7** — Tests: state transitions table; lease serialization and stale-holder rejection; one action event per episode; resume by operator succeeds and by a non-operator gets `403`; credential rotation keeps evidence.
- [ ] **2.2.8** — Resume is the first use case that moves a workflow run back to `QUEUED`. In `application/workflows.py`, make every transition into `QUEUED` (this resume, and later targeted retry and operator retry) reset the run's step `attempt_count` to zero and keep the earlier `step_attempts` rows (review observation P43). Test in `test_workflows.py`: a run whose step reached the attempt cap, then requeued, acquires its lease again, and its earlier attempt rows remain.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 2.2.7 and the requeue case in 2.2.8.
- [ ] **Requirements:**
    - [ ] "Expired-session fixture transitions the source account to `AUTH_REQUIRED` and the workflow run to `WAITING_FOR_USER` after one detection, emits one action notification, and resumes after operator-provided session refresh." (action delivered as a notification from Task 5.2)
    - [ ] "Concurrent tasks for one account serialize; expired lease holders cannot overwrite a newer session or task result."
    - [ ] "A workflow run that returns to `QUEUED` (authorized resume, targeted retry, operator retry) starts its step's attempt count at zero and keeps the earlier `step_attempts` rows, so the attempt cap counts only attempts since the requeue (review observation P43; `test_workflows.py`)."
    - [ ] "Keep browser profile and session state encrypted outside Git and logs. Rotating a source credential invalidates affected sessions without losing evidence."
    - [ ] "Operators need an explicit role and scope for … source login …; audit records identify actor and reason." (source login part)
    - [ ] SHOULD: "Source account deletion revokes credentials, cancels queued source work and applies the approved evidence policy. Audit records retain only the metadata the policy permits."
- [ ] **Verify:** Run the expired-session test twice; `outbox_events` holds exactly one action event for the episode.

---

### Task 2.3: Playwright TheMLS Browser Capture Task

Capture a recorded listing page with Playwright in an on-demand container, store HTML and media as content-addressed evidence, and enforce host, network and size safety rules.

**Modules:** `backend`, `infrastructure/docker`, `infrastructure/dogfood`, `fixtures`
**Depends on:** 2.2
**Decisions:** D23, D11, D2, D26, D44

**Files:**

```
backend/src/property_focus/adapters/sources/themls_browser.py    (modify)
backend/src/property_focus/application/enrichment.py             (modify)
backend/src/property_focus/runtime/launcher.py                   (modify)
backend/src/property_focus/config.py                             (modify)
infrastructure/docker/browser.Dockerfile                         (create)
infrastructure/dogfood/compose.yaml                              (modify)
compose.yaml                                                     (modify)
fixtures/themls/listing.html                                     (create)
fixtures/themls/login.html                                       (create)
backend/tests/integration/test_browser.py                        (modify)
backend/tests/unit/test_runtime_manifests.py                     (modify)
```

**Sub-tasks:**

- [ ] **2.3.1** — Write `browser.Dockerfile` with pinned Playwright and browser versions.
- [ ] **2.3.2** — Register the TheMLS browser enricher with capabilities `LISTING`, `PHOTOS`, `REMARKS`, `LISTING_HISTORY`; extract normalized facts and source time from the page and write a `BROWSER` source observation (never a property row).
- [ ] **2.3.3** — Store HTML and each referenced media file through `EvidenceStore` with stable evidence locators; session cookies and credentials never enter stored evidence.
- [ ] **2.3.4** — Enforce safety: allowlisted TheMLS hosts only; block private-network destinations and unsafe redirects; 20 MiB per media object, 200 MiB cumulative per listing task, 60-second request timeout; unsupported media types; each failure returns an explicit stable code. Never silently truncate evidence and mark it complete.
- [ ] **2.3.5** — Dogfood launch: add a `browser-task` profile and a docker-socket proxy limited to create/start/wait/remove of the pinned browser image; the launcher starts browser tasks with `docker compose run --rm browser-task`; ordinary tasks stay scheduler subprocesses. Extend `test_runtime_manifests.py` for the new service.
- [ ] **2.3.6** — Classify source failures: `source_changed` (`BUG_UNKNOWN`; preserve HTML and screenshot, stop the unsafe parse, leave a `PARTIAL` profile), `source_removed` (`DATA_UNAVAILABLE`; record observed unavailability, never declare a listing sold without evidence), `rate_limited` (`TRANSIENT`; honor Retry-After and shared source concurrency).
- [ ] **2.3.7** — Tests in `test_browser.py` over `listing.html` and `login.html`: normalized facts, source time, HTML and media evidence; login page triggers the Task 2.2 `AUTH_REQUIRED` path; disallowed host, private-IP redirect, oversized download and unsupported media each fail with their code and make no internal network fetch; a changed page structure yields `source_changed` with preserved HTML; a seeded secret never appears in stored evidence.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0; `docker build -f infrastructure/docker/browser.Dockerfile .` succeeds.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 2.3.7.
- [ ] **Requirements:**
    - [ ] "the TheMLS browser registers `LISTING`, `PHOTOS`, `REMARKS` and `LISTING_HISTORY`."
    - [ ] "Capture a recorded listing's normalized facts, source time, HTML and referenced media with stable evidence locators."
    - [ ] "Disallowed hosts, private IP redirects, oversized downloads and unsupported media fail with explicit codes and no internal network fetch."
    - [ ] "Dogfood starts browser tasks with `docker compose run --rm browser-task` through a docker-socket proxy limited to create/start/wait/remove of the pinned browser image; ordinary tasks run as subprocesses of the scheduler container."
    - [ ] "The email parser, TheMLS browser, manual add and document upload each write `source_observations` and never insert `properties` directly" (browser part).
    - [ ] "A test seeds known secret values and asserts they never appear in … stored evidence." (evidence part)
    - [ ] Error table rows `source_changed`, `source_removed` and `rate_limited`; "Never silently truncate evidence and mark it complete."
- [ ] **Verify:** Run one browser task locally with `docker compose run --rm browser-task` against the fixture page; the container exits and evidence rows exist.

---

### Task 2.4: Evidence Access API and `get_evidence_metadata`

Serve authorized evidence metadata and short-lived signed downloads, add the phase 2 MCP tool, and create the source-rights record.

**Modules:** `backend`, `frontend` (generated client), `specs`
**Depends on:** 2.3
**Decisions:** D44, D35, D7, D29

**Files:**

```
backend/src/property_focus/adapters/storage/objects.py           (modify)
backend/src/property_focus/application/context.py                (modify)
backend/src/property_focus/application/ingestion.py              (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/src/property_focus/adapters/mcp/server.py                (modify)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
specs/property-focus/source-rights.md                            (create)
backend/tests/integration/test_tenancy.py                        (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/contract/test_mcp.py                               (modify)
```

**Sub-tasks:**

- [ ] **2.4.1** — Implement `read_authorized` and entitlement checks: evidence shared through an entitlement group requires an explicit membership join; account-scoped evidence is visible only to its account.
- [ ] **2.4.2** — Add `GET /v1/properties/{id}/evidence` (authorized metadata, no storage credentials) and `POST /v1/evidence/{id}/download` (signed URL, default expiry 5 minutes, entitlement checked at issuance).
- [ ] **2.4.3** — Add the MCP tool `get_evidence_metadata` with a parity case; content access uses authorized application reads, not public object URLs.
- [ ] **2.4.4** — Create `specs/property-focus/source-rights.md` with sections for rights, retention classes, entitlement groups, Object Lock eligibility per evidence class and the TheMLS session findings; mark every policy entry as pending the user's decision (CP-2.A).
- [ ] **2.4.5** — Tests: account A requesting account B's private evidence ID gets an indistinguishable `404` and no signed URL; a signed URL expires; entitlement revoked after issuance blocks a new URL.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-INT` passes the tenancy, contract and parity cases in 2.4.5.
- [ ] **Requirements:**
    - [ ] "signed downloads expire and check entitlement at issuance."
    - [ ] "A token for account A cannot … obtain signed URLs for account B's private records" and key scenario "Account A tries account B's private evidence ID | Indistinguishable 404; no signed URL".
    - [ ] "Evidence shared through an entitlement group MUST require an explicit membership join."
    - [ ] MCP tool list: "`get_evidence_metadata` (phase 2)"; "Evidence content access uses authorized application reads, not public object URLs."
- [ ] **Verify:** Call `POST /v1/evidence/{id}/download` and confirm the returned expiry is 5 minutes after issue time.

---

### Task 2.5: TheMLS Enrichment Exit Evidence

Record the phase 2 exit evidence and the source session drill for the Intelligence ready gate.

**Modules:** `backend`, `specs`
**Depends on:** 2.1–2.4, CP-2.B
**Decisions:** D23, D11, D44

**Files:**

```
backend/tests/replay/test_golden_replay.py                       (modify)
backend/scripts/smoke_dogfood.py                                 (modify)
specs/property-focus/gates/intelligence-ready.md                 (create)
```

**Sub-tasks:**

- [ ] **2.5.1** — Add a replay case: recorded listing capture, expired session, operator resume, recapture; evidence digests unchanged and no evidence overwritten.
- [ ] **2.5.2** — Extend `smoke_dogfood.py` to check launched tasks and authorized evidence access for the synthetic account.
- [ ] **2.5.3** — Write the phase 2 section of `intelligence-ready.md`: source session drill steps and results, capture evidence audit, phase 2 MCP parity.

**Validation:**

- [ ] **Build:** `VERIFY` exits 0.
- [ ] **Tests:** `BE-REPLAY` passes the new capture replay case.
- [ ] **Requirements:**
    - [ ] Phase 2 exit: "Captured listing with expired-session recovery and immutable evidence."
    - [ ] Intelligence ready artifact: "Source session drill" and "phase 2–3 MCP parity" (phase 2 part).
- [ ] **Verify:** `intelligence-ready.md` phase 2 section links the drill and replay output. **Live (explicit user approval required):** `make smoke-dogfood` with evidence access.

---

## Phase 3: Property Intelligence

**Goal:** Ontology v1 in Postgres, claims with the four value statuses, versioned search subscriptions with owner membership, the deterministic hard pre-filter, requirements evaluation, the model router with OpenAI and Anthropic adapters, the first analyzer set, baseline and deep tiers with manual triggers, the budget ledger, invalidation, human corrections, the VOI queue and the evaluation harness with shadow runs. **Deliverable:** the Intelligence ready gate (phases 2–3).

> **Checkpoint CP-3.A (blocking for the first live paid analyzer run) — AI budget per tier and useful latency target.** Owner: user and operator, before the first paid analyzer run in phase 3. Question: what budget limit per budget tier (`BASELINE`, `DEEP`, `EXPERIMENT`) and what latency target apply? Tasks 3.1–3.14 use configurable conservative limits, the fake provider and recorded responses. No task makes a live paid provider call (including `make test-live` provider checks) until the user answers and the limits are set in configuration.
>
> **Checkpoint CP-3.B (blocking for any public-record provider) — public-record provider for `ASSESSOR`, `PERMITS`, `ZONING`, `TAX_HISTORY`.** Owner: user, before the provider enters a phase. No provider is chosen. Phase 3 tasks run the deep tier without these capabilities and mark them `DEFERRED` with attributes `UNKNOWN`. Do not add any public-record provider adapter until the user names a permitted provider.

### Task 3.1: Ontology v1 in Postgres

Store the ontology as immutable versions with a draft/publish lifecycle, a code registry of resolution rules and a seed of at least 75 attributes.

**Modules:** `backend`, `ontology`
**Depends on:** 2.5
**Decisions:** D32, D33, D7

**Files:**

```
ontology/schema/attribute.schema.json                            (create)
ontology/v1.0.0/attributes.json                                  (create)
ontology/CHANGELOG.md                                            (modify)
backend/src/property_focus/domain/ontology.py                    (create)
backend/src/property_focus/domain/resolution_rules.py            (create)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/db/repositories.py           (modify)
backend/src/property_focus/cli.py                                (modify)
backend/migrations/versions/0006_ontology.py                     (create)
backend/tests/unit/test_ontology.py                              (create)
README.md                                                        (modify)
```

**Sub-tasks:**

- [ ] **3.1.1** — Migration `0006`: `ontology_versions` (semantic version, state, digest, published_at, published_by; partial unique index for one `ACTIVE`) and `ontology_attributes` (the plan's columns; unique version/key); triggers make non-`DRAFT` versions and their attributes immutable. Add a `NOT VALID` check so every new `analysis_runs` row records `ontology_version`.
- [ ] **3.1.2** — Write `attribute.schema.json` and `domain/resolution_rules.py`: a code registry of named rules with scalar parameters, including `official_record_first`, `human_first_for_visible` and a current-listing rule for list price.
- [ ] **3.1.3** — Write `ontology/v1.0.0/attributes.json`: at least 75 attributes covering `structure`, `rooms`, `layout`, `indoor_outdoor`, `yard`, `garage`, `condition`, `kitchen`, `architecture`, `location`, `risks`, `development_potential` (plus `listing`), with the plan's representative keys and every D42 traffic key; each attribute has category, subject type, value type, unit or explicit unitless marker, allowed values for enumerations, a registered rule name and a default invalidation mode. Seed no buyer desired values as facts.
- [ ] **3.1.4** — Implement `domain/ontology.py` validation (schema, scalar-only parameters, registered rule, at least 75 attributes, every category present) and version compatibility: a breaking change requires a new major version with an explicit attribute mapping recorded in `CHANGELOG.md`; old claims are never reinterpreted.
- [ ] **3.1.5** — Implement `cli ontology load <dir>` (validate, insert `DRAFT`) and `cli ontology publish <version>` (`DRAFT` to `ACTIVE`, prior `ACTIVE` to `RETIRED`, one transaction). Update `README.md` bootstrap.
- [ ] **3.1.6** — Tests in `test_ontology.py`: 74 attributes fails; a missing category fails; a non-scalar parameter fails; an unregistered rule fails; publish transitions; editing a non-`DRAFT` version fails (integration); coverage report per category.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0; `cli ontology load ontology/v1.0.0` then `cli ontology publish 1.0.0` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 3.1.6.
- [ ] **Requirements:**
    - [ ] "`cli ontology load ontology/v1.0.0` validates the seed against `ontology/schema/attribute.schema.json` and inserts a `DRAFT` version; `cli ontology publish <version>` makes it `ACTIVE` and the prior `ACTIVE` version `RETIRED` in one transaction. Updating a non-`DRAFT` version's attributes fails."
    - [ ] "ACTIVE v1 contains at least 75 attributes, each with category, subject type, value type, unit or explicit unitless marker, allowed values for enumerated types, and a resolution rule name registered in `domain/resolution_rules.py`. Each of the twelve categories … has at least one attribute (`test_ontology.py`)."
    - [ ] "A seed whose rule parameter is not a scalar, or whose rule name is not registered, fails load; JSONB holds no expressions."
    - [ ] "A breaking change creates a new major version with an explicit attribute mapping; old claims are never reinterpreted."
    - [ ] Key scenario: "Ontology seed with 74 attributes, or a missing category | `ontology load` fails".
- [ ] **Verify:** `jq length ontology/v1.0.0/attributes.json` prints 75 or more; `jq -r '.[].key' ontology/v1.0.0/attributes.json | grep -c '^location\.'` covers the ten D42 traffic keys.

---

### Task 3.2: Claims, Resolver and Intelligence Profiles

Store immutable claims with mandatory evidence, resolve them to the four value statuses, and build reproducible, scope-aware intelligence profiles.

**Modules:** `backend`
**Depends on:** 3.1
**Decisions:** D7, D12, D35, D36, D2, D8

**Files:**

```
backend/src/property_focus/domain/claims.py                      (create)
backend/src/property_focus/domain/resolution_rules.py            (modify)
backend/src/property_focus/application/intelligence.py           (create)
backend/src/property_focus/application/ingestion.py              (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/migrations/versions/0007_claims_profiles.py              (create)
backend/tests/unit/test_claims.py                                (create)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **3.2.1** — Migration `0007`: `claims` (subject, attribute key, typed JSONB value, ontology version, provider, source type `SOURCE`/`ANALYZER`/`HUMAN`/`PROVIDER`, observed time, confidence method/value, analysis run or author, scope; immutable), `claim_evidence` (locator kinds `ASSET`, `IMAGE_REGION`, `FLOORPLAN_REGION`, `TEXT_SPAN`, `RECORD`, `PROVIDER_RESPONSE`; locator JSON) with a deferred constraint that rejects an `ANALYZER` claim without evidence at commit, and `intelligence_profiles` (immutable revisions).
- [ ] **3.2.2** — Implement `domain/claims.py`: `ValueStatus` with exactly `OBSERVED`, `INFERRED`, `UNKNOWN`, `CONFLICTING`; confidence never turns an inference into an observation; staleness is a separate `invalidated_at` field.
- [ ] **3.2.3** — Convert source observations into `SOURCE` claims for listing-fact attributes; a field reported differently by two sources keeps both claims with their provider and source type.
- [ ] **3.2.4** — Implement the deterministic resolver: per-attribute rule; missing attribute resolves to `UNKNOWN`; unresolvable conflict yields `CONFLICTING` and keeps both claims; `HUMAN` outranks `ANALYZER` under `human_first_for_visible`; under `official_record_first` a `HUMAN` claim without supporting evidence does not outrank an assessor claim.
- [ ] **3.2.5** — Implement profile assembly in `intelligence.py`: value status, value, confidence, claim and evidence IDs, winning analyzer version, input digest, scope, completeness (share of `ACTIVE` attributes not `UNKNOWN`), ontology and resolver versions; identical inputs give the same canonical output digest.
- [ ] **3.2.6** — Include the entitlement scope in every profile cache key so a profile built from evidence restricted to one account is never served to another account.
- [ ] **3.2.7** — Tests: `test_claims.py` enumerates the status type; contradictory bedroom claims resolve deterministically or to `CONFLICTING` with both evidence links; `HUMAN` versus analyzer kitchen quality; `HUMAN` versus assessor parcel ID without evidence; replay digest equality; `ANALYZER` claim without evidence fails (integration); restricted-evidence profile not served to another account (`test_tenancy.py`).

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 3.2.7.
- [ ] **Requirements:**
    - [ ] "Every resolved value carries `status` of `OBSERVED`, `INFERRED`, `UNKNOWN` or `CONFLICTING`, plus value, confidence, evidence IDs and the analyzer version of the winning claim. No other status token exists in schemas or code (`test_claims.py` enumerates the type)."
    - [ ] "A missing attribute resolves to `UNKNOWN` and never evaluates as false in any requirement or score."
    - [ ] "A fixed conflict fixture resolves deterministically under the attribute's rule and retains both claims; an unresolvable conflict yields `CONFLICTING`."
    - [ ] "A `HUMAN` claim outranks `ANALYZER` claims for attributes whose rule is `human_first_for_visible`; for attributes using `official_record_first` it does not outrank an assessor claim unless it attaches supporting evidence. The original analyzer claim and run remain."
    - [ ] "Profile output includes value status, evidence/claim IDs, input digest, scope, completeness, ontology version and resolver version; replaying identical inputs and versions yields the same canonical output digest."
    - [ ] "Every inferred claim has at least one `claim_evidence` row with a durable evidence asset ID and locator; inserting an `ANALYZER` claim without evidence fails a database constraint."
    - [ ] "A field reported differently by two sources keeps both claims with their provider and source type."
    - [ ] "A profile built from evidence restricted to one account is not served to another account through shared cache lookup." and "A cached result must include its entitlement scope in the cache key."
    - [ ] "Every `analysis_runs` and `claims` row records the ontology version" (claims part).
- [ ] **Verify:** `grep -rnE "'(STALE|PENDING_VALUE|FALSE)'" backend/src/property_focus/domain/claims.py` returns nothing.

---

### Task 3.3: Invalidation

Record invalidations separately from recomputation with the four modes, the `invalidation-batch` task and the evidence tombstone procedure.

**Modules:** `backend`, `infrastructure/dogfood`
**Depends on:** 3.2
**Decisions:** D13, D53, D8

**Files:**

```
backend/src/property_focus/domain/invalidation.py                (create)
backend/src/property_focus/application/intelligence.py           (modify)
backend/src/property_focus/runtime/scheduled.py                  (modify)
backend/src/property_focus/cli.py                                (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/migrations/versions/0008_invalidations.py                (create)
infrastructure/dogfood/scheduler.crontab                         (modify)
backend/tests/unit/test_invalidation.py                          (create)
```

**Sub-tasks:**

- [ ] **3.3.1** — Migration `0008`: `invalidations` (subject, capability or attribute, cause `SOURCE_CHANGE`/`ONTOLOGY_VERSION`/`ANALYZER_VERSION`/`HUMAN_CLAIM`/`TTL`, mode `EVENT`/`LAZY`/`BATCH`/`MANUAL`, invalidated_at, resolved_by analysis run).
- [ ] **3.3.2** — Implement `domain/invalidation.py`: map a changed input to its dependent capabilities and attributes using each attribute's default invalidation mode.
- [ ] **3.3.3** — `EVENT`: a source price change writes an invalidation and launches recomputation of dependent components only. `LAZY`: recompute on the next profile read. `BATCH`: publishing a new ontology version writes invalidations for affected attributes and starts no analysis run. `MANUAL`: wait for `POST /properties/{id}/reanalyze` (Task 3.11).
- [ ] **3.3.4** — Register the `invalidation-batch` scheduled kind (hourly crontab entry) that processes pending `BATCH` invalidations.
- [ ] **3.3.5** — Implement the evidence deletion/retention procedure: record tombstones and invalidate dependent published profiles where source rights require removal.
- [ ] **3.3.6** — Tests: each mode; ontology publish writes `BATCH` invalidations and no run until the sweep; a stale value keeps its status and shows `invalidated_at`; tombstone invalidates dependent profiles.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` passes `test_invalidation.py` (modes, ontology-change eligibility, staleness) and `test_runtime_manifests.py` accepts `invalidation-batch`.
- [ ] **Requirements:**
    - [ ] "A source price change writes an `EVENT` invalidation and launches recomputation of dependent components only; an attribute with `LAZY` mode recomputes on the next read of the profile; publishing a new ontology version writes `BATCH` invalidations for affected attributes and starts no analysis run until `invalidation-batch` runs; `MANUAL` waits for `POST /properties/{id}/reanalyze` (`test_invalidation.py`)." (`MANUAL` completed in Task 3.11)
    - [ ] "A stale value displays its invalidation time and keeps its status; staleness is not a value status."
    - [ ] "A correction creates another record and preserves the prior evidence. A deletion/retention procedure records tombstones and invalidates dependent published views where source rights require removal."
    - [ ] Key scenario: "New ontology version published | `BATCH` invalidations written; no analysis run until the batch sweep".
- [ ] **Verify:** Publish a test ontology version in a scratch database and confirm `analysis_runs` count is unchanged until `cli run-task invalidation-batch` runs.

---

### Task 3.4: Search Subscription Domain, Schema and Membership

Create immutable subscription versions, the subscription materiality classifier, search membership with an owner row, and search-scoped RLS.

**Modules:** `backend`
**Depends on:** 3.1
**Decisions:** D16, D17, A1, D5 (surviving parts)

**Files:**

```
backend/src/property_focus/domain/subscriptions.py               (create)
backend/src/property_focus/application/subscriptions.py          (create)
backend/src/property_focus/application/context.py                (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/migrations/versions/0009_search_subscriptions.py         (create)
backend/tests/unit/test_subscriptions.py                         (create)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **3.4.1** — Migration `0009`: `search_subscriptions`, `search_subscription_versions` (append-only, immutable by trigger, the plan's fields incl. strategy `owner_occupancy`/`live_in_and_rent`/`rental_hold`, strategy criteria, origin `EXPLICIT`/`LEARNED`/`INTERVIEW`/`REVERT`/`PROFILE_COPY`, created_by, digest) with a constraint that a `LEARNED` or `INTERVIEW` version has evidence refs, and `search_members` (partial unique indexes: one active row per search/user, one active `owner` per search; removal sets `removed_at`, never deletes).
- [ ] **3.4.2** — In migration `0009`, grant the runtime role access to the new tables and create search-scoped forced RLS policies: a row is visible only when the transaction's user has an active `search_members` row for that search. Do not edit `rls.sql`.
- [ ] **3.4.3** — Implement criteria validation and the subscription materiality classifier: `MATERIAL` when geography or budget changes, a hard criterion is added, removed or edited, strategy changes, or a soft-criterion weight moves by more than 0.05 normalized share; everything else `LOW_IMPACT`.
- [ ] **3.4.4** — Implement the create use case: subscription, first version and active `owner` member row in one transaction.
- [ ] **3.4.5** — Implement the per-request membership check in `context.py`: read `search_members` inside the transaction on every search-scoped call, never token claims. Encode rights: the owner cannot leave or be removed.
- [ ] **3.4.6** — Tests: table-driven materiality rules; a version row cannot be updated; a `LEARNED` version without evidence refs fails; owner row created with the subscription; a non-member sees no rows through RLS.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` (`test_subscriptions.py` materiality table and evidence rule) and `BE-INT` (RLS and owner-row cases) pass.
- [ ] **Requirements:**
    - [ ] "`domain/subscriptions.py` classifies a subscription diff as `MATERIAL` when it changes geography or budget; adds, removes or edits a hard criterion; changes strategy (plan recommendation); or moves a soft-criterion weight by more than 0.05 normalized share. Everything else, such as name or notification policy, is `LOW_IMPACT`. Table-driven tests cover each rule."
    - [ ] "Every version records origin, `created_by` and evidence refs; a `LEARNED` or `INTERVIEW` version without evidence refs fails a database constraint."
    - [ ] "Phase 3: `POST /search-subscriptions` inserts an active `owner` row in `search_members` in the same transaction."
    - [ ] "Search-scoped tables … MUST be keyed by a non-null search subscription ID under forced RLS that admits a row only when the transaction's user has an active `search_members` row for that search."
    - [ ] "A migration that adds an account-private or search-scoped table creates that table's runtime-role grants and forced RLS policies in the same migration" (`rls.sql` unchanged; `test_migrations.py` passes).
    - [ ] "Authorization reads `search_members` on every request and never relies on token claims."
- [ ] **Verify:** In psql as the runtime role with another user's context, `select count(*) from search_subscription_versions` returns 0 for a search the user does not belong to.

---

### Task 3.5: Subscription REST Routes and Phase 3 Subscription MCP Tools

Expose subscription create, edit with `If-Match`, version history and revert, with material-change confirmation and non-member `404`s through REST and MCP.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 3.4
**Decisions:** D16, D17, D30, D29, A1

**Files:**

```
backend/src/property_focus/application/subscriptions.py          (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/src/property_focus/adapters/mcp/server.py                (modify)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_subscriptions.py                         (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/contract/test_mcp.py                               (modify)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **3.5.1** — Add `POST /v1/search-subscriptions` (`201 {search_subscription_id, version}`, ETag; caller becomes `owner`) and `GET /v1/search-subscriptions` (searches where the caller is an active member, with role and current versions).
- [ ] **3.5.2** — Add `PATCH /v1/search-subscriptions/{id}` with `If-Match`: `200` with the new version; `412` on a stale ETag; `409 material_change_requires_confirmation` listing material items unless `confirm_material: true`; the confirmed version records the member as `created_by`. Any member may patch.
- [ ] **3.5.3** — Add `GET /v1/search-subscriptions/{id}/versions` and `POST /v1/search-subscriptions/{id}/revert` (`{version}`, `If-Match`; new version with origin `REVERT` and the target's content digest).
- [ ] **3.5.4** — Reject a subscription version that belongs to another subscription in REST and MCP inputs.
- [ ] **3.5.5** — Add MCP tools `list_search_subscriptions` (active memberships only) and `create_search_subscription` (write) with parity cases.
- [ ] **3.5.6** — Tests: geography change without and with `confirm_material`; stale `If-Match`; revert digest equality; a non-member gets the same `404` body as a nonexistent search ID on every route and tool, reads and writes.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 3.5.6.
- [ ] **Requirements:**
    - [ ] "`POST /search-subscriptions` creates a subscription and its first immutable `search_subscription_versions` row. `PATCH /search-subscriptions/{id}` with `If-Match` creates a new version and returns `200` with it; a stale ETag returns `412`; prior versions remain readable through `GET /search-subscriptions/{id}/versions`; no code path updates a version row (`test_subscriptions.py`)."
    - [ ] "Any member's `PATCH` with a `MATERIAL` diff returns `409` with code `material_change_requires_confirmation` and the material items unless the body sets `confirm_material: true`; the confirmed version records that member as `created_by`."
    - [ ] "`POST /search-subscriptions/{id}/revert` with `{version}` and `If-Match` creates a new version with origin `REVERT` whose content digest equals the target version's."
    - [ ] "Every search-scoped REST route and MCP tool returns `404` to an account without an active membership, on reads and writes alike, with the same body as a nonexistent search ID (`test_tenancy.py`, `test_mcp.py`)."
    - [ ] "REST and MCP reject a subscription version from another subscription. Check active search membership and input entitlements on the server for every search-scoped request, regardless of the active route."
    - [ ] MCP tool list: "`list_search_subscriptions` and `create_search_subscription` (phase 3)"; "`list_search_subscriptions` returns only searches where the caller is an active member."
    - [ ] Key scenario: "`PATCH /search-subscriptions/{id}` changes geography without `confirm_material` | `409 material_change_requires_confirmation`; with it, `200` and a new version with evidence; stale `If-Match` gives `412`".
- [ ] **Verify:** `grep -rn "UPDATE search_subscription_versions\|update(SearchSubscriptionVersion" backend/src` returns nothing.

---

### Task 3.6: Hard Pre-Filter, Requirements Evaluation and Ranked Feed

Run the deterministic `prefilter_v1` stage over every active subscription, evaluate requirements with three-valued logic, and serve the requirements-grouped feed and evaluations.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 3.3, 3.5
**Decisions:** D3, D9, D7, A1, D29

**Files:**

```
backend/src/property_focus/domain/funnel.py                      (create)
backend/src/property_focus/domain/evaluation.py                  (create)
backend/src/property_focus/application/evaluation.py             (create)
backend/src/property_focus/application/subscriptions.py          (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/src/property_focus/adapters/mcp/server.py                (modify)
backend/migrations/versions/0010_evaluations_funnel.py           (create)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_funnel.py                                (create)
backend/tests/unit/test_evaluation.py                            (create)
backend/tests/contract/test_api.py                               (modify)
backend/tests/contract/test_mcp.py                               (modify)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **3.6.1** — Migration `0010`: `property_evaluations` (the plan's columns; unique input digest; search-scoped RLS) and `funnel_decisions` (property, stage name and version, mode `PUBLISHED`/`SHADOW`, outcome, created_at; holds no tenant-private criteria).
- [ ] **3.6.2** — Implement the effective hard-requirement set in `domain/funnel.py`: the subscription version's hard criteria (copied profile requirements join in Task 4.3); no private profile joins live.
- [ ] **3.6.3** — Implement `prefilter_v1`: evaluate listing-fact claims against every active subscription across all tenants through an explicit system authorization path (no null-tenant shortcut); a property survives when any subscription returns pass or unknown; record the stage version on each decision; run a candidate version in shadow and report disagreements without changing outcomes; no LLM.
- [ ] **3.6.4** — Implement requirements evaluation in `domain/evaluation.py`: hard false gives `fail`; hard unknown gives `unknown` unless another criterion fails; `pass` needs all true; phase 3 evaluations carry null dimension scores and overall.
- [ ] **3.6.5** — On subscription create or a material change, write `BATCH` invalidations and re-run the pre-filter over properties with an active listing; emit the event that starts a `BASELINE` run for a newly surviving property (run wiring in Task 3.10). Editing one subscription leaves another subscription's evaluations intact.
- [ ] **3.6.6** — Add `GET /v1/properties?search_subscription_id=` (order `pass`, `unknown`, `fail`, then overall descending, then `id`; `fail` excluded unless `requirements=fail`; cursor bound to scope and evaluation revision), `GET /v1/properties/{id}/evaluation`, the evaluation in `GET /v1/properties/{id}` when a subscription is supplied, and MCP `get_property_evaluation` with a parity case.
- [ ] **3.6.7** — Tests: `test_funnel.py` (decisions, effective set, shadow disagreement report, runs with no provider configured); `test_evaluation.py` (fail precedence, unknown handling, reproducibility for a fixed profile revision and subscription version); feed ordering and `fail` exclusion (contract); two subscriptions under one account evaluated independently; non-member `404` on evaluation routes and tool.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 3.6.7.
- [ ] **Requirements:**
    - [ ] "`domain/funnel.py` stage `prefilter_v1` evaluates listing-fact claims against the effective hard-requirement set of every active subscription across all tenants; a property with known facts failing every subscription gets no `BASELINE` paid call; pass or unknown for any subscription lets it survive." (zero-reservation assertion in Task 3.10)
    - [ ] "Before phase 4 no profile exists, so the set is the subscription's own hard criteria."
    - [ ] "Creating a subscription or materially changing its criteria, including applying a profile, writes `BATCH` invalidations and re-runs the pre-filter over properties with an active listing; a newly surviving property receives a `BASELINE` run."
    - [ ] "The stage runs without an LLM in `backend/tests/unit/test_funnel.py`, records its version on each decision, and can run a candidate version in shadow, reporting disagreements without changing outcomes."
    - [ ] "Requirements (phase 3, over the effective hard-requirement set): hard false yields `fail`; hard unknown yields `unknown` unless another criterion fails, in which case `fail` takes precedence; `pass` requires all hard criteria true. Phase 3 evaluations carry requirements only, with null dimension scores and overall."
    - [ ] "Exclude `fail` homes from the default feed; `requirements=fail` shows them. `GET /properties?search_subscription_id=` orders by requirements group (`pass`, `unknown`, `fail`), then overall descending under the subscription's scoring version, then `id`."
    - [ ] "Under one sign-in, create two subscriptions with distinct strategy criteria. Editing one subscription leaves the other's evaluations intact. … Include subscription ID and version in evaluation caches."
    - [ ] Key scenario: "Missing hard requirement and high soft score | Requirements `unknown`; no confirmed pass; listed after all `pass` homes".
    - [ ] MCP tool list: "`get_property_evaluation` (phase 3)".
- [ ] **Verify:** Run `test_funnel.py` with no model provider configured and network disabled; it passes.

---

### Task 3.7: Model Provider Contract, Router, OpenAI and Anthropic Adapters

Add the `ModelProvider` contract with structured validation, the capability-based `ModelRouter`, both provider adapters, a fake provider, and the shared contract test over recorded responses.

**Modules:** `backend`, `fixtures`
**Depends on:** 0.7
**Decisions:** D31, A2, D49

**Files:**

```
backend/src/property_focus/adapters/ai/__init__.py               (create)
backend/src/property_focus/adapters/ai/base.py                   (create)
backend/src/property_focus/adapters/ai/router.py                 (create)
backend/src/property_focus/adapters/ai/openai.py                 (create)
backend/src/property_focus/adapters/ai/anthropic.py              (create)
backend/src/property_focus/adapters/ai/fake.py                   (create)
backend/src/property_focus/application/ports.py                  (modify)
backend/src/property_focus/config.py                             (modify)
fixtures/providers/openai/recorded-responses.json                (create)
fixtures/providers/anthropic/recorded-responses.json             (create)
backend/tests/unit/test_ai.py                                    (create)
backend/tests/live/test_providers.py                             (modify)
```

**Sub-tasks:**

- [ ] **3.7.1** — Define `ModelProvider`, `ModelRouter`, `ModelCapability` (`TEXT`, `VISION`, `STRUCTURED_OUTPUT`), and Pydantic `InferenceRequest`/`InferenceResult` with the plan's fields.
- [ ] **3.7.2** — Implement `base.py`: validate output against `output_schema`; allow one bounded repair attempt under budget, then classify `schema_invalid` and keep safe metadata; record provider, model, model version, prompt hash, schema hash, input evidence IDs, usage, latency, finish reason and result digest; never log private source text.
- [ ] **3.7.3** — Implement `router.py`: select a provider by capability and the model policy in `config.py`; return `unsupported_capability` before any paid call when no configured provider offers the capability; fallback keeps requested capabilities and privacy constraints and never starts an unlimited cascade of paid attempts.
- [ ] **3.7.4** — Implement `openai.py` and `anthropic.py`, credentials through `SecretProvider`; implement `fake.py` for development and tests with no network.
- [ ] **3.7.5** — Treat evidence as data: no tool definitions reach providers; source text cannot change the schema, access credentials or authorize a write.
- [ ] **3.7.6** — Tests in `test_ai.py`: the shared provider contract runs against both adapters with recorded responses and no network; schema rejection and one repair; `unsupported_capability` before charge; for each capability, remapping it in `config.py` from one adapter to the other routes the next request to the other adapter; a prompt-injection fixture cannot add tools or change the schema; seeded secrets never appear in request payloads.
- [ ] **3.7.7** — Extend `backend/tests/live/test_providers.py` with opt-in live checks for both providers (run only after CP-3.A).

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` passes `test_ai.py` with the cases in 3.7.6 and no network access.
- [ ] **Requirements:**
    - [ ] "`adapters/ai/router.py` selects a provider by capability (`VISION`, `STRUCTURED_OUTPUT`, `TEXT`) and the model policy in `config.py`; a request for a capability no configured provider offers returns `unsupported_capability` before a paid call."
    - [ ] "With both adapters configured, changing the provider mapped to a capability in `config.py` routes the next run for that capability to the other provider with no analyzer code change; `test_ai.py` asserts this for each capability (A2)."
    - [ ] "A fake provider can exercise analyzers without network access. The OpenAI (`adapters/ai/openai.py`) and Anthropic (`adapters/ai/anthropic.py`) adapters ship with the first analyzer set in phase 3, and both pass the same provider contract in `backend/tests/unit/test_ai.py`, using recorded provider responses and no network, at the Intelligence ready gate (A2)."
    - [ ] "Invalid output is rejected; permit one bounded repair attempt under budget, then classify `schema_invalid` and preserve safe metadata."
    - [ ] "Record provider, model, prompt hash, schema hash, input evidence IDs, usage, latency and result digest. Do not log private source text."
    - [ ] "A malicious instruction embedded in email or HTML cannot call tools, change the schema, access credentials or authorize a write."
    - [ ] "Provider fallback preserves requested capabilities and privacy constraints, and a failed call does not authorize an unlimited cascade of paid attempts."
    - [ ] "A test seeds known secret values and asserts they never appear in … model request payloads" (payload part).
- [ ] **Verify:** Run `BE-UNIT` with networking blocked (for example `unshare -n` or a socket-blocking pytest plugin); `test_ai.py` passes. **Live (explicit user approval required, after CP-3.A):** `PF_LIVE=1 make test-live` provider checks.

---

### Task 3.8: Budget Ledger and Tier Reservations

Reserve estimated spend per budget tier before every provider call, attribute usage, and reconcile uncertain outcomes.

**Modules:** `backend`
**Depends on:** 3.7
**Decisions:** D56, D54, D55

**Files:**

```
backend/src/property_focus/application/budgets.py                (create)
backend/src/property_focus/application/workflows.py              (modify)
backend/src/property_focus/runtime/scheduled.py                  (modify)
backend/src/property_focus/config.py                             (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/migrations/versions/0011_budgets.py                      (create)
backend/tests/unit/test_budgets.py                               (create)
backend/tests/integration/test_workflows.py                      (modify)
```

**Sub-tasks:**

- [ ] **3.8.1** — Migration `0011`: `budget_reservations` and `usage_events` with the plan's columns and a unique provider operation reference where available.
- [ ] **3.8.2** — Configure budget-tier limits (`BASELINE`, `DEEP`, `EXPERIMENT`), period and reset timezone in `config.py` with conservative defaults; analyzers hold no cost limits.
- [ ] **3.8.3** — Implement reservation: reserve before every provider call against the paying account's tier budget; paid work started for a search charges the search owner's account; a member's `MANUAL` request charges that member's account; concurrent reservations cannot exceed the available budget.
- [ ] **3.8.4** — Record analysis tier, budget tier, property, workflow run, analysis run, cost category, analyzer, model and provider on every reservation and usage event; one usage event per paid operation; shared costs carry one explicit owner; an unknown cost is flagged, never stored as zero.
- [ ] **3.8.5** — On exhaustion return `budget_exceeded`: optional calls are not made, the component becomes `DEFERRED`, intake and existing results stay usable.
- [ ] **3.8.6** — Reconcile in the `repair` task: expire or reconcile reservations after uncertain outcomes; `provider_outcome_unknown` keeps the reservation and is never retried automatically; re-call only when the provider reports no prior effect.
- [ ] **3.8.7** — Tests: payer rules; two paid calls competing for the remaining tier funds (at most the supported reservation succeeds); exhaustion defers; crash after provider success before step completion keeps one committed result and one usage event.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` (`test_budgets.py`) and `BE-INT` (`test_workflows.py` budget race and ambiguity cases) pass.
- [ ] **Requirements:**
    - [ ] "Phase 3: `application/budgets.py` reserves estimated spend in `budget_reservations` before every provider call against the paying account's budget for the call's budget tier (`BASELINE`, `DEEP`, `EXPERIMENT`), configured in `config.py`; analyzers contain no cost limits. Paid work started for a search charges the search owner's account; a member's `MANUAL` request charges that member's account (plan recommendation). Each reservation and usage event records analysis tier, budget tier, property, workflow run, analysis run, cost category, analyzer, model and provider. Concurrent tasks cannot reserve more than the available budget (`test_workflows.py`); reservations expire or reconcile after uncertain outcomes through a repair task."
    - [ ] "Every paid operation has one usage event linked to its workflow run and payer; shared costs have an explicit owner rather than duplicate full charges to multiple tenants."
    - [ ] "Exhausted budget prevents new optional provider calls, marks those components `DEFERRED` and leaves ingestion and existing-result access usable."
    - [ ] "… after provider success but before workflow step completion: retries preserve one committed domain result and account for possible provider-side ambiguity."
    - [ ] Key scenario: "Two paid calls compete for remaining budget-tier funds | At most the budget-supported reservation succeeds".
- [ ] **Verify:** Run the race test 20 times (`pytest -k budget_race --count 20` or a loop); it never over-reserves.

---

### Task 3.9: Analyzer Framework and Baseline Analyzers

Add the analyzer protocol and registry, computation-key reuse, and the four baseline analyzers with versioned prompts.

**Modules:** `backend`
**Depends on:** 3.2, 3.7, 3.8
**Decisions:** D14, D54, D8, D35, D31

**Files:**

```
backend/src/property_focus/analyzers/__init__.py                 (create)
backend/src/property_focus/analyzers/base.py                     (create)
backend/src/property_focus/analyzers/text.py                     (create)
backend/src/property_focus/analyzers/photo.py                    (create)
backend/src/property_focus/analyzers/condition.py                (create)
backend/src/property_focus/analyzers/kitchen.py                  (create)
backend/src/property_focus/analyzers/prompts/text-v1.md          (create)
backend/src/property_focus/analyzers/prompts/photo-v1.md         (create)
backend/src/property_focus/analyzers/prompts/condition-v1.md     (create)
backend/src/property_focus/analyzers/prompts/kitchen-v1.md       (create)
backend/src/property_focus/application/intelligence.py           (modify)
backend/tests/unit/test_analyzers.py                             (create)
```

**Sub-tasks:**

- [ ] **3.9.1** — Implement `analyzers/base.py`: `Analyzer` protocol and registry; each analyzer declares name, version, analysis tier, required model capabilities, input evidence kinds and output attribute keys.
- [ ] **3.9.2** — Validate outputs: a claim for an undeclared key or a key absent from the ontology fails validation and publishes nothing.
- [ ] **3.9.3** — Compute the computation key from input evidence digests plus analyzer, prompt, model and ontology versions; an identical key reuses the stored run; any changed version creates a new run; no run is overwritten.
- [ ] **3.9.4** — Implement `TextAnalyzer`, `PhotoAnalyzer`, `ConditionAnalyzer` and `KitchenAnalyzer` with versioned prompts; each emits `INFERRED` claims with confidence and `claim_evidence` locators (`TEXT_SPAN`, `IMAGE_REGION`, `ASSET`).
- [ ] **3.9.5** — Persist each run with provider, model, model version, prompt version, analyzer version, ontology version, tokens, cost, latency, confidence and structured result, reserving budget through Task 3.8 first.
- [ ] **3.9.6** — Tests with the fake provider: undeclared key rejected; invented ontology key rejected; computation-key reuse and new run on version change; run record fields complete; prompt injection in source HTML causes no tool execution or policy change.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` passes `test_analyzers.py` cases in 3.9.6.
- [ ] **Requirements:**
    - [ ] "Each analyzer in `analyzers/` declares name, version, analysis tier, required capabilities, input evidence kinds and output attribute keys; an output claim for an undeclared key fails validation."
    - [ ] "Every `analysis_runs` row records provider, model, model version, prompt version, analyzer version, ontology version, tokens, cost, latency, confidence and structured result."
    - [ ] "Identical computation keys (input evidence digests plus analyzer, prompt, model and ontology versions) reuse the existing run; changing any one creates a new run (D54)."
    - [ ] Key scenarios: "LLM output invents ontology key | Validation failure, no published unsupported claim"; "Source HTML includes prompt injection | No tool execution or policy change".
    - [ ] "A test seeds known secret values and asserts they never appear in … analysis run records" (analysis-run part).
- [ ] **Verify:** `grep -rniE "openai|anthropic" backend/src/property_focus/analyzers` returns nothing (analyzers never name a vendor).

---

### Task 3.10: Analysis Workflow and Baseline Tier

Run the analyze workflow locally in one on-demand process, give every surviving property a `BASELINE` run, and serve intelligence with tier and component state.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 3.6, 3.9
**Decisions:** D1, D53, D21, D26, D3, D54

**Files:**

```
backend/src/property_focus/runtime/local_workflow.py             (create)
backend/src/property_focus/application/intelligence.py           (modify)
backend/src/property_focus/application/evaluation.py             (modify)
backend/src/property_focus/application/workflows.py              (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/src/property_focus/adapters/mcp/server.py                (modify)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/integration/test_workflows.py                      (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/contract/test_mcp.py                               (modify)
```

**Sub-tasks:**

- [ ] **3.10.1** — Implement `local_workflow.py`: the workflow contract states in order (load and authorize trusted run; acquire lease and reserve budget-tier funds; resolve cached evidence; run selected independent components with a catch per branch; persist runs, claims or classified failure; assemble `COMPLETE` or `PARTIAL` profile; recompute dependent evaluations and triggers; commit result and outbox event; reconcile budget and release lease), parameterized by analysis tier and budget tier, with per-component results in Postgres.
- [ ] **3.10.2** — Launch a `BASELINE` run (Text, Photo, Condition, Kitchen) for every property that survives the pre-filter, including a property that newly survives after a subscription change; launch none for a property with no surviving decision.
- [ ] **3.10.3** — Partial results: a failed optional component leaves a `PARTIAL` profile with successful facts, component status, last success time and retry information; `DATA_UNAVAILABLE` resolves to `UNKNOWN`; components retry independently.
- [ ] **3.10.4** — Cancellation: when cancellation wins the commit race, publish no new profile and keep committed evidence and usage.
- [ ] **3.10.5** — Add `GET /v1/properties/{id}/intelligence` (`PropertyIntelligenceProfile` with ontology version, tiers with states `NOT_TRIGGERED`/`PENDING`/`PARTIAL`/`COMPLETE` and triggers, components) and MCP `get_property_intelligence` with a parity case; extend `GET /v1/properties/{id}/history` with analysis runs, triggers and evaluation changes.
- [ ] **3.10.6** — Tests: a property rejected by every subscription gets zero `budget_reservations` and zero provider calls; a newly surviving property gets exactly one `BASELINE` run; a property appears in `GET /properties` with listing facts and requirements before any analyzer completes; one failed optional enrichment gives `PARTIAL`; cancellation race; no other tier state token exists in schemas.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-INT` passes the cases in 3.10.6 and the parity case.
- [ ] **Requirements:**
    - [ ] "Phase 3: every property that survives the phase 3 pre-filter receives a `BASELINE` run (TextAnalyzer, PhotoAnalyzer, ConditionAnalyzer, KitchenAnalyzer); a property with no surviving pre-filter decision receives none."
    - [ ] "`test_workflows.py` asserts zero `budget_reservations` and zero provider calls for a property rejected by every subscription."
    - [ ] "A property appears in `GET /properties` with listing facts and requirements evaluated before any analyzer completes. Its intelligence shows one state per analysis tier: `NOT_TRIGGERED` …, `PENDING` …, `PARTIAL` … or `COMPLETE` …. No other tier state token exists in schemas or code."
    - [ ] "Cancelled workflow runs publish no new profile after cancellation wins the commit race; preserve already committed evidence and usage."
    - [ ] "Every `analysis_runs` and `claims` row records the ontology version; `GET /properties/{id}/intelligence` returns it."
    - [ ] Key scenarios: "One failed optional enrichment | `PARTIAL` profile, successful facts and component retry"; "Subscription created while a property was earlier rejected by every subscription, and the property passes the new subscription | Pre-filter re-run makes it survive; one `BASELINE` run follows"; "Property fails every subscription's known hard requirements | No `BASELINE` paid call".
    - [ ] "Dogfood runs `runtime/local_workflow.py` in one on-demand process, invoking the same task handlers with the same input envelope in the same state order."
    - [ ] MCP tool list: "`get_property_intelligence` (phase 3)".
- [ ] **Verify:** Run `cli seed --dataset synthetic` with the fake provider; `GET /v1/properties/{id}/intelligence` for a seeded survivor shows `BASELINE` `COMPLETE` and `DEEP` `NOT_TRIGGERED`.

---

### Task 3.11: Deep Tier, Manual Triggers and Reanalysis

Add the deep analyzers, trigger records, manual deep analysis and operator-gated reanalysis, and close the `MANUAL` invalidation path.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 3.10
**Decisions:** D1, D6, D13, D14, D56, D7

**Files:**

```
backend/src/property_focus/analyzers/floorplan.py                (create)
backend/src/property_focus/analyzers/layout.py                   (create)
backend/src/property_focus/analyzers/prompts/floorplan-v1.md     (create)
backend/src/property_focus/analyzers/prompts/layout-v1.md        (create)
backend/src/property_focus/domain/triggers.py                    (create)
backend/src/property_focus/application/intelligence.py           (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/src/property_focus/adapters/mcp/server.py                (modify)
backend/migrations/versions/0012_analysis_triggers.py            (create)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_triggers.py                              (create)
backend/tests/unit/test_invalidation.py                          (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/contract/test_mcp.py                               (modify)
```

**Sub-tasks:**

- [ ] **3.11.1** — Implement `FloorplanAnalyzer` and `LayoutAnalyzer` (tier `DEEP`, `FLOORPLAN_REGION` locators) with versioned prompts.
- [ ] **3.11.2** — Migration `0012`: `analysis_triggers` (property, analysis tier, budget tier, type `AUTO`/`MANUAL`/`EXPERIMENT`, strategy name and version, reasons, requester or subscription, workflow run, created_at). Implement the trigger record model in `domain/triggers.py` (strategy `deep_trigger_v1` lands in Task 4.7).
- [ ] **3.11.3** — Add `POST /v1/properties/{id}/deep-analysis` (`{reason?}`, `Idempotency-Key`, `202 {trigger_id, workflow_run_id, status_url}`): `MANUAL` trigger, analysis tier `DEEP`, `DEEP` budget tier checked against the requesting member's account; the property stays usable throughout.
- [ ] **3.11.4** — Add `POST /v1/properties/{id}/reanalyze` (`{components?, analysis_tier, force:false}`, `Idempotency-Key`, `202`): resolves `MANUAL` invalidations; `force` is operator-only and budget-checked.
- [ ] **3.11.5** — Run the deep tier without `ASSESSOR`, `PERMITS`, `ZONING`, `TAX_HISTORY`: those components are `DEFERRED` and their attributes `UNKNOWN` (CP-3.B). Deep results are property intelligence served to every authorized account.
- [ ] **3.11.6** — Add MCP tools `request_deep_analysis` and `request_reanalysis` (writes, idempotency keys, membership and budget checks) with parity cases.
- [ ] **3.11.7** — Tests: `MANUAL` trigger record fields; deep run recorded with analysis tier `DEEP` and budget tier `DEEP`; `force` rejected for a non-operator; `MANUAL` invalidation resolved by reanalyze; `EVENT` recompute touches dependent components only.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` (`test_triggers.py` `MANUAL` records, `test_invalidation.py` `MANUAL` and `EVENT`) and `BE-INT` (contract and parity) pass.
- [ ] **Requirements:**
    - [ ] "`POST /properties/{id}/deep-analysis` creates a `MANUAL` trigger and a `DEEP` run (FloorplanAnalyzer, LayoutAnalyzer, plus later deep components)."
    - [ ] "Each trigger stores type, strategy name and version, and reasons."
    - [ ] "`MANUAL` waits for `POST /properties/{id}/reanalyze` (`test_invalidation.py`)."
    - [ ] "Deep research results are property intelligence, so they serve every authorized tenant."
    - [ ] Risks: "Deep tier runs without those capabilities and marks them `UNKNOWN` until a permitted provider exists."
    - [ ] Key scenario: "Member requests deep analysis | `MANUAL` trigger, `DEEP` run, property remains usable throughout".
    - [ ] MCP tool list: "`request_deep_analysis`, `request_reanalysis` (phase 3)".
- [ ] **Verify:** Call `POST /v1/properties/{id}/deep-analysis` twice with the same `Idempotency-Key`; both return the same `trigger_id`.

---

### Task 3.12: Human Corrections and Value-of-Information Queue

Turn human corrections into `HUMAN` claims and ask people to verify only attributes whose answer could change a decision.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 3.11
**Decisions:** D36, D37, D12

**Files:**

```
backend/src/property_focus/domain/verification.py                (create)
backend/src/property_focus/application/intelligence.py           (modify)
backend/src/property_focus/config.py                             (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/migrations/versions/0013_verification_requests.py        (create)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_verification.py                          (create)
backend/tests/unit/test_claims.py                                (modify)
backend/tests/contract/test_api.py                               (modify)
```

**Sub-tasks:**

- [ ] **3.12.1** — Migration `0013`: `verification_requests` (target attribute or claim, VOI score and inputs, state, asked_at, answered claim ID; scoped to the asking account when tied to a preference).
- [ ] **3.12.2** — Add `POST /v1/claims/{id}/verifications` (`{outcome, corrected_value?, evidence_ids?, reason}`, `201`): create a `HUMAN` claim and a verification record; the original analyzer claim and run remain.
- [ ] **3.12.3** — Implement `domain/verification.py`: `voi = uncertainty * importance * decision_impact` with uncertainty `1 - confidence` (1 for `UNKNOWN` and `CONFLICTING`), importance the max normalized preference weight referencing the attribute across the current versions of searches the asking user belongs to (1 for hard requirements), and decision impact the absolute change in overall score or requirements status between the attribute's best and worst allowed values.
- [ ] **3.12.4** — Add `GET /v1/verifications`: ordered by VOI descending, omits requests below `VOI_ASK_THRESHOLD` (config), never asks about an attribute no preference references, restricted to allowed scope.
- [ ] **3.12.5** — Expose `HUMAN` claims as ground truth for the attributes they cover to the evaluation harness (Task 3.13).
- [ ] **3.12.6** — Tests: VOI formula cases; threshold filter; unreferenced attribute excluded; kitchen-quality correction wins and keeps the analyzer claim; parcel-ID correction without evidence loses to the assessor.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` (`test_verification.py`, `test_claims.py`) and `BE-INT` (contract) pass.
- [ ] **Requirements:**
    - [ ] "`domain/verification.py` computes `voi = uncertainty * importance * decision_impact`, where uncertainty is `1 - confidence` (1 for `UNKNOWN` and `CONFLICTING`), importance is the max normalized preference weight … (1 for hard requirements), and decision impact is the absolute change in overall score or requirements status between the attribute's best and worst allowed values."
    - [ ] "`GET /verifications` orders by VOI descending and omits requests below `VOI_ASK_THRESHOLD`; an uncertain attribute that no preference references is never asked (`backend/tests/unit/test_verification.py`)."
    - [ ] "Human corrections become claims with high authority for subjective or visible attributes, and the original AI output stays (D36)."
    - [ ] Key scenarios: "Human correction of kitchen quality versus analyzer claim | `HUMAN` claim wins; analyzer run and claim remain"; "Human claim contradicts assessor parcel ID without evidence | Assessor value stays resolved".
    - [ ] "Human corrections (`HUMAN` claims) feed the ground-truth set for the attributes they cover."
- [ ] **Verify:** `GET /v1/verifications` on seeded data returns items in non-increasing VOI order.

---

### Task 3.13: Evaluation Harness and Shadow Runs

Compare versioned analyzers, resolvers, triggers and scoring strategies on quality, cost and latency; run shadow candidates without publication; create `EXPERIMENT` triggers.

**Modules:** `backend`, `fixtures`, `specs`
**Depends on:** 3.12
**Decisions:** D45, D46, D6, D56, D3

**Files:**

```
backend/src/property_focus/application/harness.py                (create)
backend/src/property_focus/cli.py                                (modify)
backend/evals/config/baseline.yaml                               (create)
backend/evals/reports/.gitkeep                                   (create)
specs/property-focus/evaluation-policy.md                        (create)
.github/workflows/evaluate.yml                                   (create)
backend/tests/unit/test_harness.py                               (create)
backend/tests/replay/test_golden_replay.py                       (modify)
```

**Sub-tasks:**

- [ ] **3.13.1** — Implement `harness.py`: accept any registered versioned analyzer, resolver, comp selector, valuation model, trigger strategy or scoring strategy; run on a frozen evidence set with frozen historical as-of times.
- [ ] **3.13.2** — Shadow mode: candidate outputs are `SHADOW` runs; they never publish profiles, change evaluations or send notifications.
- [ ] **3.13.3** — `EXPERIMENT` triggers: create them for properties the pre-filter rejected; runs record analysis tier (`BASELINE` or `DEEP`, by analyzer set) and budget tier `EXPERIMENT`, reserve against the `EXPERIMENT` budget, and notify no one.
- [ ] **3.13.4** — Reports in `backend/evals/reports/`: identity precision, field-level correctness and abstention, hard-filter false passes (target zero in the curated gate), evaluation changes, evidence support, trigger rate, and cost and latency per property for every compared version. Synthetic cases are excluded from golden-gate metrics; with no real cases the golden gate reports "not met", never "passed".
- [ ] **3.13.5** — Implement `cli evaluate --config ... --offline --fail-on-regression` and `--mode shadow --no-publish`; write `baseline.yaml` (dataset hashes, metrics, thresholds) and `evaluation-policy.md` (dataset splits, metrics, release-threshold procedure; thresholds set before seeing candidate results).
- [ ] **3.13.6** — Promotion: a maintainer promotes a version only with a report, through a reviewed configuration change; a regression blocks automatic promotion. Add `evaluate.yml` (manual and scheduled replay, golden and shadow evaluation).
- [ ] **3.13.7** — Tests: shadow run publishes nothing (`test_harness.py`); `EXPERIMENT` trigger fields and budget tier; synthetic-only dataset reports the golden gate as not met (`test_golden_replay.py`); a shadow candidate that scores better produces a report only.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0; `EVAL` exits 0 on the synthetic dataset.
- [ ] **Tests:** `BE-UNIT` and `BE-REPLAY` pass the cases in 3.13.7.
- [ ] **Requirements:**
    - [ ] "The harness accepts any versioned analyzer, resolver, comp selector, valuation model, trigger strategy or scoring strategy; shadow runs cannot publish profiles, change evaluations or send user notifications. Promotion requires a report and maintainer action; regression blocks automatic promotion. Shadow runs and promotion work on dogfood from phase 3 as part of V1 (D45)."
    - [ ] "The evaluation harness can create `EXPERIMENT` triggers for properties rejected by the pre-filter; these runs record their analysis tier (`BASELINE` or `DEEP`, by analyzer set) and budget tier `EXPERIMENT`, reserve against the `EXPERIMENT` budget, and do not notify users."
    - [ ] "Synthetic cases live only in `fixtures/synthetic/`; the harness excludes them from every golden-gate metric and reports a golden gate as not met, never passed, when it has no real cases (`test_golden_replay.py`)."
    - [ ] "Report identity precision, field-level correctness/abstention, hard-filter false passes, evaluation changes, evidence support, trigger rate, cost and latency; target zero false passes in the curated hard-filter gate."
    - [ ] "Evaluation reports include cost and latency per property next to quality metrics for every compared version."
    - [ ] "Freeze historical as-of times to prevent future-data leakage."
    - [ ] Key scenario: "Shadow candidate scores better | Report only; no publication without promotion".
- [ ] **Verify:** After `cli evaluate --mode shadow --no-publish`, the counts of `intelligence_profiles`, `property_evaluations` and `notifications`-bound outbox events are unchanged.

---

### Task 3.14: Intelligence Ready Gate

Collect the Intelligence ready gate artifacts for phases 2–3.

**Modules:** `backend`, `specs`
**Depends on:** 3.1–3.13, CP-3.A (live checks only)
**Decisions:** D3, D7, D31, A2, D45, D46, A1

**Files:**

```
specs/property-focus/gates/intelligence-ready.md                 (modify)
```

**Sub-tasks:**

- [ ] **3.14.1** — Record the pre-filter report with zero paid calls on rejected properties and the ontology coverage report (75+ attributes, every category).
- [ ] **3.14.2** — Record subscription versioning tests and non-member `404` tests on every search route and tool.
- [ ] **3.14.3** — Record the provider contract report for both adapters (`test_ai.py`) and the profile evidence audit (every inferred claim has evidence).
- [ ] **3.14.4** — Record the golden-field report over real reviewed cases (synthetic excluded; "not met" while no real cases exist), the shadow isolation test and phase 2–3 MCP parity.
- [ ] **3.14.5** — Re-run the zero-model-call ingestion assertion now that real provider adapters exist.

**Validation:**

- [ ] **Build:** `VERIFY` and `EVAL` exit 0.
- [ ] **Tests:** All phase 0–3 suites pass.
- [ ] **Requirements:**
    - [ ] Phase 3 exit: "No paid analyzer call on a pre-filter reject, golden-property evaluation and evidence-linked profile, both provider adapters passing the provider contract, non-member `404` on search routes."
    - [ ] Intelligence ready artifacts: "Source session drill, ontology coverage report (75+ attributes), pre-filter report with zero paid calls on rejected properties, subscription versioning tests, non-member `404` tests on search routes and tools, provider contract report for both adapters (`test_ai.py`), profile evidence audit, golden-field report over real reviewed cases (synthetic excluded), shadow isolation test, phase 2–3 MCP parity."
- [ ] **Verify:** `intelligence-ready.md` links every artifact. **Live (explicit user approval required, after CP-3.A):** `PF_LIVE=1 make test-live`.

---

## Phase 4: Preferences and Evaluation

**Goal:** Per-account PreferenceProfile versions, copying a profile into a search, preference definitions, the adaptive interview, preference evolution for profiles and subscriptions, multidimensional evaluation and automatic deep-research triggers. **Deliverable:** the Evaluation ready gate.

> **Checkpoint CP-4.A (blocking for Task 4.5, sub-task 4.5.4) — interview-proposed budget or geography for a user with no matching search.** Owner: user, before phase 4. The plan recommends a `SUBSCRIPTION` proposal with no target whose approval creates the subscription with the caller as owner (`201`). Do not implement the no-target path until the user confirms or changes this recommendation. The rest of Task 4.5 proceeds.
>
> **Checkpoint CP-4.B (blocking for Task 4.8) — initial ontology attributes and preference weights.** Owner: user, in phase 4. The seeded 75+ attributes and the preference definitions and default weights are proposals. The user reviews them before the Evaluation ready gate closes; each dogfood user's review of their first preference version is a Dogfood ready artifact (Task 5.12).

### Task 4.1: Global Preference Definitions

Seed global preference definitions that reference ontology v1 attributes and a code registry of utility functions.

**Modules:** `backend`, `ontology`
**Depends on:** 3.14
**Decisions:** D34, D33

**Files:**

```
ontology/v1.0.0/preference-definitions.json                      (create)
ontology/schema/preference-definition.schema.json                (create)
ontology/CHANGELOG.md                                            (modify)
backend/src/property_focus/domain/preferences.py                 (create)
backend/src/property_focus/domain/ontology.py                    (modify)
backend/src/property_focus/cli.py                                (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/migrations/versions/0014_preference_definitions.py       (create)
backend/tests/unit/test_preferences.py                           (create)
backend/tests/unit/test_ontology.py                              (modify)
```

**Sub-tasks:**

- [ ] **4.1.1** — Migration `0014`: `preference_definitions` (ontology version FK, key, category `property`/`household_lifestyle`, lifestyle goals, satisfying attribute keys as FKs to same-version attributes, utility function name, scalar parameters; unique version/key).
- [ ] **4.1.2** — Add a utility-function code registry in `domain/preferences.py`; a definition naming an unregistered function or a non-scalar parameter fails load.
- [ ] **4.1.3** — Write `preference-definitions.json` and its schema (for example "quiet street" satisfied by `location.traffic_level` and `location.cul_de_sac`), each with a category.
- [ ] **4.1.4** — Extend `cli ontology load` to validate and insert preference definitions with their version; for a database where v1.0.0 is already `ACTIVE` and has no definitions, insert them once without touching attribute rows.
- [ ] **4.1.5** — Tests: unknown attribute key fails; unregistered utility function fails; category required; definitions load idempotently.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0; `cli ontology load ontology/v1.0.0` exits 0 on a fresh and on a phase 3 database.
- [ ] **Tests:** `BE-UNIT` passes the cases in 4.1.5.
- [ ] **Requirements:**
    - [ ] D34 model: "Global **preference definitions** describe how people may care about ontology attributes, for example 'quiet street' satisfied by low traffic level and cul-de-sac status."
    - [ ] `preference_definitions`: "category (`property`, `household_lifestyle`; plan recommendation, used by the profile-copy rule for `rental_hold` searches) … satisfying attribute keys (FK to same-version attributes), utility function name from code registry, scalar parameters; unique version/key (D34)".
- [ ] **Verify:** `select count(*) from preference_definitions where ontology_version = '1.0.0'` equals the number of entries in the seed file.

---

### Task 4.2: PreferenceProfile Versions and Phase 4 MCP Tools

Give each account one private, versioned profile with material-change confirmation, and expose it through REST and MCP.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 4.1
**Decisions:** D16, D34, A1, D5 (surviving parts), D29, D30

**Files:**

```
backend/src/property_focus/domain/preferences.py                 (modify)
backend/src/property_focus/application/preferences.py            (create)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/src/property_focus/adapters/mcp/server.py                (modify)
backend/migrations/versions/0015_preference_profiles.py          (create)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_preferences.py                           (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/contract/test_mcp.py                               (modify)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **4.2.1** — Migration `0015`: `preference_profiles` (tenant; partial unique index allows one `ACTIVE` per `tenant_id`; private forced RLS) and `preference_versions` (append-only; ontology version, goals, preferences with definition key, importance `critical`/`high`/`medium`/`low`, hard flag, acceptable alternatives, tradeoffs, examples, dimension weights, unknown policy, origin `EXPLICIT`/`LEARNED`/`INTERVIEW`/`REVERT`, evidence refs, digest). Geography and budget fields do not exist on profile versions.
- [ ] **4.2.2** — Implement the profile materiality classifier: `MATERIAL` when a hard requirement is added, removed or edited; importance changes to or from `critical`; a tradeoff involving a hard or `critical` preference changes; any dimension weight changes; a soft weight moves by more than 0.05 normalized share; or cumulative learned drift exceeds 0.10 since the last explicit approval. Everything else `LOW_IMPACT`.
- [ ] **4.2.3** — Create the account's profile with an empty `EXPLICIT` first version on first access; add `GET /v1/preferences` (active profile, current version, ETag).
- [ ] **4.2.4** — Add `PATCH /v1/preferences/{id}` (`If-Match`; `200` new version; `412` stale; `409 material_change_requires_confirmation` unless `confirm_material: true`; `422` for an unknown definition), `GET /v1/preferences/{id}/versions` and `POST /v1/preferences/{id}/revert` (new version with the target's content digest).
- [ ] **4.2.5** — Editing a profile creates no search version and changes no evaluation.
- [ ] **4.2.6** — Add MCP tools `get_preferences` and `update_preferences` (write; requires `confirm_material` for material changes) with parity cases.
- [ ] **4.2.7** — Tests: table-driven materiality rules; second `ACTIVE` profile for one account fails the partial unique index; revert digest; two accounts sharing a search each keep their own profile; account B's profile and versions return `404` to every other account, including members of B's searches.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 4.2.7.
- [ ] **Requirements:**
    - [ ] "`PATCH /preferences/{id}` with `If-Match` creates a new immutable `preference_versions` row and returns `200` with it; prior versions remain readable through `GET /preferences/{id}/versions`; a stale ETag returns `412`."
    - [ ] "`domain/preferences.py` classifies a profile diff as `MATERIAL` when … (thresholds confirmed by the user on 2026-11-02). Everything else is `LOW_IMPACT`. Geography and budget are subscription fields and use the subscription classifier. Table-driven tests cover each rule."
    - [ ] "A member's `PATCH` with a `MATERIAL` diff returns `409` with code `material_change_requires_confirmation` and the material items unless the body sets `confirm_material: true`."
    - [ ] "`POST /preferences/{id}/revert` with `{version}` creates a new version whose content digest equals the target version's."
    - [ ] "Each preference references a definition in `preference_definitions` for the profile's ontology version; a reference to an unknown definition returns `422`."
    - [ ] "An account has exactly one `ACTIVE` preference profile; creating a second for the same account fails the partial unique index on `tenant_id` (`test_preferences.py`). Two accounts sharing a search each keep their own active profile."
    - [ ] "Editing a private PreferenceProfile creates no search version and changes no evaluation until a member applies the profile to a search."
    - [ ] "Account B's profile, profile versions … return `404` to every other account, including members of B's searches." (profile part)
    - [ ] MCP tool list: "`get_preferences` and `update_preferences` (phase 4)"; "require `confirm_material` for material preference changes".
    - [ ] Key scenario: "Revert to an earlier preference version | New version with identical content digest".
- [ ] **Verify:** Two concurrent `PATCH` calls with the same `If-Match` produce one `200` and one `412`.

---

### Task 4.3: Copy a Profile into a Search

Copy a member's active profile into a new search version under the strategy copy rule, with material-change rules and pre-filter re-run.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 4.2
**Decisions:** A1, D16, D17, D3

**Files:**

```
backend/src/property_focus/domain/subscriptions.py               (modify)
backend/src/property_focus/domain/funnel.py                      (modify)
backend/src/property_focus/application/subscriptions.py          (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_subscriptions.py                         (modify)
backend/tests/unit/test_funnel.py                                (modify)
backend/tests/unit/test_evaluation.py                            (modify)
backend/tests/contract/test_api.py                               (modify)
```

**Sub-tasks:**

- [ ] **4.3.1** — Implement the copy rule in `domain/subscriptions.py`: into `rental_hold`, copy only hard requirements whose definition category is `property`; into `owner_occupancy` and `live_in_and_rent`, copy every profile hard requirement.
- [ ] **4.3.2** — Accept `apply_profile: true` on `POST` and `PATCH /v1/search-subscriptions`: copy the caller's active profile version (hard requirements under the copy rule, soft preferences, dimension weights, and the profile destinations those preferences reference), replace the previously copied section, record the source profile version and applying user, origin `PROFILE_COPY`.
- [ ] **4.3.3** — On `PATCH`, a copy that changes a hard requirement or a dimension weight is `MATERIAL`; the `409` lists every profile destination that would become visible to members (destinations exist from Task 6.3).
- [ ] **4.3.4** — Include copied hard requirements in the effective hard-requirement set; applying a profile writes `BATCH` invalidations and re-runs the pre-filter.
- [ ] **4.3.5** — Tests: the commute (`household_lifestyle`) and bedrooms (`property`) scenario on a `rental_hold` and an `owner_occupancy` search; editing the profile after a copy leaves the effective set and evaluations unchanged; copy origin and provenance recorded.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 4.3.5.
- [ ] **Requirements:**
    - [ ] "From phase 4, `POST /search-subscriptions` and `PATCH /search-subscriptions/{id}` accept `apply_profile: true`. The server copies the caller's active profile version into the new version: hard requirements under the copy rule, soft preferences, dimension weights, and the profile destinations those preferences reference. The copy replaces the previously copied section, records the source profile version and the applying user, and has origin `PROFILE_COPY`. On `PATCH`, a copy that changes a hard requirement or a dimension weight is `MATERIAL`, and the `409` lists every profile destination that would become visible to members (plan recommendation)."
    - [ ] "Copying a profile into a `rental_hold` search copies only hard requirements whose preference definition has category `property`; copying into an `owner_occupancy` or `live_in_and_rent` search copies every profile hard requirement (plan recommendation)."
    - [ ] "Effective hard-requirement set (plan recommendation): the subscription version's hard criteria, including any hard requirements a member copied in from a profile. No private profile joins the set live. … `test_funnel.py` and `test_evaluation.py` cover each strategy, and assert that editing a profile after a copy leaves the set unchanged."
    - [ ] Key scenarios: "A member applies a profile with hard requirements 'commute at most 30 minutes' (`household_lifestyle`) and 'at least 2 bedrooms' (`property`) to a rental hold search …"; "Member edits their private profile after applying it to a shared search | No new search version; evaluations unchanged until a member applies the profile again".
- [ ] **Verify:** After a profile `PATCH`, `GET /v1/search-subscriptions/{id}/versions` shows no new version.

---

### Task 4.4: Preference Proposals and Learned Evolution

Turn feedback into proposals for profile or subscription targets, auto-apply low-impact changes, and gate material ones behind owner-only or any-member approval.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 4.3
**Decisions:** D16, A1, D15

**Files:**

```
backend/src/property_focus/domain/preferences.py                 (modify)
backend/src/property_focus/application/preferences.py            (modify)
backend/src/property_focus/application/subscriptions.py          (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/migrations/versions/0016_proposals_feedback.py           (create)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_preferences.py                           (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **4.4.1** — Migration `0016`: `preference_proposals` (the plan's columns; check constraint that the base version matches the target kind; target ID null only for a `SUBSCRIPTION` proposal that creates a subscription; RLS: `PROFILE` visible to the profile owner, `SUBSCRIPTION` to the search's members) and `feedback` (the plan's columns; RLS shows `visit_question` rows to members and `note`, `like`, `dislike` rows to their author; `visit_question` requires non-empty text). The feedback REST route lands in Task 5.1.
- [ ] **4.4.2** — Implement learning from `like` and `dislike` feedback: create a proposal per target, classified by that target's classifier; `LOW_IMPACT` applies automatically as a `LEARNED` version with evidence feedback IDs; `MATERIAL` waits as `AWAITING_APPROVAL`; the target's current version stays unchanged until approval.
- [ ] **4.4.3** — Add `GET /v1/preferences/{id}/proposals`, `POST /v1/preferences/{id}/proposals/{proposal_id}/approve` and `/reject` (profile owner only; any other account gets `404`).
- [ ] **4.4.4** — Add `GET /v1/search-subscriptions/{id}/proposals`, `POST /v1/search-subscriptions/{id}/proposals/{proposal_id}/approve` and `/reject` (any active member; `decided_by` and an audit event record member and reason; a second decision returns `409`). Members see diff, materiality and the count of supporting feedback items, never the content of another member's `note`, `like` or `dislike`.
- [ ] **4.4.5** — Restoring an earlier version moves an `AUTO_APPLIED` or `APPLIED` proposal to `REVERTED`.
- [ ] **4.4.6** — Tests: learned weight change of 0.03 auto-applies with evidence; learned change to a profile hard requirement waits; learned budget or geography change makes a `SUBSCRIPTION` proposal and leaves both the subscription version and the profile unchanged; cumulative drift above 0.10 becomes material; member B approves member A's material subscription proposal (`200`, `decided_by` B; second decision `409`); a non-owner deciding a `PROFILE` proposal gets `404`; feedback content privacy in proposal responses.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 4.4.6.
- [ ] **Requirements:**
    - [ ] "Learned updates from feedback create `preference_proposals` whose target kind is `PROFILE` or `SUBSCRIPTION`, classified by that target's classifier: `LOW_IMPACT` proposals apply automatically as a version of the target with origin `LEARNED` and their evidence feedback IDs; `MATERIAL` proposals wait as `AWAITING_APPROVAL` until … approve or reject. A learned budget change leaves the subscription's current version unchanged until approval (`test_preferences.py`)."
    - [ ] "Only the profile's owner may approve or reject a `PROFILE` proposal; any other account gets `404`. Any active member of the search may approve or reject a `SUBSCRIPTION` proposal; `decided_by` and an audit event record that member and the reason, and a second decision on the same proposal returns `409` (plan recommendation). Members see a `SUBSCRIPTION` proposal's diff, materiality and the count of supporting feedback items; the content of `note`, `like` and `dislike` evidence stays visible only to its author."
    - [ ] "From phase 4, learned and interview changes to a subscription arrive as `preference_proposals` with target kind `SUBSCRIPTION` and follow the proposal rules under the PreferenceProfile requirement."
    - [ ] "Account B's … `PROFILE` proposals … return `404` to every other account, including members of B's searches."
    - [ ] Key scenarios: "Learned weight change of 0.03 share"; "Learned change to a profile hard requirement"; "Learned or interview change to a subscription's budget or geography"; "Member B approves a material `SUBSCRIPTION` proposal that came from member A's feedback".
- [ ] **Verify:** Query `audit_events` after an approval test; it holds actor, reason and the proposal ID.

---

### Task 4.5: Adaptive Preference Interview

Build the caller's private profile through an explicit-state interview that sends the model only the state and the last six turns.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 4.4
**Decisions:** D15, D16, A1

**Files:**

```
backend/src/property_focus/domain/interview.py                   (create)
backend/src/property_focus/application/preferences.py            (modify)
backend/src/property_focus/analyzers/prompts/interview-v1.md     (create)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/migrations/versions/0017_interview_sessions.py           (create)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_preferences.py                           (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **4.5.1** — Migration `0017`: `interview_sessions` (tenant, profile, state JSONB with captured goals, filled and open slots, pending examples and step; last 6 turns; status; private RLS).
- [ ] **4.5.2** — Implement `domain/interview.py`: a state machine where structured questions fill the required slots (geography, budget, bedrooms, property type, deal-breakers) before free conversation.
- [ ] **4.5.3** — Add `POST /v1/preferences/{id}/interview-sessions` (optional `search_subscription_id` of a search the caller belongs to, else `404`; `201` with state and first questions) and `POST /v1/interview-sessions/{id}/answers` (`200` with state, next questions and proposals). A session edits only the caller's own profile. Model calls go through the router and budget ledger.
- [ ] **4.5.4** — Geography and budget answers become `SUBSCRIPTION` proposals against the given search; without a search, a `SUBSCRIPTION` proposal with no target whose approval creates the subscription with the caller as owner (`201`) — only after CP-4.A. No geography or budget value enters `preference_versions`.
- [ ] **4.5.5** — Interview proposals link each lifestyle goal to preferences, satisfying ontology attributes, importance and tradeoffs, and example properties, and apply only through the approval path.
- [ ] **4.5.6** — Tests: required slots first; payload contains the state object and at most 6 turns, and turn 20's payload is no larger than turn 7's plus the state delta; geography answer creates a `SUBSCRIPTION` proposal and no profile field; a session for a search the caller does not belong to gets `404`; another account's session returns `404`.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 4.5.6.
- [ ] **Requirements:**
    - [ ] "`POST /preferences/{id}/interview-sessions` starts a session; `POST /interview-sessions/{id}/answers` returns the next questions and any proposals. Structured questions fill the required slots (geography, budget, bedrooms, property type, deal-breakers) before free conversation. A session edits only the caller's own profile. It takes an optional `search_subscription_id` of a search the caller belongs to (`404` otherwise): geography and budget answers become `SUBSCRIPTION` proposals against it, or, without one, a `SUBSCRIPTION` proposal with no target whose approval creates the subscription with the caller as owner (`201`); no geography or budget value ever enters a `preference_versions` row (`test_preferences.py`)."
    - [ ] "The model request contains the session state object and at most the last 6 turns; `test_preferences.py` asserts that turn 20's request payload is no larger than turn 7's plus the size of the state delta."
    - [ ] "Proposals from the interview link each lifestyle goal to preferences, satisfying ontology attributes, importance and tradeoffs, and example properties; they apply only through the proposal approval path."
    - [ ] "Account B's … interview sessions … return `404` to every other account."
    - [ ] "Do not expose … the interview in the initial MCP surface."
    - [ ] Key scenario: "Interview turn 20 | Model request carries state plus last 6 turns".
- [ ] **Verify:** `grep -n "interview" backend/src/property_focus/adapters/mcp/server.py` returns nothing.

---

### Task 4.6: Multidimensional Evaluation (`scoring_v1`) and Explanations

Compute lifestyle fit, property quality, critical mismatches, coverage and overall fit deterministically; rank the feed by overall fit; add the explanation analyzer that cannot change numbers.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 4.3
**Decisions:** D9, D7, D53, A1

**Files:**

```
backend/src/property_focus/domain/evaluation.py                  (modify)
backend/src/property_focus/domain/preferences.py                 (modify)
backend/src/property_focus/application/evaluation.py             (modify)
backend/src/property_focus/analyzers/explanation.py              (create)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_evaluation.py                            (modify)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **4.6.1** — Implement dimension scoring for `lifestyle_fit` and `property_quality`: validate weights (nonnegative, positive total); `score_D = 100 * sum(weight * known_utility) / sum(all_weights)`; `coverage_D = sum(known_weights) / sum(all_weights)`; unknown, `CONFLICTING` and stale values follow the frozen unknown policy and never pass a hard requirement.
- [ ] **4.6.2** — Implement critical mismatches (every `critical` preference with known utility 0) and `overall = max(0, sum(dim_weight * score_D) / sum(dim_weight over applicable D) - CRITICAL_PENALTY * count(critical_mismatches))` with `scoring_v1` weights lifestyle 0.6, quality 0.4, `CRITICAL_PENALTY = 15`; subscription dimension weights replace defaults when set.
- [ ] **4.6.3** — `rental_hold` evaluations have `lifestyle_fit: null`; a null dimension leaves the denominator.
- [ ] **4.6.4** — Feed: order by requirements group, then overall descending under the subscription's scoring version, then `id`.
- [ ] **4.6.5** — Implement `analyzers/explanation.py`: receives only the computed evaluation and cited claims; stores text separately; changing it cannot change any number.
- [ ] **4.6.6** — Recompute evaluations when a search's criteria or copied preferences change without recapturing evidence or rerunning analyzers; a weights-only change creates a new evaluation version.
- [ ] **4.6.7** — Tests: identical inputs give identical requirements, scores, coverage, mismatches, overall and explanation-input digest; unknown contributes zero with lower coverage; critical penalty; `rental_hold` null lifestyle; a fake provider returning numbers in explanation text leaves scores unchanged; weights-only change triggers no analyzer run; two accounts reuse one rights-cleared profile while their evaluations stay within each search.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 4.6.7.
- [ ] **Requirements:**
    - [ ] "A fixed intelligence profile revision, subscription version and scoring version produce identical requirements status, dimension scores, coverage, critical mismatches, overall score and explanation-input digest."
    - [ ] "For each scored dimension D in {lifestyle_fit, property_quality, value}: validate weights as nonnegative with positive total; `score_D = …` and `coverage_D = …`. Unknown, `CONFLICTING` and stale values follow the frozen unknown policy and cannot silently pass hard requirements." (value dimension in Task 7.3)
    - [ ] "Critical mismatches list every `critical`-importance preference whose known utility is 0. `overall = …`; scoring version `scoring_v1` (phase 4) uses lifestyle 0.6 and property quality 0.4 with `CRITICAL_PENALTY = 15` … Weights come from the subscription version's dimension weights, set directly or copied from a profile, when set."
    - [ ] "`analyzers/explanation.py` receives only the computed evaluation and cited claims; its output text is stored separately and changing it cannot change any number. A test with a fake provider returning numbers in text asserts unchanged scores."
    - [ ] "A `rental_hold` evaluation has `lifestyle_fit: null` and uses the effective hard-requirement set defined under the pre-filter requirement."
    - [ ] "Two authorized accounts may reuse a rights-cleared property profile, while their preference versions … differ and remain private; evaluations differ per search and stay within each search's members."
    - [ ] "Changing a search's criteria or copied preferences recomputes its evaluations without recapturing unchanged source evidence or rerunning analyzers; changing an ontology or analyzer version creates a new profile revision."
    - [ ] Key scenarios: "Explanation model output contains a different score | Stored evaluation numbers unchanged"; "Preference version changes only weights | New evaluation version; no source recapture or analyzer rerun".
- [ ] **Verify:** `grep -rn "explanation" backend/src/property_focus/domain/evaluation.py` shows no read of explanation output into any score.

---

### Task 4.7: Automatic Deep-Research Triggers (`deep_trigger_v1`)

Fire `AUTO` triggers for top-quartile and anomaly properties and report the trigger rate.

**Modules:** `backend`
**Depends on:** 4.6
**Decisions:** D6, D1, D56, D45

**Files:**

```
backend/src/property_focus/domain/triggers.py                    (modify)
backend/src/property_focus/application/evaluation.py             (modify)
backend/src/property_focus/application/harness.py                (modify)
backend/tests/unit/test_triggers.py                              (modify)
backend/tests/replay/test_golden_replay.py                       (modify)
```

**Sub-tasks:**

- [ ] **4.7.1** — Implement `deep_trigger_v1`: an `AUTO` trigger when a property's overall score is at or above the 75th percentile of that subscription's baseline overall scores over the trailing 30 days and its requirements status is not `fail`.
- [ ] **4.7.2** — Implement anomaly rules: list price drop of 5% or more; a `CONFLICTING` hard-requirement attribute (the valuation p10 rule lands in Task 7.3).
- [ ] **4.7.3** — Record type, strategy name and version, and reasons on each trigger; start a `DEEP` run charged to the search owner's account under the `DEEP` budget tier.
- [ ] **4.7.4** — Add the trigger-rate metric to the harness report: share of baseline-evaluated properties with non-anomaly `AUTO` triggers on the golden replay corpus.
- [ ] **4.7.5** — Tests: quartile boundary; `fail` excluded; each anomaly rule; reasons recorded; trigger-rate computation on synthetic replay data.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` (`test_triggers.py` AUTO, quartile and anomaly rules) and `BE-REPLAY` pass.
- [ ] **Requirements:**
    - [ ] "Phase 4: `domain/triggers.py` strategy `deep_trigger_v1` writes an `AUTO` trigger when a property's overall score is at or above the 75th percentile of that subscription's baseline overall scores over the trailing 30 days and its requirements status is not `fail`, or when an anomaly rule fires (list price drop of 5% or more; `CONFLICTING` hard-requirement attribute; from phase 7, list price below valuation p10). Each trigger stores type, strategy name and version, and reasons."
    - [ ] "On the golden replay corpus, `AUTO` triggers fire for 20–30% of baseline-evaluated properties, excluding anomaly triggers; the evaluation report records the rate." (rate recorded here; gate check in Task 4.8)
    - [ ] Key scenario: "Property in the subscription's top quartile | `AUTO` trigger with strategy `deep_trigger_v1` and reasons".
- [ ] **Verify:** Run `EVAL`; the report in `backend/evals/reports/` contains a `trigger_rate` field.

---

### Task 4.8: Evaluation Ready Gate

Collect the Evaluation ready gate artifacts.

**Modules:** `backend`, `specs`
**Depends on:** 4.1–4.7, CP-4.B
**Decisions:** D9, D16, D6, A1

**Files:**

```
specs/property-focus/gates/evaluation-ready.md                   (create)
```

**Sub-tasks:**

- [ ] **4.8.1** — Record deterministic golden evaluations and unknown-handling results (golden metrics count real reviewed cases only; report "not met" while none exist).
- [ ] **4.8.2** — Record materiality table tests (profile and subscription), profile-copy tests per strategy, and owner-only `PROFILE` and any-member `SUBSCRIPTION` approval tests.
- [ ] **4.8.3** — Record the trigger-rate report (target 20–30% `AUTO`, anomalies excluded) and phase 4 MCP parity.
- [ ] **4.8.4** — Record the CP-4.B user review of ontology attributes and preference definitions.

**Validation:**

- [ ] **Build:** `VERIFY` and `EVAL` exit 0.
- [ ] **Tests:** All phase 0–4 suites pass.
- [ ] **Requirements:**
    - [ ] Phase 4 exit: "Deterministic golden evaluations, unknown handling and trigger-rate report."
    - [ ] Evaluation ready artifacts: "Deterministic golden evaluations, materiality table tests, profile-copy tests per strategy, owner-only `PROFILE` and any-member `SUBSCRIPTION` approval tests, trigger-rate report (20–30% AUTO), phase 4 MCP parity."
- [ ] **Verify:** `evaluation-ready.md` links every artifact and states the trigger rate.

---

## Phase 5: Homebuyer Dogfood

**Goal:** Homebuyer onboarding, feed and seven-section property page, manual add UI, search sharing, shortlist, visit questions, tours with post-tour questions, corrections, email and in-app notifications, operations UI, cost views and budget controls, per-account REST and MCP rate limits (Task 5.13, A5), and backups. **Deliverable:** the Dogfood ready gate on DigitalOcean.

> **Checkpoint CP-5.A (blocking for Task 5.12) — recovery targets on single-host dogfood.** Owner: operator, before phase 5 release. Question: can the dogfood setup meet the initial 24-hour RPO and 8-hour RTO, or does the operator accept different targets? The measured restore drill (Task 5.10) informs the answer.
>
> **Checkpoint CP-5.B (blocking for real buying decisions, not for building) — dogfood inputs.** Owner: user. Dogfood uses real buying decisions only after source authorization (CP-2.A), user-reviewed preference inputs, budget configuration (CP-3.A) and evidence visibility work. Open items to confirm: first mailbox provider and authorized TheMLS workflow; initial geography, hard requirements, budget limits and notification destinations; retention and reuse policy before immutable retention or shared caching of licensed material. Tasks 5.1–5.11 proceed with synthetic data.

### Task 5.1: Shortlist, Feedback, Visit Questions and Tours API

Let search members shortlist homes, record feedback and visit questions, and record tours with post-tour verification requests; add the `record_feedback` MCP tool.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 4.8
**Decisions:** A1, D37, D30, D29, D17

**Files:**

```
backend/src/property_focus/application/subscriptions.py          (modify)
backend/src/property_focus/application/intelligence.py           (modify)
backend/src/property_focus/application/preferences.py            (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/src/property_focus/adapters/mcp/server.py                (modify)
backend/migrations/versions/0018_shortlist_tours.py              (create)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/contract/test_mcp.py                               (modify)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **5.1.1** — Migration `0018`: `shortlist_entries` (unique subscription/property; saved_by; search-scoped RLS) and `tours` (subscription, property, visited_at, recorded_by; search-scoped RLS).
- [ ] **5.1.2** — Add `PUT /v1/search-subscriptions/{id}/shortlist/{property_id}` (`201` first save, `200` with the same entry on repeat), `DELETE` on the same path (`204`, also when absent) and `GET /v1/search-subscriptions/{id}/shortlist` (this subscription only, each entry with `saved_by`).
- [ ] **5.1.3** — Add `POST /v1/properties/{id}/feedback` (`{search_subscription_id, target_revision, kind, text?}`, `201`; `visit_question` needs non-empty text, else `422`; `like`/`dislike` feed Task 4.4 learning; feedback never mutates source evidence) and `GET /v1/search-subscriptions/{id}/feedback?kind=&property_id=` (the search's visit questions plus the caller's own notes, likes and dislikes).
- [ ] **5.1.4** — Add `POST /v1/properties/{id}/tours` (`201`; visible to members) creating, for the recording member, at most three post-tour verification requests with the highest VOI above the threshold.
- [ ] **5.1.5** — Add MCP tool `record_feedback` (write, membership check) with a parity case.
- [ ] **5.1.6** — Tests: shortlist twice in subscription 1 then open subscription 2; a second member sees the entry with `saved_by`; non-member `404` on read and `PUT`; visit question listed for subscription 1 only and visible to every member; empty text `422`; member A's note invisible to member B; tour creates at most three requests, highest VOI first.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-INT` passes the cases in 5.1.6.
- [ ] **Requirements:**
    - [ ] "`PUT /search-subscriptions/{subscription_id}/shortlist/{property_id}` returns `201` on the first save and `200` with the same entry on repeats; one row exists per subscription/property. `DELETE` on the same path returns `204`, also when no entry exists."
    - [ ] "An entry saved in one subscription does not appear in `GET /search-subscriptions/{other_id}/shortlist` for the same account. Every member of a search sees entries saved by any member, each with `saved_by`. A non-member receives `404` on read and on `PUT`."
    - [ ] "`POST /properties/{id}/feedback` (D30) with `kind: visit_question` and non-empty `text` stores a question visible to every member of the subscription; `GET /search-subscriptions/{subscription_id}/feedback?kind=visit_question&property_id=...` lists it for any member; a non-member receives `404`. Empty `text` returns `422`. Feedback of kind `note`, `like` or `dislike` appears only in its author's responses."
    - [ ] "`POST /properties/{id}/tours` records a visit visible to every member of the subscription and creates, for the recording member, at most three post-tour verification requests, the highest VOI scores above the configured threshold (D37)."
    - [ ] "`backend/tests/contract/test_api.py` and `backend/tests/integration/test_tenancy.py` cover these cases."
    - [ ] "Inside a shared search, member A's `note`, `like` and `dislike` feedback never appears in member B's responses, while A's visit questions, shortlist entries, tours, search versions and the search's evaluations do (`test_tenancy.py` …)."
    - [ ] "Include subscription ID and version in … shortlist records."
    - [ ] MCP tool list: "`record_feedback` (phase 5)".
    - [ ] Key scenarios: shortlist twice; flag a visit question; record a tour; "Member A writes a `note` and a `visit_question` on a shared search | Member B sees the visit question and not the note".
- [ ] **Verify:** `select count(*) from shortlist_entries where search_subscription_id=$1 and property_id=$2` is 1 after the repeat-save test.

---

### Task 5.2: Email and In-App Notifications

Dispatch notifications through a pluggable `NotificationStrategy` with deduplication, member fan-out, policy controls and provider reconciliation.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 5.1
**Decisions:** D51, D10, D54, D55, A1

**Files:**

```
backend/src/property_focus/application/notifications.py          (create)
backend/src/property_focus/adapters/notifications/__init__.py    (create)
backend/src/property_focus/adapters/notifications/email.py       (create)
backend/src/property_focus/adapters/notifications/in_app.py      (create)
backend/src/property_focus/application/ports.py                  (modify)
backend/src/property_focus/runtime/scheduled.py                  (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/migrations/versions/0019_notifications.py                (create)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_notifications.py                         (create)
backend/tests/integration/test_workflows.py                      (modify)
backend/tests/contract/test_api.py                               (modify)
```

**Sub-tasks:**

- [ ] **5.2.1** — Migration `0019`: `notifications` and `notification_deliveries` (recipient user and tenant, nullable subscription, event, result revision, strategy `EMAIL`/`IN_APP`, deep link, dedupe key, provider ID, state; strategy dedupe unique) and per-user channel settings.
- [ ] **5.2.2** — Define `NotificationStrategy` and implement `EmailNotificationStrategy` (Mailpit locally; provider idempotency where available) and `InAppNotificationStrategy`; selection logic in `application/notifications.py` needs no change to add a strategy.
- [ ] **5.2.3** — Dispatch: a search event notifies each active member once through that member's channel settings, with the recipient user in the dedupe key; a member removed before dispatch gets nothing; dedupe uses result or event revision plus recipient and strategy; subscription ID and version are part of alert dedupe.
- [ ] **5.2.4** — Apply user opt-out and the subscription notification policy (threshold, digest window, cooldown); deliver Task 2.2 source-login actions to operators once per unresolved episode.
- [ ] **5.2.5** — Every notification deep-links to `/homebuyer/properties/{id}` (or the Investor equivalent); content discloses no private financial details; the linked page requires authenticated authorization.
- [ ] **5.2.6** — Ambiguous email outcome: record `notification_unknown`, keep the in-app record, schedule no automatic retry; the `repair` task reconciles with the provider and resends only when the provider reports no delivery.
- [ ] **5.2.7** — Add `GET /v1/notifications` (cursor, unread filter), `PATCH /v1/notifications/{id}` (`{read: true}`) and `PUT /v1/notification-settings` (channel opt-ins, addresses, timezone).
- [ ] **5.2.8** — Tests: fake strategy registration; dedupe keys; two-member fan-out; removed member skipped; replay creates no duplicate in-app record; cooldown; email timeout with unknown outcome; contract cases for the three routes.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` (`test_notifications.py`) and `BE-INT` pass the cases in 5.2.8.
- [ ] **Requirements:**
    - [ ] "`EmailNotificationStrategy` and `InAppNotificationStrategy` implement one `NotificationStrategy` protocol; adding a strategy requires no change to `application/notifications.py` selection logic (`backend/tests/unit/test_notifications.py` registers a fake strategy)."
    - [ ] "Replaying an event does not duplicate the in-app record; email dedupe uses result/event revision plus recipient/strategy and records ambiguous provider outcomes."
    - [ ] "A search event notifies each active member once, through that member's own channel settings, with the recipient user in the dedupe key; a member removed before dispatch receives nothing (`test_notifications.py`)."
    - [ ] "User opt-out and the subscription's notification policy (threshold, digest window, cooldown) affect dispatch; source login failures notify once per unresolved episode."
    - [ ] "Notification content does not disclose private financial details; deep links require authenticated authorization."
    - [ ] "Every notification links to `/homebuyer/properties/{id}` (or the Investor equivalent)."
    - [ ] "Notification replay, email redelivery and workflow retry each produce one committed effect, enforced by database uniqueness constraints listed in the data model."
    - [ ] Key scenarios: "Search event on a search with two members | One notification per member …"; "Email send times out with unknown provider outcome | `notification_unknown`; no automatic retry; `repair` reconciles before any resend".
- [ ] **Verify:** Trigger one search event in local Compose; Mailpit shows one email per member and the API shows one in-app record per member.

---

### Task 5.3: Search Sharing API

Invite people by email, accept invitations with a single-use token, list members, and remove or leave, with access ending on the next request.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 5.2
**Decisions:** A1, D5 (surviving parts), D17

**Files:**

```
backend/src/property_focus/application/subscriptions.py          (modify)
backend/src/property_focus/domain/subscriptions.py               (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/migrations/versions/0020_search_invitations.py           (create)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_subscriptions.py                         (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **5.3.1** — Migration `0020`: `search_invitations` (lowercased email, SHA-256 of the token only, invited_by, expires_at 7 days, state `PENDING`/`ACCEPTED`/`EXPIRED`, accepted_by/at; partial unique index for one `PENDING` per search/email) and one dedicated database function that reads an invitation by token hash (not a membership policy).
- [ ] **5.3.2** — Add `POST /v1/search-subscriptions/{id}/invitations` (`{email}`): owner gets `201` and exactly one invitation email with a single-use link; a repeat for a `PENDING` email returns `200` with that invitation and sends nothing; a member gets `403`; a non-member gets `404`.
- [ ] **5.3.3** — Add `POST /v1/search-invitations/{token}/accept`: a signed-in account whose verified email matches (case-insensitive) gets `200 {search_subscription_id, role: member}` and an active member row; an account created after the invitation accepts the same way; mismatched email, expired, used or unknown token each return `404` and create no row.
- [ ] **5.3.4** — Add `GET /v1/search-subscriptions/{id}/members` (active members with role plus pending invitations, for any member) and `DELETE /v1/search-subscriptions/{id}/members/{user_id}` (`204` for owner removing a member or a member leaving; `409 owner_cannot_leave` when targeting the owner; `403` when a member targets another member; sets `removed_at`).
- [ ] **5.3.5** — Keep membership changes out of MCP.
- [ ] **5.3.6** — Tests: invite and accept by a new account; repeat invite; member invite `403`; wrong email `404`; expired and reused token `404`; owner removal `409`; member-removes-member `403`; after removal, the removed account's next request to the shortlist and `get_property_evaluation` returns `404` and it receives no further notifications for the search.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 5.3.6.
- [ ] **Requirements:**
    - [ ] "Phase 5: `POST /search-subscriptions/{id}/invitations` with `{email}` from the owner returns `201` and sends exactly one invitation email carrying a single-use link; repeating it for an email with a `PENDING` invitation returns `200` with that invitation and sends nothing. A member who is not the owner gets `403`; a non-member gets `404`."
    - [ ] "`POST /search-invitations/{token}/accept` from a signed-in account whose verified email equals the invited email (case-insensitive) returns `200` and creates an active `member` row; an account created after the invitation accepts the same way. A mismatched email, an expired, already-used or unknown token each return `404` and create no membership row."
    - [ ] "`GET /search-subscriptions/{id}/members` returns every active member with role, plus pending invitations, to any member."
    - [ ] "`DELETE /search-subscriptions/{id}/members/{user_id}` returns `204` when the owner removes a member or a member removes themselves. Targeting the owner returns `409` with code `owner_cannot_leave`; a member removing another member gets `403` (plan recommendation). Authorization reads `search_members` on every request and never relies on token claims, so the removed account's next request to any route or tool for that search returns `404`, and it receives no later notification for the search (`test_tenancy.py`)."
    - [ ] "Invitation acceptance reads `search_invitations` by token hash through one dedicated database function, not through a membership policy."
    - [ ] "Do not expose … search membership changes … in the initial MCP surface."
    - [ ] Key scenarios: owner invites `spouse@example.com`; different verified email presents the token; owner removes the spouse.
- [ ] **Verify:** Run `\d search_invitations` in psql; the table has a token hash column and no raw token column.

---

### Task 5.4: Operations API, Cost Views and Budget Controls

Give operators a scoped, audited view of failed and paused work, replay, costs and budget-tier limits.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 5.2
**Decisions:** D55, D56

**Files:**

```
backend/src/property_focus/application/workflows.py              (modify)
backend/src/property_focus/application/budgets.py                (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/integration/test_workflows.py                      (modify)
```

**Sub-tasks:**

- [ ] **5.4.1** — Add `GET /v1/ops/workflow-runs` (failure class, code, age, source, cursor): failed and paused work including malformed intake messages with evidence references, scoped to operators.
- [ ] **5.4.2** — Add `POST /v1/ops/workflow-runs/{id}/retries` (steps, reason, idempotency key; `202` child run with prior-attempt links; audited); `BUG_UNKNOWN` work is replayed only this way.
- [ ] **5.4.3** — Add `GET /v1/ops/costs` (grouping by budget tier, analysis tier, category, analyzer, provider, property): estimated versus actual, reservations, missing provider prices flagged, budget reset timezone; never show an unknown cost as zero.
- [ ] **5.4.4** — Add `PATCH /v1/ops/budgets/{budget_tier}` (limit, period, reason; `200`; audited) for operators with budget-change scope.
- [ ] **5.4.5** — Tests: non-operator gets `403` on every ops route; retry creates a child run linked to prior attempts; cost grouping with an unknown price; budget change writes an audit event with actor and reason.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-INT` passes the cases in 5.4.5.
- [ ] **Requirements:**
    - [ ] "`GET /ops/workflow-runs` and `pages/Operations.tsx` list failed work by class with scoped, audited replay." (UI in Task 5.8)
    - [ ] "Phase 5: `GET /ops/costs` and `pages/Operations.tsx` show estimated versus actual usage by budget tier, analysis tier, category, analyzer and provider, missing provider prices, and budget reset timezone; an operator with budget-change scope adjusts tier limits with an audit record. Do not report an unknown cost as zero."
    - [ ] "Operators need an explicit role and scope for retry, merge, source login, and budget changes; audit records identify actor and reason." (retry and budget parts)
    - [ ] "Failed work persists in Postgres and appears in the operations screen, not only in logs or dead-letter queues."
    - [ ] "malformed messages remain visible in operations with an evidence reference."
- [ ] **Verify:** `GET /v1/ops/costs` on seeded data returns `null` (not `0`) for a usage event with an unknown price.

---

### Task 5.5: Homebuyer Shell, Feed and Fit Components

Build the Homebuyer route group and feed with requirements groups, fit dimensions, coverage and result status, plus the browser E2E harness. The feed is the first page that issues API queries, so this task also stops a `401` sign-in loop (review observation P46).

**Modules:** `frontend`, root tooling
**Depends on:** 5.1
**Decisions:** D10, D9, D53, D29

**Files:**

```
frontend/package.json                                    (modify)
frontend/playwright.config.ts                            (create)
frontend/src/app/router.tsx                              (modify)
frontend/src/products/homebuyer/Layout.tsx               (create)
frontend/src/products/homebuyer/HomeFeed.tsx             (create)
frontend/src/components/FitDimensions.tsx                (create)
frontend/src/components/ResultStatus.tsx                 (create)
frontend/src/styles.css                                  (modify)
frontend/src/api/baseApi.ts                              (modify)
frontend/src/auth/session.ts                             (modify)
frontend/tests/evaluation.test.tsx                       (create)
frontend/tests/baseApi.test.ts                           (modify)
frontend/tests/router.test.tsx                           (modify)
pnpm-lock.yaml                                           (modify)
Makefile                                                 (modify)
```

**Sub-tasks:**

- [ ] **5.5.1** — Add `@axe-core/playwright` and Playwright; write `playwright.config.ts` (projects, base URL, test directory `e2e/`).
- [ ] **5.5.2** — Add `make test-e2e`: start isolated synthetic services, including the local Keycloak realm with synthetic users, run `pnpm --filter @property-focus/web exec playwright test` with `frontend/playwright.config.ts`, and clean up its own resources.
- [ ] **5.5.3** — Build `products/homebuyer/Layout.tsx` (navigation, `/homebuyer` route group behind the sign-in boundary) and `HomeFeed.tsx` (requirements group, overall fit, unknowns; `fail` hidden unless filtered) using the generated client only.
- [ ] **5.5.4** — Build `FitDimensions.tsx` (dimension scores, coverage, critical mismatches, unknown contributions) and `ResultStatus.tsx` (`PARTIAL`, stale with invalidation time, `UNKNOWN`), never using color alone to convey eligibility or failure.
- [ ] **5.5.5** — Write `frontend/tests/evaluation.test.tsx`: fit dimensions render coverage; unknown requirements are shown in their own group; status indicators have text labels.
- [ ] **5.5.6** — Stop the `401` sign-in loop (P46): a `401` on a request made with an access token from a sign-in that has just completed does not end the session and start another sign-in; the app shows a sign-in failure with a retry action. A `401` on an older token still ends the session. Tests in `baseApi.test.ts` and `router.test.tsx`: an API that rejects every fresh token produces one sign-in and then the failure view, not a second authorize navigation; a `401` after a token has been in use still starts a new sign-in.

**Validation:**

- [ ] **Build:** `FE` exits 0.
- [ ] **Tests:** `pnpm --filter @property-focus/web test --run` passes `evaluation.test.tsx` and the 5.5.6 cases in `baseApi.test.ts` and `router.test.tsx`.
- [ ] **Requirements:**
    - [ ] "Phase 5 ships Homebuyer onboarding, feed and property page under `/homebuyer` (`products/homebuyer/HomeFeed.tsx`, `products/homebuyer/PropertyDetail.tsx`). … Both call the same generated client and application use cases." (feed part)
    - [ ] "The feed orders homes by requirements group, pass before unknown, and then by overall fit. It hides `fail` homes by default and shows them only when you filter for them."
    - [ ] "The UI does not use color alone to convey eligibility or failure."
    - [ ] "A `401` from the API on a request made with an access token from a sign-in that has just completed does not start another sign-in: the app stops and shows a sign-in failure with a retry action …" (P46)
    - [ ] "`make test-e2e` MUST start isolated synthetic services, including the local Keycloak realm with synthetic users, run `pnpm --filter @property-focus/web exec playwright test` with `frontend/playwright.config.ts`, and clean up its own resources."
- [ ] **Verify:** `make test-e2e` with an empty `e2e/` directory starts and stops services and leaves no running containers (`docker ps`).

---

### Task 5.6: Homebuyer Property Page

Build the seven-section property page with evidence viewing, timeline, deep research status, shortlist, visit questions, tours, feedback and corrections.

**Modules:** `frontend`
**Depends on:** 5.5
**Decisions:** D10, D35, D36, D37, D53, D6

**Files:**

```
frontend/src/products/homebuyer/PropertyDetail.tsx       (create)
frontend/src/components/Timeline.tsx                     (create)
frontend/src/components/EvidenceViewer.tsx               (create)
frontend/src/components/DeepResearchPanel.tsx            (create)
frontend/src/app/router.tsx                              (modify)
frontend/src/styles.css                                  (modify)
frontend/tests/property-detail.test.tsx                  (create)
```

**Sub-tasks:**

- [ ] **5.6.1** — Build `PropertyDetail.tsx` at `/homebuyer/properties/{id}` with the sections Summary, Photos, Personal Fit, Property Intelligence, Deep Research, Timeline and Feedback, each with a heading.
- [ ] **5.6.2** — Build `EvidenceViewer.tsx` (authorized image and document display through signed downloads, region highlights) and `Timeline.tsx` (listing history, analysis runs, evaluation changes).
- [ ] **5.6.3** — Build `DeepResearchPanel.tsx` (tier state, trigger reasons, deep findings, manual deep-analysis request).
- [ ] **5.6.4** — Add shortlist, visit question, tour recording, feedback (note, like, dislike) and correction (claim verification) actions.
- [ ] **5.6.5** — A location or enrichment timeout leaves property facts visible with component status, last success time and retry information.
- [ ] **5.6.6** — Write `property-detail.test.tsx`: seven headings render; a `PARTIAL` component shows status, last success and retry text.

**Validation:**

- [ ] **Build:** `FE` exits 0.
- [ ] **Tests:** `pnpm --filter @property-focus/web test --run` passes `property-detail.test.tsx`.
- [ ] **Requirements:**
    - [ ] "The property page renders the sections Summary, Photos, Personal Fit, Property Intelligence, Deep Research, Timeline and Feedback (D10)."
    - [ ] "A location timeout leaves property facts visible with component status, last success time and retry information."
    - [ ] Homebuyer property page: "Lead with fit, layout, location, affordability and unknowns. Shortlist a home or flag a question for a visit."
- [ ] **Verify:** Open `/homebuyer/properties/{id}` under `make dev` with seeded data and confirm the seven section headings in order.

---

### Task 5.7: Onboarding, Preferences, Interview and Manual Add UI

Build Homebuyer onboarding with the interview and search creation, the preferences page with versions, proposals and apply-profile, and manual add.

**Modules:** `frontend`
**Depends on:** 5.5
**Decisions:** D15, D16, D17, A1

**Files:**

```
frontend/src/products/homebuyer/Onboarding.tsx           (create)
frontend/src/components/InterviewPanel.tsx               (create)
frontend/src/pages/Preferences.tsx                       (create)
frontend/src/pages/AddProperty.tsx                       (create)
frontend/src/app/router.tsx                              (modify)
frontend/tests/preferences.test.tsx                      (create)
```

**Sub-tasks:**

- [ ] **5.7.1** — Build `Onboarding.tsx`: run the interview, then create a search with budget and geography, optionally applying the profile.
- [ ] **5.7.2** — Build `InterviewPanel.tsx`: structured questions first, conversation turns, proposal confirmation through the approval path.
- [ ] **5.7.3** — Build `Preferences.tsx`: profile and subscription versions, proposals with approve and reject, material-change confirmation dialog, revert, apply profile to a search (with the list of items that would become visible to members).
- [ ] **5.7.4** — Build `AddProperty.tsx`: add by URL or address; show `unsupported_source` as a field error.
- [ ] **5.7.5** — Write `preferences.test.tsx`: material-change `409` opens a confirmation dialog; approve sends the proposal ID; unsupported URL shows the error text.

**Validation:**

- [ ] **Build:** `FE` exits 0.
- [ ] **Tests:** `pnpm --filter @property-focus/web test --run` passes `preferences.test.tsx`.
- [ ] **Requirements:**
    - [ ] "Phase 5 adds `frontend/src/pages/AddProperty.tsx`."
    - [ ] "A user can sign in, run the interview, inspect the feed, open evidence, inspect fit dimensions and unknowns, edit preferences, approve a proposal, and submit a correction." (interview, edit and approve parts)
    - [ ] Homebuyer onboarding: "Run the preference interview, then start a home search with budget and geography, optionally shared with a spouse or partner."
- [ ] **Verify:** Under `make dev`, complete onboarding with the fake provider and confirm a search appears in `GET /v1/search-subscriptions`.

---

### Task 5.8: Sharing, Verification, Notifications and Operations UI

Build the members page, the VOI-ordered verification queue, the notification inbox and settings, and the role-gated operations page.

**Modules:** `frontend`
**Depends on:** 5.3, 5.4, 5.6
**Decisions:** A1, D37, D51, D55, D56

**Files:**

```
frontend/src/pages/SearchMembers.tsx                     (create)
frontend/src/pages/Verification.tsx                      (create)
frontend/src/pages/Notifications.tsx                     (create)
frontend/src/pages/Operations.tsx                        (create)
frontend/src/app/router.tsx                              (modify)
frontend/tests/operations.test.tsx                       (create)
```

**Sub-tasks:**

- [ ] **5.8.1** — Build `SearchMembers.tsx`: members, pending invitations, invite (owner only), leave and remove.
- [ ] **5.8.2** — Build `Verification.tsx`: VOI-ordered queue and correction submission.
- [ ] **5.8.3** — Build `Notifications.tsx`: in-app inbox, read state and channel settings.
- [ ] **5.8.4** — Build `Operations.tsx` (operator role only): failed work by class, credential state, costs (unknown shown as unknown), budget-tier edits with reason, retry with confirmation.
- [ ] **5.8.5** — Write `operations.test.tsx`: replay requires confirmation and a reason; controls hidden without the operator role; unknown cost rendered as text, not `0`.

**Validation:**

- [ ] **Build:** `FE` exits 0.
- [ ] **Tests:** `pnpm --filter @property-focus/web test --run` passes `operations.test.tsx`.
- [ ] **Requirements:**
    - [ ] "`GET /ops/workflow-runs` and `pages/Operations.tsx` list failed work by class with scoped, audited replay."
    - [ ] "Operators use an operations screen for queue age, failed work, credential status, cost and replay history."
    - [ ] "Users choose channels."
- [ ] **Verify:** Sign in as a non-operator under `make dev`; `/operations` shows no controls and the API returns `403`.

---

### Task 5.9: Dogfood, Sharing and Accessibility E2E Suites

Write the browser E2E suites for the user loop, two-account sharing and accessibility.

**Modules:** `frontend`
**Depends on:** 5.6, 5.7, 5.8
**Decisions:** D10, A1, D46

**Files:**

```
frontend/e2e/dogfood.spec.ts                             (create)
frontend/e2e/sharing.spec.ts                             (create)
frontend/e2e/accessibility.spec.ts                       (create)
```

**Sub-tasks:**

- [ ] **5.9.1** — `dogfood.spec.ts`: sign in, run the interview, inspect the feed, open evidence, inspect fit dimensions and unknowns, edit preferences, approve a proposal, submit a correction, add a property by address, shortlist a home and flag a visit question from the property page, assert each of the seven headings, and follow a notification link to `/homebuyer/properties/{id}`; include auth redirects, evidence permissions, partial results and operations roles.
- [ ] **5.9.2** — `sharing.spec.ts`: two accounts; invite, accept, shared shortlist and visit question, author-only notes, removal followed by `404`.
- [ ] **5.9.3** — `accessibility.spec.ts`: axe scan of the feed, property page dialogs and verification views with zero serious or critical violations; complete feed, evidence dialog and verification correction with the keyboard alone.

**Validation:**

- [ ] **Build:** `FE` exits 0.
- [ ] **Tests:** `E2E` passes all three specs.
- [ ] **Requirements:**
    - [ ] "A user can sign in, run the interview, inspect the feed, open evidence, inspect fit dimensions and unknowns, edit preferences, approve a proposal, and submit a correction."
    - [ ] "`dogfood.spec.ts` asserts each heading." and "`dogfood.spec.ts` adds a property by address." and "`frontend/e2e/dogfood.spec.ts` shortlists a home and flags a visit question from the Homebuyer property page."
    - [ ] "`frontend/e2e/accessibility.spec.ts` (run by `make test-e2e`) scans the feed, property page dialogs and verification views with `@axe-core/playwright` and reports zero serious or critical violations. The same spec completes feed, evidence dialog and verification correction using the keyboard alone."
    - [ ] Sharing E2E: "Two accounts: invite, accept, shared shortlist and visit question, author-only notes, removal" (`frontend/e2e/sharing.spec.ts`).
- [ ] **Verify:** `make test-e2e` output lists the three spec files and an axe summary with zero serious or critical violations.

---

### Task 5.10: Backups, Restore Drill and Dogfood Storage Check

Back up Postgres and evidence storage daily off the host, verify restores in isolation, prove write-once behavior on the real dogfood bucket, and delete unused dynamically registered MCP clients automatically before other users can sign up (user decision P49).

**Modules:** `backend`, `infrastructure/dogfood`, `docs`
**Depends on:** 5.4
**Decisions:** D52, D44, D19, A4, A3

**Files:**

```
backend/src/property_focus/runtime/scheduled.py                  (modify)
backend/src/property_focus/cli.py                                (modify)
infrastructure/dogfood/backup.sh                                 (create)
infrastructure/dogfood/scheduler.crontab                         (modify)
backend/tests/integration/test_recovery.py                       (create)
backend/tests/integration/test_evidence_immutability.py          (create)
.github/workflows/backup-restore-check.yml                       (create)
backend/scripts/keycloak_realm.py                                (modify)
backend/tests/unit/test_keycloak_realm_script.py                 (modify)
backend/tests/integration/test_keycloak_realm.py                 (modify)
infrastructure/dogfood/deploy.sh                                 (modify)
infrastructure/dogfood/compose.yaml                              (modify)
.env.example                                                     (modify)
docs/operations.md                                               (modify)
Makefile                                                         (modify)
```

**Sub-tasks:**

- [ ] **5.10.1** — Register the `backup` scheduled kind (daily crontab entry): back up Postgres (the application database and Keycloak's `keycloak` database) and evidence storage independently to off-server storage with the backup role, keep 30 daily copies and 12 monthly snapshots, and fail when the latest backup is older than 26 hours. `backup.sh` wraps `cli run-task backup` in the scheduler container for manual runs and drills.
- [ ] **5.10.2** — Implement `cli restore-check` and `make restore-check`: restore a selected test backup into an isolated database and object namespace and run `test_recovery.py`; refuse production targets.
- [ ] **5.10.3** — Write `test_recovery.py`: relational counts, evidence manifests and digests, decryption access, outbox restart after restore, and a synthetic user signing in against a Keycloak started on the restored `keycloak` database.
- [ ] **5.10.4** — Write `test_evidence_immutability.py` and `make storage-check ENV=dogfood`: against a dedicated test prefix in the real dogfood bucket (refuse MiniStack endpoints), an overwrite or delete with runtime credentials fails or leaves the recorded version readable with its recorded SHA-256; objects restored from the independent backup match their digests.
- [ ] **5.10.5** — Add `backup-restore-check.yml` (restore validation on isolated resources) and document the restore-drill procedure (including the Keycloak sign-in check), quarterly cadence and RPO/RTO recording in `docs/operations.md`.
- [ ] **5.10.6** — Add `keycloak_realm.py prune-clients`: over the host's loopback admin port, delete each dynamically registered client of realm `property-focus` that has had no user or offline session for `PF_KEYCLOAK_CLIENT_IDLE_DAYS` days (default 30; add it to `.env.example` and the dogfood `compose.yaml` environment), never a client the realm file defines, and log each deleted client ID. `deploy.sh` installs a daily host schedule that runs it the same way it runs `apply` (outside the scheduler container, because no application module calls the identity provider's admin API). Replace the manual pruning steps in `docs/operations.md` with this job and a manual run command.
- [ ] **5.10.7** — Tests: in `test_keycloak_realm_script.py`, the selection rule with a fake admin client and an injected clock (realm-file clients never selected; a client with a session never selected; a client idle for less than the period kept); in `test_keycloak_realm.py`, register two clients, sign a user in through one, run `prune-clients` with the idle period elapsed, and assert only the unused client is deleted and a second run deletes nothing.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `RECOVERY` (`make restore-check`) passes locally; `test_runtime_manifests.py` accepts `backup`; `BE-UNIT` and `BE-INT` pass the `prune-clients` cases in 5.10.7.
- [ ] **Requirements:**
    - [ ] "The `backup` scheduled task (`cli run-task backup`, started daily by `scheduler.crontab`; `infrastructure/dogfood/backup.sh` wraps it for manual runs) backs up Postgres (the application database and Keycloak's `keycloak` database) and evidence storage to off-server storage, keeps 30 daily copies and 12 monthly snapshots, and fails if the latest backup is older than 26 hours."
    - [ ] "A restore drill … verify relational counts, evidence manifests/digests and decryption access, and sign a synthetic user in against the restored Keycloak (A4)"
    - [ ] "`make restore-check` restores a selected test backup to an isolated database/object namespace and runs `test_recovery.py`; it MUST refuse production targets."
    - [ ] "`make storage-check ENV=dogfood` runs `backend/tests/integration/test_evidence_immutability.py` against the real DigitalOcean evidence bucket. A runtime-credential overwrite or delete of a committed object fails or leaves the recorded version readable with its recorded SHA-256; objects restored from the independent backup match their recorded digests. A MiniStack run of the same test does not count."
    - [ ] "DigitalOcean dogfood must pass write-once and restore tests; an emulator alone does not establish durable immutability."
    - [ ] "Phase 5, before other users can sign up: a daily job deletes each dynamically registered client that has had no user or offline session for `PF_KEYCLOAK_CLIENT_IDLE_DAYS` days (default 30; plan recommendation) … never deletes a client the realm file defines, and logs each deleted client ID."
- [ ] **Verify:** `make restore-check` with a production-like target name exits non-zero with a refusal message. `bash -n infrastructure/dogfood/deploy.sh` exits 0 and `grep -n prune-clients infrastructure/dogfood/deploy.sh` shows the daily schedule. **Live (explicit user approval required):** `make storage-check ENV=dogfood`.

---

### Task 5.11: End-to-End Trace, Alerts and Dogfood Observability

Link one trace from MLS email to notification and define the alert set with documented thresholds.

**Modules:** `backend`, `infrastructure/dogfood`, `docs`
**Depends on:** 5.2
**Decisions:** D50, D55, D56

**Files:**

```
backend/src/property_focus/adapters/telemetry.py                 (modify)
backend/src/property_focus/application/notifications.py          (modify)
backend/src/property_focus/application/intelligence.py           (modify)
infrastructure/dogfood/otel-collector.yaml                       (modify)
backend/tests/integration/test_telemetry.py                      (modify)
docs/operations.md                                               (modify)
```

**Sub-tasks:**

- [ ] **5.11.1** — Add spans for property resolution, analysis run, model call, claim write and notification, linked across async hops with span links.
- [ ] **5.11.2** — Test: replaying `fixtures/mail/multipart.eml` produces one trace containing spans for ingestion, property resolution, workflow, analysis run, model call, claim write and notification.
- [ ] **5.11.3** — Define alerts on oldest queue age, intake lag, credential failure, repeated parser failure, DLQ growth, failed-work count by class, workflow latency, error rate, database saturation and budget exhaustion; thresholds are configurable and documented in `docs/operations.md`; emit the measured `imap-poll` gap distribution.
- [ ] **5.11.4** — Route these metrics and alerts through `otel-collector.yaml` to the self-hosted stack.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-INT` passes the end-to-end trace test.
- [ ] **Requirements:**
    - [ ] "Replaying `fixtures/mail/multipart.eml` produces one trace, linked across async hops with span links, containing spans for ingestion, property resolution, workflow, analysis run, model call, claim write and notification (`test_telemetry.py`)."
    - [ ] "Alert on oldest queue age, intake lag, credential failure, repeated parser failure, DLQ growth, failed-work count by class, workflow latency, error rate, database saturation and budget exhaustion. Initial thresholds are configurable and documented, then tuned from pilot observations."
    - [ ] "Dogfood reports the measured poll-gap distribution."
    - [ ] Key scenario: "Fixture email replay | One linked trace from intake to notification".
- [ ] **Verify:** `docs/operations.md` lists each of the ten alerts with its initial threshold.

---

### Task 5.13: Per-Account Rate Limits for REST and MCP

Limit each account's (tenant's) authenticated request rate on REST and MCP, with `429` and `Retry-After` (A5). Numbered 5.13 so existing task numbers stay stable; it runs before Task 5.12.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 4.8
**Decisions:** A5, D55, D28

**Files:**

```
backend/src/property_focus/config.py                             (modify)
backend/src/property_focus/application/rate_limits.py            (create)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/migrations/versions/<NNNN>_rate_limits.py                (create)
backend/src/property_focus/adapters/rest/app.py                  (modify)
backend/src/property_focus/adapters/mcp/server.py                (modify)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
.env.example                                                     (modify)
backend/tests/unit/test_rate_limits.py                           (create)
backend/tests/unit/test_config.py                                (modify)
backend/tests/integration/test_rate_limits.py                    (create)
docs/system/modules/rest-api.md                                  (modify)
docs/system/modules/mcp-adapter.md                               (modify)
```

The migration takes the next free number when the task runs (Rules for Every Task); later illustrative numbers shift by one.

**Sub-tasks:**

- [ ] **5.13.1** — Add `PF_RATE_LIMIT_REST_PER_MINUTE` (default 120) and `PF_RATE_LIMIT_MCP_PER_MINUTE` (default 60; user-confirmed initial limits, 2026-11-02) to `config.py` and `.env.example`, positive integers only; add `test_config.py` cases.
- [ ] **5.13.2** — Migration: table `request_rate_windows` (tenant, surface `rest` or `mcp`, window start, count; primary key on the first three), with runtime-role grants and forced RLS by tenant, created in this migration only.
- [ ] **5.13.3** — Implement `application/rate_limits.py`: `admit(context, surface)` increments the current one-minute window with one atomic upsert that returns the new count, inside the tenant's unit of work; over the limit it raises `CodedError` `rate_limited` carrying the seconds until the window ends (at least 1). Old windows are deleted by the same statement path or by `repair`; they never block admission.
- [ ] **5.13.4** — REST: call `admit` after authentication for every authenticated route; map `rate_limited` to `429` `application/problem+json` with a `Retry-After` header; leave `/health/*` uncounted. Add the `429` response to the OpenAPI schema and run `GEN`.
- [ ] **5.13.5** — MCP: call `admit` after the `TokenVerifier` has resolved the context and before any tool runs; answer HTTP `429` with `Retry-After` and a JSON-RPC error `-32000` whose `data` is `{"code": "rate_limited", "retryAfterSeconds": <n>}`, through the same outer handler that maps `dependency_transient` to `503`. Leave well-known metadata and unauthenticated requests uncounted.
- [ ] **5.13.6** — Tests: `test_rate_limits.py` (unit): window start, remaining seconds and the minimum of 1. `test_rate_limits.py` (integration, Keycloak-issued tokens, low limits set by the test): request N+1 in a window gets `429` with `Retry-After` on REST and MCP and runs no use case; another account in the same window succeeds; REST and MCP counts are separate; the next window admits again (injected clock); 20 concurrent requests with limit 10 admit exactly 10. Update `rest-api.md` and `mcp-adapter.md`.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 5.13.6.
- [ ] **Requirements:**
    - [ ] "After authentication, `application/rate_limits.py` counts each account's requests per surface (REST, MCP) in a fixed one-minute window stored in Postgres, so every process and Lambda shares one count …"
    - [ ] "Over the limit, REST answers `429` `application/problem+json` with code `rate_limited` and a `Retry-After` header … MCP answers HTTP `429` with the same `Retry-After` and a JSON-RPC error `-32000` … and runs no tool."
    - [ ] "Unauthenticated requests, `/health/*` and well-known metadata are not counted; edge limits (Cloudflare, AWS WAF) cover them."
    - [ ] "`backend/tests/integration/test_rate_limits.py`: request N+1 in one window gets `429` with `Retry-After` on REST and MCP; another account in the same window is unaffected; the REST and MCP counts are separate; the next window admits requests again; concurrent requests at the limit admit exactly N …"
    - [ ] Error table row "`rate_limited` (per-account request limit, phase 5, A5)" and Key scenario "Account A sends 121 REST requests in one minute; account B sends one".
- [ ] **Verify:** On `make dev` with `PF_RATE_LIMIT_REST_PER_MINUTE=2`, a third `GET /v1/workflow-runs/<random id>` within one minute returns `429` with a `Retry-After` header and a problem body with code `rate_limited`.

---

### Task 5.12: Dogfood Ready Gate

Collect the Dogfood ready gate artifacts on DigitalOcean.

**Modules:** all, `specs`
**Depends on:** 5.1–5.11, 5.13, CP-5.A, CP-4.B
**Decisions:** D10, D51, D52, A1, D19, A5

**Files:**

```
specs/property-focus/gates/dogfood-ready.md              (create)
```

**Sub-tasks:**

- [ ] **5.12.1** — Deploy to dogfood through `deploy-dogfood.yml` (live; user approval). Before a second account signs up, confirm on the host that the daily `prune-clients` schedule from 5.10.6 is installed and record it with the 5.10.7 test results in `dogfood-ready.md` (user decision P49). Then run the end-to-end report: two accounts sharing one search complete the intake-to-review loop.
- [ ] **5.12.2** — Record the two-account sharing E2E (`sharing.spec.ts`) including the removed-member `404`, phase 5 MCP parity and the accessibility report.
- [ ] **5.12.3** — Record each dogfood user's confirmed first preference version.
- [ ] **5.12.4** — Run the restore drill into an isolated environment and record measured RPO and RTO against 24-hour and 8-hour objectives in `docs/operations.md`; run `make storage-check ENV=dogfood` (live; user approval).
- [ ] **5.12.5** — Record the per-account rate-limit results (`test_rate_limits.py`, unit and integration) in `dogfood-ready.md`.

**Validation:**

- [ ] **Build:** `VERIFY` exits 0.
- [ ] **Tests:** `E2E`, `RECOVERY` and all backend suites pass.
- [ ] **Requirements:**
    - [ ] Phase 5 exit: "Two accounts sharing one search complete the intake-to-review loop on DigitalOcean; a removed member gets `404`; restore drill passes."
    - [ ] Dogfood ready artifacts: "End-to-end report, registered-client cleanup test, per-account rate-limit tests (`test_rate_limits.py`), two-account sharing E2E (`sharing.spec.ts`) including removed-member `404`, phase 5 MCP parity, accessibility report, user-confirmed preference version, dogfood storage-check and restore report."
    - [ ] "A restore drill runs before the Dogfood ready gate and quarterly afterward: restore into an isolated environment; verify relational counts, evidence manifests/digests and decryption access; record measured RPO/RTO against 24-hour/8-hour initial objectives in `docs/operations.md`."
    - [ ] Key scenario: "Backup restore after host loss | Evidence references resolve and measured RPO/RTO report exists".
- [ ] **Verify:** `dogfood-ready.md` links every artifact. **Live (explicit user approval required):** dogfood deployment, `make smoke-dogfood`, `make storage-check ENV=dogfood`, restore drill on dogfood.

---

## Phase 6: Location Intelligence

**Goal:** A reusable location subsystem with busy/quiet street attributes, the first permitted location source, profile and search destinations, and time-of-day travel times. **Deliverable:** the Location ready gate.

> **Checkpoint CP-6.A (blocking for Tasks 6.1 and 6.3 provider adapters) — first permitted location and travel-time sources.** Owner: user / source owner. The plan lists initial location and travel-time providers under "Research still required". Do not write a provider adapter until the user names a permitted source and `specs/property-focus/source-rights.md` has its entry. Domain, schema and fake-provider work may proceed.

### Task 6.1: Location Source and Location Profiles

Add the first permitted location source and store reusable location profiles with measured traffic claims.

**Modules:** `backend`, `specs`
**Depends on:** 5.12, CP-6.A
**Decisions:** D42, D11, D7, D2

**Files:**

```
backend/src/property_focus/adapters/sources/location.py          (create)
backend/src/property_focus/application/location.py               (create)
backend/src/property_focus/application/enrichment.py             (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/migrations/versions/0021_location_profiles.py            (create)
specs/property-focus/source-rights.md                            (modify)
docs/compatibility.md                                            (modify)
backend/tests/unit/test_location.py                              (create)
```

**Sub-tasks:**

- [ ] **6.1.1** — Migration `0021`: `location_profiles` (property, ontology version, location and traffic claims, freshness, provenance). Add PostGIS only if this source requires polygons or spatial queries; if so, record the extension in the migration and in `docs/compatibility.md`.
- [ ] **6.1.2** — Implement `adapters/sources/location.py` registering `LOCATION_CONTEXT` and `TRAFFIC` capabilities; write `OBSERVED` claims for measured values with `PROVIDER_RESPONSE` evidence.
- [ ] **6.1.3** — Implement `application/location.py`: build location profiles once per property and reuse them for every authorized account.
- [ ] **6.1.4** — Record the source's rights, retention and reuse entry in `source-rights.md`.
- [ ] **6.1.5** — Tests with recorded provider responses: measured attributes are `OBSERVED`; missing data resolves to `UNKNOWN`; a timeout leaves a `PARTIAL` profile with component status.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` passes `test_location.py`.
- [ ] **Requirements:**
    - [ ] "The location subsystem writes claims for the location and traffic attributes in ontology v1, including `location.traffic_level`, … and `location.cul_de_sac`, each `OBSERVED` when measured and `INFERRED` when estimated." (measured part)
    - [ ] "Location intelligence is its own reusable subsystem (D42)."
    - [ ] "Store polygons and spatial queries with PostGIS only if the first location source requires them; record the extension in migrations and compatibility tests."
    - [ ] Location ready artifact: "source-rights entry".
- [ ] **Verify:** `GET /v1/properties/{id}/intelligence` for a seeded property includes a location profile with per-attribute status.

---

### Task 6.2: Street and Traffic Inference

Infer street and traffic attributes from evidence where no measurement exists, label them `INFERRED`, and make `location.traffic_level` usable in preferences.

**Modules:** `backend`
**Depends on:** 6.1
**Decisions:** D42, D14, D9

**Files:**

```
backend/src/property_focus/analyzers/location.py                 (create)
backend/src/property_focus/analyzers/prompts/location-v1.md      (create)
backend/src/property_focus/domain/evaluation.py                  (modify)
backend/tests/unit/test_location.py                              (modify)
backend/tests/unit/test_evaluation.py                            (modify)
```

**Sub-tasks:**

- [ ] **6.2.1** — Implement the street/traffic inference analyzer with a versioned prompt; it emits `INFERRED` claims with confidence and evidence for the D42 traffic keys when no measured value exists.
- [ ] **6.2.2** — Keep measured and inferred values distinct in resolution and in API output.
- [ ] **6.2.3** — Make `location.traffic_level` (`QUIET`, `MODERATE`, `BUSY`) usable in hard and soft preferences and criteria.
- [ ] **6.2.4** — Exclude protected demographic characteristics and proxies for steering from every suitability score; add a test that fails if such an attribute is referenced by a scoring path.
- [ ] **6.2.5** — Tests: inferred versus measured precedence; traffic level as a hard requirement (pass, fail, unknown) and as a soft preference.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` passes the cases in 6.2.5 and the steering-exclusion test.
- [ ] **Requirements:**
    - [ ] "… each `OBSERVED` when measured and `INFERRED` when estimated." (inferred part)
    - [ ] "`location.traffic_level` (`QUIET`, `MODERATE`, `BUSY`) is a first-class attribute usable in hard and soft preferences."
    - [ ] "The product distinguishes measured traffic from an inference." and "Protected demographic characteristics and proxies for steering do not enter suitability scores."
- [ ] **Verify:** `grep -rn "location-v1" backend/src/property_focus/analyzers/location.py` shows the prompt version recorded on runs.

---

### Task 6.3: Profile and Search Destinations and Travel Times

Store private profile destinations and shared search destinations, compute time-of-day travel times, and feed only search destinations into evaluation.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 6.1, CP-6.A
**Decisions:** D43, A1, D9, D16

**Files:**

```
backend/src/property_focus/application/location.py               (modify)
backend/src/property_focus/application/preferences.py            (modify)
backend/src/property_focus/application/subscriptions.py          (modify)
backend/src/property_focus/application/evaluation.py             (modify)
backend/src/property_focus/domain/preferences.py                 (modify)
backend/src/property_focus/domain/subscriptions.py               (modify)
backend/src/property_focus/domain/evaluation.py                  (modify)
backend/src/property_focus/adapters/sources/location.py          (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/migrations/versions/0022_destinations_travel_times.py    (create)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_evaluation.py                            (modify)
backend/tests/unit/test_subscriptions.py                         (modify)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **6.3.1** — Add destinations to preference versions (profile destination, private) and subscription versions (search destination, visible to members): label, kind (`work`, `school`, `family`, `gym`, `airport`, `other`), location, time windows (weekday set plus arrive-by or depart-at), mode and importance. Editable through `PATCH /preferences/{id}` and `PATCH /search-subscriptions/{id}`.
- [ ] **6.3.2** — Migration `0022`: `travel_time_results` (exactly one of owning tenant or search subscription by check constraint; property, destination digest, window, mode, minutes, provider, computed_at; RLS private to the account or scoped to the search).
- [ ] **6.3.3** — Register the `TRAVEL_TIME` capability provider; compute travel time per property, destination and window.
- [ ] **6.3.4** — Feed only search-destination travel times into the search's lifestyle fit; profile-destination results are shown only to their owner. A `rental_hold` evaluation's travel times do not affect overall.
- [ ] **6.3.5** — Recompute: a profile-destination change recomputes only its owner's travel times and no evaluation; a search-destination change recomputes that search's travel times and evaluations. `apply_profile` copies referenced profile destinations, and the `PATCH` `409` lists each one that would become visible.
- [ ] **6.3.6** — Tests: a fellow member gets `404` for another member's profile destination and its results; profile-destination change leaves evaluations unchanged; search-destination change recomputes evaluations; rental hold far from work keeps `lifestyle_fit` null; check constraint rejects both owners set.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 6.3.6.
- [ ] **Requirements:**
    - [ ] "A destination lives on a preference version (profile destination, private) or on a subscription version (search destination, visible to members) and carries label, kind (…), location, time windows (weekday set plus arrive-by or depart-at time), mode and importance. Travel times are computed per property, destination and window and stored in `travel_time_results`."
    - [ ] "Only search destinations feed a search's evaluation; profile destinations produce travel times shown only to their owner (plan recommendation). Every other account, including a fellow member of a shared search, receives `404` for a profile destination or its results (`test_tenancy.py`)."
    - [ ] "Changing a profile destination recomputes only its owner's travel times and changes no evaluation. Changing a search destination recomputes that search's travel times and evaluations."
    - [ ] "On `PATCH`, a copy that changes a hard requirement or a dimension weight is `MATERIAL`, and the `409` lists every profile destination that would become visible to members." (destination listing part)
    - [ ] "Account B's … profile destinations and their travel times … return `404` to every other account."
    - [ ] Key scenario: "Rental hold subscription evaluates a home far from a member's work destination | `lifestyle_fit` null; travel time does not affect overall".
- [ ] **Verify:** Insert a `travel_time_results` row with both `tenant_id` and `search_subscription_id` set; the check constraint rejects it.

---

### Task 6.4: Destinations UI

Let users manage profile and search destinations and see shared evaluations plus their own private travel times.

**Modules:** `frontend`
**Depends on:** 6.3
**Decisions:** D43, A1

**Files:**

```
frontend/src/pages/Preferences.tsx                       (modify)
frontend/src/products/homebuyer/PropertyDetail.tsx       (modify)
frontend/tests/preferences.test.tsx                      (modify)
```

**Sub-tasks:**

- [ ] **6.4.1** — Add profile destination and search destination editors to `Preferences.tsx`, clearly labelled private versus shared.
- [ ] **6.4.2** — On the property page, show the shared evaluation plus travel times to the viewer's own private destinations, with measured versus inferred traffic labels.
- [ ] **6.4.3** — Tests: private and shared labels render; the apply-profile dialog lists destinations that would become visible.

**Validation:**

- [ ] **Build:** `FE` exits 0.
- [ ] **Tests:** `pnpm --filter @property-focus/web test --run` passes the new cases.
- [ ] **Requirements:**
    - [ ] "On a shared search, each member sees the shared evaluation plus travel times to their own private destinations (plan recommendation)."
    - [ ] `frontend/src/pages/Preferences.tsx`: "profile and search destinations (phase 6)".
- [ ] **Verify:** Under `make dev` with two synthetic members, member B's property page does not show member A's profile destination.

---

### Task 6.5: Location Ready Gate

Collect the Location ready gate artifacts.

**Modules:** `backend`, `specs`
**Depends on:** 6.1–6.4
**Decisions:** D42, D43, D45

**Files:**

```
backend/src/property_focus/application/harness.py                (modify)
specs/property-focus/gates/location-ready.md                     (create)
```

**Sub-tasks:**

- [ ] **6.5.1** — Add field-level evaluation of location attributes to the harness, split by measured versus inferred labels.
- [ ] **6.5.2** — Record street inference labels, profile versus search destination privacy tests, and the source-rights entry in `location-ready.md`.

**Validation:**

- [ ] **Build:** `VERIFY` and `EVAL` exit 0.
- [ ] **Tests:** All suites pass.
- [ ] **Requirements:**
    - [ ] Phase 6 exit: "Field-level evaluation with measured versus inferred labels."
    - [ ] Location ready artifacts: "Street inference labels (measured versus inferred), profile versus search destination privacy tests, source-rights entry."
- [ ] **Verify:** `location-ready.md` links every artifact.

---

## Phase 7: Market Intelligence

**Goal:** Explainable comp selection, probabilistic price distributions, the Value dimension and holdout evaluation. **Deliverable:** the Valuation ready gate.

> **Checkpoint CP-7.A (blocking for Tasks 7.1 and 7.4) — licensed comparable-sales source and valuation corpus.** Owner: model owner and user, in phase 7. No valuation corpus or calibration target exists. Do not add a market source adapter or `fixtures/valuation/valuation-holdout.jsonl` until a licensed source is named and its rights are recorded in `source-rights.md`.
>
> **Checkpoint CP-7.B (blocking for valuation promotion) — valuation release thresholds.** Owner: user / model owner. Set thresholds after measuring the baseline on a representative held-out set and store them in `specs/property-focus/evaluation-policy.md` before any promotion. Never pick them after seeing candidate performance. Until then the valuation abstains in published evaluations.

### Task 7.1: Comparable-Sales Source and Market Profiles

Add the first licensed comparable-sales source and store market data with rights, provenance and as-of discipline.

**Modules:** `backend`, `fixtures`, `specs`
**Depends on:** 6.5, CP-7.A
**Decisions:** D38, D2, D11

**Files:**

```
backend/src/property_focus/adapters/sources/market.py            (create)
backend/src/property_focus/application/finance.py                (create)
backend/src/property_focus/application/enrichment.py             (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/migrations/versions/0023_market_comps.py                 (create)
fixtures/valuation/valuation-holdout.jsonl                       (create)
specs/property-focus/source-rights.md                            (modify)
backend/tests/unit/test_finance.py                               (create)
```

**Sub-tasks:**

- [ ] **7.1.1** — Migration `0023`: `market_profiles` and `comparable_sales` (geography/class/time scope, sale date and price, rights, provenance; dedupe on source sale ID).
- [ ] **7.1.2** — Implement `adapters/sources/market.py` as a capability provider; store responses as evidence.
- [ ] **7.1.3** — Exclude sales after the as-of time from every historical run.
- [ ] **7.1.4** — Add the time-split holdout file only when licensed; record rights in `source-rights.md`.
- [ ] **7.1.5** — Tests: duplicate source sale ID deduplicates; a future-dated sale is excluded for an earlier as-of.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` passes the cases in 7.1.5.
- [ ] **Requirements:**
    - [ ] `market_profiles`, `comparable_sales`: "Geography/class/time scope, sale date/price, rights, provenance; dedupe source sale ID; exclude future information from historical runs."
    - [ ] Key scenario: "Sparse comps or future-dated sale | … exclusion; no leaked future sale in backtest" (exclusion part).
- [ ] **Verify:** `uv run pytest backend/tests/unit/test_finance.py -q -k future` passes.

---

### Task 7.2: Deterministic Comp Selection and Price Distribution

Select comparable sales with versioned weights and explain every selection; produce a distribution or abstain.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 7.1
**Decisions:** D38, D39, D40, D53

**Files:**

```
backend/src/property_focus/domain/valuation.py                   (create)
backend/src/property_focus/application/finance.py                (modify)
backend/src/property_focus/runtime/local_workflow.py             (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/migrations/versions/0024_valuations.py                   (create)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_finance.py                               (modify)
```

**Sub-tasks:**

- [ ] **7.2.1** — Migration `0024`: `valuations` (property/profile, comp selector and model versions, as-of, currency, quantiles, central estimate, confidence, selected comps with similarity features, weights and reasons, list-price comparison, status).
- [ ] **7.2.2** — Implement comp scoring in `domain/valuation.py` on micro-location, sale recency, living area, lot size, bed/bath configuration, architecture, condition, views, pool, garage and layout quality with versioned weights; store each comp's per-feature similarity, weight and a reason string.
- [ ] **7.2.3** — Produce p50, ordered p10/p90, confidence (`HIGH`, `MEDIUM`, `LOW` plus interval-width ratio), currency, as-of, list-price comparison (difference and the list price's percentile), comparable IDs, support and model version; return `insufficient_data` when the support policy fails (sparse or stale comps).
- [ ] **7.2.4** — Add the valuation branch to the analyze workflow and expose valuation and comps through `GET /v1/properties/{id}/intelligence` (no separate valuation endpoint).
- [ ] **7.2.5** — Tests: deterministic selection for fixed inputs; quantiles ordered; sparse comps give `insufficient_data`; reasons present for every selected comp.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` passes `test_finance.py` comp selection and quantile cases.
- [ ] **Requirements:**
    - [ ] "Comp selection is deterministic: `domain/valuation.py` scores candidates on micro-location, sale recency, living area, lot size, bed/bath configuration, architecture, condition, views, pool, garage and layout quality with versioned weights; each valuation stores the selected comps, each comp's per-feature similarity and weight, and a reason string. `GET /properties/{id}/intelligence` exposes them."
    - [ ] "Return central estimate (p50), likely range (p10/p90, ordered), confidence (`HIGH`, `MEDIUM`, `LOW` plus the interval-width ratio), currency, as-of time, list-price comparison (difference and list price's percentile within the distribution), comparable IDs, support and model version; return `insufficient_data` when support policy fails."
    - [ ] "Valuations are read through `GET /properties/{id}/intelligence`; the baseline workflow computes them from phase 7, so no separate valuation endpoint exists."
    - [ ] Key scenario: "Sparse comps or future-dated sale | Abstention or exclusion".
- [ ] **Verify:** `GET /v1/properties/{id}/intelligence` for a seeded property shows `valuation.comps[*].reason` non-empty.

---

### Task 7.3: Value Dimension, `scoring_v2` and Valuation UI

Add the Value dimension from the distribution, move owner-occupancy scoring to `scoring_v2`, add the p10 anomaly trigger and the valuation page.

**Modules:** `backend`, `frontend`
**Depends on:** 7.2
**Decisions:** D38, D40, D9, D6

**Files:**

```
backend/src/property_focus/domain/evaluation.py                  (modify)
backend/src/property_focus/domain/triggers.py                    (modify)
backend/src/property_focus/application/evaluation.py             (modify)
frontend/src/pages/Valuation.tsx                                 (create)
frontend/src/components/FitDimensions.tsx                        (modify)
frontend/src/app/router.tsx                                      (modify)
backend/tests/unit/test_finance.py                               (modify)
backend/tests/unit/test_evaluation.py                            (modify)
backend/tests/unit/test_triggers.py                              (modify)
frontend/tests/evaluation.test.tsx                               (modify)
```

**Sub-tasks:**

- [ ] **7.3.1** — Implement the value score: `raw = 100 * P(V >= list_price)` interpolated from the quantiles; `confidence_factor = clamp(1 - ((p90 - p10) / p50) / MAX_WIDTH_RATIO, 0, 1)` with `MAX_WIDTH_RATIO = 0.5`; `score_value = 50 + (raw - 50) * confidence_factor`; `value` is null without a valuation.
- [ ] **7.3.2** — Add `scoring_v2` (lifestyle 0.5, quality 0.25, value 0.25); `owner_occupancy` subscriptions use `scoring_v2`; subscription dimension weights override.
- [ ] **7.3.3** — Add the anomaly rule "list price below valuation p10" to `deep_trigger_v1`.
- [ ] **7.3.4** — Build `Valuation.tsx` (distribution, confidence, comps with reasons) and show the value dimension in `FitDimensions.tsx`.
- [ ] **7.3.5** — Tests: two valuations with equal p50 above list and different widths give different scores, the narrower higher; `scoring_v2` overall; p10 anomaly trigger; front-end renders value with its confidence.

**Validation:**

- [ ] **Build:** `BE-CHECK`, `GEN` and `FE` exit 0.
- [ ] **Tests:** `BE-UNIT` and front-end tests pass the cases in 7.3.5.
- [ ] **Requirements:**
    - [ ] "Value dimension: `raw = 100 * P(V >= list_price)` … `score_value = 50 + (raw - 50) * confidence_factor`. Two valuations with equal p50 above list price and different widths yield different value scores, the narrower one higher (`test_finance.py`)."
    - [ ] "`scoring_v2` (phase 7) adds value with lifestyle 0.5, quality 0.25, value 0.25. … `owner_occupancy` subscriptions stay on `scoring_v2`."
    - [ ] Anomaly rule: "from phase 7, list price below valuation p10".
    - [ ] Key scenario: "Two valuations, equal p50 above list, different widths | Narrower interval gives the higher value score".
- [ ] **Verify:** Open `/valuation` for a seeded property under `make dev`; comps and their reasons are listed.

---

### Task 7.4: Valuation Backtest and Holdout Evaluation

Backtest comp selectors and valuation models on time-separated held-out sales with frozen thresholds.

**Modules:** `backend`, `specs`
**Depends on:** 7.3, CP-7.A
**Decisions:** D45, D39, D40

**Files:**

```
backend/src/property_focus/application/harness.py                (modify)
backend/evals/config/baseline.yaml                               (modify)
specs/property-focus/evaluation-policy.md                        (modify)
backend/tests/unit/test_harness.py                               (modify)
backend/tests/unit/test_finance.py                               (modify)
```

**Sub-tasks:**

- [ ] **7.4.1** — Register comp selector and valuation model versions with the harness; run them on time-separated held-out sales with frozen as-of times.
- [ ] **7.4.2** — Report median absolute percentage error, bias and empirical p10–p90 coverage, including sparse segments.
- [ ] **7.4.3** — Read release thresholds from `evaluation-policy.md`; refuse promotion when thresholds are missing (CP-7.B); keep valuation abstaining in published evaluations until the gate passes.
- [ ] **7.4.4** — Tests: leakage guard (no sale after as-of in any fold); metrics on a synthetic fixture; promotion refused without thresholds.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0; `EVAL` exits 0.
- [ ] **Tests:** `BE-UNIT` passes the cases in 7.4.4.
- [ ] **Requirements:**
    - [ ] "Backtest on time-separated held-out sales; report median absolute percentage error, bias and empirical p10-p90 coverage, including sparse segments. Store release thresholds in `evaluation-policy.md` before promotion; do not pick them after seeing candidate performance."
    - [ ] Risks: "Baseline and holdout design; abstain until release gate passes."
    - [ ] Key scenario: "Sparse comps or future-dated sale | … no leaked future sale in backtest".
- [ ] **Verify:** The backtest report in `backend/evals/reports/` includes MdAPE, bias and coverage per segment.

---

### Task 7.5: Valuation Ready Gate

Collect the Valuation ready gate artifacts.

**Modules:** `backend`, `specs`
**Depends on:** 7.1–7.4, CP-7.B
**Decisions:** D38, D39, D40

**Files:**

```
specs/property-focus/gates/valuation-ready.md                    (create)
```

**Sub-tasks:**

- [ ] **7.5.1** — Record frozen thresholds, the holdout report, the comp explanation audit (every valuation lists comps with reasons) and abstention behavior.
- [ ] **7.5.2** — Record calibrated interval checks, or explicit abstention when calibration fails.

**Validation:**

- [ ] **Build:** `VERIFY` and `EVAL` exit 0.
- [ ] **Tests:** All suites pass.
- [ ] **Requirements:**
    - [ ] Phase 7 exit: "Backtest report and calibrated interval checks or explicit abstention."
    - [ ] Valuation ready artifacts: "Frozen thresholds, holdout report, comp explanation audit, abstention behavior."
- [ ] **Verify:** `valuation-ready.md` links every artifact.

---

## Phase 8: Investor Experience and Offer Strategy

**Goal:** Investor onboarding and feed, rent-roll intake, unit-level occupancy, operating expenses, financing, cash flow, the Investment dimension and `scoring_v3` ranking, offer bids and walk-away threshold, offer outcomes, and the live-in scenario in Homebuyer. **Deliverable:** the Investor and offer ready gate.

> **Checkpoint CP-8.A (blocking for Task 8.4) — Investor ranking metrics and weights.** Owner: user, before phase 8. No register decision fixes them. The plan recommends an Investment dimension over `underwriting.cash_flow`, `underwriting.cap_rate` and `underwriting.dscr` with the `scoring_v3` weights. Do not implement Task 8.4 until the user confirms or changes this recommendation.
>
> **Checkpoint CP-8.B (blocking for evaluation data sourcing in Task 8.7) — Investor launch size range.** Owner: user. One to four units is the proposed starting boundary; five-plus-unit support is undecided. Confirm before sourcing evaluation data.

### Task 8.1: Ontology v1.1.0 (Units, Leases, Expenses)

Publish the additive minor ontology version with unit, lease and expense attributes and investor preference definitions.

**Modules:** `ontology`, `backend`
**Depends on:** 7.5
**Decisions:** D32, D33, D34

**Files:**

```
ontology/v1.1.0/attributes.json                          (create)
ontology/v1.1.0/preference-definitions.json              (create)
ontology/CHANGELOG.md                                    (modify)
backend/tests/unit/test_ontology.py                      (modify)
README.md                                                (modify)
```

**Sub-tasks:**

- [ ] **8.1.1** — Write `v1.1.0/attributes.json`: all v1.0.0 attributes plus `unit.label`, `unit.bedrooms`, `unit.bathrooms`, `unit.occupancy_status` (`occupied`, `vacant`, `owner_occupied`), `unit.lease_start`, `unit.lease_end`, `unit.available_at_closing` (boolean; no supporting claim resolves to `UNKNOWN`), `unit.contractual_rent`, `unit.collected_rent`, `unit.projected_rent` (amount, currency, rent period), `expense.operating_expense` (category, amount, currency, period), `expense.property_tax`, `expense.insurance`.
- [ ] **8.1.2** — Write `v1.1.0/preference-definitions.json` with additive investor definitions.
- [ ] **8.1.3** — Record the additive compatibility in `CHANGELOG.md`; `underwriting.*` keys are criterion attributes computed per subscription, not claims, and are not seeded as attributes.
- [ ] **8.1.4** — Tests: v1.1.0 loads and publishes; v1.0.0 retires; existing claims keep their ontology version.

**Validation:**

- [ ] **Build:** `cli ontology load ontology/v1.1.0` and `cli ontology publish 1.1.0` exit 0.
- [ ] **Tests:** `BE-UNIT` passes the cases in 8.1.4.
- [ ] **Requirements:**
    - [ ] "Phase 8 publishes ontology v1.1.0, an additive minor version with unit, lease and expense keys: … `underwriting.net_operating_income`, `underwriting.cash_flow`, `underwriting.cap_rate` and `underwriting.dscr` are criterion attributes computed per subscription by `domain/underwriting.py` and stored with `property_evaluations`; they are not claims."
    - [ ] "A new ontology version marks results eligible for recomputation without forcing a backfill."
- [ ] **Verify:** After publish, `select state from ontology_versions` shows `1.0.0 RETIRED` and `1.1.0 ACTIVE`.

---

### Task 8.2: Rent Roll, Lease and Expense Document Intake

Accept permitted documents as private evidence, extract dated unit and expense claims, and keep rent bases distinct.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 8.1
**Decisions:** D2, D35, D44, D14, D4

**Files:**

```
backend/src/property_focus/analyzers/documents.py                (create)
backend/src/property_focus/analyzers/prompts/documents-v1.md     (create)
backend/src/property_focus/application/ingestion.py              (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_analyzers.py                             (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **8.2.1** — Add `POST /v1/properties/{id}/documents` (`multipart/form-data`: one `file` of `application/pdf`, `text/csv`, xlsx, `image/jpeg` or `image/png`, max 20 MiB; `document_type` `rent_roll`/`lease`/`expense_statement`; optional `unit_id`; `Idempotency-Key`): `202 {evidence_id, workflow_run_id, status_url}`; `413` oversize; `415` unsupported type; a repeated digest returns the existing evidence.
- [ ] **8.2.2** — Store the file as evidence private to the uploading account before extraction and write a `DOCUMENT` source observation; a lease or rent roll never creates a sale listing.
- [ ] **8.2.3** — Implement the documents analyzer with a versioned prompt: dated unit claims for occupancy, lease end, contractual rent and rent period; collected and projected rent as distinct claim types; operating expenses with subject, period and source. The completed run's result references list the created claim IDs.
- [ ] **8.2.4** — Tests: each status code; repeated digest; another account gets `404` for the evidence; collected and projected rent stay distinct; a rent roll unit label creates no listing.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 8.2.4.
- [ ] **Requirements:**
    - [ ] "In phase 8, accept a permitted rent roll, lease or expense statement through `POST /properties/{id}/documents` as immutable evidence and create dated unit claims for occupancy, lease end, contractual rent and rent period. Keep collected and projected rent as distinct claim types. Store operating expenses with subject, period and source. A buyer assumption remains private to its scenario."
    - [ ] API row `POST /properties/{id}/documents`: "stores evidence private to the uploading account before extraction; the completed run's result references list the created claim IDs; a repeated digest returns the existing evidence; `413` oversize, `415` unsupported type".
    - [ ] "The email parser, TheMLS browser, manual add and document upload each write `source_observations` and never insert `properties` directly" (document part); "A lease or rent roll cannot create another sale listing."
- [ ] **Verify:** Upload the same CSV twice with different idempotency keys; both responses return the same `evidence_id`.

---

### Task 8.3: Underwriting

Compute effective rent, NOI, debt service, reserves and cash flow deterministically, and evaluate underwriting criteria and unit availability.

**Modules:** `backend`
**Depends on:** 8.2
**Decisions:** D9, D41, D53

**Files:**

```
backend/src/property_focus/domain/underwriting.py                (create)
backend/src/property_focus/domain/evaluation.py                  (modify)
backend/src/property_focus/application/evaluation.py             (modify)
backend/tests/unit/test_underwriting.py                          (create)
backend/tests/unit/test_evaluation.py                            (modify)
```

**Sub-tasks:**

- [ ] **8.3.1** — Implement `domain/underwriting.py` with integer minor units: for a common period and currency, compute effective rental income from included units under a stated rent basis and an explicit vacancy assumption applied once; subtract operating expenses for NOI; subtract debt service and separately identified reserves for cash flow; compute cap rate (annual NOI / list price) and DSCR (annual NOI / annual debt service).
- [ ] **8.3.2** — A live-in scenario excludes rent for the selected owner unit and keeps whole-property costs. Incomplete required inputs return null with missing-field reasons; an explicit zero is allowed.
- [ ] **8.3.3** — For `rental_hold` and `live_in_and_rent` subscriptions, call underwriting from `application/evaluation.py` with the subscription version's budget and financing assumptions; criteria on `underwriting.net_operating_income` and `underwriting.cash_flow` evaluate like attributes; a null output yields `unknown`; store the underwriting version and digest on `property_evaluations` (null for `owner_occupancy`).
- [ ] **8.3.4** — Implement the `unit.available_at_closing` hard criterion: pass when at least one unit is `true`, fail when every unit is `false`, `unknown` otherwise.
- [ ] **8.3.5** — Tests in `test_underwriting.py`: the duplex scenario ($1,500/$1,200 rent, zero vacancy, $700 expenses, $800 debt service, $100 reserves gives $1,100/month rental hold and −$400/month when occupying A, both on the same valuation revision); missing rent for B gives unknown contractual-basis cash flow naming B, and a separate projection scenario labels its assumption; vacancy applied once; explicit zero. In `test_evaluation.py`: identical inputs give identical results; availability unknown puts the home in the `unknown` group.

**Validation:**

- [ ] **Build:** `BE-CHECK` exits 0.
- [ ] **Tests:** `BE-UNIT` passes the cases in 8.3.5.
- [ ] **Requirements:**
    - [ ] "For a common period and currency, compute effective rental income from included units under a stated rent basis and explicit vacancy assumption. Apply vacancy once. Subtract operating expenses to obtain net operating income, then debt service and separately identified reserves for modeled cash flow. A live-in scenario excludes rent for the selected owner unit but keeps whole-property costs. Return null and missing-field reasons for incomplete required inputs. Allow an explicit zero."
    - [ ] "From phase 8, for `rental_hold` and `live_in_and_rent` subscriptions, `application/evaluation.py` calls `domain/underwriting.py` … a null underwriting output yields `unknown`. `property_evaluations` stores the underwriting version and digest, and `test_evaluation.py` asserts identical results for identical inputs."
    - [ ] "A `live_in_and_rent` hard criterion on `unit.available_at_closing` passes when at least one unit is `true`, fails when every unit is `false`, and is `unknown` otherwise. Unknown homes appear in the `unknown` requirements group of the feed, never among confirmed matches."
    - [ ] Key scenarios: duplex $1,500/$1,200 cash flow; "Missing rent for B, only an agent projection available"; live-in duplex requiring a unit available at closing with no supporting claim.
- [ ] **Verify:** `grep -n "float" backend/src/property_focus/domain/underwriting.py` returns nothing.

---

### Task 8.4: Investment Dimension and `scoring_v3`

Score income strategies against soft investment criteria and rank Investor and income-strategy feeds by `scoring_v3`.

> **Blocked:** until CP-8.A is answered.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 8.3, CP-8.A
**Decisions:** D9, A1

**Files:**

```
backend/src/property_focus/domain/evaluation.py                  (modify)
backend/src/property_focus/domain/subscriptions.py               (modify)
backend/src/property_focus/application/evaluation.py             (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_evaluation.py                            (modify)
backend/tests/unit/test_subscriptions.py                         (modify)
backend/tests/contract/test_api.py                               (modify)
```

**Sub-tasks:**

- [ ] **8.4.1** — Validate soft investment criteria on subscription versions: metric in {`underwriting.cash_flow`, `underwriting.cap_rate`, `underwriting.dscr`}, `floor`, `target`, weight `w_c >= 0`; `floor >= target` returns `422`.
- [ ] **8.4.2** — Compute `u_c = clamp((x_c - floor_c) / (target_c - floor_c), 0, 1)` for known `x_c` (null contributes 0); `score_investment = 100 * sum(w_c * u_c over known) / sum(w_c over all)`; `coverage_investment = sum(w_c over known) / sum(w_c over all)`; no soft investment criteria gives `investment: null`.
- [ ] **8.4.3** — Add `scoring_v3`: `rental_hold` investment 0.5, value 0.3, quality 0.2 (lifestyle null); `live_in_and_rent` lifestyle 0.3, investment 0.3, value 0.2, quality 0.2; `CRITICAL_PENALTY = 15`; subscription dimension weights override; a null dimension leaves the denominator.
- [ ] **8.4.4** — Order Investor and Homebuyer feeds for `rental_hold` and `live_in_and_rent` subscriptions by requirements group, then `scoring_v3` overall descending, then `id`.
- [ ] **8.4.5** — Tests: the two Key-scenario investment cases (score 45 with coverage 1.0; score ≈16.7 with coverage 1/3 and requirements unaffected); feed ordering 72 before 58 with `id` tie-break; `floor >= target` gives `422`; explanation text cannot change investment numbers.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 8.4.5.
- [ ] **Requirements:**
    - [ ] "Investment dimension (phase 8, `rental_hold` and `live_in_and_rent` only; plan recommendation): each soft investment criterion c … a criterion with `floor >= target` returns `422`. `u_c = …` … `score_investment = …`; `coverage_investment = …`. A subscription with no soft investment criteria has `investment: null`. Code computes every number; `analyzers/explanation.py` only explains it."
    - [ ] "`scoring_v3` (phase 8, income strategies; plan recommendation): `rental_hold` uses investment 0.5, value 0.3, property quality 0.2 (lifestyle null); `live_in_and_rent` uses lifestyle 0.3, investment 0.3, value 0.2, property quality 0.2; `CRITICAL_PENALTY = 15`. … `test_evaluation.py` asserts the duplex investment scenarios under Key scenarios."
    - [ ] "For `rental_hold` and `live_in_and_rent` subscriptions from phase 8, the Investor and Homebuyer feeds order by requirements group, then `scoring_v3` overall descending, then `id`."
    - [ ] "An unknown cash flow counts as zero and lowers coverage; it never counts as a failed target."
    - [ ] Key scenarios: rental hold soft criteria cash flow and DSCR; added cap-rate criterion with unknown debt service; Investor feed 72 and 58.
- [ ] **Verify:** `uv run pytest backend/tests/unit/test_evaluation.py -q -k investment` passes.

---

### Task 8.5: Offer Scenarios and Outcomes

Compute private, reproducible offer scenarios with four bids and binding constraints, record outcomes, and add the phase 8 MCP tools.

**Modules:** `backend`, `frontend` (generated client)
**Depends on:** 8.3
**Decisions:** D41, A1, D29, D40

**Files:**

```
backend/src/property_focus/domain/offers.py                      (create)
backend/src/property_focus/application/finance.py                (modify)
backend/src/property_focus/adapters/db/models.py                 (modify)
backend/src/property_focus/adapters/rest/routes.py               (modify)
backend/src/property_focus/adapters/rest/schemas.py              (modify)
backend/src/property_focus/adapters/mcp/server.py                (modify)
backend/migrations/versions/0025_offers.py                       (create)
frontend/src/api/openapi.json                                    (modify)
frontend/src/api/generated.ts                                    (modify)
backend/tests/unit/test_finance.py                               (modify)
backend/tests/contract/test_api.py                               (modify)
backend/tests/contract/test_mcp.py                               (modify)
backend/tests/integration/test_tenancy.py                        (modify)
```

**Sub-tasks:**

- [ ] **8.5.1** — Migration `0025`: `offer_scenarios` and `offer_outcomes` (the plan's columns; private RLS to the creating account).
- [ ] **8.5.2** — Implement `domain/offers.py`: `walk_away = min(confirmed hard financial limit, underwriting price ceiling for income strategies, p50 * (1 + premium_tolerance))`; `value_bid = min(p50, walk_away)`; `competitive_bid = min(q(0.65 + 0.10 * high_competition), walk_away)`; `win_focused_bid = min(p90, walk_away)`; enforce `value_bid <= competitive_bid <= win_focused_bid <= walk_away`; never exceed a confirmed hard limit; output each binding constraint and missing inputs; no seller acceptance probability.
- [ ] **8.5.3** — Add `POST /v1/offer-scenarios` (caller must be a member of the subscription; subscription and valuation revisions, owner unit for live-in, rent basis, `expense_claim_ids`, optional private `expense_overrides`, financing, reserves, premium tolerance; `201` immutable scenario) and `GET /v1/offer-scenarios/{id}`. Results include strategy, currency, period, included units, rent basis, effective rent, operating expenses, NOI, debt service, reserves, cash flow, the four bids and missing inputs; each expense line is labelled `claim` or `buyer_assumption`; computed subtotals appear whenever their inputs exist.
- [ ] **8.5.4** — Add `POST /v1/offer-scenarios/{id}/outcomes` (`{offered_amount, result, final_sale_price?}`, `201`, private); outcomes enter the evaluation dataset only with the account's consent.
- [ ] **8.5.5** — Add MCP tools `create_offer_scenario` (write) and `get_offer_scenario` with parity cases.
- [ ] **8.5.6** — Tests: hard limit below p50 puts all four outputs at or below the limit and lists it as binding; bid ordering holds for random valid inputs; identical frozen inputs give identical outputs; another account (including a fellow search member) gets `404` for a scenario and its outcomes.

**Validation:**

- [ ] **Build:** `BE-CHECK` and `GEN` exit 0.
- [ ] **Tests:** `BE-UNIT` and `BE-INT` pass the cases in 8.5.6.
- [ ] **Requirements:**
    - [ ] "Offer scenarios: `walk_away = …`; `value_bid = …`; `competitive_bid = …`; `win_focused_bid = …` … Bids satisfy `value_bid <= competitive_bid <= win_focused_bid <= walk_away` and never exceed a confirmed hard limit. Output each binding constraint."
    - [ ] "Recalculate offer scenarios from frozen buyer inputs, valuation revision, financing assumptions and repair reserves; identical inputs give identical outputs."
    - [ ] "`POST /offer-scenarios/{id}/outcomes` records offered amount, result and final sale price when known; outcomes stay private to the creating account and appear in the evaluation dataset only with that account's consent."
    - [ ] "Omit seller acceptance probabilities unless a separately evaluated probability model supports them."
    - [ ] "Account B's … offer scenarios and offer outcomes return `404` to every other account, including members of B's searches."
    - [ ] MCP tool list: "`create_offer_scenario` and `get_offer_scenario` (phase 8)"; "Offer scenario results, through REST and the phase 8 MCP tools, include strategy, currency, period, included units, rent basis, effective rent, operating expenses, net operating income, debt service, reserves, cash flow, the four bid outputs and missing inputs. Return computed subtotals for which all inputs are available."
    - [ ] Key scenario: "Offer scenario with hard limit below p50 | All four outputs at or below the limit; limit listed as binding".
- [ ] **Verify:** `grep -rniE "accept(ance)?_prob" backend/src` returns nothing.

---

### Task 8.6: Investor UI and Homebuyer Live-In Scenario

Build the Investor route group and expose the live-in scenario in Homebuyer, with the product E2E suite.

**Modules:** `frontend`
**Depends on:** 8.4, 8.5
**Decisions:** D10, D41, D17, A1

**Files:**

```
frontend/src/products/investor/Layout.tsx                (create)
frontend/src/products/investor/Onboarding.tsx            (create)
frontend/src/products/investor/AcquisitionFeed.tsx       (create)
frontend/src/products/investor/PropertyDetail.tsx        (create)
frontend/src/pages/OfferScenarios.tsx                    (create)
frontend/src/products/homebuyer/PropertyDetail.tsx       (modify)
frontend/src/app/router.tsx                              (modify)
frontend/e2e/products.spec.ts                            (create)
frontend/tests/investor.test.tsx                         (create)
```

**Sub-tasks:**

- [ ] **8.6.1** — Build `/investor` `Layout.tsx` and `Onboarding.tsx` (strategy, budget, financing assumptions, income criteria).
- [ ] **8.6.2** — Build `AcquisitionFeed.tsx` (investment criteria, incomplete estimates shown as unknown, `scoring_v3` order).
- [ ] **8.6.3** — Build Investor `PropertyDetail.tsx`: the shared D10 sections plus units, occupancy, documented rents, rent roll, expense evidence, projected cash flow and scenario comparison with binding constraints; every notification deep link resolves to the Investor equivalent.
- [ ] **8.6.4** — Build `OfferScenarios.tsx` (private inputs, bids, walk-away, outcomes) and expose the live-in scenario on the Homebuyer property page.
- [ ] **8.6.5** — Write `products.spec.ts`: one account creates two subscriptions with distinct strategy criteria (editing one leaves the other's evaluations intact) and switches a duplex between strategies; `investor.test.tsx` covers missing inputs labelled before totals.

**Validation:**

- [ ] **Build:** `FE` exits 0.
- [ ] **Tests:** front-end unit tests and `E2E` (including `products.spec.ts`) pass.
- [ ] **Requirements:**
    - [ ] "Phase 8 ships equivalent Investor entry points under `/investor` with acquisition criteria, rent roll and cash-flow comparison. Both call the same generated client and application use cases."
    - [ ] "Under one sign-in, create two subscriptions with distinct strategy criteria. Editing one subscription leaves the other's evaluations intact." (`frontend/e2e/products.spec.ts`: "Same-account subscriptions and duplex strategy switch")
    - [ ] "Expose the live-in scenario in Homebuyer too."
    - [ ] "Label each basis and show missing inputs before presenting a total."
    - [ ] "The Investor page adds income and scenarios."
- [ ] **Verify:** `make test-e2e` output lists `products.spec.ts` as passed.

---

### Task 8.7: Investor and Offer Ready Gate

Collect the Investor and offer ready gate artifacts.

**Modules:** all, `specs`
**Depends on:** 8.1–8.6, CP-8.B
**Decisions:** D9, D41, A1

**Files:**

```
specs/property-focus/gates/investor-offer-ready.md       (create)
```

**Sub-tasks:**

- [ ] **8.7.1** — Record the same-account two-subscription E2E, the duplex comparison under both strategies, and Investment and `scoring_v3` ordering tests.
- [ ] **8.7.2** — Record missing-rent checks, bid ordering tests, reproducible scenarios, phase 8 MCP parity and the privacy report (scenario, outcome and document isolation tests).

**Validation:**

- [ ] **Build:** `VERIFY` exits 0.
- [ ] **Tests:** `E2E` and all backend suites pass.
- [ ] **Requirements:**
    - [ ] Phase 8 exit: "One duplex evaluated under both strategies, Investor feed ordered by `scoring_v3`, plus reproducible scenarios and isolation tests."
    - [ ] Investor and offer ready artifacts: "Same-account two-subscription E2E, duplex comparison, Investment and `scoring_v3` ordering tests, missing-rent checks, bid ordering tests, phase 8 MCP parity and privacy report."
    - [ ] "V1 is phases 0–8 running on DigitalOcean dogfood, including evaluation and shadow experimentation (D45)."
- [ ] **Verify:** `investor-offer-ready.md` links every artifact. **Live (explicit user approval required):** deploy phase 8 to dogfood and run `make smoke-dogfood`.

---

## Phase 9: AWS Release Hardening

**Goal:** CDK deployment of data, API, workflow and observability stacks; the Cognito identity stack deployed and configured as the AWS issuer for REST and MCP (A4); AWS WAF and API Gateway throttling for `/v1` and `/mcp` (Task 9.8, A5); Step Functions and Fargate compatibility; AWS-side shadow runs; operational drills. **Deliverable:** the AWS ready gate.

> **Checkpoint CP-9.A (blocking for Tasks 9.4–9.8 live steps) — AWS beta account.** Owner: user. Provision an isolated AWS beta account, add it to the CDK account allowlist and the `make smoke-aws` beta allowlist, and configure the `beta` GitHub environment's OIDC role. Synthesis and unit tests proceed without it. Merging `deploy-aws.yml` to `main` starts a beta deployment, so merge it only after this answer is recorded. Create the `production` environment (restricted to `main`, with the user as its required reviewer) and its OIDC role only after the AWS ready gate passes; until then the pipeline ends after the beta checks (plan recommendation).

> **Checkpoint CP-9.B (blocking for sub-task 9.7.5) — Cognito OAuth dynamic client registration.** Owner: infrastructure engineer, in phase 9 (moved from phase 0 by A4). Question: does Cognito support OAuth dynamic client registration or Client ID Metadata Documents for MCP clients? Record the research result and, if it does not, the callback-URL allowlist procedure for the identity stack's shared pre-registered MCP client. Tasks 9.1–9.4 and 9.7.1–9.7.4 proceed. Do not build dynamic registration on Cognito before the answer is recorded.

> **Checkpoint CP-9.C (blocking for Task 9.6) — dogfood account carry-over.** Owner: user, before phase 9. Question: must accounts created in dogfood (Keycloak subjects) carry over to AWS (Cognito subjects)? Users bind to issuer and subject, so without a linking step a first Cognito sign-in creates a new account. If the answer is yes, add an operator-approved linking task (for example by verified email) before the gate.

> **Checkpoint CP-9.D (deferred; blocks no task) — re-evaluate Keycloak Client ID Metadata Documents.** Owner: infrastructure engineer, asking the user. Trigger: Keycloak no longer marks `cimd` experimental, or MCP dynamic client registration removal approaches (no earlier than 12 months after 2026-07-28), whichever comes first; check at each Keycloak upgrade and each new MCP revision, in any phase. Background: on 2026-11-02 the user kept `cimd` and `resource-indicators` off (option C) after Keycloak 26.8.1 measurements recorded in the plan (Resolved list, Risks, Codebase findings): the realm-wide `secure-client-uris-pattern` executor rejects every CIMD sign-in and no executor can allow `http` only on loopback for CIMD clients; CIMD accepts only hand-listed trusted domains for client-ID and redirect hosts; `resource-indicators` sets `aud` to the resource URL alone and breaks dynamic registration with `invalid_target`. Notes for the re-evaluation: Keycloak persists CIMD clients as public, consent-required, with `fullScopeAllowed` false, and rejects secret-bearing documents; an `http` client ID needs `cimd-allow-http-scheme`; a host-side test fixture reaches the Keycloak container at `host.docker.internal` with `extra_hosts: host-gateway`; realm placeholders are raw text substitution, so a resource allow-list needs separate `PF_KEYCLOAK_MCP_RESOURCE` and `PF_KEYCLOAK_API_RESOURCE` placeholders; since the phase 0 review (SSRF fix), a second `secure-client-uris-pattern` executor in the realm refuses every URI field Keycloak would fetch or call for an anonymous registration (sector identifier, request URIs, back-channel logout, JWKS, admin URL), and a CIMD client ID is itself a URL Keycloak fetches, so the re-evaluation must reconcile CIMD with that executor as well as with the redirect rule. Question: enable CIMD, under which redirect rule and trusted-domain list, and how do tokens keep `property-focus-api` in `aud` if resource indicators are on? Until the answer is recorded, the realm advertises no `client_id_metadata_document_supported` and no task builds CIMD; a yes adds an implementation task at that point.

### Task 9.1: CDK Data and API Stacks

Define RDS, S3 evidence storage, KMS and backups, and API Gateway with REST and MCP Lambdas.

**Modules:** `infrastructure/cdk`, `backend`, `infrastructure/docker`
**Depends on:** 8.7
**Decisions:** D27, D44, D20, D52, D28

**Files:**

```
infrastructure/cdk/lib/data-stack.ts                     (create)
infrastructure/cdk/lib/api-stack.ts                      (create)
infrastructure/cdk/bin/property-focus.ts                 (modify)
infrastructure/cdk/test/security.test.ts                 (create)
backend/src/property_focus/runtime/lambda_handler.py     (modify)
infrastructure/docker/backend.Dockerfile                 (modify)
```

**Sub-tasks:**

- [ ] **9.1.1** — Implement `data-stack.ts`: RDS Postgres with point-in-time recovery and backups, S3 evidence bucket with versioning, a deny-overwrite/delete policy for runtime roles, Object Lock only on evidence classes that `source-rights.md` permits, KMS encryption, blocked public access.
- [ ] **9.1.2** — Implement `api-stack.ts`: API Gateway with the FastAPI Lambda for `/v1` and the MCP Lambda at `/mcp` (stateless, imports the application layer directly), bounded database pools, scoped IAM; no Fargate for MCP.
- [ ] **9.1.3** — Add REST and MCP Lambda entry points to `lambda_handler.py` and a Lambda stage to `backend.Dockerfile`.
- [ ] **9.1.4** — Write `security.test.ts`: no public access, IAM least privilege, encryption at rest, S3 versioning, deny-overwrite/delete for runtime roles, Object Lock limited to permitted classes.
- [ ] **9.1.5** — Before writing `api-stack.ts`, confirm against current AWS documentation that AWS WAF attaches to API Gateway REST API stages and not to HTTP APIs, and record the result with its date in the coder's report (the orchestrator adds it to the plan's research findings). Build the API as a regional REST API (`AWS::ApiGateway::RestApi`) with Lambda proxy integrations for `/v1` and `/mcp`, so Task 9.8 can attach AWS WAF (plan recommendation, A5). If the research shows REST APIs cannot carry the WAF, stop and report: CloudFront with AWS WAF in front of the API is the fallback, and the user confirms it before work continues.

**Validation:**

- [ ] **Build:** `INFRA` exits 0.
- [ ] **Tests:** `pnpm --filter @property-focus/infra test --run` passes `security.test.ts`.
- [ ] **Requirements:**
    - [ ] "`infrastructure/cdk/test/security.test.ts` asserts S3 versioning, a deny-overwrite/delete policy for runtime roles, and Object Lock only on evidence classes that `source-rights.md` permits. An Object Lock policy must not prevent a required licensed-data deletion without a reviewed policy decision."
    - [ ] "AWS starts with API Gateway and Lambda for FastAPI (D27), Cognito for identity (D28), S3 for evidence, and RDS Postgres with bounded connection pools. The MCP adapter runs as its own Lambda behind the same API Gateway at `/mcp`."
    - [ ] "Use capped database pools and RDS Proxy only if concurrency measurements justify it."
    - [ ] "`infrastructure/cdk/lib/api-stack.ts` builds the API as an API Gateway REST API (regional), because AWS WAF attaches to REST API stages and not to HTTP APIs (plan recommendation). Phase 9 research confirms the constraint against current AWS documentation before the stack is built …"
- [ ] **Verify:** `pnpm --filter @property-focus/infra exec cdk ls` lists data, api and identity stacks.

---

### Task 9.2: CDK Workflow Stack and `analyze.asl.json`

Define Step Functions, task Lambdas, queues, schedules and the Fargate browser task, with workflow assertions.

**Modules:** `infrastructure/cdk`, `backend`
**Depends on:** 9.1
**Decisions:** D21, D26, D22, D23

**Files:**

```
infrastructure/cdk/lib/workflow-stack.ts                 (create)
infrastructure/cdk/workflows/analyze.asl.json            (create)
infrastructure/cdk/test/workflow.test.ts                 (create)
infrastructure/cdk/bin/property-focus.ts                 (modify)
backend/src/property_focus/runtime/launcher.py           (modify)
docs/compatibility.md                                    (modify)
```

**Sub-tasks:**

- [ ] **9.2.1** — Write `analyze.asl.json` with the workflow contract states (5–15 total), parameterized by analysis tier and budget tier; catches inside each Parallel branch so optional enrichment errors do not fail the Parallel state; each execution named after the launch's idempotency key `<workflow run id>.<launch_count>` (`execution_name` in `runtime/launcher.py`; `next_launch()` increments `workflow_runs.launch_count`), so a redelivered launch reuses its name and a relaunch gets a new one; IDs and evidence references only in state; human waits end the execution.
- [ ] **9.2.2** — Implement `workflow-stack.ts`: Step Functions Standard, task Lambdas with timeouts above their bounded runtime, SQS buffers with DLQs, EventBridge Scheduler schedules (`imap-poll` and `outbox-sweep` each minute, `repair` every 5 minutes, `invalidation-batch` hourly) and the Fargate browser task with the encrypted profile stored outside the container.
- [ ] **9.2.3** — Point the AWS launcher paths at these resources.
- [ ] **9.2.4** — Write `workflow.test.ts`: state count 5–15, retry and catch coverage, timeouts, reference-only payloads, and no Temporal dependency in any manifest.
- [ ] **9.2.5** — Distinguish domain replay tests from actual ASL execution tests in `docs/compatibility.md`.

**Validation:**

- [ ] **Build:** `INFRA` exits 0.
- [ ] **Tests:** `pnpm --filter @property-focus/infra test --run` passes `workflow.test.ts`.
- [ ] **Requirements:**
    - [ ] "`infrastructure/cdk/test/workflow.test.ts` asserts `analyze.asl.json` has between 5 and 15 states, and no Temporal dependency exists in any manifest (D21)."
    - [ ] "In ASL, implement independent branches with catches inside each branch; do not fail the whole Parallel state for optional enrichment errors. Human waits persist as database state and terminate the current workflow; resume starts a new bounded execution. Name each execution after the launch's idempotency key, `<workflow run id>.<launch_count>` …"
    - [ ] "Bound Lambda task runtime below its configured timeout; use references rather than HTML/media in workflow state."
    - [ ] "EventBridge Scheduler (AWS) … start[s] `imap-poll` once per minute." (AWS part)
    - [ ] "`docs/compatibility.md` MUST distinguish domain replay tests from actual ASL execution tests."
- [ ] **Verify:** `grep -rni temporal package.json pyproject.toml backend/pyproject.toml frontend/package.json infrastructure/cdk/package.json` returns nothing.

---

### Task 9.3: CDK Observability Stack

Export telemetry to CloudWatch and define dashboards, alarms and budget notifications.

**Modules:** `infrastructure/cdk`, `backend`
**Depends on:** 9.2
**Decisions:** D50, D56

**Files:**

```
infrastructure/cdk/lib/observability-stack.ts            (create)
infrastructure/cdk/bin/property-focus.ts                 (modify)
infrastructure/cdk/test/observability.test.ts            (create)
backend/src/property_focus/adapters/telemetry.py         (modify)
```

**Sub-tasks:**

- [ ] **9.3.1** — Configure CloudWatch export in `telemetry.py` for AWS environments.
- [ ] **9.3.2** — Implement dashboards and alarms for the ten alert signals from Task 5.11 with configurable thresholds, plus budget notifications.
- [ ] **9.3.3** — Write `observability.test.ts`: one alarm per signal and a budget alarm exist.

**Validation:**

- [ ] **Build:** `INFRA` exits 0.
- [ ] **Tests:** `pnpm --filter @property-focus/infra test --run` passes `observability.test.ts`.
- [ ] **Requirements:**
    - [ ] "AWS exports to CloudWatch."
    - [ ] "Alert on oldest queue age, intake lag, credential failure, repeated parser failure, DLQ growth, failed-work count by class, workflow latency, error rate, database saturation and budget exhaustion." (AWS part)
- [ ] **Verify:** `pnpm --filter @property-focus/infra synth` output contains the budget alarm resource.

---

### Task 9.4: AWS Deployment Pipeline and AWS Test Suites

Deploy through a GitHub Actions pipeline that runs on merge to `main`: beta first, the beta checks (the integration tests), then promotion to production only when every check passes and a reviewer approves the `production` environment (user decisions, 2026-11-02 and 2026-11-04). Add the AWS smoke and redelivery suite and a structure test for both deploy workflows.

**Modules:** root tooling, `backend`, `infrastructure/cdk`
**Depends on:** 9.3, CP-9.A (live steps)
**Decisions:** D48, D49, D20

**Files:**

```
.github/workflows/deploy-aws.yml                         (create)
backend/tests/aws/__init__.py                            (create)
backend/tests/aws/test_pipeline.py                       (create)
backend/tests/unit/test_deploy_workflows.py              (create)
Makefile                                                 (modify)
docs/operations.md                                       (modify)
```

**Sub-tasks:**

- [ ] **9.4.1** — Write `deploy-aws.yml`, triggered by `push` to `main`: a build job (image built once, tagged with commit and digest); a `beta` job on the `beta` GitHub environment (`cdk diff` recorded in the run log, CDK stacks deployed, migrations as one controlled task); beta-check jobs; a `production` job on the `production` environment, which waits for its required reviewer's approval, that deploys the same digest and stacks, runs migrations as one controlled task and runs the readiness health check. Each AWS job assumes its environment's scoped role through GitHub OIDC (`permissions: id-token: write`, `role-to-assume`); no AWS access key appears in the workflow or repository secrets. One concurrency group per environment with `cancel-in-progress: false` (plan recommendation). Image rollback without reversing destructive schema changes stays a documented operator step (9.4.4).
- [ ] **9.4.2** — Write `backend/tests/aws/test_pipeline.py`: S3 write and read, SQS redelivery and DLQ, the EventBridge `imap-poll` schedule, Step Functions retries and catches, Lambda auth on REST and MCP, and Fargate browser launch.
- [ ] **9.4.3** — Add `make smoke-aws ENV=beta`: require an explicit beta account allowlist, invoke `uv run pytest backend/tests/aws -q`, never create or delete production data.
- [ ] **9.4.4** — Document the AWS deploy, rollback and expand/contract migration procedure in `docs/operations.md`.
- [ ] **9.4.5** — Promotion gate. The beta-check jobs run the integration tests: the beta readiness health check and `make smoke-aws ENV=beta` (plan recommendation for the list). The `production` job `needs` every beta-check job and carries no `if: always()`, `continue-on-error` or other override, so a failed or cancelled check skips it and production keeps its image, stacks and schema. The `production` environment restricts deployments to `main` and has required reviewers (the user), so the production job waits for approval after the beta checks pass and never deploys without it (user decision, 2026-11-04: no automatic promotion yet). Document the pipeline, the approval step and the failing-check behavior in `docs/operations.md`.
- [ ] **9.4.6** — Write `backend/tests/unit/test_deploy_workflows.py`, which parses both deploy workflows. For `deploy-dogfood.yml`: `workflow_dispatch` is the only trigger and the deploy job uses the `dogfood` environment. For `deploy-aws.yml`: the trigger is `push` to `main`; `production` depends, directly or through `needs`, on `beta` and every beta-check job and runs on the `production` environment; no job on that path uses `if: always()`, `if: failure()`, `continue-on-error` or another status-function override; `production` deploys the digest output of the build job that beta used; both AWS jobs use `id-token: write` and `role-to-assume`; neither workflow names `aws-access-key-id`, `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY`. Negative cases: a workflow fixture whose `production` job lacks the `needs` on a beta check, one whose `production` job has no `environment: production`, one with `continue-on-error` on a check, one with a `push` trigger added to dogfood, and one with a static AWS key each fail.

**Validation:**

- [ ] **Build:** `INFRA` exits 0; workflow syntax check passes.
- [ ] **Tests:** `test_pipeline.py` is collected and skipped without beta credentials; `BE-UNIT` passes `test_deploy_workflows.py`, including the negative cases in 9.4.6.
- [ ] **Requirements:**
    - [ ] "CI scans committed files, `/fixtures` and evidence fixtures for secrets; cloud deployments use GitHub OIDC with scoped roles and protected production environments."
    - [ ] "`deploy-dogfood.yml` has `workflow_dispatch` as its only trigger and runs its deploy job on the protected `dogfood` GitHub environment, whose required reviewers approve each run. Merging to `main` starts no dogfood deployment."
    - [ ] "Phase 9: `deploy-aws.yml` triggers on `push` to `main`. Its jobs run in this order: build the image once …; deploy … to the `beta` environment …; run the beta checks; after a reviewer approves the `production` environment, deploy the same image digest and stacks to the `production` environment."
    - [ ] "The beta checks are the integration tests: the beta readiness health check and `make smoke-aws ENV=beta` (`backend/tests/aws`: …) (plan recommendation for the list)."
    - [ ] "The production job `needs` every beta-check job and has no `if: always()`, `continue-on-error` or other override, so any failed or cancelled beta check skips it and production keeps its previous image, stacks and schema."
    - [ ] "Promotion needs a human approval: the `production` GitHub environment restricts deployments to `main` and has required reviewers (the user), so the production job waits for that approval after the beta checks pass and never deploys without it (user decision, 2026-11-04)."
    - [ ] "Both AWS jobs obtain credentials only through GitHub OIDC (`permissions: id-token: write` and a scoped `role-to-assume` per environment). No workflow or repository secret holds an AWS access key."
    - [ ] "`make smoke-aws` MUST require an explicit beta account allowlist and invoke `uv run pytest backend/tests/aws -q`; it must not create or delete production data."
    - [ ] "Build once, tag with commit and digest, promote the same image, run migrations as one controlled task, then run smoke checks. Roll back application images without reversing destructive schema changes; use expand/contract migrations." (AWS part)
- [ ] **Verify:** `make smoke-aws ENV=production` exits non-zero with an allowlist refusal. `grep -nE 'aws-access-key-id|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY' .github/workflows/*.yml` returns nothing. **Live (explicit user approval required):** `pnpm --filter @property-focus/infra diff --context environment=beta`.

---

### Task 9.7: Cognito Identity on AWS

Deploy the retained CDK identity stack to beta, configure the generic OIDC verifier with the Cognito issuer for the REST and MCP Lambdas, settle MCP client registration on Cognito, and make web sign-out work against Cognito's hosted UI (A4). Numbered 9.7 so existing task numbers stay stable; it runs after Task 9.4 and before Task 9.5.

**Modules:** `infrastructure/cdk`, `backend`, `frontend`, root tooling, `docs`
**Depends on:** 9.1, 9.4; CP-9.A (live steps); CP-9.B (sub-task 9.7.5)
**Decisions:** D28, A3, A4, D49

**Files:**

```
Makefile                                                 (modify)
infrastructure/cdk/cdk.json                              (modify)
infrastructure/cdk/lib/api-stack.ts                      (modify)
infrastructure/cdk/test/identity.test.ts                 (modify)
.github/workflows/deploy-aws.yml                         (modify)
backend/tests/unit/test_auth.py                          (modify)
backend/tests/aws/test_pipeline.py                       (modify)
frontend/src/auth/session.ts                             (modify)
frontend/tests/sign-out.test.tsx                         (modify)
docs/operations.md                                       (modify)
```

**Sub-tasks:**

- [ ] **9.7.1** — Change `make deploy-identity` to accept only `ENV=beta` or `ENV=production` and refuse `dogfood`; remove the `dogfood` entry from the `propertyFocus:environments` context in `cdk.json` and set the beta callback and logout URLs to the beta hostnames; keep `identity.test.ts` passing.
- [ ] **9.7.2** — Add the identity stack to `deploy-aws.yml`: the `beta` job runs `make deploy-identity ENV=beta` after recording a `cdk diff` that lists only identity-stack resources, before the beta checks; the `production` job runs `make deploy-identity ENV=production` under the same promotion gate (9.4.5).
- [ ] **9.7.3** — In `api-stack.ts`, give the REST and MCP Lambdas the Cognito issuer as ordinary configuration (Parameter Store or environment): `PF_OIDC_ISSUER` set to the user-pool issuer, `PF_OIDC_AUDIENCE` set to the web app client ID and `PF_MCP_AUDIENCE` to the MCP app client ID(s) (they must not overlap), `PF_OIDC_AUDIENCE_CLAIM=client_id`, `PF_OIDC_TOKEN_TYPE=token_use=access`. Add no Cognito-specific code.
- [ ] **9.7.4** — Extend `test_auth.py`: under the AWS configuration a Cognito-shaped access token (test key; `client_id` and `token_use=access`) passes, a Cognito ID token (`token_use=id`) fails and a Keycloak-shaped token fails; under the dogfood configuration a Cognito-shaped token fails.
- [ ] **9.7.5** — Apply the CP-9.B result: if Cognito supports dynamic client registration, enable it under rules equivalent to the Keycloak realm's (public clients, PKCE `S256`, HTTPS or loopback redirects); otherwise keep the identity stack's one pre-registered public MCP client and document the callback-URL allowlist procedure in `docs/operations.md`. On AWS, protected-resource metadata names the Cognito issuer.
- [ ] **9.7.6** — Extend `test_pipeline.py` and `make smoke-aws`: sign a synthetic user in through the Cognito hosted UI with PKCE and call one REST endpoint and the MCP endpoint, running the generic MCP client flow when dynamic registration is available.
- [ ] **9.7.7** — Cognito sign-out. Cognito's discovery document lists no `end_session_endpoint`, and its `/logout` expects `client_id` and `logout_uri` instead of `id_token_hint` and `post_logout_redirect_uri`. Set `PF_WEB_OIDC_END_SESSION_URL` to the hosted UI's `/logout` in the AWS web build (`deploy-aws.yml`), and make `auth/session.ts` send `client_id` and `logout_uri` (the post-sign-out URL, registered as a logout URL in 9.7.1) to that endpoint, keeping the session out of its signed-in state before navigating. Extend `sign-out.test.tsx`: under the Cognito build settings sign-out navigates once to `/logout?client_id=<web client ID>&logout_uri=<post-sign-out URL>` and starts no sign-in; under the Keycloak settings the sign-out request is unchanged. Extend `make smoke-aws` to sign the synthetic user out and check that the next visit starts a new sign-in.

**Validation:**

- [ ] **Build:** `INFRA`, `BE-CHECK` and `FE` exit 0.
- [ ] **Tests:** `BE-UNIT` passes the Cognito-shaped cases in 9.7.4; `pnpm --filter @property-focus/web test --run` passes the 9.7.7 cases in `sign-out.test.tsx`; `pnpm --filter @property-focus/infra test --run` passes `identity.test.ts`; `test_pipeline.py` is collected and skipped without beta credentials.
- [ ] **Requirements:**
    - [ ] "`infrastructure/cdk/lib/identity-stack.ts`, built in phase 0, synthesizes in CI and is not deployed before phase 9. In phase 9, `make deploy-identity ENV=beta` deploys only the identity stack (user pool, web app client, MCP app client) to the beta account; `cdk diff` for that command lists no resource outside the identity stack."
    - [ ] "AWS environments configure the same `adapters/auth/oidc.py` verifier with the Cognito user-pool issuer; no Cognito-specific code path exists. … A Keycloak-issued token fails under the AWS configuration and a Cognito-shaped token fails under the dogfood configuration (`test_auth.py`)."
    - [ ] "On AWS, protected-resource metadata names the Cognito issuer. MCP client registration follows the phase 9 research result: dynamic registration if Cognito supports it; otherwise the identity stack's one pre-registered public MCP client and its callback URL allowlist, the only permitted client-specific configuration. `make smoke-aws` signs a synthetic user in through the Cognito hosted UI with PKCE and calls one REST endpoint and the MCP endpoint."
    - [ ] "Web sign-out ends the Cognito session. … sign-out navigates to `/logout?client_id=<web client ID>&logout_uri=<post-sign-out URL>` after the session leaves its signed-in state. `frontend/tests/sign-out.test.tsx` asserts that URL under the Cognito build settings and an unchanged Keycloak sign-out request under the dogfood settings; `make smoke-aws` signs the synthetic user out and a following visit starts a new sign-in."
- [ ] **Verify:** `make deploy-identity ENV=dogfood` exits non-zero with a refusal. `grep -rni cognito backend/src/property_focus` returns nothing. **Live (explicit user approval required):** `make deploy-identity ENV=beta` with its `cdk diff`; `make smoke-aws ENV=beta` with the Cognito sign-in and sign-out.

---

### Task 9.8: AWS WAF and API Gateway Throttling

Protect `/v1` and `/mcp` on AWS with an AWS WAF web ACL and API Gateway stage and route throttling (A5). Numbered 9.8 so existing task numbers stay stable; it runs after Tasks 9.1 and 9.4 and before Task 9.5.

**Modules:** `infrastructure/cdk`, `backend` (AWS tests), `docs`
**Depends on:** 9.1, 9.4; CP-9.A (live steps)
**Decisions:** A5, D27, D48

**Files:**

```
infrastructure/cdk/lib/api-stack.ts                      (modify)
infrastructure/cdk/cdk.json                              (modify)
infrastructure/cdk/test/edge.test.ts                     (create)
backend/tests/aws/test_pipeline.py                       (modify)
docs/operations.md                                       (modify)
```

**Sub-tasks:**

- [ ] **9.8.1** — In `api-stack.ts`, create a regional AWS WAF web ACL with default action allow and associate it with the REST API stage. Add the managed rule groups `AWSManagedRulesCommonRuleSet`, `AWSManagedRulesKnownBadInputsRuleSet` and `AWSManagedRulesAmazonIpReputationList`, with CloudWatch metrics and sampled requests on.
- [ ] **9.8.2** — Add two per-IP rate-based rules answering `429`: one over every path and a tighter one scoped down to paths starting with `/mcp`. Read both rates from CDK context in `cdk.json`: 1000 and 300 requests per 5 minutes (user-confirmed initial limits, 2026-11-02).
- [ ] **9.8.3** — Send WAF logs to CloudWatch Logs with the `Authorization` header redacted.
- [ ] **9.8.4** — Set the stage's default throttling (rate and burst) and a tighter method throttle on the `/mcp` resource, both from CDK context.
- [ ] **9.8.5** — Write `edge.test.ts`: the API is an `AWS::ApiGateway::RestApi` and no `AWS::ApiGatewayV2::Api` exists; a web ACL association targets the API stage; the three managed groups are present; two rate-based rules exist and the `/mcp` one has a scope-down on `/mcp` and the lower limit; logging redacts `authorization`; stage throttling is set and the `/mcp` method throttle is lower.
- [ ] **9.8.6** — Extend `test_pipeline.py` and `make smoke-aws` (live): the beta stage has the web ACL associated; a burst from one client above the `/mcp` limit gets `429`. Document the WAF and throttling values, how to tune them and how to read WAF logs in `docs/operations.md`.

**Validation:**

- [ ] **Build:** `INFRA` exits 0.
- [ ] **Tests:** `pnpm --filter @property-focus/infra test --run` passes `edge.test.ts`; `test_pipeline.py` is collected and skipped without beta credentials.
- [ ] **Requirements:**
    - [ ] "A regional AWS WAF web ACL is associated with the API stage and covers `/v1` and `/mcp`. It holds the AWS managed rule groups … a per-IP rate-based rule over every path, and a tighter per-IP rate-based rule scoped down to `/mcp`, both answering `429` … WAF logs redact the `Authorization` header (`edge.test.ts`)."
    - [ ] "The API stage sets default throttling (rate and burst), and the `/mcp` route has a tighter method throttle, both from CDK context (`edge.test.ts`)."
    - [ ] "`make smoke-aws ENV=beta` asserts the web ACL is associated with the beta stage, and that a burst from one client above the `/mcp` limit gets `429` (live)."
    - [ ] "`edge.test.ts` fails on any `AWS::ApiGatewayV2::Api`."
- [ ] **Verify:** `pnpm --filter @property-focus/infra synth` output for the API stack contains one `AWS::WAFv2::WebACLAssociation`. **Live (explicit user approval required):** `make smoke-aws ENV=beta` with the WAF and burst checks.

---

### Task 9.5: AWS Compatibility, AWS-Side Shadow Runs and Drills

Deploy to isolated beta, exercise the real AWS paths, run shadow evaluation on AWS, and run fault, budget, restore and rollback drills.

**Modules:** all, `docs`
**Depends on:** 9.4, 9.7, 9.8, CP-9.A
**Decisions:** D19, D21, D45, D52, D26, D56, D48

**Files:**

```
docs/compatibility.md                                    (modify)
docs/operations.md                                       (modify)
backend/evals/config/baseline.yaml                       (modify)
```

**Sub-tasks:**

- [ ] **9.5.1** — Deploy CDK stacks into the isolated beta account and run `make smoke-aws ENV=beta` (live; user approval).
- [ ] **9.5.2** — Record each operation's AWS result next to the MiniStack result in `docs/compatibility.md`; an unsupported emulator operation never counts as an AWS pass.
- [ ] **9.5.3** — Run AWS-side shadow evaluation (`cli evaluate --mode shadow --no-publish` against beta) and confirm no publication.
- [ ] **9.5.4** — Run fault injection (task Lambda failure, SQS redelivery, Step Functions catch paths), confirm budget alarms fire, and measure database concurrency to decide on RDS Proxy.
- [ ] **9.5.5** — Demonstrate point-in-time database recovery and restoration of evidence references, encryption access and application configuration; run an image rollback; record all reports in `docs/operations.md`.
- [ ] **9.5.6** — Pipeline drills (live; user approval). Record one merge-triggered `deploy-aws.yml` run that deploys beta and passes the beta checks. Then run the pipeline once with a beta check forced to fail (for example a synthetic failing case that only `backend/tests/aws` reads, selected by an environment variable; plan recommendation) and record that the `production` job was skipped and that production's image digest and stack versions match their values before the run. Before the `production` environment exists (CP-9.A), the drill records that the pipeline ended at the failed check.

**Validation:**

- [ ] **Build:** `VERIFY` exits 0.
- [ ] **Tests:** `make smoke-aws ENV=beta` passes (live).
- [ ] **Requirements:**
    - [ ] "Deploy CDK stacks into an isolated account/environment and exercise S3, SQS redelivery/DLQ, the EventBridge `imap-poll` schedule, Step Functions retries/catches, Lambda auth and Fargate browser launch."
    - [ ] "Before AWS production, the team must demonstrate point-in-time database recovery and restoration of evidence references, encryption access, and application configuration."
    - [ ] "Phase 9 adds AWS-side shadow runs only."
    - [ ] "A drill at the AWS ready gate forces one beta check to fail and records that the production job was skipped and production's image digest did not change."
    - [ ] "Engineers prove service compatibility in real AWS before release" and "Engineers may use Lambda for short, measured browser tasks after a compatibility test, but must not require a browser session to survive a Lambda invocation."
- [ ] **Verify:** `docs/compatibility.md` has a non-empty AWS result for every row. **Live (explicit user approval required):** all of 9.5.1–9.5.6.

---

### Task 9.6: AWS Ready Gate

Collect the AWS ready gate artifacts.

**Modules:** all, `specs`
**Depends on:** 9.1–9.5, 9.7, 9.8, CP-9.C
**Decisions:** D19, D21, D48, D52, A4, A5

**Files:**

```
specs/property-focus/gates/aws-ready.md                  (create)
```

**Sub-tasks:**

- [ ] **9.6.1** — Record the CDK diff review (including the identity stack), the AWS beta smoke with Cognito sign-in for REST and MCP, fault injection and budget alarm results.
- [ ] **9.6.2** — Record the failure/recovery (restore) and rollback reports.
- [ ] **9.6.3** — Record the deployment pipeline evidence: `test_deploy_workflows.py` results, the merge-triggered beta run with passing beta checks, and the 9.5.6 failing-check drill showing production skipped and unchanged.
- [ ] **9.6.4** — Record the edge evidence: `edge.test.ts` results and the beta WAF association and `/mcp` burst check from 9.8.6.

**Validation:**

- [ ] **Build:** `VERIFY` exits 0.
- [ ] **Tests:** All suites pass; live results are linked.
- [ ] **Requirements:**
    - [ ] Phase 9 exit: "AWS beta smoke including Cognito sign-in, WAF and throttling assertions, a pipeline run promoting only after the beta checks pass and the production approval is given, a failing-check drill that leaves production untouched, fault injection, budget alarms, restore and rollback reports."
    - [ ] AWS ready artifacts: "CDK diff review including the identity stack, `edge.test.ts` results and the beta WAF and throttling smoke, `test_deploy_workflows.py` results, a merge-triggered pipeline run that deploys beta and passes the beta checks, the failing-beta-check drill showing production skipped and unchanged, AWS smoke with Cognito sign-in for REST and MCP and Cognito sign-out from the web app, failure/recovery and rollback reports."
- [ ] **Verify:** `aws-ready.md` links every artifact and records the CP-9.C answer. **Live (explicit user approval required):** `pnpm --filter @property-focus/infra diff --context environment=beta`; `make smoke-aws ENV=beta`.

<!-- END OF SPEC -->
