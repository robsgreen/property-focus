"""Workflow run rules: state transitions, leases and fencing, idempotency, failures, launch."""

import asyncio
import itertools
import json
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]
from botocore.stub import Stubber  # type: ignore[import-untyped]
from pydantic import ValidationError

from property_focus.adapters.db.models import WORKFLOW_STATES, WorkflowRun
from property_focus.application.ports import TaskInput
from property_focus.application.workflows import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    StaleLeaseError,
    check_idempotent_replay,
    check_transition,
    downstream_run_id,
    ensure_lease_current,
    failure_effect,
    input_digest,
    may_acquire,
    next_launch,
    task_input_for,
)
from property_focus.domain.errors import CodedError, RetryPolicy, UnknownErrorCodeError
from property_focus.runtime.launcher import (
    FargateTaskLauncher,
    LambdaTaskLauncher,
    StepFunctionsTaskLauncher,
    execution_name,
    launch_failure,
    task_command,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
DEADLINE = NOW + timedelta(hours=1)
STATE_MACHINE = "arn:aws:states:us-east-1:111122223333:stateMachine:analyze"

# The edges of the plan's workflow state diagram.
DIAGRAM_EDGES = {
    ("QUEUED", "RUNNING"),
    ("RUNNING", "COMPLETE"),
    ("RUNNING", "PARTIAL"),
    ("RUNNING", "WAITING_FOR_USER"),
    ("RUNNING", "FAILED"),
    ("WAITING_FOR_USER", "QUEUED"),
    ("PARTIAL", "QUEUED"),
    ("FAILED", "QUEUED"),
    ("QUEUED", "CANCELLED"),
    ("RUNNING", "CANCELLED"),
}
OTHER_PAIRS = sorted(set(itertools.product(WORKFLOW_STATES, repeat=2)) - DIAGRAM_EDGES)


def task_input(**changes: Any) -> TaskInput:
    values: dict[str, Any] = {
        "workflow_run_id": uuid.UUID("6f0f8a8e-3a52-4c1e-9d0e-4b1f6a7c2d10"),
        "task_kind": "parse-email",
        "subject_type": "ingest_message",
        "subject_id": uuid.UUID("0d6f3b9c-5a1e-4f7b-8c2d-9e0a1b2c3d4e"),
        "input_digest": "abc123",
        "application_version": "0.1.0",
        "idempotency_key": "6f0f8a8e-3a52-4c1e-9d0e-4b1f6a7c2d10.0",
        "deadline": DEADLINE,
    }
    return TaskInput(**(values | changes))


def queued_run() -> WorkflowRun:
    run_input = {"subject_type": "ingest_message", "subject_id": str(uuid.uuid4())}
    return WorkflowRun(
        id=uuid.uuid4(),
        kind="parse-email",
        input=run_input,
        input_digest=input_digest(run_input),
        state="QUEUED",
        fencing_token=0,
        launch_count=0,
        deadline_at=DEADLINE,
    )


class FakeStepFunctions:
    """Starts each execution name once, like Step Functions; a repeat raises
    ``ExecutionAlreadyExists``."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self._lock = threading.Lock()

    def start_execution(self, *, stateMachineArn: str, name: str, input: str) -> dict[str, Any]:  # noqa: N803
        with self._lock:
            if name in self.started:
                raise ClientError(
                    {"Error": {"Code": "ExecutionAlreadyExists", "Message": name}},
                    "StartExecution",
                )
            self.started.append(name)
        prefix = stateMachineArn.replace(":stateMachine:", ":execution:", 1)
        return {"executionArn": f"{prefix}:{name}"}


def aws_client(service: str) -> Any:
    # Static dummy credentials keep botocore from searching the machine for real ones.
    return boto3.session.Session(
        aws_access_key_id="dummy", aws_secret_access_key="dummy", region_name="us-east-1"
    ).client(service)


@pytest.fixture
def stubbed() -> Iterator[Any]:
    stubbers: list[Stubber] = []

    def stub(service: str) -> tuple[Any, Stubber]:
        client = aws_client(service)
        stubber = Stubber(client)
        stubber.activate()
        stubbers.append(stubber)
        return client, stubber

    yield stub
    for stubber in stubbers:
        stubber.assert_no_pending_responses()
        stubber.deactivate()


class TestStateTransitions:
    def test_every_state_has_a_transition_entry(self) -> None:
        assert set(ALLOWED_TRANSITIONS) == set(WORKFLOW_STATES)

    @pytest.mark.parametrize(("current", "target"), sorted(DIAGRAM_EDGES))
    def test_diagram_edges_are_allowed(self, current: str, target: str) -> None:
        check_transition(current, target)

    @pytest.mark.parametrize(("current", "target"), OTHER_PAIRS)
    def test_every_other_change_is_rejected(self, current: str, target: str) -> None:
        with pytest.raises(InvalidTransitionError):
            check_transition(current, target)


class TestLeasesAndFencing:
    @pytest.mark.parametrize(
        ("state", "lease_expires_at", "expected"),
        [
            ("QUEUED", None, True),
            ("RUNNING", NOW - timedelta(seconds=1), True),  # crashed holder or retry due
            ("RUNNING", NOW, True),
            ("RUNNING", NOW + timedelta(minutes=1), False),  # held by another task
            ("COMPLETE", None, False),
            ("PARTIAL", None, False),
            ("FAILED", None, False),
            ("CANCELLED", None, False),
            ("WAITING_FOR_USER", None, False),
        ],
    )
    def test_who_may_take_the_lease(
        self, state: str, lease_expires_at: datetime | None, expected: bool
    ) -> None:
        assert may_acquire(state, lease_expires_at, NOW) is expected

    def test_the_current_holder_may_complete(self) -> None:
        ensure_lease_current("RUNNING", 3, 3)

    def test_an_older_fencing_token_is_stale(self) -> None:
        with pytest.raises(StaleLeaseError):
            ensure_lease_current("RUNNING", 4, 3)

    @pytest.mark.parametrize("state", ["COMPLETE", "CANCELLED", "FAILED"])
    def test_a_run_that_left_running_rejects_its_holder(self, state: str) -> None:
        with pytest.raises(StaleLeaseError):
            ensure_lease_current(state, 3, 3)


class TestIdempotency:
    def test_digest_ignores_key_order(self) -> None:
        assert input_digest({"a": 1, "b": [2]}) == input_digest({"b": [2], "a": 1})
        assert input_digest({"a": 1}) != input_digest({"a": 2})

    def test_same_key_and_request_is_a_replay(self) -> None:
        check_idempotent_replay("digest-1", "digest-1")

    def test_same_key_with_a_different_request_conflicts(self) -> None:
        with pytest.raises(CodedError) as raised:
            check_idempotent_replay("digest-1", "digest-2")
        assert raised.value.code == "idempotency_conflict"
        assert raised.value.error.failure_class == "INVALID_INPUT"

    def test_downstream_run_id_is_stable_per_outbox_event(self) -> None:
        event_id = uuid.uuid4()
        assert downstream_run_id(event_id) == downstream_run_id(event_id)
        assert downstream_run_id(event_id) != downstream_run_id(uuid.uuid4())


class TestFailureEffect:
    def effect(self, code: str, attempts_made: int = 1) -> Any:
        return failure_effect(
            CodedError(code, "safe"),
            attempts_made=attempts_made,
            now=NOW,
            deadline=DEADLINE,
            policy=RetryPolicy(),
        )

    def test_transient_failure_schedules_a_retry(self) -> None:
        effect = self.effect("rate_limited")
        assert effect.run_state == "RUNNING"
        assert NOW < effect.next_attempt_at < DEADLINE

    def test_transient_failure_stops_after_the_attempt_cap(self) -> None:
        assert self.effect("timeout", attempts_made=4).run_state == "FAILED"

    def test_reconciliation_codes_are_not_retried(self) -> None:
        assert self.effect("provider_outcome_unknown").run_state == "FAILED"

    def test_auth_required_waits_for_a_person(self) -> None:
        effect = self.effect("auth_required")
        assert effect.run_state == "WAITING_FOR_USER"
        assert effect.next_attempt_at is None

    @pytest.mark.parametrize("code", ["schema_invalid", "invalid_input", "internal_defect"])
    def test_non_transient_failures_fail_the_run(self, code: str) -> None:
        assert self.effect(code).run_state == "FAILED"

    def test_coded_errors_need_a_registered_code(self) -> None:
        with pytest.raises(UnknownErrorCodeError):
            CodedError("made_up", "safe")


class TestTaskInput:
    def test_round_trips_through_json(self) -> None:
        task = task_input()
        assert TaskInput.model_validate_json(task.model_dump_json()) == task

    def test_rejects_a_tenant_or_scope_field(self) -> None:
        payload = json.loads(task_input().model_dump_json()) | {"tenant_id": str(uuid.uuid4())}
        with pytest.raises(ValidationError):
            TaskInput.model_validate(payload)

    def test_requires_a_timezone_aware_deadline(self) -> None:
        with pytest.raises(ValidationError):
            task_input(deadline=datetime(2026, 11, 2, 12, 0))


class TestLaunchers:
    def test_run_task_command_line(self) -> None:
        task = task_input()
        command = task_command(["property-focus", "run-task"], task)
        assert command[:4] == ["property-focus", "run-task", "parse-email", "--task-input"]
        assert TaskInput.model_validate_json(command[4]) == task

    @pytest.mark.parametrize(
        ("code", "status", "expected"),
        [
            ("ThrottlingException", 400, "rate_limited"),
            ("TooManyRequestsException", 429, "rate_limited"),
            ("ServiceException", 500, "dependency_transient"),
            ("Anything", 503, "dependency_transient"),
        ],
    )
    def test_retryable_aws_refusals_are_coded(self, code: str, status: int, expected: str) -> None:
        error = ClientError(
            {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, "Invoke"
        )
        failure = launch_failure(error)
        assert failure is not None
        assert failure.code == expected

    def test_other_aws_errors_are_not_coded(self) -> None:
        error = ClientError(
            {
                "Error": {"Code": "AccessDeniedException"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            },
            "Invoke",
        )
        assert launch_failure(error) is None

    def test_lambda_launch_invokes_asynchronously_with_the_envelope(self, stubbed: Any) -> None:
        client, stubber = stubbed("lambda")
        task = task_input()
        payload = json.dumps({"task_input": task.model_dump(mode="json")}).encode()
        stubber.add_response(
            "invoke",
            {"StatusCode": 202, "ResponseMetadata": {"RequestId": "req-1"}},
            {"FunctionName": "tasks", "InvocationType": "Event", "Payload": payload},
        )
        launcher = LambdaTaskLauncher(client, "tasks")
        assert asyncio.run(launcher.launch(task)) == "lambda:req-1"

    def test_lambda_throttling_raises_rate_limited(self, stubbed: Any) -> None:
        client, stubber = stubbed("lambda")
        stubber.add_client_error("invoke", "TooManyRequestsException", http_status_code=429)
        with pytest.raises(CodedError) as raised:
            asyncio.run(LambdaTaskLauncher(client, "tasks").launch(task_input()))
        assert raised.value.code == "rate_limited"

    def test_step_functions_reuses_the_execution_of_a_duplicate_launch(self, stubbed: Any) -> None:
        client, stubber = stubbed("stepfunctions")
        task = task_input()
        stubber.add_client_error("start_execution", "ExecutionAlreadyExists")
        launched = asyncio.run(StepFunctionsTaskLauncher(client, STATE_MACHINE).launch(task))
        name = "6f0f8a8e-3a52-4c1e-9d0e-4b1f6a7c2d10-0"
        assert launched == f"arn:aws:states:us-east-1:111122223333:execution:analyze:{name}"

    def test_concurrent_deliveries_of_one_envelope_start_one_execution(self) -> None:
        client = FakeStepFunctions()
        launcher = StepFunctionsTaskLauncher(client, STATE_MACHINE)
        task = next_launch(queued_run())

        async def deliver_twice() -> list[str]:
            return list(await asyncio.gather(launcher.launch(task), launcher.launch(task)))

        first, second = asyncio.run(deliver_twice())
        assert client.started == [execution_name(task)]
        assert first == second

    def test_a_relaunch_without_a_lease_grant_starts_a_new_execution(self) -> None:
        # The first execution failed before taking the lease, so the run and its fencing
        # token are unchanged when repair relaunches it.
        client = FakeStepFunctions()
        launcher = StepFunctionsTaskLauncher(client, STATE_MACHINE)
        run = queued_run()

        first = asyncio.run(launcher.launch(next_launch(run)))
        relaunched = asyncio.run(launcher.launch(next_launch(run)))

        assert first != relaunched
        assert len(client.started) == 2
        assert run.fencing_token == 0

    def test_the_envelope_names_the_latest_launch(self) -> None:
        run = queued_run()
        launched = next_launch(run)
        assert task_input_for(run) == launched
        assert launched.idempotency_key == f"{run.id}.1"
        assert launched.deadline == DEADLINE

    def test_fargate_placement_failure_is_transient(self, stubbed: Any) -> None:
        client, stubber = stubbed("ecs")
        stubber.add_response("run_task", {"tasks": [], "failures": [{"reason": "RESOURCE:CPU"}]})
        launcher = FargateTaskLauncher(
            client,
            cluster="tasks",
            task_definition="browser:1",
            container_name="task",
            subnets=["subnet-1"],
            security_groups=["sg-1"],
        )
        with pytest.raises(CodedError) as raised:
            asyncio.run(launcher.launch(task_input()))
        assert raised.value.code == "dependency_transient"
