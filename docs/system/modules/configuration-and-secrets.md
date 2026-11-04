# Configuration and Secrets

Every backend process reads its deployment choices from `PF_*` environment variables into one frozen `Settings` object, and reads secrets separately through the `SecretProvider` port by logical name. Settings never hold a secret value, and application code never reads a secret from the environment directly.

## Key files

| File | Role |
|---|---|
| `backend/src/property_focus/config.py` | `Settings`, `Settings.from_environment()`, `Settings.mcp_endpoint()`, `McpEndpoint`, `ConfigurationError`, `DevelopmentOnlyError` |
| `backend/src/property_focus/application/ports.py` | `SecretProvider` protocol, `SecretNotFoundError` |
| `backend/src/property_focus/adapters/secrets.py` | `DeploymentInjectedSecretProvider`, `AwsSecretProvider`, `secret_provider_for()` |
| `.env.example` | Local template with placeholders only; copy to `.env` (ignored by Git) |
| `backend/tests/unit/test_config.py`, `test_secrets.py` | Validation rules; botocore Stubber cases for the AWS provider |

## Settings

`Settings.from_environment(environ=None)` reads only the variables below; unset and empty variables take their defaults. Invalid input raises `ConfigurationError` naming each failing variable without echoing its value. The model forbids extra fields and is immutable.

| Variable | Field | Default | Notes |
|---|---|---|---|
| `PF_ENVIRONMENT` | `environment` | none (required) | `development`, `dogfood`, `staging` or `production`. No default, so a deployment that forgets it fails at start-up |
| `PF_DATA_MODE` | `data_mode` | `synthetic` | `synthetic` (fixtures only) or `live` |
| `PF_AUTH_FIXTURE_ISSUER` | `fixture_issuer_enabled` | `false` | Refused unless `development` |
| `PF_SECRET_PROVIDER` | `secret_provider` | `aws` in staging/production, else `deployment` | |
| `PF_SECRET_PREFIX` | `secret_prefix` | `property-focus/<environment>` | Lower-case path pattern |
| `PF_AWS_REGION` | `aws_region` | SDK resolution | AWS provider only |
| `PF_DATABASE_URL` | `database_url` | `postgresql+psycopg://pf_runtime@localhost:5432/property_focus` | Runtime role. Must not contain a password |
| `PF_MIGRATION_DATABASE_URL` | `migration_database_url` | `postgresql+psycopg://pf_migrator@localhost:5432/property_focus` | Migration owner. Must not contain a password |
| `PF_OIDC_ISSUER` | `oidc_issuer` | `http://localhost:8080/realms/property-focus` in development only | Required and `https` elsewhere |
| `PF_OIDC_JWKS_URL` | `oidc_jwks_url` | from discovery | Backchannel override (Compose service name); `https` outside development |
| `PF_OIDC_AUDIENCE` | `oidc_audience` | `property-focus-api` | Audiences the REST API accepts (the realm gives `property-focus-api` to the web client only). Comma-separated; a token passes with any one |
| `PF_MCP_AUDIENCE` | `mcp_audience` | `property-focus-mcp` | Audiences the MCP server accepts (the realm gives `property-focus-mcp` to dynamically registered clients). Comma-separated; must share no value with `PF_OIDC_AUDIENCE`, so a token is accepted by one surface only. The defaults hold in every environment, dogfood included |
| `PF_OIDC_AUDIENCE_CLAIM` | `oidc_audience_claim` | `aud` | `client_id` for Cognito |
| `PF_OIDC_TOKEN_TYPE` | `oidc_token_type` | `typ=Bearer` | `claim=value`; Cognito uses `token_use=access` |
| `PF_OIDC_ALGORITHMS` | `oidc_algorithms` | `RS256,ES256` | Subset of `SUPPORTED_TOKEN_ALGORITHMS`; `HS*` and `none` are never accepted |
| `PF_OIDC_CLOCK_SKEW_SECONDS` | `oidc_clock_skew_seconds` | `60` | 0 to 300 |
| `PF_OTLP_ENDPOINT` | `otlp_endpoint` | unset | OTLP/HTTP base URL; unset exports nothing |
| `PF_MCP_PUBLIC_URL` | `mcp_public_url` | `http://localhost:8001/mcp` in development only | The MCP server's public URL: the SDK's resource server URL and the metadata's `resource`. `http` or `https` URL. Dogfood: `https://<app hostname>/mcp` |
| `PF_MCP_ALLOWED_HOSTS` | `mcp_allowed_hosts` | `localhost:8001,127.0.0.1:8001` in development only | Comma-separated `Host` values the MCP transport serves. Dogfood: the app hostname |
| `PF_MCP_ALLOWED_ORIGINS` | `mcp_allowed_origins` | `http://localhost:8001,http://127.0.0.1:8001` in development only | Comma-separated `Origin` values the MCP transport serves. Dogfood: `https://<app hostname>` |

Variables outside this table that the repository uses belong to other programs: `PF_KEYCLOAK_*`, `PF_WEB_ORIGIN` and `PF_SMOKE_USER_*` feed Keycloak and `backend/scripts/keycloak_realm.py`; `PF_WEB_OIDC_ISSUER`, `PF_WEB_OIDC_CLIENT_ID` and the optional `PF_WEB_OIDC_END_SESSION_URL` are build-time settings of the web app ([web app](./web-app.md)); `PF_DOGFOOD_*` and `PF_OBSERVABILITY_*` configure the dogfood host ([operations](../../operations.md)).

### Development-only features

Only the MCP server needs the `PF_MCP_*` values, so the other processes run without them: `Settings.mcp_endpoint()`, which `adapters/mcp/server.create_app()` calls at start-up, raises `ConfigurationError` naming each one that is unset or empty, as all three are outside development until the deployment sets them.

`settings.development` is true only for `PF_ENVIRONMENT=development`. `require_development(feature)` raises `DevelopmentOnlyError` elsewhere; `backend/scripts/dev.py` (`make dev`) calls it and exits 2 when refused. Validation also refuses `PF_AUTH_FIXTURE_ISSUER=true` outside development, and a non-`https` issuer or JWKS URL outside development.

## Secret providers

`SecretProvider.get(logical_name)` returns the value for a lower-case snake-case name such as `database_password`. A name that does not match raises `ValueError`; a name with no value raises `SecretNotFoundError`, whose message names the logical name only. `secret_provider_for(settings)` picks the implementation:

| Provider | Selected by | Lookup |
|---|---|---|
| `DeploymentInjectedSecretProvider` | `PF_SECRET_PROVIDER=deployment` (dogfood, development) | Environment variable `PF_<LOGICAL_NAME>`, read at each call; unset or empty counts as missing |
| `AwsSecretProvider` | `PF_SECRET_PROVIDER=aws` (staging, production) | Secrets Manager secret `<prefix>/<name>`, then Parameter Store parameter `/<prefix>/<name>` with decryption. No caching, so a rotated value applies on the next call. Binary secrets raise `ValueError`; AWS errors other than not-found propagate |

Both providers pass every value they return to `telemetry.register_secret`, so later log lines and exported spans redact it.

Logical names in use today:

| Name | Used by |
|---|---|
| `database_password` | `adapters/db.database_url(settings, "runtime")`, through `connect_runtime()` in the REST app, MCP app, task runtime and CLI `seed` |
| `migration_database_password` | `adapters/db.database_url(settings, "migration")`, the migration owner's connection (`backend/migrations/env.py`) |

`database_url()` is the only place that pairs a database role with its URL setting and password secret. It returns the URL without a password when the secret is missing, so libpq falls back to its own sources; local Compose trusts local connections and needs none.

## Consumers

The REST app, MCP app, CLI, task runtime, `backend/scripts/dev.py` and the migration environment all start from `Settings.from_environment()`. `config.py` imports nothing from the application, so every layer may read it.

## Design decisions

- Secrets stay out of `Settings` so a logged or dumped configuration never leaks one (D49). The URL validators reject an embedded password for the same reason.
- `PF_ENVIRONMENT` has no default so a misconfigured deployment cannot run as development.
- The OIDC settings are provider-neutral claim names and values, so moving from Keycloak to Cognito is a configuration change, not a code change.

## Related docs

- [Identity and authentication](./identity-and-auth.md) for how the `PF_OIDC_*` settings are used
- [Database and tenancy](./database-and-tenancy.md) for the two database roles
- [Telemetry](./telemetry.md) for secret redaction
- [Architecture](../architecture.md)
