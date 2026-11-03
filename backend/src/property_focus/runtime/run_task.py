"""The single entry point for background tasks: one ``TaskInput`` in, one recorded outcome.

Every task execution, whether a dogfood process (``cli run-task``), a Lambda invocation
(``lambda_handler``) or a Fargate container, calls ``run_task``. It takes the lease on the
workflow run the envelope names, runs the registered handler in the run's own scope,
commits the handler's writes together with the run's result, and then launches the
downstream tasks the handler's events name.

Tasks act with the runtime's workload credentials (the runtime database role and the
secret provider), never with a user's token. A task for a tenant runs in that tenant's
scope, taken from the trusted workflow run; a task for shared work runs in the service
scope.

Each leased execution runs in a ``task <kind>`` span that continues the trace its workflow
run was created in, and its log lines carry the run's correlation ID.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from opentelemetry.trace import StatusCode
from sqlalchemy import Engine

from property_focus.adapters.db import connect_runtime
from property_focus.adapters.db.repositories import UnitOfWork
from property_focus.adapters.secrets import secret_provider_for
from property_focus.adapters.telemetry import bind_correlation_id, resume_trace
from property_focus.application.ports import SecretProvider, TaskInput, TaskLauncher
from property_focus.application.workflows import (
    Declined,
    Lease,
    NewEvent,
    OutboxPublisher,
    StaleLeaseError,
    abandon_attempt,
    acquire_lease,
    complete_run,
    fail_attempt,
    record_event,
    utc_now,
)
from property_focus.config import Settings
from property_focus.domain.errors import CodedError, RetryPolicy
from property_focus.runtime.launcher import launcher_for

logger = logging.getLogger(__name__)

# What happened to one delivered TaskInput. The run itself holds the full record.
#   complete, partial            the handler's result committed
#   retry_scheduled              the attempt failed; repair relaunches the run later
#   waiting_for_user, failed     the attempt failed and the run stopped (``failed`` also
#                                when the run was out of attempts or time before it)
#   already_finished             nothing ran: the run had already finished
#   already_waiting_for_user     nothing ran: the run is waiting for a person
#   lease_held                   nothing ran: another task holds the run's lease, or its
#                                retry is not due yet
#   rejected                     nothing ran: the envelope matches no run
#   stale                        the lease expired or the run was cancelled mid-task;
#                                the handler's writes were discarded
TaskRunOutcome = Literal[
    "complete",
    "partial",
    "retry_scheduled",
    "waiting_for_user",
    "failed",
    "already_finished",
    "already_waiting_for_user",
    "lease_held",
    "rejected",
    "stale",
]


@dataclass(frozen=True)
class TaskResult:
    """A handler's successful result.

    ``PARTIAL`` means usable output with missing components. ``result_refs`` are the IDs of
    what the task produced, recorded on the run.
    """

    state: Literal["COMPLETE", "PARTIAL"] = "COMPLETE"
    result_refs: Sequence[str] = ()


@dataclass
class TaskContext:
    """What a handler gets: its envelope, a unit of work in the run's scope, and secrets.

    Writes through ``uow`` commit only if the handler returns and its lease is still
    current. A handler reports a failure by raising ``CodedError``; any other exception
    is recorded as ``internal_defect``.
    """

    task: TaskInput
    uow: UnitOfWork
    secrets: SecretProvider
    # The tenant the run belongs to, or None for shared work.
    tenant_id: uuid.UUID | None
    correlation_id: str | None
    now: datetime
    outbox_event_ids: list[uuid.UUID] = field(default_factory=list)

    def emit(self, event: NewEvent) -> None:
        """Record ``event`` with this task's result; its destinations launch after commit."""
        self.outbox_event_ids += record_event(
            self.uow.session,
            event,
            tenant_id=self.tenant_id,
            now=self.now,
            parent_run_id=self.task.workflow_run_id,
            correlation_id=self.correlation_id,
        )


type TaskHandler = Callable[[TaskContext], Awaitable[TaskResult]]

# Task kind -> handler. Every kind a workflow run can name is registered here.
type TaskRegistry = Mapping[str, TaskHandler]
DEFAULT_TASKS: TaskRegistry = {}


@dataclass(frozen=True)
class TaskRuntime:
    """Everything tasks share within one process.

    ``lease_duration`` must exceed the longest handler run; a task still running when its
    lease expires may be superseded, and its result is then discarded.
    """

    engine: Engine
    launcher: TaskLauncher
    secrets: SecretProvider
    tasks: TaskRegistry = field(default_factory=lambda: DEFAULT_TASKS)
    clock: Callable[[], datetime] = utc_now
    lease_duration: timedelta = timedelta(minutes=15)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)

    @property
    def publisher(self) -> OutboxPublisher:
        return OutboxPublisher(self.engine, self.launcher, clock=self.clock)


async def runtime_from_settings(
    settings: Settings, tasks: TaskRegistry = DEFAULT_TASKS
) -> TaskRuntime:
    """Build the runtime ``settings`` describe: database pool, secrets and launcher."""
    secrets = secret_provider_for(settings)
    return TaskRuntime(
        engine=await connect_runtime(settings, pool_size=2, secrets=secrets),
        launcher=launcher_for(settings),
        secrets=secrets,
        tasks=tasks,
    )


async def run_task(task: TaskInput, runtime: TaskRuntime) -> TaskRunOutcome:
    """Run ``task`` once, if its workflow run still needs it, and return what happened.

    Safe under duplicate and concurrent delivery: only the holder of the run's current
    lease commits a result. Failures are recorded on the run, not raised.
    """
    claim = acquire_lease(
        runtime.engine,
        task,
        now=runtime.clock(),
        lease_duration=runtime.lease_duration,
        policy=runtime.retry_policy,
    )
    if isinstance(claim, Declined):
        if claim.outcome == "rejected":
            logger.warning(
                "task %s for run %s matches no run", task.task_kind, task.workflow_run_id
            )
        return claim.outcome

    attributes = {
        "property_focus.workflow_run_id": str(claim.workflow_run_id),
        "property_focus.task_kind": task.task_kind,
        "property_focus.attempt_number": claim.attempt_number,
    }
    with (
        resume_trace(f"task {task.task_kind}", claim.trace_context, attributes=attributes) as span,
        bind_correlation_id(claim.correlation_id),
    ):
        outcome = await _run_leased(task, claim, runtime)
        span.set_attribute("property_focus.task_outcome", outcome)
        if outcome == "failed":
            span.set_status(StatusCode.ERROR)
    return outcome


async def _run_leased(task: TaskInput, lease: Lease, runtime: TaskRuntime) -> TaskRunOutcome:
    """Run ``task`` under ``lease``, record its outcome on the run and launch what it emitted."""
    try:
        result, outbox_event_ids = await _execute(task, lease, runtime)
    except StaleLeaseError:
        abandon_attempt(runtime.engine, lease, now=runtime.clock())
        return "stale"
    except CodedError as error:
        return _report_failure(runtime, lease, error)
    except Exception:
        logger.exception("task %s for run %s failed", task.task_kind, task.workflow_run_id)
        return _report_failure(
            runtime,
            lease,
            CodedError("internal_defect", "The task failed with an unexpected error."),
        )

    await runtime.publisher.publish(outbox_event_ids)
    return "complete" if result.state == "COMPLETE" else "partial"


async def _execute(
    task: TaskInput, lease: Lease, runtime: TaskRuntime
) -> tuple[TaskResult, list[uuid.UUID]]:
    """Run the handler and commit its writes with the run's result, in one transaction."""
    handler = runtime.tasks.get(task.task_kind)
    if handler is None:
        raise CodedError("internal_defect", f"No handler is registered for {task.task_kind}.")
    with UnitOfWork(runtime.engine, lease.scope) as uow:
        context = TaskContext(
            task=task,
            uow=uow,
            secrets=runtime.secrets,
            tenant_id=lease.tenant_id,
            correlation_id=lease.correlation_id,
            now=runtime.clock(),
        )
        result = await handler(context)
        complete_run(
            uow.session,
            lease,
            state=result.state,
            result_refs=result.result_refs,
            now=runtime.clock(),
        )
    return result, context.outbox_event_ids


def _report_failure(runtime: TaskRuntime, lease: Lease, error: CodedError) -> TaskRunOutcome:
    try:
        effect = fail_attempt(
            runtime.engine, lease, error, now=runtime.clock(), policy=runtime.retry_policy
        )
    except StaleLeaseError:
        abandon_attempt(runtime.engine, lease, now=runtime.clock())
        return "stale"
    match effect.run_state:
        case "RUNNING":
            return "retry_scheduled"
        case "WAITING_FOR_USER":
            return "waiting_for_user"
        case "FAILED":
            return "failed"
