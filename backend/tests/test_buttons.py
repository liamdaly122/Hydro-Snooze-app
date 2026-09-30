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
from hydrosnooze.models import Button, Mode, Power, Schedule, SleepStage, Stage
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


class _TextSensor(_Sensor): ...


@pytest.fixture
def fake_api(monkeypatch):
    """Stand in for the library, which is not installed on a development Mac."""
    entities: list[object] = []
    module = types.ModuleType("aioesphomeapi")
    module.SensorInfo = _Sensor  # type: ignore[attr-defined]
    module.BinarySensorInfo = _BinarySensor  # type: ignore[attr-defined]
    module.TextSensorInfo = _TextSensor  # type: ignore[attr-defined]
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
async def test_on_off_is_one_press_and_no_waiting(service):
    """The whole of the 22 September fix.

    It used to call power_off, which sends the gesture and then polls the plug
    for up to power_confirm_s to be sure. Two minutes. At 07:30 that patience is
    the difference between a bed that switched off and a bed that ran all day;
    on a bedside button it is somebody pressing again because nothing happened.
    """
    started = service.clock.now()
    await tap(service, BUTTON_POWER)

    # A toggle, so the state is genuinely unknown until the plug reports. Saying
    # OFF here would be the confident lie this whole project exists to avoid.
    assert service.state.power is Power.UNKNOWN
    assert said(service) == [
        "Bedside: on/off. Sent the on/off gesture. It should go off, and the plug says "
        "which within half a minute."
    ]
    # The clock is what proves it. power_off would have advanced it by the
    # settle and the polling; one press costs the presses and nothing else.
    assert service.clock.now() - started < timedelta(seconds=30)


@pytest.mark.asyncio
async def test_it_says_which_way_it_expects_the_toggle_to_go(service):
    service._set_state(power=Power.OFF)
    await tap(service, BUTTON_POWER)
    assert "It should come on" in said(service)[0]


@pytest.mark.asyncio
async def test_pressing_it_twice_is_making_sure_not_a_round_trip(service):
    """A press is not a quantity, so it does not add up."""
    await tap(service, BUTTON_POWER, BUTTON_POWER)
    assert len(said(service)) == 1


@pytest.mark.asyncio
async def test_a_stale_reading_costs_nothing_because_it_only_picks_the_wording(service):
    """The plug decides what to expect, never what to send. So a state nothing
    has confirmed still gets a press, rather than nothing."""
    service._set_state(power=Power.UNKNOWN)
    before = service.transmitter.count(Button.POWER)
    await tap(service, BUTTON_POWER)
    assert service.transmitter.count(Button.POWER) == before + 1
    assert "Nothing had confirmed which way it was" in said(service)[0]


@pytest.mark.asyncio
async def test_on_off_and_a_temperature_together_drops_the_temperature(service):
    """A fumble in the dark. Doing both would set a temperature on a unit whose
    state nothing knows, so the bigger gesture wins and the log says the other
    was dropped."""
    await tap(service, BUTTON_POWER, BUTTON_WARMER, BUTTON_WARMER)
    assert stage_temp(service, Stage.DEEP) == 24
    assert said(service) == [
        "Bedside: on/off and 2 warmer. Doing the on/off and leaving the temperature alone.",
        "Bedside: on/off. Sent the on/off gesture. It should go off, and the plug says "
        "which within half a minute.",
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


# --- The fifteen seconds in the middle ----------------------------------------
#
# Found at the bed on 17 September: five taps of cooler, and the app caught two.
#
# The window closes, the totals are taken, and then about fifteen seconds of
# infrared go out. The task running all that is not finished, so nothing starts
# another one, and every press landing in those fifteen seconds went into a total
# that nothing was left waiting to read. They were not dropped, which is almost
# worse: they sat there until the next press happened to start a fresh task, and
# then arrived in a lump attached to somebody else's tap.


@pytest.mark.asyncio
async def test_presses_during_the_command_are_not_lost(service, monkeypatch):
    sent: list[int] = []
    really_set = service.set_stage_tonight

    async def slow(stage, target_c):
        sent.append(target_c)
        if len(sent) == 1:
            # Three more taps while the first lot is still going out.
            for _ in range(3):
                service._button_pressed(BUTTON_COOLER)
        return await really_set(stage, target_c)

    monkeypatch.setattr(service, "set_stage_tonight", slow)
    await tap(service, BUTTON_COOLER, BUTTON_COOLER)

    assert sent == [22, 19], "the three taps in the middle went nowhere"
    assert stage_temp(service, Stage.DEEP) == 19


@pytest.mark.asyncio
async def test_they_never_arrive_attached_to_a_later_press(service, monkeypatch):
    """The half of this that is worse than losing them.

    A total nothing is waiting to read is not empty, it is stale. The next press,
    minutes later, would start a fresh task and find four degrees of somebody
    else's tapping sitting in front of its own one.
    """
    sent: list[int] = []
    really_set = service.set_stage_tonight

    async def slow(stage, target_c):
        sent.append(target_c)
        if len(sent) == 1:
            service._button_pressed(BUTTON_COOLER)
        return await really_set(stage, target_c)

    monkeypatch.setattr(service, "set_stage_tonight", slow)
    await tap(service, BUTTON_COOLER)  # 24 -> 23, and one more in the middle
    assert sent == [23, 22]

    service.clock.advance(timedelta(minutes=5))
    await tap(service, BUTTON_WARMER)
    assert sent == [23, 22, 23], "a later tap inherited the earlier one"


@pytest.mark.asyncio
async def test_a_press_cancelled_by_its_opposite_mid_command_sends_nothing_more(
    service, monkeypatch
):
    sent: list[int] = []
    really_set = service.set_stage_tonight

    async def slow(stage, target_c):
        sent.append(target_c)
        if len(sent) == 1:
            service._button_pressed(BUTTON_COOLER)
            service._button_pressed(BUTTON_WARMER)
        return await really_set(stage, target_c)

    monkeypatch.setattr(service, "set_stage_tonight", slow)
    await tap(service, BUTTON_WARMER)
    assert sent == [25]
    assert service._button_delta == 0


@pytest.mark.asyncio
async def test_a_crash_does_not_leave_a_total_behind(service, monkeypatch):
    """A total nothing is waiting to read is stale, not empty."""

    async def explode(*a, **k):
        raise RuntimeError("something nobody thought of")

    monkeypatch.setattr(service, "set_stage_tonight", explode)
    await tap(service, BUTTON_WARMER, BUTTON_WARMER)

    assert service._button_delta == 0
    assert service._button_until is None


# --- Which access point it actually joined -------------------------------------
#
# The board prefers the hub and falls back to a booster, which is what makes it
# safe to point at the hub without a torch and a USB cable. It also makes "which
# one is it on" a real question, and until this there was no way to answer it
# without a serial cable and a boot banner.


@pytest.mark.asyncio
async def test_the_board_says_which_network_it_joined(fake_api):
    from hydrosnooze.adapters.probes import NETWORK

    fake_api.extend(
        [_Sensor(i, n) for i, n in enumerate(NAMES, 1)]
        + [_TextSensor(40, NETWORK)]
    )
    p = Probes(VirtualClock(NOW), host="192.0.2.9")
    await p._connect()
    assert p.network is None, "nothing said yet is not a guess"

    p._on_state(State(40, "VM1876778"))
    assert p.network == "VM1876778"


def test_the_network_name_is_not_a_reading(probes):
    """Same rule as the signal and the buttons. It is about the link rather than
    about the bed, so it must not hold off a rebuild of a dead probe bus."""
    probes._network_key = 40
    probes.last_reading_at = None
    probes._on_state(State(40, "VM1876778"))
    assert probes.network == "VM1876778"
    assert probes.last_reading_at is None
    assert probes.readings == {}
    assert probes.missing() == list(NAMES)


def test_an_empty_network_name_is_not_a_network(probes):
    probes._network_key = 40
    probes._on_state(State(40, ""))
    assert probes.network is None


def test_the_health_row_names_the_access_point(tmp_path):
    from hydrosnooze.adapters.probes import FLOW, RETURN, ROOM, Reading

    service = Service(
        Settings(db_path=str(tmp_path / "s.db"), probes_host="192.0.2.9"), echo=False
    )
    now = service.clock.now()
    for name, value in ((FLOW, 20.8), (RETURN, 20.5), (ROOM, 21.0)):
        service.probes.readings[name] = Reading(value, now)
    service.probes.last_reading_at = now

    service.probes.signal_dbm = -53
    service.probes.network = "VM1876778"
    assert "Signal -53 dBm on VM1876778" in service._probes_health().detail

    # On the booster and weak, which is the combination worth seeing at a glance.
    service.probes.signal_dbm = -85
    service.probes.network = "VM1876778_EXT"
    detail = service._probes_health().detail
    assert "Signal -85 dBm on VM1876778_EXT, weak enough to expect gaps" in detail

    # A board flashed before this existed says nothing, rather than guessing.
    service.probes.network = None
    assert "on " not in service._probes_health().detail
    service.db.close()


# --- The gesture, against the simulated unit ------------------------------------
#
# For one morning the bedside on/off sent a single bare press of power. On a
# running unit with a dark display that press only wakes the display, and
# overnight the display is always dark, so the button could not switch the unit
# off at the one time it exists for. A second tap would have rescued it and the
# settle window merges a second tap away on purpose.
#
# These drive the simulated unit from each starting state, because the claim is
# that one gesture is right from all of them, and a claim like that is only as
# good as the states it was tried from.


def _unit(service: Service, *, powered: bool, lit: bool = False):
    unit = service.unit
    unit.powered = powered
    unit.powered_at = service.clock.now() if powered else None
    unit.schedule_armed_at = None
    unit.adjusting = False
    unit.display_awake_until = None
    if powered and lit:
        unit._wake(service.clock.now())
    return unit


@pytest.mark.asyncio
async def test_it_switches_off_a_running_unit_with_a_dark_display(service):
    """The overnight case, and the one the bare press got wrong."""
    unit = _unit(service, powered=True, lit=False)
    await tap(service, BUTTON_POWER)
    assert unit.powered is False


@pytest.mark.asyncio
async def test_it_switches_off_a_running_unit_with_a_lit_display(service):
    unit = _unit(service, powered=True, lit=True)
    await tap(service, BUTTON_POWER)
    assert unit.powered is False


@pytest.mark.asyncio
async def test_it_switches_on_a_unit_that_is_off(service):
    """The wake presses are ignored by an off unit, so the same gesture turns
    it on. Which is what makes it safe to send without knowing the state."""
    service._set_state(power=Power.OFF)
    unit = _unit(service, powered=False)
    await tap(service, BUTTON_POWER)
    assert unit.powered is True


@pytest.mark.asyncio
async def test_it_is_the_same_gesture_whatever_the_app_believes(service):
    """The belief picks the wording and nothing else. A stale ON over a unit that
    is actually off still turns it on, rather than sending something that only
    works if the belief is right."""
    service._set_state(power=Power.ON)
    unit = _unit(service, powered=False)
    await tap(service, BUTTON_POWER)
    assert unit.powered is True


# --- The two power buttons are different on purpose -----------------------------
#
# Pinned because a stray edit on 22 September turned the app's power button into
# the bedside gesture and all 689 tests passed. Nothing here said which button
# sends what, so a change to what a user-facing button does was invisible.


@pytest.mark.asyncio
async def test_the_app_power_button_is_one_bare_press(service):
    """The remote's own button, for someone looking at the unit. If it only
    wakes the display they press it again, and nothing merges that away."""
    before = list(service.transmitter.sent)
    await service.press_power()
    sent = service.transmitter.sent[len(before):]
    assert sent == [Button.POWER]


@pytest.mark.asyncio
async def test_the_bedside_power_button_is_wake_then_power(service):
    """For someone lying in the dark. A second tap is merged by the settle
    window, so the one gesture has to work from any state on its own."""
    before = list(service.transmitter.sent)
    await tap(service, BUTTON_POWER)
    sent = service.transmitter.sent[len(before):]
    assert sent == [Button.TEMP_DOWN, Button.TEMP_DOWN, Button.POWER]


# --- How long the board has really been quiet -------------------------------------


@pytest.mark.asyncio
async def test_reconnecting_does_not_reset_how_long_it_has_been_quiet(fake_api):
    """From the review of 22 September. Every connect set the silence clock to
    now, so a board that had said nothing for an hour, and had reconnected
    two minutes ago, was reported as "Nothing for 2 minutes". The fresh window
    a new link gets before it is torn down again is a separate question."""
    fake_api.extend([_Sensor(i, n) for i, n in enumerate(NAMES, 1)])
    p = Probes(VirtualClock(NOW), host="192.0.2.9")
    p.last_reading_at = NOW
    p.clock.advance(timedelta(minutes=58))

    await p._connect()
    p.clock.advance(timedelta(minutes=2))

    assert p.quiet_for == timedelta(hours=1), "since the last reading, not the last connect"
    # And the new link still gets its full window before it is rebuilt.
    assert p._silent_for() == timedelta(minutes=2)
