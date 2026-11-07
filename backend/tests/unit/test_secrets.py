"""Secret providers: lookup by logical name in each store, and selection by configuration."""

import asyncio
from collections.abc import Iterator
from typing import Any

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from botocore.stub import Stubber  # type: ignore[import-untyped]

from property_focus.adapters.secrets import (
    AwsSecretProvider,
    DeploymentInjectedSecretProvider,
    secret_provider_for,
)
from property_focus.application.ports import SecretNotFoundError, SecretProvider
from property_focus.config import Settings

PREFIX = "property-focus/beta"


def aws_client(service: str) -> Any:
    # Static dummy credentials keep botocore from searching the machine for real ones.
    return boto3.session.Session(
        aws_access_key_id="dummy", aws_secret_access_key="dummy", region_name="us-east-1"
    ).client(service)


@pytest.fixture
def secrets_manager() -> Iterator[tuple[Any, Stubber]]:
    client = aws_client("secretsmanager")
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def parameter_store() -> Iterator[tuple[Any, Stubber]]:
    client = aws_client("ssm")
    with Stubber(client) as stubber:
        yield client, stubber
        stubber.assert_no_pending_responses()


@pytest.fixture
def provider(
    secrets_manager: tuple[Any, Stubber], parameter_store: tuple[Any, Stubber]
) -> AwsSecretProvider:
    return AwsSecretProvider(
        prefix=PREFIX, secrets_client=secrets_manager[0], parameters_client=parameter_store[0]
    )


def get(provider: SecretProvider, logical_name: str) -> str:
    return asyncio.run(provider.get(logical_name))


class TestDeploymentInjectedSecretProvider:
    def test_reads_the_prefixed_upper_case_variable(self) -> None:
        provider = DeploymentInjectedSecretProvider({"PF_MLS_MAILBOX_PASSWORD": "synthetic"})

        assert get(provider, "mls_mailbox_password") == "synthetic"

    @pytest.mark.parametrize("environ", [{}, {"PF_OPENAI_API_KEY": ""}])
    def test_unset_or_empty_variable_is_not_found(self, environ: dict[str, str]) -> None:
        provider = DeploymentInjectedSecretProvider(environ)

        with pytest.raises(SecretNotFoundError, match="PF_OPENAI_API_KEY"):
            get(provider, "openai_api_key")

    @pytest.mark.parametrize("name", ["OpenAI_Key", "openai-api-key", "../etc", "", "_x"])
    def test_rejects_names_that_are_not_lowercase_snake_case(self, name: str) -> None:
        with pytest.raises(ValueError, match="lowercase snake case"):
            get(DeploymentInjectedSecretProvider({}), name)


class TestAwsSecretProvider:
    def test_reads_secrets_manager_first(
        self, provider: AwsSecretProvider, secrets_manager: tuple[Any, Stubber]
    ) -> None:
        secrets_manager[1].add_response(
            "get_secret_value",
            {"SecretString": "synthetic"},
            {"SecretId": f"{PREFIX}/openai_api_key"},
        )

        assert get(provider, "openai_api_key") == "synthetic"

    def test_falls_back_to_decrypted_parameter_store(
        self,
        provider: AwsSecretProvider,
        secrets_manager: tuple[Any, Stubber],
        parameter_store: tuple[Any, Stubber],
    ) -> None:
        secrets_manager[1].add_client_error("get_secret_value", "ResourceNotFoundException")
        parameter_store[1].add_response(
            "get_parameter",
            {"Parameter": {"Name": f"/{PREFIX}/mls_mailbox_host", "Value": "imap.test"}},
            {"Name": f"/{PREFIX}/mls_mailbox_host", "WithDecryption": True},
        )

        assert get(provider, "mls_mailbox_host") == "imap.test"

    def test_name_in_neither_store_is_not_found(
        self,
        provider: AwsSecretProvider,
        secrets_manager: tuple[Any, Stubber],
        parameter_store: tuple[Any, Stubber],
    ) -> None:
        secrets_manager[1].add_client_error("get_secret_value", "ResourceNotFoundException")
        parameter_store[1].add_client_error("get_parameter", "ParameterNotFound")

        with pytest.raises(SecretNotFoundError, match="missing_name"):
            get(provider, "missing_name")

    def test_other_aws_errors_propagate_without_falling_back(
        self, provider: AwsSecretProvider, secrets_manager: tuple[Any, Stubber]
    ) -> None:
        secrets_manager[1].add_client_error("get_secret_value", "AccessDeniedException")

        with pytest.raises(ClientError, match="AccessDeniedException"):
            get(provider, "openai_api_key")

    def test_rejects_invalid_names_before_calling_aws(self, provider: AwsSecretProvider) -> None:
        with pytest.raises(ValueError, match="lowercase snake case"):
            get(provider, "../other-app/key")


class TestSelection:
    def test_dogfood_uses_deployment_injected_values(self) -> None:
        settings = Settings.model_validate(
            {"environment": "dogfood", "oidc_issuer": "https://auth.example.test/realms/pf"}
        )

        assert isinstance(secret_provider_for(settings), DeploymentInjectedSecretProvider)

    def test_beta_uses_aws(self) -> None:
        settings = Settings.model_validate(
            {
                "environment": "beta",
                "oidc_issuer": "https://auth.example.test/realms/pf",
                "aws_region": "us-east-1",
            }
        )

        assert isinstance(secret_provider_for(settings), AwsSecretProvider)
