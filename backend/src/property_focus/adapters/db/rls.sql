-- Database roles, privileges and row-level security for the foundation tables.
--
-- Installed by migration 0001_foundation, which runs it as the migration owner after it
-- creates the tables. This file is part of that released migration: later migrations add
-- the grants and policies for their own tables and never edit this file.
--
-- Roles
--
--   pf_migrator     Migration owner. Owns the database and every table and runs
--                   migrations. It needs LOGIN, CREATEROLE and (for test databases)
--                   CREATEDB, and must not be a superuser in deployed environments. It is
--                   created once per cluster by an administrator, before the first
--                   migration (local Compose does this in its bootstrap script).
--   pf_runtime      The application (API, MCP adapter, tasks). Owns nothing, has no
--                   BYPASSRLS, and gets only the table privileges granted below, so row
--                   security always applies to it. Created here with LOGIN and no
--                   password; deployments set its password, or IAM authentication, as an
--                   administrator step and expose the password as the database_password
--                   secret.
--   pf_maintenance  Database maintenance (VACUUM, ANALYZE, REINDEX, CLUSTER) through the
--                   MAINTAIN privilege on every table; it cannot read or change rows.
--                   NOLOGIN: an operator's own login is granted this role when needed.
--   pf_backup       Backups. SELECT on every table, nothing else. NOLOGIN: the backup job's
--                   login is granted this role, and an administrator also gives that login
--                   BYPASSRLS, because a complete dump must read every account's rows. No
--                   runtime login ever gets BYPASSRLS or membership in this role.
--
-- Row-level security
--
--   Each unit of work sets its scope with SET LOCAL (transaction-scoped settings that
--   PostgreSQL clears at commit or rollback):
--     pf.tenant_id, pf.user_id  an account scope: the signed-in user and their account
--     pf.service_scope = 'on'   the service scope of background tasks, which coordinate
--                               workflow runs across accounts
--   Account-private tables (tenant_id NOT NULL) admit a row only in its own account's
--   scope; the service scope sees none of them. Workflow tables admit a run in its
--   account's scope and every run in the service scope; steps and attempts follow the
--   visibility of their run. FORCE ROW LEVEL SECURITY applies the policies to the table
--   owner too. Identity tables (users, tenants, user_roles) and the event tables hold no
--   account-private content and have no row security; the runtime role's privileges on
--   them are narrow instead.
--
-- Avoid percent signs and colon-prefixed words: the migration sends this file through
-- the database driver as one script.

-- Roles ---------------------------------------------------------------------------------

DO $roles$
BEGIN
    IF current_user = 'pf_runtime' THEN
        RAISE EXCEPTION 'migrations must run as the migration owner, not pf_runtime';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pf_runtime') THEN
        CREATE ROLE pf_runtime LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pf_maintenance') THEN
        CREATE ROLE pf_maintenance NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pf_backup') THEN
        CREATE ROLE pf_backup NOLOGIN;
    END IF;
    -- The roles exist cluster-wide and may predate this database; refuse a runtime role
    -- that could escape row security.
    IF EXISTS (
        SELECT FROM pg_roles
        WHERE rolname = 'pf_runtime' AND (rolsuper OR rolbypassrls)
    ) THEN
        RAISE EXCEPTION 'pf_runtime must not be a superuser or have BYPASSRLS';
    END IF;
    IF pg_has_role('pf_runtime', current_user, 'USAGE')
        OR pg_has_role('pf_runtime', 'pf_backup', 'USAGE') THEN
        RAISE EXCEPTION 'pf_runtime must not be a member of the migration owner or pf_backup';
    END IF;
END
$roles$;

-- Privileges ----------------------------------------------------------------------------

GRANT USAGE ON SCHEMA public TO pf_runtime, pf_maintenance, pf_backup;

GRANT SELECT, INSERT ON users, tenants TO pf_runtime;
GRANT UPDATE (email, email_verified, updated_at) ON users TO pf_runtime;
GRANT SELECT ON user_roles TO pf_runtime;
GRANT SELECT, INSERT, UPDATE ON workflow_runs, workflow_steps, step_attempts TO pf_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON idempotency_records TO pf_runtime;
-- Events and audit records are append-only for the application.
GRANT SELECT, INSERT ON domain_events, processed_events, audit_events TO pf_runtime;
GRANT SELECT, INSERT, UPDATE ON outbox_events TO pf_runtime;

GRANT MAINTAIN ON ALL TABLES IN SCHEMA public TO pf_maintenance;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO pf_backup;
-- Tables later migrations create get the same maintenance and backup privileges. Runtime
-- privileges stay explicit per table.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT MAINTAIN ON TABLES TO pf_maintenance;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO pf_backup;

-- Scope functions -----------------------------------------------------------------------

-- The account of the current transaction's scope, or NULL outside an account scope.
CREATE FUNCTION pf_current_tenant_id() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    RETURN nullif(current_setting('pf.tenant_id', true), '')::uuid;

-- The signed-in user of the current transaction's scope, or NULL outside an account scope.
CREATE FUNCTION pf_current_user_id() RETURNS uuid
    LANGUAGE sql STABLE PARALLEL SAFE
    RETURN nullif(current_setting('pf.user_id', true), '')::uuid;

-- True only inside the service scope of a background task.
CREATE FUNCTION pf_service_scope() RETURNS boolean
    LANGUAGE sql STABLE PARALLEL SAFE
    RETURN coalesce(current_setting('pf.service_scope', true), '') = 'on';

-- Account-private tables ----------------------------------------------------------------

ALTER TABLE idempotency_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_records FORCE ROW LEVEL SECURITY;
CREATE POLICY account_rows ON idempotency_records
    USING (tenant_id = pf_current_tenant_id())
    WITH CHECK (tenant_id = pf_current_tenant_id());

ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY account_rows ON audit_events
    USING (tenant_id = pf_current_tenant_id())
    WITH CHECK (tenant_id = pf_current_tenant_id());

-- Workflow tables -----------------------------------------------------------------------

ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY account_or_service_rows ON workflow_runs
    USING (tenant_id = pf_current_tenant_id() OR pf_service_scope())
    WITH CHECK (tenant_id = pf_current_tenant_id() OR pf_service_scope());

-- The subqueries are themselves filtered by the parent table's policy.
ALTER TABLE workflow_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_steps FORCE ROW LEVEL SECURITY;
CREATE POLICY visible_run_rows ON workflow_steps
    USING (EXISTS (SELECT FROM workflow_runs r WHERE r.id = workflow_run_id));

ALTER TABLE step_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE step_attempts FORCE ROW LEVEL SECURITY;
CREATE POLICY visible_step_rows ON step_attempts
    USING (EXISTS (SELECT FROM workflow_steps s WHERE s.id = workflow_step_id));
