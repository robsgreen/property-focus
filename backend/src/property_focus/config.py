"""Typed process configuration read from ``PF_*`` environment variables.

Configuration holds deployment choices only. Secrets are never settings: application code
reads them through the ``SecretProvider`` port by logical name.

Development-only features are opt-in and allowed only when ``PF_ENVIRONMENT`` is
``development``. ``PF_ENVIRONMENT`` has no default, so a deployment that forgets it fails
at start-up instead of silently running as development.
"""

import os
from collections.abc import Mapping
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Environment = Literal["development", "dogfood", "staging", "production"]

# synthetic: fixtures only, no live sources or paid model calls. live: real sources and
# providers, with credentials resolved through the secret provider.
DataMode = Literal["synthetic", "live"]

# aws: Secrets Manager, then Parameter Store. deployment: values the deployment injects
# into the process environment (dogfood and local development).
SecretProviderKind = Literal["aws", "deployment"]

# Environment variable -> settings field. Only these variables are read.
_ENVIRONMENT_VARIABLES: dict[str, str] = {
    "PF_ENVIRONMENT": "environment",
    "PF_DATA_MODE": "data_mode",
    "PF_AUTH_FIXTURE_ISSUER": "fixture_issuer_enabled",
    "PF_SECRET_PROVIDER": "secret_provider",
    "PF_SECRET_PREFIX": "secret_prefix",
    "PF_AWS_REGION": "aws_region",
    "PF_DATABASE_URL": "database_url",
    "PF_MIGRATION_DATABASE_URL": "migration_database_url",
    "PF_OIDC_ISSUER": "oidc_issuer",
    "PF_OIDC_JWKS_URL": "oidc_jwks_url",
    "PF_OIDC_AUDIENCE": "oidc_audience",
    "PF_OIDC_AUDIENCE_CLAIM": "oidc_audience_claim",
    "PF_OIDC_TOKEN_TYPE": "oidc_token_type",
    "PF_OIDC_ALGORITHMS": "oidc_algorithms",
    "PF_OIDC_CLOCK_SKEW_SECONDS": "oidc_clock_skew_seconds",
    "PF_OTLP_ENDPOINT": "otlp_endpoint",
    "PF_MCP_PUBLIC_URL": "mcp_public_url",
    "PF_MCP_ALLOWED_HOSTS": "mcp_allowed_hosts",
    "PF_MCP_ALLOWED_ORIGINS": "mcp_allowed_origins",
}

# Signing algorithms an access token may use. Symmetric (HS*) and unsigned (none) tokens are
# never accepted, whatever PF_OIDC_ALGORITHMS says.
SUPPORTED_TOKEN_ALGORITHMS = frozenset({"RS256", "ES256"})

# The issuer development uses when PF_OIDC_ISSUER is unset: the local Compose Keycloak realm.
LOCAL_OIDC_ISSUER = "http://localhost:8080/realms/property-focus"

# The MCP endpoint development uses when PF_MCP_* are unset: `make dev` on loopback port 8001.
LOCAL_MCP_PUBLIC_URL = "http://localhost:8001/mcp"
LOCAL_MCP_ALLOWED_HOSTS = ("localhost:8001", "127.0.0.1:8001")
LOCAL_MCP_ALLOWED_ORIGINS = ("http://localhost:8001", "http://127.0.0.1:8001")


class ConfigurationError(ValueError):
    """The process environment does not describe a valid configuration."""


class DevelopmentOnlyError(RuntimeError):
    """A development-only feature was requested outside the development environment."""


class McpEndpoint(BaseModel):
    """Where the MCP server is reached and which requests its transport accepts."""

    model_config = ConfigDict(frozen=True)

    # The server's public URL: the resource that tokens and protected-resource metadata name.
    public_url: str
    # `Host` header values the transport accepts; anything else is refused.
    allowed_hosts: tuple[str, ...]
    # `Origin` header values the transport accepts from browsers; anything else is refused.
    allowed_origins: tuple[str, ...]


class Settings(BaseModel):
    """Validated, immutable process configuration.

    Build it with ``Settings.from_environment()``, or ``Settings.model_validate()`` in tests,
    so the environment-derived defaults apply.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    environment: Environment
    data_mode: DataMode = "synthetic"
    # Accept bearer tokens from the signed local fixture issuer. Development only.
    fixture_issuer_enabled: bool = False
    # Defaults to "aws" in staging and production, "deployment" elsewhere.
    secret_provider: SecretProviderKind
    # Namespace for AWS secret and parameter names; defaults to "property-focus/<environment>".
    secret_prefix: str = Field(pattern=r"^[a-z0-9][a-z0-9/_-]*[a-z0-9]$")
    # AWS region for the AWS secret provider; None uses the AWS SDK's own resolution.
    aws_region: str | None = None
    # SQLAlchemy URL for the runtime database role (pf_runtime), without a password. The
    # password, when the server needs one, is the `database_password` secret.
    database_url: str = "postgresql+psycopg://pf_runtime@localhost:5432/property_focus"
    # SQLAlchemy URL for the migration owner role, without a password. The password, when the
    # server needs one, is the `migration_database_password` secret.
    migration_database_url: str = "postgresql+psycopg://pf_migrator@localhost:5432/property_focus"
    # OIDC access-token validation (plan: Authentication configuration). The issuer is the
    # expected `iss` and the base of its discovery document; it defaults to the local Keycloak
    # realm in development and is required, and must be https, everywhere else.
    oidc_issuer: str
    # Key-set URL that replaces the discovery document's `jwks_uri`, for a backchannel host
    # (Compose service name); None reads it from discovery. Must be https outside development.
    oidc_jwks_url: str | None = None
    # Accepted audience values; a token passes when its audience claim holds any of them.
    # PF_OIDC_AUDIENCE is comma-separated.
    oidc_audience: tuple[str, ...] = Field(default=("property-focus-api",), min_length=1)
    # The claim holding the audience: `aud` for Keycloak, `client_id` for Cognito.
    oidc_audience_claim: str = Field(default="aud", min_length=1)
    # (claim, value) that marks an access token; PF_OIDC_TOKEN_TYPE is `claim=value`, such as
    # `typ=Bearer` (Keycloak) or `token_use=access` (Cognito).
    oidc_token_type: tuple[str, str] = ("typ", "Bearer")
    # Allowed signing algorithms, a non-empty subset of SUPPORTED_TOKEN_ALGORITHMS.
    # PF_OIDC_ALGORITHMS is comma-separated.
    oidc_algorithms: tuple[str, ...] = Field(default=("RS256", "ES256"), min_length=1)
    # Tolerance in seconds applied to `exp` and `nbf`.
    oidc_clock_skew_seconds: int = Field(default=60, ge=0, le=300)
    # Base URL of the OTLP/HTTP collector that receives traces and metrics, such as
    # http://localhost:4318 (the local Compose `otel` service); None exports nothing.
    otlp_endpoint: str | None = Field(default=None, pattern=r"^https?://[^/\s]+")
    # The MCP server's endpoint (plan: Authentication configuration). Development defaults to
    # `make dev` on loopback; elsewhere every value is required by the MCP server only
    # (``mcp_endpoint``), so the other processes run without them. PF_MCP_ALLOWED_HOSTS and
    # PF_MCP_ALLOWED_ORIGINS are comma-separated.
    mcp_public_url: str | None = Field(default=None, pattern=r"^https?://[^/\s]+(/\S*)?$")
    mcp_allowed_hosts: tuple[str, ...] = ()
    mcp_allowed_origins: tuple[str, ...] = ()

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def _refuse_embedded_password(cls, url: str) -> str:
        if urlsplit(url).password is not None:
            raise ValueError("must not contain a password; the secret provider supplies it")
        return url

    @field_validator(
        "oidc_audience",
        "oidc_algorithms",
        "mcp_allowed_hosts",
        "mcp_allowed_origins",
        mode="before",
    )
    @classmethod
    def _split_list(cls, value: Any) -> Any:
        """Accept a comma-separated string, as the environment provides."""
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("oidc_token_type", mode="before")
    @classmethod
    def _split_claim_value(cls, value: Any) -> Any:
        """Accept ``claim=value``, as the environment provides."""
        if isinstance(value, str):
            claim, separator, expected = value.partition("=")
            if not (separator and claim.strip() and expected.strip()):
                raise ValueError("must be claim=value, such as typ=Bearer")
            return (claim.strip(), expected.strip())
        return value

    @field_validator("oidc_algorithms")
    @classmethod
    def _refuse_unsupported_algorithms(cls, algorithms: tuple[str, ...]) -> tuple[str, ...]:
        unsupported = sorted(set(algorithms) - SUPPORTED_TOKEN_ALGORITHMS)
        if unsupported:
            raise ValueError(
                f"unsupported algorithm(s) {', '.join(unsupported)}; "
                f"allowed: {', '.join(sorted(SUPPORTED_TOKEN_ALGORITHMS))}"
            )
        return algorithms

    @model_validator(mode="before")
    @classmethod
    def _default_from_environment(cls, values: Any) -> Any:
        """Fill the defaults that depend on ``environment`` before field validation."""
        if not isinstance(values, dict):
            return values
        environment = values.get("environment")
        values = dict(values)
        values.setdefault(
            "secret_provider", "aws" if environment in ("staging", "production") else "deployment"
        )
        if isinstance(environment, str):
            values.setdefault("secret_prefix", f"property-focus/{environment}")
        if environment == "development":
            values.setdefault("oidc_issuer", LOCAL_OIDC_ISSUER)
            values.setdefault("mcp_public_url", LOCAL_MCP_PUBLIC_URL)
            values.setdefault("mcp_allowed_hosts", LOCAL_MCP_ALLOWED_HOSTS)
            values.setdefault("mcp_allowed_origins", LOCAL_MCP_ALLOWED_ORIGINS)
        return values

    @model_validator(mode="after")
    def _refuse_development_features(self) -> Self:
        if self.fixture_issuer_enabled and not self.development:
            raise ValueError(
                "PF_AUTH_FIXTURE_ISSUER is development-only and is refused when "
                f"PF_ENVIRONMENT={self.environment}"
            )
        return self

    @model_validator(mode="after")
    def _require_https_identity_provider(self) -> Self:
        if self.development:
            return self
        if urlsplit(self.oidc_issuer).scheme != "https":
            raise ValueError("PF_OIDC_ISSUER must be an https URL outside development")
        if self.oidc_jwks_url is not None and urlsplit(self.oidc_jwks_url).scheme != "https":
            raise ValueError("PF_OIDC_JWKS_URL must be an https URL outside development")
        return self

    @property
    def development(self) -> bool:
        """True only when ``PF_ENVIRONMENT`` is ``development``."""
        return self.environment == "development"

    def require_development(self, feature: str) -> None:
        """Refuse ``feature`` (for example ``"make dev"``) outside development.

        Raises ``DevelopmentOnlyError`` naming the feature and the active environment.
        """
        if not self.development:
            raise DevelopmentOnlyError(
                f"{feature} is development-only and is refused when "
                f"PF_ENVIRONMENT={self.environment}"
            )

    def mcp_endpoint(self) -> McpEndpoint:
        """The MCP server's endpoint settings (``PF_MCP_*``).

        Development defaults every value to ``make dev`` on loopback. Raises
        ``ConfigurationError`` naming each value that is unset or empty, as it is outside
        development until the deployment sets it.
        """
        public_url, hosts, origins = (
            self.mcp_public_url,
            self.mcp_allowed_hosts,
            self.mcp_allowed_origins,
        )
        if not public_url or not hosts or not origins:
            missing = [
                variable
                for variable, value in (
                    ("PF_MCP_PUBLIC_URL", public_url),
                    ("PF_MCP_ALLOWED_HOSTS", hosts),
                    ("PF_MCP_ALLOWED_ORIGINS", origins),
                )
                if not value
            ]
            raise ConfigurationError(
                "invalid configuration: "
                + "; ".join(f"{variable}: required by the MCP server" for variable in missing)
            )
        return McpEndpoint(public_url=public_url, allowed_hosts=hosts, allowed_origins=origins)

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> Self:
        """Read and validate settings from ``environ`` (default: ``os.environ``).

        Unset and empty variables take their defaults. Raises ``ConfigurationError`` listing
        every invalid or missing variable.
        """
        source = os.environ if environ is None else environ
        values = {
            field: source[variable]
            for variable, field in _ENVIRONMENT_VARIABLES.items()
            if source.get(variable)
        }
        try:
            return cls.model_validate(values)
        except ValidationError as error:
            raise ConfigurationError(_describe(error)) from error


def _describe(error: ValidationError) -> str:
    """Name each failing variable with pydantic's reason, without echoing input values."""
    variables = {field: variable for variable, field in _ENVIRONMENT_VARIABLES.items()}
    problems = []
    for detail in error.errors(include_input=False, include_url=False):
        field = str(detail["loc"][0]) if detail["loc"] else ""
        problems.append(f"{variables.get(field, 'configuration')}: {detail['msg']}")
    return "invalid configuration: " + "; ".join(problems)
