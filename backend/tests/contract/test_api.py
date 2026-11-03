"""The REST API's wire contract: problem responses, authentication, correlation IDs, health
probes and the first authenticated route, with tokens from the local Keycloak realm."""

import uuid
from collections.abc import Callable

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, select
from support.keycloak import ISSUER, LocalKeycloak, Tokens

from property_focus.adapters.auth.oidc import OidcVerifier
from property_focus.adapters.db.models import Tenant, User, WorkflowRun, WorkflowStep
from property_focus.adapters.db.repositories import TenantScope, UnitOfWork
from property_focus.adapters.rest import schemas
from property_focus.adapters.rest.app import create_app
from property_focus.application.workflows import admit_run, utc_now
from property_focus.config import Settings

PROBLEM_FIELDS = set(schemas.Problem.model_fields) - {"retry_after_seconds"}


def assert_problem(response: Response, status: int, code: str) -> dict[str, object]:
    """Check ``response`` is a problem of ``status`` and ``code``; return its body."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body: dict[str, object] = response.json()
    assert set(body) == PROBLEM_FIELDS
    assert schemas.Problem.model_validate(body).status == status
    assert body["code"] == code
    assert body["correlation_id"] == response.headers["x-correlation-id"]
    return body


@pytest.fixture
def tokens(keycloak: LocalKeycloak) -> Tokens:
    """Tokens of a new realm user, from the web client's PKCE sign-in."""
    return keycloak.pkce_sign_in(keycloak.create_user())


@pytest.fixture
def signed_in(tokens: Tokens) -> dict[str, str]:
    """Authorization headers carrying ``tokens``' access token."""
    return {"Authorization": f"Bearer {tokens.access_token}"}


def tenant_of(engine: Engine, tokens: Tokens) -> TenantScope:
    """The tenant provisioned for the subject of ``tokens``."""
    with engine.connect() as connection:
        user_id, tenant_id = connection.execute(
            select(User.id, Tenant.id)
            .join(Tenant, Tenant.owner_user_id == User.id)
            .where(User.issuer == ISSUER, User.subject == tokens.access_claims["sub"])
        ).one()
    return TenantScope(tenant_id=tenant_id, user_id=user_id)


def test_request_without_a_token_is_a_401_problem(api: TestClient) -> None:
    response = api.get(f"/v1/workflow-runs/{uuid.uuid4()}")

    body = assert_problem(response, 401, "unauthorized")
    assert body["failure_class"] is None
    assert body["retryable"] is False
    assert body["type"] == "urn:property-focus:problem:unauthorized"
    assert body["title"] == "Unauthorized"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("authorization", ["Bearer not-a-token", "Basic dXNlcjpwYXNz"])
def test_request_with_an_invalid_credential_is_a_401_problem(
    api: TestClient, authorization: str
) -> None:
    response = api.get(
        f"/v1/workflow-runs/{uuid.uuid4()}", headers={"Authorization": authorization}
    )

    assert_problem(response, 401, "unauthorized")


def test_keycloak_access_token_is_accepted_by_the_workflow_run_route(
    api: TestClient, runtime_engine: Engine, tokens: Tokens, signed_in: dict[str, str]
) -> None:
    missing = api.get(f"/v1/workflow-runs/{uuid.uuid4()}", headers=signed_in)
    tenant = tenant_of(runtime_engine, tokens)
    with UnitOfWork(runtime_engine, tenant) as uow:
        run = uow.repository(WorkflowRun).add(
            WorkflowRun(kind="test", input_digest="d", deadline_at=utc_now())
        )
        uow.session.flush()
        uow.repository(WorkflowStep).add(WorkflowStep(workflow_run_id=run.id, name="load"))

    response = api.get(f"/v1/workflow-runs/{run.id}", headers=signed_in)

    assert_problem(missing, 404, "not_found")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    body = schemas.WorkflowRun.model_validate(response.json())
    assert (body.id, body.kind, body.state) == (run.id, "test", "QUEUED")
    assert [(step.name, step.state, step.attempt_count) for step in body.steps] == [
        ("load", "QUEUED", 0)
    ]
    assert "tenant_id" not in response.json()


def test_malformed_id_is_a_400_problem(api: TestClient, signed_in: dict[str, str]) -> None:
    response = api.get("/v1/workflow-runs/not-a-uuid", headers=signed_in)

    body = assert_problem(response, 400, "invalid_input")
    assert body["failure_class"] == "INVALID_INPUT"


def test_unknown_path_is_a_404_problem(api: TestClient) -> None:
    assert_problem(api.get("/v1/nothing-here"), 404, "not_found")


def test_unexpected_error_is_a_redacted_500_problem(
    settings: Settings, runtime_engine: Engine, verifier: OidcVerifier
) -> None:
    app: FastAPI = create_app(settings, engine=runtime_engine, verifier=verifier)

    @app.get("/explode")
    def explode() -> None:
        raise RuntimeError("secret-internal-detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/explode", headers={"X-Correlation-ID": "trace-500"})

    body = assert_problem(response, 500, "internal_defect")
    assert body["failure_class"] == "BUG_UNKNOWN"
    assert body["correlation_id"] == "trace-500"
    assert "secret-internal-detail" not in response.text


def test_safe_correlation_id_is_echoed_and_an_unsafe_one_replaced(api: TestClient) -> None:
    echoed = api.get("/health/live", headers={"X-Correlation-ID": "req-123.abc"})
    replaced = api.get("/v1/workflow-runs/x", headers={"X-Correlation-ID": "bad id\twith spaces"})

    assert echoed.headers["x-correlation-id"] == "req-123.abc"
    assert replaced.headers["x-correlation-id"] != "bad id\twith spaces"
    assert replaced.json()["correlation_id"] == replaced.headers["x-correlation-id"]


def test_health_probes(api: TestClient) -> None:
    live, ready = api.get("/health/live"), api.get("/health/ready")

    assert (live.status_code, live.json()) == (200, {"status": "ok"})
    assert (ready.status_code, ready.json()) == (200, {"status": "ok"})


def test_openapi_documents_the_bearer_scheme_and_problem_responses(api: TestClient) -> None:
    document = api.get("/openapi.json").json()
    operation = document["paths"]["/v1/workflow-runs/{run_id}"]["get"]

    assert operation["security"] == [{"HTTPBearer": []}]
    assert document["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert "422" not in operation["responses"]
    for status in ("400", "401", "404"):
        assert operation["responses"][status]["content"] == {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
        }


def test_app_without_an_injected_engine_connects_with_its_settings(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")

    assert (response.status_code, response.json()) == (200, {"status": "ok"})


def test_idempotency_key_reused_with_another_input_is_a_409_problem(
    settings: Settings,
    runtime_engine: Engine,
    verifier: OidcVerifier,
    new_tenant: Callable[[], TenantScope],
) -> None:
    app: FastAPI = create_app(settings, engine=runtime_engine, verifier=verifier)
    tenant = new_tenant()

    @app.post("/admissions/{subject_id}")
    def admit(subject_id: uuid.UUID, request: Request) -> dict[str, object]:
        with UnitOfWork(runtime_engine, tenant) as uow:
            admission = admit_run(
                uow,
                operation="record-subject",
                idempotency_key=request.headers["Idempotency-Key"],
                kind="record-subject",
                subject_type="property",
                subject_id=subject_id,
                now=utc_now(),
            )
        return {"id": str(admission.workflow_run_id), "replayed": admission.replayed}

    subject, key = uuid.uuid4(), {"Idempotency-Key": f"key-{uuid.uuid4()}"}
    with TestClient(app, raise_server_exceptions=False) as client:
        first = client.post(f"/admissions/{subject}", headers=key)
        repeat = client.post(f"/admissions/{subject}", headers=key)
        conflict = client.post(f"/admissions/{uuid.uuid4()}", headers=key)

    assert first.json()["replayed"] is False
    assert repeat.json() == {"id": first.json()["id"], "replayed": True}
    body = assert_problem(conflict, 409, "idempotency_conflict")
    assert body["retryable"] is False
