"""The crontab-driven scheduler daemon that dogfood and ``make dev`` run."""

import sys
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from property_focus.runtime.scheduler import CrontabError, Scheduler, parse_crontab


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_parses_entries_and_skips_comments_and_blank_lines() -> None:
    entries = parse_crontab(
        "# header\n\n"
        "* * * * * python -m property_focus.cli run-task outbox-sweep\n"
        "*/5 * * * * python -m property_focus.cli run-task 'repair'\n"
    )

    assert [entry.command[-1] for entry in entries] == ["outbox-sweep", "repair"]
    assert [entry.line for entry in entries] == [3, 4]
    assert entries[1].minutes == frozenset(range(0, 60, 5))


@pytest.mark.parametrize(
    ("schedule", "due", "not_due"),
    [
        ("* * * * *", "2026-11-02T10:17", None),
        ("*/5 * * * *", "2026-11-02T10:15", "2026-11-02T10:17"),
        ("17 3 * * *", "2026-11-02T03:17", "2026-11-02T04:17"),
        ("0 9-17/4 * * *", "2026-11-02T13:00", "2026-11-02T11:00"),
        ("0 0 1,15 * *", "2026-11-15T00:00", "2026-11-14T00:00"),
        ("0 0 * * 7", "2026-11-01T00:00", "2026-11-02T00:00"),  # 2026-11-01 is a Sunday
        ("30 2 * 12 *", "2026-12-05T02:30", "2026-11-05T02:30"),
        ("10/20 * * * *", "2026-11-02T10:50", "2026-11-02T10:20"),
    ],
)
def test_entry_is_due_only_in_matching_minutes(
    schedule: str, due: str, not_due: str | None
) -> None:
    (entry,) = parse_crontab(f"{schedule} true\n")

    assert entry.is_due(at(due))
    if not_due is not None:
        assert not entry.is_due(at(not_due))


def test_restricted_day_of_month_and_day_of_week_match_either_day() -> None:
    (entry,) = parse_crontab("0 0 13 * 5 true\n")  # the 13th, or any Friday

    assert entry.is_due(at("2026-11-13T00:00"))  # a Friday the 13th
    assert entry.is_due(at("2026-11-06T00:00"))  # a Friday
    assert entry.is_due(at("2026-12-13T00:00"))  # a Sunday the 13th
    assert not entry.is_due(at("2026-11-07T00:00"))


@pytest.mark.parametrize(
    "line",
    [
        "* * * * run-task-without-five-fields",
        "60 * * * * true",
        "* 24 * * * true",
        "* * 0 * * true",
        "*/0 * * * * true",
        "5-1 * * * * true",
        "x * * * * true",
        "* * * * * python 'unterminated",
    ],
)
def test_rejects_malformed_entries_naming_the_line(line: str) -> None:
    with pytest.raises(CrontabError, match="line 2"):
        parse_crontab(f"# comment\n{line}\n")


class FakeProcess:
    """A started process; it runs until the test sets its exit ``status``."""

    def __init__(self, status: int | None = None) -> None:
        self.status = status

    def poll(self) -> int | None:
        return self.status

    def wait(self) -> int:
        self.status = 0 if self.status is None else self.status
        return self.status


class Recorder:
    """A process starter that records each command and hands out ``FakeProcess``es."""

    def __init__(self, *, exit_at_once: bool = False) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.processes: list[FakeProcess] = []
        self._exit_at_once = exit_at_once

    def start(self, command: Sequence[str]) -> FakeProcess:
        self.commands.append(tuple(command))
        self.processes.append(FakeProcess(0 if self._exit_at_once else None))
        return self.processes[-1]


def test_tick_starts_due_entries_and_skips_one_still_running() -> None:
    recorder = Recorder()
    entries = parse_crontab("* * * * * every-minute\n*/5 * * * * every-five\n")
    scheduler = Scheduler(entries, start_process=recorder.start)

    scheduler.tick(at("2026-11-02T10:00"))
    scheduler.tick(at("2026-11-02T10:01"))  # every-minute's first run has not exited
    recorder.processes[0].status = 0
    scheduler.tick(at("2026-11-02T10:02"))

    assert recorder.commands == [("every-minute",), ("every-five",), ("every-minute",)]


class SimulatedTime(threading.Event):
    """A stop event over a simulated clock: ``wait`` advances the clock by its timeout
    instead of sleeping, and the event sets itself once the clock reaches ``stop_at``."""

    def __init__(self, start: datetime, stop_at: datetime) -> None:
        super().__init__()
        self.now = start
        self._stop_at = stop_at

    def wait(self, timeout: float | None = None) -> bool:
        self.now += timedelta(seconds=timeout or 0)
        if self.now >= self._stop_at:
            self.set()
        return self.is_set()


def test_run_ticks_once_a_minute_until_stopped() -> None:
    recorder = Recorder(exit_at_once=True)
    time_ = SimulatedTime(at("2026-11-02T10:00:10"), stop_at=at("2026-11-02T10:03:30"))

    Scheduler(parse_crontab("* * * * * every-minute\n"), start_process=recorder.start).run(
        time_, clock=lambda: time_.now
    )

    # Ticks for 10:01, 10:02 and 10:03; the stop arrives before 10:04.
    assert recorder.commands == [("every-minute",)] * 3


def test_default_starter_runs_the_command_without_a_shell(tmp_path: Path) -> None:
    out = tmp_path / "argv.txt"
    script = "import sys, pathlib; pathlib.Path(sys.argv[1]).write_text(repr(sys.argv[2:]))"
    entries = parse_crontab(f"* * * * * {sys.executable} -c '{script}' {out} ; echo $HOME\n")

    Scheduler(entries).tick(at("2026-11-02T10:00"))

    deadline = time.monotonic() + 10
    while not out.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert out.read_text() == repr([";", "echo", "$HOME"])
