"""SQLite. Small, local, and the only thing that survives a restart.

Plain sqlite3 rather than an async driver or an ORM. Every operation here is a
handful of rows, the Pi is the only writer, and a beginner following SETUP.md
should not have to install a database.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time
from pathlib import Path
from typing import NamedTuple

from .events import Event, Level
from .models import (
    MINUTES_IN_A_DAY,
    Holiday,
    Profile,
    Mode,
    Schedule,
    SleepStage,
    Stage,
    Preconditioning,
    Tonight,
    Underway,
    default_stages,
    with_all_stages,
)
from .notes import NightNote

# Renamed on the way in. `Stage` here already means Drift, Deep, REM and Wake:
# what the bed is asked for. This is what the sleeper was measured doing.
from .trials import NightRun, PartRun
from .withings.parse import Minute, Night
from .withings.parse import Stage as SleepStateRun

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

    `target_c` is what the bed was being asked for at that moment, written down
    rather than worked out again afterwards. The morning report used to rebuild
    the night from the schedule as it stands *then*, so editing a routine over
    breakfast rescored the night behind it, a nudge never appeared at all, and a
    perfect night could read as a total failure without a single reading having
    changed. A measurement and the thing it was measured against belong on the
    same row.
    """

    at: datetime
    watts: float
    flow_c: float | None = None
    return_c: float | None = None
    room_c: float | None = None
    target_c: int | None = None


class StoredSession(NamedTuple):
    """One signed-in device, as stored. See access.py."""

    token_hash: str
    #: A mark of the password it signed in with, not the password or its hash.
    password: str
    created_at: datetime
    seen_at: datetime
    #: "home" or "tailscale": which way it came in when it signed in.
    via: str
    #: Which browser, roughly, so a list of them means something to a person.
    label: str


class Decided(NamedTuple):
    """What was decided about one evening's suggestion."""

    wake_on: str
    #: accepted or declined.
    decision: str
    temps: dict[str, int]
    test_part: str | None
    test_offset_c: int | None
    #: Taken by Autopilot itself in the evening, rather than by a tap.
    auto: bool = False


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


class WithingsAccount(NamedTuple):
    """The stored connection to Withings. Two of these fields are keys to my
    health data, so nothing ever logs one of these whole."""

    user_id: str | None
    access_token: str
    refresh_token: str
    expires_at: int
    scope: str | None
    connected_at: int
    last_update: int | None
    needs_reconnect: bool


class StoredNight(NamedTuple):
    """A night as stored: the summary, without its stages and minutes, which are
    read separately because most questions about a week never need them."""

    id: int
    wake_on: str
    start_at: int
    end_at: int
    timezone: str | None
    modified: int
    completed: bool | None
    data: dict[str, object]
    events: dict[str, list[int]] | None


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
    updated_at           TEXT,
    other_days           TEXT    NOT NULL DEFAULT '[]',
    other_bed_time       TEXT    NOT NULL DEFAULT '',
    other_wake_time      TEXT    NOT NULL DEFAULT ''
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

-- How tonight's getting ready was decided, once it has begun. One row, for one
-- night, and a row for any other night is ignored. See models.Underway: the plan
-- is worked out afresh from the bed on every tick, and once the bed is moving
-- that reading is the pre-heat working, not a reason to change the night.
CREATE TABLE IF NOT EXISTS underway (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    wake_on      TEXT    NOT NULL,
    precool_at   TEXT    NOT NULL,
    mode         TEXT,
    lead_minutes INTEGER NOT NULL,
    reason       TEXT    NOT NULL
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
-- What is different about one particular night.
--
-- One row, carrying the wake morning it belongs to. That is what makes it expire
-- without anything having to tidy up: a row for any other night is spent, and the
-- next thing to write here overwrites it.
--
-- The saved schedule is the routine. This is the exception, and keeping the two
-- apart is the whole point. Reaching for the temperature at 2am used to write the
-- new number straight into the routine, so "I was cold once" became "this is how
-- I sleep" and undoing it meant remembering what the number had been.
CREATE TABLE IF NOT EXISTS tonight (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    wake_on     TEXT    NOT NULL,
    skip        INTEGER NOT NULL DEFAULT 0,
    stages      TEXT,
    wake_time   TEXT,
    bed_time    TEXT,
    nudge_c     INTEGER NOT NULL DEFAULT 0,
    nudge_until TEXT,
    cooling_speed TEXT
);

-- Away from home. One row, like tonight, and for the same reason: it expires by
-- the calendar moving past `back_on`, and the next holiday overwrites it.
--
-- Its own table rather than a flag on the schedule, because it is not a change
-- to the routine. Nothing in the schedule is touched while it is set, so there
-- is nothing to put back when it ends.
CREATE TABLE IF NOT EXISTS holiday (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    leaves_on TEXT    NOT NULL,
    back_on   TEXT    NOT NULL
);

-- The handful of settings that are not part of a schedule.
--
-- One row. `learning_on` is the switch behind "use what it has learned": off, and
-- the pre-heat goes back to estimating and nothing corrects the temperature. It
-- exists because the correction is the one learned thing that changes what the
-- bed actually does, and a way out of that should not require SSH.
--
-- `timing_since` is the Sleep timing card's Start again: the last morning that
-- no longer counts, or NULL for every night. The nights before it are kept.
--
-- `autopilot_on` is the switch over all of Autopilot: learned timings and
-- corrections, the drift response and the evening suggestion. Off, the bed runs
-- exactly the temperatures set, at the times set.
CREATE TABLE IF NOT EXISTS preferences (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    learning_on  INTEGER NOT NULL DEFAULT 1,
    timing_since TEXT,
    autopilot_on INTEGER NOT NULL DEFAULT 1,
    hold         TEXT    NOT NULL DEFAULT 'balanced',
    tariff_p     REAL
);

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

-- The Withings account. One row, and the only one in this database that cannot
-- be put back by anything short of me signing in again by hand. See
-- save_withings_tokens for why it is written differently from everything else.
--
-- Every time on the Withings side is a unix timestamp, including these.
CREATE TABLE IF NOT EXISTS withings_account (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    user_id         TEXT,
    access_token    TEXT    NOT NULL,
    refresh_token   TEXT    NOT NULL,
    expires_at      INTEGER NOT NULL,
    scope           TEXT,
    connected_at    INTEGER NOT NULL,
    -- getsummary's lastupdate: the newest `modified` stored so far.
    last_update     INTEGER,
    -- Withings refused the refresh token. Kept so the app still says so after a
    -- restart, and cleared by the next refresh that works.
    needs_reconnect INTEGER NOT NULL DEFAULT 0
);

-- One night off the mat, as Withings last described it.
--
-- Unix time, not the local time the power samples use. A night that runs
-- through the clocks going back has an hour that happens twice, and a unix
-- timestamp is the one kind of time that does not.
--
-- Replaced whole every time it changes, because a night grows: it exists a
-- minute or two after the first time out of bed, stretches each time I get back
-- in, and is modified again the following night.
CREATE TABLE IF NOT EXISTS sleep_nights (
    id        INTEGER PRIMARY KEY,
    wake_on   TEXT    NOT NULL,
    start_at  INTEGER NOT NULL,
    end_at    INTEGER NOT NULL,
    timezone  TEXT,
    modified  INTEGER NOT NULL,
    completed INTEGER,
    -- The summary fields as sent, as JSON, less night_events.
    data      TEXT    NOT NULL,
    -- night_events decoded into absolute times, as JSON. NULL when none came.
    events    TEXT
);
CREATE INDEX IF NOT EXISTS sleep_nights_start ON sleep_nights (start_at);

-- Runs of one sleep state. Merged from Withings' intervals, which are cut far
-- shorter than a stage, and never across a gap, because a gap is out of bed.
CREATE TABLE IF NOT EXISTS sleep_stages (
    night_id INTEGER NOT NULL,
    start_at INTEGER NOT NULL,
    end_at   INTEGER NOT NULL,
    state    INTEGER NOT NULL,
    PRIMARY KEY (night_id, start_at)
);

-- One row a minute in bed. This is the half of the join against the bed
-- temperature that sleep brings: both sides have a reading every minute or so,
-- and this side knows which state I was in for each one.
--
-- NULL is not available, never zero. Heart-rate variability of 0 is stored as
-- NULL too, because it is Withings saying it could not measure it.
CREATE TABLE IF NOT EXISTS sleep_minutes (
    at          INTEGER PRIMARY KEY,
    night_id    INTEGER NOT NULL,
    state       INTEGER NOT NULL,
    hr          INTEGER,
    rr          INTEGER,
    sdnn_1      INTEGER,
    rmssd       INTEGER,
    hrv_quality INTEGER,
    mvt_score   INTEGER,
    snoring     INTEGER
);
CREATE INDEX IF NOT EXISTS sleep_minutes_night ON sleep_minutes (night_id);

-- What each night ran, one row per morning, for the scoreboard. See trials.py.
-- Only the bed's side of the night: the mat's is read from sleep_nights when the
-- scoreboard is built, because Withings goes on changing a night for most of
-- the day after. `parts` is JSON, one object per part of the night.
CREATE TABLE IF NOT EXISTS night_runs (
    wake_on       TEXT    PRIMARY KEY,
    bedtime_at    TEXT    NOT NULL,
    wake_at       TEXT    NOT NULL,
    parts         TEXT    NOT NULL,
    room_c        REAL,
    test_part     TEXT,
    test_offset_c INTEGER,
    rebuilt       INTEGER NOT NULL DEFAULT 0,
    written_at    TEXT    NOT NULL,
    kwh           REAL
);

-- What was decided about each evening's suggestion (suggest.py), one row per
-- night. Kept so the morning can mark a test night as one, and so the app does
-- not offer a suggestion again once it has been answered.
CREATE TABLE IF NOT EXISTS suggestions (
    wake_on       TEXT    PRIMARY KEY,
    decision      TEXT    NOT NULL,
    deep_c        INTEGER,
    rem_c         INTEGER,
    test_part     TEXT,
    test_offset_c INTEGER,
    decided_at    TEXT    NOT NULL,
    auto          INTEGER NOT NULL DEFAULT 0
);

-- How far the suggestions may take each part: `reach` degrees either side of
-- `centre_c`, which is where the part was when the limits were set.
CREATE TABLE IF NOT EXISTS suggest_limits (
    part     TEXT    PRIMARY KEY,
    centre_c INTEGER NOT NULL,
    reach    INTEGER NOT NULL
);

-- How a night felt, from me rather than the mat (notes.py). One row a morning,
-- keyed like every other night. `tags` is a JSON list of notes.TAGS keys.
CREATE TABLE IF NOT EXISTS night_notes (
    wake_on    TEXT PRIMARY KEY,
    rating     INTEGER,
    felt       TEXT,
    tags       TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    submitted  INTEGER NOT NULL DEFAULT 0
);

-- Each device signed in (access.py). The token itself is never stored, only a
-- hash of it, so a copy of this file signs nobody in. `password` is a mark of
-- the password the device signed in with: change the password and every row
-- stops matching, which is what makes changing it sign everything out.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    password   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    seen_at    TEXT NOT NULL,
    via        TEXT NOT NULL,
    label      TEXT NOT NULL DEFAULT ''
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
    "other_days",
    "other_bed_time",
    "other_wake_time",
)

#: How long the unit's own three phases lasted, in minutes. Fixed in the hardware,
#: and the only durations a database written before the app drove the night can
#: have meant.
LEGACY_PHASE_MINUTES = (240, 240, 30)

#: Which stages the unit's own three phases were, spelled out rather than taken
#: off the front of STAGE_ORDER, which has since grown one the hardware never had.
LEGACY_PHASES = (Stage.DEEP, Stage.REM, Stage.WAKE)


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
        # What the bed was being asked for on that beat. NULL on every row older
        # than this column, and the report falls back to reconstructing those.
        if "target_c" not in cols:
            self._db.execute("ALTER TABLE power_samples ADD COLUMN target_c INTEGER")

        # The probe columns. Older rows keep NULL, which is honest: those runs
        # were decided off the plug and no temperature was measured.
        pre = {r["name"] for r in self._db.execute("PRAGMA table_info(precondition_runs)")}
        for name, kind in (
            ("start_c", "REAL"),
            ("end_c", "REAL"),
            ("room_c", "REAL"),
            ("decided_by", "TEXT"),
            # What actually went over the infrared, which stops being the same as
            # `target_c` the moment a correction is earned. See learned_offset_c.
            ("sent_c", "REAL"),
            # Whether this run still teaches anything. "Start again" clears it
            # rather than deleting the row: the run happened, and a record of the
            # night is not the app's to throw away because somebody disliked what
            # it concluded from it.
            ("counts", "INTEGER NOT NULL DEFAULT 1"),
        ):
            if name not in pre:
                self._db.execute(f"ALTER TABLE precondition_runs ADD COLUMN {name} {kind}")

        # Tonight's own cooling speed. NULL is "the usual one", which is what
        # every row written before it existed meant.
        night = {r["name"] for r in self._db.execute("PRAGMA table_info(tonight)")}
        if "cooling_speed" not in night:
            self._db.execute("ALTER TABLE tonight ADD COLUMN cooling_speed TEXT")

        # The Sleep timing card's Start again. NULL is "count every night", which
        # is what a database from before the button meant.
        prefs = {r["name"] for r in self._db.execute("PRAGMA table_info(preferences)")}
        if "timing_since" not in prefs:
            self._db.execute("ALTER TABLE preferences ADD COLUMN timing_since TEXT")
        # The switch over all of Autopilot. On, which is what every database from
        # before the switch was running.
        if "autopilot_on" not in prefs:
            self._db.execute(
                "ALTER TABLE preferences ADD COLUMN autopilot_on INTEGER NOT NULL DEFAULT 1"
            )
        # Whether a night's suggestion was taken by Autopilot itself rather than
        # by a tap. No on every row from before it could be.
        taken = {r["name"] for r in self._db.execute("PRAGMA table_info(suggestions)")}
        if "auto" not in taken:
            self._db.execute(
                "ALTER TABLE suggestions ADD COLUMN auto INTEGER NOT NULL DEFAULT 0"
            )

        # How closely warm parts are held (hold.py). Balanced, which is the
        # default from the day there was a choice, on every database before it.
        if "hold" not in prefs:
            self._db.execute(
                "ALTER TABLE preferences ADD COLUMN hold TEXT NOT NULL DEFAULT 'balanced'"
            )
        # What electricity costs, for Trends. NULL until it is set.
        if "tariff_p" not in prefs:
            self._db.execute("ALTER TABLE preferences ADD COLUMN tariff_p REAL")

        # Whether a night's note was submitted and put away. Not on any row
        # written before there was a Submit button, which is what nought says.
        notes = {r["name"] for r in self._db.execute("PRAGMA table_info(night_notes)")}
        if "submitted" not in notes:
            self._db.execute(
                "ALTER TABLE night_notes ADD COLUMN submitted INTEGER NOT NULL DEFAULT 0"
            )

        # What each night used. NULL on rows from before, filled in from the
        # plug's readings by Service.record_missing.
        runs = {r["name"] for r in self._db.execute("PRAGMA table_info(night_runs)")}
        if "kwh" not in runs:
            self._db.execute("ALTER TABLE night_runs ADD COLUMN kwh REAL")

        columns = {r["name"] for r in self._db.execute("PRAGMA table_info(schedule)")}
        added = [
            # Empty rather than a real time: a row written before bedtime was a
            # setting has its own night length sitting in the stage durations,
            # and _bed_time_from works it out rather than imposing 22:30.
            ("bed_time", "TEXT NOT NULL DEFAULT ''"),
            ("stages", "TEXT NOT NULL DEFAULT '[]'"),
            ("cooling_speed", "TEXT NOT NULL DEFAULT 'quiet'"),
            # The weekend's own times. None, which is what every night before
            # there were two sets of times ran on.
            ("other_days", "TEXT NOT NULL DEFAULT '[]'"),
            ("other_bed_time", "TEXT NOT NULL DEFAULT ''"),
            ("other_wake_time", "TEXT NOT NULL DEFAULT ''"),
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
                stages=stages_from_json(r["stages"]),
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

    def stored_tonight(self) -> Tonight | None:
        """Whatever row is there, whichever night it is for.

        Only one caller, and it needs it for a circular reason worth spelling
        out: which night we are in is worked out from the saved alarm, and during
        a lie-in that is not the alarm tonight will actually use. So the stored
        row has to be readable *before* the date is known, or a long enough
        lie-in plus a restart loses the night it belongs to.
        """
        return self._tonight_from(self._db.execute(
            "SELECT * FROM tonight WHERE id = 1"
        ).fetchone())

    def load_tonight(self, wake_on: date) -> Tonight | None:
        """Tonight's exceptions, if the stored ones are for tonight.

        A row for any other night is spent and is not handed back, so a Tonight
        expires by the calendar moving rather than by anything remembering to
        clear it. It is left in place: one stale row costs nothing and the next
        write overwrites it.
        """
        row = self._db.execute("SELECT * FROM tonight WHERE id = 1").fetchone()
        if row is None or row["wake_on"] != wake_on.isoformat():
            return None
        return self._tonight_from(row)

    @staticmethod
    def _tonight_from(row: sqlite3.Row | None) -> Tonight | None:
        if row is None:
            return None
        return Tonight(
            wake_on=date.fromisoformat(row["wake_on"]),
            skip=bool(row["skip"]),
            stages=(tuple(saved) if (saved := stages_from_json(row["stages"])) else None),
            wake_time=_time_from(row["wake_time"]) if row["wake_time"] else None,
            bed_time=_time_from(row["bed_time"]) if row["bed_time"] else None,
            nudge_c=row["nudge_c"] or 0,
            nudge_until=_parse(row["nudge_until"]),
            cooling_speed=Mode(row["cooling_speed"]) if row["cooling_speed"] else None,
        )

    def save_tonight(self, tonight: Tonight) -> None:
        self._db.execute(
            """
            INSERT INTO tonight (id, wake_on, skip, stages, wake_time, bed_time,
                                 nudge_c, nudge_until, cooling_speed)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                wake_on=excluded.wake_on, skip=excluded.skip,
                stages=excluded.stages, wake_time=excluded.wake_time,
                bed_time=excluded.bed_time, nudge_c=excluded.nudge_c,
                nudge_until=excluded.nudge_until,
                cooling_speed=excluded.cooling_speed
            """,
            (
                tonight.wake_on.isoformat(),
                int(tonight.skip),
                (
                    json.dumps(
                        [
                            {
                                "stage": x.stage.value,
                                "duration_minutes": x.duration_minutes,
                                "temp_c": x.temp_c,
                            }
                            for x in tonight.stages
                        ]
                    )
                    if tonight.stages is not None
                    else None
                ),
                tonight.wake_time.strftime("%H:%M") if tonight.wake_time else None,
                tonight.bed_time.strftime("%H:%M") if tonight.bed_time else None,
                tonight.nudge_c,
                _iso(tonight.nudge_until),
                tonight.cooling_speed.value if tonight.cooling_speed else None,
            ),
        )
        self._db.commit()

    def clear_tonight(self) -> None:
        self._db.execute("DELETE FROM tonight WHERE id = 1")
        self._db.commit()

    def holiday(self) -> Holiday | None:
        """The holiday, whether or not it is over. The service decides that."""
        row = self._db.execute("SELECT * FROM holiday WHERE id = 1").fetchone()
        if row is None:
            return None
        return Holiday(
            leaves_on=date.fromisoformat(row["leaves_on"]),
            back_on=date.fromisoformat(row["back_on"]),
        )

    def save_holiday(self, holiday: Holiday) -> None:
        self._db.execute(
            "INSERT INTO holiday (id, leaves_on, back_on) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "leaves_on = excluded.leaves_on, back_on = excluded.back_on",
            (holiday.leaves_on.isoformat(), holiday.back_on.isoformat()),
        )
        self._db.commit()

    def clear_holiday(self) -> None:
        self._db.execute("DELETE FROM holiday WHERE id = 1")
        self._db.commit()

    def save_schedule(self, schedule: Schedule) -> None:
        self._db.execute(
            """
            INSERT INTO schedule (id, name, enabled, days_of_week, wake_time,
                                  bed_time, stages, cooling_speed, updated_at,
                                  other_days, other_bed_time, other_wake_time)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, enabled=excluded.enabled,
                days_of_week=excluded.days_of_week, wake_time=excluded.wake_time,
                bed_time=excluded.bed_time,
                stages=excluded.stages, cooling_speed=excluded.cooling_speed,
                updated_at=excluded.updated_at,
                other_days=excluded.other_days,
                other_bed_time=excluded.other_bed_time,
                other_wake_time=excluded.other_wake_time
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
                json.dumps(schedule.other_days),
                schedule.other_bed_time.strftime("%H:%M") if schedule.other_bed_time else "",
                schedule.other_wake_time.strftime("%H:%M") if schedule.other_wake_time else "",
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

    # --- Tonight's getting ready, once it has begun -----------------------------

    def underway(self) -> Underway | None:
        row = self._db.execute(
            "SELECT wake_on, precool_at, mode, lead_minutes, reason FROM underway WHERE id = 1"
        ).fetchone()
        if row is None:
            return None
        return Underway(
            wake_on=date.fromisoformat(row["wake_on"]),
            preconditioning=Preconditioning(
                Mode(row["mode"]) if row["mode"] else None,
                int(row["lead_minutes"]),
                row["reason"],
            ),
            precool_at=datetime.fromisoformat(row["precool_at"]),
        )

    def set_underway(self, underway: Underway | None) -> None:
        with self._db:
            self._db.execute("DELETE FROM underway")
            if underway is None:
                return
            pre = underway.preconditioning
            self._db.execute(
                "INSERT INTO underway (id, wake_on, precool_at, mode, lead_minutes, reason) "
                "VALUES (1, ?, ?, ?, ?, ?)",
                (
                    underway.wake_on.isoformat(),
                    underway.precool_at.isoformat(),
                    pre.mode.value if pre.mode else None,
                    pre.lead_minutes,
                    pre.reason,
                ),
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
        target_c: int | None = None,
    ) -> None:
        self._pending_power.append((at.isoformat(), watts, flow_c, return_c, room_c, target_c))
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
                "(at, watts, flow_c, return_c, room_c, target_c) VALUES (?, ?, ?, ?, ?, ?)",
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

    def night_history(self, since: datetime, until: datetime | None = None) -> list[Sample]:
        """Everything measured since a moment, watts and degrees together.

        The whole night in one query, which is what a chart wants and what
        answering "did the bed actually hold 27C" needs. Until this existed the
        degrees were shown live and then thrown away, so the only question that
        could be asked in the morning was about the machine.
        """
        self.flush_power()
        # `until` for anything describing one night. Without it, "last night"
        # asked in the evening took in the whole day and tonight's pre-heat.
        end = "9999" if until is None else until.isoformat()
        rows = self._db.execute(
            "SELECT at, watts, flow_c, return_c, room_c, target_c FROM power_samples "
            "WHERE at >= ? AND at <= ? ORDER BY at",
            (since.isoformat(), end),
        ).fetchall()
        return [
            Sample(
                datetime.fromisoformat(r["at"]),
                r["watts"],
                r["flow_c"],
                r["return_c"],
                r["room_c"],
                None if r["target_c"] is None else int(r["target_c"]),
            )
            for r in rows
        ]

    def samples_as_written(self, start: datetime, end: datetime) -> list[Sample]:
        """Every sample between two local times, in the order they were taken.

        The order they were taken is not the order of their timestamps for one
        hour a year. Samples carry local time with no zone, so on the night the
        clocks go back 01:00 to 02:00 happens twice, and sorting by the time
        shuffles the two hours together. Rows are written as the samples are
        taken, so the row order is the order they happened in, and it is what
        lets the sleep join tell the first 01:30 from the second.
        """
        self.flush_power()
        rows = self._db.execute(
            "SELECT at, watts, flow_c, return_c, room_c, target_c FROM power_samples "
            "WHERE at >= ? AND at <= ? ORDER BY rowid",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [
            Sample(
                datetime.fromisoformat(r["at"]),
                r["watts"],
                r["flow_c"],
                r["return_c"],
                r["room_c"],
                None if r["target_c"] is None else int(r["target_c"]),
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
        sent_c: float | None = None,
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
            "(at, mode, target_c, seconds, reached, start_c, end_c, room_c, sent_c, decided_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                at.isoformat(),
                mode,
                target_c,
                seconds,
                int(reached),
                start_c,
                end_c,
                room_c,
                sent_c,
                decided_by,
            ),
        )
        self._db.commit()

    def precondition_since(
        self, start: datetime, until: datetime | None = None
    ) -> PreconditionRow | None:
        """The pre-conditioning run for one night, or None if it never ran.

        The newest row after `start` used to be the answer, and by the next
        evening the newest row is tonight's, so last night was described as
        having got ready the way tonight is getting ready. `until` bounds it.
        """
        rows = [
            r for r in self.precondition_runs(5)
            if r.at >= start and (until is None or r.at <= until)
        ]
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
            "WHERE mode = ? AND reached = 1 AND counts = 1 AND ABS(target_c - ?) <= ? "
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

    def learned_offset_c(
        self, mode: str, target_c: int, *, within_c: int = 3
    ) -> float | None:
        """How far this bed usually ends up from what the unit was asked for.

        Measured on 12 September, from runs the probes decided, with nobody in
        the bed: warming to 28C in a 20.6C room settled at 25.9, and turbo to 27C
        in a 20.5C room settled at 26.6. Two degrees out one way and less than
        half the other, at almost the same target in almost the same room.

        That is not the unit being wrong. The unit heats water at its own outlet
        and the probes sit on the hose at the bed, so heat leaks in between. What
        differs between the two is how hard each mode circulates: turbo drives it
        and holds the bed near the setpoint, warming is gentler and lets it sag.
        So the correction is learned per mode, and per target, because the loss
        also grows with how far the water is from the room.

        A **mean** here, deliberately, where the lead time learns a rate. There
        the distances varied sixteenfold, so a duration meant nothing without
        one. Here every target sits in the narrow band somebody sleeps in and the
        room barely moves, so the gap is near enough constant across the runs
        being averaged, and dividing by it would only amplify the noise.

        None until there are enough runs to be worth trusting. One night is an
        anecdote, and no correction is better than a confident wrong one.

        **Measured against what was sent, not against what was asked for.** Those
        are the same number only until this method first returns something. After
        that the app asks for 28 and sends 30, and scoring the night against the
        28 would read a working correction as no droop at all: the mean decays,
        the correction shrinks, the bed goes cold, and it earns the correction
        back. A slow oscillation with nothing in the log to explain it. Matching
        still goes on `target_c`, because that is what the caller is asking about;
        only the arithmetic uses `sent_c`. Rows written before the column existed
        fall back to `target_c`, which is what they actually sent.
        """
        rows = self._db.execute(
            "SELECT COALESCE(sent_c, target_c) AS asked, end_c FROM precondition_runs "
            "WHERE mode = ? AND reached = 1 AND counts = 1 AND ABS(target_c - ?) <= ? "
            "AND end_c IS NOT NULL ORDER BY id DESC LIMIT 10",
            (mode, target_c, within_c),
        ).fetchall()
        if len(rows) < MIN_RUNS_TO_LEARN:
            return None
        offsets = [r["end_c"] - r["asked"] for r in rows]
        return round(sum(offsets) / len(offsets), 1)

    def learning_progress(self) -> list[dict[str, object]]:
        """How close each mode is to being measured rather than estimated.

        Two things are learned from the same runs and they qualify differently, so
        they are counted apart: how fast this bed moves needs a run that actually
        travelled, and where it settles needs any finished run near the target.

        Grouped by mode rather than by target. The windows overlap, because a run
        at 27C teaches the app about 28C too, so listing every target separately
        would show the same three runs under three headings and make it look like
        nine. Each mode reports against its most recent target, which is the one
        tonight will use unless the schedule has changed.

        Only runs the probes decided appear here. A run the plug timed says the
        machine stopped working; it never says where the bed ended up, so it
        teaches neither of these.
        """
        # Which modes to list, and this one deliberately does not filter `counts`.
        # Start again sets a mode back to nought; it should not make the mode
        # vanish off the card. A section that disappears reads as the app having
        # deleted the nights, which is the one thing it promises it did not do.
        modes = self._db.execute(
            "SELECT mode, MAX(id) AS newest FROM precondition_runs "
            "WHERE reached = 1 AND end_c IS NOT NULL GROUP BY mode "
            # Most recently used first, which is the mode last night ran in and
            # so the one anybody reading this in the morning came here about.
            "ORDER BY newest DESC"
        ).fetchall()

        out: list[dict[str, object]] = []
        for row in modes:
            latest = self._db.execute(
                "SELECT target_c FROM precondition_runs WHERE id = ?", (row["newest"],)
            ).fetchone()
            out.append(self.learning_for(row["mode"], latest["target_c"]))
        return out

    def learning_for(self, mode: str, target_c: int, *, within_c: int = 3) -> dict[str, object]:
        """One mode's progress, and what it has measured if it is there yet."""
        rows = self._db.execute(
            "SELECT seconds, start_c, end_c FROM precondition_runs "
            "WHERE mode = ? AND reached = 1 AND counts = 1 AND ABS(target_c - ?) <= ? "
            "AND end_c IS NOT NULL ORDER BY id DESC LIMIT 10",
            (mode, target_c, within_c),
        ).fetchall()
        travelled = [
            r
            for r in rows
            if r["start_c"] is not None
            and abs(r["end_c"] - r["start_c"]) >= MIN_LEARNABLE_GAP_C
        ]
        return {
            "mode": mode,
            "target_c": target_c,
            "needed": MIN_RUNS_TO_LEARN,
            "pace_runs": len(travelled),
            "settle_runs": len(rows),
            # The measured figures, or None while it is still estimating. Asked
            # through the same methods the service uses, so the card can never
            # claim something the unit is not actually being sent.
            "pace_minutes": self.learned_lead_minutes(mode, target_c, 10.0),
            "settle_c": self.learned_offset_c(mode, target_c),
        }

    def learning_on(self) -> bool:
        row = self._db.execute("SELECT learning_on FROM preferences WHERE id = 1").fetchone()
        return True if row is None else bool(row["learning_on"])

    def autopilot_on(self) -> bool:
        row = self._db.execute("SELECT autopilot_on FROM preferences WHERE id = 1").fetchone()
        return True if row is None else bool(row["autopilot_on"])

    def set_autopilot_on(self, on: bool) -> None:
        self._db.execute(
            "INSERT INTO preferences (id, autopilot_on) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET autopilot_on = excluded.autopilot_on",
            (int(on),),
        )
        self._db.commit()

    def hold(self) -> str:
        row = self._db.execute("SELECT hold FROM preferences WHERE id = 1").fetchone()
        return "balanced" if row is None else row["hold"]

    def set_hold(self, hold: str) -> None:
        self._db.execute(
            "INSERT INTO preferences (id, hold) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET hold = excluded.hold",
            (hold,),
        )
        self._db.commit()

    def timing_since(self) -> str | None:
        """The last morning the Sleep timing card no longer counts, if it was reset."""
        row = self._db.execute("SELECT timing_since FROM preferences WHERE id = 1").fetchone()
        return None if row is None else row["timing_since"]

    def set_timing_since(self, wake_on: str | None) -> None:
        self._db.execute(
            "INSERT INTO preferences (id, timing_since) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET timing_since = excluded.timing_since",
            (wake_on,),
        )
        self._db.commit()

    def set_learning_on(self, on: bool) -> None:
        self._db.execute(
            "INSERT INTO preferences (id, learning_on) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET learning_on = excluded.learning_on",
            (int(on),),
        )
        self._db.commit()

    def forget_learning(self, mode: str | None = None) -> int:
        """Start again, for one mode or for all of them.

        The rows stay. `counts` goes to zero, so they stop teaching and remain a
        record of what happened on those nights. A night is not the app's to
        delete because somebody disliked the conclusion it drew from it, and the
        Autopilot chart reads the same table.
        """
        if mode is None:
            cursor = self._db.execute("UPDATE precondition_runs SET counts = 0 WHERE counts = 1")
        else:
            cursor = self._db.execute(
                "UPDATE precondition_runs SET counts = 0 WHERE counts = 1 AND mode = ?", (mode,)
            )
        self._db.commit()
        return cursor.rowcount

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

    # --- Withings -----------------------------------------------------------------

    def withings_account(self) -> WithingsAccount | None:
        row = self._db.execute("SELECT * FROM withings_account WHERE id = 1").fetchone()
        if row is None:
            return None
        return WithingsAccount(
            user_id=row["user_id"],
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            expires_at=row["expires_at"],
            scope=row["scope"],
            connected_at=row["connected_at"],
            last_update=row["last_update"],
            needs_reconnect=bool(row["needs_reconnect"]),
        )

    def save_withings_tokens(
        self,
        *,
        access_token: str,
        refresh_token: str,
        expires_at: int,
        scope: str | None,
        user_id: str | None,
        now: int,
    ) -> None:
        """The one write in this database that has to survive a power cut.

        Everything else here runs with synchronous=NORMAL, which in WAL mode can
        lose the last few commits if the power goes, and for a power sample or an
        event that is a fair price. For these it is not. Withings replaces the
        refresh token every time it is used and the old one stops working eight
        hours later, so a Pi that lost the new pair and stayed off longer than that
        would come back disconnected for good. So this one commit waits for the
        disk before it returns.

        Called before the new access token is used, never after. A token that has
        been used and not kept is exactly the failure this exists to prevent.

        A saved pair means the connection works, so it clears needs_reconnect.
        """
        # Anything pending belongs to somebody else and should not ride along,
        # and the setting only changes outside a transaction.
        self._db.commit()
        self._db.execute("PRAGMA synchronous=FULL")
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO withings_account (id, user_id, access_token, refresh_token, "
                    "expires_at, scope, connected_at, needs_reconnect) "
                    "VALUES (1, ?, ?, ?, ?, ?, ?, 0) "
                    "ON CONFLICT(id) DO UPDATE SET user_id = excluded.user_id, "
                    "access_token = excluded.access_token, "
                    "refresh_token = excluded.refresh_token, "
                    "expires_at = excluded.expires_at, scope = excluded.scope, "
                    "needs_reconnect = 0",
                    (user_id, access_token, refresh_token, expires_at, scope, now),
                )
        finally:
            self._db.execute("PRAGMA synchronous=NORMAL")

    def set_withings_last_update(self, at: int) -> None:
        self._db.execute("UPDATE withings_account SET last_update = ? WHERE id = 1", (at,))
        self._db.commit()

    def set_withings_needs_reconnect(self, needs: bool) -> None:
        self._db.execute(
            "UPDATE withings_account SET needs_reconnect = ? WHERE id = 1", (int(needs),)
        )
        self._db.commit()

    def forget_withings(self) -> None:
        """Disconnect. The tokens go; the nights stay, because they happened."""
        self._db.execute("DELETE FROM withings_account WHERE id = 1")
        self._db.commit()

    def save_sleep_night(self, night: Night) -> None:
        """One night, replacing whatever was stored for it, in one transaction.

        Replaced whole rather than merged, because a night grows and a merge
        would keep whatever the smaller version said that the bigger one no
        longer does.

        Matched on its start as well as its id. That the id stays the same while
        a night grows is assumed, not proven, and a night stored twice would be
        counted twice in every average on the Health Report.
        """
        with self._db:
            stale = [
                r["id"]
                for r in self._db.execute(
                    "SELECT id FROM sleep_nights WHERE id = ? OR start_at = ?",
                    (night.id, night.start_at),
                )
            ]
            for old in stale:
                self._db.execute("DELETE FROM sleep_minutes WHERE night_id = ?", (old,))
                self._db.execute("DELETE FROM sleep_stages WHERE night_id = ?", (old,))
                self._db.execute("DELETE FROM sleep_nights WHERE id = ?", (old,))
            self._db.execute(
                "INSERT INTO sleep_nights (id, wake_on, start_at, end_at, timezone, modified, "
                "completed, data, events) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    night.id,
                    night.wake_on,
                    night.start_at,
                    night.end_at,
                    night.timezone,
                    night.modified,
                    None if night.completed is None else int(night.completed),
                    json.dumps(night.data),
                    None if night.events is None else json.dumps(night.events),
                ),
            )
            self._db.executemany(
                "INSERT INTO sleep_stages (night_id, start_at, end_at, state) VALUES (?, ?, ?, ?)",
                [(night.id, s.start_at, s.end_at, s.state) for s in night.stages],
            )
            # OR REPLACE because two nights cannot share a minute. If Withings
            # ever sent two that overlapped, the newer one wins that minute rather
            # than the whole save failing and losing both.
            self._db.executemany(
                "INSERT OR REPLACE INTO sleep_minutes (at, night_id, state, hr, rr, sdnn_1, "
                "rmssd, hrv_quality, mvt_score, snoring) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (m.at, night.id, m.state, m.hr, m.rr, m.sdnn_1, m.rmssd, m.hrv_quality,
                     m.mvt_score, m.snoring)
                    for m in night.minutes
                ],
            )

    def sleep_night_modified(self, night_id: int) -> int | None:
        """When the stored copy of a night was last changed by Withings, if held."""
        row = self._db.execute(
            "SELECT modified FROM sleep_nights WHERE id = ?", (night_id,)
        ).fetchone()
        return None if row is None else row["modified"]

    def sleep_nights(
        self, first_wake_on: str | None = None, last_wake_on: str | None = None
    ) -> list[StoredNight]:
        """Nights, oldest first, optionally between two mornings inclusive."""
        rows = self._db.execute(
            "SELECT * FROM sleep_nights WHERE wake_on >= ? AND wake_on <= ? ORDER BY start_at",
            (first_wake_on or "", last_wake_on or "9999"),
        ).fetchall()
        return [_stored_night(r) for r in rows]

    def sleep_night_starting(self, start_at: int) -> StoredNight | None:
        """The night held for this start, under whatever id it was stored with."""
        row = self._db.execute(
            "SELECT * FROM sleep_nights WHERE start_at = ? LIMIT 1", (start_at,)
        ).fetchone()
        return None if row is None else _stored_night(row)

    def sleep_night_on(self, wake_on: str) -> StoredNight | None:
        """The night that ended on this morning.

        The longest, if there is more than one. A nap may yet turn up as a night of
        its own, and "the night of the 23rd" means the one I slept, not the one I
        dozed through on the sofa.
        """
        row = self._db.execute(
            "SELECT * FROM sleep_nights WHERE wake_on = ? ORDER BY end_at - start_at DESC LIMIT 1",
            (wake_on,),
        ).fetchone()
        return None if row is None else _stored_night(row)

    def earliest_sleep_wake_on(self) -> str | None:
        row = self._db.execute("SELECT MIN(wake_on) AS first FROM sleep_nights").fetchone()
        return row["first"]

    def latest_sleep_night(self) -> StoredNight | None:
        row = self._db.execute(
            "SELECT * FROM sleep_nights ORDER BY start_at DESC LIMIT 1"
        ).fetchone()
        return None if row is None else _stored_night(row)

    def sleep_stages(self, night_id: int) -> list[SleepStateRun]:
        rows = self._db.execute(
            "SELECT start_at, end_at, state FROM sleep_stages WHERE night_id = ? ORDER BY start_at",
            (night_id,),
        ).fetchall()
        return [SleepStateRun(r["start_at"], r["end_at"], r["state"]) for r in rows]

    def sleep_vitals(self, night_ids: list[int]) -> dict[int, tuple[float | None, float | None]]:
        """Each night's average heart-rate variability (RMSSD) and breathing rate,
        over the minutes spent asleep.

        Asleep only, the way Withings works out its own heart rate figures. AVG
        passes over NULL, which is where a zero variability reading went, so no
        "could not measure" drags an average down.
        """
        if not night_ids:
            return {}
        marks = ",".join("?" * len(night_ids))
        rows = self._db.execute(
            "SELECT night_id, AVG(rmssd) AS hrv, AVG(rr) AS rr FROM sleep_minutes "
            f"WHERE state != 0 AND night_id IN ({marks}) GROUP BY night_id",
            night_ids,
        ).fetchall()
        return {r["night_id"]: (r["hrv"], r["rr"]) for r in rows}

    # --- What each night ran ------------------------------------------------

    def save_night_run(self, run: NightRun, written_at: datetime) -> None:
        """Keep what a night ran, without ever making the record worse.

        A night worked out afterwards never replaces one written on the morning
        itself, which knew exactly where each part started and ended. And a test
        already marked on the night is kept when the rest of the row is written
        again: the evening knew it was a test, the morning does not.
        """
        parts = json.dumps([
            {
                "part": p.part,
                "starts_at": p.starts_at.isoformat(),
                "ends_at": p.ends_at.isoformat(),
                "set_c": p.set_c,
                "held": p.held,
                "bed_c": p.bed_c,
                "by_hand": p.by_hand,
            }
            for p in run.parts
        ])
        self._db.execute(
            "INSERT INTO night_runs (wake_on, bedtime_at, wake_at, parts, room_c, test_part, "
            "test_offset_c, rebuilt, written_at, kwh) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(wake_on) DO UPDATE SET "
            "bedtime_at = excluded.bedtime_at, wake_at = excluded.wake_at, "
            "parts = excluded.parts, room_c = excluded.room_c, "
            "test_part = COALESCE(excluded.test_part, night_runs.test_part), "
            "test_offset_c = COALESCE(excluded.test_offset_c, night_runs.test_offset_c), "
            "rebuilt = excluded.rebuilt, written_at = excluded.written_at, "
            "kwh = COALESCE(excluded.kwh, night_runs.kwh) "
            "WHERE excluded.rebuilt = 0 OR night_runs.rebuilt = 1",
            (
                run.wake_on,
                run.bedtime_at.isoformat(),
                run.wake_at.isoformat(),
                parts,
                run.room_c,
                run.test_part,
                run.test_offset_c,
                int(run.rebuilt),
                written_at.isoformat(),
                run.kwh,
            ),
        )
        self._db.commit()

    def set_night_kwh(self, wake_on: str, kwh: float) -> None:
        """Fill in what an earlier night used, on a row written before it was kept."""
        self._db.execute("UPDATE night_runs SET kwh = ? WHERE wake_on = ?", (kwh, wake_on))
        self._db.commit()

    def tariff_p(self) -> float | None:
        """What a kWh costs, in pence, or None until it has been set."""
        row = self._db.execute("SELECT tariff_p FROM preferences WHERE id = 1").fetchone()
        return None if row is None else row["tariff_p"]

    def set_tariff_p(self, pence: float | None) -> None:
        self._db.execute(
            "INSERT INTO preferences (id, tariff_p) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET tariff_p = excluded.tariff_p",
            (pence,),
        )
        self._db.commit()

    def night_runs(
        self, first_wake_on: str | None = None, last_wake_on: str | None = None
    ) -> list[NightRun]:
        """What each night ran, oldest first, optionally between two mornings."""
        rows = self._db.execute(
            "SELECT * FROM night_runs WHERE wake_on >= ? AND wake_on <= ? ORDER BY wake_on",
            (first_wake_on or "", last_wake_on or "9999"),
        ).fetchall()
        return [_night_run(r) for r in rows]

    # --- The evening suggestion ----------------------------------------------

    def save_decision(self, decided: Decided, at: datetime) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO suggestions (wake_on, decision, deep_c, rem_c, test_part, "
            "test_offset_c, decided_at, auto) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decided.wake_on,
                decided.decision,
                decided.temps.get("deep"),
                decided.temps.get("rem"),
                decided.test_part,
                decided.test_offset_c,
                at.isoformat(),
                int(decided.auto),
            ),
        )
        self._db.commit()

    def forget_decision(self, wake_on: str) -> None:
        self._db.execute("DELETE FROM suggestions WHERE wake_on = ?", (wake_on,))
        self._db.commit()

    def decision_for(self, wake_on: str) -> Decided | None:
        row = self._db.execute(
            "SELECT * FROM suggestions WHERE wake_on = ?", (wake_on,)
        ).fetchone()
        if row is None:
            return None
        return Decided(
            wake_on=row["wake_on"],
            decision=row["decision"],
            temps={k: row[f"{k}_c"] for k in ("deep", "rem") if row[f"{k}_c"] is not None},
            test_part=row["test_part"],
            test_offset_c=row["test_offset_c"],
            auto=bool(row["auto"]),
        )

    def suggest_limits(self) -> dict[str, tuple[int, int]]:
        """Each part's (centre_c, reach), for the parts that have limits set."""
        rows = self._db.execute("SELECT part, centre_c, reach FROM suggest_limits").fetchall()
        return {r["part"]: (r["centre_c"], r["reach"]) for r in rows}

    def set_suggest_limit(self, part: str, centre_c: int, reach: int) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO suggest_limits (part, centre_c, reach) VALUES (?, ?, ?)",
            (part, centre_c, reach),
        )
        self._db.commit()

    def sleep_minutes(self, night_id: int) -> list[Minute]:
        rows = self._db.execute(
            "SELECT * FROM sleep_minutes WHERE night_id = ? ORDER BY at", (night_id,)
        ).fetchall()
        return [
            Minute(
                r["at"], r["state"], r["hr"], r["rr"], r["sdnn_1"], r["rmssd"],
                r["hrv_quality"], r["mvt_score"], r["snoring"],
            )
            for r in rows
        ]

    # --- How nights felt (notes.py) ------------------------------------------------

    def night_note(self, wake_on: str) -> NightNote | None:
        row = self._db.execute(
            "SELECT * FROM night_notes WHERE wake_on = ?", (wake_on,)
        ).fetchone()
        return _night_note(row) if row is not None else None

    def night_notes(self, first: str, last: str) -> dict[str, NightNote]:
        rows = self._db.execute(
            "SELECT * FROM night_notes WHERE wake_on BETWEEN ? AND ?", (first, last)
        ).fetchall()
        return {r["wake_on"]: _night_note(r) for r in rows}

    def save_night_note(self, note: NightNote, at: datetime) -> None:
        self._db.execute(
            "INSERT INTO night_notes (wake_on, rating, felt, tags, updated_at, submitted) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(wake_on) DO UPDATE SET rating = excluded.rating, "
            "felt = excluded.felt, tags = excluded.tags, updated_at = excluded.updated_at, "
            "submitted = excluded.submitted",
            (
                note.wake_on,
                note.rating,
                note.felt,
                json.dumps(list(note.tags)),
                at.isoformat(),
                int(note.submitted),
            ),
        )
        self._db.commit()

    # --- Signed-in devices (access.py) ---------------------------------------------

    def add_session(self, session: StoredSession) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO sessions (token_hash, password, created_at, seen_at, via, label) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                session.token_hash,
                session.password,
                session.created_at.isoformat(),
                session.seen_at.isoformat(),
                session.via,
                session.label,
            ),
        )
        self._db.commit()

    def session(self, token_hash: str) -> StoredSession | None:
        row = self._db.execute(
            "SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None:
            return None
        return StoredSession(
            token_hash=row["token_hash"],
            password=row["password"],
            created_at=datetime.fromisoformat(row["created_at"]),
            seen_at=datetime.fromisoformat(row["seen_at"]),
            via=row["via"],
            label=row["label"],
        )

    def session_seen(self, token_hash: str, at: datetime) -> None:
        self._db.execute(
            "UPDATE sessions SET seen_at = ? WHERE token_hash = ?", (at.isoformat(), token_hash)
        )
        self._db.commit()

    def end_session(self, token_hash: str) -> None:
        self._db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        self._db.commit()

    def end_every_session(self) -> int:
        count = self._db.execute("DELETE FROM sessions").rowcount
        self._db.commit()
        return count

    def session_count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


def _night_note(row: sqlite3.Row) -> NightNote:
    return NightNote(
        wake_on=row["wake_on"],
        rating=row["rating"],
        felt=row["felt"],
        tags=tuple(json.loads(row["tags"] or "[]")),
        submitted=bool(row["submitted"]),
    )


def _night_run(row: sqlite3.Row) -> NightRun:
    return NightRun(
        wake_on=row["wake_on"],
        bedtime_at=datetime.fromisoformat(row["bedtime_at"]),
        wake_at=datetime.fromisoformat(row["wake_at"]),
        parts=tuple(
            PartRun(
                part=p["part"],
                starts_at=datetime.fromisoformat(p["starts_at"]),
                ends_at=datetime.fromisoformat(p["ends_at"]),
                set_c=p["set_c"],
                held=p["held"],
                bed_c=p["bed_c"],
                by_hand=bool(p["by_hand"]),
            )
            for p in json.loads(row["parts"])
        ),
        room_c=row["room_c"],
        test_part=row["test_part"],
        test_offset_c=row["test_offset_c"],
        rebuilt=bool(row["rebuilt"]),
        kwh=row["kwh"],
    )


def _stored_night(row: sqlite3.Row) -> StoredNight:
    return StoredNight(
        id=row["id"],
        wake_on=row["wake_on"],
        start_at=row["start_at"],
        end_at=row["end_at"],
        timezone=row["timezone"],
        modified=row["modified"],
        completed=None if row["completed"] is None else bool(row["completed"]),
        data=json.loads(row["data"]),
        events=None if row["events"] is None else json.loads(row["events"]),
    )


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
        other_days=json.loads(row["other_days"]) if row["other_days"] else [],
        other_bed_time=_time_from(row["other_bed_time"]) if row["other_bed_time"] else None,
        other_wake_time=_time_from(row["other_wake_time"]) if row["other_wake_time"] else None,
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


def stages_from_json(raw: str | None) -> list[SleepStage]:
    """Stored stages, brought up to whatever the night is made of now.

    The one place stage JSON is read. Schedules, saved profiles and tonight-only
    overrides are all stored the same way and all predate Drift, so they all need
    the same filling in, and three copies of that would be three chances to
    disagree about what an old night turns into.
    """
    parsed = json.loads(raw) if raw else []
    # Empty in, empty out. A column defaulted to '[]' by the migration is a row
    # that never had stages, not a row whose night was four empty ones, and only
    # the caller knows what to fall back to. Checking the string rather than what
    # came out of it is how this went wrong once already: '[]' is truthy.
    if not parsed:
        return []
    return with_all_stages(
        [SleepStage(Stage(s["stage"]), s["duration_minutes"], s["temp_c"]) for s in parsed]
    )


def _stages_from(row: sqlite3.Row) -> list[SleepStage]:
    """The night, in order.

    Before the app drove the night itself the schedule was the unit's own three
    phases: 4h, 4h and 30m, fixed, one temperature each. Those are Deep, REM and
    Wake with the durations spelled out, so an upgraded database keeps the three
    temperatures rather than quietly resetting to the defaults.

    Named here rather than taken from STAGE_ORDER, which has since grown a fourth
    stage the hardware never had. Zipping a three-phase row against it would have
    quietly slid every temperature one stage along.
    """
    if stored := stages_from_json(row["stages"]):
        return stored
    # sqlite3.Row iterates its values, not its names, so membership goes via keys().
    if "phase1_temp_c" in set(row.keys()):
        temps = (row["phase1_temp_c"], row["phase2_temp_c"], row["phase3_temp_c"])
        return with_all_stages(
            [
                SleepStage(stage, minutes, temp)
                for stage, minutes, temp in zip(
                    LEGACY_PHASES, LEGACY_PHASE_MINUTES, temps, strict=True
                )
            ]
        )
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
