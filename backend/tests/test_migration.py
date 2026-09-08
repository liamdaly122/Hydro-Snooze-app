"""Opening a database written by an older version of the app.

The schema is created with CREATE TABLE IF NOT EXISTS and there is no migration
framework, so a table that already exists keeps whatever shape it was made with.
That is fine while columns are only ever added. It stopped being fine when the
app started driving the night itself and the three fixed phase columns went away:
they were NOT NULL with no default, nothing wrote them any more, and so every
save failed while every read carried on working.
"""

from __future__ import annotations

import sqlite3
from datetime import time

import pytest

from hydrosnooze.db import Database
from hydrosnooze.models import Mode, Stage

#: The schedule table exactly as it was before the app drove the night.
LEGACY_SCHEMA = """
CREATE TABLE schedule (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    name                 TEXT    NOT NULL,
    enabled              INTEGER NOT NULL,
    days_of_week         TEXT    NOT NULL,
    wake_time            TEXT    NOT NULL,
    phase1_temp_c        INTEGER NOT NULL,
    phase2_temp_c        INTEGER NOT NULL,
    phase3_temp_c        INTEGER NOT NULL,
    mode                 TEXT    NOT NULL,
    precool_enabled      INTEGER NOT NULL,
    precool_lead_minutes INTEGER NOT NULL,
    last_written_at      TEXT,
    updated_at           TEXT
);
"""

#: The columns added in place by the first attempt at a migration, which got the
#: reads working and left the writes broken. Liam's Mac was in this state.
HALF_MIGRATED = (
    ("precondition", "TEXT NOT NULL DEFAULT 'cool'"),
    ("stages", "TEXT NOT NULL DEFAULT '[]'"),
    ("cooling_speed", "TEXT NOT NULL DEFAULT 'quiet'"),
)


def legacy_db(path, *, mode: str = "standard", half_migrated: bool = False) -> None:
    db = sqlite3.connect(path)
    db.executescript(LEGACY_SCHEMA)
    db.execute(
        "INSERT INTO schedule VALUES (1, 'Tonight', 1, '[0, 1, 2, 3, 4]', '06:30',"
        " 19, 21, 26, ?, 1, 30, NULL, '2026-09-01T22:00:00')",
        (mode,),
    )
    if half_migrated:
        for name, definition in HALF_MIGRATED:
            db.execute(f"ALTER TABLE schedule ADD COLUMN {name} {definition}")
    db.commit()
    db.close()


@pytest.fixture(params=[False, True], ids=["old-schema", "half-migrated"])
def upgraded(request, tmp_path) -> Database:
    """A database from before the rewrite, opened by the current app."""
    path = tmp_path / "legacy.db"
    legacy_db(path, half_migrated=request.param)
    db = Database(path)
    yield db
    db.close()


def test_saving_works_at_all(upgraded: Database) -> None:
    """The bug itself: every PUT /api/schedule returned 500 on an upgraded Mac."""
    upgraded.save_schedule(upgraded.load_schedule())


def test_the_old_columns_are_gone(upgraded: Database) -> None:
    columns = [r["name"] for r in upgraded._db.execute("PRAGMA table_info(schedule)")]
    assert "phase1_temp_c" not in columns
    assert "last_written_at" not in columns
    assert columns.count("id") == 1


def test_the_rebuild_leaves_nothing_behind(upgraded: Database) -> None:
    tables = upgraded._db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    assert "schedule_old" not in {r["name"] for r in tables}


def test_the_three_phases_become_the_three_stages(upgraded: Database) -> None:
    """The old phases were 4h, 4h and 30m, fixed. Those are Deep, REM and Wake,
    so the temperatures Liam set carry over rather than reverting to defaults."""
    stages = upgraded.load_schedule().stages
    assert [(s.stage, s.duration_minutes, s.temp_c) for s in stages] == [
        (Stage.DEEP, 240, 19),
        (Stage.REM, 240, 21),
        (Stage.WAKE, 30, 26),
    ]


def test_the_rest_of_the_schedule_survives(upgraded: Database) -> None:
    schedule = upgraded.load_schedule()
    assert schedule.name == "Tonight"
    assert schedule.enabled is True
    assert schedule.days_of_week == [0, 1, 2, 3, 4]
    assert schedule.wake_time == time(6, 30)


def test_the_old_night_mode_becomes_the_cooling_speed(upgraded: Database) -> None:
    assert upgraded.load_schedule().cooling_speed is Mode.STANDARD


def test_a_legacy_warming_night_has_no_speed_to_carry(tmp_path) -> None:
    """`mode` used to cover warming too. It cannot now: a stage's mode follows its
    temperature, and this setting only says how hard a cooling stage works."""
    path = tmp_path / "warming.db"
    legacy_db(path, mode="warming")
    db = Database(path)
    assert db.load_schedule().cooling_speed.is_cooling
    db.close()


def test_the_upgrade_survives_a_restart(upgraded: Database) -> None:
    """Reopening must not undo the rebuild or re-derive the stages from columns
    that are no longer there."""
    before = upgraded.load_schedule()
    path = upgraded.path
    upgraded.close()

    again = Database(path)
    try:
        assert again.load_schedule() == before
        again.save_schedule(before)
    finally:
        again.close()


def test_a_current_database_is_left_alone(tmp_path) -> None:
    path = tmp_path / "current.db"
    first = Database(path)
    schedule = first.load_schedule()
    first.close()

    second = Database(path)
    try:
        assert second.load_schedule() == schedule
    finally:
        second.close()


# --- Keeping the database from growing forever ---------------------------------


def test_pruning_events_keeps_the_newest():
    """prune_events was written and then never called, so events were the one
    table growing without limit, on the SD card that is the likeliest thing in
    the whole setup to fail."""
    from datetime import datetime

    from hydrosnooze.db import Database
    from hydrosnooze.events import Event

    db = Database(":memory:")
    for i in range(60):
        db.add_event(Event(id=0, at=datetime(2026, 9, 8, 21, 0), level="info", kind="t", message=f"e{i}"))

    db.prune_events(keep=25)
    kept = db.recent_events(500)

    assert len(kept) == 25
    assert kept[0].message == "e59", "it threw away the newest instead of the oldest"
    assert kept[-1].message == "e35"


def test_pruning_a_small_log_does_nothing():
    from datetime import datetime

    from hydrosnooze.db import Database
    from hydrosnooze.events import Event

    db = Database(":memory:")
    db.add_event(Event(id=0, at=datetime(2026, 9, 8, 21, 0), level="info", kind="t", message="only"))
    db.prune_events(keep=100)
    assert len(db.recent_events(10)) == 1
