"""Workflow runs: admission, state transitions, leases with fencing, failures and the outbox.

A workflow run is one admitted unit of background work. Its lifecycle follows the plan's
state diagram (``ALLOWED_TRANSITIONS``). A task works on a run only while it holds the
run's lease:

- Acquiring the lease moves the run to ``RUNNING``, sets ``lease_expires_at`` and increments
  ``fencing_token``. A run whose lease has expired can be acquired again, which is how a
  crashed task's work is recovered.
- Completing or failing a run first checks that the run is still ``RUNNING`` under the
  caller's fencing token, in the same transaction as the task's result. A stale holder
  (its lease expired and another task took over, or the run was cancelled) gets
  ``StaleLeaseError`` and its result is rolled back.
- A failed attempt that the retry policy retries keeps the run ``RUNNING`` with its lease
  expiring at the retry time, so the scheduled ``repair`` task relaunches it on its first
  pass after that time. Repair runs every few minutes, so that cadence, not the policy's
  shorter delays, bounds how soon a retry actually starts.

Events reach downstream tasks through the transactional outbox: ``record_event`` writes a
domain event, one outbox event per destination task kind and a ``QUEUED`` run for each, in
the caller's transaction. After commit, ``OutboxPublisher`` launches each run and marks its
outbox event published; ``OutboxPublisher.sweep`` launches what a crash left pending.
Delivery is therefore at least once, and a run's lease makes duplicates harmless.

Idempotency records replay their key for ``IDEMPOTENCY_TTL``; the scheduled ``repair``
task deletes expired ones (``prune_idempotency_records``).

The coroutines here run their database work in a worker thread (``asyncio.to_thread``),
so a slow query never blocks the caller's event loop, and never hold a transaction open
while they await a launch.

Every run and outbox event records the trace context it was created in. Publishing an
event and running a run's task continue that trace across the asynchronous hop
(``telemetry.resume_trace``), so one trace follows the work from the request that started
it through every downstream task.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from typing import Any, Literal

from sqlalchemy import ColumnElement, Engine, and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from property_focus.adapters.db.models import (
    DomainEvent,
    IdempotencyRecord,
    OutboxEvent,
    StepAttempt,
    Tenant,
    WorkflowRun,
    WorkflowStep,
)
from property_focus.adapters.db.repositories import (
    Scope,
    ServiceScope,
    TenantScope,
    UnitOfWork,
)
from property_focus.adapters.telemetry import current_trace_context, resume_trace
from property_focus.application.context import ExecutionContext
from property_focus.application.ports import TaskInput, TaskLauncher
from property_focus.domain.errors import CodedError, RetryPolicy

logger = logging.getLogger(__name__)

# Target states each workflow state may move to (plan: orchestration state diagram).
ALLOWED_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "QUEUED": frozenset({"RUNNING", "CANCELLED"}),
    "RUNNING": frozenset({"COMPLETE", "PARTIAL", "WAITING_FOR_USER", "FAILED", "CANCELLED"}),
    "WAITING_FOR_USER": frozenset({"QUEUED"}),  # authorized resume
    "PARTIAL": frozenset({"QUEUED"}),  # targeted retry
    "FAILED": frozenset({"QUEUED"}),  # operator retry after correction
    "COMPLETE": frozenset(),
    "CANCELLED": frozenset(),
}

# A run admitted without an explicit deadline stops retrying this long after admission.
DEFAULT_RUN_DEADLINE = timedelta(hours=24)
# An idempotency key replays its first request for this long after that request; later it
# admits a new one.
IDEMPOTENCY_TTL = timedelta(hours=24)
# Runs launched from an outbox event derive their ID from it (see ``downstream_run_id``).
_OUTBOX_RUN_NAMESPACE = uuid.UUID("0b6c1d2e-7f4a-4c55-9a43-5e2f8d7c9b10")
# Version of this application, carried in each TaskInput it builds.
_APPLICATION_VERSION = version("property-focus")


class InvalidTransitionError(ValueError):
    """A workflow state change the state diagram does not allow."""


class StaleLeaseError(RuntimeError):
    """The caller's lease on a run is no longer current; its result must not be committed."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def check_transition(current: str, target: str) -> None:
    """Raise ``InvalidTransitionError`` unless a run may move from ``current`` to ``target``."""
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise InvalidTransitionError(f"a workflow run cannot move from {current} to {target}")


def may_acquire(state: str, lease_expires_at: datetime | None, now: datetime) -> bool:
    """True when a task may take a run's lease at ``now``.

    A ``QUEUED`` run may be acquired, and so may a ``RUNNING`` run whose lease has expired
    (its holder crashed, or its retry time has come). Every other state is either finished
    or waiting for a person, and a live lease belongs to another task.
    """
    if state == "QUEUED":
        return True
    return state == "RUNNING" and lease_expires_at is not None and lease_expires_at <= now


def ensure_lease_current(state: str, fencing_token: int, presented_token: int) -> None:
    """Raise ``StaleLeaseError`` unless the run is ``RUNNING`` under ``presented_token``."""
    if state != "RUNNING" or fencing_token != presented_token:
        raise StaleLeaseError(
            f"lease {presented_token} is stale: the run is {state} under lease {fencing_token}"
        )


def input_digest(value: Mapping[str, Any]) -> str:
    """SHA-256 of ``value`` as canonical JSON (sorted keys, no whitespace), in hex."""
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def check_idempotent_replay(stored_digest: str, request_digest: str) -> None:
    """Accept a repeated idempotency key only with the request it was first used for.

    Raises ``CodedError`` ``idempotency_conflict`` when the digests differ.
    """
    if stored_digest != request_digest:
        raise CodedError(
            "idempotency_conflict",
            "This idempotency key was already used for a different request.",
        )


@dataclass(frozen=True)
class FailureEffect:
    """What a failed attempt does to its run.

    ``run_state`` is ``RUNNING`` when a retry is scheduled at ``next_attempt_at``, otherwise
    ``WAITING_FOR_USER`` (a person must act first) or ``FAILED``.
    """

    run_state: Literal["RUNNING", "WAITING_FOR_USER", "FAILED"]
    next_attempt_at: datetime | None = None


def failure_effect(
    failure: CodedError,
    *,
    attempts_made: int,
    now: datetime,
    deadline: datetime,
    policy: RetryPolicy,
) -> FailureEffect:
    """Decide a failed attempt's effect on its run under ``policy``.

    ``AUTH_REQUIRED`` pauses the run for a person; codes the policy retries schedule the
    next attempt; everything else fails the run.
    """
    if failure.error.failure_class == "AUTH_REQUIRED":
        return FailureEffect("WAITING_FOR_USER")
    next_attempt_at = policy.next_attempt_at(
        failure.code,
        attempts_made=attempts_made,
        now=now,
        deadline=deadline,
        retry_after=failure.retry_after,
    )
    if next_attempt_at is None:
        return FailureEffect("FAILED")
    return FailureEffect("RUNNING", next_attempt_at)


def downstream_run_id(outbox_event_id: uuid.UUID) -> uuid.UUID:
    """The ID of the run an outbox event launches, derived so the publisher needs no lookup."""
    return uuid.uuid5(_OUTBOX_RUN_NAMESPACE, str(outbox_event_id))


def task_input_for(run: WorkflowRun) -> TaskInput:
    """The envelope for the latest launch of ``run``.

    The idempotency key names the run and its launch count, so every delivery of one launch
    carries the same key and the next launch (``next_launch``) a new one.
    """
    return TaskInput(
        workflow_run_id=run.id,
        task_kind=run.kind,
        subject_type=run.input["subject_type"],
        subject_id=uuid.UUID(run.input["subject_id"]),
        input_digest=run.input_digest,
        application_version=_APPLICATION_VERSION,
        idempotency_key=f"{run.id}.{run.launch_count}",
        deadline=run.deadline_at,
    )


def next_launch(run: WorkflowRun) -> TaskInput:
    """Count one more launch of ``run`` and return the envelope for it.

    The count joins the caller's transaction. A relaunch therefore gets a new idempotency
    key even when no task took the run's lease since the last launch (the execution failed
    before reaching ``acquire_lease``), so an execution service that deduplicates by that
    key starts it again.
    """
    run.launch_count += 1
    return task_input_for(run)


# Admission ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Admission:
    """The run an idempotent request admitted; ``replayed`` when an earlier request with the
    same key admitted it."""

    workflow_run_id: uuid.UUID
    replayed: bool


def admit_run(
    uow: UnitOfWork,
    *,
    operation: str,
    idempotency_key: str,
    kind: str,
    subject_type: str,
    subject_id: uuid.UUID,
    now: datetime,
    correlation_id: str | None = None,
    deadline: timedelta = DEFAULT_RUN_DEADLINE,
    idempotency_ttl: timedelta = IDEMPOTENCY_TTL,
) -> Admission:
    """Admit a ``QUEUED`` run of ``kind`` for the tenant of ``uow``, at most once per key.

    The idempotency record and the run commit with the caller's transaction. Repeating
    ``idempotency_key`` for ``operation`` within ``idempotency_ttl`` of its first request,
    with the same kind and subject, returns that request's run as replayed, also when the
    two requests run concurrently (the second waits for the first to commit); with anything
    else it raises ``CodedError`` ``idempotency_conflict``. Once the key has expired it
    admits a new run, as for a new key. The caller launches the run after commit;
    ``repair`` launches one that a crash left unlaunched. Raises ``TypeError`` outside a
    tenant scope.
    """
    if not isinstance(uow.scope, TenantScope):
        raise TypeError("idempotent admission needs a tenant scope")
    run_input = {"subject_type": subject_type, "subject_id": str(subject_id)}
    request_digest = input_digest({"kind": kind, "input": run_input})
    session = uow.session
    run_id = uuid.uuid4()
    record = insert(IdempotencyRecord).values(
        tenant_id=uow.scope.tenant_id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        state="COMPLETE",
        result_id=run_id,
        expires_at=now + idempotency_ttl,
    )
    # A concurrent request with the same key holds its record until it commits or rolls
    # back; the insert waits for it, then records this request, replaces an expired
    # record with it, or does nothing.
    recorded = session.execute(
        record.on_conflict_do_update(
            index_elements=[
                IdempotencyRecord.tenant_id,
                IdempotencyRecord.operation,
                IdempotencyRecord.idempotency_key,
            ],
            set_={
                "request_digest": record.excluded.request_digest,
                "state": record.excluded.state,
                "result_id": record.excluded.result_id,
                "expires_at": record.excluded.expires_at,
                "created_at": func.now(),
                "updated_at": func.now(),
            },
            where=IdempotencyRecord.expires_at <= now,
        ).returning(IdempotencyRecord.id)
    ).scalar_one_or_none()
    if recorded is None:
        existing = session.scalars(
            select(IdempotencyRecord).where(
                IdempotencyRecord.tenant_id == uow.scope.tenant_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        ).one()
        check_idempotent_replay(existing.request_digest, request_digest)
        assert existing.result_id is not None, "admission records always name their run"
        return Admission(existing.result_id, replayed=True)

    session.add(
        _new_run(
            run_id=run_id,
            kind=kind,
            run_input=run_input,
            tenant_id=uow.scope.tenant_id,
            correlation_id=correlation_id or uuid.uuid4().hex,
            deadline_at=now + deadline,
            trace_context=current_trace_context(),
        )
    )
    session.flush()
    return Admission(run_id, replayed=False)


def prune_idempotency_records(engine: Engine, *, now: datetime, batch_size: int = 1000) -> int:
    """Delete up to ``batch_size`` idempotency records that expired by ``now``, oldest first;
    return how many were deleted.

    Runs in the service scope, which row security lets see and delete expired records only.
    """
    with UnitOfWork(engine, ServiceScope()) as uow:
        expired = (
            select(IdempotencyRecord.id)
            .where(IdempotencyRecord.expires_at <= now)
            .order_by(IdempotencyRecord.expires_at)
            .limit(batch_size)
        )
        deleted = uow.session.scalars(
            delete(IdempotencyRecord)
            .where(IdempotencyRecord.id.in_(expired.scalar_subquery()))
            .returning(IdempotencyRecord.id)
        ).all()
        return len(deleted)


def _new_run(
    *,
    kind: str,
    run_input: dict[str, Any],
    tenant_id: uuid.UUID | None,
    correlation_id: str | None,
    deadline_at: datetime,
    trace_context: Mapping[str, str],
    run_id: uuid.UUID | None = None,
    parent_run_id: uuid.UUID | None = None,
) -> WorkflowRun:
    return WorkflowRun(
        id=run_id or uuid.uuid4(),
        tenant_id=tenant_id,
        kind=kind,
        input=run_input,
        input_digest=input_digest(run_input),
        state="QUEUED",
        launch_count=0,
        parent_run_id=parent_run_id,
        correlation_id=correlation_id,
        trace_context=dict(trace_context),
        deadline_at=deadline_at,
    )


def cancel_run(session: Session, run_id: uuid.UUID, *, now: datetime) -> None:
    """Cancel a ``QUEUED`` or ``RUNNING`` run.

    A running task notices at completion: its lease is no longer current, so its result is
    discarded. Raises ``InvalidTransitionError`` for a run in any other state and
    ``LookupError`` for a run the session's scope cannot see.
    """
    run = session.get(WorkflowRun, run_id, with_for_update=True)
    if run is None:
        raise LookupError(f"workflow run {run_id} not found")
    check_transition(run.state, "CANCELLED")
    run.state = "CANCELLED"
    run.lease_expires_at = None
    run.finished_at = now


def read_workflow_run(
    engine: Engine, context: ExecutionContext, run_id: uuid.UUID
) -> tuple[WorkflowRun, Sequence[WorkflowStep]]:
    """Return the caller's run ``run_id`` and its steps in creation order.

    Raises ``CodedError`` ``not_found`` for a run that does not exist and for one outside
    the caller's tenant (another tenant's, or shared work), with the same explanation, so
    callers cannot tell the two apart. The rows are detached and fully loaded.
    """
    with UnitOfWork(engine, context.scope) as uow:
        run = uow.repository(WorkflowRun).get(run_id)
        if run is None:
            raise CodedError("not_found", "No workflow run has this ID.")
        steps = uow.session.scalars(
            select(WorkflowStep)
            .where(WorkflowStep.workflow_run_id == run_id)
            .order_by(WorkflowStep.created_at, WorkflowStep.name)
        ).all()
        return run, steps


# Outbox ------------------------------------------------------------------------------


@dataclass(frozen=True)
class NewEvent:
    """A domain event to record, and the task kinds it launches."""

    event_type: str
    aggregate_type: str
    aggregate_id: uuid.UUID
    aggregate_revision: int
    # IDs and references only, never private content or secrets.
    payload: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = 1
    destinations: Sequence[str] = ()


def record_event(
    session: Session,
    event: NewEvent,
    *,
    tenant_id: uuid.UUID | None,
    now: datetime,
    parent_run_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
    deadline: timedelta = DEFAULT_RUN_DEADLINE,
) -> list[uuid.UUID]:
    """Write ``event``, an outbox event per destination and a ``QUEUED`` run for each.

    The writes join the caller's transaction, so they commit or roll back with the change
    the event describes. Each run works on the event's aggregate, belongs to ``tenant_id``
    (None for shared work) and has the ID ``downstream_run_id`` derives from its outbox
    event. The outbox events and runs record the current trace context. Returns the outbox
    event IDs, for ``OutboxPublisher.publish`` after commit.
    """
    domain_event = DomainEvent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        event_type=event.event_type,
        schema_version=event.schema_version,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        aggregate_revision=event.aggregate_revision,
        payload=dict(event.payload),
        occurred_at=now,
    )
    session.add(domain_event)
    # Without relationships the session does not order inserts by foreign key.
    session.flush()
    run_input = {
        "domain_event_id": str(domain_event.id),
        "subject_type": event.aggregate_type,
        "subject_id": str(event.aggregate_id),
    }
    trace_context = current_trace_context()
    outbox_ids = []
    for destination in event.destinations:
        outbox_event = OutboxEvent(
            id=uuid.uuid4(),
            domain_event_id=domain_event.id,
            tenant_id=tenant_id,
            destination=destination,
            trace_context=trace_context,
        )
        session.add(outbox_event)
        session.add(
            _new_run(
                run_id=downstream_run_id(outbox_event.id),
                kind=destination,
                run_input=run_input,
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                deadline_at=now + deadline,
                parent_run_id=parent_run_id,
                trace_context=trace_context,
            )
        )
        outbox_ids.append(outbox_event.id)
    session.flush()  # surface a constraint violation at the call site
    return outbox_ids


class OutboxPublisher:
    """Launches the runs that outbox events stand for, and marks the events published.

    Each event is published in three steps, so no transaction is open while the launch
    runs and the launched task can take its lease at once:

    1. Claim, in one short transaction: count the run's next launch (``next_launch``) and
       mark the event claimed until ``CLAIM_DURATION`` from now. Every launch therefore
       has its own launch count, and a concurrent ``publish`` or ``sweep`` skips the
       event while the claim lasts.
    2. Launch, outside any transaction.
    3. Mark the event published, in a second short transaction.

    A launch that fails, or a crash after the claim, leaves the event pending; the first
    sweep after its claim expires launches it again. A crash after a launch but before the
    event is marked published makes that sweep launch it a second time, which the run's
    lease turns into a harmless duplicate.
    """

    # The sweep leaves recent events to the task that committed them.
    SWEEP_MIN_AGE = timedelta(seconds=30)
    # How long a claimed event is left to its publisher's launch. A launch takes seconds;
    # one that outlasts its claim may be launched again by a sweep, harmlessly.
    CLAIM_DURATION = timedelta(seconds=30)

    def __init__(
        self, engine: Engine, launcher: TaskLauncher, *, clock: Callable[[], datetime] = utc_now
    ) -> None:
        self._engine = engine
        self._launcher = launcher
        self._clock = clock

    async def publish(self, outbox_event_ids: Iterable[uuid.UUID]) -> int:
        """Launch each pending, unclaimed event among ``outbox_event_ids``; return how many."""
        published = 0
        for outbox_event_id in outbox_event_ids:
            if await self._publish_one(outbox_event_id):
                published += 1
        return published

    async def sweep(self, *, batch_size: int = 100) -> int:
        """Launch events pending for longer than ``SWEEP_MIN_AGE`` and not claimed by a
        live publisher, oldest first.

        Handles at most ``batch_size`` events; returns how many it launched.
        """
        return await self.publish(await asyncio.to_thread(self._sweepable, batch_size))

    def _sweepable(self, batch_size: int) -> Sequence[uuid.UUID]:
        now = self._clock()
        with UnitOfWork(self._engine, ServiceScope()) as uow:
            return uow.session.scalars(
                select(OutboxEvent.id)
                .where(
                    OutboxEvent.state == "PENDING",
                    OutboxEvent.created_at < now - self.SWEEP_MIN_AGE,
                    _claim_lapsed(now),
                )
                .order_by(OutboxEvent.created_at)
                .limit(batch_size)
            ).all()

    async def _publish_one(self, outbox_event_id: uuid.UUID) -> bool:
        claim = await asyncio.to_thread(self._claim, outbox_event_id)
        if claim is None:
            return False
        task, trace_context = claim
        attributes = {
            "property_focus.outbox_event_id": str(outbox_event_id),
            "property_focus.workflow_run_id": str(task.workflow_run_id),
            "property_focus.task_kind": task.task_kind,
        }
        with resume_trace(f"publish {task.task_kind}", trace_context, attributes=attributes):
            try:
                await self._launcher.launch(task)
            except Exception:
                logger.exception(
                    "launching run %s for outbox event %s failed",
                    task.workflow_run_id,
                    outbox_event_id,
                )
                return False
        await asyncio.to_thread(self._mark_published, outbox_event_id)
        return True

    def _claim(self, outbox_event_id: uuid.UUID) -> tuple[TaskInput, Mapping[str, str]] | None:
        """Claim the event and count its run's next launch; return the launch's envelope and
        the event's trace context, or None when the event is published, claimed or locked."""
        now = self._clock()
        with UnitOfWork(self._engine, ServiceScope()) as uow:
            session = uow.session
            event = session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.id == outbox_event_id,
                    OutboxEvent.state == "PENDING",
                    _claim_lapsed(now),
                )
                .with_for_update(skip_locked=True)
            ).one_or_none()
            if event is None:
                return None
            event.publish_attempts += 1
            # Locked so that a concurrent repair pass cannot count the same launch.
            run = session.get(WorkflowRun, downstream_run_id(event.id), with_for_update=True)
            if run is None:
                logger.error("outbox event %s has no run to launch", event.id)
                return None
            event.claim_expires_at = now + self.CLAIM_DURATION
            return next_launch(run), dict(event.trace_context)

    def _mark_published(self, outbox_event_id: uuid.UUID) -> None:
        with UnitOfWork(self._engine, ServiceScope()) as uow:
            uow.session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == outbox_event_id, OutboxEvent.state == "PENDING")
                .values(state="PUBLISHED", published_at=self._clock(), claim_expires_at=None)
            )


def _claim_lapsed(now: datetime) -> ColumnElement[bool]:
    """SQL condition: no publisher's claim on the outbox event is live at ``now``."""
    return or_(OutboxEvent.claim_expires_at.is_(None), OutboxEvent.claim_expires_at <= now)


async def relaunch_stalled_runs(
    engine: Engine,
    launcher: TaskLauncher,
    *,
    now: datetime,
    stranded_after: timedelta = timedelta(minutes=5),
    batch_size: int = 100,
) -> int:
    """Launch runs that no task is working on: the ``repair`` scheduled task.

    These are ``RUNNING`` runs whose lease has expired (the task crashed, or a retry is
    due) and ``QUEUED`` runs untouched for ``stranded_after`` (their outbox event or
    admission launch was lost). Runs locked by a task right now are left for the next pass.
    Each launch is counted (``next_launch``) before it starts; a run whose stored input
    cannot form an envelope is logged and skipped. Handles at most
    ``batch_size`` runs; returns how many it launched. A run that is also launched
    elsewhere starts once: the lease decides.
    """
    launches = await asyncio.to_thread(
        _count_relaunches, engine, now=now, stranded_after=stranded_after, batch_size=batch_size
    )
    launched = 0
    for task in launches:
        try:
            await launcher.launch(task)
        except Exception:
            logger.exception("relaunching run %s failed", task.workflow_run_id)
        else:
            launched += 1
    return launched


def _count_relaunches(
    engine: Engine, *, now: datetime, stranded_after: timedelta, batch_size: int
) -> list[TaskInput]:
    """Count the next launch of each stalled run and return their envelopes, committed."""
    with UnitOfWork(engine, ServiceScope()) as uow:
        runs = uow.session.scalars(
            select(WorkflowRun)
            .where(
                or_(
                    and_(WorkflowRun.state == "RUNNING", WorkflowRun.lease_expires_at <= now),
                    and_(
                        WorkflowRun.state == "QUEUED",
                        WorkflowRun.updated_at < now - stranded_after,
                    ),
                )
            )
            .order_by(WorkflowRun.updated_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        launches = []
        for run in runs:
            try:
                launches.append(next_launch(run))
            except (KeyError, ValueError):  # a malformed run must not stop the whole pass
                logger.exception("run %s has no valid task input", run.id)
    return launches


# Leases ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Lease:
    """A task's claim on one run, valid while the run's fencing token still equals ours."""

    workflow_run_id: uuid.UUID
    fencing_token: int
    step_id: uuid.UUID
    attempt_id: uuid.UUID
    attempt_number: int
    correlation_id: str | None
    deadline: datetime
    # The scope the task's own reads and writes run in, from the run's tenant.
    scope: Scope
    # The W3C trace context the run was created in; empty when none was recorded.
    trace_context: Mapping[str, str]

    @property
    def tenant_id(self) -> uuid.UUID | None:
        """The tenant the run belongs to, or None for shared work."""
        return self.scope.tenant_id if isinstance(self.scope, TenantScope) else None


@dataclass(frozen=True)
class Declined:
    """Why no lease was granted.

    - ``already_finished``: the run is ``COMPLETE``, ``PARTIAL``, ``FAILED`` or
      ``CANCELLED``.
    - ``already_waiting_for_user``: the run is ``WAITING_FOR_USER``.
    - ``lease_held``: the run's lease has not expired: another task is running it, or its
      retry is not due yet.
    - ``rejected``: no visible run matches the envelope's ID, kind and input digest.
    - ``failed``: the run was out of attempts or past its deadline and is now ``FAILED``.
    """

    outcome: Literal[
        "already_finished", "already_waiting_for_user", "lease_held", "rejected", "failed"
    ]


def acquire_lease(
    engine: Engine,
    task: TaskInput,
    *,
    now: datetime,
    lease_duration: timedelta,
    policy: RetryPolicy,
) -> Lease | Declined:
    """Take the lease on the run ``task`` names and start a new step attempt, if allowed.

    The run is trusted, the envelope is not: the run must match the envelope's kind and
    input digest, and the lease's scope comes from the run's tenant. A run already
    attempted ``policy.max_attempts`` times without a recorded result (each attempt
    crashed), or past its deadline, fails instead of starting again.
    """
    with UnitOfWork(engine, ServiceScope()) as uow:
        session = uow.session
        run = session.get(WorkflowRun, task.workflow_run_id, with_for_update=True)
        if run is None or (run.kind, run.input_digest) != (task.task_kind, task.input_digest):
            return Declined("rejected")
        if not may_acquire(run.state, run.lease_expires_at, now):
            match run.state:
                case "RUNNING":
                    return Declined("lease_held")
                case "WAITING_FOR_USER":
                    return Declined("already_waiting_for_user")
                case _:
                    return Declined("already_finished")

        step = session.scalars(
            select(WorkflowStep).where(
                WorkflowStep.workflow_run_id == run.id, WorkflowStep.name == run.kind
            )
        ).one_or_none()
        if step is None:
            step = WorkflowStep(
                id=uuid.uuid4(), workflow_run_id=run.id, name=run.kind, attempt_count=0
            )
            session.add(step)
        run.state = "RUNNING"  # from QUEUED, or still RUNNING after an expired lease

        exhausted = None
        if now >= run.deadline_at:
            exhausted = CodedError("timeout", "The work did not finish before its deadline.")
        elif step.attempt_count >= policy.max_attempts:
            exhausted = CodedError(
                "internal_defect",
                f"The task stopped {step.attempt_count} times without reporting a result.",
            )
        if exhausted is not None:
            _record_failure(run, step, None, exhausted, FailureEffect("FAILED"), now)
            return Declined("failed")

        run.fencing_token += 1
        run.lease_expires_at = now + lease_duration
        run.started_at = run.started_at or now
        step.state = "RUNNING"
        step.attempt_count += 1
        step.next_attempt_at = None
        # Without relationships the session does not order inserts by foreign key, so the
        # step must exist before its attempt.
        session.flush()
        attempt = StepAttempt(
            id=uuid.uuid4(),
            workflow_step_id=step.id,
            attempt_number=step.attempt_count,
            fencing_token=run.fencing_token,
            correlation_id=run.correlation_id,
            started_at=now,
        )
        session.add(attempt)

        scope: Scope = ServiceScope()
        if run.tenant_id is not None:
            owner = session.get_one(Tenant, run.tenant_id).owner_user_id
            scope = TenantScope(tenant_id=run.tenant_id, user_id=owner)
        return Lease(
            workflow_run_id=run.id,
            fencing_token=run.fencing_token,
            step_id=step.id,
            attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            correlation_id=run.correlation_id,
            deadline=run.deadline_at,
            scope=scope,
            trace_context=dict(run.trace_context),
        )


def complete_run(
    session: Session,
    lease: Lease,
    *,
    state: Literal["COMPLETE", "PARTIAL"],
    result_refs: Sequence[str],
    now: datetime,
) -> None:
    """Record the leased run's result in ``session``'s transaction, with the task's writes.

    Raises ``StaleLeaseError`` when the lease is no longer current; the caller must then
    roll back, discarding the task's writes.
    """
    run, step, attempt = _locked(session, lease)
    check_transition(run.state, state)
    run.state = step.state = attempt.outcome = state
    run.result_refs = step.result_refs = list(result_refs)
    run.lease_expires_at = None
    run.finished_at = attempt.finished_at = now


def fail_attempt(
    engine: Engine, lease: Lease, failure: CodedError, *, now: datetime, policy: RetryPolicy
) -> FailureEffect:
    """Persist a failed attempt on the leased run and schedule a retry when one applies.

    The step records the failure class, stable code, retryability, safe explanation,
    attempt count and correlation ID. Raises ``StaleLeaseError`` when the lease is no
    longer current; nothing is recorded on the run then.
    """
    with UnitOfWork(engine, ServiceScope()) as uow:
        run, step, attempt = _locked(uow.session, lease)
        effect = failure_effect(
            failure,
            attempts_made=step.attempt_count,
            now=now,
            deadline=lease.deadline,
            policy=policy,
        )
        _record_failure(run, step, attempt, failure, effect, now)
        return effect


def abandon_attempt(engine: Engine, lease: Lease, *, now: datetime) -> None:
    """Close a stale holder's attempt as ``CANCELLED``; the run belongs to someone else."""
    with UnitOfWork(engine, ServiceScope()) as uow:
        attempt = uow.session.get_one(StepAttempt, lease.attempt_id)
        attempt.outcome = "CANCELLED"
        attempt.finished_at = now


def _locked(session: Session, lease: Lease) -> tuple[WorkflowRun, WorkflowStep, StepAttempt]:
    """Lock the leased run and check the lease; return the run, its step and the attempt."""
    run = session.get_one(WorkflowRun, lease.workflow_run_id, with_for_update=True)
    ensure_lease_current(run.state, run.fencing_token, lease.fencing_token)
    return (
        run,
        session.get_one(WorkflowStep, lease.step_id),
        session.get_one(StepAttempt, lease.attempt_id),
    )


def _record_failure(
    run: WorkflowRun,
    step: WorkflowStep,
    attempt: StepAttempt | None,
    failure: CodedError,
    effect: FailureEffect,
    now: datetime,
) -> None:
    failure_class = failure.error.failure_class
    step.failure_class = failure_class
    step.failure_code = failure.code
    step.retryable = failure.error.retries_automatically
    step.safe_explanation = failure.safe_explanation
    step.correlation_id = run.correlation_id
    step.next_attempt_at = effect.next_attempt_at
    if attempt is not None:
        attempt.outcome = "FAILED"
        attempt.failure_class = failure_class
        attempt.failure_code = failure.code
        attempt.finished_at = now

    if effect.run_state == "RUNNING":
        # The lease is held until the retry time; repair relaunches the run on its first
        # pass after it, so the retry starts up to one repair interval later than scheduled.
        step.state = "QUEUED"
        run.lease_expires_at = effect.next_attempt_at
        return
    check_transition(run.state, effect.run_state)
    run.state = step.state = effect.run_state
    run.failure_class = failure_class
    run.failure_code = failure.code
    run.lease_expires_at = None
    if effect.run_state == "FAILED":
        run.finished_at = now
