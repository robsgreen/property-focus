"""Exercise each AWS operation the application uses against the pinned MiniStack and write
``docs/compatibility.md``: ``make compatibility-local``.

Each operation runs through the application's own adapter (``AwsSecretProvider`` and the
task launchers), with boto3 clients pointed at MiniStack, after setting up whatever the
operation needs (a secret, a function, a state machine, a cluster) under ``pf-compat``
names. An operation passes only when the adapter returns what it returns against AWS; any
error is recorded with its message. The AWS column stays "not run" until the operation has
been exercised against a real account (phase 9): an emulator pass never counts as an AWS
pass, and an unsupported emulator operation needs the fallback named in its row.

MiniStack must be running (``docker compose up -d ministack``). The emulator version and
digest come from the ``ministack`` image pinned in the root ``compose.yaml``.

Usage: ``uv run python backend/scripts/compatibility_report.py [--endpoint URL] [--out FILE]``.
Exit codes: 0 report written and every operation passed locally, 1 report written with at
least one local failure, 2 MiniStack unreachable or its image not pinned.
"""

import argparse
import asyncio
import io
import json
import re
import sys
import urllib.request
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from property_focus.adapters.secrets import AwsSecretProvider
from property_focus.application.ports import SecretNotFoundError, TaskInput
from property_focus.runtime.launcher import (
    FargateTaskLauncher,
    LambdaTaskLauncher,
    StepFunctionsTaskLauncher,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPOSITORY_ROOT / "compose.yaml"
DEFAULT_OUT = REPOSITORY_ROOT / "docs" / "compatibility.md"
DEFAULT_ENDPOINT = "http://localhost:4566"
REGION = "us-east-1"
# MiniStack accepts any credentials; these are placeholders, not secrets.
_EMULATOR_CREDENTIALS = {"aws_access_key_id": "ministack", "aws_secret_access_key": "ministack"}
_ROLE_ARN = "arn:aws:iam::000000000000:role/pf-compat"
_PINNED_IMAGE = re.compile(r"^\s*image:\s*(ministackorg/ministack:(\S+)@(sha256:[0-9a-f]{64}))\s*$")
AWS_NOT_RUN = "not run (no AWS account before phase 9)"


@dataclass(frozen=True)
class Operation:
    """One AWS operation the application calls, how it is exercised, and its fallback."""

    service: str
    name: str
    used_by: str
    exercise: Callable[["Emulator"], Awaitable[None]]
    fallback: str


@dataclass(frozen=True)
class Result:
    """How one operation fared against the emulator; ``detail`` explains a failure."""

    operation: Operation
    passed: bool
    detail: str


class Emulator:
    """boto3 clients for MiniStack, and the ``pf-compat`` resources the operations need."""

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint
        self.run_id = uuid.uuid4().hex[:8]

    def client(self, service: str) -> Any:
        """A boto3 client for ``service`` that talks to the emulator."""
        return boto3.client(
            service, endpoint_url=self._endpoint, region_name=REGION, **_EMULATOR_CREDENTIALS
        )

    def secret_provider(self) -> AwsSecretProvider:
        return AwsSecretProvider(
            prefix=f"pf-compat/{self.run_id}",
            secrets_client=self.client("secretsmanager"),
            parameters_client=self.client("ssm"),
        )


def _task_input() -> TaskInput:
    """A synthetic task envelope; nothing runs it, only the launch API sees it."""
    return TaskInput(
        workflow_run_id=uuid.uuid4(),
        task_kind="compat-check",
        subject_type="property",
        subject_id=uuid.uuid4(),
        input_digest="0" * 64,
        application_version="compat",
        idempotency_key=f"compat:{uuid.uuid4()}",
        deadline=datetime.now(UTC) + timedelta(hours=1),
    )


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def secrets_manager_get_secret_value(emulator: Emulator) -> None:
    provider = emulator.secret_provider()
    emulator.client("secretsmanager").create_secret(
        Name=f"pf-compat/{emulator.run_id}/sample_secret", SecretString="emulated-value"
    )
    expect(await provider.get("sample_secret") == "emulated-value", "wrong secret value")


async def ssm_get_parameter(emulator: Emulator) -> None:
    provider = emulator.secret_provider()
    emulator.client("ssm").put_parameter(
        Name=f"/pf-compat/{emulator.run_id}/sample_parameter",
        Value="emulated-value",
        Type="SecureString",
    )
    expect(await provider.get("sample_parameter") == "emulated-value", "wrong parameter value")
    # A name in neither store must read as "not configured", which needs both stores'
    # not-found error codes to match AWS.
    try:
        await provider.get("missing_name")
    except SecretNotFoundError:
        return
    raise AssertionError("a missing name did not raise SecretNotFoundError")


async def lambda_invoke(emulator: Emulator) -> None:
    client = emulator.client("lambda")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("handler.py", "def handler(event, context):\n    return {}\n")
    name = f"pf-compat-{emulator.run_id}"
    client.create_function(
        FunctionName=name,
        Runtime="python3.13",
        Role=_ROLE_ARN,
        Handler="handler.handler",
        Code={"ZipFile": archive.getvalue()},
    )
    reference = await LambdaTaskLauncher(client, name).launch(_task_input())
    expect(reference.startswith("lambda:"), f"unexpected launch reference {reference}")


async def stepfunctions_start_execution(emulator: Emulator) -> None:
    client = emulator.client("stepfunctions")
    definition = {"StartAt": "Done", "States": {"Done": {"Type": "Pass", "End": True}}}
    machine = client.create_state_machine(
        name=f"pf-compat-{emulator.run_id}", definition=json.dumps(definition), roleArn=_ROLE_ARN
    )["stateMachineArn"]
    launcher = StepFunctionsTaskLauncher(client, machine)
    task = _task_input()
    first = await launcher.launch(task)
    # Launching the same envelope again must reuse the execution (ExecutionAlreadyExists).
    second = await launcher.launch(task)
    expect(first == second, f"a repeated launch started {second}, not {first}")


async def ecs_run_task(emulator: Emulator) -> None:
    client = emulator.client("ecs")
    cluster = f"pf-compat-{emulator.run_id}"
    client.create_cluster(clusterName=cluster)
    client.register_task_definition(
        family=cluster,
        requiresCompatibilities=["FARGATE"],
        networkMode="awsvpc",
        cpu="256",
        memory="512",
        containerDefinitions=[{"name": "task", "image": "busybox", "essential": True}],
    )
    launcher = FargateTaskLauncher(
        client,
        cluster=cluster,
        task_definition=cluster,
        container_name="task",
        subnets=["subnet-pf-compat"],
        security_groups=["sg-pf-compat"],
    )
    reference = await launcher.launch(_task_input())
    expect(":task/" in reference, f"unexpected task ARN {reference}")


OPERATIONS = [
    Operation(
        "Secrets Manager",
        "GetSecretValue",
        "`AwsSecretProvider` (`adapters/secrets.py`)",
        secrets_manager_get_secret_value,
        "botocore Stubber cases in `backend/tests/unit/test_secrets.py`; verify on AWS beta",
    ),
    Operation(
        "Systems Manager Parameter Store",
        "GetParameter (SecureString, ParameterNotFound)",
        "`AwsSecretProvider` (`adapters/secrets.py`)",
        ssm_get_parameter,
        "botocore Stubber cases in `backend/tests/unit/test_secrets.py`; verify on AWS beta",
    ),
    Operation(
        "Lambda",
        "Invoke (InvocationType Event)",
        "`LambdaTaskLauncher` (`runtime/launcher.py`)",
        lambda_invoke,
        "botocore Stubber cases in `backend/tests/unit/test_workflow_runs.py`; verify on AWS beta",
    ),
    Operation(
        "Step Functions",
        "StartExecution (ExecutionAlreadyExists reuse)",
        "`StepFunctionsTaskLauncher` (`runtime/launcher.py`)",
        stepfunctions_start_execution,
        "botocore Stubber cases in `backend/tests/unit/test_workflow_runs.py`; verify on AWS beta",
    ),
    Operation(
        "ECS",
        "RunTask (FARGATE launch type)",
        "`FargateTaskLauncher` (`runtime/launcher.py`)",
        ecs_run_task,
        "botocore Stubber cases in `backend/tests/unit/test_workflow_runs.py`; verify on AWS beta",
    ),
]


def pinned_ministack() -> tuple[str, str]:
    """The (version, digest) of the ``ministack`` image pinned in the root compose file.

    Raises ``LookupError`` when the image is not pinned by tag and digest.
    """
    for line in COMPOSE_FILE.read_text(encoding="utf-8").splitlines():
        match = _PINNED_IMAGE.match(line)
        if match:
            return match.group(2), match.group(3)
    raise LookupError(f"{COMPOSE_FILE.name} pins no ministackorg/ministack image by digest")


async def exercise(operation: Operation, emulator: Emulator) -> Result:
    try:
        await operation.exercise(emulator)
    except (AssertionError, ClientError, BotoCoreError, KeyError, ValueError) as error:
        return Result(operation, False, f"{type(error).__name__}: {error}".replace("|", "/"))
    return Result(operation, True, "")


def render(results: list[Result], version: str, digest: str) -> str:
    """The Markdown report for ``results``; identical results render identical text."""
    rows = [
        f"| {r.operation.service} | {r.operation.name} | {r.operation.used_by} | "
        f"MiniStack {version} | {'pass' if r.passed else 'fail: ' + r.detail} | "
        f"{AWS_NOT_RUN} | {r.operation.fallback} |"
        for r in results
    ]
    unsupported = [
        f"- {r.operation.service} {r.operation.name}: {r.detail}" for r in results if not r.passed
    ]
    return "\n".join(
        [
            "# AWS Compatibility Inventory",
            "",
            "Generated by `make compatibility-local` (`backend/scripts/compatibility_report.py`);",
            "do not edit by hand. Each operation runs through the application's own adapter",
            "against the MiniStack emulator pinned in the root `compose.yaml` (D19).",
            "",
            f"Pinned emulator: `ministackorg/ministack:{version}@{digest}`",
            "",
            "A local pass shows the emulator behaves as the adapter expects; it never counts as",
            "an AWS pass. The AWS column records a run against a real account, which starts in",
            "phase 9 (A4). An operation the emulator does not support relies on its fallback",
            "until it passes on AWS.",
            "",
            "| Service | Operation | Used by | Emulator | Local result | AWS result | Fallback |",
            "|---|---|---|---|---|---|---|",
            *rows,
            "",
            "## Unsupported operations",
            "",
            *(unsupported or ["None: every operation above passed against the pinned emulator."]),
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="MiniStack endpoint URL")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="report to write")
    arguments = parser.parse_args()

    try:
        version, digest = pinned_ministack()
        urllib.request.urlopen(f"{arguments.endpoint}/_ministack/health", timeout=5).close()
    except (LookupError, OSError) as error:
        print(f"compatibility-local: {error}", file=sys.stderr)
        return 2

    emulator = Emulator(arguments.endpoint)
    results = [asyncio.run(exercise(operation, emulator)) for operation in OPERATIONS]
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(render(results, version, digest), encoding="utf-8")
    for result in results:
        outcome = "pass" if result.passed else f"FAIL {result.detail}"
        print(f"{result.operation.service} {result.operation.name}: {outcome}")
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
