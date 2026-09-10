"""The morning report: the night in the few lines worth reading over breakfast.

Every night produced a few thousand measurements and nobody read any of them. The
event log has everything and is far too long; the device bar has this moment and
has forgotten the last eight hours. So "did that go well?" meant opening the app
and scrolling, which nobody does on the fourth morning.

Two things this has to get right, and they pull against each other. It has to be
short enough to read without deciding to, and it has to be honest enough that a
quiet one means something. A report that says "all fine" on a night with a missed
stage is worse than no report, because it is how you learn to stop reading them.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from hydrosnooze import report
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import PreconditionRow, Sample
from hydrosnooze.events import Event
from hydrosnooze.models import Schedule, SleepStage, Stage
from hydrosnooze.scheduler import REPORT_AFTER, Job
from hydrosnooze.service import Service

WAKE_ON = datetime(2026, 9, 11).date()


@pytest.fixture
def plan():
    schedule = Schedule(
        wake_time=time(7, 30),
        days_of_week=list(range(7)),
        stages=[
            SleepStage(Stage.DEEP, 240, 26),
            SleepStage(Stage.REM, 210, 27),
            SleepStage(Stage.WAKE, 30, 28),
        ],
    )
    return schedule.plan_for(WAKE_ON)


def all_stages(plan) -> set[str]:
    return {f"stage:{s.stage.value}" for s in plan.steps} | {"precool", "power_off"}


def night(plan, *, offset: float = 0.2, watts: float = 168.0) -> list[Sample]:
    """A night of samples where the bed tracks whatever stage is running."""
    out, at = [], plan.bedtime_at
    while at < plan.wake_at:
        step = next((s for s in plan.steps if s.starts_at <= at < s.ends_at), None)
        target = step.temp_c if step else plan.steps[0].temp_c
        out.append(
            Sample(at, watts, flow_c=target - 0.6, return_c=target + offset, room_c=19.6)
        )
        at += timedelta(seconds=30)
    return out


def ready(plan) -> PreconditionRow:
    return PreconditionRow(
        plan.precool_at, "warming", 26, 1320, True, 19.2, 26.1, 19.6, "probes"
    )


# --- A night that worked --------------------------------------------------------


def test_a_good_night_is_short_and_says_nothing_alarming(plan):
    made = report.build(plan, night(plan), [], all_stages(plan), ready(plan))

    assert made.level == "info"
    assert "with notes" not in made.title
    assert "All 3 stages landed." in made.body
    assert len(made.body.splitlines()) <= 6, "nobody reads more than this"


def test_it_says_how_long_getting_ready_took_and_which_sensor_said_so(plan):
    made = report.build(plan, night(plan), [], all_stages(plan), ready(plan))
    assert "Ready in 22m" in made.body
    assert "19.2 to 26.1C" in made.body
    assert "on the hoses" in made.body


def test_the_bed_is_judged_against_the_stage_it_was_in(plan):
    """Not against one number. A night that steps 24C to 28C has no single
    setpoint, and reporting the spread alone makes a working night look like a
    wandering one."""
    made = report.build(plan, night(plan, offset=0.2), [], all_stages(plan))
    assert "never more than 0.2C off the stage it was in" in made.body


def test_a_bed_that_never_got_near_its_stages_says_so_plainly(plan):
    cold = [Sample(s.at, s.watts, s.flow_c, 21.0, s.room_c) for s in night(plan)]
    made = report.build(plan, cold, [], all_stages(plan))
    assert "% of the night more than" in made.body
    assert "off on average" in made.body


def test_mode_swaps_are_counted_the_way_a_person_would(plan):
    swaps = [
        Event(i, plan.bedtime_at, "info", "mode", "switched") for i in range(1, 3)
    ]
    made = report.build(plan, night(plan), swaps, all_stages(plan))
    assert "Swapped mode twice to keep it quiet." in made.body

    one = report.build(plan, night(plan), swaps[:1], all_stages(plan))
    assert "Swapped mode once" in one.body


# --- A night that did not -------------------------------------------------------


def test_a_missed_stage_is_the_first_line_and_changes_the_title(plan):
    fired = all_stages(plan) - {"stage:rem"}
    made = report.build(plan, night(plan), [], fired, ready(plan))

    assert made.level == "warning"
    assert "with notes" in made.title
    assert made.body.splitlines()[0] == "2 of 3 stages landed. Missed: REM."


def test_warnings_are_counted_and_the_first_one_is_quoted(plan):
    events = [
        Event(1, plan.bedtime_at, "warning", "plug", "The plug is not answering."),
        Event(2, plan.bedtime_at, "error", "stage", "Something else went wrong."),
        Event(3, plan.bedtime_at, "info", "stage", "Deep: 24C in quiet"),
    ]
    made = report.build(plan, night(plan), events, all_stages(plan))
    assert "2 things worth a look. First: The plug is not answering." in made.body
    assert made.level == "warning"


def test_one_warning_reads_as_one_thing(plan):
    events = [Event(1, plan.bedtime_at, "warning", "plug", "Not answering.")]
    made = report.build(plan, night(plan), events, all_stages(plan))
    assert made.body.endswith("One thing worth a look. First: Not answering.")


def test_pre_conditioning_that_never_settled_says_that_rather_than_a_time(plan):
    never = PreconditionRow(plan.precool_at, "warming", 26, 10800, False)
    made = report.build(plan, night(plan), [], all_stages(plan), never)
    assert "without settling at 26C" in made.body


# --- Never inventing anything ---------------------------------------------------


def test_a_night_with_no_probes_reports_the_machine_and_stops_there(plan):
    blind = [Sample(s.at, s.watts) for s in night(plan)]
    made = report.build(plan, blind, [], all_stages(plan))
    assert "Bed ran" not in made.body
    assert "kWh" in made.body


def test_energy_is_what_was_measured_rather_than_watts_times_a_constant():
    """One hour at 100 W is 0.1 kWh, and a three hour hole where the plug was
    unreachable is not 0.9 kWh of invented electricity."""
    start = datetime(2026, 9, 11, 0, 0)
    steady = [Sample(start + timedelta(seconds=30 * i), 100.0) for i in range(121)]
    assert report._kwh(steady) == 0.1

    gap = [
        Sample(start, 300.0),
        Sample(start + timedelta(hours=3), 300.0),
        Sample(start + timedelta(hours=3, seconds=30), 300.0),
    ]
    assert report._kwh(gap) < 0.05


def test_the_window_starts_before_the_bed_did(plan):
    start, end = report.window(plan)
    assert start < plan.precool_at
    assert end > plan.wake_at


# --- Sent once, by the same machinery as everything else ------------------------


@pytest.fixture
def service(tmp_path, plan):
    clock = VirtualClock(plan.wake_at + REPORT_AFTER)
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), clock=clock, echo=False)
    svc.schedule = Schedule(
        wake_time=time(7, 30),
        days_of_week=list(range(7)),
        stages=[
            SleepStage(Stage.DEEP, 240, 26),
            SleepStage(Stage.REM, 210, 27),
            SleepStage(Stage.WAKE, 30, 28),
        ],
    )
    yield svc
    svc.db.close()


def test_it_becomes_due_after_the_wake_time_not_at_it(service, plan):
    """After the power off has had its own window, so the report describes a night
    that is completely over, including whether switching off worked."""
    service.clock.jump_to(plan.wake_at + timedelta(minutes=1))
    for job in iter(lambda: service.scheduler.due(service.schedule, service.clock.now()), None):
        if job.kind == "report":
            pytest.fail("too early, the power off has not had its window")
        service.scheduler.fired.mark(job)

    service.clock.jump_to(plan.wake_at + REPORT_AFTER + timedelta(minutes=1))
    job = service.scheduler.due(service.schedule, service.clock.now())
    assert job is not None and job.kind == "report"


def test_it_is_sent_once_and_a_restart_does_not_send_it_again(service, plan):
    job = Job("report", plan)
    assert service._send_report(plan) is True
    service.scheduler.fired.mark(job)

    said = [e for e in service.events.recent(20) if e.kind == "report"]
    assert len(said) == 1
    assert service.scheduler.fired.has_fired(job)


def test_a_report_that_cannot_be_built_does_not_keep_retrying(service, plan):
    """The night is already over. There is nothing to retry, and leaving it
    unfired would try again every minute until the grace window closed."""
    service.db.close()  # anything that reads the night will now throw
    assert service._send_report(plan) is True
