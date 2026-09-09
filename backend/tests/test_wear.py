"""Writing less to the card, without lying about what was written.

The SD card is the likeliest single thing to end this project, and the power
samples are far and away its heaviest writer: one row every thirty seconds, each
its own committed transaction, each fsync turned by the card into an erase of a
block thousands of times larger than the row.

Measured before and after, a day of sampling went from 11,544 fdatasync calls to
8. The whole risk in that is one thing, and it gets most of the tests here: a
sample held in hand must never be a sample that reads back missing.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from hydrosnooze.db import POWER_BATCH, Database

START = datetime(2026, 9, 9, 22, 0)


@pytest.fixture
def db(tmp_path):
    made = Database(str(tmp_path / "hydrosnooze.db"))
    yield made
    made.close()


def rows(db: Database) -> int:
    """What is actually on the card, going behind the batching to look."""
    return db._db.execute("SELECT COUNT(*) FROM power_samples").fetchone()[0]


def sample(db: Database, n: int) -> None:
    for i in range(n):
        db.add_power_sample(START + timedelta(seconds=30 * i), 166.0 + i)


# --- Writing less -------------------------------------------------------------


def test_the_card_is_left_alone_between_batches(db):
    sample(db, POWER_BATCH - 1)
    assert rows(db) == 0


def test_a_full_batch_goes_down_in_one_transaction(db):
    sample(db, POWER_BATCH)
    assert rows(db) == POWER_BATCH


def test_a_day_of_sampling_is_a_day_of_batches_not_a_day_of_commits(db):
    a_day = 2 * 60 * 24
    sample(db, a_day)
    assert rows(db) == a_day - (a_day % POWER_BATCH)


def test_it_uses_a_journal_that_does_not_fsync_every_commit(db):
    assert db._db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db._db.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL


def test_an_in_memory_database_needs_no_special_case():
    """The tests and the simulator run on one of these, and a pragma that failed
    rather than reporting what it kept would break every one of them."""
    mem = Database(":memory:")
    assert mem._db.execute("PRAGMA journal_mode").fetchone()[0] == "memory"
    mem.close()


# --- Without lying about it ---------------------------------------------------


def test_a_held_sample_is_unwritten_but_never_invisible(db):
    """The one that matters. The pre-conditioning learning reads this back to
    work out how long the bed really takes, and a read that quietly missed the
    last nine minutes would teach it the wrong number."""
    sample(db, 5)
    assert rows(db) == 0
    assert len(db.power_history(START)) == 5


def test_reading_settles_what_was_in_hand(db):
    sample(db, 5)
    db.power_history(START)
    assert rows(db) == 5


def test_the_values_survive_the_round_trip(db):
    sample(db, 3)
    assert [w for _, w in db.power_history(START)] == [166.0, 167.0, 168.0]


def test_pruning_does_not_prune_what_has_not_landed_yet(db):
    """Prune reads the table too, in its way. Deleting before the buffer was
    written would leave samples older than the cutoff arriving afterwards."""
    sample(db, 5)
    db.prune_power(START - timedelta(days=7))
    assert rows(db) == 5


def test_closing_writes_what_is_left(tmp_path):
    """A clean shutdown loses nothing. systemd stops the service before the Pi
    reboots, so a planned restart is not a hole in the chart."""
    path = str(tmp_path / "hydrosnooze.db")
    first = Database(path)
    for i in range(3):
        first.add_power_sample(START + timedelta(seconds=30 * i), 166.0)
    first.close()

    again = Database(path)
    assert len(again.power_history(START)) == 3
    again.close()


def test_what_an_unclean_stop_actually_costs(tmp_path):
    """Stated rather than glossed over. A kill -9 loses whatever is in hand, up
    to ten minutes of chart, and nothing else: no schedule, no events already
    written, no corrupt file."""
    path = str(tmp_path / "hydrosnooze.db")
    first = Database(path)
    sample(first, POWER_BATCH + 5)
    # No close. The process is simply gone.
    del first

    again = Database(path)
    assert len(again.power_history(START)) == POWER_BATCH
    assert again.load_schedule() is not None
    again.close()
