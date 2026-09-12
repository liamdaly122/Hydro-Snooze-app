"""The unlock card, and the two controls beside it.

Two things are learned from the same nights and they qualify apart. How fast this
bed moves needs a night that actually travelled; where it settles needs any
finished night near the target. The card counts them separately because they
arrive separately, and a single "3 of 3" covering both would claim the pre-heat
was timed on a week where nothing ever moved more than half a degree.

The switch and the reset are here for one reason: the temperature correction is
the only learned thing in this project that changes what the bed does. Everything
else it measures ends up on a screen. So there has to be a way out of it that
does not involve SSH, and a way to say "that conclusion is wrong, start again"
that does not throw away the record of the nights it was drawn from.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient

from hydrosnooze.api.schemas import learning_json
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.db import MIN_RUNS_TO_LEARN, Database
from hydrosnooze.main import app as real_app
from hydrosnooze.models import Mode, Schedule, SleepStage, Stage
from hydrosnooze.service import Service

NOW = datetime(2026, 9, 12, 20, 0)


@pytest.fixture
def db():
    return Database(":memory:")


def ran(db, *, mode="warming", target=28, frm=19.5, to=25.9, minutes=55, reached=True):
    db.record_precondition(
        NOW, mode, target, minutes * 60, reached, start_c=frm, end_c=to, decided_by="probes"
    )


# --- Counting the two halves apart -----------------------------------------------


def test_a_mode_with_nothing_recorded_counts_from_nought(db):
    row = db.learning_for("warming", 28)
    assert (row["pace_runs"], row["settle_runs"]) == (0, 0)
    assert row["pace_minutes"] is None and row["settle_c"] is None
    assert row["needed"] == MIN_RUNS_TO_LEARN


def test_nights_that_barely_moved_count_for_where_it_settles_and_not_for_pace(db):
    """The distinction the 11 September bug was made of.

    A bed already at 27.6 going to 28.1 says nothing about how fast this bed
    warms, and it says exactly as much as any other night about where it ends up
    relative to the dial. Counting those nights towards the pre-heat is what sent
    it out at 21:58 for a 22:00 bedtime.
    """
    for _ in range(MIN_RUNS_TO_LEARN + 1):
        ran(db, frm=27.6, to=28.1, minutes=2)

    row = db.learning_for("warming", 28)
    assert row["pace_runs"] == 0, "nothing travelled far enough to time"
    assert row["settle_runs"] == MIN_RUNS_TO_LEARN + 1
    assert row["pace_minutes"] is None
    assert row["settle_c"] == 0.1


def test_progress_is_one_row_a_mode_and_not_one_a_target(db):
    """The windows overlap by three degrees either way, so a night at 27 teaches
    the app about 28 as well. Listing every target would show the same three
    nights under three headings and make three look like nine."""
    for target, end in ((27, 24.9), (28, 25.9), (29, 26.9)):
        ran(db, target=target, to=end)

    rows = db.learning_progress()
    assert [r["mode"] for r in rows] == ["warming"]
    assert rows[0]["target_c"] == 29, "the latest, which is the one tonight will use"
    assert rows[0]["settle_runs"] == 3


def test_a_night_the_plug_timed_teaches_neither_half(db):
    """It says the machine stopped working. It never says where the bed got to."""
    for _ in range(MIN_RUNS_TO_LEARN + 1):
        db.record_precondition(NOW, "warming", 28, 3300, True, decided_by="plug")

    assert db.learning_progress() == []
    row = db.learning_for("warming", 28)
    assert (row["pace_runs"], row["settle_runs"]) == (0, 0)


# --- Starting again ---------------------------------------------------------------


def test_starting_again_keeps_the_nights_and_stops_them_teaching(db):
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(db)
    assert db.learned_offset_c("warming", 28) == -2.1

    assert db.forget_learning() == MIN_RUNS_TO_LEARN
    assert db.learned_offset_c("warming", 28) is None
    assert db.learned_lead_minutes("warming", 28, 10.0) is None
    assert db.learning_for("warming", 28)["settle_runs"] == 0
    assert len(db.precondition_runs()) == MIN_RUNS_TO_LEARN, "the nights themselves are kept"


def test_the_mode_used_most_recently_is_listed_first(db):
    """Which is the one last night ran in, and so the one somebody reading this
    over breakfast came here about."""
    ran(db, mode="turbo", target=24, frm=27.0, to=23.6)
    ran(db)
    assert [r["mode"] for r in db.learning_progress()] == ["warming", "turbo"]


def test_starting_again_on_one_mode_leaves_the_other_alone(db):
    """The common case. The pre-heat got warming wrong and cooling has nothing to
    do with it."""
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(db)
        ran(db, mode="turbo", target=24, frm=27.0, to=23.6)

    assert db.forget_learning("warming") == MIN_RUNS_TO_LEARN
    assert db.learned_offset_c("warming", 28) is None
    assert db.learned_offset_c("turbo", 24) == -0.4


def test_a_mode_set_aside_stays_on_the_card_at_nought(db):
    """Start again is a reset, not a delete, and the card has to look like one.

    A whole section disappearing reads as the app having thrown the nights away,
    which is exactly what the button promises it does not do.
    """
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(db)
    db.forget_learning()

    rows = db.learning_progress()
    assert [r["mode"] for r in rows] == ["warming"]
    assert (rows[0]["pace_runs"], rows[0]["settle_runs"]) == (0, 0)


def test_starting_again_twice_does_not_count_the_same_nights_again(db):
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(db)
    assert db.forget_learning() == MIN_RUNS_TO_LEARN
    assert db.forget_learning() == 0


def test_nights_set_aside_come_back_no_further_forward(db):
    """Start again means start again. Three nights set aside and one new one is
    one night of evidence, not four."""
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(db)
    db.forget_learning()
    ran(db)

    row = db.learning_for("warming", 28)
    assert row["settle_runs"] == 1
    assert row["settle_c"] is None


# --- The switch -------------------------------------------------------------------


def test_learning_is_on_before_anybody_touches_it(db):
    assert db.learning_on() is True


def test_the_switch_is_remembered(db):
    db.set_learning_on(False)
    assert db.learning_on() is False
    db.set_learning_on(True)
    assert db.learning_on() is True


# --- What the service does with the switch ----------------------------------------


@pytest.fixture
def service():
    return Service(Settings(db_path=":memory:"), clock=VirtualClock(NOW), echo=False)


def measured(service):
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(service.db)


def test_switched_on_the_correction_changes_what_is_sent(service):
    measured(service)
    assert service._corrected(28, Mode.WARMING) == 30


def test_switched_off_the_number_you_asked_for_is_the_number_sent(service):
    """The point of having a switch at all. Off is not "learn a bit less"; it is
    the behaviour this app had before any of it existed."""
    measured(service)
    service.set_learning(False)

    assert service._corrected(28, Mode.WARMING) == 28
    assert service._learned_lead(Mode.WARMING, 28, 10.0) is None


def test_switching_off_measures_nothing_away(service):
    """Off stops it being used. It does not stop it being true, and switching
    back on must not cost three more nights."""
    measured(service)
    service.set_learning(False)
    service.set_learning(True)
    assert service._corrected(28, Mode.WARMING) == 30


def test_the_card_reports_what_is_actually_being_sent(service):
    """Not the drift, which is a measurement, but the number that goes out because
    of it. Asked through the same method the sequences call, so the card cannot
    describe a correction that is not happening."""
    measured(service)
    row = service.learning()["modes"][0]
    assert row["sends_c"] == service._corrected(28, Mode.WARMING)

    service.set_learning(False)
    row = service.learning()["modes"][0]
    assert row["sends_c"] == 28


def test_tonight_gets_a_row_before_it_has_ever_run(service):
    """The first evening is when "nothing measured yet, three nights to go" is the
    most useful thing this card can say, and it is the evening with no rows."""
    service.schedule = Schedule(
        wake_time=time(7, 30),
        bed_time=time(22, 30),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        stages=[SleepStage(Stage.DEEP, 240, 28), SleepStage(Stage.WAKE, 120, 26)],
    )
    rows = service.learning()["modes"]
    assert [r["mode"] for r in rows] == ["warming"]
    assert rows[0]["settle_runs"] == 0


# --- The sentences the card draws --------------------------------------------------


def test_a_locked_row_says_what_it_is_waiting_for(service):
    card = learning_json(service.learning())
    pace = card["modes"][0]["skills"][0]
    assert pace["unlocked"] is False
    assert "estimate" in pace["detail"]


def test_an_unlocked_row_names_the_number_that_goes_out(service):
    measured(service)
    settle = learning_json(service.learning())["modes"][0]["skills"][1]
    assert settle["unlocked"] is True
    assert "sends 30° for a 28° bed" in settle["detail"]


def test_switched_off_the_card_says_the_correction_is_not_happening(service):
    """The one sentence that could quietly become a lie. It describes something
    the unit is being sent, and with the switch off it is not being sent."""
    measured(service)
    service.set_learning(False)
    settle = learning_json(service.learning())["modes"][0]["skills"][1]
    assert "Lands 2.1° below" in settle["detail"]
    assert "not being corrected" in settle["detail"].lower()
    assert "sends" not in settle["detail"]


def test_the_dots_never_count_past_what_was_needed(service):
    """Six nights in, the card should not be drawing six dots out of three."""
    for _ in range(6):
        ran(service.db)
    skill = learning_json(service.learning())["modes"][0]["skills"][1]
    assert (skill["runs"], skill["needed"]) == (3, 3)


# --- Over HTTP ---------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    service = Service(
        Settings(db_path=str(tmp_path / "s.db")), clock=VirtualClock(NOW), echo=False
    )
    real_app.state.service = service
    real_app.state.build = "test"
    yield TestClient(real_app), service
    service.db.close()


def test_the_card_is_readable_with_nothing_recorded(client):
    http, _ = client
    body = http.get("/api/learning").json()
    assert body["on"] is True
    assert isinstance(body["modes"], list)


def test_the_switch_goes_both_ways_over_http(client):
    http, service = client
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(service.db)

    assert http.post("/api/learning", json={"on": False}).json()["on"] is False
    assert service._corrected(28, Mode.WARMING) == 28

    assert http.post("/api/learning", json={"on": True}).json()["on"] is True
    assert service._corrected(28, Mode.WARMING) == 30


def test_starting_again_over_http_clears_one_mode(client):
    http, service = client
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(service.db)
        ran(service.db, mode="turbo", target=24, frm=27.0, to=23.6)

    body = http.post("/api/learning/forget", json={"mode": "warming"}).json()
    by_mode = {m["mode"]: m["unlocked"] for m in body["modes"]}
    assert by_mode["warming"] == 0, "set back to nought, and still on the card"
    assert by_mode["turbo"] == 2, "cooling had nothing to do with it"
    assert service.db.learned_offset_c("turbo", 24) == -0.4
    assert len(service.db.precondition_runs(limit=99)) == MIN_RUNS_TO_LEARN * 2


def test_starting_again_with_no_mode_clears_everything(client):
    """Warming goes back to nought, and what is left is tonight's own mode at
    nought, which is the same card anybody sees before their first night."""
    http, service = client
    for _ in range(MIN_RUNS_TO_LEARN):
        ran(service.db)

    modes = http.post("/api/learning/forget", json={}).json()["modes"]
    assert "warming" in {m["mode"] for m in modes}, "reset, not deleted"
    assert all(m["unlocked"] == 0 for m in modes)
    assert service.db.learned_offset_c("warming", 28) is None


def test_a_mode_that_is_not_a_mode_is_refused(client):
    http, _ = client
    assert http.post("/api/learning/forget", json={"mode": "toasty"}).status_code == 422
