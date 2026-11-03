"""Who is calling: the verified identity behind a request and the execution context built from it.

Both request adapters (REST and MCP) verify the bearer token, then call ``resolve_context``,
which maps the token's issuer and subject to the internal ``users`` row and the tenant that
user owns, creating both on the first authenticated request (A1). The tenant always comes
from this mapping, never from a path, query or body field.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, select, update
from sqlalchemy.dialects.postgresql import insert

from property_focus.adapters.db.models import Tenant, User, UserRole
from property_focus.adapters.db.repositories import TenantScope


@dataclass(frozen=True)
class VerifiedIdentity:
    """The caller as a validated access token describes them.

    ``email`` is the token's email claim when it has one; ``email_verified`` is true only
    when the token says so explicitly.
    """

    issuer: str
    subject: str
    email: str | None = None
    email_verified: bool = False


@dataclass(frozen=True)
class ExecutionContext:
    """What every use case knows about the caller of one request.

    ``principal_id`` is the internal user ID (``users.id``) of the caller and ``tenant_id``
    the tenant that user owns. Search access is not carried here: each search-scoped call
    checks the caller's active membership inside its transaction. ``roles`` are the system
    roles (``reviewer``, ``operator``) granted in ``user_roles``.
    """

    principal_id: uuid.UUID
    tenant_id: uuid.UUID
    entitlement_ids: tuple[uuid.UUID, ...]
    correlation_id: str
    roles: frozenset[str]

    @property
    def scope(self) -> TenantScope:
        """The scope the caller's own reads and writes run in: their tenant."""
        return TenantScope(tenant_id=self.tenant_id, user_id=self.principal_id)


def provision_tenant(connection: Connection, identity: VerifiedIdentity) -> TenantScope:
    """Return the scope of the tenant ``identity`` owns, creating its user and tenant if absent.

    Runs in the caller's transaction. Concurrent calls for the same new identity create one
    user and one tenant: inserts that lose the race do nothing, and the rows are read back.
    The email is stored only when the token marks it verified; a newly verified or changed
    email replaces the stored one. Unverified identities are stored with an empty email.
    """
    email = identity.email if identity.email_verified and identity.email else ""
    by_identity = (User.issuer == identity.issuer) & (User.subject == identity.subject)

    user = connection.execute(
        select(User.id, User.email, User.email_verified).where(by_identity)
    ).one_or_none()
    if user is None:
        connection.execute(
            insert(User)
            .values(
                issuer=identity.issuer,
                subject=identity.subject,
                email=email,
                email_verified=bool(email),
            )
            .on_conflict_do_nothing(index_elements=[User.issuer, User.subject])
        )
        user = connection.execute(
            select(User.id, User.email, User.email_verified).where(by_identity)
        ).one()
    elif email and (user.email, user.email_verified) != (email, True):
        connection.execute(
            update(User).where(User.id == user.id).values(email=email, email_verified=True)
        )

    tenant_id = connection.execute(
        select(Tenant.id).where(Tenant.owner_user_id == user.id)
    ).scalar_one_or_none()
    if tenant_id is None:
        connection.execute(
            insert(Tenant)
            .values(owner_user_id=user.id)
            .on_conflict_do_nothing(index_elements=[Tenant.owner_user_id])
        )
        tenant_id = connection.execute(
            select(Tenant.id).where(Tenant.owner_user_id == user.id)
        ).scalar_one()
    return TenantScope(tenant_id=tenant_id, user_id=user.id)


def resolve_context(
    engine: Engine, identity: VerifiedIdentity, *, correlation_id: str
) -> ExecutionContext:
    """Build the execution context for a request from a verified ``identity``.

    Provisions the caller's user and tenant on their first request (``provision_tenant``)
    and reads their system roles, in one transaction on the runtime role. The transaction
    sets no tenant scope: it touches only the identity tables, which hold no tenant-private
    rows.
    """
    with engine.begin() as connection:
        scope = provision_tenant(connection, identity)
        roles = connection.execute(
            select(UserRole.role).where(UserRole.user_id == scope.user_id)
        ).scalars()
        return ExecutionContext(
            principal_id=scope.user_id,
            tenant_id=scope.tenant_id,
            # Entitlements to licensed shared intelligence arrive with their tables.
            entitlement_ids=(),
            correlation_id=correlation_id,
            roles=frozenset(roles),
        )
