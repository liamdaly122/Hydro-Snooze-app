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
from hydrosnooze.models import QUIET_KIND, Schedule, SleepStage, Stage
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
    assert "All 4 stages landed." in made.body
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
        Event(i, plan.bedtime_at, "info", QUIET_KIND, "switched") for i in range(1, 3)
    ]
    made = report.build(plan, night(plan), swaps, all_stages(plan))
    assert "Swapped mode twice to keep it quiet." in made.body

    one = report.build(plan, night(plan), swaps[:1], all_stages(plan))
    assert "Swapped mode once" in one.body


def test_an_ordinary_mode_change_is_not_a_swap_to_keep_it_quiet(plan):
    """The bug this replaces. sequences.py has always logged every set_mode under
    "mode": every stage boundary, everything pressed by hand, every step of
    getting the bed ready. Counting those as well had the report claiming a
    night swapped modes to stay quiet several times more often than it had."""
    ordinary = [
        Event(1, plan.bedtime_at, "info", "mode", "Set mode to warming via warm then cool"),
        Event(2, plan.bedtime_at, "info", "mode", "Set mode to quiet via warm then cool"),
    ]
    made = report.build(plan, night(plan), ordinary, all_stages(plan))
    assert "Swapped mode" not in made.body


# --- A night that did not -------------------------------------------------------


def test_a_missed_stage_is_the_first_line_and_changes_the_title(plan):
    fired = all_stages(plan) - {"stage:rem"}
    made = report.build(plan, night(plan), [], fired, ready(plan))

    assert made.level == "warning"
    assert "with notes" in made.title
    assert made.body.splitlines()[0] == "3 of 4 stages landed. Missed: REM."


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
    assert report.kwh(steady) == 0.1

    gap = [
        Sample(start, 300.0),
        Sample(start + timedelta(hours=3), 300.0),
        Sample(start + timedelta(hours=3, seconds=30), 300.0),
    ]
    assert report.kwh(gap) < 0.05


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


# --- Last night means last night -------------------------------------------------
#
# Found in review on 22 September. The report and Autopilot asked the database
# for everything since the night started and never said when it ended, so
# opening Autopilot in the evening described last night using the whole day
# and tonight's pre-heat as well: the energy, the bed's range and the score all
# took in hours that were not part of it, and "ready" was tonight's row.


def test_the_night_report_stops_at_the_end_of_the_night(service, plan):
    start, end = report.window(plan)
    inside = plan.steps[0].starts_at + timedelta(minutes=30)
    tomorrow_evening = plan.wake_at + timedelta(hours=15)

    service.db.add_power_sample(inside, 150.0, return_c=19.0, target_c=19)
    service.db.add_power_sample(tomorrow_evening, 900.0, return_c=35.0, target_c=27)
    service.db.record_precondition(plan.precool_at, "turbo", 19, 1200, True)
    service.db.record_precondition(tomorrow_evening, "warming", 27, 600, True)

    service.clock.jump_to(tomorrow_evening + timedelta(minutes=10))
    night = service.night_report(plan)

    assert night.high_c == 19.0, "tomorrow evening's 35C leaked into last night"
    assert night.ready is not None and night.ready.mode == "turbo"


# --- One push for one report ----------------------------------------------------


def test_a_report_quoting_a_loud_warning_is_pushed_once(service, plan):
    """Also from review. The report is logged as an event and then pushed on
    its own. A night with a blaster blip quotes 'is not answering' in the body,
    which is on the notifier's loud list, so the event pushed it a second time
    as a 'HydroSnooze warning' on top of the report itself."""
    sent: list[tuple[str, str]] = []
    service.notifier.topic = "test-topic"
    service.notifier.push = lambda title, message, **_: sent.append((title, message))
    # What start() does, so events reach the database and the notifier.
    service.events.subscribe(service._on_event)

    service.clock.jump_to(plan.steps[1].starts_at)
    service.events.warning(
        "blaster", "The blaster at hydrosnooze-ir.local is not answering. Presses will not reach"
    )
    service.clock.jump_to(plan.wake_at + REPORT_AFTER)
    sent.clear()

    service._send_report(plan)
    assert len(sent) == 1, [title for title, _ in sent]
    assert sent[0][0].startswith("Autopilot")
    assert "not answering" in sent[0][1], "the case this is about"


def test_the_report_scores_against_what_was_asked_for_at_the_time(plan):
    """Also from review. The report rebuilt each target from the plan, while
    Autopilot uses the target written down with each sample. A stage changed at
    the bedside to 27C is in the sample and not in the plan, so the morning
    message called a bed that did exactly as asked three degrees off, and the
    screen a tap away said it was on target."""
    deep = plan.steps[1]
    samples = [
        Sample(deep.starts_at + timedelta(minutes=m), 150.0, None, float(deep.temp_c + 3), None,
               deep.temp_c + 3)
        for m in range(0, 120, 5)
    ]
    said = report._how_the_bed_did(plan, samples)
    assert said is not None and "never more than 0.0C off" in said, said
