"""AWS Lambda entry point for background tasks; it delegates to ``run_task``.

An event is either ``{"task_input": <TaskInput>}`` from ``LambdaTaskLauncher`` or Step
Functions, or ``{"scheduled_kind": "<kind>"}`` from an EventBridge schedule. The runtime
(database pool, secrets, launcher) is built from the ``PF_*`` environment once per Lambda
execution environment and reused by later invocations.
"""

import asyncio
from collections.abc import Mapping
from functools import cache
from typing import Any

from property_focus.application.ports import TaskInput
from property_focus.config import Settings
from property_focus.runtime.run_task import TaskRuntime, run_task, runtime_from_settings
from property_focus.runtime.scheduled import run_scheduled


def handler(event: Mapping[str, Any], context: object) -> dict[str, Any]:
    """The Lambda handler: run ``event`` with the environment's runtime."""
    return handle(event, _runtime())


def handle(event: Mapping[str, Any], runtime: TaskRuntime) -> dict[str, Any]:
    """Run one task or scheduled kind described by ``event`` and summarize the outcome.

    Raises ``pydantic.ValidationError`` for a malformed ``task_input``, ``KeyError`` for an
    unregistered scheduled kind and ``ValueError`` for an event of neither shape.
    """
    if "task_input" in event:
        task = TaskInput.model_validate(event["task_input"])
        outcome = asyncio.run(run_task(task, runtime))
        return {"workflow_run_id": str(task.workflow_run_id), "outcome": outcome}
    if "scheduled_kind" in event:
        kind = str(event["scheduled_kind"])
        return {"scheduled_kind": kind, "handled": asyncio.run(run_scheduled(kind, runtime))}
    raise ValueError("event names neither task_input nor scheduled_kind")


@cache
def _runtime() -> TaskRuntime:
    return asyncio.run(runtime_from_settings(Settings.from_environment()))
