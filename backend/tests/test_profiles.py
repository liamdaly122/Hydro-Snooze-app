"""Saved nights, by name. "Summer", "Winter", "Guest room".

A profile is the shape of a night and nothing else: the stage temperatures, how
long each lasts, and the cooling speed. Not the wake time and not the days of the
week, because those belong to the week you are having rather than to the weather,
and loading "Summer" should never move an alarm.

The one design decision worth defending is that nothing stores which profile is
active. It is worked out by comparing each saved night against the schedule. A
stored flag plus a schedule anyone can edit afterwards drift apart on the very
first edit, and then the app is confidently naming a night that is not running.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time

import pytest

from hydrosnooze.config import Settings
from hydrosnooze.db import Database
from hydrosnooze.models import Mode, Schedule, SleepStage, Stage
from hydrosnooze.service import Service

NOW = datetime(2026, 9, 11, 9, 0)


@pytest.fixture
def db():
    # Closed, like the service fixture below. Database.close() flushes the
    # pending power samples first, so skipping it also skips the clean shutdown
    # path the rest of the suite exercises.
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def schedule():
    return Schedule(
        wake_time=time(7, 30),
        stages=[
            SleepStage(Stage.DEEP, 240, 17),
            SleepStage(Stage.REM, 210, 20),
            SleepStage(Stage.WAKE, 30, 26),
        ],
    )


def night(wake, *temps, durations=(240, 210, 30)):
    return Schedule(
        wake_time=wake,
        stages=[
            SleepStage(stage, minutes, temp)
            for stage, minutes, temp in zip(
                (Stage.DEEP, Stage.REM, Stage.WAKE), durations, temps, strict=True
            )
        ],
    )


def test_a_saved_night_comes_back_as_it_went_in(db, schedule):
    saved = db.save_profile("Summer", schedule, NOW)
    assert saved.name == "Summer"
    assert [s.temp_c for s in saved.stages] == [17, 20, 26]
    # Against the schedule rather than against what was typed above: a Schedule
    # rescales its stages to fill the night exactly, so the schedule's durations
    # are the real ones and those are what a snapshot has to preserve.
    assert [s.duration_minutes for s in saved.stages] == [
        s.duration_minutes for s in schedule.stages
    ]
    assert saved.cooling_speed is schedule.cooling_speed


def test_the_running_one_is_worked_out_rather_than_remembered(db, schedule):
    """The whole design. A flag plus a schedule anyone can edit drift apart on
    the first edit, and then the app names a night that is not running."""
    saved = db.save_profile("Summer", schedule, NOW)
    assert saved.matches(schedule)
    assert not saved.matches(night(schedule.wake_time, 18, 20, 26)), "a degree is a different night"


def test_the_same_numbers_over_a_different_night_is_a_different_night(db, schedule):
    """Durations count. Four hours of deep at 17C is not two hours of it."""
    saved = db.save_profile("Summer", schedule, NOW)
    assert not saved.matches(
        night(schedule.wake_time, 17, 20, 26, durations=(120, 330, 30))
    )


def test_the_cooling_speed_is_part_of_it(db, schedule):
    saved = db.save_profile("Summer", schedule, NOW)
    assert not saved.matches(replace(schedule, cooling_speed=Mode.TURBO))


def test_saving_over_a_name_replaces_rather_than_duplicates(db, schedule):
    """Two profiles called Summer is never what anyone meant, and choosing
    between them in a list is worse than losing the older one."""
    db.save_profile("Summer", schedule, NOW)
    db.save_profile("Summer", night(schedule.wake_time, 15, 20, 26), NOW)

    assert len(db.profiles()) == 1
    assert db.profiles()[0].stages[0].temp_c == 15


def test_the_name_match_ignores_case_and_keeps_the_spelling_you_typed(db, schedule):
    """Matching is case-insensitive so "summer" overwrites "Summer" rather than
    making a second one. The spelling that survives has to be the one just
    typed, or the list redraws with the old one and reads as a failed rename."""
    db.save_profile("Summer", schedule, NOW)
    db.save_profile("SUMMER", schedule, NOW)

    assert len(db.profiles()) == 1
    assert db.profiles()[0].name == "SUMMER"


def test_they_come_back_in_a_stable_order(db, schedule):
    for name in ("Winter", "summer", "Guest room"):
        db.save_profile(name, schedule, NOW)
    assert [p.name for p in db.profiles()] == ["Guest room", "summer", "Winter"]


def test_deleting_one_leaves_the_others(db, schedule):
    doomed = db.save_profile("Summer", schedule, NOW)
    db.save_profile("Winter", schedule, NOW)

    assert db.delete_profile(doomed.id) is True
    assert [p.name for p in db.profiles()] == ["Winter"]
    assert db.delete_profile(doomed.id) is False, "and says so the second time"


def test_an_unknown_id_is_not_found_rather_than_an_error(db):
    assert db.profile(999) is None


# --- Through the service, which is what the app talks to -------------------------


@pytest.fixture
def service(tmp_path):
    svc = Service(Settings(db_path=str(tmp_path / "s.db")), echo=False)
    yield svc
    svc.db.close()


def test_loading_one_leaves_the_wake_time_and_the_days_alone(service):
    """A profile is about the weather rather than the week, so loading Summer
    must never move an alarm."""
    service.update_schedule({"wake_time": time(5, 45), "days_of_week": [0, 1]})
    saved = service.db.save_profile("Summer", service.schedule, NOW)

    # Drift away from it, then load it back.
    service.update_schedule(
        {"stages": [SleepStage(s.stage, s.duration_minutes, 19) for s in saved.stages]}
    )
    assert not saved.matches(service.schedule)

    service.update_schedule(
        {"stages": list(saved.stages), "cooling_speed": saved.cooling_speed}
    )

    assert service.schedule.wake_time == time(5, 45)
    assert service.schedule.days_of_week == [0, 1]
    assert saved.matches(service.schedule), "and it is running again"


def test_exactly_one_is_running_at_a_time(service):
    """Not enforced anywhere, which is the point: it falls out of comparing
    against a single schedule, so there is no state that can hold two."""
    summer = service.db.save_profile("Summer", service.schedule, NOW)
    service.update_schedule(
        {"stages": [SleepStage(s.stage, s.duration_minutes, 27) for s in summer.stages]}
    )
    winter = service.db.save_profile("Winter", service.schedule, NOW)

    running = [p.name for p in service.db.profiles() if p.matches(service.schedule)]
    assert running == ["Winter"]

    service.update_schedule(
        {"stages": list(summer.stages), "cooling_speed": summer.cooling_speed}
    )
    running = [p.name for p in service.db.profiles() if p.matches(service.schedule)]
    assert running == ["Summer"]
    assert winter.id != summer.id


def test_a_profile_survives_the_night_getting_longer(db, schedule):
    """The bug that made this feature useless the moment a wake time moved.

    A Schedule rescales its stages to fill the night, so a profile saved from an
    eight hour night lands in a nine hour night as different minutes. Comparing
    the numbers as saved meant a profile read as not running immediately after
    being loaded, which is the one case that has to work.
    """
    saved = db.save_profile("Summer", schedule, NOW)

    longer = Schedule(wake_time=time(9, 0), bed_time=schedule.bed_time, stages=list(saved.stages))
    assert longer.night_minutes != schedule.night_minutes, "the test needs a different night"
    assert [s.duration_minutes for s in longer.stages] != [
        s.duration_minutes for s in saved.stages
    ], "and the stages really were rescaled"

    assert saved.matches(longer), "loading it has to leave it reading as running"
