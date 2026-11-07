"""The scheduler daemon: starts the commands of a crontab at their times and does nothing else.

Dogfood and ``make dev`` run it over ``infrastructure/dogfood/scheduler.crontab``, whose
entries each start one short-lived ``run-task <kind>`` process (``runtime/scheduled.py``).
The daemon does no business work itself (D26): it only starts processes and reaps them.

Crontab format: one entry per line, five schedule fields (minute, hour, day of month, month,
day of week) then the command. Blank lines and lines starting with ``#`` are ignored. A field
is ``*`` or a comma-separated list of ``N``, ``A-B``, ``*/S`` or ``A-B/S``; day of week runs
0-7 with both 0 and 7 meaning Sunday. When both day fields are restricted, a day matches
either, as in cron. Times are UTC. The command is split like a shell would split it, but no
shell runs it, so it cannot pipe, redirect or expand variables.

An entry whose previous process is still running is skipped for that minute rather than
started twice.
"""

import logging
import shlex
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

logger = logging.getLogger(__name__)

# (name, lowest value, highest value) of each schedule field, in crontab order.
_FIELDS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of month", 1, 31),
    ("month", 1, 12),
    ("day of week", 0, 7),
)


class Process(Protocol):
    """The part of ``subprocess.Popen`` the scheduler uses."""

    def poll(self) -> int | None: ...

    def wait(self) -> int: ...


type StartProcess = Callable[[Sequence[str]], Process]


class CrontabError(ValueError):
    """A crontab line is not a valid entry; the message names the line."""


@dataclass(frozen=True)
class CronEntry:
    """One crontab entry: when it runs and the command it starts.

    The five sets hold the values each schedule field matches; ``days_of_week`` uses 0 for
    Sunday (a 7 in the crontab is stored as 0). ``line`` is the 1-based crontab line.
    """

    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    # True when that day field was ``*``, which changes how the two day fields combine.
    any_day_of_month: bool
    any_day_of_week: bool
    command: tuple[str, ...]
    line: int

    def is_due(self, minute: datetime) -> bool:
        """Whether the entry runs in the UTC minute that contains ``minute``."""
        minute = minute.astimezone(UTC)
        day_of_week = minute.isoweekday() % 7
        if self.any_day_of_month or self.any_day_of_week:
            day_matches = minute.day in self.days_of_month and day_of_week in self.days_of_week
        else:
            day_matches = minute.day in self.days_of_month or day_of_week in self.days_of_week
        return (
            day_matches
            and minute.minute in self.minutes
            and minute.hour in self.hours
            and minute.month in self.months
        )


def parse_crontab(text: str) -> list[CronEntry]:
    """Parse crontab ``text`` into its entries, in file order.

    Raises ``CrontabError`` naming the first invalid line.
    """
    entries = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 5)
        if len(parts) < 6:
            raise CrontabError(f"line {number}: expected five schedule fields and a command")
        try:
            values = [
                _field_values(field, *limits)
                for field, limits in zip(parts[:5], _FIELDS, strict=True)
            ]
            command = tuple(shlex.split(parts[5]))
        except ValueError as error:
            raise CrontabError(f"line {number}: {error}") from None
        entries.append(
            CronEntry(
                minutes=values[0],
                hours=values[1],
                days_of_month=values[2],
                months=values[3],
                days_of_week=frozenset(day % 7 for day in values[4]),
                any_day_of_month=parts[2] == "*",
                any_day_of_week=parts[4] == "*",
                command=command,
                line=number,
            )
        )
    return entries


def _field_values(field: str, name: str, low: int, high: int) -> frozenset[int]:
    """The values the schedule field ``name`` matches; raises ``ValueError`` when malformed."""
    values: set[int] = set()
    for item in field.split(","):
        span, _, step_text = item.partition("/")
        step = int(step_text) if step_text else 1
        if span == "*":
            start, end = low, high
        elif "-" in span:
            start_text, _, end_text = span.partition("-")
            start, end = int(start_text), int(end_text)
        else:
            start = end = int(span)
            if step_text:
                end = high
        if step < 1 or not low <= start <= end <= high:
            raise ValueError(f"{name} field {field!r} is outside {low}-{high}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


def _start_process(command: Sequence[str]) -> Process:
    """Start ``command`` with this process's environment and output streams."""
    return subprocess.Popen(command, stdin=subprocess.DEVNULL)


class Scheduler:
    """Starts due crontab entries once per minute until stopped.

    ``start_process`` starts one command and returns its process; the default inherits this
    process's environment, standard output and standard error.
    """

    def __init__(
        self, entries: Sequence[CronEntry], *, start_process: StartProcess = _start_process
    ) -> None:
        self._entries = tuple(entries)
        self._start_process = start_process
        self._running: dict[CronEntry, Process] = {}

    def tick(self, minute: datetime) -> list[CronEntry]:
        """Reap finished processes, then start every entry due in ``minute`` whose previous
        process has exited; return the entries started."""
        self.reap()
        started = []
        for entry in self._entries:
            if not entry.is_due(minute):
                continue
            if entry in self._running:
                logger.warning(
                    "scheduler: skipping %s; its previous run is still going",
                    shlex.join(entry.command),
                )
                continue
            self._running[entry] = self._start_process(entry.command)
            started.append(entry)
        return started

    def reap(self) -> None:
        """Forget processes that have exited, logging each exit status."""
        for entry, process in list(self._running.items()):
            status = process.poll()
            if status is None:
                continue
            del self._running[entry]
            log = logger.info if status == 0 else logger.error
            log("scheduler: %s exited with status %d", shlex.join(entry.command), status)

    def run(self, stop: threading.Event, *, clock: Callable[[], datetime] | None = None) -> None:
        """Tick at the start of every minute until ``stop`` is set, then wait for the
        processes still running to exit. ``clock`` returns the current time (default: UTC
        now)."""
        now = clock or (lambda: datetime.now(UTC))
        logger.info("scheduler: started with %d entries", len(self._entries))
        next_minute = now().replace(second=0, microsecond=0) + timedelta(minutes=1)
        while not stop.wait(max(0.0, (next_minute - now()).total_seconds())):
            self.tick(next_minute)
            next_minute += timedelta(minutes=1)
            # After a long pause (a suspended laptop), skip the missed minutes.
            next_minute = max(next_minute, now().replace(second=0, microsecond=0))
        for process in self._running.values():
            process.wait()
        self.reap()
        logger.info("scheduler: stopped")
