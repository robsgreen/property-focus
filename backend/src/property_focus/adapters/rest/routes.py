"""REST routes: health probes and the authenticated ``/v1`` API.

Every ``/v1`` route depends on ``execution_context``, which verifies the bearer token and
resolves the caller's tenant; a route never takes a tenant from the path or body.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from property_focus.adapters.auth.oidc import OidcVerifier, authenticate
from property_focus.adapters.rest import schemas
from property_focus.application.context import ExecutionContext
from property_focus.application.workflows import read_workflow_run
from property_focus.domain.errors import CodedError

# Declares the bearer scheme in OpenAPI; missing credentials are reported by
# ``execution_context`` as a problem response, not by FastAPI.
_bearer = HTTPBearer(auto_error=False, description="An OIDC access token from the issuer.")

# Error statuses the routes document; ``app.py`` publishes their media type as
# application/problem+json.
PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": schemas.Problem} for status in (400, 401, 404, 503)
}

health = APIRouter(tags=["health"])
v1 = APIRouter(prefix="/v1", responses=PROBLEM_RESPONSES)


def _correlation_id(request: Request) -> str:
    """The request's correlation ID, set by the correlation middleware."""
    value: str = request.state.correlation_id
    return value


def _engine(request: Request) -> Engine:
    engine: Engine = request.app.state.engine
    return engine


async def execution_context(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> ExecutionContext:
    """Authenticate the request and return its execution context.

    Provisions the caller's user and tenant on their first request. Raises ``CodedError``
    ``unauthorized`` without a valid bearer access token.
    """
    if credentials is None:  # no Authorization header, or not the Bearer scheme
        raise CodedError("unauthorized", "Sign in to continue.")
    verifier: OidcVerifier = request.app.state.verifier
    return await authenticate(
        verifier,
        _engine(request),
        credentials.credentials,
        correlation_id=_correlation_id(request),
    )


Context = Annotated[ExecutionContext, Depends(execution_context)]


@health.get("/health/live", response_model=schemas.Health, operation_id="getLiveness")
def live() -> schemas.Health:
    """The process is serving requests."""
    return schemas.Health(status="ok")


@health.get(
    "/health/ready",
    response_model=schemas.Health,
    responses=PROBLEM_RESPONSES,
    operation_id="getReadiness",
)
def ready(request: Request) -> schemas.Health:
    """The process can serve requests: its database answers."""
    try:
        with _engine(request).connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise CodedError("dependency_transient", "The database is unavailable.") from error
    return schemas.Health(status="ok")


@v1.get(
    "/workflow-runs/{run_id}",
    response_model=schemas.WorkflowRun,
    tags=["workflow-runs"],
    operation_id="getWorkflowRun",
)
def get_workflow_run(run_id: uuid.UUID, context: Context, request: Request) -> schemas.WorkflowRun:
    """A workflow run of the caller's tenant, with its steps and results.

    Returns ``404`` for a run that does not exist and for one the caller may not see, with
    the same body.
    """
    return schemas.WorkflowRun.from_rows(*read_workflow_run(_engine(request), context, run_id))
