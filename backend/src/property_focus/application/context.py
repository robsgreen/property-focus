"""Who is calling: the verified identity behind a request and the execution context built from it.

Both request adapters (REST and MCP) verify the bearer token, then call ``resolve_context``,
which maps the token's issuer and subject to the internal ``users`` row and the tenant that
user owns, creating both on the first authenticated request (A1). The tenant always comes
from this mapping, never from a path, query or body field.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import Connection, Engine, select, text

from property_focus.adapters.db.models import UserRole
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

    Runs in the caller's transaction, through the database function
    ``pf_provision_account`` (migration 0002): row security shows a tenant scope only its
    own user and tenant, and before this call the caller has no scope. Concurrent calls
    for the same new identity create one user and one tenant. The email is stored only
    when the token marks it verified; a newly verified or changed email replaces the
    stored one. Unverified identities are stored with an empty email.
    """
    email = identity.email if identity.email_verified and identity.email else ""
    user_id, tenant_id = connection.execute(
        text(
            "SELECT account_user_id, account_tenant_id "
            "FROM pf_provision_account(:issuer, :subject, :email)"
        ),
        {"issuer": identity.issuer, "subject": identity.subject, "email": email},
    ).one()
    return TenantScope(tenant_id=tenant_id, user_id=user_id)


def resolve_context(
    engine: Engine, identity: VerifiedIdentity, *, correlation_id: str
) -> ExecutionContext:
    """Build the execution context for a request from a verified ``identity``.

    Provisions the caller's user and tenant on their first request (``provision_tenant``)
    and reads their system roles, in one transaction on the runtime role. The transaction
    sets no tenant scope: provisioning runs in its own database function, and
    ``user_roles`` holds no tenant-private rows.
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
