"""Tenant isolation: row-level security, runtime-role limits, scopes, tenant provisioning on
the first authenticated request, and cross-tenant reads through the REST API."""

import json
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import URL, Connection, Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from support.keycloak import ISSUER, LocalKeycloak, TestUser

from property_focus.adapters.db import create_database_engine
from property_focus.adapters.db.models import (
    AuditEvent,
    Base,
    IdempotencyRecord,
    Tenant,
    User,
    WorkflowRun,
    WorkflowStep,
)
from property_focus.adapters.db.repositories import (
    ScopeError,
    ServiceScope,
    TenantScope,
    UnitOfWork,
)
from property_focus.application.context import VerifiedIdentity, provision_tenant
from property_focus.application.workflows import (
    IDEMPOTENCY_TTL,
    NewEvent,
    record_event,
    utc_now,
)
from property_focus.cli import FIXTURES_DIR, SEED_DATASETS, main
from property_focus.config import Settings

NewTenant = Callable[[], TenantScope]

INSUFFICIENT_PRIVILEGE = "42501"


def _seed_private_rows(engine: Engine, tenant: TenantScope) -> None:
    """Write one row of each tenant-private kind, and a workflow run, for ``tenant``."""
    with UnitOfWork(engine, tenant) as uow:
        uow.repository(IdempotencyRecord).add(
            IdempotencyRecord(
                operation="add_property",
                idempotency_key="k1",
                request_digest="d",
                expires_at=utc_now() + IDEMPOTENCY_TTL,
            )
        )
        uow.repository(AuditEvent).add(
            AuditEvent(principal_user_id=tenant.user_id, action="create", target_type="test")
        )
        uow.repository(WorkflowRun).add(
            WorkflowRun(kind="test", input_digest="d", deadline_at=utc_now())
        )


def _set_tenant(connection: Connection, tenant: TenantScope) -> None:
    connection.execute(
        text("SELECT set_config('pf.tenant_id', :tenant, true)"), {"tenant": str(tenant.tenant_id)}
    )
    connection.execute(
        text("SELECT set_config('pf.user_id', :user, true)"), {"user": str(tenant.user_id)}
    )


def _record_tenant_event(engine: Engine, tenant: TenantScope) -> uuid.UUID:
    """Record one domain event in ``tenant``, with an outbox event and a processed mark;
    return the domain event's ID."""
    event = NewEvent(
        event_type="subject.changed",
        aggregate_type="property",
        aggregate_id=uuid.uuid4(),
        aggregate_revision=1,
        destinations=("record-subject",),
    )
    with UnitOfWork(engine, tenant) as uow:
        record_event(uow.session, event, tenant_id=tenant.tenant_id, now=utc_now())
        event_id = uow.session.execute(
            text("SELECT id FROM domain_events WHERE aggregate_id = :aggregate"),
            {"aggregate": event.aggregate_id},
        ).scalar_one()
        uow.session.execute(
            text(
                "INSERT INTO processed_events (consumer, domain_event_id, tenant_id) "
                "VALUES ('record-subject', :event, :tenant)"
            ),
            {"event": event_id, "tenant": tenant.tenant_id},
        )
    return uuid.UUID(str(event_id))


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


def test_runtime_role_cannot_read_another_tenants_rows_with_raw_sql(
    runtime_engine: Engine, new_tenant: NewTenant
) -> None:
    a, b = new_tenant(), new_tenant()
    _seed_private_rows(runtime_engine, a)
    _seed_private_rows(runtime_engine, b)

    for table in ("idempotency_records", "audit_events", "workflow_runs"):
        with runtime_engine.begin() as connection:
            _set_tenant(connection, b)
            # Asking for tenant A's rows by tenant ID, from tenant B's scope, finds none.
            rows_of_a = connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :a"), {"a": a.tenant_id}
            ).scalar_one()
            visible_tenants = connection.execute(
                text(f"SELECT DISTINCT tenant_id FROM {table}")
            ).scalars()
            assert rows_of_a == 0, table
            assert set(visible_tenants) == {b.tenant_id}, table

            if table != "audit_events":  # audit records are append-only for the runtime role
                changed = connection.execute(
                    text(f"UPDATE {table} SET created_at = now() WHERE tenant_id = :a"),
                    {"a": a.tenant_id},
                ).rowcount
                assert changed == 0, table

        # Outside any scope nothing tenant-scoped is visible.
        with runtime_engine.begin() as connection:
            assert connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


def test_runtime_role_cannot_read_another_users_identity_rows_with_raw_sql(
    runtime_engine: Engine, new_tenant: NewTenant
) -> None:
    a, b = new_tenant(), new_tenant()

    with runtime_engine.begin() as connection:
        _set_tenant(connection, b)
        users = connection.execute(text("SELECT id, email FROM users")).all()
        tenants = connection.execute(text("SELECT id FROM tenants")).scalars().all()
        user_a = connection.execute(
            text("SELECT count(*) FROM users WHERE id = :a"), {"a": a.user_id}
        ).scalar_one()
    assert [user_id for user_id, _ in users] == [b.user_id]
    assert tenants == [b.tenant_id]
    assert user_a == 0

    # Only the provisioning function changes a stored email.
    with runtime_engine.connect() as connection:
        _set_tenant(connection, b)
        with pytest.raises(DBAPIError) as raised:
            connection.execute(text("UPDATE users SET email = 'taken@example.test'"))
    assert _sqlstate(raised.value) == INSUFFICIENT_PRIVILEGE

    # Outside any scope no identity row is visible.
    with runtime_engine.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM tenants")).scalar_one() == 0


def test_runtime_role_cannot_read_another_tenants_events_with_raw_sql(
    runtime_engine: Engine, new_tenant: NewTenant
) -> None:
    a, b = new_tenant(), new_tenant()
    event_of_a, event_of_b = (
        _record_tenant_event(runtime_engine, a),
        _record_tenant_event(runtime_engine, b),
    )

    queries = {
        "domain_events": "SELECT id FROM domain_events",
        "outbox_events": "SELECT domain_event_id FROM outbox_events",
        "processed_events": "SELECT domain_event_id FROM processed_events",
    }
    for table, query in queries.items():
        with runtime_engine.begin() as connection:
            _set_tenant(connection, b)
            assert set(connection.execute(text(query)).scalars()) == {event_of_b}, table
        with runtime_engine.begin() as connection:
            assert connection.execute(text(query)).all() == [], table
        with UnitOfWork(runtime_engine, ServiceScope()) as uow:
            visible = set(uow.session.execute(text(query)).scalars())
            assert {event_of_a, event_of_b} <= visible, table


def test_account_provisioning_runs_through_a_pinned_security_definer_function(
    runtime_engine: Engine,
) -> None:
    with runtime_engine.connect() as connection:
        definer, config, public_execute, runtime_execute = connection.execute(
            text(
                "SELECT p.prosecdef, p.proconfig, "
                "has_function_privilege('public', p.oid, 'EXECUTE'), "
                "has_function_privilege('pf_runtime', p.oid, 'EXECUTE') "
                "FROM pg_proc p WHERE p.proname = 'pf_provision_account'"
            )
        ).one()

    assert definer
    assert "search_path=pg_catalog, public, pg_temp" in config
    assert (public_execute, runtime_execute) == (False, True)


def test_provisioning_stores_only_a_verified_email_and_replaces_it_when_it_changes(
    runtime_engine: Engine,
) -> None:
    subject = f"subject-{uuid.uuid4()}"

    def provision(email: str | None, verified: bool) -> tuple[str, bool]:
        identity = VerifiedIdentity(
            issuer=ISSUER, subject=subject, email=email, email_verified=verified
        )
        with runtime_engine.begin() as connection:
            scope = provision_tenant(connection, identity)
        with UnitOfWork(runtime_engine, scope) as uow:
            stored = uow.session.execute(
                select(User.email, User.email_verified).where(User.id == scope.user_id)
            ).one()
        return stored.email, stored.email_verified

    assert provision(None, False) == ("", False)
    assert provision("first@example.test", False) == ("", False)
    assert provision("first@example.test", True) == ("first@example.test", True)
    # A token without a verified email keeps the stored one.
    assert provision(None, False) == ("first@example.test", True)
    assert provision("second@example.test", True) == ("second@example.test", True)


def test_runtime_role_cannot_write_rows_into_another_tenant(
    runtime_engine: Engine, new_tenant: NewTenant
) -> None:
    a, b = new_tenant(), new_tenant()

    with runtime_engine.connect() as connection:
        _set_tenant(connection, b)
        with pytest.raises(DBAPIError) as raised:
            connection.execute(
                text(
                    "INSERT INTO idempotency_records (tenant_id, operation, idempotency_key, "
                    "request_digest) VALUES (:a, 'add_property', 'k', 'd')"
                ),
                {"a": a.tenant_id},
            )

    assert _sqlstate(raised.value) == INSUFFICIENT_PRIVILEGE


@pytest.mark.parametrize("outcome", ["commit", "rollback"])
def test_scope_does_not_leak_to_the_next_pooled_transaction(
    runtime_url: URL, new_tenant: NewTenant, outcome: str
) -> None:
    tenant = new_tenant()
    # One pooled connection, so the next transaction reuses the same server session.
    engine = create_database_engine(runtime_url, pool_size=1)
    try:
        abandoned = pytest.raises(RuntimeError) if outcome == "rollback" else nullcontext()
        with abandoned, UnitOfWork(engine, tenant) as uow:
            scoped_pid = uow.session.execute(text("SELECT pg_backend_pid()")).scalar_one()
            uow.repository(AuditEvent).add(
                AuditEvent(principal_user_id=tenant.user_id, action="a", target_type="t")
            )
            uow.session.flush()
            if outcome == "rollback":
                raise RuntimeError("abandon the transaction")

        with engine.begin() as connection:
            pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            tenant_setting = connection.execute(
                text("SELECT current_setting('pf.tenant_id', true)")
            ).scalar_one()
            user = connection.execute(
                text("SELECT current_setting('pf.user_id', true)")
            ).scalar_one()
            audit_rows = connection.execute(text("SELECT count(*) FROM audit_events")).scalar_one()
    finally:
        engine.dispose()

    assert pid == scoped_pid
    assert not tenant_setting
    assert not user
    assert audit_rows == 0


@pytest.mark.parametrize(
    "statement",
    [
        "ALTER TABLE audit_events DISABLE ROW LEVEL SECURITY",
        "ALTER TABLE audit_events NO FORCE ROW LEVEL SECURITY",
        "DROP POLICY account_rows ON audit_events",
        "ALTER ROLE pf_runtime BYPASSRLS",
    ],
)
def test_runtime_role_cannot_disable_row_security(runtime_engine: Engine, statement: str) -> None:
    with pytest.raises(DBAPIError) as raised, runtime_engine.begin() as connection:
        connection.execute(text(statement))

    assert _sqlstate(raised.value) == INSUFFICIENT_PRIVILEGE


def test_runtime_role_cannot_turn_row_security_off_for_its_session(
    runtime_engine: Engine,
) -> None:
    with runtime_engine.connect() as connection:
        connection.execute(text("SET LOCAL row_security = off"))
        with pytest.raises(DBAPIError) as raised:
            connection.execute(text("SELECT count(*) FROM audit_events"))

    assert _sqlstate(raised.value) == INSUFFICIENT_PRIVILEGE


def test_runtime_role_owns_nothing_and_cannot_bypass_row_security(runtime_engine: Engine) -> None:
    with runtime_engine.connect() as connection:
        superuser, bypass = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        owned = connection.execute(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relowner = "
                "(SELECT oid FROM pg_roles WHERE rolname = current_user)"
            )
        ).scalar_one()

    assert (superuser, bypass) == (False, False)
    assert owned == 0


def test_every_tenant_private_table_forces_row_security(runtime_engine: Engine) -> None:
    with runtime_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity AND c.relforcerowsecurity "
                "FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public' "
                "JOIN pg_attribute a ON a.attrelid = c.oid "
                "WHERE c.relkind = 'r' AND a.attname = 'tenant_id' AND a.attnotnull"
            )
        ).all()

    forced = {table: is_forced for table, is_forced in rows}
    assert {"idempotency_records", "audit_events"} <= forced.keys()
    assert all(forced.values()), forced


def test_identity_and_event_tables_force_row_security(runtime_engine: Engine) -> None:
    tables = ("users", "tenants", "domain_events", "outbox_events", "processed_events")
    with runtime_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT relname, relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE relnamespace = 'public'::regnamespace AND relname = ANY(:tables)"
            ),
            {"tables": list(tables)},
        ).all()

    assert dict(rows) == dict.fromkeys(tables, True)


def test_service_scope_sees_every_workflow_run_but_no_private_rows(
    runtime_engine: Engine, new_tenant: NewTenant
) -> None:
    a, b = new_tenant(), new_tenant()
    _seed_private_rows(runtime_engine, a)
    with UnitOfWork(runtime_engine, a) as uow:
        run_of_a = uow.repository(WorkflowRun).add(
            WorkflowRun(kind="test", input_digest="a", deadline_at=utc_now())
        )
        uow.session.flush()
        uow.repository(WorkflowStep).add(WorkflowStep(workflow_run_id=run_of_a.id, name="load"))
    with UnitOfWork(runtime_engine, ServiceScope()) as uow:
        shared_run = uow.repository(WorkflowRun).add(
            WorkflowRun(kind="test", input_digest="s", deadline_at=utc_now())
        )

    with UnitOfWork(runtime_engine, ServiceScope()) as uow:
        runs = uow.repository(WorkflowRun)
        assert runs.get(run_of_a.id) is not None
        assert runs.get(shared_run.id) is not None
        assert uow.session.execute(text("SELECT count(*) FROM workflow_steps")).scalar_one() >= 1
        assert uow.session.execute(text("SELECT count(*) FROM audit_events")).scalar_one() == 0
        with pytest.raises(ScopeError):
            uow.repository(AuditEvent)

    with UnitOfWork(runtime_engine, b) as uow:
        assert uow.repository(WorkflowRun).get(run_of_a.id) is None
        assert uow.repository(WorkflowRun).get(shared_run.id) is None
        steps = uow.session.execute(
            text("SELECT count(*) FROM workflow_steps WHERE workflow_run_id = :run"),
            {"run": run_of_a.id},
        ).scalar_one()
        assert steps == 0


def test_scoped_repository_stamps_the_scope_tenant_and_refuses_another(
    runtime_engine: Engine, new_tenant: NewTenant
) -> None:
    a, b = new_tenant(), new_tenant()
    with UnitOfWork(runtime_engine, a) as uow:
        records = uow.repository(IdempotencyRecord)
        own = records.add(
            IdempotencyRecord(
                operation="add_property",
                idempotency_key="k",
                request_digest="d",
                expires_at=utc_now() + IDEMPOTENCY_TTL,
            )
        )
        with pytest.raises(ScopeError):
            records.add(
                IdempotencyRecord(
                    tenant_id=b.tenant_id,
                    operation="add_property",
                    idempotency_key="k",
                    request_digest="d",
                    expires_at=utc_now() + IDEMPOTENCY_TTL,
                )
            )
    assert own.tenant_id == a.tenant_id

    with UnitOfWork(runtime_engine, b) as uow:
        assert uow.repository(IdempotencyRecord).get(own.id) is None


def test_unit_of_work_is_unusable_outside_its_block(runtime_engine: Engine) -> None:
    uow = UnitOfWork(runtime_engine, ServiceScope())

    with pytest.raises(ScopeError):
        uow.session  # noqa: B018


def test_models_match_the_migrated_schema(migration_engine: Engine) -> None:
    """Scoped repositories read a table's privacy from its model, so models must not drift."""
    with migration_engine.connect() as connection:
        differences = compare_metadata(MigrationContext.configure(connection), Base.metadata)

    assert differences == []


# Tenant provisioning and cross-tenant reads through the REST API, with tokens from the
# local Keycloak realm --------------------------------------------------------------------


def _bearer(keycloak: LocalKeycloak, user: TestUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {keycloak.pkce_sign_in(user).access_token}"}


def _tenants_of(engine: Engine, keycloak: LocalKeycloak, user: TestUser) -> list[TenantScope]:
    """Every (user, owned tenant) pair stored for ``user``'s issuer and subject."""
    subject = keycloak.pkce_sign_in(user).access_claims["sub"]
    with UnitOfWork(engine, ServiceScope()) as uow:
        rows = uow.session.execute(
            select(User.id, Tenant.id)
            .join(Tenant, Tenant.owner_user_id == User.id, isouter=True)
            .where(User.issuer == ISSUER, User.subject == subject)
        ).all()
    return [TenantScope(tenant_id=tenant_id, user_id=user_id) for user_id, tenant_id in rows]


def test_first_request_provisions_one_user_and_tenant_and_a_repeat_creates_nothing(
    api: TestClient, keycloak: LocalKeycloak, runtime_engine: Engine
) -> None:
    user = keycloak.create_user()
    assert _tenants_of(runtime_engine, keycloak, user) == []

    first = api.get(f"/v1/workflow-runs/{uuid.uuid4()}", headers=_bearer(keycloak, user))
    provisioned = _tenants_of(runtime_engine, keycloak, user)
    repeat = api.get(f"/v1/workflow-runs/{uuid.uuid4()}", headers=_bearer(keycloak, user))

    assert (first.status_code, repeat.status_code) == (404, 404)
    assert len(provisioned) == 1
    assert provisioned[0].tenant_id is not None
    assert _tenants_of(runtime_engine, keycloak, user) == provisioned
    with UnitOfWork(runtime_engine, provisioned[0]) as uow:
        email, verified = uow.session.execute(
            select(User.email, User.email_verified).where(User.id == provisioned[0].user_id)
        ).one()
    assert (email, verified) == (user.email, True)


def test_concurrent_first_requests_provision_one_user_and_tenant(
    api: TestClient, keycloak: LocalKeycloak, runtime_engine: Engine
) -> None:
    user = keycloak.create_user()
    headers = _bearer(keycloak, user)
    callers = 8
    start = threading.Barrier(callers)

    def first_request(_: int) -> int:
        start.wait()
        return api.get(f"/v1/workflow-runs/{uuid.uuid4()}", headers=headers).status_code

    with ThreadPoolExecutor(callers) as pool:
        statuses = list(pool.map(first_request, range(callers)))

    assert statuses == [404] * callers
    assert len(_tenants_of(runtime_engine, keycloak, user)) == 1


def test_another_tenants_workflow_run_looks_exactly_like_a_missing_one(
    api: TestClient, keycloak: LocalKeycloak, runtime_engine: Engine
) -> None:
    owner, stranger = keycloak.create_user(), keycloak.create_user()
    api.get(
        "/v1/workflow-runs/00000000-0000-0000-0000-000000000000", headers=_bearer(keycloak, owner)
    )
    (tenant,) = _tenants_of(runtime_engine, keycloak, owner)
    with UnitOfWork(runtime_engine, tenant) as uow:
        run = uow.repository(WorkflowRun).add(
            WorkflowRun(kind="test", input_digest="d", deadline_at=utc_now())
        )

    def get(run_id: uuid.UUID, user: TestUser) -> Response:
        headers = {**_bearer(keycloak, user), "X-Correlation-ID": "isolation-check"}
        return api.get(f"/v1/workflow-runs/{run_id}", headers=headers)

    own = get(run.id, owner)
    others = get(run.id, stranger)
    missing = get(uuid.uuid4(), stranger)

    assert own.status_code == 200
    assert own.json()["id"] == str(run.id)
    assert others.status_code == missing.status_code == 404
    assert others.content == missing.content
    assert others.headers["content-type"] == missing.headers["content-type"]


def test_seeding_the_synthetic_dataset_twice_creates_its_accounts_once(
    settings: Settings, runtime_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PF_ENVIRONMENT", "development")
    monkeypatch.setenv("PF_DATABASE_URL", settings.database_url)
    accounts = json.loads((FIXTURES_DIR / SEED_DATASETS["synthetic"]).read_text())["accounts"]
    subjects = [entry["subject"] for entry in accounts]

    def seeded() -> list[tuple[str, int]]:
        with UnitOfWork(runtime_engine, ServiceScope()) as uow:
            rows = uow.session.execute(
                select(User.subject, func.count(Tenant.id))
                .join(Tenant, Tenant.owner_user_id == User.id, isouter=True)
                .where(User.subject.in_(subjects))
                .group_by(User.subject)
                .order_by(User.subject)
            ).all()
        return [(subject, tenants) for subject, tenants in rows]

    assert main(["seed", "--dataset", "synthetic"]) == 0
    first = seeded()
    assert main(["seed", "--dataset", "synthetic"]) == 0

    assert first == [(subject, 1) for subject in sorted(subjects)]
    assert seeded() == first
