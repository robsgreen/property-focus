"""Scheduled kinds: short-lived tasks a scheduler starts, each run as ``cli run-task <kind>``.

The dogfood ``scheduler.crontab`` and the AWS EventBridge schedules may start only the
kinds registered in ``SCHEDULED_KINDS``:

- ``outbox-sweep`` (each minute): launch outbox events pending for more than 30 seconds,
  which a crash between commit and launch leaves behind.
- ``repair`` (every 5 minutes): relaunch runs whose lease expired (a crashed task, or a
  retry that is due) and queued runs whose launch was lost, including those of stranded
  outbox events.

Each invocation does one bounded pass and exits; none loops waiting for work.
"""

from collections.abc import Awaitable, Callable, Mapping

from property_focus.application.workflows import relaunch_stalled_runs
from property_focus.runtime.run_task import TaskRuntime

type ScheduledTask = Callable[[TaskRuntime], Awaitable[int]]


async def outbox_sweep(runtime: TaskRuntime) -> int:
    """Launch stale pending outbox events; return how many were launched."""
    return await runtime.publisher.sweep()


async def repair(runtime: TaskRuntime) -> int:
    """Relaunch stalled workflow runs; return how many were launched."""
    return await relaunch_stalled_runs(runtime.engine, runtime.launcher, now=runtime.clock())


SCHEDULED_KINDS: Mapping[str, ScheduledTask] = {
    "outbox-sweep": outbox_sweep,
    "repair": repair,
}


async def run_scheduled(kind: str, runtime: TaskRuntime) -> int:
    """Run the scheduled ``kind`` once; return how many items it handled.

    Raises ``KeyError`` for a kind that is not registered.
    """
    return await SCHEDULED_KINDS[kind](runtime)
