"""``SecretProvider`` implementations and the configuration-driven choice between them.

- ``AwsSecretProvider`` (AWS): Secrets Manager holds secrets; Parameter Store holds
  configuration. Both are keyed by ``<prefix>/<logical name>``.
- ``DeploymentInjectedSecretProvider`` (dogfood, local development): the deployment injects
  each value into the process environment as ``PF_<LOGICAL_NAME>``.

Neither provider logs or echoes values; errors name the logical name only. Both register
every value they return with telemetry, which redacts it from logs and exported spans.
"""

import asyncio
import os
import re
from collections.abc import Mapping
from typing import Any, Protocol

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from property_focus.adapters.telemetry import register_secret
from property_focus.application.ports import SecretNotFoundError, SecretProvider
from property_focus.config import Settings

_LOGICAL_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


def _checked(logical_name: str) -> str:
    if not _LOGICAL_NAME.fullmatch(logical_name):
        raise ValueError(f"logical secret name must be lowercase snake case: {logical_name!r}")
    return logical_name


class DeploymentInjectedSecretProvider:
    """Reads values the deployment injected into the environment as ``PF_<LOGICAL_NAME>``.

    An unset or empty variable counts as not configured.
    """

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        """``environ`` defaults to ``os.environ``, read at each lookup."""
        self._environ = os.environ if environ is None else environ

    async def get(self, logical_name: str) -> str:
        variable = f"PF_{_checked(logical_name).upper()}"
        value = self._environ.get(variable)
        if not value:
            raise SecretNotFoundError(f"{logical_name}: environment variable {variable} is not set")
        register_secret(value)
        return value


class SecretsManagerClient(Protocol):
    """The part of the boto3 ``secretsmanager`` client this module uses."""

    def get_secret_value(self, *, SecretId: str) -> Mapping[str, Any]: ...  # noqa: N803


class ParameterStoreClient(Protocol):
    """The part of the boto3 ``ssm`` client this module uses."""

    def get_parameter(self, *, Name: str, WithDecryption: bool) -> Mapping[str, Any]: ...  # noqa: N803


class AwsSecretProvider:
    """Resolves a logical name from Secrets Manager, then from Parameter Store.

    The secret ID is ``<prefix>/<logical name>`` and the parameter name is
    ``/<prefix>/<logical name>`` (SecureString parameters are decrypted). A name found in
    neither store raises ``SecretNotFoundError``; other AWS errors, such as throttling or
    access denied, propagate as ``botocore`` ``ClientError``. Lookups are not cached, so a
    rotated value is seen on the next call.
    """

    def __init__(
        self,
        *,
        prefix: str,
        secrets_client: SecretsManagerClient,
        parameters_client: ParameterStoreClient,
    ) -> None:
        self._prefix = prefix
        self._secrets = secrets_client
        self._parameters = parameters_client

    async def get(self, logical_name: str) -> str:
        value = await asyncio.to_thread(self._lookup, _checked(logical_name))
        register_secret(value)
        return value

    def _lookup(self, logical_name: str) -> str:
        key = f"{self._prefix}/{logical_name}"
        try:
            secret = self._secrets.get_secret_value(SecretId=key)
        except ClientError as error:
            if _error_code(error) != "ResourceNotFoundException":
                raise
        else:
            if "SecretString" not in secret:
                raise ValueError(f"{logical_name}: binary secrets are not supported")
            return str(secret["SecretString"])

        try:
            parameter = self._parameters.get_parameter(Name=f"/{key}", WithDecryption=True)
        except ClientError as error:
            if _error_code(error) != "ParameterNotFound":
                raise
            raise SecretNotFoundError(
                f"{logical_name}: not in Secrets Manager or Parameter Store under {key}"
            ) from None
        return str(parameter["Parameter"]["Value"])


def _error_code(error: ClientError) -> str:
    return str(error.response.get("Error", {}).get("Code", ""))


def secret_provider_for(settings: Settings) -> SecretProvider:
    """Build the provider ``settings.secret_provider`` selects."""
    if settings.secret_provider == "aws":
        session = boto3.session.Session(region_name=settings.aws_region)
        return AwsSecretProvider(
            prefix=settings.secret_prefix,
            secrets_client=session.client("secretsmanager"),
            parameters_client=session.client("ssm"),
        )
    return DeploymentInjectedSecretProvider()
