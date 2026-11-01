# Workspace: property-focus

Default branch: main   Remote: robsgreen/property-focus   GitHub: yes

Status: **greenfield**. The repository contains only `README.md` (title line), `.claude/` (settings enabling the `sdlc` plugin, plus project skills `impeccable` and `taste-skill`), and `specs/property-focus/` (`master-plan.md` draft, `architecture-decisions.md`). No source code, package manifest, lockfile, Makefile, compose file, or CI workflow exists yet.

Everything marked **planned** below comes from `specs/property-focus/master-plan.md` (sections "Scope and repository map" and "Test strategy and exact commands"). Those commands are the plan's verification contract; none can run until the files that define them are created. Re-run workspace setup when the Phase 0 foundation lands (root `pyproject.toml`, `package.json`, `Makefile`, `compose.yaml`, `.github/workflows/ci.yml`) to replace planned entries with discovered ones.

## Capabilities

| Capability | Status |
|------------|--------|
| Install | ❌ Not supported yet — no manifests. Planned: `pnpm install --frozen-lockfile` and `uv sync --all-packages --frozen` (first bootstrap without locks: `pnpm install`, `uv sync --all-packages`, then commit `pnpm-lock.yaml` and `uv.lock`) |
| Build | ❌ Not supported yet. Planned: `pnpm --filter @property-focus/web build`; `pnpm --filter @property-focus/infra synth` |
| Type-check | ❌ Not supported yet. Planned: `uv run mypy backend/src`; `pnpm --filter @property-focus/web typecheck` |
| Lint/Format | ❌ Not supported yet. Planned: `uv run ruff check backend`; `uv run ruff format --check backend`; `pnpm --filter @property-focus/web lint`; `make check-layout`; `uv run python backend/scripts/check_boundaries.py`; `uv run python backend/scripts/check_fixture_secrets.py`; `make check-generated` |
| Unit Tests | ❌ Not supported yet. Planned: `uv run pytest backend/tests/unit -q`; `pnpm --filter @property-focus/web test --run`; `pnpm --filter @property-focus/infra test --run` |
| Integration Tests | ❌ Not supported yet. Planned: `uv run pytest backend/tests/integration backend/tests/contract -q`; `uv run pytest backend/tests/replay -q`; `make test-e2e` (see Integration Tests) |
| Local Dev | ❌ Not supported yet. Planned: `docker compose up -d postgres ministack mailpit otel` then `make dev` |
| Logging | ❌ Not established. Planned: structured JSON logs with redaction via `backend/src/property_focus/adapters/telemetry.py` (D50) |
| Metrics | ❌ Not established. Planned: OpenTelemetry traces/metrics; CloudWatch on AWS, self-hosted OTel stack on dogfood (D50) |

Planned aggregate: `make verify` runs the full pull-request verification set locally; CI (`.github/workflows/ci.yml`, D48) runs a path-filtered subset.

## Modules

All modules are **planned, not yet present**. Per the plan, create each module when its phase begins.

- `backend` — Python 3.13+, uv workspace member, FastAPI REST + MCP adapters, SQLAlchemy/Alembic, distribution `property-focus`, package `property_focus` (`backend/src/property_focus`, layered `domain/` → `application/` → `adapters/`). (build: n/a; lint: `uv run ruff check backend` + `uv run ruff format --check backend`; type-check: `uv run mypy backend/src`; test: `uv run pytest backend/tests/unit -q`, integration/contract/replay as above)
- `frontend` — React + TypeScript + Vite, Redux Toolkit / RTK Query with OpenAPI codegen; pnpm package `@property-focus/web`. (build: `pnpm --filter @property-focus/web build`; lint: `pnpm --filter @property-focus/web lint`; type-check: `pnpm --filter @property-focus/web typecheck`; test: `pnpm --filter @property-focus/web test --run`; e2e: `make test-e2e`)
- `infrastructure/cdk` — TypeScript AWS CDK app; pnpm package `@property-focus/infra`. (build: `pnpm --filter @property-focus/infra synth`; test: `pnpm --filter @property-focus/infra test --run`)
- `infrastructure/docker`, `infrastructure/dogfood` — container images and DigitalOcean dogfood deployment (compose override, scheduler crontab, Caddy, OTel collector, deploy/backup scripts). No build/test commands of their own; exercised by `make smoke-dogfood` and `make storage-check ENV=dogfood`.
- `ontology` — versioned JSON seed data and JSON Schema (`ontology/v1.0.0`, `v1.1.0`). Validated by `uv run --package property-focus python -m property_focus.cli ontology load ontology/v1.0.0`.
- `fixtures` — synthetic mail, recorded pages, golden set and evaluation datasets. Consumed by backend replay tests and `cli replay` / `cli evaluate`.
- `specs`, `docs` — documentation only (see Docs Layout).

Planned root tooling: `pyproject.toml` (uv workspace root, member `backend`), `package.json` + `pnpm-workspace.yaml` (workspaces `frontend`, `infrastructure/cdk`), `tsconfig.base.json`, `Makefile`, `compose.yaml`, `.env.example`, `.github/workflows/*`.

## Module Dependencies

Planned (from the master plan; no manifests exist yet to confirm):

```
frontend → backend          (frontend/src/api/openapi.json exported by backend/scripts/export_openapi.py; make check-generated)
backend → ontology          (cli ontology load/publish reads ontology/<version>)
backend → fixtures          (replay, evaluation and integration tests)
infrastructure → backend    (backend.Dockerfile / browser.Dockerfile package backend; CDK deploys its Lambdas)
infrastructure → frontend   (dogfood Caddy and CDK serve the built web app)
```

## Docs Layout

- **User path override:** master plans and decision records live under `/specs/<slug>/` (git-tracked), e.g. `specs/property-focus/master-plan.md` and `specs/property-focus/architecture-decisions.md`. The plan also places downstream task specs under `specs/property-focus/tasks/` and further spec documents (`source-rights.md`, `evaluation-policy.md`) there. SDLC lifecycle state (status.md, task-spec.md, review.md, events.jsonl, post-mortem.md) still lives in `.sdlc/specs/<name>/` per `sdlc-conventions`.
- `AGENTS.md`: absent (planned at repo root by the master plan). `CLAUDE.md`: absent.
- `docs/`: absent. Planned by the master plan: `docs/compatibility.md`, `docs/operations.md`. `docs/system/` (architecture.md, modules/) and `docs/specs/` retros are not yet present.
- `README.md`: title only; planned to carry bootstrap and commands.

## Integration Tests

NOT SUPPORTED yet — no test suites or compose file exist. Planned (master plan "Test strategy and exact commands", D46 layers):

- Deterministic (every PR, path-filtered): `uv run pytest backend/tests/integration backend/tests/contract -q`. Prerequisites: Docker running and `docker compose up -d postgres ministack mailpit otel`; `.env` from `.env.example` (synthetic mode, no private credentials).
- Replay: `uv run pytest backend/tests/replay -q`; `uv run --package property-focus python -m property_focus.cli replay --manifest fixtures/golden/manifest.json --offline`; `... cli evaluate --config backend/evals/config/baseline.yaml --offline --fail-on-regression`.
- Browser E2E: `make test-e2e` (starts isolated synthetic services, runs Playwright incl. `e2e/accessibility.spec.ts`, cleans up).
- Recovery/compat: `make restore-check`, `make compatibility-local`.
- Live (opt-in/nightly, **not runnable locally by the agent** — needs authorized provider/mailbox credentials, AWS staging, or the dogfood bucket): `make test-live` (requires `PF_LIVE=1`), `make smoke-aws ENV=staging`, `make smoke-dogfood`, `make storage-check ENV=dogfood`, `pnpm --filter @property-focus/infra diff --context environment=staging`.

## Local Development

NOT SUPPORTED yet. Planned bootstrap from repo root:

```sh
corepack enable
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
cp .env.example .env
docker compose up -d postgres ministack mailpit otel
uv run --package property-focus python -m property_focus.cli migrate
uv run --package property-focus python -m property_focus.cli ontology load ontology/v1.0.0
uv run --package property-focus python -m property_focus.cli ontology publish 1.0.0
uv run --package property-focus python -m property_focus.cli seed --dataset synthetic
make dev   # API :8000, MCP :8001, Vite :5173, local scheduler (loopback; development config only)
```

## Observability

### Logging
NOT ESTABLISHED. Planned (D50): structured JSON logs with redaction, configured in `backend/src/property_focus/adapters/telemetry.py`; covered by `backend/tests/integration/test_telemetry.py`.

### Metrics
NOT ESTABLISHED. Planned (D50): OpenTelemetry, one trace linking MLS email → ingestion → workflow → analysis run → model call → claim → notification; CloudWatch export and dashboards/alarms (`infrastructure/cdk/lib/observability-stack.ts`) on AWS; self-hosted OTel collector (`infrastructure/dogfood/otel-collector.yaml`) on dogfood.
