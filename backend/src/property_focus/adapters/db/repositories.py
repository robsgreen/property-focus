"""The scoped unit of work and scoped repositories over the runtime role's connections.

Every transaction runs in exactly one scope, which the database's row-level security
enforces (see ``rls.sql``):

- ``TenantScope``: a user acting in the tenant they own. Sees that tenant's private rows
  and workflow runs.
- ``ServiceScope``: a background task coordinating work across tenants. Sees every
  workflow run, event, user and tenant, and no tenant-private rows except expired
  idempotency records, which it may delete.

The scope is set with ``SET LOCAL`` at the start of each transaction, so PostgreSQL clears
it at commit or rollback and a pooled connection never carries it into the next one.
"""

import uuid
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

from sqlalchemy import Connection, Engine, event, func, select
from sqlalchemy.orm import Session, SessionTransaction

from property_focus.adapters.db.models import Base


@dataclass(frozen=True)
class TenantScope:
    """A user acting in the tenant they own: ``user_id`` is the tenant's owner."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(frozen=True)
class ServiceScope:
    """A background task, acting for no single tenant."""


type Scope = TenantScope | ServiceScope


class ScopeError(RuntimeError):
    """A read or write that the unit of work's scope does not allow."""


class UnitOfWork:
    """One database transaction in one scope.

    Use as a context manager: entering begins the transaction and applies the scope;
    leaving commits, or rolls back when the block raises. A transaction begun after an
    explicit ``session.commit()`` inside the block gets the same scope.
    """

    def __init__(self, engine: Engine, scope: Scope) -> None:
        self.scope = scope
        self._engine = engine
        self._session: Session | None = None

    def __enter__(self) -> Self:
        session = Session(self._engine, expire_on_commit=False)
        # Session-level listener: applies to every transaction this session begins.
        event.listen(session, "after_begin", self._apply_scope)
        session.begin()
        self._session = session
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self.session
        self._session = None
        try:
            if exc_type is None:
                session.commit()
            else:
                session.rollback()
        finally:
            session.close()

    @property
    def session(self) -> Session:
        """The SQLAlchemy session; raises ``ScopeError`` outside the ``with`` block."""
        if self._session is None:
            raise ScopeError("the unit of work is not active; use it in a with block")
        return self._session

    def repository[M: Base](self, model: type[M]) -> "ScopedRepository[M]":
        """A repository for ``model`` bound to this unit of work's session and scope."""
        return ScopedRepository(self.session, self.scope, model)

    def _apply_scope(
        self, _session: Session, _transaction: SessionTransaction, connection: Connection
    ) -> None:
        match self.scope:
            case TenantScope(tenant_id=tenant_id, user_id=user_id):
                settings = {"pf.tenant_id": str(tenant_id), "pf.user_id": str(user_id)}
            case ServiceScope():
                settings = {"pf.service_scope": "on"}
        for name, value in settings.items():
            # set_config(..., is_local => true) is SET LOCAL with a bound value.
            connection.execute(select(func.set_config(name, value, True)))


class ScopedRepository[M: Base]:
    """Adds and reads one model's rows inside a scope.

    For a table with a ``tenant_id`` column, the repository applies the scope explicitly,
    on top of row-level security:

    - In a tenant scope, ``add`` fills a missing ``tenant_id`` with the scope's tenant and
      refuses another tenant's; ``get`` reads only the scope's tenant.
    - In the service scope, tenant-private tables (``tenant_id NOT NULL``) are refused, and
      other tables are read and written as given.

    Tables without ``tenant_id`` hold no tenant data and pass through unchanged.
    """

    def __init__(self, session: Session, scope: Scope, model: type[M]) -> None:
        self._session = session
        self._scope = scope
        self._model = model
        tenant_column = model.__table__.columns.get("tenant_id")
        self._has_tenant = tenant_column is not None
        tenant_private = tenant_column is not None and not tenant_column.nullable
        if tenant_private and isinstance(scope, ServiceScope):
            raise ScopeError(f"{model.__tablename__} is tenant-private; use a tenant scope")

    def add(self, row: M) -> M:
        """Stage ``row`` for insert at the next flush and return it.

        Raises ``ScopeError`` when ``row`` belongs to a tenant other than the scope's.
        """
        if self._has_tenant and isinstance(self._scope, TenantScope):
            row_tenant = getattr(row, "tenant_id", None)
            if row_tenant is None:
                setattr(row, "tenant_id", self._scope.tenant_id)  # noqa: B010
            elif row_tenant != self._scope.tenant_id:
                raise ScopeError(f"{self._model.__tablename__} row belongs to another tenant")
        self._session.add(row)
        return row

    def get(self, primary_key: Any) -> M | None:
        """Return the row with ``primary_key`` (a tuple for a composite key) or None.

        A row of another tenant counts as absent.
        """
        row = self._session.get(self._model, primary_key)
        if (
            row is not None
            and self._has_tenant
            and isinstance(self._scope, TenantScope)
            and getattr(row, "tenant_id", None) != self._scope.tenant_id
        ):
            return None
        return row
