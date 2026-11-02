# Workspace: property-focus

Default branch: main   Remote: robsgreen/property-focus   GitHub: yes

Status: **Phase 0 foundation present on branch `sdlc/property-focus-p0`** (worktree `/home/user/property-focus-worktrees/property-focus-p0`). The main checkout (`/home/user/property-focus`) is still on the planning branch without code; every command below was discovered from the files on the phase branch and runs from the repository root of a checkout that has them. Sources read: root `package.json`, `pnpm-workspace.yaml`, `pyproject.toml`, `backend/pyproject.toml`, `Makefile`, `compose.yaml`, `.env.example`, `.github/workflows/ci.yml`, `.github/workflows/deploy-dogfood.yml`, `frontend/package.json`, `infrastructure/cdk/package.json`, `AGENTS.md`, `.markdownlint-cli2.yaml`.

Toolchain: pnpm 10.28.0 via Corepack (`corepack enable`), Node `>=22.13.0 <23` (`engineStrict`), Python `>=3.13`, uv 0.11.x.

## Capabilities

| Capability | Status |
|------------|--------|
| Install | ✅ `make install` (= `pnpm install --frozen-lockfile` + `uv lock --check` + `uv sync --all-packages --frozen`) |
| Build | ✅ `pnpm --filter @property-focus/web build`; `pnpm --filter @property-focus/infra synth` (both inside `make fe` / `make infra`); backend images via `docker compose --profile app build` |
| Type-check | ✅ `uv run mypy backend/src` (in `make be-check`); `pnpm --filter @property-focus/web typecheck` (in `make fe`); `pnpm --filter @property-focus/infra build` (`tsc --noEmit`, not in any make target or CI job) |
| Lint/Format | ✅ `make be-check` (check-layout, `ruff check backend`, `ruff format --check backend`, mypy, `check_boundaries.py`, `check_fixture_secrets.py`); `pnpm --filter @property-focus/web lint` (eslint, `--max-warnings 0`); `make check-generated`; `pnpm lint:md` (docs/**, specs/**). No frontend/CDK format checker is configured |
| Unit Tests | ✅ `make be-unit` (`uv run pytest backend/tests/unit -q`); `pnpm --filter @property-focus/web test --run`; `pnpm --filter @property-focus/infra test --run` |
| Integration Tests | ✅ `make be-int` (needs Docker + `.env`; see Integration Tests); `make be-replay` (currently skips both steps — no suite/manifest yet) |
| Local Dev | ✅ `make dev` after `docker compose up -d --wait postgres keycloak ministack mailpit otel` (see Local Development) |
| Logging | ✅ stdlib `logging` with JSON-lines handler + redaction in `backend/src/property_focus/adapters/telemetry.py` |
| Metrics | ✅ OpenTelemetry SDK (traces + metrics, OTLP/HTTP to `PF_OTLP_ENDPOINT`) in `adapters/telemetry.py` |

Aggregate: `make verify` = `install be-check be-unit be-int be-replay check-generated fe infra` (full local PR verification; needs Docker and `.env` with Keycloak admin values). AGENTS.md requires it before handing in a change.

CI gate (`.github/workflows/ci.yml`, on PR / push to main / manual): a `changes` job path-filters into jobs `backend` (`make be-check`, `make be-unit`), `integration` (`cp .env.example .env`, `make be-int`), `replay` (`make be-replay`), `generation` (`make check-generated`), `frontend` (`make fe`), `cdk` (`make infra`), `bootstrap` (clean install, compose up, `cli migrate`, ontology load/publish when `ontology/v1.0.0` exists, `cli seed --dataset synthetic`), `markdown` (`pnpm lint:md`); `secret-scan` (`uv run --no-project --python 3.13 python backend/scripts/check_fixture_secrets.py`) runs on every PR. Paths outside the listed dirs select every job.

Not present (planned in earlier notes, no Makefile target exists): `make test-e2e`, `make test-live`, `make restore-check`, `make storage-check`, `make smoke-aws` (`make check-layout` does exist and runs inside `be-check`).

### LIVE commands (need credentials and explicit user approval — never run unprompted)

- `make deploy-identity ENV=dogfood` — `cdk deploy PropertyFocus-Identity-dogfood --exclusively`; needs AWS credentials for the account allowlisted in `infrastructure/cdk/cdk.json`; refuses any ENV but `dogfood`.
- `make smoke-dogfood` — `backend/scripts/smoke_dogfood.py` against the deployed dogfood host; needs `PF_DOGFOOD_APP_HOST`, `PF_DOGFOOD_AUTH_HOST`, `PF_SMOKE_USER_EMAIL`, `PF_SMOKE_USER_PASSWORD`.
- `.github/workflows/deploy-dogfood.yml` — `workflow_dispatch` only, gated by the protected `dogfood` GitHub environment's required reviewers; rsyncs to the DigitalOcean host, runs `infrastructure/dogfood/deploy.sh <sha>`, then `make smoke-dogfood`. Needs `DOGFOOD_SSH_*` vars/secrets, `DOGFOOD_ENV_FILE`, `PF_SMOKE_USER_PASSWORD`.
- `pnpm --filter @property-focus/infra diff` (`cdk diff`) — needs AWS credentials.

## Modules

- `backend` — Python uv workspace member, distribution `property-focus`, package `property_focus` (`backend/src/property_focus`: `domain/` → `application/` → `adapters/` (auth, db, mcp, rest, secrets, telemetry) + `runtime/`, `cli.py`, `config.py`); FastAPI REST + MCP, SQLAlchemy/Alembic (`backend/migrations`), console script `property-focus`. (install: `uv sync --all-packages --frozen`; build: n/a (image: `infrastructure/docker/backend.Dockerfile`); lint/format/type-check: `make be-check`; unit: `make be-unit`; integration/contract: `make be-int` (Docker + Keycloak); replay: `make be-replay`). Tests: `backend/tests/{unit,integration,contract,support}`; no `backend/tests/replay` or `backend/evals/` yet.
- `frontend` — pnpm package `@property-focus/web`: React 19 + TypeScript + Vite, Redux Toolkit / RTK Query client generated from `frontend/src/api/openapi.json`, oidc-client-ts. (build: `pnpm --filter @property-focus/web build`; lint: `pnpm --filter @property-focus/web lint`; type-check: `pnpm --filter @property-focus/web typecheck`; test: `pnpm --filter @property-focus/web test --run` (vitest, jsdom); codegen: `pnpm --filter @property-focus/web generate:api`; dev: `pnpm --filter @property-focus/web dev`; all-in-one: `make fe`). No Playwright e2e suite yet.
- `infrastructure/cdk` — pnpm package `@property-focus/infra`, TypeScript AWS CDK app (`lib/identity-stack.ts`, `lib/deployment-environment.ts`). (type-check: `pnpm --filter @property-focus/infra build`; build: `pnpm --filter @property-focus/infra synth` (no AWS credentials); test: `pnpm --filter @property-focus/infra test --run`; all-in-one: `make infra`; LIVE: `make deploy-identity ENV=dogfood`, `diff`).
- `infrastructure/docker` — `backend.Dockerfile` (targets `task`, `api`, `mcp`, `scheduler`) and `keycloak.Dockerfile`. No commands of their own; built by `docker compose --profile app up -d --build` and by `infrastructure/dogfood/deploy.sh`; backend unit tests check them (CI maps changes here to `cdk` + `backend`).
- `infrastructure/dogfood` — DigitalOcean dogfood runtime: `compose.yaml` override, `Caddyfile`, `otel-collector.yaml`, `scheduler.crontab`, `deploy.sh [--rollback] <commit>` (runs on the host; LIVE). Exercised only by the `deploy-dogfood` workflow and `make smoke-dogfood`.
- `infrastructure/keycloak` — `realm-property-focus.json`, imported by the Compose `keycloak` service on first start; covered by `backend/tests/integration/test_keycloak_realm.py` (CI maps changes here to `cdk`, `integration`, `bootstrap`). Helper: `backend/scripts/keycloak_realm.py`.
- `ontology` — **not yet populated**: only `CHANGELOG.md`; `ontology/v1.0.0/` lands in phase 3. Then: `uv run --package property-focus python -m property_focus.cli ontology load ontology/v1.0.0` and `... ontology publish 1.0.0` (CI `bootstrap` runs them once the directory exists).
- `fixtures` — **partially populated**: `fixtures/synthetic/accounts.json` only; `fixtures/golden/` (manifest for `make be-replay`) not yet present. Scanned by `check_fixture_secrets.py`.
- `specs` — plans and decision records (`specs/property-focus/master-plan.md`, `architecture-decisions.md`, `tasks/`, `gates/`); lint: `pnpm lint:md`.
- `docs` — `docs/compatibility.md` (regenerated by `make compatibility-local`), `docs/operations.md`; lint: `pnpm lint:md`.

Root tooling: `pyproject.toml` (virtual uv workspace root, member `backend`; ruff/mypy strict/pytest config, dev group), `package.json` + `pnpm-workspace.yaml` (workspaces `frontend`, `infrastructure/cdk`; `lint:md` script), `tsconfig.base.json`, `Makefile`, `compose.yaml`, `.env.example`, `.markdownlint-cli2.yaml`, `uv.lock`, `pnpm-lock.yaml`.

## Module Dependencies

```
frontend → backend                 (frontend/src/api/openapi.json exported by backend/scripts/export_openapi.py; make generate-api / check-generated)
infrastructure/docker → backend    (backend.Dockerfile packages backend; bakes infrastructure/dogfood/scheduler.crontab)
infrastructure/dogfood → infrastructure/docker, infrastructure/keycloak, frontend (deploy.sh builds images; Caddy serves frontend/dist)
infrastructure/cdk → (none yet)    (identity stack only; no backend/frontend packaging yet)
backend tests → infrastructure/keycloak, compose services (integration)
backend → fixtures                 (secret scan; replay once fixtures/golden exists)
backend → ontology                 (cli ontology load/publish, once ontology/v1.0.0 exists)
```

No workspace-protocol or path dependencies between the pnpm packages, and the uv workspace has one member.

## Docs Layout

- **User path override:** master plans, decision records, task specs and gates live under `/specs/<slug>/` (git-tracked): `specs/property-focus/master-plan.md`, `specs/property-focus/architecture-decisions.md`, `specs/property-focus/tasks/` (`task-spec.md`, `test-plan.md`), `specs/property-focus/gates/` (`foundation-ready`, `foundation-ready.md`). SDLC lifecycle state (status.md, review.md, events.jsonl, post-mortem.md) still lives in `.sdlc/specs/<name>/` per `sdlc-conventions`.
- `AGENTS.md`: present at the root on the phase branch (layout, backend boundaries, verification, lockfiles, generated files, data/secrets rules). `CLAUDE.md`: absent.
- `docs/`: `compatibility.md`, `operations.md`. `docs/system/` (architecture.md, modules/) and `docs/specs/` retros are absent.
- `README.md`: present.
- Markdown lint: `pnpm lint:md` over `docs/**/*.md` and `specs/**/*.md`.

## Integration Tests

- `make be-int`: `docker compose up -d --wait postgres keycloak ministack mailpit otel`, then `uv run pytest backend/tests/integration backend/tests/contract -q`. Prerequisites: Docker running; `.env` from `cp .env.example .env` containing `PF_KEYCLOAK_ADMIN_USERNAME` / `PF_KEYCLOAK_ADMIN_PASSWORD` (Keycloak refuses to start without them); synthetic mode, no private credentials. Keycloak start can take up to ~2 min (start_period 120s).
- `make be-replay`: `uv run pytest backend/tests/replay -q` and `cli replay --manifest fixtures/golden/manifest.json --offline`; each step currently prints a skip notice (neither exists yet).
- `make compatibility-local`: starts `ministack`, runs `backend/scripts/compatibility_report.py`, regenerates `docs/compatibility.md` (Docker).
- `make check-generated`: regenerates OpenAPI + RTK Query client and fails on diff (no services).
- LIVE, not runnable by the agent: `make smoke-dogfood`, `make deploy-identity ENV=dogfood`, the `deploy-dogfood` workflow (see LIVE commands). `.env.example` mentions opt-in `PF_LIVE=1` live tests via `make test-live`, but no such target exists yet.

## Local Development

```sh
corepack enable
make install
cp .env.example .env          # fill PF_KEYCLOAK_ADMIN_USERNAME / PF_KEYCLOAK_ADMIN_PASSWORD
docker compose up -d --wait postgres keycloak ministack mailpit otel
uv run --package property-focus python -m property_focus.cli migrate
uv run --package property-focus python -m property_focus.cli seed --dataset synthetic
make dev    # backend/scripts/dev.py: API 127.0.0.1:8000, MCP 127.0.0.1:8001, Vite 5173, scheduler; refuses anything but PF_ENVIRONMENT=development
```

Alternative: `docker compose --profile app up -d --build` runs the built images (`migrate`, `api`, `mcp`, `scheduler`). Compose services (all bound to 127.0.0.1): `postgres` 5432, `keycloak` 8080 (admin console `/admin/`, realm `property-focus`), `ministack` 4566 (AWS emulator), `mailpit` SMTP 1025 / UI 8025, `otel` collector 4317/4318 (debug exporter).

## Observability

### Logging
Python stdlib `logging` with a root handler installed by `configure_telemetry` (`backend/src/property_focus/adapters/telemetry.py`) that writes one JSON object per line to stderr, each carrying `trace_id`, `span_id` and `correlation_id` (`bind_correlation_id`). Redaction on every log line and span: values registered from the `SecretProvider` (`register_secret`), sensitive keys (`redact_attribute`), bearer/basic/JWT/cookie/`key=value` secrets in free text (`redact_text`). Covered by `backend/tests/integration/test_telemetry.py`.

### Metrics
OpenTelemetry SDK (`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` 1.45.2): tracer and meter providers export over OTLP/HTTP to `PF_OTLP_ENDPOINT` (unset = nothing exported; Compose sets `http://otel:4318`, host dev uses `http://localhost:4318`). Async hops carry W3C trace context (`current_trace_context` / `resume_trace`). Collectors: Compose `otel` locally; `infrastructure/dogfood/otel-collector.yaml` on dogfood. CloudWatch / CDK observability stack not yet present.
