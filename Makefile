# Repository commands. Each target maps to a command set in the task spec and
# runs from the repository root.

.PHONY: install check-compose check-layout be-check be-unit be-int be-replay generate-api \
	check-generated fe infra deploy-identity verify dev smoke-dogfood compatibility-local \
	scan scan-semgrep scan-bandit scan-deps

# This Makefile's directory, so its scripts are found even when it runs with -f from elsewhere.
REPO_ROOT := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

# Files generated from the backend's REST schema; never edited by hand.
GENERATED_API := frontend/src/api/openapi.json frontend/src/api/generated.ts

# Scanner versions for `make scan`. Pinned so a laptop and CI run the same tools.
SEMGREP_VERSION := 1.183.0
BANDIT_VERSION := 1.9.2
PIP_AUDIT_VERSION := 2.9.0
SEMGREP_SARIF := build/semgrep.sarif

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
install: check-compose
	pnpm install --frozen-lockfile
	uv lock --check
	uv sync --all-packages --frozen

# Fails, naming both versions, when the Docker Compose plugin is older than the Compose files
# and scripts need.
check-compose:
	@bash "$(REPO_ROOT)infrastructure/docker/check-compose-version.sh"

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

# Local pull-request verification: the path-aware CI jobs, regardless of the changed paths.
# be-int needs Docker and a .env with the Keycloak admin values. Security scans are
# separate (`make scan`): the rule packs are large, and CI reports them without failing
# the workflow until SCAN_ENFORCE is true.
verify: install be-check be-unit be-int be-replay check-generated fe infra

# Semgrep, Bandit and the dependency audits. Exits non-zero when a finding remains.
# Needs uv, pnpm and network. Not a dependency of `verify`.
scan:
	@status=0; \
	$(MAKE) scan-bandit || status=1; \
	$(MAKE) scan-semgrep || status=1; \
	$(MAKE) scan-deps || status=1; \
	exit $$status

# Registry packs for the languages in this repo. p/react and p/javascript cover the
# React app and the JavaScript rules that also apply to TypeScript. .claude/ is excluded
# on the command line so a project .semgrepignore does not replace Semgrep's defaults.
scan-semgrep:
	mkdir -p build
	SEMGREP_SEND_METRICS=off uvx --python 3.13 --from semgrep==$(SEMGREP_VERSION) semgrep scan \
		--metrics=off \
		--disable-version-check \
		--error \
		--config p/python \
		--config p/owasp-top-ten \
		--config p/secrets \
		--config p/github-actions \
		--config p/dockerfile \
		--config p/typescript \
		--config p/react \
		--config p/javascript \
		--exclude .claude \
		--sarif \
		--output $(SEMGREP_SARIF) \
		. ; \
	code=$$?; \
	if [ "$$code" -gt 1 ]; then exit "$$code"; fi; \
	uv run --no-project --python 3.13 python backend/scripts/filter_semgrep_sarif.py $(SEMGREP_SARIF)

# backend/src only, medium+ severity and confidence. Skips live in pyproject.toml.
scan-bandit:
	uvx --python 3.13 --from bandit==$(BANDIT_VERSION) bandit \
		--configfile pyproject.toml \
		--recursive backend/src \
		--severity-level medium \
		--confidence-level medium

# uv export of the lockfile (every group, no local workspace packages) and pnpm audit.
# pnpm's audit level is high: lower advisories still print in the summary.
scan-deps:
	@mkdir -p build
	@status=0; \
	uv export --frozen --all-packages --all-groups --no-emit-workspace --no-header --no-hashes \
		--format requirements-txt --output-file build/requirements-audit.txt >/dev/null \
		&& uvx --python 3.13 --from pip-audit==$(PIP_AUDIT_VERSION) pip-audit \
			--requirement build/requirements-audit.txt --progress-spinner off \
		|| status=1; \
	pnpm audit --audit-level high || status=1; \
	exit $$status

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
