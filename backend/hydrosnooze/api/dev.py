"""Time travel and a window into the simulated unit.

Only mounted when the transmitter is fake. On the Pi with real hardware attached
these routes do not exist at all, so there is no way to jump the clock on a unit
that is actually running.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..service import Service

router = APIRouter(prefix="/api/dev")


class ClockBody(BaseModel):
    #: "21:29" for this evening, or a full ISO timestamp for a specific date.
    jump_to: str | None = None
    advance_minutes: float | None = None
    speed: float | None = None


def _service(request: Request) -> Service:
    service: Service = request.app.state.service
    if not service.is_simulated:
        raise HTTPException(404, "Not simulated")
    return service


@router.get("/sim")
async def get_sim(request: Request) -> dict[str, object]:
    snapshot = _service(request).sim_snapshot()
    assert snapshot is not None
    return snapshot


@router.post("/clock")
async def post_clock(request: Request, body: ClockBody) -> dict[str, object]:
    service = _service(request)

    if body.jump_to:
        service.sim_jump_to(_parse_target(body.jump_to, service.clock.now()))
    if body.advance_minutes:
        service.sim_jump_to(service.clock.now() + timedelta(minutes=body.advance_minutes))
    if body.speed is not None:
        service.sim_set_speed(body.speed)

    snapshot = service.sim_snapshot()
    assert snapshot is not None
    return snapshot


@router.post("/reset")
async def post_reset(request: Request) -> dict[str, object]:
    """Put the simulated unit back to cold and dark, as if freshly plugged in."""
    service = _service(request)
    assert service.unit is not None
    unit = service.unit
    unit.powered = False
    unit.powered_at = None
    unit.schedule_armed_at = None
    unit.wizard_phase = None
    unit.display_awake_until = None
    unit.adjusting = False
    service.scheduler.fired = type(service.scheduler.fired)()
    service.events.info("sim", "Simulated unit reset")
    snapshot = service.sim_snapshot()
    assert snapshot is not None
    return snapshot


def _parse_target(value: str, now: datetime) -> datetime:
    """Accept "21:29" for today, or a full ISO timestamp."""
    try:
        if len(value) <= 5 and ":" in value:
            hour, minute = (int(p) for p in value.split(":"))
            return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, f"Could not read '{value}' as a time") from exc
