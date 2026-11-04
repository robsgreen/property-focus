# Contributor and agent rules

These rules apply to every change in this repository, by people and by coding agents.
The design sources are `specs/property-focus/master-plan.md` and
`specs/property-focus/architecture-decisions.md`; the decision register wins on conflict.

## What Is This?

Property Focus is a personal real-estate analyst. It will ingest MLS listings, build
evidence-backed intelligence about each **property** (a physical home; a **listing** is one
marketing episode of it), and rank homes against each **search**'s requirements. Each
person signs up for their own **account** (in code, the **tenant**): the boundary for their
private data. Phase 0 built the foundation only: sign-in, tenant provisioning, row-level
tenancy, the workflow-run engine, telemetry, the web shell and the dogfood deployment. No
listing, property or search feature exists yet.

## Backend/Dependencies

- **OIDC issuer**: a Keycloak realm kept as code (`infrastructure/keycloak/`). The backend
  reads only its discovery document and key set; the web app and MCP clients sign in there.
- **PostgreSQL 17**: all relational state, with row-level security per tenant.
- **OTLP collector**: traces and metrics (OpenObserve on dogfood).
- **AWS** (later phases): Secrets Manager and Parameter Store, Lambda, Step Functions, ECS;
  emulated locally by MiniStack. A Cognito identity stack is retained for phase 9.

## Tech Stack

- Backend: Python 3.13, uv workspace, FastAPI, Starlette, MCP Python SDK (`mcp`),
  SQLAlchemy 2, Alembic, psycopg, PyJWT, pydantic, OpenTelemetry, boto3; ruff, mypy, pytest
- Frontend: React 19, React Router 7, Redux Toolkit / RTK Query (OpenAPI codegen),
  oidc-client-ts, Vite, Vitest, ESLint, TypeScript 5.9 (pnpm)
- Infrastructure: Docker Compose 2.27.2 or later (`make install` checks), Keycloak 26.8, Caddy,
  AWS CDK (TypeScript), GitHub Actions

## Architecture at a Glance

Browser and MCP clients send a bearer token to the REST API (`/v1`, port 8000) or the MCP
adapter (`POST /mcp`, port 8001). Both call `oidc.authenticate()`, which verifies the token
and maps issuer and subject to a user and the tenant they own (created on first request).
Use cases run in a `UnitOfWork` scoped to that tenant; RLS enforces it. Background work is
a workflow run, admitted with the change that caused it and carried onward by a
transactional outbox; short-lived `run-task` processes take a fenced lease, run the handler
and commit its result. A cron-style scheduler starts `outbox-sweep` and `repair` to recover
from crashes. Full narrative: [docs/system/architecture.md](docs/system/architecture.md).

## Before Making Changes

Read the doc for the area you touch, then the source.

| Area | Doc |
|---|---|
| `PF_*` settings, secret providers | [configuration-and-secrets](docs/system/modules/configuration-and-secrets.md) |
| Error codes, failure classes, retry policy | [failures-and-retries](docs/system/modules/failures-and-retries.md) |
| Models, migrations, roles, RLS, unit of work | [database-and-tenancy](docs/system/modules/database-and-tenancy.md) |
| Token verification, tenant provisioning, Keycloak realm, Cognito | [identity-and-auth](docs/system/modules/identity-and-auth.md) |
| REST routes, problem responses, OpenAPI export | [rest-api](docs/system/modules/rest-api.md) |
| MCP endpoint, protocol eras, tools | [mcp-adapter](docs/system/modules/mcp-adapter.md) |
| Workflow runs, outbox, task runtime, launchers, scheduler | [workflows-and-tasks](docs/system/modules/workflows-and-tasks.md) |
| Logs, traces, metrics, redaction | [telemetry](docs/system/modules/telemetry.md) |
| `frontend/` | [web-app](docs/system/modules/web-app.md) |
| Compose, images, dogfood, CDK, CI | [infrastructure-and-delivery](docs/system/modules/infrastructure-and-delivery.md) |
| Dogfood runbooks | [docs/operations.md](docs/operations.md) |
| MiniStack versus AWS | [docs/compatibility.md](docs/compatibility.md) (generated) |

## Key Conventions

1. Keep the top-level layout (see Layout below); `make check-layout` fails otherwise.
2. Respect the backend layer boundaries (see Backend boundaries); `check_boundaries.py`
   fails otherwise.
3. Take the tenant from the verified token, never from a path, query or body field.
4. Raise `CodedError` with a registered code and a safe explanation; never put secrets or
   private content in an error, log line or event payload.
5. Read secrets only through the `SecretProvider` port by logical name; settings never
   hold secrets.
6. Admit background work as a workflow run in the same transaction as the change that
   causes it; tasks deduplicate by their run and commit only under a current lease.
7. Never edit generated files; regenerate them (see Generated files).
8. Append migrations; never rewrite a released one, and keep schema changes
   expand/contract.
9. Never push, deploy or run a live command without the user's explicit approval.
10. Keep docs in sync: update `docs/system/` and this file in the same change as the code
    they describe.

## Build & Dev

See `README.md` for bootstrap and the full command table. The common ones:

```sh
make install          # Compose version check, frozen-lockfile installs
make dev              # API :8000, MCP :8001, web :5173, scheduler (development only)
make be-check be-unit # lint, format, types, boundaries, credential scan, unit tests
make be-int           # integration and contract tests (Docker and .env)
make fe               # web app lint, types, tests, build
make verify           # everything CI can run
```

## Directory Map

```
backend/
  src/property_focus/
    config.py              PF_* settings
    cli.py                 property-focus migrate | seed | run-task | serve | schedule
    domain/errors.py       stable error codes, failure classes, retry policy
    application/           ports, execution context, workflow engine
    adapters/auth/         OIDC verifier and authenticate()
    adapters/db/           engine, models, unit of work, rls.sql
    adapters/rest/         FastAPI app, routes, schemas
    adapters/mcp/          MCP server on the MCP Python SDK
    adapters/secrets.py    secret providers
    adapters/telemetry.py  OTel, JSON logs, redaction
    runtime/               run_task, launchers, scheduled kinds, scheduler, Lambda handler
  migrations/              Alembic (versions/NNNN_*.py, one per appended change)
  scripts/                 dev, export_openapi, keycloak_realm, checks, smoke, compatibility
  tests/                   unit, integration, contract, support
frontend/src/              main.tsx, auth/session.ts, app/ (router, store), api/ (generated client)
infrastructure/            cdk/, docker/, dogfood/, keycloak/
fixtures/ ontology/        synthetic accounts; ontology changelog
docs/                      system/ (living docs), operations.md, compatibility.md
specs/                     plans, decisions, task specs, gate evidence
```

## Layout

Keep the top-level layout: `backend/`, `frontend/`, `infrastructure/`, `ontology/`,
`fixtures/`, `specs/`, `docs/`, plus root tooling and `.github/`. Do not create root
`apps/`, `packages/`, `infra/` or `evals/` (`make check-layout` fails). Evaluation
datasets live under `fixtures/`; evaluation configuration and reports under
`backend/evals/`.

## Backend boundaries

The backend is one modular monolith (`backend/src/property_focus`) with these layers:

- `domain/`: pure rules and types. It must not import `adapters`, `runtime`, FastAPI,
  boto3 or Playwright.
- `application/`: use cases and ports (protocols). It may import the database and
  telemetry adapters (`adapters.db`, `adapters.telemetry`) and no other adapter: it
  reaches everything else (secrets, task launchers, later sources and providers) through
  ports, and never imports `adapters.rest`, `adapters.mcp` or `adapters.auth`.
- `adapters/`: REST, MCP, database, storage, sources, AI providers, notifications,
  secrets and telemetry implementations.
- `runtime/`: task entry points and launchers.

`backend/scripts/check_boundaries.py` enforces the import rules. Application code reads
secrets only through the `SecretProvider` port by logical name.

## Verification

Before handing in a change, run from the repository root:

```sh
make verify
```

It runs the frozen-lockfile installs, the layout check, ruff lint and format check, mypy,
the boundary check, the credential scan, the backend unit tests, the integration and
contract tests against the local Compose services (`make be-int`; needs Docker and
`.env`), the replay tests (`make be-replay`), the generated-client check
(`make check-generated`), the web app checks (`make fe`) and the CDK tests. Changes
to Markdown under `docs/` or `specs/` also run `pnpm lint:md`.

## Dependencies and lockfiles

- Use bounded version ranges in `pyproject.toml` and exact versions in `package.json`.
- Commit `uv.lock` and `pnpm-lock.yaml` with every manifest change. Installs use
  `uv sync --all-packages --frozen` and `pnpm install --frozen-lockfile`, so lock drift
  fails.

## Generated files

- Never edit `frontend/src/api/generated.ts` by hand. After any REST schema change,
  export `frontend/src/api/openapi.json` and regenerate the client; `make check-generated`
  fails when the committed schema or client differs from a fresh generation.
- Database changes go in one new appended Alembic migration under
  `backend/migrations/versions/`; never rewrite a released migration.
- `docs/compatibility.md` is written by `make compatibility-local`; never edit it by hand.

## Data and secrets

- Fixtures are synthetic or rights-cleared and redacted. Never commit credentials,
  browser profiles or session state, unredacted private email, licensed source corpora,
  production data or another account's private records.
- `backend/scripts/check_fixture_secrets.py` scans committed files and `fixtures/` for
  credential patterns and fails on a hit.
- `.env` is local and ignored; `.env.example` holds placeholders only.
- Never create the legacy `pvpd` dot-directory anywhere in the repository.
