# Property Focus

A personal real-estate analyst: it ingests MLS listings, builds evidence-backed property
intelligence, and ranks homes against each search's requirements and preferences. The
design lives in `specs/property-focus/` (master plan and architecture decision register).

## Repository layout

| Path | Contents |
|---|---|
| `backend/` | Python 3.13 modular monolith: the `property-focus` distribution (`property_focus` package), scripts and tests |
| `frontend/` | React + TypeScript web app (`@property-focus/web`) |
| `infrastructure/` | TypeScript CDK app (`infrastructure/cdk`, `@property-focus/infra`), images and dogfood deployment files |
| `ontology/` | Versioned property ontology seeds and their changelog |
| `fixtures/` | Synthetic and rights-cleared test fixtures; no credentials or private data |
| `specs/` | Plans, decisions, task specs and release-gate evidence |
| `docs/` | Living system documentation (`docs/system/`), operations runbook and AWS compatibility inventory |

Root `apps/`, `packages/`, `infra/` and `evals/` directories are not allowed; `make check-layout` fails if one exists.

## Prerequisites

- Node.js 22 LTS (22.13 or later) with Corepack (pnpm is pinned in `package.json` as `packageManager`)
- [uv](https://docs.astral.sh/uv/) 0.11 (installs Python 3.13 when needed)
- Docker with the Compose plugin 2.27.2 or later (`docker compose version`; Docker Desktop bundles it), and GNU make. `make install` stops with "Docker Compose >= 2.27.2 is required" on an older one, which would otherwise reject the Compose files' inline `configs` content

## Bootstrap

From the repository root:

```sh
corepack enable
pnpm install --frozen-lockfile
uv sync --all-packages --frozen
cp .env.example .env    # synthetic mode by default; needs no private credentials
docker compose up -d --wait postgres keycloak ministack mailpit otel
uv run --env-file .env --package property-focus python -m property_focus.cli migrate
uv run --env-file .env --package property-focus python -m property_focus.cli seed --dataset synthetic
make dev
```

Both installs read the committed lockfiles (`pnpm-lock.yaml`, `uv.lock`); a manifest
change without a matching lockfile update fails. To change a dependency, edit the
manifest, run `pnpm install` or `uv lock`, review the resolved versions and commit the
lockfile with the manifest.

Keycloak refuses to start without `PF_KEYCLOAK_ADMIN_USERNAME` and
`PF_KEYCLOAK_ADMIN_PASSWORD` in `.env` (replace the template's placeholders with your own
local values); on first start it imports the `property-focus` realm from
`infrastructure/keycloak/`. `migrate` creates the schema as the `pf_migrator` role, and
`seed` adds the synthetic accounts in `fixtures/synthetic/accounts.json` (they cannot sign
in). To sign in to the web app at `http://localhost:5173`, register a user on the local
realm's sign-in page; the verification email lands in Mailpit at `http://localhost:8025`.
The CI `bootstrap` job runs every step before `make dev` from a clean checkout. The ontology load and
publish steps join the bootstrap with the first ontology version (`ontology/v1.0.0`).

## Commands

| Command | What it does |
|---|---|
| `make install` | Checks the Compose version (`make check-compose`), then frozen-lockfile installs for pnpm and uv; fails when a manifest changed without its lockfile (`uv lock --check`) |
| `make check-layout` | Fails on forbidden root directories |
| `make be-check` | Layout check, `ruff check`, `ruff format --check`, `mypy backend/src`, import-boundary check and credential scan |
| `make be-unit` | Backend unit tests (`uv run pytest backend/tests/unit -q`) |
| `make be-int` | Starts the local Compose services (Postgres, Keycloak, MiniStack, Mailpit, OTel) and runs the backend integration tests; needs `.env` with the Keycloak admin values |
| `make be-replay` | Replay tests and the offline golden-set replay; each is skipped until its suite or manifest exists |
| `make generate-api` | Exports `frontend/src/api/openapi.json` from the FastAPI app (no live services needed) and regenerates the RTK Query client `frontend/src/api/generated.ts` |
| `make check-generated` | Runs `make generate-api` and fails when either generated file differs from its committed or staged version |
| `make fe` | Web app lint, type check, unit tests and production build (`@property-focus/web`) |
| `make infra` | CDK type check, assertion tests and synth for `infrastructure/cdk`; needs no AWS credentials |
| `make deploy-identity ENV=dogfood` | Deploys only the Cognito identity stack to the allowlisted dogfood AWS account (live; needs approval and credentials) |
| `make verify` | The full pull-request verification set: everything above except deployment, including `make be-int` (needs Docker and `.env`) |
| `make dev` | API on 127.0.0.1:8000, MCP on 127.0.0.1:8001, the web app on http://localhost:5173 and the scheduler, against the local Keycloak realm, after migrating the local database to the latest schema; refuses any `PF_ENVIRONMENT` but `development`. Needs the local services |
| `docker compose --profile app up -d --build` | The same API, MCP adapter and scheduler as built images (`infrastructure/docker/backend.Dockerfile`), after a one-shot `migrate`; an alternative to `make dev` |
| `make compatibility-local` | Exercises each AWS operation the application uses against the pinned MiniStack and regenerates `docs/compatibility.md` |
| `make smoke-dogfood` | Checks the deployed dogfood environment from outside its host, through Cloudflare (live; needs approval); see `docs/operations.md` |
| `pnpm --filter @property-focus/web dev` | Web app on http://localhost:5173, signing in through the local Keycloak and forwarding `/v1` and `/health` to the API on port 8000 |
| `pnpm lint:md` | Markdown lint for `docs/**` and `specs/**` |

Individual checks:

```sh
uv run ruff check backend
uv run ruff format --check backend
uv run mypy backend/src
uv run python backend/scripts/check_boundaries.py
uv run python backend/scripts/check_fixture_secrets.py
uv run pytest backend/tests/unit -q
```

Pull requests run the same checks in GitHub Actions (`.github/workflows/ci.yml`), selected by
the paths they change; the path filters are listed at the top of that file. A pull request
that changes only `docs/**` or `specs/**` runs just the credential scan and `pnpm lint:md`.

See `AGENTS.md` for module boundaries and contribution rules, and
`docs/system/architecture.md` for how the system works.
