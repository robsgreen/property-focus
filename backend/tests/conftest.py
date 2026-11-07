"""Fixtures for the integration and contract suites, which run against local Compose services.

A freshly migrated database on a real PostgreSQL server: each test session creates its own
database as the migration owner, migrates it through the same code path as
``property-focus migrate``, and drops it at the end. The server is
the one ``PF_MIGRATION_DATABASE_URL`` and ``PF_DATABASE_URL`` name (by default the local
Compose ``postgres`` service); only the database name is replaced.

The local Keycloak realm issues the tokens, and ``api`` serves the REST application on the
test database with the verifier for that realm. Unit tests request none of these fixtures.
"""

import asyncio
import os
import uuid
from collections.abc import Callable, Iterator
from urllib.error import URLError

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
from support.keycloak import KEYCLOAK_URL, LocalKeycloak

from property_focus.adapters.auth.oidc import OidcVerifier, verifier_for
from property_focus.adapters.db import DatabaseRole, create_database_engine, database_url, migrate
from property_focus.adapters.db.models import Tenant, User
from property_focus.adapters.db.repositories import ServiceScope, TenantScope, UnitOfWork
from property_focus.adapters.rest.app import create_app
from property_focus.config import Settings


def _resolved(settings: Settings, role: DatabaseRole) -> URL:
    return asyncio.run(database_url(settings, role))


@pytest.fixture(scope="session")
def settings() -> Iterator[Settings]:
    """Settings naming a new, migrated database that is dropped after the session, with the
    MCP endpoint at ``http://testserver``, where in-process test clients send requests."""
    base = Settings.from_environment({"PF_ENVIRONMENT": "development", **os.environ})
    name = f"pf_test_{uuid.uuid4().hex[:12]}"
    admin_url = _resolved(base, "migration")
    admin = create_engine(
        admin_url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"'))
    except OperationalError as error:
        pytest.fail(
            f"PostgreSQL is not reachable at {admin_url.host}:{admin_url.port} "
            f"({error.orig}); start it with `docker compose up -d postgres`"
        )

    def renamed(url: str) -> str:
        return make_url(url).set(database=name).render_as_string(hide_password=False)

    test_settings = base.model_copy(
        update={
            "database_url": renamed(base.database_url),
            "migration_database_url": renamed(base.migration_database_url),
            # The MCP application as in-process test clients reach it.
            "mcp_public_url": "http://testserver/mcp",
            "mcp_allowed_hosts": ("testserver",),
            "mcp_allowed_origins": ("http://testserver",),
        }
    )
    try:
        migrate(test_settings)
        yield test_settings
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture(scope="session")
def runtime_url(settings: Settings) -> URL:
    """The runtime role's URL for the test database."""
    return _resolved(settings, "runtime")


@pytest.fixture(scope="session")
def runtime_engine(runtime_url: URL) -> Iterator[Engine]:
    """A connection pool for the runtime role, as the application uses it."""
    engine = create_database_engine(runtime_url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def migration_engine(settings: Settings) -> Iterator[Engine]:
    """A connection to the test database as the migration owner."""
    engine = create_engine(
        _resolved(settings, "migration"),
        poolclass=NullPool,
    )
    yield engine
    engine.dispose()


@pytest.fixture
def new_tenant(runtime_engine: Engine) -> Callable[[], TenantScope]:
    """Create a user and the tenant they own; return that tenant's scope."""

    def create() -> TenantScope:
        user = User(
            issuer="https://issuer.test",
            subject=str(uuid.uuid4()),
            email="buyer@example.test",
            email_verified=True,
        )
        with UnitOfWork(runtime_engine, ServiceScope()) as uow:
            uow.session.add(user)
            uow.session.flush()
            tenant = Tenant(owner_user_id=user.id)
            uow.session.add(tenant)
            uow.session.flush()
        return TenantScope(tenant_id=tenant.id, user_id=user.id)

    return create


@pytest.fixture(scope="session")
def keycloak() -> Iterator[LocalKeycloak]:
    """The local Keycloak ``property-focus`` realm; the users and clients it creates are
    deleted after the session."""
    local = LocalKeycloak()
    try:
        local.realm.discovery  # noqa: B018 - fails fast when Keycloak is down
    except URLError as error:
        pytest.fail(
            f"Keycloak is not reachable at {KEYCLOAK_URL} ({error.reason}); start it with "
            "`docker compose up -d --wait keycloak mailpit`"
        )
    yield local
    local.close()


@pytest.fixture(scope="session")
def verifier(settings: Settings, keycloak: LocalKeycloak) -> OidcVerifier:
    """The verifier for the configured issuer, the local Keycloak realm."""
    return verifier_for(settings)


@pytest.fixture(scope="session")
def api(settings: Settings, runtime_engine: Engine, verifier: OidcVerifier) -> Iterator[TestClient]:
    """An HTTP client for the REST application, served on the test database."""
    app = create_app(settings, engine=runtime_engine, verifier=verifier)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
