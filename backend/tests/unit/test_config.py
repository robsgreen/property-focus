"""Settings: environment validation, defaults and refusal of development-only features."""

import asyncio

import pytest

from property_focus.adapters.db import create_database_engine, database_url
from property_focus.config import (
    LOCAL_OIDC_ISSUER,
    ConfigurationError,
    DevelopmentOnlyError,
    McpEndpoint,
    Settings,
)

# Outside development the OIDC issuer is required; deployed-environment cases add this one.
DEPLOYED_ISSUER = "https://auth.example.test/realms/property-focus"


def test_development_environment_uses_synthetic_data_and_injected_secrets() -> None:
    settings = Settings.from_environment({"PF_ENVIRONMENT": "development"})

    assert settings.development
    assert settings.data_mode == "synthetic"
    assert settings.secret_provider == "deployment"
    assert settings.secret_prefix == "property-focus/development"
    assert not settings.fixture_issuer_enabled


@pytest.mark.parametrize(
    ("environment", "provider"),
    [("dogfood", "deployment"), ("beta", "aws"), ("production", "aws")],
)
def test_secret_provider_defaults_by_environment(environment: str, provider: str) -> None:
    settings = Settings.from_environment(
        {"PF_ENVIRONMENT": environment, "PF_OIDC_ISSUER": DEPLOYED_ISSUER}
    )

    assert not settings.development
    assert settings.secret_provider == provider


def test_explicit_values_override_defaults() -> None:
    settings = Settings.from_environment(
        {
            "PF_ENVIRONMENT": "dogfood",
            "PF_OIDC_ISSUER": DEPLOYED_ISSUER,
            "PF_DATA_MODE": "live",
            "PF_SECRET_PROVIDER": "aws",
            "PF_SECRET_PREFIX": "pf/dogfood",
            "PF_AWS_REGION": "us-west-2",
        }
    )

    assert settings.data_mode == "live"
    assert settings.secret_provider == "aws"
    assert settings.secret_prefix == "pf/dogfood"
    assert settings.aws_region == "us-west-2"


def test_empty_variables_take_defaults() -> None:
    settings = Settings.from_environment(
        {"PF_ENVIRONMENT": "beta", "PF_OIDC_ISSUER": DEPLOYED_ISSUER, "PF_DATA_MODE": ""}
    )

    assert settings.data_mode == "synthetic"


def test_unrelated_variables_are_ignored() -> None:
    settings = Settings.from_environment({"PF_ENVIRONMENT": "development", "PF_LIVE": "1"})

    assert settings.environment == "development"


def test_missing_environment_is_refused_rather_than_defaulting_to_development() -> None:
    with pytest.raises(ConfigurationError, match="PF_ENVIRONMENT"):
        Settings.from_environment({})


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("PF_ENVIRONMENT", "qa"),
        ("PF_DATA_MODE", "real"),
        ("PF_SECRET_PROVIDER", "vault"),
        ("PF_SECRET_PREFIX", "/Leading/Slash"),
        ("PF_AUTH_FIXTURE_ISSUER", "sometimes"),
    ],
)
def test_invalid_values_name_the_variable(variable: str, value: str) -> None:
    environ = {"PF_ENVIRONMENT": "development", variable: value}

    with pytest.raises(ConfigurationError, match=variable) as raised:
        Settings.from_environment(environ)

    assert value not in str(raised.value)


def test_fixture_issuer_is_allowed_in_development() -> None:
    settings = Settings.from_environment(
        {"PF_ENVIRONMENT": "development", "PF_AUTH_FIXTURE_ISSUER": "true"}
    )

    assert settings.fixture_issuer_enabled


@pytest.mark.parametrize("environment", ["dogfood", "beta", "production"])
def test_fixture_issuer_is_refused_outside_development(environment: str) -> None:
    environ = {
        "PF_ENVIRONMENT": environment,
        "PF_OIDC_ISSUER": DEPLOYED_ISSUER,
        "PF_AUTH_FIXTURE_ISSUER": "true",
    }

    with pytest.raises(ConfigurationError, match="PF_AUTH_FIXTURE_ISSUER is development-only"):
        Settings.from_environment(environ)


def test_development_only_feature_runs_in_development() -> None:
    Settings.model_validate({"environment": "development"}).require_development("make dev")


@pytest.mark.parametrize("environment", ["dogfood", "beta", "production"])
def test_development_only_feature_is_refused_outside_development(environment: str) -> None:
    settings = Settings.model_validate({"environment": environment, "oidc_issuer": DEPLOYED_ISSUER})

    with pytest.raises(DevelopmentOnlyError, match=f"make dev .*PF_ENVIRONMENT={environment}"):
        settings.require_development("make dev")


def test_settings_are_immutable() -> None:
    settings = Settings.model_validate({"environment": "development"})

    with pytest.raises(ValueError, match="frozen"):
        settings.environment = "production"  # type: ignore[misc]


def test_database_urls_default_to_local_compose_roles() -> None:
    settings = Settings.from_environment({"PF_ENVIRONMENT": "development"})

    assert settings.database_url.startswith("postgresql+psycopg://pf_runtime@localhost:5432/")
    assert settings.migration_database_url.startswith(
        "postgresql+psycopg://pf_migrator@localhost:5432/"
    )


@pytest.mark.parametrize("variable", ["PF_DATABASE_URL", "PF_MIGRATION_DATABASE_URL"])
def test_database_url_with_a_password_is_refused_without_echoing_it(variable: str) -> None:
    environ = {
        "PF_ENVIRONMENT": "dogfood",
        "PF_OIDC_ISSUER": DEPLOYED_ISSUER,
        variable: "postgresql+psycopg://pf_runtime:s3cr3t-value@db.internal/property_focus",
    }

    with pytest.raises(
        ConfigurationError, match=f"{variable}.*must not contain a password"
    ) as raised:
        Settings.from_environment(environ)

    assert "s3cr3t-value" not in str(raised.value)


def test_database_password_comes_from_the_secret_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PF_DATABASE_PASSWORD", "placeholder-db-password")
    monkeypatch.setenv("PF_MIGRATION_DATABASE_PASSWORD", "placeholder-owner-password")
    settings = Settings.from_environment({"PF_ENVIRONMENT": "development"})

    runtime = asyncio.run(database_url(settings, "runtime"))
    migration = asyncio.run(database_url(settings, "migration"))

    assert (runtime.username, runtime.password) == ("pf_runtime", "placeholder-db-password")
    assert (migration.username, migration.password) == (
        "pf_migrator",
        "placeholder-owner-password",
    )


def test_database_url_without_a_configured_password_connects_without_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PF_DATABASE_PASSWORD", raising=False)
    settings = Settings.from_environment({"PF_ENVIRONMENT": "development"})

    url = asyncio.run(database_url(settings))

    assert url.password is None


def test_database_url_reads_the_password_from_a_given_secret_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PF_DATABASE_PASSWORD", "placeholder-db-password")
    settings = Settings.from_environment({"PF_ENVIRONMENT": "development"})

    class GivenSecrets:
        async def get(self, logical_name: str) -> str:
            return f"given-{logical_name}"

    url = asyncio.run(database_url(settings, secrets=GivenSecrets()))

    assert url.password == "given-database_password"


def test_runtime_engines_hide_statement_parameters_from_errors() -> None:
    engine = create_database_engine("postgresql+psycopg://pf_runtime@db/pf", pool_size=1)

    assert engine.hide_parameters


def test_oidc_defaults_follow_the_local_keycloak_realm_in_development() -> None:
    settings = Settings.from_environment({"PF_ENVIRONMENT": "development"})

    assert settings.oidc_issuer == LOCAL_OIDC_ISSUER
    assert settings.oidc_jwks_url is None
    assert settings.oidc_audience == ("property-focus-api",)
    assert settings.mcp_audience == ("property-focus-mcp",)
    assert settings.oidc_audience_claim == "aud"
    assert settings.oidc_token_type == ("typ", "Bearer")
    assert settings.oidc_algorithms == ("RS256", "ES256")
    assert settings.oidc_clock_skew_seconds == 60


def test_oidc_settings_are_read_from_the_environment() -> None:
    settings = Settings.from_environment(
        {
            "PF_ENVIRONMENT": "beta",
            "PF_OIDC_ISSUER": DEPLOYED_ISSUER,
            "PF_OIDC_JWKS_URL": "https://auth.example.test/keys",
            "PF_OIDC_AUDIENCE": "web-client",
            "PF_MCP_AUDIENCE": "mcp-client, other-mcp-client",
            "PF_OIDC_AUDIENCE_CLAIM": "client_id",
            "PF_OIDC_TOKEN_TYPE": "token_use=access",
            "PF_OIDC_ALGORITHMS": "RS256",
            "PF_OIDC_CLOCK_SKEW_SECONDS": "30",
        }
    )

    assert settings.oidc_jwks_url == "https://auth.example.test/keys"
    assert settings.oidc_audience == ("web-client",)
    assert settings.mcp_audience == ("mcp-client", "other-mcp-client")
    assert settings.oidc_audience_claim == "client_id"
    assert settings.oidc_token_type == ("token_use", "access")
    assert settings.oidc_algorithms == ("RS256",)
    assert settings.oidc_clock_skew_seconds == 30


@pytest.mark.parametrize("environment", ["dogfood", "beta", "production"])
def test_oidc_issuer_is_required_outside_development(environment: str) -> None:
    with pytest.raises(ConfigurationError, match="PF_OIDC_ISSUER"):
        Settings.from_environment({"PF_ENVIRONMENT": environment})


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("PF_OIDC_ISSUER", "http://auth.example.test/realms/property-focus"),
        ("PF_OIDC_JWKS_URL", "http://keycloak:8080/realms/property-focus/certs"),
    ],
)
def test_identity_provider_urls_must_be_https_outside_development(
    variable: str, value: str
) -> None:
    environ = {"PF_ENVIRONMENT": "dogfood", "PF_OIDC_ISSUER": DEPLOYED_ISSUER, variable: value}

    with pytest.raises(ConfigurationError, match=f"{variable} must be an https URL"):
        Settings.from_environment(environ)


def test_development_accepts_the_http_local_realm_and_backchannel_key_set() -> None:
    settings = Settings.from_environment(
        {
            "PF_ENVIRONMENT": "development",
            "PF_OIDC_JWKS_URL": "http://keycloak:8080/realms/property-focus/certs",
        }
    )

    assert settings.oidc_jwks_url == "http://keycloak:8080/realms/property-focus/certs"


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("PF_OIDC_ALGORITHMS", "HS256"),
        ("PF_OIDC_ALGORITHMS", "RS256,none"),
        ("PF_OIDC_ALGORITHMS", " , "),
        ("PF_OIDC_TOKEN_TYPE", "Bearer"),
        ("PF_OIDC_TOKEN_TYPE", "typ="),
        ("PF_OIDC_AUDIENCE", ","),
        ("PF_MCP_AUDIENCE", ","),
        ("PF_OIDC_CLOCK_SKEW_SECONDS", "-1"),
        ("PF_OIDC_CLOCK_SKEW_SECONDS", "3600"),
    ],
)
def test_invalid_oidc_settings_name_the_variable(variable: str, value: str) -> None:
    with pytest.raises(ConfigurationError, match=variable):
        Settings.from_environment({"PF_ENVIRONMENT": "development", variable: value})


@pytest.mark.parametrize("environment", ["development", "dogfood"])
def test_rest_and_mcp_audiences_default_to_the_realm_audiences_everywhere(
    environment: str,
) -> None:
    settings = Settings.from_environment(
        {"PF_ENVIRONMENT": environment, "PF_OIDC_ISSUER": DEPLOYED_ISSUER}
    )

    assert settings.oidc_audience == ("property-focus-api",)
    assert settings.mcp_audience == ("property-focus-mcp",)


def test_rest_and_mcp_audiences_must_not_share_a_value() -> None:
    environ = {
        "PF_ENVIRONMENT": "development",
        "PF_OIDC_AUDIENCE": "property-focus-api,shared",
        "PF_MCP_AUDIENCE": "shared",
    }

    with pytest.raises(ConfigurationError, match="PF_MCP_AUDIENCE") as raised:
        Settings.from_environment(environ)
    assert "shared: shared" in str(raised.value)


def test_mcp_endpoint_defaults_to_make_dev_on_loopback_in_development() -> None:
    settings = Settings.from_environment({"PF_ENVIRONMENT": "development"})

    assert settings.mcp_endpoint() == McpEndpoint(
        public_url="http://localhost:8001/mcp",
        allowed_hosts=("localhost:8001", "127.0.0.1:8001"),
        allowed_origins=("http://localhost:8001", "http://127.0.0.1:8001"),
    )


def test_mcp_endpoint_is_read_from_the_environment() -> None:
    settings = Settings.from_environment(
        {
            "PF_ENVIRONMENT": "dogfood",
            "PF_OIDC_ISSUER": DEPLOYED_ISSUER,
            "PF_MCP_PUBLIC_URL": "https://app.example.test/mcp",
            "PF_MCP_ALLOWED_HOSTS": "app.example.test",
            "PF_MCP_ALLOWED_ORIGINS": "https://app.example.test, https://other.example.test",
        }
    )

    assert settings.mcp_endpoint() == McpEndpoint(
        public_url="https://app.example.test/mcp",
        allowed_hosts=("app.example.test",),
        allowed_origins=("https://app.example.test", "https://other.example.test"),
    )


@pytest.mark.parametrize("environment", ["dogfood", "beta", "production"])
def test_mcp_endpoint_is_refused_while_unset_outside_development(environment: str) -> None:
    settings = Settings.from_environment(
        {"PF_ENVIRONMENT": environment, "PF_OIDC_ISSUER": DEPLOYED_ISSUER}
    )

    with pytest.raises(ConfigurationError) as raised:
        settings.mcp_endpoint()
    for variable in ("PF_MCP_PUBLIC_URL", "PF_MCP_ALLOWED_HOSTS", "PF_MCP_ALLOWED_ORIGINS"):
        assert variable in str(raised.value)


def test_mcp_endpoint_refuses_an_empty_allowlist() -> None:
    settings = Settings.from_environment(
        {"PF_ENVIRONMENT": "development", "PF_MCP_ALLOWED_HOSTS": " , "}
    )

    with pytest.raises(ConfigurationError, match="PF_MCP_ALLOWED_HOSTS"):
        settings.mcp_endpoint()


@pytest.mark.parametrize("value", ["localhost:8001/mcp", "ftp://app.example.test/mcp"])
def test_mcp_public_url_must_be_an_http_url(value: str) -> None:
    with pytest.raises(ConfigurationError, match="PF_MCP_PUBLIC_URL"):
        Settings.from_environment({"PF_ENVIRONMENT": "development", "PF_MCP_PUBLIC_URL": value})
