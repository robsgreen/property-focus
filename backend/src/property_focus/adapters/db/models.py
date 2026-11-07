"""SQLAlchemy models for the foundation tables.

The models mirror the schema the migrations create; migrations never import them. A
``tenant_id`` column marks tenant data. When it is ``NOT NULL`` the table is
tenant-private: forced row-level security admits a row only inside its own tenant's scope
(see ``rls.sql``), and ``ScopedRepository`` refuses such a table outside a tenant scope.

Every primary key is a UUID and every timestamp is a UTC ``timestamptz``. JSONB columns
hold IDs and references only, never private content or secrets.
"""

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any, ClassVar, Literal, get_args

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from property_focus.domain.errors import FAILURE_CLASSES

# Workflow run and step states (plan: orchestration state diagram).
WorkflowState = Literal[
    "QUEUED", "RUNNING", "COMPLETE", "PARTIAL", "WAITING_FOR_USER", "FAILED", "CANCELLED"
]
WORKFLOW_STATES: tuple[WorkflowState, ...] = get_args(WorkflowState)
# Outcomes of one finished step attempt; a running attempt has none.
ATTEMPT_OUTCOMES = ("COMPLETE", "PARTIAL", "FAILED", "CANCELLED")
AnalysisTier = Literal["BASELINE", "DEEP"]
ANALYSIS_TIERS: tuple[AnalysisTier, ...] = get_args(AnalysisTier)
BudgetTier = Literal["BASELINE", "DEEP", "EXPERIMENT"]
BUDGET_TIERS: tuple[BudgetTier, ...] = get_args(BudgetTier)
SYSTEM_ROLES = ("reviewer", "operator")
IDEMPOTENCY_STATES = ("IN_PROGRESS", "COMPLETE", "FAILED")
PUBLISH_STATES = ("PENDING", "PUBLISHED")


def _one_of(column: str, values: Iterable[str]) -> str:
    """SQL check that ``column`` holds one of ``values`` (NULL passes, as in SQL)."""
    return f"{column} IN ({', '.join(repr(value) for value in sorted(values))})"


class Base(DeclarativeBase):
    """Declarative base: constraint names follow the migrations' naming convention."""

    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(server_default=func.now())


def _updated_at() -> Mapped[datetime]:
    return mapped_column(server_default=func.now(), onupdate=func.now())


class User(Base):
    """A person, identified by the token issuer and subject, independent of Cognito internals."""

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("issuer", "subject"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    issuer: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    # Copied from the access token; used to match search invitations.
    email: Mapped[str] = mapped_column(Text)
    email_verified: Mapped[bool]
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Tenant(Base):
    """A tenant: the isolation boundary for private data (an account, to its user). Each user
    owns exactly one."""

    __tablename__ = "tenants"
    # (id, owner_user_id) is the target of scoped foreign keys that tie a row's principal to
    # the tenant it is recorded under.
    __table_args__ = (UniqueConstraint("id", "owner_user_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    created_at: Mapped[datetime] = _created_at()


class UserRole(Base):
    """A system role held by a user. Only ``reviewer`` and ``operator`` exist."""

    __tablename__ = "user_roles"
    __table_args__ = (CheckConstraint(_one_of("role", SYSTEM_ROLES), name="role"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(Text, primary_key=True)
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    granted_at: Mapped[datetime] = _created_at()


class WorkflowRun(Base):
    """One admitted unit of background work and its lifecycle.

    ``tenant_id`` is the tenant the work belongs to, or NULL for shared-intelligence work
    that no tenant owns; such runs are visible only in the service scope.
    """

    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(_one_of("state", WORKFLOW_STATES), name="state"),
        CheckConstraint(_one_of("analysis_tier", ANALYSIS_TIERS), name="analysis_tier"),
        CheckConstraint(_one_of("budget_tier", BUDGET_TIERS), name="budget_tier"),
        CheckConstraint(_one_of("failure_class", FAILURE_CLASSES), name="failure_class"),
        Index(None, "tenant_id", "state", "created_at"),
        # The repair task's scan of unfinished runs, oldest first.
        Index(
            "ix_workflow_runs_unfinished_updated_at",
            "updated_at",
            postgresql_where=text("state IN ('QUEUED', 'RUNNING')"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"))
    kind: Mapped[str] = mapped_column(Text)
    # Both NULL for non-analysis work.
    analysis_tier: Mapped[str | None] = mapped_column(Text)
    budget_tier: Mapped[str | None] = mapped_column(Text)
    # IDs and references the task needs, and the digest that identifies the computation.
    input: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    input_digest: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="QUEUED")
    lease_expires_at: Mapped[datetime | None]
    # Incremented on each lease grant; a completion carrying an older token is stale.
    fencing_token: Mapped[int] = mapped_column(BigInteger, server_default="0")
    # Incremented each time the outbox publisher or repair launches the run; part of each
    # launch's idempotency key, so a relaunch is never mistaken for a duplicate delivery.
    launch_count: Mapped[int] = mapped_column(Integer, server_default="0")
    failure_class: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(Text)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    correlation_id: Mapped[str | None] = mapped_column(Text)
    # W3C trace context carrier linking this run to the span that admitted it.
    trace_context: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    result_refs: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    # Automatic retries stop at this time.
    deadline_at: Mapped[datetime]
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class WorkflowStep(Base):
    """One named component of a workflow run, with its latest failure, if any."""

    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "name"),
        CheckConstraint(_one_of("state", WORKFLOW_STATES), name="state"),
        CheckConstraint(_one_of("failure_class", FAILURE_CLASSES), name="failure_class"),
        CheckConstraint("attempt_count >= 0", name="attempt_count"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"))
    name: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="QUEUED")
    attempt_count: Mapped[int] = mapped_column(Integer, server_default="0")
    failure_class: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool | None]
    # Shown to users and operators; never contains secrets or private content.
    safe_explanation: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None]
    result_refs: Mapped[list[Any]] = mapped_column(server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class StepAttempt(Base):
    """One execution attempt of a workflow step, kept after it finishes."""

    __tablename__ = "step_attempts"
    __table_args__ = (
        UniqueConstraint("workflow_step_id", "attempt_number"),
        CheckConstraint(_one_of("outcome", ATTEMPT_OUTCOMES), name="outcome"),
        CheckConstraint(_one_of("failure_class", FAILURE_CLASSES), name="failure_class"),
        CheckConstraint("attempt_number >= 1", name="attempt_number"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    workflow_step_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_steps.id"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    fencing_token: Mapped[int] = mapped_column(BigInteger)
    # NULL while the attempt runs.
    outcome: Mapped[str | None] = mapped_column(Text)
    failure_class: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None]


class IdempotencyRecord(Base):
    """The outcome of one idempotent request, keyed by the calling tenant (tenant-private)."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("tenant_id", "operation", "idempotency_key"),
        CheckConstraint(_one_of("state", IDEMPOTENCY_STATES), name="state"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    operation: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(Text)
    # Digest of the request body; the same key with a different digest is a conflict.
    request_digest: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="IN_PROGRESS")
    result_id: Mapped[uuid.UUID | None]
    lease_expires_at: Mapped[datetime | None]
    # The key replays until this time; afterwards it admits a new request, and the repair
    # task deletes the record. ``admit_run`` sets it from ``workflows.IDEMPOTENCY_TTL``, the
    # one source of the TTL. Migration 0002's column default exists to backfill the records
    # it found, so the model declares none.
    expires_at: Mapped[datetime] = mapped_column(index=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class DomainEvent(Base):
    """An immutable record that an aggregate changed, written with the change itself."""

    __tablename__ = "domain_events"
    __table_args__ = (Index(None, "aggregate_type", "aggregate_id"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    # The tenant whose change the event records, or NULL for shared work.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"))
    event_type: Mapped[str] = mapped_column(Text)
    # Version of the payload's shape for this event type.
    schema_version: Mapped[int] = mapped_column(Integer)
    aggregate_type: Mapped[str] = mapped_column(Text)
    aggregate_id: Mapped[uuid.UUID]
    aggregate_revision: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    occurred_at: Mapped[datetime]
    created_at: Mapped[datetime] = _created_at()


class OutboxEvent(Base):
    """A domain event waiting to be launched to one downstream destination."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("domain_event_id", "destination"),
        CheckConstraint(_one_of("state", PUBLISH_STATES), name="state"),
        CheckConstraint("publish_attempts >= 0", name="publish_attempts"),
        Index(
            "ix_outbox_events_pending_created_at",
            "created_at",
            postgresql_where=text("state = 'PENDING'"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    domain_event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("domain_events.id"))
    # The domain event's tenant, or NULL for shared work.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"))
    # The downstream task kind this event launches.
    destination: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="PENDING")
    publish_attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    # A publisher has counted a launch and is launching it until this time; NULL when no
    # launch is under way. An expired claim is a publisher that died mid-launch.
    claim_expires_at: Mapped[datetime | None]
    trace_context: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _created_at()
    published_at: Mapped[datetime | None]


class ProcessedEvent(Base):
    """Marks that a consumer has applied a domain event, so redelivery has no second effect."""

    __tablename__ = "processed_events"

    consumer: Mapped[str] = mapped_column(Text, primary_key=True)
    domain_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("domain_events.id"), primary_key=True
    )
    # The domain event's tenant, or NULL for shared work.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"))
    processed_at: Mapped[datetime] = _created_at()


class AuditEvent(Base):
    """An immutable record of who did what to which target, and why (tenant-private).

    The principal is the tenant's owner, or a service task when ``principal_user_id`` is
    NULL; the scoped foreign key on (tenant_id, principal_user_id) keeps a user's action
    under that user's own tenant.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "principal_user_id"],
            ["tenants.id", "tenants.owner_user_id"],
        ),
        CheckConstraint(
            "(principal_user_id IS NULL) <> (principal_service IS NULL)", name="principal"
        ),
        Index(None, "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    principal_user_id: Mapped[uuid.UUID | None]
    # The service task kind when no user acted.
    principal_service: Mapped[str | None] = mapped_column(Text)
    # Search the action concerned, if any (the search tables arrive later).
    search_subscription_id: Mapped[uuid.UUID | None]
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str] = mapped_column(Text)
    target_id: Mapped[uuid.UUID | None]
    target_revision: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text)
    # Redacted before writing: IDs and safe descriptors only.
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = _created_at()
