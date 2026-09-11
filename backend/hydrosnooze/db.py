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
from typing import NamedTuple

from .events import Event, Level
from .models import (
    MINUTES_IN_A_DAY,
    Profile,
    STAGE_ORDER,
    Mode,
    Schedule,
    SleepStage,
    Stage,
    default_stages,
)

#: Nights needed before the measured figure replaces the estimate. One night is
#: an anecdote, and the estimate it would replace is at least consistent.
MIN_RUNS_TO_LEARN = 3

#: The least distance a run must have covered before it can teach a rate.
#:
#: A run that started half a degree from its target got there almost at once,
#: and two minutes divided by half a degree is not a rate. It is noise with a
#: unit attached, and it is what sent the pre-heat out at 21:58.
MIN_LEARNABLE_GAP_C = 2.0

#: Fixed cost on top of a learned rate: the presses themselves, and the unit
#: getting going before any water moves.
#:
#: Smaller than the estimate's fifteen, because that fifteen is also covering
#: the estimate being a guess. A measured rate does not need padding for that.
LEARNED_BASE_MINUTES = 5


class Sample(NamedTuple):
    """One sampling beat: what the unit drew and what the bed was doing.

    The degrees are None on any beat the probe board was quiet, and on every beat
    recorded before the probes existed.
    """

    at: datetime
    watts: float
    flow_c: float | None = None
    return_c: float | None = None
    room_c: float | None = None


class PreconditionRow(NamedTuple):
    """One finished pre-conditioning run, as it was stored.

    Named rather than a bare tuple because the temperatures arrived later than
    the timings did, and `row[6]` is a poor way to ask which one is the end
    temperature. Everything after `reached` is None on a run decided off the plug
    alone, which is every run recorded before the probes went on.
    """

    at: datetime
    mode: str
    target_c: int
    seconds: int
    reached: bool
    start_c: float | None = None
    end_c: float | None = None
    room_c: float | None = None
    decided_by: str | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedule (
    id                   INTEGER PRIMARY KEY CHECK (id = 1),
    name                 TEXT    NOT NULL,
    enabled              INTEGER NOT NULL,
    days_of_week         TEXT    NOT NULL,
    wake_time            TEXT    NOT NULL,
    bed_time             TEXT    NOT NULL,
    stages               TEXT    NOT NULL,
    cooling_speed        TEXT    NOT NULL,
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

-- One row per sampling beat: what the unit drew, and what the bed was doing.
--
-- The temperatures share this table rather than getting their own, because they
-- are read on the same beat for exactly this reason: a chart wants watts and
-- degrees from the same moment, and two tables would have to be joined on a
-- timestamp that was always going to be the same one.
--
-- Nullable, because they were not measurable until the probes went on and are
-- not measurable whenever that board is quiet. A row with watts and no degrees
-- is the truth about that moment.
CREATE TABLE IF NOT EXISTS power_samples (
    at       TEXT NOT NULL PRIMARY KEY,
    watts    REAL NOT NULL,
    flow_c   REAL,
    return_c REAL,
    room_c   REAL
);

-- Which jobs have already run tonight, so a restart does not forget.
--
-- This lived only in memory until 9 September, which was harmless while a
-- restart was an unusual event. It stopped being harmless the day Restart=always
-- and the watchdog made restarts routine and the notifier started pushing to a
-- phone: the marks were lost, the next tick decided every stage that had already
-- run had been missed, and a night that went perfectly sent two alarms at 3am.
--
-- Keyed by job rather than by night, so this table holds five rows and never
-- grows. The wake time is the value, and `has_fired` compares it, so a mark left
-- over from a previous night simply does not match and is ignored. Nothing to
-- prune.
CREATE TABLE IF NOT EXISTS fired_jobs (
    key     TEXT PRIMARY KEY,
    wake_at TEXT NOT NULL
);

-- Saved nights, by name. "Summer", "Winter", "Guest room".
--
-- Only the shape of a night: the stages and the cooling speed. Not the wake time
-- and not the days of the week, because those belong to the week you are having
-- rather than to the weather, and loading "Summer" should not move an alarm.
--
-- No `active` column on purpose. Which one is running is worked out by comparing
-- the stages against the schedule, so it cannot be a flag left behind by an edit
-- that happened afterwards.
CREATE TABLE IF NOT EXISTS profiles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL,
    stages        TEXT    NOT NULL,
    cooling_speed TEXT    NOT NULL,
    created_at    TEXT    NOT NULL
);

-- How long the bed really took to reach a temperature.
--
-- The lead time before bedtime used to be an estimate: a fixed cost plus a rate
-- per degree, from an assumed room temperature. It can be answered properly now,
-- by two sensors that answer it differently.
--
-- The plug answers it about the machine. When the unit reaches its setpoint the
-- draw falls out of the working band into the idle one, and the time to that
-- fall is how long the machine worked for.
--
-- The hose probes answer it about the bed. While the bed is still taking heat
-- the water comes back at a different temperature from the way it went out, and
-- when that gap closes the exchange has finished.
--
-- `decided_by` records which of the two made the call, because they are not the
-- same measurement and averaging them together would quietly hide that.
--
-- `reached` is false when it never got there, which is worth keeping rather than
-- discarding: a run that never settled means the target was not achievable that
-- night, and that is the more useful thing to know.
CREATE TABLE IF NOT EXISTS precondition_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT    NOT NULL,
    mode        TEXT    NOT NULL,
    target_c    INTEGER NOT NULL,
    seconds     INTEGER NOT NULL,
    reached     INTEGER NOT NULL,
    start_c     REAL,
    end_c       REAL,
    room_c      REAL,
    decided_by  TEXT
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
    "bed_time",
    "stages",
    "cooling_speed",
    "updated_at",
)

#: How long the unit's own three phases lasted, in minutes. Fixed in the hardware,
#: and the only durations a database written before the app drove the night can
#: have meant.
LEGACY_PHASE_MINUTES = (240, 240, 30)


#: How many power samples to hold before writing them as one transaction.
#:
#: This database lives on an SD card and the samples are far and away its
#: heaviest writer: one row every thirty seconds is 2,880 committed transactions
#: a day, each a handful of bytes that the card turns into an erase of a block
#: thousands of times larger. Twenty at a time is ten minutes of them.
#:
#: The card is already the component most likely to end this project, so it is
#: worth the one cost: an unclean shutdown loses whatever is still in hand. That
#: is up to ten minutes of chart and nothing else. Nothing can read a wrong
#: answer, because every read flushes first, so a held sample is unwritten but
#: never invisible.
POWER_BATCH = 20


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._pending_power: list[tuple] = []
        self._tune_for_an_sd_card()
        self._db.executescript(SCHEMA)
        self._migrate()
        self._db.commit()

    def _tune_for_an_sd_card(self) -> None:
        """Two pragmas, and the reason for both is the same component.

        The default rollback journal fsyncs on every commit, and flash turns each
        of those into an erase far larger than the row being written. WAL appends
        instead and syncs at a checkpoint, which is the difference between
        thousands of fsyncs a day and a handful.

        synchronous=NORMAL is the honest half of the trade. A power cut can lose
        the last few committed transactions. It cannot corrupt the file, which is
        the failure that would actually matter, and everything at risk is a power
        sample or an event rather than the schedule.

        An in-memory database has no journal to set and reports the mode it kept
        rather than failing, so the tests need no special case.
        """
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")

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
        # The temperatures on a sample. Older rows keep NULL, which is honest:
        # nothing measured the bed on those nights.
        cols = {r["name"] for r in self._db.execute("PRAGMA table_info(power_samples)")}
        for name in ("flow_c", "return_c", "room_c"):
            if name not in cols:
                self._db.execute(f"ALTER TABLE power_samples ADD COLUMN {name} REAL")

        # The probe columns. Older rows keep NULL, which is honest: those runs
        # were decided off the plug and no temperature was measured.
        pre = {r["name"] for r in self._db.execute("PRAGMA table_info(precondition_runs)")}
        for name, kind in (
            ("start_c", "REAL"),
            ("end_c", "REAL"),
            ("room_c", "REAL"),
            ("decided_by", "TEXT"),
        ):
            if name not in pre:
                self._db.execute(f"ALTER TABLE precondition_runs ADD COLUMN {name} {kind}")

        columns = {r["name"] for r in self._db.execute("PRAGMA table_info(schedule)")}
        added = [
            # Empty rather than a real time: a row written before bedtime was a
            # setting has its own night length sitting in the stage durations,
            # and _bed_time_from works it out rather than imposing 22:30.
            ("bed_time", "TEXT NOT NULL DEFAULT ''"),
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
        """Write what is in hand, then let SQLite fold the WAL back in.

        A clean close checkpoints and removes the -wal file, which is what makes
        copying the database somewhere else safe. That is exactly why the setup
        stops the service before doing it.
        """
        self.flush_power()
        self._db.close()

    # --- Saved nights -----------------------------------------------------------

    def profiles(self) -> list[Profile]:
        rows = self._db.execute("SELECT * FROM profiles ORDER BY name COLLATE NOCASE").fetchall()
        return [
            Profile(
                id=r["id"],
                name=r["name"],
                stages=[
                    SleepStage(Stage(s["stage"]), s["duration_minutes"], s["temp_c"])
                    for s in json.loads(r["stages"])
                ],
                cooling_speed=Mode(r["cooling_speed"]),
                created_at=_parse(r["created_at"]),
            )
            for r in rows
        ]

    def save_profile(self, name: str, schedule: Schedule, at: datetime) -> Profile:
        """Snapshot the night the schedule is currently holding, under a name.

        A name that already exists is overwritten rather than duplicated. Two
        profiles called Summer is never what anyone meant, and picking between
        them in a list is worse than losing the older one.
        """
        stages = json.dumps(
            [
                {"stage": s.stage.value, "duration_minutes": s.duration_minutes, "temp_c": s.temp_c}
                for s in schedule.stages
            ]
        )
        existing = self._db.execute(
            "SELECT id FROM profiles WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if existing is not None:
            # The name too. Matching is case-insensitive, so typing "summer"
            # over "Summer" took this branch and silently kept the old spelling,
            # which reads as the rename having failed rather than as a rule.
            self._db.execute(
                "UPDATE profiles SET name = ?, stages = ?, cooling_speed = ? WHERE id = ?",
                (name, stages, schedule.cooling_speed.value, existing["id"]),
            )
            new_id = existing["id"]
        else:
            cursor = self._db.execute(
                "INSERT INTO profiles (name, stages, cooling_speed, created_at) "
                "VALUES (?, ?, ?, ?)",
                (name, stages, schedule.cooling_speed.value, at.isoformat()),
            )
            new_id = cursor.lastrowid
        self._db.commit()
        return next(p for p in self.profiles() if p.id == new_id)

    def delete_profile(self, profile_id: int) -> bool:
        cursor = self._db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        self._db.commit()
        return cursor.rowcount > 0

    def profile(self, profile_id: int) -> Profile | None:
        return next((p for p in self.profiles() if p.id == profile_id), None)

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
                                  bed_time, stages, cooling_speed, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, enabled=excluded.enabled,
                days_of_week=excluded.days_of_week, wake_time=excluded.wake_time,
                bed_time=excluded.bed_time,
                stages=excluded.stages, cooling_speed=excluded.cooling_speed,
                updated_at=excluded.updated_at
            """,
            (
                schedule.name,
                int(schedule.enabled),
                json.dumps(schedule.days_of_week),
                schedule.wake_time.strftime("%H:%M"),
                schedule.bed_time.strftime("%H:%M"),
                json.dumps(
                    [
                        {"stage": s.stage.value, "duration_minutes": s.duration_minutes, "temp_c": s.temp_c}
                        for s in schedule.stages
                    ]
                ),
                schedule.cooling_speed.value,
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

    def events_between(self, start: datetime, end: datetime) -> list[Event]:
        """Everything logged inside a stretch of time, oldest first.

        For the morning report, which asks about one night rather than about the
        last two hundred things that happened.
        """
        rows = self._db.execute(
            "SELECT * FROM events WHERE at >= ? AND at <= ? ORDER BY id",
            (start.isoformat(), end.isoformat()),
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

    # --- Which jobs have already run --------------------------------------------

    def fired_marks(self) -> dict[str, datetime]:
        rows = self._db.execute("SELECT key, wake_at FROM fired_jobs").fetchall()
        return {r["key"]: datetime.fromisoformat(r["wake_at"]) for r in rows}

    def set_fired_marks(self, marks: dict[str, datetime]) -> None:
        """Replace the lot, in one transaction.

        Whole-set rather than one row at a time because there are five of them and
        a half-written set is a worse thing to come back to than a stale one.
        """
        with self._db:
            self._db.execute("DELETE FROM fired_jobs")
            self._db.executemany(
                "INSERT INTO fired_jobs (key, wake_at) VALUES (?, ?)",
                [(key, at.isoformat()) for key, at in marks.items()],
            )

    # --- Power ----------------------------------------------------------------

    def add_power_sample(
        self,
        at: datetime,
        watts: float,
        *,
        flow_c: float | None = None,
        return_c: float | None = None,
        room_c: float | None = None,
    ) -> None:
        self._pending_power.append((at.isoformat(), watts, flow_c, return_c, room_c))
        if len(self._pending_power) >= POWER_BATCH:
            self.flush_power()

    def flush_power(self) -> None:
        """Write whatever is in hand. Called before every read of it, so the
        batching is invisible to anything asking a question."""
        if not self._pending_power:
            return
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO power_samples "
                "(at, watts, flow_c, return_c, room_c) VALUES (?, ?, ?, ?, ?)",
                self._pending_power,
            )
        self._pending_power.clear()

    def power_history(self, since: datetime) -> list[tuple[datetime, float]]:
        self.flush_power()
        rows = self._db.execute(
            "SELECT at, watts FROM power_samples WHERE at >= ? ORDER BY at",
            (since.isoformat(),),
        ).fetchall()
        return [(datetime.fromisoformat(r["at"]), r["watts"]) for r in rows]

    def night_history(self, since: datetime) -> list[Sample]:
        """Everything measured since a moment, watts and degrees together.

        The whole night in one query, which is what a chart wants and what
        answering "did the bed actually hold 27C" needs. Until this existed the
        degrees were shown live and then thrown away, so the only question that
        could be asked in the morning was about the machine.
        """
        self.flush_power()
        rows = self._db.execute(
            "SELECT at, watts, flow_c, return_c, room_c FROM power_samples "
            "WHERE at >= ? ORDER BY at",
            (since.isoformat(),),
        ).fetchall()
        return [
            Sample(
                datetime.fromisoformat(r["at"]),
                r["watts"],
                r["flow_c"],
                r["return_c"],
                r["room_c"],
            )
            for r in rows
        ]

    # --- What the bed actually does -------------------------------------------

    def record_precondition(
        self,
        at: datetime,
        mode: str,
        target_c: int,
        seconds: int,
        reached: bool,
        *,
        start_c: float | None = None,
        end_c: float | None = None,
        room_c: float | None = None,
        decided_by: str | None = None,
    ) -> None:
        """One pre-conditioning run, and what it took.

        The temperatures are optional because they were not measurable until the
        probes went on, and because a probe board that has gone quiet must not
        stop the run being recorded. A row with the timing and no temperatures is
        worth more than no row.
        """
        self._db.execute(
            "INSERT INTO precondition_runs "
            "(at, mode, target_c, seconds, reached, start_c, end_c, room_c, decided_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                at.isoformat(),
                mode,
                target_c,
                seconds,
                int(reached),
                start_c,
                end_c,
                room_c,
                decided_by,
            ),
        )
        self._db.commit()

    def precondition_since(self, start: datetime) -> PreconditionRow | None:
        """The pre-conditioning run for one night, or None if it never ran."""
        rows = [r for r in self.precondition_runs(5) if r.at >= start]
        return rows[0] if rows else None

    def learned_lead_minutes(
        self, mode: str, target_c: int, gap_c: float, *, within_c: int = 3
    ) -> int | None:
        """How long this bed needs to close a gap of this size, near this target.

        A **rate**, not a duration, and that distinction is the whole of this
        method. It used to average the raw minutes of past runs at a similar
        target and hand that back whatever tonight's gap was.

        On 11 September that put Liam's pre-heat at 21:58 for a 22:00 bedtime,
        aiming to take the bed from 20C to 28C in two minutes. He had spent the
        previous nights with the bed already warm, so its runs had half a degree
        to cover and reached their target almost at once. Three of those and it
        had "learned" that this bed warms in two minutes, as a fact about the
        bed rather than about the half degree. The estimate it replaced would
        have said twenty-three.

        So only runs that actually travelled teach anything, and what they teach
        is minutes per degree, applied to the distance tonight really has to go.
        """
        rows = self._db.execute(
            "SELECT seconds, start_c, end_c FROM precondition_runs "
            "WHERE mode = ? AND reached = 1 AND ABS(target_c - ?) <= ? "
            "AND start_c IS NOT NULL AND end_c IS NOT NULL "
            "ORDER BY id DESC LIMIT 10",
            (mode, target_c, within_c),
        ).fetchall()

        rates = [
            r["seconds"] / 60 / travelled
            for r in rows
            if (travelled := abs(r["end_c"] - r["start_c"])) >= MIN_LEARNABLE_GAP_C
        ]
        if len(rates) < MIN_RUNS_TO_LEARN:
            return None
        return max(1, round(LEARNED_BASE_MINUTES + abs(gap_c) * (sum(rates) / len(rates))))

    def precondition_runs(self, limit: int = 20) -> list[PreconditionRow]:
        rows = self._db.execute(
            "SELECT * FROM precondition_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            PreconditionRow(
                datetime.fromisoformat(r["at"]),
                r["mode"],
                r["target_c"],
                r["seconds"],
                bool(r["reached"]),
                r["start_c"],
                r["end_c"],
                r["room_c"],
                r["decided_by"],
            )
            for r in rows
        ]

    def prune_power(self, before: datetime) -> None:
        self.flush_power()
        self._db.execute("DELETE FROM power_samples WHERE at < ?", (before.isoformat(),))
        self._db.commit()


def _schedule_from(row: sqlite3.Row) -> Schedule:
    """One row, whatever shape of the app wrote it.

    Run after `_migrate`, so every current column is there. Older columns may be
    there too, and where they hold something the current ones cannot, they win:
    a row that still has them has never been written by this version of the app,
    because writing it is exactly what was failing.
    """
    wake_time = _time_from(row["wake_time"])
    stages = _stages_from(row)
    return Schedule(
        name=row["name"],
        enabled=bool(row["enabled"]),
        days_of_week=json.loads(row["days_of_week"]),
        wake_time=wake_time,
        bed_time=_bed_time_from(row, wake_time, stages),
        stages=stages,
        cooling_speed=_cooling_speed_from(row),
        updated_at=_parse(row["updated_at"]),
    )


def _bed_time_from(row: sqlite3.Row, wake_time: time, stages: list[SleepStage]) -> time:
    """When the lights go out.

    A row written before bedtime was a setting does not have the column, but it
    does have the night: the stage durations are what bedtime used to be derived
    from. Working backwards from the wake time keeps the night exactly as long as
    it was, rather than imposing a default and quietly rescaling everything.
    """
    stored = row["bed_time"] if "bed_time" in set(row.keys()) else ""
    if stored:
        return _time_from(stored)
    minutes = sum(s.duration_minutes for s in stages)
    wake_minutes = wake_time.hour * 60 + wake_time.minute
    bed = (wake_minutes - minutes) % MINUTES_IN_A_DAY
    return time(bed // 60, bed % 60)


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


def _time_from(raw: str) -> time:
    hour, minute = (int(part) for part in raw.split(":"))
    return time(hour, minute)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


_ = Level
