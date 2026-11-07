"""Foundation: tenants, users, system roles, workflow runs, idempotency, events and audit.

Creates the tables, then installs the database roles, privileges and row-level security
from ``property_focus/adapters/db/rls.sql``. Must run as the migration owner.

Revision ID: 0001
Revises: none
"""

from importlib.resources import files
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

WORKFLOW_STATES = (
    "'QUEUED', 'RUNNING', 'COMPLETE', 'PARTIAL', 'WAITING_FOR_USER', 'FAILED', 'CANCELLED'"
)
FAILURE_CLASSES = (
    "'AUTH_REQUIRED', 'BUG_UNKNOWN', 'DATA_UNAVAILABLE', 'INVALID_INPUT', 'PERMANENT', 'TRANSIENT'"
)

# Created in this order; dropped in reverse.
TABLES = (
    "users",
    "tenants",
    "user_roles",
    "workflow_runs",
    "workflow_steps",
    "step_attempts",
    "idempotency_records",
    "domain_events",
    "outbox_events",
    "processed_events",
    "audit_events",
)


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.func.gen_random_uuid())


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    server_default = None if nullable else sa.func.now()
    return sa.Column(
        name, sa.DateTime(timezone=True), nullable=nullable, server_default=server_default
    )


def _jsonb(name: str, empty: str) -> sa.Column[Any]:
    return sa.Column(name, JSONB(), nullable=False, server_default=sa.text(f"'{empty}'::jsonb"))


def upgrade() -> None:
    op.create_table(
        "users",
        _id(),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer_subject"),
    )
    op.create_table(
        "tenants",
        _id(),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_tenants_owner_user_id_users"
        ),
        sa.UniqueConstraint("owner_user_id", name="uq_tenants_owner_user_id"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_tenants_id_owner_user_id"),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("granted_by_user_id", sa.Uuid(), nullable=True),
        _timestamp("granted_at"),
        sa.PrimaryKeyConstraint("user_id", "role", name="pk_user_roles"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_roles_user_id_users"),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"], ["users.id"], name="fk_user_roles_granted_by_user_id_users"
        ),
        sa.CheckConstraint("role IN ('operator', 'reviewer')", name="ck_user_roles_role"),
    )
    op.create_table(
        "workflow_runs",
        _id(),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("analysis_tier", sa.Text(), nullable=True),
        sa.Column("budget_tier", sa.Text(), nullable=True),
        _jsonb("input", "{}"),
        sa.Column("input_digest", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="QUEUED"),
        _timestamp("lease_expires_at", nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("launch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_class", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        _jsonb("trace_context", "{}"),
        _jsonb("result_refs", "[]"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _timestamp("started_at", nullable=True),
        _timestamp("finished_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_runs"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_workflow_runs_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"],
            ["workflow_runs.id"],
            name="fk_workflow_runs_parent_run_id_workflow_runs",
        ),
        sa.CheckConstraint(f"state IN ({WORKFLOW_STATES})", name="ck_workflow_runs_state"),
        sa.CheckConstraint(
            "analysis_tier IN ('BASELINE', 'DEEP')", name="ck_workflow_runs_analysis_tier"
        ),
        sa.CheckConstraint(
            "budget_tier IN ('BASELINE', 'DEEP', 'EXPERIMENT')", name="ck_workflow_runs_budget_tier"
        ),
        sa.CheckConstraint(
            f"failure_class IN ({FAILURE_CLASSES})", name="ck_workflow_runs_failure_class"
        ),
    )
    op.create_index(
        "ix_workflow_runs_tenant_id_state_created_at",
        "workflow_runs",
        ["tenant_id", "state", "created_at"],
    )
    op.create_index(
        "ix_workflow_runs_unfinished_updated_at",
        "workflow_runs",
        ["updated_at"],
        postgresql_where=sa.text("state IN ('QUEUED', 'RUNNING')"),
    )
    op.create_table(
        "workflow_steps",
        _id(),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="QUEUED"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_class", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("safe_explanation", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        _timestamp("next_attempt_at", nullable=True),
        _jsonb("result_refs", "[]"),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_steps"),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            name="fk_workflow_steps_workflow_run_id_workflow_runs",
        ),
        sa.UniqueConstraint(
            "workflow_run_id", "name", name="uq_workflow_steps_workflow_run_id_name"
        ),
        sa.CheckConstraint(f"state IN ({WORKFLOW_STATES})", name="ck_workflow_steps_state"),
        sa.CheckConstraint(
            f"failure_class IN ({FAILURE_CLASSES})", name="ck_workflow_steps_failure_class"
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_workflow_steps_attempt_count"),
    )
    op.create_table(
        "step_attempts",
        _id(),
        sa.Column("workflow_step_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("failure_class", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.Text(), nullable=True),
        _timestamp("started_at"),
        _timestamp("finished_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_step_attempts"),
        sa.ForeignKeyConstraint(
            ["workflow_step_id"],
            ["workflow_steps.id"],
            name="fk_step_attempts_workflow_step_id_workflow_steps",
        ),
        sa.UniqueConstraint(
            "workflow_step_id",
            "attempt_number",
            name="uq_step_attempts_workflow_step_id_attempt_number",
        ),
        sa.CheckConstraint(
            "outcome IN ('CANCELLED', 'COMPLETE', 'FAILED', 'PARTIAL')",
            name="ck_step_attempts_outcome",
        ),
        sa.CheckConstraint(
            f"failure_class IN ({FAILURE_CLASSES})", name="ck_step_attempts_failure_class"
        ),
        sa.CheckConstraint("attempt_number >= 1", name="ck_step_attempts_attempt_number"),
    )
    op.create_table(
        "idempotency_records",
        _id(),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("request_digest", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("result_id", sa.Uuid(), nullable=True),
        _timestamp("lease_expires_at", nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_records"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_idempotency_records_tenant_id_tenants"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "operation",
            "idempotency_key",
            name="uq_idempotency_records_tenant_id_operation_idempotency_key",
        ),
        sa.CheckConstraint(
            "state IN ('COMPLETE', 'FAILED', 'IN_PROGRESS')", name="ck_idempotency_records_state"
        ),
    )
    op.create_table(
        "domain_events",
        _id(),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("aggregate_type", sa.Text(), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_revision", sa.BigInteger(), nullable=False),
        _jsonb("payload", "{}"),
        _timestamp("occurred_at", nullable=False),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_domain_events"),
    )
    op.create_index(
        "ix_domain_events_aggregate_type_aggregate_id",
        "domain_events",
        ["aggregate_type", "aggregate_id"],
    )
    op.create_table(
        "outbox_events",
        _id(),
        sa.Column("domain_event_id", sa.Uuid(), nullable=False),
        sa.Column("destination", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="PENDING"),
        sa.Column("publish_attempts", sa.Integer(), nullable=False, server_default="0"),
        _jsonb("trace_context", "{}"),
        _timestamp("created_at"),
        _timestamp("published_at", nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
        sa.ForeignKeyConstraint(
            ["domain_event_id"],
            ["domain_events.id"],
            name="fk_outbox_events_domain_event_id_domain_events",
        ),
        sa.UniqueConstraint(
            "domain_event_id", "destination", name="uq_outbox_events_domain_event_id_destination"
        ),
        sa.CheckConstraint("state IN ('PENDING', 'PUBLISHED')", name="ck_outbox_events_state"),
        sa.CheckConstraint("publish_attempts >= 0", name="ck_outbox_events_publish_attempts"),
    )
    op.create_index(
        "ix_outbox_events_pending_created_at",
        "outbox_events",
        ["created_at"],
        postgresql_where=sa.text("state = 'PENDING'"),
    )
    op.create_table(
        "processed_events",
        sa.Column("consumer", sa.Text(), nullable=False),
        sa.Column("domain_event_id", sa.Uuid(), nullable=False),
        _timestamp("processed_at"),
        sa.PrimaryKeyConstraint("consumer", "domain_event_id", name="pk_processed_events"),
        sa.ForeignKeyConstraint(
            ["domain_event_id"],
            ["domain_events.id"],
            name="fk_processed_events_domain_event_id_domain_events",
        ),
    )
    op.create_table(
        "audit_events",
        _id(),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("principal_user_id", sa.Uuid(), nullable=True),
        sa.Column("principal_service", sa.Text(), nullable=True),
        sa.Column("search_subscription_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("target_revision", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        _jsonb("metadata", "{}"),
        _timestamp("created_at"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_audit_events_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "principal_user_id"],
            ["tenants.id", "tenants.owner_user_id"],
            name="fk_audit_events_tenant_id_principal_user_id_tenants",
        ),
        sa.CheckConstraint(
            "(principal_user_id IS NULL) <> (principal_service IS NULL)",
            name="ck_audit_events_principal",
        ),
    )
    op.create_index(
        "ix_audit_events_tenant_id_created_at", "audit_events", ["tenant_id", "created_at"]
    )

    rls = files("property_focus.adapters.db").joinpath("rls.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(rls)


def downgrade() -> None:
    """Drop the foundation tables, scope functions and default privileges.

    The roles stay: they are cluster-wide and may serve other databases.
    """
    for table in reversed(TABLES):
        op.drop_table(table)
    op.execute(
        "DROP FUNCTION pf_current_tenant_id(), pf_current_user_id(), pf_service_scope();"
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE MAINTAIN ON TABLES FROM pf_maintenance;"
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM pf_backup;"
        "REVOKE USAGE ON SCHEMA public FROM pf_runtime, pf_maintenance, pf_backup;"
    )
