"""The night of 9 September, and the three bugs that made it possible.

The blaster's Wi-Fi link died at some point overnight. What should have happened
is a warning, a retry every thirty seconds, and a stage that either landed late
or was reported missed. What actually happened was a stack trace every second
for hours, a bed left switched on all morning, and a device bar showing green
throughout.

Three things had to go wrong together, so all three are pinned here.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import Button, Schedule
from hydrosnooze.sequences import CommandFailed, Commands
from hydrosnooze.service import RETRY_AFTER, Service


class DeadLink(Exception):
    """Stands in for aioesphomeapi's APIConnectionError.

    The point is that it is not CommandFailed and not TransmitterError: it is a
    type the layers above have never heard of, which is exactly what escaped.
    """


class DroppedTransmitter:
    def __init__(self) -> None:
        self.attempts = 0

    async def press(self, button: Button, note: str = "") -> None:
        self.attempts += 1
        raise DeadLink("Not connected to hydrosnooze-ir @ 192.168.1.178!")

    async def reachable(self) -> bool:
        return False

    async def close(self) -> None: ...


@pytest.fixture
def service():
    clock = VirtualClock(datetime(2026, 9, 9, 2, 0))
    svc = Service(Settings(db_path=":memory:"), clock=clock, echo=False)
    svc.schedule = Schedule(wake_time=time(6, 30), days_of_week=[0, 1, 2, 3, 4, 5, 6])
    svc.transmitter = DroppedTransmitter()
    svc.commands = Commands(svc.transmitter, svc.power, svc.clock, svc.settings, svc.events)
    return svc


# --- 1. The library's error must not reach the scheduler as itself -------------


@pytest.mark.asyncio
async def test_an_unknown_transmitter_error_becomes_a_command_failure(service):
    """CommandFailed is the one thing the scheduler catches. Anything else went
    straight past it."""
    with pytest.raises(CommandFailed):
        await service.commands._press(Button.TEMP_DOWN, "wake 1/2")


# --- 2. A raising job must still back off --------------------------------------


@pytest.mark.asyncio
async def test_a_stage_that_raises_is_not_retried_every_second(service):
    """The actual failure. Neither branch of _tick ran, so the job stayed
    unmarked and no backoff was recorded, and due() handed it back one second
    later, forever."""
    plan = service.schedule.plan_for(datetime(2026, 9, 9).date())
    service.clock.jump_to(plan.steps[0].starts_at)

    for _ in range(30):
        await service._tick()
        service.clock.advance(timedelta(seconds=1))

    assert service.transmitter.attempts == 1, (
        f"tried {service.transmitter.attempts} times in 30 seconds"
    )


@pytest.mark.asyncio
async def test_it_does_try_again_once_the_backoff_has_passed(service):
    plan = service.schedule.plan_for(datetime(2026, 9, 9).date())
    service.clock.jump_to(plan.steps[0].starts_at)

    await service._tick()
    service.clock.advance(RETRY_AFTER + timedelta(seconds=1))
    await service._tick()

    assert service.transmitter.attempts == 2


@pytest.mark.asyncio
async def test_the_loop_never_sees_the_exception(service):
    """_tick_loop's catch-all logged it and carried on, which is why this ran all
    night rather than stopping. _tick has to absorb it itself."""
    plan = service.schedule.plan_for(datetime(2026, 9, 9).date())
    service.clock.jump_to(plan.steps[0].starts_at)
    await service._tick()  # must not raise


@pytest.mark.asyncio
async def test_it_says_so_once_rather_than_every_second(service):
    plan = service.schedule.plan_for(datetime(2026, 9, 9).date())
    service.clock.jump_to(plan.steps[0].starts_at)

    for _ in range(20):
        await service._tick()
        service.clock.advance(timedelta(seconds=1))

    complaints = [e for e in service.events.recent(100) if e.level in ("error", "warning")]
    assert 0 < len(complaints) <= 3, f"logged {len(complaints)} times"


# --- 3. The bar must not show green on a dead link -----------------------------


@pytest.mark.asyncio
async def test_a_blaster_that_cannot_be_reached_is_not_reported_healthy(service):
    """It showed green all night, because reachable() only checked a button list
    cached from whenever the process last connected successfully."""
    await service._check_blaster()
    blaster = next(d for d in service.health() if d.name == "blaster")
    assert blaster.health.value != "ok"


# --- The adapter itself, against a link that dies underneath it -----------------


class FakeEntity:
    def __init__(self, object_id: str, key: int) -> None:
        self.object_id, self.key = object_id, key


class FakeClient:
    """An aioesphomeapi client whose connection can be pulled out from under it,
    which is what a Wi-Fi drop looks like from the adapter's side."""

    instances: list["FakeClient"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.alive = True
        self.presses: list[int] = []
        FakeClient.instances.append(self)

    async def connect(self, login: bool = False) -> None:
        if not self.alive:
            raise DeadLink("Not connected!")

    async def list_entities_services(self):
        if not self.alive:
            raise DeadLink("Not connected!")
        return [FakeEntity(b.value, i) for i, b in enumerate(Button)], []

    def button_command(self, key: int) -> None:
        if not self.alive:
            raise DeadLink("Not connected!")
        self.presses.append(key)

    async def disconnect(self) -> None:
        self.alive = False


@pytest.fixture
def blaster(monkeypatch):
    import sys
    import types

    from hydrosnooze.adapters.esphome import EsphomeTransmitter

    FakeClient.instances = []
    fake = types.ModuleType("aioesphomeapi")
    fake.APIClient = FakeClient
    fake.ButtonInfo = FakeEntity
    monkeypatch.setitem(sys.modules, "aioesphomeapi", fake)
    return EsphomeTransmitter("192.168.1.178")


@pytest.mark.asyncio
async def test_a_press_reconnects_when_the_link_has_gone(blaster):
    """The root cause. _connect returned early whenever a client object existed,
    so once the socket died every press for the rest of the night raised
    "Not connected" against a client that was never going to work again."""
    await blaster.press(Button.POWER)
    assert len(FakeClient.instances) == 1

    FakeClient.instances[0].alive = False  # the Wi-Fi drops overnight
    await blaster.press(Button.TEMP_DOWN)

    assert len(FakeClient.instances) == 2, "it reused the dead client"
    assert FakeClient.instances[1].presses, "the retry never sent anything"


@pytest.mark.asyncio
async def test_a_press_that_cannot_be_sent_raises_the_type_above_expects(blaster):
    from hydrosnooze.adapters.esphome import TransmitterError

    await blaster.press(Button.POWER)
    for client in FakeClient.instances:
        client.alive = False
    monkey = FakeClient.__init__

    def born_dead(self, *a, **k):
        monkey(self, *a, **k)
        self.alive = False

    FakeClient.__init__ = born_dead
    try:
        with pytest.raises(TransmitterError):
            await blaster.press(Button.TEMP_DOWN)
    finally:
        FakeClient.__init__ = monkey


@pytest.mark.asyncio
async def test_the_health_check_notices_a_dead_link(blaster):
    """It used to answer from a cached button list, so it stayed green for as
    long as the process had ever connected once."""
    assert await blaster.reachable() is True

    FakeClient.instances[-1].alive = False
    FakeClient.__init__ = (lambda orig: lambda self, *a, **k: (orig(self, *a, **k), setattr(self, "alive", False))[0])(FakeClient.__init__)

    assert await blaster.reachable() is False
