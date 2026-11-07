"""``TaskLauncher`` implementations: where a task's execution starts.

- ``SubprocessTaskLauncher`` (dogfood, local development): a detached
  ``property-focus run-task`` process on the same host.
- ``LambdaTaskLauncher``: an asynchronous invocation of the task Lambda, whose handler is
  ``runtime/lambda_handler.py``.
- ``StepFunctionsTaskLauncher``: an execution of a state machine, named after the
  envelope's idempotency key so a duplicate delivery of one launch starts nothing new.
- ``FargateTaskLauncher``: an ECS RunTask of the task image, for browser and heavy work.

Every execution ends in ``runtime/run_task.py``. Launchers only start work; the task's
outcome is recorded on its workflow run. AWS refusals worth retrying (throttling, service
errors) raise ``CodedError`` ``rate_limited`` or ``dependency_transient``; other AWS errors
propagate as ``botocore`` ``ClientError``.
"""

import asyncio
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from property_focus.application.ports import TaskInput, TaskLauncher
from property_focus.config import Settings
from property_focus.domain.errors import CodedError

# AWS error codes meaning "slow down" across Lambda, Step Functions and ECS.
_THROTTLING_CODES = frozenset(
    {
        "Throttling",
        "ThrottlingException",
        "ThrottledException",
        "TooManyRequestsException",
        "RequestLimitExceeded",
        "ProvisionedThroughputExceededException",
    }
)
# Codes for a temporarily unavailable service, beyond any 5xx status.
_TRANSIENT_CODES = frozenset(
    {"ServiceException", "ServiceUnavailable", "InternalFailure", "ServerException"}
)
# Step Functions execution names: at most 80 of these characters.
_EXECUTION_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def task_command(command_prefix: Sequence[str], task: TaskInput) -> list[str]:
    """The ``run-task`` command line that runs ``task``, after ``command_prefix``."""
    return [*command_prefix, task.task_kind, "--task-input", task.model_dump_json()]


def launch_failure(error: ClientError) -> CodedError | None:
    """Classify an AWS error from starting an execution.

    Returns ``rate_limited`` for throttling, ``dependency_transient`` for a service error,
    and None for anything else (such as access denied or a missing resource), which
    retrying will not fix.
    """
    details = error.response.get("Error", {})
    code = str(details.get("Code", ""))
    status = int(error.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
    if code in _THROTTLING_CODES:
        return CodedError("rate_limited", "The task service is throttling new executions.")
    if code in _TRANSIENT_CODES or status >= 500:
        return CodedError("dependency_transient", "The task service is temporarily unavailable.")
    return None


async def _call_aws(call: Any, **parameters: Any) -> Any:
    """Run one blocking boto3 call off the event loop, classifying retryable refusals."""
    try:
        return await asyncio.to_thread(call, **parameters)
    except ClientError as error:
        failure = launch_failure(error)
        if failure is None:
            raise
        raise failure from error


class SubprocessTaskLauncher:
    """Starts each task as its own ``run-task`` process and returns at once.

    The process runs in a new session, so it outlives the launching process (such as a
    scheduler invocation that exits right after the sweep). Its output goes to the
    launcher's own standard output and error.
    """

    def __init__(
        self,
        command_prefix: Sequence[str] = (sys.executable, "-m", "property_focus.cli", "run-task"),
        *,
        env: Mapping[str, str] | None = None,
    ) -> None:
        """``env`` is the child's whole environment; None inherits this process's."""
        self._command_prefix = tuple(command_prefix)
        self._env = None if env is None else dict(env)

    async def launch(self, task: TaskInput) -> str:
        process = subprocess.Popen(
            task_command(self._command_prefix, task),
            stdin=subprocess.DEVNULL,
            env=self._env,
            start_new_session=True,
        )
        return f"pid:{process.pid}"


class LambdaClient(Protocol):
    """The part of the boto3 ``lambda`` client this module uses."""

    def invoke(
        self,
        *,
        FunctionName: str,  # noqa: N803
        InvocationType: str,  # noqa: N803
        Payload: bytes,  # noqa: N803
    ) -> Mapping[str, Any]: ...


class LambdaTaskLauncher:
    """Invokes the task Lambda asynchronously with ``{"task_input": <TaskInput>}``.

    Lambda retries a failed asynchronous invocation itself; ``run_task`` records failures
    on the run instead of raising, so those retries only cover crashed invocations.
    """

    def __init__(self, client: LambdaClient, function_name: str) -> None:
        self._client = client
        self._function_name = function_name

    async def launch(self, task: TaskInput) -> str:
        payload = json.dumps({"task_input": task.model_dump(mode="json")}).encode()
        response = await _call_aws(
            self._client.invoke,
            FunctionName=self._function_name,
            InvocationType="Event",
            Payload=payload,
        )
        return f"lambda:{response['ResponseMetadata']['RequestId']}"


class StepFunctionsClient(Protocol):
    """The part of the boto3 ``stepfunctions`` client this module uses."""

    def start_execution(
        self,
        *,
        stateMachineArn: str,  # noqa: N803
        name: str,
        input: str,
    ) -> Mapping[str, Any]: ...


class StepFunctionsTaskLauncher:
    """Starts a Standard state machine execution with the TaskInput as its input.

    The execution name comes from the envelope's idempotency key, so delivering the same
    envelope twice reuses the first execution instead of starting a second, while a
    relaunch of the run (a new envelope, with a new key) starts a new execution.
    """

    def __init__(self, client: StepFunctionsClient, state_machine_arn: str) -> None:
        self._client = client
        self._state_machine_arn = state_machine_arn

    async def launch(self, task: TaskInput) -> str:
        name = execution_name(task)
        try:
            response = await _call_aws(
                self._client.start_execution,
                stateMachineArn=self._state_machine_arn,
                name=name,
                input=task.model_dump_json(),
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") != "ExecutionAlreadyExists":
                raise
            # arn:...:stateMachine:<machine> -> arn:...:execution:<machine>:<name>
            prefix = self._state_machine_arn.replace(":stateMachine:", ":execution:", 1)
            return f"{prefix}:{name}"
        return str(response["executionArn"])


def execution_name(task: TaskInput) -> str:
    """A valid, deterministic Step Functions execution name for ``task``."""
    return _EXECUTION_NAME_UNSAFE.sub("-", task.idempotency_key)[:80]


class EcsClient(Protocol):
    """The part of the boto3 ``ecs`` client this module uses."""

    def run_task(self, **parameters: Any) -> Mapping[str, Any]: ...


class FargateTaskLauncher:
    """Starts one Fargate task of ``task_definition`` running ``run-task`` for the envelope.

    The container's command is overridden with ``command_prefix`` followed by the
    ``run-task`` arguments.
    """

    def __init__(
        self,
        client: EcsClient,
        *,
        cluster: str,
        task_definition: str,
        container_name: str,
        subnets: Sequence[str],
        security_groups: Sequence[str],
        command_prefix: Sequence[str] = ("property-focus", "run-task"),
    ) -> None:
        self._client = client
        self._cluster = cluster
        self._task_definition = task_definition
        self._container_name = container_name
        self._network = {
            "awsvpcConfiguration": {
                "subnets": list(subnets),
                "securityGroups": list(security_groups),
                "assignPublicIp": "DISABLED",
            }
        }
        self._command_prefix = tuple(command_prefix)

    async def launch(self, task: TaskInput) -> str:
        response = await _call_aws(
            self._client.run_task,
            cluster=self._cluster,
            taskDefinition=self._task_definition,
            launchType="FARGATE",
            count=1,
            networkConfiguration=self._network,
            startedBy=str(task.workflow_run_id),
            overrides={
                "containerOverrides": [
                    {
                        "name": self._container_name,
                        "command": task_command(self._command_prefix, task),
                    }
                ]
            },
        )
        tasks = response.get("tasks") or []
        if not tasks:
            # ECS reports capacity and placement problems as failures, not errors.
            raise CodedError("dependency_transient", "Fargate did not start the task.")
        return str(tasks[0]["taskArn"])


def launcher_for(settings: Settings) -> TaskLauncher:
    """The launcher for ``settings.environment``.

    Development and dogfood start tasks as local processes. The AWS environments get their
    launch targets from the workflow stack, which is not configured yet, so they are
    refused with ``NotImplementedError``.
    """
    if settings.environment in ("development", "dogfood"):
        return SubprocessTaskLauncher()
    raise NotImplementedError(
        f"no task launch targets are configured for PF_ENVIRONMENT={settings.environment}"
    )
