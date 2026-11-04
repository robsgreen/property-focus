"""Task runtime against a real database: launchers, outbox recovery, duplicates and leases.

Run as a script, this module is the ``property-focus`` command line with the test task
kinds registered, so the dogfood launcher's child process runs the same handlers.
"""

import asyncio
import json
import os
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import URL, Engine, create_engine, select, text
from sqlalchemy.pool import NullPool

from property_focus import cli
from property_focus.adapters.db.models import (
    AuditEvent,
    IdempotencyRecord,
    OutboxEvent,
    StepAttempt,
    WorkflowRun,
    WorkflowStep,
)
from property_focus.adapters.db.repositories import ServiceScope, TenantScope, UnitOfWork
from property_focus.adapters.secrets import DeploymentInjectedSecretProvider
from property_focus.application.ports import TaskInput
from property_focus.application.workflows import (
    DEFAULT_RUN_DEADLINE,
    IDEMPOTENCY_TTL,
    Admission,
    Declined,
    InvalidTransitionError,
    Lease,
    NewEvent,
    OutboxPublisher,
    acquire_lease,
    admit_run,
    cancel_run,
    downstream_run_id,
    prune_idempotency_records,
    record_event,
    task_input_for,
    utc_now,
)
from property_focus.config import Settings
from property_focus.domain.errors import CodedError, RetryPolicy
from property_focus.runtime import lambda_handler
from property_focus.runtime.launcher import LambdaTaskLauncher, SubprocessTaskLauncher
from property_focus.runtime.run_task import (
    TaskContext,
    TaskHandler,
    TaskResult,
    TaskRunOutcome,
    TaskRuntime,
    run_task,
)
from property_focus.runtime.scheduled import run_scheduled

NewTenant = Callable[[], TenantScope]

SUBJECT = uuid.UUID("3b8f2c1d-6e4a-4f0b-9c7d-1a2b3c4d5e6f")
RECORDED = "subject.recorded"
LEASE = timedelta(minutes=15)


async def record_subject(context: TaskContext) -> TaskResult:
    """The test task's domain result: one audit row in the run's tenant."""
    row = AuditEvent(
        id=uuid.uuid4(),
        principal_service=context.task.task_kind,
        action=RECORDED,
        target_type=context.task.subject_type,
        target_id=context.task.subject_id,
        metadata_={"input_digest": context.task.input_digest},
    )
    context.write(lambda uow: uow.repository(AuditEvent).add(row))
    return TaskResult(result_refs=[str(row.id)])


async def emit_downstream(context: TaskContext) -> TaskResult:
    """An upstream task: commits an event that launches ``record-subject``."""
    context.emit(
        NewEvent(
            event_type="subject.changed",
            aggregate_type=context.task.subject_type,
            aggregate_id=context.task.subject_id,
            aggregate_revision=1,
            destinations=("record-subject",),
        )
    )
    return TaskResult()


TASKS: Mapping[str, TaskHandler] = {
    "record-subject": record_subject,
    "emit-downstream": emit_downstream,
}


class SimulatedCrashError(BaseException):
    """Stands in for the process dying: nothing in the runtime catches it."""


class RecordingLauncher:
    """Collects launched envelopes; ``deliver`` then runs them, as an execution service would."""

    def __init__(self) -> None:
        self.launched: list[TaskInput] = []

    async def launch(self, task: TaskInput) -> str:
        self.launched.append(task)
        return f"recorded:{len(self.launched)}"

    def launched_runs(self) -> list[uuid.UUID]:
        return [task.workflow_run_id for task in self.launched]

    def deliver(self, runtime: TaskRuntime, run_id: uuid.UUID) -> list[TaskRunOutcome]:
        """Run every recorded launch of ``run_id`` and forget them."""
        mine = [task for task in self.launched if task.workflow_run_id == run_id]
        self.launched = [task for task in self.launched if task.workflow_run_id != run_id]
        return [asyncio.run(run_task(task, runtime)) for task in mine]


class CrashingLauncher:
    """Crashes the launching process before, or right after, a recorded launch."""

    def __init__(self, recorder: RecordingLauncher | None = None) -> None:
        self._recorder = recorder

    async def launch(self, task: TaskInput) -> str:
        if self._recorder is not None:
            await self._recorder.launch(task)
        raise SimulatedCrashError


class FakeLambdaClient:
    """Accepts asynchronous invocations and keeps their payloads for delivery."""

    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    def invoke(self, *, FunctionName: str, InvocationType: str, Payload: bytes) -> dict[str, Any]:  # noqa: N803
        assert InvocationType == "Event"
        self.payloads.append(Payload)
        return {"StatusCode": 202, "ResponseMetadata": {"RequestId": f"req-{len(self.payloads)}"}}


@pytest.fixture
def make_runtime(runtime_engine: Engine) -> Callable[..., TaskRuntime]:
    def make(
        launcher: Any = None,
        *,
        tasks: Mapping[str, TaskHandler] = TASKS,
        clock: Callable[[], datetime] = utc_now,
    ) -> TaskRuntime:
        return TaskRuntime(
            engine=runtime_engine,
            launcher=launcher or RecordingLauncher(),
            secrets=DeploymentInjectedSecretProvider({}),
            tasks=tasks,
            clock=clock,
            lease_duration=LEASE,
        )

    return make


def admit(
    engine: Engine,
    tenant: TenantScope,
    kind: str = "record-subject",
    *,
    key: str | None = None,
    subject: uuid.UUID = SUBJECT,
    now: datetime | None = None,
) -> uuid.UUID:
    with UnitOfWork(engine, tenant) as uow:
        admission = admit_run(
            uow,
            operation="test",
            idempotency_key=key or uuid.uuid4().hex,
            kind=kind,
            subject_type="property",
            subject_id=subject,
            now=now or utc_now(),
        )
    return admission.workflow_run_id


def task_for(engine: Engine, run_id: uuid.UUID) -> TaskInput:
    with UnitOfWork(engine, ServiceScope()) as uow:
        return task_input_for(uow.session.get_one(WorkflowRun, run_id))


def load_run(engine: Engine, run_id: uuid.UUID) -> WorkflowRun:
    with UnitOfWork(engine, ServiceScope()) as uow:
        return uow.session.get_one(WorkflowRun, run_id)


def load_step(engine: Engine, run_id: uuid.UUID) -> WorkflowStep:
    with UnitOfWork(engine, ServiceScope()) as uow:
        return uow.session.scalars(
            select(WorkflowStep).where(WorkflowStep.workflow_run_id == run_id)
        ).one()


def load_attempts(engine: Engine, run_id: uuid.UUID) -> list[StepAttempt]:
    with UnitOfWork(engine, ServiceScope()) as uow:
        return list(
            uow.session.scalars(
                select(StepAttempt)
                .join(WorkflowStep, StepAttempt.workflow_step_id == WorkflowStep.id)
                .where(WorkflowStep.workflow_run_id == run_id)
                .order_by(StepAttempt.attempt_number)
            )
        )


def results(engine: Engine, tenant: TenantScope, run_id: uuid.UUID) -> list[AuditEvent]:
    """The domain results the run committed: rows its ``result_refs`` name."""
    refs = [uuid.UUID(ref) for ref in load_run(engine, run_id).result_refs]
    with UnitOfWork(engine, tenant) as uow:
        return list(uow.session.scalars(select(AuditEvent).where(AuditEvent.id.in_(refs))))


def recorded_count(engine: Engine, tenant: TenantScope, subject: uuid.UUID) -> int:
    with UnitOfWork(engine, tenant) as uow:
        rows = uow.session.scalars(
            select(AuditEvent).where(AuditEvent.action == RECORDED, AuditEvent.target_id == subject)
        )
        return len(list(rows))


def committed_result(engine: Engine, tenant: TenantScope, run_id: uuid.UUID) -> dict[str, Any]:
    """Everything the run committed, without IDs and timestamps."""
    run, step = load_run(engine, run_id), load_step(engine, run_id)
    return {
        "run": (run.state, run.failure_class, run.failure_code, run.fencing_token),
        "step": (step.state, step.attempt_count, step.failure_code),
        "attempts": [attempt.outcome for attempt in load_attempts(engine, run_id)],
        "results": [
            (row.tenant_id, row.action, row.target_type, row.target_id, row.metadata_)
            for row in results(engine, tenant, run_id)
        ],
    }


def outbox_event_for(engine: Engine, upstream_run_id: uuid.UUID) -> OutboxEvent:
    """The single outbox event the upstream run's task wrote."""
    with UnitOfWork(engine, ServiceScope()) as uow:
        downstream_ids = uow.session.scalars(
            select(WorkflowRun.id).where(WorkflowRun.parent_run_id == upstream_run_id)
        ).all()
        events = [
            event
            for event in uow.session.scalars(select(OutboxEvent))
            if downstream_run_id(event.id) in downstream_ids
        ]
    assert len(events) == 1
    return events[0]


def later(delta: timedelta) -> Callable[[], datetime]:
    return lambda: utc_now() + delta


def wait_for_state(engine: Engine, run_id: uuid.UUID, state: str) -> None:
    deadline = time.monotonic() + 30
    while load_run(engine, run_id).state != state:
        assert time.monotonic() < deadline, f"run {run_id} did not reach {state}"
        time.sleep(0.1)


def test_dogfood_process_and_lambda_adapter_commit_identical_results(
    runtime_engine: Engine,
    settings: Settings,
    new_tenant: NewTenant,
    make_runtime: Callable[..., TaskRuntime],
) -> None:
    tenant = new_tenant()
    by_process, by_lambda = admit(runtime_engine, tenant), admit(runtime_engine, tenant)

    process_launcher = SubprocessTaskLauncher(
        [sys.executable, __file__, "run-task"],
        env={
            **os.environ,
            "PF_ENVIRONMENT": "development",
            "PF_DATABASE_URL": settings.database_url,
        },
    )
    asyncio.run(process_launcher.launch(task_for(runtime_engine, by_process)))

    client = FakeLambdaClient()
    asyncio.run(LambdaTaskLauncher(client, "tasks").launch(task_for(runtime_engine, by_lambda)))
    runtime = make_runtime()
    replies = [lambda_handler.handle(json.loads(payload), runtime) for payload in client.payloads]
    assert replies == [{"workflow_run_id": str(by_lambda), "outcome": "complete"}]

    wait_for_state(runtime_engine, by_process, "COMPLETE")
    expected = {
        "run": ("COMPLETE", None, None, 1),
        "step": ("COMPLETE", 1, None),
        "attempts": ["COMPLETE"],
        "results": [
            (
                tenant.tenant_id,  # the tenant comes from the trusted run
                RECORDED,
                "property",
                SUBJECT,
                {"input_digest": load_run(runtime_engine, by_lambda).input_digest},
            )
        ],
    }
    assert committed_result(runtime_engine, tenant, by_process) == expected
    assert committed_result(runtime_engine, tenant, by_lambda) == expected


def test_crash_between_commit_and_launch_is_launched_once_by_the_next_sweep(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant, subject = new_tenant(), uuid.uuid4()
    upstream = admit(runtime_engine, tenant, "emit-downstream", subject=subject)

    with pytest.raises(SimulatedCrashError):
        asyncio.run(run_task(task_for(runtime_engine, upstream), make_runtime(CrashingLauncher())))
    assert load_run(runtime_engine, upstream).state == "COMPLETE"
    event = outbox_event_for(runtime_engine, upstream)
    downstream = downstream_run_id(event.id)
    assert (event.state, load_run(runtime_engine, downstream).state) == ("PENDING", "QUEUED")

    recorder = RecordingLauncher()
    asyncio.run(run_scheduled("outbox-sweep", make_runtime(recorder)))
    assert downstream not in recorder.launched_runs()  # too recent: the committer's to launch

    asyncio.run(
        run_scheduled("outbox-sweep", make_runtime(recorder, clock=later(timedelta(seconds=31))))
    )
    assert recorder.deliver(make_runtime(), downstream) == ["complete"]
    assert outbox_event_for(runtime_engine, upstream).state == "PUBLISHED"
    assert recorded_count(runtime_engine, tenant, subject) == 1

    asyncio.run(
        run_scheduled("outbox-sweep", make_runtime(recorder, clock=later(timedelta(minutes=2))))
    )
    assert downstream not in recorder.launched_runs()


def test_crash_after_launch_before_acknowledge_keeps_one_result(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant, subject = new_tenant(), uuid.uuid4()
    upstream = admit(runtime_engine, tenant, "emit-downstream", subject=subject)
    recorder = RecordingLauncher()

    with pytest.raises(SimulatedCrashError):
        asyncio.run(
            run_task(task_for(runtime_engine, upstream), make_runtime(CrashingLauncher(recorder)))
        )
    event = outbox_event_for(runtime_engine, upstream)
    downstream = downstream_run_id(event.id)
    assert event.state == "PENDING"
    assert recorder.deliver(make_runtime(), downstream) == ["complete"]

    # The event still looks unlaunched, so the sweep launches it again.
    asyncio.run(
        run_scheduled("outbox-sweep", make_runtime(recorder, clock=later(timedelta(seconds=31))))
    )
    assert recorder.deliver(make_runtime(), downstream) == ["already_finished"]
    assert outbox_event_for(runtime_engine, upstream).state == "PUBLISHED"
    assert recorded_count(runtime_engine, tenant, subject) == 1


def test_duplicate_delivery_and_idempotent_admission(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant, subject = new_tenant(), uuid.uuid4()
    run_id = admit(runtime_engine, tenant, key="same-key", subject=subject)

    with UnitOfWork(runtime_engine, tenant) as uow:
        replay = admit_run(
            uow,
            operation="test",
            idempotency_key="same-key",
            kind="record-subject",
            subject_type="property",
            subject_id=subject,
            now=utc_now(),
        )
    assert (replay.workflow_run_id, replay.replayed) == (run_id, True)
    with pytest.raises(CodedError) as conflict, UnitOfWork(runtime_engine, tenant) as uow:
        admit_run(
            uow,
            operation="test",
            idempotency_key="same-key",
            kind="record-subject",
            subject_type="property",
            subject_id=uuid.uuid4(),
            now=utc_now(),
        )
    assert conflict.value.code == "idempotency_conflict"

    task = task_for(runtime_engine, run_id)
    runtime = make_runtime()
    outcomes = [asyncio.run(run_task(task, runtime)) for _ in range(2)]
    assert outcomes == ["complete", "already_finished"]
    assert recorded_count(runtime_engine, tenant, subject) == 1


def test_expired_lease_is_recovered_and_the_stale_holder_is_rejected(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant, subject = new_tenant(), uuid.uuid4()
    run_id = admit(runtime_engine, tenant, subject=subject)
    takeover_runtime = make_runtime(clock=later(LEASE + timedelta(minutes=1)))
    takeover: list[TaskRunOutcome] = []

    async def outlives_its_lease(context: TaskContext) -> TaskResult:
        # While this holder is still working, its lease expires and another task takes over.
        takeover.append(await run_task(context.task, takeover_runtime))
        return await record_subject(context)

    stale_runtime = make_runtime(tasks={"record-subject": outlives_its_lease})
    assert asyncio.run(run_task(task_for(runtime_engine, run_id), stale_runtime)) == "stale"

    assert takeover == ["complete"]
    run = load_run(runtime_engine, run_id)
    assert (run.state, run.fencing_token) == ("COMPLETE", 2)
    assert [a.outcome for a in load_attempts(runtime_engine, run_id)] == ["CANCELLED", "COMPLETE"]
    assert recorded_count(runtime_engine, tenant, subject) == 1


def test_repair_relaunches_a_run_whose_task_crashed(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant, subject = new_tenant(), uuid.uuid4()
    run_id = admit(runtime_engine, tenant, subject=subject)
    task = task_for(runtime_engine, run_id)
    # A task takes the lease and dies without reporting.
    acquire_lease(runtime_engine, task, now=utc_now(), lease_duration=LEASE, policy=RetryPolicy())

    recorder = RecordingLauncher()
    asyncio.run(run_scheduled("repair", make_runtime(recorder, clock=later(timedelta(minutes=1)))))
    assert run_id not in recorder.launched_runs()

    asyncio.run(
        run_scheduled("repair", make_runtime(recorder, clock=later(LEASE + timedelta(minutes=1))))
    )
    recovery_runtime = make_runtime(clock=later(LEASE + timedelta(minutes=1)))
    assert recorder.deliver(recovery_runtime, run_id) == ["complete"]
    assert load_step(runtime_engine, run_id).attempt_count == 2
    assert recorded_count(runtime_engine, tenant, subject) == 1


def test_failed_step_is_persisted_and_retried_through_the_policy(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant, subject = new_tenant(), uuid.uuid4()
    run_id = admit(runtime_engine, tenant, subject=subject)
    calls: list[int] = []

    async def throttled_once(context: TaskContext) -> TaskResult:
        calls.append(1)
        if len(calls) == 1:
            raise CodedError(
                "rate_limited",
                "The source asked us to slow down.",
                retry_after=timedelta(minutes=2),
            )
        return await record_subject(context)

    tasks = {"record-subject": throttled_once}
    before = utc_now()
    assert asyncio.run(run_task(task_for(runtime_engine, run_id), make_runtime(tasks=tasks))) == (
        "retry_scheduled"
    )

    run, step = load_run(runtime_engine, run_id), load_step(runtime_engine, run_id)
    assert (step.failure_class, step.failure_code, step.retryable) == (
        "TRANSIENT",
        "rate_limited",
        True,
    )
    assert step.safe_explanation == "The source asked us to slow down."
    assert (step.attempt_count, step.state) == (1, "QUEUED")
    assert step.correlation_id is not None
    assert step.correlation_id == run.correlation_id
    assert step.next_attempt_at is not None
    assert step.next_attempt_at >= before + timedelta(minutes=2)  # Retry-After is a floor
    assert (run.state, run.lease_expires_at) == ("RUNNING", step.next_attempt_at)
    assert [a.failure_code for a in load_attempts(runtime_engine, run_id)] == ["rate_limited"]

    # Not before the retry time; repair relaunches it once due.
    assert asyncio.run(run_task(task_for(runtime_engine, run_id), make_runtime(tasks=tasks))) == (
        "lease_held"
    )
    due = later(step.next_attempt_at - utc_now() + timedelta(seconds=1))
    recorder = RecordingLauncher()
    asyncio.run(run_scheduled("repair", make_runtime(recorder, clock=due)))
    assert recorder.deliver(make_runtime(tasks=tasks, clock=due), run_id) == ["complete"]
    assert load_step(runtime_engine, run_id).attempt_count == 2
    assert recorded_count(runtime_engine, tenant, subject) == 1


def test_unexpected_errors_fail_the_run_without_leaking_details(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant = new_tenant()
    run_id = admit(runtime_engine, tenant)

    async def broken(context: TaskContext) -> TaskResult:
        raise ValueError("private detail from the source page")

    runtime = make_runtime(tasks={"record-subject": broken})
    assert asyncio.run(run_task(task_for(runtime_engine, run_id), runtime)) == "failed"

    run, step = load_run(runtime_engine, run_id), load_step(runtime_engine, run_id)
    assert (run.state, run.failure_class, run.failure_code) == (
        "FAILED",
        "BUG_UNKNOWN",
        "internal_defect",
    )
    assert (step.retryable, step.next_attempt_at) == (False, None)
    assert step.safe_explanation is not None
    assert "private detail" not in step.safe_explanation


def test_auth_required_waits_for_a_person(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant = new_tenant()
    run_id = admit(runtime_engine, tenant)

    async def signed_out(context: TaskContext) -> TaskResult:
        raise CodedError("auth_required", "The source tenant needs to sign in again.")

    runtime = make_runtime(tasks={"record-subject": signed_out})
    assert asyncio.run(run_task(task_for(runtime_engine, run_id), runtime)) == "waiting_for_user"
    run = load_run(runtime_engine, run_id)
    assert (run.state, run.failure_class, run.lease_expires_at) == (
        "WAITING_FOR_USER",
        "AUTH_REQUIRED",
        None,
    )
    # A later delivery runs nothing until a person resumes the run.
    assert asyncio.run(run_task(task_for(runtime_engine, run_id), runtime)) == (
        "already_waiting_for_user"
    )


def test_an_envelope_cannot_redirect_or_invent_a_run(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant, subject = new_tenant(), uuid.uuid4()
    run_id = admit(runtime_engine, tenant, subject=subject)
    task = task_for(runtime_engine, run_id)
    runtime = make_runtime()

    tampered = task.model_copy(update={"input_digest": "0" * 64})
    invented = task.model_copy(update={"workflow_run_id": uuid.uuid4()})
    assert asyncio.run(run_task(tampered, runtime)) == "rejected"
    assert asyncio.run(run_task(invented, runtime)) == "rejected"
    assert load_run(runtime_engine, run_id).state == "QUEUED"
    assert recorded_count(runtime_engine, tenant, subject) == 0


def test_a_cancelled_run_never_runs(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant, subject = new_tenant(), uuid.uuid4()
    run_id = admit(runtime_engine, tenant, subject=subject)
    with UnitOfWork(runtime_engine, tenant) as uow:
        cancel_run(uow.session, run_id, now=utc_now())

    assert asyncio.run(run_task(task_for(runtime_engine, run_id), make_runtime())) == (
        "already_finished"
    )
    assert recorded_count(runtime_engine, tenant, subject) == 0
    with pytest.raises(InvalidTransitionError), UnitOfWork(runtime_engine, tenant) as uow:
        cancel_run(uow.session, run_id, now=utc_now())


def test_a_run_whose_attempts_all_crashed_fails_instead_of_starting_again(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant, subject = new_tenant(), uuid.uuid4()
    run_id = admit(runtime_engine, tenant, subject=subject)
    policy = RetryPolicy()
    now = utc_now()
    for attempt in range(policy.max_attempts):
        # Each holder takes the lease and dies; the next one finds it expired.
        lease = acquire_lease(
            runtime_engine,
            task_for(runtime_engine, run_id),
            now=now + attempt * (LEASE + timedelta(seconds=1)),
            lease_duration=LEASE,
            policy=policy,
        )
        assert isinstance(lease, Lease)

    after_last = later(policy.max_attempts * (LEASE + timedelta(seconds=1)))
    outcome = asyncio.run(
        run_task(task_for(runtime_engine, run_id), make_runtime(clock=after_last))
    )

    assert outcome == "failed"
    run, step = load_run(runtime_engine, run_id), load_step(runtime_engine, run_id)
    assert (run.state, run.failure_class, run.failure_code) == (
        "FAILED",
        "BUG_UNKNOWN",
        "internal_defect",
    )
    assert (step.attempt_count, step.retryable) == (policy.max_attempts, False)
    assert recorded_count(runtime_engine, tenant, subject) == 0


def test_a_run_past_its_deadline_fails_with_a_timeout(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant, subject = new_tenant(), uuid.uuid4()
    run_id = admit(runtime_engine, tenant, subject=subject)
    past_deadline = later(DEFAULT_RUN_DEADLINE + timedelta(minutes=1))

    outcome = asyncio.run(
        run_task(task_for(runtime_engine, run_id), make_runtime(clock=past_deadline))
    )

    assert outcome == "failed"
    run, step = load_run(runtime_engine, run_id), load_step(runtime_engine, run_id)
    assert (run.state, run.failure_class, run.failure_code) == ("FAILED", "TRANSIENT", "timeout")
    assert step.attempt_count == 0  # no attempt started
    assert load_attempts(runtime_engine, run_id) == []
    assert recorded_count(runtime_engine, tenant, subject) == 0


def test_repair_launches_a_queued_run_whose_launch_was_lost(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant, subject = new_tenant(), uuid.uuid4()
    run_id = admit(runtime_engine, tenant, subject=subject)  # admitted, never launched
    recorder = RecordingLauncher()

    asyncio.run(run_scheduled("repair", make_runtime(recorder, clock=later(timedelta(minutes=1)))))
    assert run_id not in recorder.launched_runs()  # too recent: its launch may be under way

    stranded = later(timedelta(minutes=6))
    asyncio.run(run_scheduled("repair", make_runtime(recorder, clock=stranded)))
    assert recorder.launched_runs().count(run_id) == 1
    assert load_run(runtime_engine, run_id).launch_count == 1
    assert recorder.deliver(make_runtime(clock=stranded), run_id) == ["complete"]
    assert recorded_count(runtime_engine, tenant, subject) == 1


def test_each_relaunch_gets_a_new_idempotency_key(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    # A launched execution that dies before taking the lease leaves the run unchanged, so
    # only the launch count tells repair's relaunches apart.
    run_id = admit(runtime_engine, new_tenant())
    recorder = RecordingLauncher()

    for minutes in (6, 12):
        clock = later(timedelta(minutes=minutes))
        asyncio.run(run_scheduled("repair", make_runtime(recorder, clock=clock)))

    keys = [task.idempotency_key for task in recorder.launched if task.workflow_run_id == run_id]
    assert keys == [f"{run_id}.1", f"{run_id}.2"]


def test_concurrent_admissions_with_one_key_admit_one_run(
    runtime_engine: Engine, new_tenant: NewTenant
) -> None:
    tenant, subject, key = new_tenant(), uuid.uuid4(), uuid.uuid4().hex

    def admission(uow: UnitOfWork) -> Admission:
        return admit_run(
            uow,
            operation="test",
            idempotency_key=key,
            kind="record-subject",
            subject_type="property",
            subject_id=subject,
            now=utc_now(),
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with UnitOfWork(runtime_engine, tenant) as first_uow:
            first = admission(first_uow)

            def second_request() -> Admission:
                with UnitOfWork(runtime_engine, tenant) as uow:
                    return admission(uow)

            # The second request blocks on the first one's uncommitted record.
            second_future = pool.submit(second_request)
            time.sleep(0.5)
            assert not second_future.done()
        second = second_future.result(timeout=30)

    assert (first.replayed, second.replayed) == (False, True)
    assert second.workflow_run_id == first.workflow_run_id
    with UnitOfWork(runtime_engine, ServiceScope()) as uow:
        runs = uow.session.scalars(
            select(WorkflowRun.id).where(WorkflowRun.tenant_id == tenant.tenant_id)
        ).all()
    assert runs == [first.workflow_run_id]


def record_downstream_event(engine: Engine, tenant: TenantScope) -> uuid.UUID:
    """Commit an event that launches ``record-subject`` in ``tenant``; return its outbox ID."""
    event = NewEvent(
        event_type="subject.changed",
        aggregate_type="property",
        aggregate_id=uuid.uuid4(),
        aggregate_revision=1,
        destinations=("record-subject",),
    )
    with UnitOfWork(engine, tenant) as uow:
        (outbox_event_id,) = record_event(
            uow.session, event, tenant_id=tenant.tenant_id, now=utc_now()
        )
    return outbox_event_id


def load_outbox_event(engine: Engine, outbox_event_id: uuid.UUID) -> OutboxEvent:
    with UnitOfWork(engine, ServiceScope()) as uow:
        return uow.session.get_one(OutboxEvent, outbox_event_id)


def test_a_launched_task_takes_its_lease_while_the_publisher_is_still_launching(
    runtime_engine: Engine, runtime_url: URL, new_tenant: NewTenant
) -> None:
    outbox_event_id = record_downstream_event(runtime_engine, new_tenant())
    # The child's connection gives up on a row lock after two seconds instead of waiting.
    impatient = create_engine(
        runtime_url, poolclass=NullPool, connect_args={"options": "-c lock_timeout=2000"}
    )
    claims: list[Lease | Declined] = []

    class ChildStartingLauncher:
        """Starts the task inside ``launch``, as a fast execution service may."""

        async def launch(self, task: TaskInput) -> str:
            claims.append(
                acquire_lease(
                    impatient, task, now=utc_now(), lease_duration=LEASE, policy=RetryPolicy()
                )
            )
            return "started"

    try:
        published = asyncio.run(
            OutboxPublisher(runtime_engine, ChildStartingLauncher()).publish([outbox_event_id])
        )
    finally:
        impatient.dispose()

    assert published == 1
    assert len(claims) == 1
    assert isinstance(claims[0], Lease)
    assert load_outbox_event(runtime_engine, outbox_event_id).state == "PUBLISHED"


def test_a_claimed_outbox_event_is_not_relaunched_until_its_claim_expires(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    outbox_event_id = record_downstream_event(runtime_engine, new_tenant())
    downstream = downstream_run_id(outbox_event_id)
    claimed_at = later(timedelta(seconds=20))
    # The publisher claims the event, then dies while launching it.
    with pytest.raises(SimulatedCrashError):
        asyncio.run(
            OutboxPublisher(runtime_engine, CrashingLauncher(), clock=claimed_at).publish(
                [outbox_event_id]
            )
        )
    assert load_run(runtime_engine, downstream).launch_count == 1

    recorder = RecordingLauncher()
    asyncio.run(
        run_scheduled("outbox-sweep", make_runtime(recorder, clock=later(timedelta(seconds=31))))
    )
    assert downstream not in recorder.launched_runs()  # still claimed by the dead publisher

    asyncio.run(
        run_scheduled("outbox-sweep", make_runtime(recorder, clock=later(timedelta(seconds=51))))
    )
    assert [task.idempotency_key for task in recorder.launched] == [f"{downstream}.2"]
    assert load_outbox_event(runtime_engine, outbox_event_id).state == "PUBLISHED"


def test_a_handler_holds_no_transaction_while_it_works(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant, subject = new_tenant(), uuid.uuid4()
    run_id = admit(runtime_engine, tenant, subject=subject)
    open_transactions: list[int] = []

    async def slow_source(context: TaskContext) -> TaskResult:
        result = await record_subject(context)
        await asyncio.sleep(0.2)  # waiting on a slow source
        with runtime_engine.connect() as observer:
            open_transactions.append(
                observer.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = "
                        "current_database() AND usename = current_user AND "
                        "pid <> pg_backend_pid() AND xact_start IS NOT NULL"
                    )
                ).scalar_one()
            )
        return result

    runtime = make_runtime(tasks={"record-subject": slow_source})
    assert asyncio.run(run_task(task_for(runtime_engine, run_id), runtime)) == "complete"

    assert open_transactions == [0]
    assert recorded_count(runtime_engine, tenant, subject) == 1


def test_runtime_connections_end_transactions_left_idle(runtime_engine: Engine) -> None:
    with runtime_engine.connect() as connection:
        timeout = connection.execute(text("SHOW idle_in_transaction_session_timeout")).scalar()

    assert timeout == "1min"


def test_an_expired_idempotency_key_admits_a_new_run(
    runtime_engine: Engine, new_tenant: NewTenant
) -> None:
    tenant, key = new_tenant(), uuid.uuid4().hex
    long_ago = utc_now() - IDEMPOTENCY_TTL - timedelta(minutes=1)
    first = admit(runtime_engine, tenant, key=key, now=long_ago)

    second = admit(runtime_engine, tenant, key=key)
    replay = admit(runtime_engine, tenant, key=key)

    assert second != first
    assert replay == second


def test_repair_prunes_expired_idempotency_records_in_bounded_batches(
    runtime_engine: Engine, new_tenant: NewTenant, make_runtime: Callable[..., TaskRuntime]
) -> None:
    tenant = new_tenant()
    long_ago = utc_now() - IDEMPOTENCY_TTL - timedelta(minutes=1)
    expired = {uuid.uuid4().hex for _ in range(3)}
    for key in expired:
        admit(runtime_engine, tenant, key=key, now=long_ago)
    current = uuid.uuid4().hex
    admit(runtime_engine, tenant, key=current)

    def keys() -> set[str]:
        with UnitOfWork(runtime_engine, tenant) as uow:
            return set(uow.session.scalars(select(IdempotencyRecord.idempotency_key)))

    assert prune_idempotency_records(runtime_engine, now=utc_now(), batch_size=2) == 2
    assert len(keys() & expired) == 1
    asyncio.run(run_scheduled("repair", make_runtime()))
    assert keys() == {current}


def test_cli_outbox_sweep_exits_cleanly(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PF_ENVIRONMENT", "development")
    monkeypatch.setenv("PF_DATABASE_URL", settings.database_url)
    started = time.monotonic()
    assert cli.main(["run-task", "outbox-sweep"]) == 0
    assert time.monotonic() - started < 10


if __name__ == "__main__":
    raise SystemExit(cli.main(sys.argv[1:], tasks=TASKS))
