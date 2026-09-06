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
from .models import Mode, Precondition, Schedule

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule (
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
    precondition         TEXT    NOT NULL DEFAULT 'cool',
    precool_lead_minutes INTEGER NOT NULL,
    last_written_at      TEXT,
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
        a database written before a column was added keeps the old shape. There is
        exactly one row and one deployment, so adding columns in place is enough:
        no version table, no migration framework.
        """
        columns = {r["name"] for r in self._db.execute("PRAGMA table_info(schedule)")}
        added = [
            ("precondition", "TEXT NOT NULL DEFAULT 'cool'"),
        ]
        for name, definition in added:
            if name not in columns:
                self._db.execute(f"ALTER TABLE schedule ADD COLUMN {name} {definition}")

    def close(self) -> None:
        self._db.close()

    # --- Schedule -------------------------------------------------------------

    def load_schedule(self) -> Schedule:
        row = self._db.execute("SELECT * FROM schedule WHERE id = 1").fetchone()
        if row is None:
            schedule = Schedule()
            self.save_schedule(schedule)
            return schedule
        hour, minute = (int(p) for p in row["wake_time"].split(":"))
        return Schedule(
            name=row["name"],
            enabled=bool(row["enabled"]),
            days_of_week=json.loads(row["days_of_week"]),
            wake_time=time(hour, minute),
            phase1_temp_c=row["phase1_temp_c"],
            phase2_temp_c=row["phase2_temp_c"],
            phase3_temp_c=row["phase3_temp_c"],
            mode=Mode(row["mode"]),
            precool_enabled=bool(row["precool_enabled"]),
            precondition=Precondition(row["precondition"]),
            precool_lead_minutes=row["precool_lead_minutes"],
            last_written_at=_parse(row["last_written_at"]),
            updated_at=_parse(row["updated_at"]),
        )

    def save_schedule(self, schedule: Schedule) -> None:
        self._db.execute(
            """
            INSERT INTO schedule (id, name, enabled, days_of_week, wake_time,
                                  phase1_temp_c, phase2_temp_c, phase3_temp_c, mode,
                                  precool_enabled, precondition, precool_lead_minutes,
                                  last_written_at, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, enabled=excluded.enabled,
                days_of_week=excluded.days_of_week, wake_time=excluded.wake_time,
                phase1_temp_c=excluded.phase1_temp_c, phase2_temp_c=excluded.phase2_temp_c,
                phase3_temp_c=excluded.phase3_temp_c, mode=excluded.mode,
                precool_enabled=excluded.precool_enabled,
                precondition=excluded.precondition,
                precool_lead_minutes=excluded.precool_lead_minutes,
                last_written_at=excluded.last_written_at, updated_at=excluded.updated_at
            """,
            (
                schedule.name,
                int(schedule.enabled),
                json.dumps(schedule.days_of_week),
                schedule.wake_time.strftime("%H:%M"),
                schedule.phase1_temp_c,
                schedule.phase2_temp_c,
                schedule.phase3_temp_c,
                schedule.mode.value,
                int(schedule.precool_enabled),
                schedule.precondition.value,
                schedule.precool_lead_minutes,
                _iso(schedule.last_written_at),
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


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


_ = Level
