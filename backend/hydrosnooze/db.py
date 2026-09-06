"""SQLite. Small, local, and the only thing that survives a restart.

Plain sqlite3 rather than an async driver or an ORM. Every operation here is a
handful of rows, the Pi is the only writer, and a beginner following SETUP.md
should not have to install a database.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, time
from pathlib import Path

from .events import Event, Level
from .models import (
    STAGE_ORDER,
    Mode,
    Precondition,
    Schedule,
    SleepStage,
    Stage,
    default_stages,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    name                 TEXT    NOT NULL,
    enabled              INTEGER NOT NULL,
    days_of_week         TEXT    NOT NULL,
    wake_time            TEXT    NOT NULL,
    stages               TEXT    NOT NULL,
    cooling_speed        TEXT    NOT NULL,
    precool_enabled      INTEGER NOT NULL,
    precondition         TEXT    NOT NULL DEFAULT 'cool',
    precool_lead_minutes INTEGER NOT NULL,
    updated_at           TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    level   TEXT NOT NULL,
    kind    TEXT NOT NULL,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_at ON events (at DESC);

CREATE TABLE IF NOT EXISTS power_samples (
    at    TEXT NOT NULL PRIMARY KEY,
    watts REAL NOT NULL
);
"""

#: Every column the schedule table has now, in the order SCHEMA declares them.
#: Anything in the table and not in here belongs to an older shape of the app and
#: is dropped by the migration below.
SCHEDULE_COLUMNS = (
    "id",
    "name",
    "enabled",
    "days_of_week",
    "wake_time",
    "stages",
    "cooling_speed",
    "precool_enabled",
    "precondition",
    "precool_lead_minutes",
    "updated_at",
)

#: How long the unit's own three phases lasted, in minutes. Fixed in the hardware,
#: and the only durations a database written before the app drove the night can
#: have meant.
LEGACY_PHASE_MINUTES = (240, 240, 30)


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._migrate()
        self._db.commit()

    def _migrate(self) -> None:
        """Bring an older database up to the current schema.

        CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so
        a database written before the schema changed keeps the old shape. There is
        exactly one row and one deployment, so this stays small: add the columns
        that are new, then rebuild the table if it still carries columns that are
        gone.

        The second half is not tidying. The schema before the app drove the night
        had phase1_temp_c NOT NULL with no default, and nothing writes that column
        any more, so on an upgraded database every single save failed with a
        constraint error while every read carried on working.
        """
        columns = {r["name"] for r in self._db.execute("PRAGMA table_info(schedule)")}
        added = [
            ("precondition", "TEXT NOT NULL DEFAULT 'cool'"),
            ("stages", "TEXT NOT NULL DEFAULT '[]'"),
            ("cooling_speed", "TEXT NOT NULL DEFAULT 'quiet'"),
        ]
        for name, definition in added:
            if name not in columns:
                self._db.execute(f"ALTER TABLE schedule ADD COLUMN {name} {definition}")
                columns.add(name)

        if columns - set(SCHEDULE_COLUMNS):
            self._rebuild_schedule()

    def _rebuild_schedule(self) -> None:
        """Rebuild the schedule table around the columns the app still has.

        SQLite only learned DROP COLUMN in 3.35 and the version on a Pi is not
        ours to choose, so the old table is renamed out of the way, the current
        one created from SCHEMA, and the row written back through the normal save
        path. One row, so there is nothing to page through.

        What the row means is preserved, not just its new-shaped columns: the
        three old fixed phases become Deep, REM and Wake, and the old single
        night mode becomes the cooling speed. See `_schedule_from`.
        """
        row = self._db.execute("SELECT * FROM schedule WHERE id = 1").fetchone()
        carried = _schedule_from(row) if row is not None else None

        self._db.execute("DROP TABLE IF EXISTS schedule_old")
        self._db.execute("ALTER TABLE schedule RENAME TO schedule_old")
        self._db.executescript(SCHEMA)
        if carried is not None:
            self.save_schedule(carried)
        self._db.execute("DROP TABLE schedule_old")
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # --- Schedule -------------------------------------------------------------

    def load_schedule(self) -> Schedule:
        row = self._db.execute("SELECT * FROM schedule WHERE id = 1").fetchone()
        if row is None:
            schedule = Schedule()
            self.save_schedule(schedule)
            return schedule
        return _schedule_from(row)

    def save_schedule(self, schedule: Schedule) -> None:
        self._db.execute(
            """
            INSERT INTO schedule (id, name, enabled, days_of_week, wake_time,
                                  stages, cooling_speed,
                                  precool_enabled, precondition, precool_lead_minutes,
                                  updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, enabled=excluded.enabled,
                days_of_week=excluded.days_of_week, wake_time=excluded.wake_time,
                stages=excluded.stages, cooling_speed=excluded.cooling_speed,
                precool_enabled=excluded.precool_enabled,
                precondition=excluded.precondition,
                precool_lead_minutes=excluded.precool_lead_minutes,
                updated_at=excluded.updated_at
            """,
            (
                schedule.name,
                int(schedule.enabled),
                json.dumps(schedule.days_of_week),
                schedule.wake_time.strftime("%H:%M"),
                json.dumps(
                    [
                        {"stage": s.stage.value, "duration_minutes": s.duration_minutes, "temp_c": s.temp_c}
                        for s in schedule.stages
                    ]
                ),
                schedule.cooling_speed.value,
                int(schedule.precool_enabled),
                schedule.precondition.value,
                schedule.precool_lead_minutes,
                _iso(schedule.updated_at),
            ),
        )
        self._db.commit()

    # --- Events ---------------------------------------------------------------

    def add_event(self, event: Event) -> None:
        self._db.execute(
            "INSERT INTO events (at, level, kind, message) VALUES (?, ?, ?, ?)",
            (event.at.isoformat(), event.level, event.kind, event.message),
        )
        self._db.commit()

    def recent_events(self, limit: int = 200) -> list[Event]:
        rows = self._db.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            Event(
                id=r["id"],
                at=datetime.fromisoformat(r["at"]),
                level=r["level"],  # type: ignore[arg-type]
                kind=r["kind"],
                message=r["message"],
            )
            for r in rows
        ]

    def prune_events(self, keep: int = 5000) -> None:
        self._db.execute(
            "DELETE FROM events WHERE id NOT IN "
            "(SELECT id FROM events ORDER BY id DESC LIMIT ?)",
            (keep,),
        )
        self._db.commit()

    # --- Power ----------------------------------------------------------------

    def add_power_sample(self, at: datetime, watts: float) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO power_samples (at, watts) VALUES (?, ?)",
            (at.isoformat(), watts),
        )
        self._db.commit()

    def power_history(self, since: datetime) -> list[tuple[datetime, float]]:
        rows = self._db.execute(
            "SELECT at, watts FROM power_samples WHERE at >= ? ORDER BY at",
            (since.isoformat(),),
        ).fetchall()
        return [(datetime.fromisoformat(r["at"]), r["watts"]) for r in rows]

    def prune_power(self, before: datetime) -> None:
        self._db.execute("DELETE FROM power_samples WHERE at < ?", (before.isoformat(),))
        self._db.commit()


def _schedule_from(row: sqlite3.Row) -> Schedule:
    """One row, whatever shape of the app wrote it.

    Run after `_migrate`, so every current column is there. Older columns may be
    there too, and where they hold something the current ones cannot, they win:
    a row that still has them has never been written by this version of the app,
    because writing it is exactly what was failing.
    """
    hour, minute = (int(p) for p in row["wake_time"].split(":"))
    return Schedule(
        name=row["name"],
        enabled=bool(row["enabled"]),
        days_of_week=json.loads(row["days_of_week"]),
        wake_time=time(hour, minute),
        stages=_stages_from(row),
        cooling_speed=_cooling_speed_from(row),
        precool_enabled=bool(row["precool_enabled"]),
        precondition=Precondition(row["precondition"]),
        precool_lead_minutes=row["precool_lead_minutes"],
        updated_at=_parse(row["updated_at"]),
    )


def _stages_from(row: sqlite3.Row) -> list[SleepStage]:
    """The night, in order.

    Before the app drove the night itself the schedule was the unit's own three
    phases: 4h, 4h and 30m, fixed, one temperature each. Those are Deep, REM and
    Wake with the durations spelled out, so an upgraded database keeps the three
    temperatures rather than quietly resetting to the defaults.
    """
    parsed = json.loads(row["stages"]) if row["stages"] else []
    if parsed:
        return [
            SleepStage(Stage(s["stage"]), s["duration_minutes"], s["temp_c"]) for s in parsed
        ]
    # sqlite3.Row iterates its values, not its names, so membership goes via keys().
    if "phase1_temp_c" in set(row.keys()):
        temps = (row["phase1_temp_c"], row["phase2_temp_c"], row["phase3_temp_c"])
        return [
            SleepStage(stage, minutes, temp)
            for stage, minutes, temp in zip(STAGE_ORDER, LEGACY_PHASE_MINUTES, temps)
        ]
    return default_stages()


def _cooling_speed_from(row: sqlite3.Row) -> Mode:
    """Which speed a cooling stage runs at.

    The old schema had one `mode` for the whole night, and warming was a valid
    choice there. It is not one here: a stage's mode follows its temperature now,
    and this only picks how hard the unit works when a stage is cooling. So a
    legacy warming night has nothing to carry over and takes the saved speed.
    """
    if "mode" in set(row.keys()):
        legacy = Mode(row["mode"])
        if legacy.is_cooling:
            return legacy
    return Mode(row["cooling_speed"])


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


_ = Level
