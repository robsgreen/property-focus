"""Row security for identity and event tables, outbox claims and idempotency expiry.

- ``users`` and ``tenants`` get forced row-level security: a tenant scope sees only its
  own user and tenant, the service scope sees every row. Signing in happens before the
  caller's tenant is known, so it goes through ``pf_provision_account``, a SECURITY
  DEFINER function that only the runtime role may execute.
- ``domain_events``, ``outbox_events`` and ``processed_events`` gain a ``tenant_id``
  (NULL for shared work) and forced row-level security: a tenant scope sees its own
  tenant's events, the service scope sees every event. Existing events take the tenant
  of the runs they launched.
- ``outbox_events.claim_expires_at`` marks an event a publisher is launching, so the
  launch can run outside any transaction without a sweep launching it as well.
- ``idempotency_records.expires_at`` bounds how long a key replays; the scheduled
  ``repair`` task deletes expired records in the service scope.

Must run as the migration owner, which owns the function.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

EVENT_TABLES = ("domain_events", "outbox_events", "processed_events")

# Sent through the database driver as one script: avoid percent signs and colon-prefixed
# words.
UPGRADE_SQL = """
-- Existing rows predate the tenant columns; the runs an event launched carry its tenant.
-- workflow_runs forces row security on its owner too, so read it in the service scope.
SELECT set_config('pf.service_scope', 'on', true);
UPDATE domain_events d SET tenant_id = r.tenant_id
    FROM workflow_runs r
    WHERE r.input ->> 'domain_event_id' = d.id::text AND r.tenant_id IS NOT NULL;
UPDATE outbox_events o SET tenant_id = d.tenant_id
    FROM domain_events d WHERE d.id = o.domain_event_id;
UPDATE processed_events p SET tenant_id = d.tenant_id
    FROM domain_events d WHERE d.id = p.domain_event_id;
SELECT set_config('pf.service_scope', '', true);

-- Identity tables: a tenant scope sees its own user and tenant; the service scope (which
-- resolves a run's tenant owner) sees every row.
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE users FORCE ROW LEVEL SECURITY;
CREATE POLICY own_or_service_rows ON users
    USING (id = pf_current_user_id() OR pf_service_scope())
    WITH CHECK (id = pf_current_user_id() OR pf_service_scope());

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY own_or_service_rows ON tenants
    USING (id = pf_current_tenant_id() OR pf_service_scope())
    WITH CHECK (id = pf_current_tenant_id() OR pf_service_scope());

-- Only pf_provision_account changes a user's email now.
REVOKE UPDATE (email, email_verified, updated_at) ON users FROM pf_runtime;

-- Event tables: an account's events in its scope, every event in the service scope.
ALTER TABLE domain_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE domain_events FORCE ROW LEVEL SECURITY;
CREATE POLICY account_or_service_rows ON domain_events
    USING (tenant_id = pf_current_tenant_id() OR pf_service_scope())
    WITH CHECK (tenant_id = pf_current_tenant_id() OR pf_service_scope());

ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox_events FORCE ROW LEVEL SECURITY;
CREATE POLICY account_or_service_rows ON outbox_events
    USING (tenant_id = pf_current_tenant_id() OR pf_service_scope())
    WITH CHECK (tenant_id = pf_current_tenant_id() OR pf_service_scope());

ALTER TABLE processed_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE processed_events FORCE ROW LEVEL SECURITY;
CREATE POLICY account_or_service_rows ON processed_events
    USING (tenant_id = pf_current_tenant_id() OR pf_service_scope())
    WITH CHECK (tenant_id = pf_current_tenant_id() OR pf_service_scope());

-- The service scope may read and delete expired idempotency records (the repair task's
-- pruning) and nothing else of them.
CREATE POLICY expired_rows_for_service ON idempotency_records FOR SELECT
    USING (pf_service_scope() AND expires_at <= now());
CREATE POLICY prune_expired_rows ON idempotency_records FOR DELETE
    USING (pf_service_scope() AND expires_at <= now());

-- Provisioning on sign-in. The caller's user and tenant are unknown until this returns,
-- so no scope admits the rows; the function runs as the table owner, switches to the
-- service scope for its own statements and restores the caller's scope before it returns
-- (an error aborts the transaction or savepoint, which undoes the switch too). It touches
-- only the caller's identity. A SET clause cannot switch the scope: PostgreSQL lets only
-- superusers attach custom settings to a function.
--
-- Returns the user for (p_issuer, p_subject) and the tenant they own, creating either
-- when absent; concurrent calls for one new identity create one of each. p_email is the
-- verified email, or '' when the token has none: a non-empty one replaces the stored
-- email and marks it verified.
CREATE FUNCTION pf_provision_account(p_issuer text, p_subject text, p_email text)
    RETURNS TABLE (account_user_id uuid, account_tenant_id uuid)
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path = pg_catalog, public, pg_temp
AS $provision$
DECLARE
    caller_service_scope text := current_setting('pf.service_scope', true);
    found_email text;
    found_verified boolean;
BEGIN
    PERFORM set_config('pf.service_scope', 'on', true);
    SELECT u.id, u.email, u.email_verified
        INTO account_user_id, found_email, found_verified
        FROM public.users u WHERE u.issuer = p_issuer AND u.subject = p_subject;
    IF account_user_id IS NULL THEN
        -- A concurrent first request may win the insert; read its row back.
        INSERT INTO public.users (issuer, subject, email, email_verified)
            VALUES (p_issuer, p_subject, p_email, p_email <> '')
            ON CONFLICT (issuer, subject) DO NOTHING;
        SELECT u.id INTO STRICT account_user_id
            FROM public.users u WHERE u.issuer = p_issuer AND u.subject = p_subject;
    ELSIF p_email <> '' AND (found_email, found_verified) IS DISTINCT FROM (p_email, true) THEN
        UPDATE public.users u SET email = p_email, email_verified = true, updated_at = now()
            WHERE u.id = account_user_id;
    END IF;

    SELECT t.id INTO account_tenant_id
        FROM public.tenants t WHERE t.owner_user_id = account_user_id;
    IF account_tenant_id IS NULL THEN
        INSERT INTO public.tenants (owner_user_id) VALUES (account_user_id)
            ON CONFLICT (owner_user_id) DO NOTHING;
        SELECT t.id INTO STRICT account_tenant_id
            FROM public.tenants t WHERE t.owner_user_id = account_user_id;
    END IF;
    PERFORM set_config('pf.service_scope', coalesce(caller_service_scope, ''), true);
    RETURN NEXT;
END
$provision$;

REVOKE ALL ON FUNCTION pf_provision_account(text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pf_provision_account(text, text, text) TO pf_runtime;
"""

DOWNGRADE_SQL = """
DROP FUNCTION pf_provision_account(text, text, text);
DROP POLICY expired_rows_for_service ON idempotency_records;
DROP POLICY prune_expired_rows ON idempotency_records;
DROP POLICY account_or_service_rows ON processed_events;
DROP POLICY account_or_service_rows ON outbox_events;
DROP POLICY account_or_service_rows ON domain_events;
ALTER TABLE processed_events NO FORCE ROW LEVEL SECURITY;
ALTER TABLE processed_events DISABLE ROW LEVEL SECURITY;
ALTER TABLE outbox_events NO FORCE ROW LEVEL SECURITY;
ALTER TABLE outbox_events DISABLE ROW LEVEL SECURITY;
ALTER TABLE domain_events NO FORCE ROW LEVEL SECURITY;
ALTER TABLE domain_events DISABLE ROW LEVEL SECURITY;
GRANT UPDATE (email, email_verified, updated_at) ON users TO pf_runtime;
DROP POLICY own_or_service_rows ON tenants;
DROP POLICY own_or_service_rows ON users;
ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY;
ALTER TABLE tenants DISABLE ROW LEVEL SECURITY;
ALTER TABLE users NO FORCE ROW LEVEL SECURITY;
ALTER TABLE users DISABLE ROW LEVEL SECURITY;
"""


def upgrade() -> None:
    for table in EVENT_TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_tenant_id_tenants", table, "tenants", ["tenant_id"], ["id"]
        )
    op.add_column(
        "outbox_events", sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Records written before this migration expire a day after it runs.
    op.add_column(
        "idempotency_records",
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '24 hours'"),
        ),
    )
    op.create_index("ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"])
    op.get_bind().exec_driver_sql(UPGRADE_SQL)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(DOWNGRADE_SQL)
    op.drop_index("ix_idempotency_records_expires_at", "idempotency_records")
    op.drop_column("idempotency_records", "expires_at")
    op.drop_column("outbox_events", "claim_expires_at")
    for table in reversed(EVENT_TABLES):
        op.drop_constraint(f"fk_{table}_tenant_id_tenants", table, type_="foreignkey")
        op.drop_column(table, "tenant_id")
