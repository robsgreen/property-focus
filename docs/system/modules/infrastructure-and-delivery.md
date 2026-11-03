# Infrastructure and Delivery

This module covers how the code runs outside a unit test: the local Compose services, the container images, the dogfood host, the CDK app, and the CI and deploy workflows. Operator procedures for dogfood (deploy, rollback, Keycloak administration, replay) live in [operations](../../operations.md); this doc describes the files and how they fit together.

## Key files

| File | Role |
|---|---|
| `compose.yaml` | Local services and the optional `app` profile |
| `Makefile` | Every repository command ([README](../../../README.md) lists them) |
| `infrastructure/docker/backend.Dockerfile` | Backend images: `task`, `api`, `mcp`, `scheduler` targets |
| `infrastructure/docker/keycloak.Dockerfile` | Optimized dogfood Keycloak with the realm file baked in |
| `infrastructure/docker/check-compose-version.sh` | Fails unless `docker compose` is 2.27.2 or later; run by `make install` and `deploy.sh` |
| `infrastructure/dogfood/compose.yaml` | The dogfood host's services |
| `infrastructure/dogfood/Caddyfile` | TLS (Cloudflare Origin CA certificate) and routing for the app host and the auth host |
| `infrastructure/dogfood/deploy.sh` | Deploy and `--rollback` on the host, with the Cloudflare edge preflight |
| `infrastructure/dogfood/firewall.sh` | Admits ports 80 and 443 only from Cloudflare's ranges; `--check` for the deploy preflight |
| `infrastructure/dogfood/cloudflare-ips.txt`, `cloudflare-origin-ca.pem` | Cloudflare's published IP ranges (the one source for the firewall and Caddy's trusted proxies) and its public Origin CA roots |
| `infrastructure/dogfood/otel-collector.yaml`, `scheduler.crontab` | Dogfood collector and schedule |
| `infrastructure/cdk/` | CDK app `@property-focus/infra`: Cognito `IdentityStack` only |
| `.github/workflows/ci.yml`, `deploy-dogfood.yml` | Pull-request checks; manual dogfood deploy |
| `backend/scripts/smoke_dogfood.py`, `compatibility_report.py`, `check_fixture_secrets.py`, `check_boundaries.py`, `dev.py` | Supporting scripts |
| `backend/tests/unit/test_runtime_manifests.py`, `test_dogfood_edge.py`, `test_repo_checks.py`, `test_compose_version_check.py`, `integration/test_ci_workflow.py` | Tests for the manifests, the Cloudflare edge configuration, repository checks, the Compose version check and CI path filters |

## Local services (`compose.yaml`)

Every port binds to `127.0.0.1`.

| Service | Image | Port | Purpose |
|---|---|---|---|
| `postgres` | `postgres:17.11-alpine3.24` | 5432 | Application database; first start creates `pf_migrator` and `property_focus` |
| `keycloak-db-init` | same | | Creates the `keycloak` role and database when missing |
| `keycloak` | `quay.io/keycloak/keycloak:26.8.1` (digest-pinned) | 8080 | `start-dev --import-realm` from `infrastructure/keycloak/`; refuses to start without `PF_KEYCLOAK_ADMIN_USERNAME`/`PASSWORD` |
| `ministack` | `ministackorg/ministack:1.5.35` (digest-pinned) | 4566 | AWS API emulator (D19) |
| `mailpit` | `axllent/mailpit:v1.33.0` | 1025, 8025 | SMTP sink for Keycloak's verification emails; web UI on 8025 |
| `otel` | `otel/opentelemetry-collector-contrib:0.164.0` | 4317, 4318 | OTLP collector with the image's default config (debug exporter) |
| `migrate`, `api`, `mcp`, `scheduler` | built from `backend.Dockerfile` | 8000, 8001 | `app` profile only: the backend as images, after a one-shot `migrate` |

Backend containers use `PF_ENVIRONMENT=development`, the Compose database host, `PF_OIDC_ISSUER=http://localhost:8080/realms/property-focus` (the issuer browsers see) and `PF_OIDC_JWKS_URL` pointing at `keycloak:8080` inside the network.

`make dev` (`backend/scripts/dev.py`) is the usual alternative: it runs the API, MCP adapter, Vite and the scheduler as host processes, exits 2 unless `PF_ENVIRONMENT=development`, and stops all four when one exits.

## Images

`backend.Dockerfile` builds one locked environment with uv and derives four targets from it: `task` (entry point `python -m property_focus.cli`, used for `migrate`), `api` (health check `/health/live`), `mcp` (health check on the protected-resource metadata) and `scheduler` (crontab baked in at `/app/scheduler.crontab`). Base images are pinned by digest. A TLS-intercepting proxy's CA bundle can be passed as the `build_ca` build secret.

`keycloak.Dockerfile` runs `kc.sh build` for Postgres with health checks and starts with `start --optimized --import-realm`. Its pinned version and digest must equal the root `compose.yaml`; `test_runtime_manifests.py` fails when they differ.

## Dogfood host

One DigitalOcean host runs `infrastructure/dogfood/compose.yaml`: `postgres`, `db-init` (roles, databases and passwords from the environment file), `migrate`, `keycloak`, `api`, `mcp`, `scheduler`, `caddy`, `otel-collector` and `observability` (OpenObserve). Third-party images are pinned by digest; backend and Keycloak images are tagged with the commit. Every service runs with `no-new-privileges` and `cap_drop: [ALL]`; Postgres gets back `CHOWN`, `DAC_READ_SEARCH`, `FOWNER`, `SETGID` and `SETUID` for its entry point, Caddy gets `NET_BIND_SERVICE` and `DAC_READ_SEARCH` (to read the origin key, which belongs to the deploy user with mode 600). Keycloak (1536 MB), Postgres (1 GB) and OpenObserve (1 GB) have `mem_limit`s.

Cloudflare proxies both public hostnames (A5); the dashboard settings are a runbook in [operations](../../operations.md#cloudflare-edge).

| Layer | Control |
|---|---|
| Cloudflare | Proxied DNS records, SSL mode Full (strict), Always Use HTTPS, the Free managed WAF ruleset, one per-IP rate-limiting rule over the auth host's registration and sign-in paths and `/mcp` (20 requests per 10 seconds, then Block with `429`); Bot Fight Mode off (user decision) |
| Host firewall | `firewall.sh` admits TCP 80 and 443 only from `cloudflare-ips.txt`, in `DOCKER-USER` (published ports) and `INPUT`, for IPv4 and IPv6 |
| Caddy | Serves the Cloudflare Origin CA certificate and key from `/etc/property-focus/origin/` (mounted read-only; the deploy workflow writes them from the `DOGFOOD_ORIGIN_CERT` and `DOGFOOD_ORIGIN_KEY` secrets) with certificate automation disabled; publishes TCP 80 and 443 only (HTTP/1.1 and HTTP/2); trusts `CF-Connecting-IP`, then `X-Forwarded-For`, only from the ranges in `PF_CLOUDFLARE_IP_RANGES`, which `deploy.sh` reads from `cloudflare-ips.txt`; sends every upstream `X-Forwarded-For` set to that resolved client IP and `X-Forwarded-Host` set to the requested host, replacing any values a client sent through Cloudflare; writes JSON access logs with the real `client_ip` and without authorization codes |
| Application | `api` and `mcp` publish no ports, so they trust forwarded headers only from Caddy |

Caddy serves two hostnames:

| Host | Routes |
|---|---|
| `PF_DOGFOOD_APP_HOST` | `/v1/*` and `/health/*` to `api:8000`; `/mcp` and `/.well-known/oauth-protected-resource*` to `mcp:8001`; `/.well-known/oauth-authorization-server` (exact path, for 2025-03-26 MCP clients) to the realm's authorization-server metadata on Keycloak, requested with the auth host as `Host` and `X-Forwarded-Host` so its issuer and endpoints are the public sign-in URLs; any other `/.well-known/*`, `/authorize`, `/register` and `/token` a `404`; everything else the web build from `/srv/web` with `index.html` fallback |
| `PF_DOGFOOD_AUTH_HOST` | `/realms/property-focus[/*]`, `/resources/*` and `/.well-known/oauth-authorization-server/realms/property-focus` to Keycloak; `/.well-known/openid-configuration/realms/property-focus` rewritten to the realm's discovery document; everything else `404` |

The deploy workflow (`deploy-dogfood.yml`, manual, gated by the `dogfood` environment's reviewers; actions pinned by commit) builds the web app with the dogfood issuer, copies the commit and build to its own release directory `/srv/property-focus/releases/<commit>`, writes `/etc/property-focus/dogfood.env`, writes the new origin certificate and key beside the live pair (`cert.pem.new`, `key.pem.new`), runs that release's `deploy.sh <commit>`, removes the SSH key, and then runs `make smoke-dogfood` from the runner, through Cloudflare: it also requires a `cf-ray` header on its responses, fails on a Cloudflare challenge page with a message naming Bot Fight Mode, and expects a direct connection to `PF_DOGFOOD_ORIGIN_ADDRESS` port 443 to fail. `deploy.sh` checks the environment file's format and reads it only through Compose (`--env-file`, `config --environment`); before changing anything on a deploy it checks the Cloudflare edge (committed ranges equal Cloudflare's published lists, `firewall.sh --check`, and an origin certificate from the Origin CA that covers both hostnames, matches its mode-600 key and has more than 30 days left, checking the new pair when the workflow wrote one), and only once that passes moves the new pair over the live one, so a rejected deploy leaves the live pair for Caddy's next restart and any rollback; then it builds images, records digests in `releases.tsv`, migrates, reconciles the realm with `keycloak_realm.py apply` and `check`, starts services, recreates Caddy and the collector (their configuration files are single-file bind mounts), checks everything through Caddy on loopback with TLS verified against `cloudflare-origin-ca.pem` (both protected-resource metadata forms and the app host's authorization-server metadata must name the realm on the auth host as issuer), and only then points the `/srv/property-focus/current` symlink at the release. `--rollback <commit>` runs an earlier release's API, MCP adapter, scheduler, Caddyfile, collector configuration and web build again and switches the symlink back; it never reverses a migration or touches Keycloak. The step-by-step and rollback live in [operations](../../operations.md).

## CDK app

`infrastructure/cdk/bin/property-focus.ts` synthesizes the stacks for the environment named by `--context environment=<name>` (default `dogfood`). `cdk.json` lists `dogfood` and `staging` under `propertyFocus:environments`, each with `account: null`, so synthesis works but any deploy refuses AWS credentials until an operator records the account. Each stack also carries a CloudFormation rule, `RecordedAccountOnly`, asserting that `AWS::AccountId` equals the recorded account (always false while it is `null`), so a synthesized template deployed outside the CDK CLI is refused in any other account. The only stack is `PropertyFocus-Identity-<env>` ([identity and authentication](./identity-and-auth.md)), synthesized with `LegacyStackSynthesizer` so it needs no `cdk bootstrap`. `make infra` runs the type check (`tsc --noEmit`), the assertion tests and `cdk synth` without credentials.

## CI (`ci.yml`)

The `changes` job maps each changed path to jobs; pushes to `main` and manual runs select all.

| Path | Jobs |
|---|---|
| `backend/*` | backend, integration, replay, generation, images, bootstrap |
| `frontend/*` | frontend, generation |
| `infrastructure/keycloak/*` | integration, images, bootstrap |
| `infrastructure/docker/*`, `infrastructure/dogfood/*` | backend, integration, images |
| `infrastructure/cdk/*` | cdk |
| `ontology/*`, `fixtures/*` | backend, replay, bootstrap |
| `docs/*`, `specs/*` | markdown |
| anything else (root files, lockfiles, workflows, other `infrastructure/*`) | all |

| Job | Runs |
|---|---|
| `secret-scan` | Every pull request: `check_fixture_secrets.py` |
| `markdown` | `pnpm lint:md` |
| `backend` | `make install`, `make be-check`, `make be-unit` |
| `integration` | `make be-int` |
| `replay` | `make be-replay` |
| `generation` | `make check-generated` |
| `frontend` | `make fe` |
| `cdk` | `make infra` |
| `images` | Builds the `task`, `api`, `mcp` and `scheduler` backend targets and the Keycloak image, and runs `docker compose -f infrastructure/dogfood/compose.yaml config --quiet` with placeholder values (including placeholder Cloudflare ranges) |
| `bootstrap` | The README bootstrap from a clean checkout: installs, `.env`, Compose services, `migrate`, ontology load and publish once `ontology/v1.0.0` exists, `seed --dataset synthetic` |

`test_ci_workflow.py` replays the path filter against sample changes. Every action is pinned by commit SHA with the release named in a comment.

## Repository checks

- `make check-layout` fails on root `apps/`, `packages/`, `infra/` or `evals/`.
- `make check-compose` (a prerequisite of `make install`) runs `check-compose-version.sh`, which fails with "Docker Compose >= 2.27.2 is required, found A.B" on an older Compose plugin. 2.27.2 is the first release with `docker compose config --environment`, which `deploy.sh` needs; the inline `configs` content of both Compose files needs 2.23.1. `deploy.sh` runs the same check before anything else on the host.
- Scripts the unit tests run on a developer machine (`deploy.sh` up to its image build, the CI path filter) use only what macOS also has: BSD `paste`, `find` and `awk`, LibreSSL's `openssl` and bash 3.2. `test_dogfood_edge.py` runs `deploy.sh` with stand-ins that reject the GNU- and OpenSSL-only options (`paste` without an operand, `stat -c`, `openssl x509 -checkhost`).
- `check_boundaries.py` enforces the layer import rules (see [AGENTS.md](../../../AGENTS.md)).
- `check_fixture_secrets.py` scans committed files and `fixtures/` for credential patterns; it uses only the standard library.
- `make compatibility-local` regenerates [compatibility](../../compatibility.md) from MiniStack runs.

## Related docs

- [Operations](../../operations.md)
- [Architecture](../architecture.md)
- [Identity and authentication](./identity-and-auth.md)
