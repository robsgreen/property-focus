# Repository commands. Each target maps to a command set in the task spec and
# runs from the repository root.

.PHONY: install check-layout be-check be-unit be-int be-replay generate-api check-generated fe \
	infra deploy-identity verify dev smoke-dogfood compatibility-local

# Files generated from the backend's REST schema; never edited by hand.
GENERATED_API := frontend/src/api/openapi.json frontend/src/api/generated.ts

# Loads the repository's .env, when there is one, into commands that read the PF_* environment.
DOTENV := $(if $(wildcard .env),--env-file .env)

# The replay suite and the golden-set manifest that `be-replay` runs once they exist.
REPLAY_TESTS := backend/tests/replay
GOLDEN_MANIFEST := fixtures/golden/manifest.json

# Root directories that D47 replaces with /backend, /frontend, /infrastructure and /fixtures.
FORBIDDEN_ROOT_DIRS := apps packages infra evals

# `uv sync --frozen` installs uv.lock as it is without comparing it to the manifests, so
# `uv lock --check` first fails when a pyproject.toml changed without its uv.lock update.
# `pnpm install --frozen-lockfile` already fails on that drift.
install:
	pnpm install --frozen-lockfile
	uv lock --check
	uv sync --all-packages --frozen

# Fails when a forbidden directory exists in the current directory (the repository root).
check-layout:
	@status=0; \
	for dir in $(FORBIDDEN_ROOT_DIRS); do \
		if [ -d "$$dir" ]; then \
			echo "check-layout: root '$$dir/' is not allowed; use /backend, /frontend, /infrastructure or /fixtures" >&2; \
			status=1; \
		fi; \
	done; \
	exit $$status

be-check: check-layout
	uv run ruff check backend
	uv run ruff format --check backend
	uv run mypy backend/src
	uv run python backend/scripts/check_boundaries.py
	uv run python backend/scripts/check_fixture_secrets.py

be-unit:
	uv run pytest backend/tests/unit -q

# Starts the local Compose services the integration tests use, then runs them.
be-int:
	docker compose up -d --wait postgres keycloak ministack mailpit otel
	uv run pytest backend/tests/integration backend/tests/contract -q

# Replay tests and the offline golden-set replay. Each step is skipped, with a notice, until
# its suite or manifest exists.
be-replay:
	$(if $(wildcard $(REPLAY_TESTS)),uv run pytest $(REPLAY_TESTS) -q,@echo "be-replay: $(REPLAY_TESTS) does not exist yet; skipped")
	$(if $(wildcard $(GOLDEN_MANIFEST)),uv run --package property-focus python -m property_focus.cli replay --manifest $(GOLDEN_MANIFEST) --offline,@echo "be-replay: $(GOLDEN_MANIFEST) does not exist yet; skipped")

# Exports the OpenAPI document from the FastAPI app (no live services) and regenerates the
# RTK Query client from it.
generate-api:
	uv run python backend/scripts/export_openapi.py --out frontend/src/api/openapi.json
	pnpm --filter @property-focus/web generate:api

# Regenerates both files and fails when either differs from its committed (or staged) version.
check-generated: generate-api
	@git ls-files --error-unmatch $(GENERATED_API) >/dev/null
	@git diff --exit-code -- $(GENERATED_API) || { \
		echo "check-generated: commit the regenerated files above (make generate-api)" >&2; \
		exit 1; \
	}

# Web app lint, type check, unit tests and production build.
fe:
	pnpm --filter @property-focus/web lint
	pnpm --filter @property-focus/web typecheck
	pnpm --filter @property-focus/web test --run
	pnpm --filter @property-focus/web build

# CDK type check, assertions and synth; needs no AWS credentials.
infra:
	pnpm --filter @property-focus/infra build
	pnpm --filter @property-focus/infra test --run
	pnpm --filter @property-focus/infra synth

# Deploys only the Cognito identity stack, and only to dogfood. The CDK app also refuses
# credentials for any account other than the one allowlisted in cdk.json.
deploy-identity:
	@if [ "$(ENV)" != "dogfood" ]; then \
		echo "deploy-identity: only ENV=dogfood is supported (got '$(ENV)')" >&2; \
		exit 1; \
	fi
	pnpm --filter @property-focus/infra exec cdk deploy PropertyFocus-Identity-$(ENV) --exclusively --context environment=$(ENV)

# Local pull-request verification: everything CI can run, regardless of the changed paths.
# be-int needs Docker and a .env with the Keycloak admin values.
verify: install be-check be-unit be-int be-replay check-generated fe infra

# Local development stack (backend/scripts/dev.py): API on 127.0.0.1:8000, MCP on
# 127.0.0.1:8001, Vite on 5173 and the scheduler, against the local Keycloak realm. Refuses
# any configuration but PF_ENVIRONMENT=development.
dev:
	uv run $(DOTENV) --package property-focus python backend/scripts/dev.py

# Live (explicit user approval required): checks the deployed dogfood environment from
# outside its host, through Cloudflare (backend/scripts/smoke_dogfood.py). Needs
# PF_DOGFOOD_APP_HOST, PF_DOGFOOD_AUTH_HOST, PF_DOGFOOD_ORIGIN_ADDRESS, PF_SMOKE_USER_EMAIL
# and PF_SMOKE_USER_PASSWORD.
smoke-dogfood:
	uv run --package property-focus python backend/scripts/smoke_dogfood.py

# Exercises each AWS operation the application uses against the pinned MiniStack and
# regenerates docs/compatibility.md.
compatibility-local:
	docker compose up -d --wait ministack
	uv run --package property-focus python backend/scripts/compatibility_report.py
