"""Wire schemas of the REST API. They appear in the OpenAPI document the web client is
generated from, so a change here is an API change."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict

from property_focus.adapters.db import models
from property_focus.adapters.db.models import AnalysisTier, BudgetTier, WorkflowState
from property_focus.domain.errors import FailureClass


class Problem(BaseModel):
    """An error response body (``application/problem+json``, RFC 9457).

    ``code`` is a stable error code and ``failure_class`` its class, or null for request
    errors and other non-failures. ``detail`` is redacted and safe to show to the user.
    ``retry_after_seconds`` is present only when the caller should wait before retrying.
    """

    model_config = ConfigDict(frozen=True)

    type: str
    title: str
    status: int
    code: str
    failure_class: FailureClass | None
    detail: str
    correlation_id: str
    retryable: bool
    retry_after_seconds: int | None = None


class Health(BaseModel):
    """A health probe result."""

    status: Literal["ok"]


class WorkflowStep(BaseModel):
    """One component of a workflow run and its latest failure, if any."""

    name: str
    state: WorkflowState
    attempt_count: int
    failure_class: FailureClass | None
    failure_code: str | None
    retryable: bool | None
    # Safe for users to read; never contains secrets or private content.
    safe_explanation: str | None
    next_attempt_at: datetime | None
    # IDs of what the step produced.
    result_refs: list[str]


class WorkflowRun(BaseModel):
    """A unit of background work: its state, its steps and what it produced."""

    id: uuid.UUID
    kind: str
    state: WorkflowState
    analysis_tier: AnalysisTier | None
    budget_tier: BudgetTier | None
    failure_class: FailureClass | None
    failure_code: str | None
    correlation_id: str | None
    # IDs of what the run produced.
    result_refs: list[str]
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    steps: list[WorkflowStep]

    @classmethod
    def from_rows(cls, run: models.WorkflowRun, steps: Sequence[models.WorkflowStep]) -> Self:
        """The wire form of ``run`` and its ``steps``, in the order given."""
        return cls.model_validate(
            {**_columns(run), "steps": [WorkflowStep.model_validate(_columns(s)) for s in steps]}
        )


def _columns(row: Any) -> dict[str, Any]:
    """A mapped row's column values by attribute name."""
    return {column.key: getattr(row, column.key) for column in row.__mapper__.column_attrs}
