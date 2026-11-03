# Database and Tenancy

PostgreSQL 17 holds all relational state. Each person owns exactly one tenant (the account they sign up for), and the database itself enforces that one tenant cannot read another's private rows: every transaction runs in a declared scope, and row-level security (RLS) filters by it. The application connects as a role that cannot bypass RLS.

## Key files

| File | Role |
|---|---|
| `backend/src/property_focus/adapters/db/__init__.py` | `connect_runtime()`, `database_url()`, `create_database_engine()`, `migrate()` |
| `backend/src/property_focus/adapters/db/models.py` | SQLAlchemy models for the foundation tables |
| `backend/src/property_focus/adapters/db/repositories.py` | `UnitOfWork`, `TenantScope`, `ServiceScope`, `ScopedRepository`, `ScopeError` |
| `backend/src/property_focus/adapters/db/rls.sql` | Roles, grants, scope functions and RLS policies; executed by migration 0001, so its bytes are pinned by `tests/unit/test_migrations.py` |
| `backend/migrations/versions/0001_foundation.py` | Creates the tables, then runs `rls.sql` |
| `backend/migrations/env.py`, `backend/alembic.ini` | Alembic environment; connects as the migration owner |
| `backend/tests/integration/test_tenancy.py` | Cross-tenant isolation, role and privilege checks against real Postgres |

## Roles

| Role | Purpose | Notes |
|---|---|---|
| `pf_migrator` | Migration owner; owns the database and every table | Created once per cluster by an administrator (local Compose: `postgres-bootstrap` config; dogfood: `db-init`). `LOGIN CREATEROLE` (plus `CREATEDB` for test databases); not a superuser in deployed environments |
| `pf_runtime` | API, MCP adapter and tasks | Created by migration 0001 with `LOGIN NOSUPERUSER NOBYPASSRLS`, owns nothing, gets only explicit table grants. The migration refuses to continue if it is a superuser, has `BYPASSRLS`, or is a member of the owner or `pf_backup` |
| `pf_maintenance` | `VACUUM`, `ANALYZE`, `REINDEX`, `CLUSTER` via `MAINTAIN` | `NOLOGIN`; cannot read rows |
| `pf_backup` | `SELECT` on every table | `NOLOGIN`; the backup login also needs `BYPASSRLS` from an administrator |

Runtime grants: `SELECT, INSERT` on `users` and `tenants`, plus `UPDATE (email, email_verified, updated_at)` on `users`; `SELECT` on `user_roles`; `SELECT, INSERT, UPDATE` on the workflow tables and `outbox_events`; full DML on `idempotency_records`; `SELECT, INSERT` only on `domain_events`, `processed_events` and `audit_events` (append-only). Later migrations grant runtime privileges per table; default privileges extend `MAINTAIN` and backup `SELECT` automatically.

## Scopes and RLS

`UnitOfWork(engine, scope)` is one transaction. On every transaction begin it runs `set_config(name, value, true)` (`SET LOCAL`), so PostgreSQL clears the scope at commit or rollback and a pooled connection never leaks it.

| Scope | Settings | Sees |
|---|---|---|
| `TenantScope(tenant_id, user_id)` | `pf.tenant_id`, `pf.user_id` | That tenant's private rows and workflow runs |
| `ServiceScope()` | `pf.service_scope = on` | Every workflow run; no tenant-private rows |

SQL helpers `pf_current_tenant_id()`, `pf_current_user_id()` and `pf_service_scope()` read these settings. Policies, all with `FORCE ROW LEVEL SECURITY` so the owner is filtered too:

| Table | Policy |
|---|---|
| `idempotency_records`, `audit_events` | Row's `tenant_id` equals the scope's tenant |
| `workflow_runs` | Scope's tenant, or service scope |
| `workflow_steps` | Parent run is visible |
| `step_attempts` | Parent step is visible |
| `users`, `tenants`, `user_roles`, `domain_events`, `outbox_events`, `processed_events` | No RLS; no tenant-private content, narrow grants instead |

`ScopedRepository` (from `uow.repository(Model)`) applies the scope again in Python for tables with `tenant_id`: in a tenant scope `add()` fills a missing `tenant_id` and refuses another tenant's, and `get()` returns `None` for another tenant's row; in the service scope, constructing a repository for a tenant-private table (`tenant_id NOT NULL`) raises `ScopeError`.

```python
with UnitOfWork(engine, context.scope) as uow:  # TenantScope of the caller's ExecutionContext
    run = uow.repository(WorkflowRun).get(run_id)  # None for another tenant's run
```

## Tables

| Table | Key columns | Notes |
|---|---|---|
| `users` | `issuer`, `subject` (unique together), `email`, `email_verified` | One row per identity-provider identity |
| `tenants` | `owner_user_id` (unique) | Exactly one owned tenant per user |
| `user_roles` | (`user_id`, `role`) | System roles such as `reviewer`, `operator` |
| `workflow_runs` | `tenant_id` (NULL for shared work), `kind`, `input`, `input_digest`, `state`, `fencing_token`, `launch_count`, `lease_expires_at`, `parent_run_id`, `correlation_id`, `trace_context`, `deadline_at` (`NOT NULL`), `result_refs` | `analysis_tier` and `budget_tier` reserved for later phases; partial index `ix_workflow_runs_unfinished_updated_at` on `updated_at` where `state IN ('QUEUED', 'RUNNING')` for `repair` |
| `workflow_steps` | (`workflow_run_id`, `name`) unique, `attempt_count`, failure fields, `safe_explanation`, `next_attempt_at` | |
| `step_attempts` | (`workflow_step_id`, `attempt_number`) unique, `fencing_token`, `outcome` | |
| `idempotency_records` | (`tenant_id`, `operation`, `idempotency_key`) unique, `request_digest`, `result_id` | Tenant-private |
| `domain_events` | `event_type`, `schema_version`, `aggregate_type`, `aggregate_id`, `aggregate_revision`, `payload` | IDs and references only |
| `outbox_events` | (`domain_event_id`, `destination`) unique, `state` (`PENDING`/`PUBLISHED`), `publish_attempts`, `trace_context` | |
| `processed_events` | (`consumer`, `domain_event_id`) | Consumer-side deduplication |
| `audit_events` | `tenant_id`, `principal_user_id` or `principal_service` (exactly one), `action`, `target_*`, `reason`, `metadata` | Tenant-private; the (`tenant_id`, `principal_user_id`) foreign key to `tenants(id, owner_user_id)` keeps a user's action in their own tenant |

Workflow states and failure classes are enforced by check constraints in the migration.

## Connections and migrations

`connect_runtime(settings, pool_size=5)` is the one way the application opens the runtime role's pool: the REST and MCP servers use 5 connections, the task runtime 2, the CLI `seed` command 1. It calls `database_url(settings, role, secrets=None)`, which pairs each role with its URL setting and password secret (`runtime`: `PF_DATABASE_URL` and `database_password`; `migration`: `PF_MIGRATION_DATABASE_URL` and `migration_database_password`, used by `migrations/env.py`) and adds the password when the secret provider has one (the given `secrets`, so the task runtime builds its provider once, or else the provider the settings describe), and `create_database_engine(url, pool_size)`, which builds a pool with `max_overflow=0`, `pool_pre_ping`, `hide_parameters` (statement parameters never appear in errors or logs) and sessions in UTC. An injected engine (tests) is never disposed by the application. `migrate(settings)` (CLI `property-focus migrate`) runs `alembic upgrade head` as the migration owner.

Rules for schema changes: append one new migration under `backend/migrations/versions/`, never edit a released one (that includes `rls.sql`), add the new table's runtime grants and policies in that migration, and keep changes expand/contract so an application rollback never needs a schema rollback ([operations](../../operations.md)).

## Consumers

`application/context.py` (tenant provisioning), `application/workflows.py`, the REST routes, the CLI `seed` command and the task runtime.

## Design decisions

- Tenancy comes from the verified identity, and RLS backs up the application checks so a missed filter cannot leak another tenant's rows (A1, research note R5).
- Separate runtime, maintenance and backup roles keep the application from ever holding `BYPASSRLS`.
- `SET LOCAL` per transaction, not per connection, so pooling cannot carry a scope across requests.

## Related docs

- [Identity and authentication](./identity-and-auth.md) for how a tenant is provisioned
- [Workflows and tasks](./workflows-and-tasks.md) for the service scope in use
- [Configuration and secrets](./configuration-and-secrets.md)
