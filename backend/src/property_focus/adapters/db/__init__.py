"""PostgreSQL adapter: connections, schema migrations, models and the scoped unit of work.

Two database roles connect: the migration owner (``Settings.migration_database_url``) runs
``migrate`` and owns every table; the runtime role (``Settings.database_url``) serves the
application through ``repositories.UnitOfWork`` and is subject to row-level security.
``rls.sql`` documents the full role model. Migration 0001 executes it, so it never changes:
roles, privileges and policies change in later migrations.
"""

from datetime import timedelta
from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, Engine, create_engine, make_url

from property_focus.adapters.secrets import secret_provider_for
from property_focus.application.ports import SecretNotFoundError, SecretProvider
from property_focus.config import Settings

# backend/alembic.ini, found relative to this package in the source tree and in images.
ALEMBIC_INI = Path(__file__).resolve().parents[4] / "alembic.ini"

# A session left idle inside a transaction this long is ended by the server, releasing its
# locks. Every runtime transaction is short (a request's unit of work, a lease grant, a
# task's final commit, an outbox claim): none waits on anything but the database, and the
# longest takes well under a second, so a minute only ever catches a leak.
IDLE_IN_TRANSACTION_TIMEOUT = timedelta(minutes=1)

# The two roles that connect: the runtime role serves the application, the migration owner
# runs migrations.
type DatabaseRole = Literal["runtime", "migration"]
# Role -> the Settings field holding its URL and the logical secret holding its password.
_ROLE_CONNECTIONS: dict[DatabaseRole, tuple[str, str]] = {
    "runtime": ("database_url", "database_password"),
    "migration": ("migration_database_url", "migration_database_password"),
}


async def database_url(
    settings: Settings,
    role: DatabaseRole = "runtime",
    *,
    secrets: SecretProvider | None = None,
) -> URL:
    """Return the URL ``role`` connects with, including its password from ``secrets``
    (by default the secret provider ``settings`` describe).

    When no password secret is configured the URL is returned without a password, and
    libpq uses its own sources (a password file, ``PGPASSWORD``, or a server that needs
    none, as in local Compose).
    """
    url_field, password_secret = _ROLE_CONNECTIONS[role]
    parsed = make_url(getattr(settings, url_field))
    try:
        password = await (secrets or secret_provider_for(settings)).get(password_secret)
    except SecretNotFoundError:
        return parsed
    return parsed.set(password=password)


def create_database_engine(url: str | URL, *, pool_size: int = 5) -> Engine:
    """Create a bounded connection pool whose sessions use UTC.

    The pool holds at most ``pool_size`` connections and never overflows, so a process
    cannot exhaust the server's connection limit. The server ends a session left idle in a
    transaction for ``IDLE_IN_TRANSACTION_TIMEOUT``. Statement parameters (which can hold
    private values such as email addresses) are left out of error messages and logs.
    """
    idle_milliseconds = int(IDLE_IN_TRANSACTION_TIMEOUT.total_seconds() * 1000)
    return create_engine(
        url,
        pool_size=pool_size,
        max_overflow=0,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "options": (
                f"-c timezone=UTC -c idle_in_transaction_session_timeout={idle_milliseconds}"
            )
        },
    )


async def connect_runtime(
    settings: Settings, *, pool_size: int = 5, secrets: SecretProvider | None = None
) -> Engine:
    """The runtime role's connection pool for ``settings``, holding at most ``pool_size``.

    The password comes from ``secrets``, by default the secret provider ``settings``
    describe. The caller disposes of the pool.
    """
    url = await database_url(settings, secrets=secrets)
    return create_database_engine(url, pool_size=pool_size)


def migrate(settings: Settings, revision: str = "head") -> None:
    """Upgrade the database ``settings`` names to ``revision`` as the migration owner.

    Raises ``FileNotFoundError`` when ``alembic.ini`` is missing next to the package.
    """
    if not ALEMBIC_INI.is_file():
        raise FileNotFoundError(f"Alembic configuration not found at {ALEMBIC_INI}")
    config = Config(ALEMBIC_INI)
    config.attributes["settings"] = settings
    command.upgrade(config, revision)
