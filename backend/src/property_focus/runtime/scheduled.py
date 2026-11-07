"""Scheduled kinds: short-lived tasks a scheduler starts, each run as ``cli run-task <kind>``.

The dogfood ``scheduler.crontab`` and the AWS EventBridge schedules may start only the
kinds registered in ``SCHEDULED_KINDS``:

- ``outbox-sweep`` (each minute): launch outbox events pending for more than 30 seconds,
  which a crash between commit and launch leaves behind.
- ``repair`` (every 5 minutes): relaunch runs whose lease expired (a crashed task, or a
  retry that is due) and queued runs whose launch was lost, including those of stranded
  outbox events; then delete up to 1000 expired idempotency records.

Each invocation does one bounded pass and exits; none loops waiting for work.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping

from property_focus.application.workflows import prune_idempotency_records, relaunch_stalled_runs
from property_focus.runtime.run_task import TaskRuntime

type ScheduledTask = Callable[[TaskRuntime], Awaitable[int]]


async def outbox_sweep(runtime: TaskRuntime) -> int:
    """Launch stale pending outbox events; return how many were launched."""
    return await runtime.publisher.sweep()


async def repair(runtime: TaskRuntime) -> int:
    """Relaunch stalled workflow runs and prune expired idempotency records; return how many
    runs were launched plus how many records were deleted."""
    now = runtime.clock()
    launched = await relaunch_stalled_runs(runtime.engine, runtime.launcher, now=now)
    pruned = await asyncio.to_thread(prune_idempotency_records, runtime.engine, now=now)
    return launched + pruned


SCHEDULED_KINDS: Mapping[str, ScheduledTask] = {
    "outbox-sweep": outbox_sweep,
    "repair": repair,
}


async def run_scheduled(kind: str, runtime: TaskRuntime) -> int:
    """Run the scheduled ``kind`` once; return how many items it handled.

    Raises ``KeyError`` for a kind that is not registered.
    """
    return await SCHEDULED_KINDS[kind](runtime)
