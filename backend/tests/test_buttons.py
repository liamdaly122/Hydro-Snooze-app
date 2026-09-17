"""The three buttons on the bedside, from the board to the bed.

Wired on 17 September onto the board that already carried the temperature
probes: a four core cable, one ground shared with the probes, and a signal core
each. They exist because the phone is the wrong instrument at 3am. Waking up
enough to find it, unlock it and read a screen is most of the way to being
awake, and the thing I actually want is to reach out and press something.

Two rules run through all of this.

**A release is a state too.** The board reports the button closing and the
button opening, and both arrive here as a state. Acting on both doubles every
press, which at a degree each is a bed two degrees away from what somebody
asked for.

**A single temperature change is thirty-eight presses of infrared and takes
about fifteen seconds.** So the presses are counted rather than acted on, and
one command goes out for the net result once the finger stops.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, time, timedelta

import pytest

from hydrosnooze.adapters.probes import (
    BUTTON_COOLER,
    BUTTON_NAMES,
    BUTTON_POWER,
    BUTTON_WARMER,
    FLOW,
    NAMES,
    RETURN,
    ROOM,
    Probes,
)
from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import Mode, Power, Schedule, SleepStage, Stage
from hydrosnooze.service import BUTTON_SETTLE, Service

#: The middle of the Deep stage on a night that arms at 22:30 and wakes at 06:30.
NOW = datetime(2026, 9, 10, 2, 0)


class State:
    """What the library hands to `_on_state`: a key and a value, nothing else."""

    def __init__(self, key: int, value: object) -> None:
        self.key = key
        self.state = value


@pytest.fixture
def probes():
    heard: list[str] = []
    p = Probes(VirtualClock(NOW), host="192.0.2.9", on_button=heard.append)
    # What _connect builds once it has asked the board what it carries.
    p._keys = {1: FLOW, 2: RETURN, 3: ROOM}
    p._buttons = {11: BUTTON_WARMER, 12: BUTTON_COOLER, 13: BUTTON_POWER}
    p.heard = heard  # type: ignore[attr-defined]
    return p


def press(probes: Probes, key: int) -> None:
    """A whole press, the way the board sends one: closed, then open again."""
    probes._on_state(State(key, True))
    probes._on_state(State(key, False))


def settle(probes: Probes) -> None:
    """Whatever the library replayed when the subscription came up."""
    for key in (11, 12, 13):
        probes._on_state(State(key, False))


# --- The adapter: a press, and only a press -----------------------------------


def test_a_press_arrives_by_name(probes):
    settle(probes)
    press(probes, 11)
    assert probes.heard == [BUTTON_WARMER]


def test_the_release_is_not_a_second_press(probes):
    """The one that would double everything. The board reports the button
    opening as well as closing, and both come through here."""
    settle(probes)
    press(probes, 12)
    assert probes.heard == [BUTTON_COOLER]
    assert probes.presses == 1


def test_the_first_state_on_a_connection_is_not_a_finger(probes):
    """aioesphomeapi replays the current state of every entity when a
    subscription comes up. On a link this weak that happens at 3am, and a replay
    read as a press would set the bed going on its own.

    So a button whose state has not been seen on this connection is recorded and
    nothing else, even when what it replays is ON.
    """
    probes._on_state(State(11, True))
    assert probes.heard == []
    # And it still works normally afterwards.
    probes._on_state(State(11, False))
    press(probes, 11)
    assert probes.heard == [BUTTON_WARMER]


def test_a_repeated_on_does_not_count_twice(probes):
    """Seen in the real log on 17 September: the board sent ON for a button that
    was already ON. Held fingers and a weak link both do this."""
    settle(probes)
    probes._on_state(State(11, True))
    probes._on_state(State(11, True))
    assert probes.heard == [BUTTON_WARMER]


def test_an_unmatched_off_is_ignored(probes):
    """Also in the real log: an OFF with no ON in front of it."""
    settle(probes)
    probes._on_state(State(12, False))
    assert probes.heard == []


def test_a_press_is_not_a_reading(probes):
    """`last_reading_at` decides whether the link is dead enough to tear down and
    rebuild, and that is a question about the probes. A board whose 1-wire bus
    has stopped is still a board the app should be complaining about, and a
    press proving the Wi-Fi is fine would quietly stop it complaining."""
    settle(probes)
    probes.last_reading_at = None
    press(probes, 11)
    assert probes.last_reading_at is None
    assert probes.readings == {}


def test_a_callback_that_throws_does_not_take_the_subscription_down(probes):
    def explode(name: str) -> None:
        raise RuntimeError("no")

    probes.on_button = explode
    settle(probes)
    press(probes, 13)  # does not raise
    assert probes.presses == 1


# --- The adapter: buttons are not probes --------------------------------------


class _Client:
    """Just enough aioesphomeapi to get through `_connect`."""

    def __init__(self, entities):
        self._entities = entities
        self.subscribed = False

    async def connect(self, login: bool = True) -> None: ...

    async def list_entities_services(self):
        return self._entities, []

    def subscribe_states(self, _cb) -> None:
        self.subscribed = True

    async def disconnect(self) -> None: ...


class _Sensor:
    def __init__(self, key, name):
        self.key, self.name = key, name


class _BinarySensor(_Sensor): ...


@pytest.fixture
def fake_api(monkeypatch):
    """Stand in for the library, which is not installed on a development Mac."""
    entities: list[object] = []
    module = types.ModuleType("aioesphomeapi")
    module.SensorInfo = _Sensor  # type: ignore[attr-defined]
    module.BinarySensorInfo = _BinarySensor  # type: ignore[attr-defined]
    module.APIClient = lambda *a, **k: _Client(entities)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aioesphomeapi", module)
    return entities


@pytest.mark.asyncio
async def test_probes_and_buttons_are_sorted_into_their_own_dictionaries(fake_api):
    fake_api.extend(
        [_Sensor(i, n) for i, n in enumerate(NAMES, 1)]
        + [_BinarySensor(i, n) for i, n in enumerate(BUTTON_NAMES, 11)]
    )
    p = Probes(VirtualClock(NOW), host="192.0.2.9")
    await p._connect()
    assert set(p._keys.values()) == set(NAMES)
    assert set(p._buttons.values()) == set(BUTTON_NAMES)
    assert p.buttons_found == 3


@pytest.mark.asyncio
async def test_three_working_buttons_do_not_excuse_three_dead_probes(fake_api):
    """The trap this separation exists for.

    The guard means "I have connected to a board that is not the probe board".
    Let buttons into `_keys` and a board with three working buttons and three
    dead probes sails straight past the one check written to catch it.
    """
    fake_api.extend([_BinarySensor(i, n) for i, n in enumerate(BUTTON_NAMES, 11)])
    p = Probes(VirtualClock(NOW), host="192.0.2.9")
    with pytest.raises(RuntimeError, match="found none of"):
        await p._connect()


@pytest.mark.asyncio
async def test_a_board_flashed_before_the_buttons_existed_is_not_a_fault(fake_api):
    fake_api.extend([_Sensor(i, n) for i, n in enumerate(NAMES, 1)])
    p = Probes(VirtualClock(NOW), host="192.0.2.9")
    await p._connect()
    assert p.buttons_found == 0
    assert p.connected


# --- The service: counting rather than acting ---------------------------------


@pytest.fixture
def service(tmp_path):
    clock = VirtualClock(NOW)
    svc = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"),
        clock=clock,
        echo=False,
    )
    svc.schedule = Schedule(
        wake_time=time(6, 30),
        bed_time=time(22, 30),
        days_of_week=[0, 1, 2, 3, 4, 5, 6],
        stages=[
            SleepStage(Stage.DRIFT, 35, 27),
            SleepStage(Stage.DEEP, 205, 24),
            SleepStage(Stage.REM, 200, 26),
            SleepStage(Stage.WAKE, 40, 28),
        ],
    )
    svc._set_state(power=Power.ON, assumed_mode=Mode.QUIET, assumed_target_c=24)
    yield svc
    svc.db.close()


async def tap(service: Service, *names: str) -> None:
    """Presses landing together, then the window closing."""
    for name in names:
        service._button_pressed(name)
    assert service._button_task is not None
    await service._button_task


def said(service: Service) -> list[str]:
    """What the buttons put in the journal, oldest first."""
    return [e.message for e in reversed(service.events.recent(40)) if e.kind == "buttons"]


def stage_temp(service: Service, stage: Stage) -> int:
    found = service.tonight_now().stage(stage)
    assert found is not None
    return found.temp_c


def test_the_fixture_really_is_mid_stage(service):
    step = service.scheduler.stage_now(service.schedule, service.clock.now())
    assert step is not None and step.stage is Stage.DEEP and step.temp_c == 24


@pytest.mark.asyncio
async def test_three_taps_are_one_command_and_three_degrees(service):
    before = service.transmitter.presses_sent

    await tap(service, BUTTON_WARMER, BUTTON_WARMER, BUTTON_WARMER)

    assert stage_temp(service, Stage.DEEP) == 27
    assert said(service) == ["Bedside: 3 warmer. Deep goes to 27C."]
    # One rail and count, not three. Three would be about a hundred and fifteen
    # presses and three quarters of a minute of a unit being hammered.
    assert service.transmitter.presses_sent - before < 60


@pytest.mark.asyncio
async def test_cooler_goes_the_other_way(service):
    await tap(service, BUTTON_COOLER, BUTTON_COOLER)
    assert stage_temp(service, Stage.DEEP) == 22
    assert said(service) == ["Bedside: 2 cooler. Deep goes to 22C."]


@pytest.mark.asyncio
async def test_warmer_then_cooler_is_nothing_at_all(service):
    """Not two commands that cancel. Nothing leaves the Pi."""
    before = service.transmitter.presses_sent
    await tap(service, BUTTON_WARMER, BUTTON_COOLER)
    assert stage_temp(service, Stage.DEEP) == 24
    assert service.transmitter.presses_sent == before
    assert said(service) == []


@pytest.mark.asyncio
async def test_each_press_pushes_the_window_out(service):
    service._button_pressed(BUTTON_WARMER)
    first = service._button_until
    service.clock.advance(timedelta(seconds=0.5))
    service._button_pressed(BUTTON_WARMER)
    assert first is not None and service._button_until == first + timedelta(seconds=0.5)
    assert service._button_until == service.clock.now() + BUTTON_SETTLE
    await service._button_task


@pytest.mark.asyncio
async def test_a_stage_change_from_a_button_is_tonight_only(service):
    """The same rule as every other 2am adjustment. Tonight, not for good."""
    await tap(service, BUTTON_WARMER)
    assert stage_temp(service, Stage.DEEP) == 25
    assert service.schedule.stage(Stage.DEEP).temp_c == 24, "the routine is untouched"


# --- The service: a finger cannot cook the bed --------------------------------


@pytest.mark.asyncio
async def test_a_held_finger_stops_at_the_safety_cap(service):
    service.settings.max_temperature_c = 26
    await tap(service, *[BUTTON_WARMER] * 9)
    assert stage_temp(service, Stage.DEEP) == 26


@pytest.mark.asyncio
async def test_pressing_on_at_the_cap_says_so_rather_than_nothing(service):
    """At 3am a button that does nothing and says nothing is a broken button."""
    service.settings.max_temperature_c = 24
    await tap(service, BUTTON_WARMER)
    assert stage_temp(service, Stage.DEEP) == 24
    assert said(service) == ["Bedside: 1 warmer. Deep is already 24C, which is as far as it goes."]


# --- The service: on and off --------------------------------------------------


@pytest.mark.asyncio
async def test_on_off_switches_a_running_unit_off(service):
    await tap(service, BUTTON_POWER)
    assert service.state.power is Power.OFF
    assert said(service) == ["Bedside: on/off. Switching the unit off."]


@pytest.mark.asyncio
async def test_on_off_switches_a_stopped_unit_on(service):
    service._set_state(power=Power.OFF)
    await tap(service, BUTTON_POWER)
    assert said(service) == ["Bedside: on/off. Switching the unit on."]


@pytest.mark.asyncio
async def test_pressing_it_twice_is_making_sure_not_a_round_trip(service):
    """A press is not a quantity, so it does not add up."""
    await tap(service, BUTTON_POWER, BUTTON_POWER)
    assert said(service) == ["Bedside: on/off. Switching the unit off."]
    assert service.state.power is Power.OFF


@pytest.mark.asyncio
async def test_with_nothing_confirming_the_power_it_sends_one_press(service):
    """No state to toggle against, so it does not invent one. One press, and the
    plug says which way it went within thirty seconds."""
    service._set_state(power=Power.UNKNOWN)
    await tap(service, BUTTON_POWER)
    assert service.state.power is Power.UNKNOWN
    assert "one press" in said(service)[0]


@pytest.mark.asyncio
async def test_on_off_and_a_temperature_together_drops_the_temperature(service):
    """A fumble in the dark. Doing both would set a temperature on a unit whose
    state nothing knows, so the bigger gesture wins and the log says the other
    was dropped."""
    await tap(service, BUTTON_POWER, BUTTON_WARMER, BUTTON_WARMER)
    assert stage_temp(service, Stage.DEEP) == 24
    assert said(service) == [
        "Bedside: on/off and 2 warmer. Doing the on/off and leaving the temperature alone.",
        "Bedside: on/off. Switching the unit off.",
    ]


# --- The service: when there is nothing to change -----------------------------


@pytest.mark.asyncio
async def test_a_temperature_button_on_a_unit_that_is_off_says_so(service):
    service._set_state(power=Power.OFF)
    before = service.transmitter.presses_sent
    await tap(service, BUTTON_WARMER)
    assert service.transmitter.presses_sent == before
    assert said(service) == ["Bedside: 1 warmer. The unit is off, so there was nothing to change."]


@pytest.mark.asyncio
async def test_outside_a_stage_it_moves_what_was_last_asked_for(service):
    """On in the evening, run by hand, with no plan to edit."""
    service.clock.jump_to(datetime(2026, 9, 10, 20, 0))
    service._set_state(power=Power.ON, assumed_mode=Mode.QUIET, assumed_target_c=22)
    await tap(service, BUTTON_WARMER, BUTTON_WARMER)
    assert said(service) == ["Bedside: 2 warmer. No stage is running, so setting 24C by hand."]
    assert service.state.assumed_target_c == 24


@pytest.mark.asyncio
async def test_outside_a_stage_with_nothing_confirmed_it_refuses_to_guess(service):
    service.clock.jump_to(datetime(2026, 9, 10, 20, 0))
    service._set_state(power=Power.ON, assumed_target_c=None)
    before = service.transmitter.presses_sent
    await tap(service, BUTTON_WARMER)
    assert service.transmitter.presses_sent == before
    assert "nothing to move" in said(service)[0]


# --- The service: nothing here may take a night down --------------------------


@pytest.mark.asyncio
async def test_a_press_the_bed_never_gets_does_not_raise(service, monkeypatch):
    """The blaster being unreachable is a bad night, not a crashed service. The
    press is on the library's own task, and an exception escaping it takes the
    probe subscription down with it."""
    from hydrosnooze.sequences import CommandFailed

    async def refuse(*a, **k):
        raise CommandFailed("nothing is reaching the unit")

    monkeypatch.setattr(service, "set_stage_tonight", refuse)
    await tap(service, BUTTON_WARMER)  # does not raise


@pytest.mark.asyncio
async def test_the_window_survives_stop(service):
    service._button_pressed(BUTTON_WARMER)
    task = service._button_task
    await service.stop()
    assert task is not None and task.cancelled() or task.done()
