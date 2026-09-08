"""A step that does not land is retried until its window closes.

The blaster is on Wi-Fi and stage boundaries are hours apart, so the realistic
failure is not a bug: it is a board that dropped off the network at 01:55. Before
this, that cost the whole stage. The job was marked done before it ran, so due()
never offered it again and the bed sat at the wrong temperature until morning
with nothing left to try.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import pytest

from hydrosnooze.clock import VirtualClock
from hydrosnooze.config import Settings
from hydrosnooze.models import Schedule
from hydrosnooze.scheduler import STAGE_GRACE
from hydrosnooze.sequences import CommandFailed
from hydrosnooze.service import RETRY_AFTER, Service


@pytest.fixture
def service():
    clock = VirtualClock(datetime(2026, 9, 8, 21, 0))
    svc = Service(Settings(db_path=":memory:"), clock=clock, echo=False)
    svc.schedule = Schedule(wake_time=time(6, 30), days_of_week=[0, 1, 2, 3, 4])
    return svc


def break_the_blaster(service, *, failures: int) -> dict:
    """Fail the first `failures` attempts at applying anything, then work."""
    counter = {"calls": 0}
    original = service._apply

    async def flaky(mode, temp_c):
        counter["calls"] += 1
        if counter["calls"] <= failures:
            raise CommandFailed("The blaster is not answering")
        return await original(mode, temp_c)

    service._apply = flaky
    return counter


async def run_until(service, until: datetime, step: timedelta = RETRY_AFTER):
    while service.clock.now() <= until:
        await service._tick()
        service.clock.advance(step)


@pytest.mark.asyncio
async def test_a_step_that_fails_is_tried_again(service):
    counter = break_the_blaster(service, failures=2)
    plan = service.schedule.plan_for(datetime(2026, 9, 9).date())
    service.clock.jump_to(plan.steps[0].starts_at)

    await run_until(service, plan.steps[0].starts_at + timedelta(minutes=3))

    assert counter["calls"] >= 3, "it gave up instead of trying again"
    deep = next(j for j in [_stage_job(service, plan, 0)])
    assert service.scheduler.fired.has_fired(deep), "the retry never got marked done"


@pytest.mark.asyncio
async def test_it_is_only_marked_done_once_it_has_actually_worked(service):
    """The bug this fixes. Marking before running made a failure permanent."""
    break_the_blaster(service, failures=99)
    plan = service.schedule.plan_for(datetime(2026, 9, 9).date())
    service.clock.jump_to(plan.steps[0].starts_at)

    await service._tick()

    assert not service.scheduler.fired.has_fired(_stage_job(service, plan, 0))


@pytest.mark.asyncio
async def test_it_does_not_hammer_the_blaster(service):
    """A second between attempts would flood the log and fix nothing."""
    counter = break_the_blaster(service, failures=99)
    plan = service.schedule.plan_for(datetime(2026, 9, 9).date())
    service.clock.jump_to(plan.steps[0].starts_at)

    for _ in range(10):
        await service._tick()
        service.clock.advance(timedelta(seconds=1))

    assert counter["calls"] == 1, "it retried without waiting"


@pytest.mark.asyncio
async def test_it_stops_once_the_stage_is_mostly_over(service):
    """Setting a stage twenty minutes late is worse than leaving the bed alone,
    which is what STAGE_GRACE has always meant. Retrying does not change that."""
    counter = break_the_blaster(service, failures=99)
    plan = service.schedule.plan_for(datetime(2026, 9, 9).date())
    start = plan.steps[0].starts_at
    service.clock.jump_to(start)

    await run_until(service, start + STAGE_GRACE + timedelta(minutes=5))
    attempts_at_close = counter["calls"]

    service.clock.advance(timedelta(minutes=10))
    await service._tick()
    assert counter["calls"] == attempts_at_close, "it kept trying past the window"


@pytest.mark.asyncio
async def test_a_missed_step_is_still_reported(service):
    """Retrying must not swallow the error. A stage that never landed is the
    single most important thing for the log to say."""
    break_the_blaster(service, failures=99)
    plan = service.schedule.plan_for(datetime(2026, 9, 9).date())
    start = plan.steps[0].starts_at
    service.clock.jump_to(start)

    await run_until(service, start + STAGE_GRACE + timedelta(minutes=5))

    messages = [e.message for e in service.events.recent(50) if e.level == "error"]
    assert any("Missed the Deep stage" in m for m in messages)


def _stage_job(service, plan, index):
    from hydrosnooze.scheduler import Job

    return Job("stage", plan, plan.steps[index])
