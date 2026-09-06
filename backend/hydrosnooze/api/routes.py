"""The HTTP surface, one route per line in Part 3 of the brief."""

from __future__ import annotations

import asyncio
import json
from datetime import time, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..models import Mode, range_for
from ..sequences import CommandFailed, WriteProgress
from ..service import Service
from .schemas import schedule_json, state_json

router = APIRouter(prefix="/api")


def _service(request: Request) -> Service:
    return request.app.state.service


# --- Bodies -------------------------------------------------------------------


class SchedulePatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    days_of_week: list[int] | None = None
    wake_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    phase1_temp_c: int | None = None
    phase2_temp_c: int | None = None
    phase3_temp_c: int | None = None
    mode: Mode | None = None
    precool_enabled: bool | None = None
    precool_lead_minutes: int | None = Field(default=None, ge=0, le=240)


class TemperatureBody(BaseModel):
    target_c: int


class ModeBody(BaseModel):
    mode: Mode


# --- Reads --------------------------------------------------------------------


@router.get("/info")
async def get_info(request: Request) -> dict[str, object]:
    service = _service(request)
    return {
        "fake_transmitter": service.settings.transmitter == "fake",
        "fake_power_monitor": service.settings.power_monitor == "fake",
        "max_temperature_c": service.settings.max_temperature_c,
    }


@router.get("/state")
async def get_state(request: Request) -> dict[str, object]:
    return state_json(_service(request).state)


@router.get("/schedule")
async def get_schedule(request: Request) -> dict[str, object]:
    return schedule_json(_service(request).schedule)


@router.get("/events")
async def get_events(request: Request, limit: int = 100) -> list[dict[str, object]]:
    return [e.as_dict() for e in _service(request).events.recent(min(limit, 500))]


@router.get("/power")
async def get_power(request: Request, hours: int = 24) -> list[dict[str, object]]:
    service = _service(request)
    since = service.clock.now() - timedelta(hours=min(hours, 168))
    return [{"at": at.isoformat(), "watts": watts} for at, watts in service.db.power_history(since)]


# --- Writes -------------------------------------------------------------------


@router.put("/schedule")
async def put_schedule(request: Request, patch: SchedulePatch) -> dict[str, object]:
    """Saves locally. Deliberately does NOT touch the unit."""
    service = _service(request)
    data = patch.model_dump(exclude_none=True)

    mode = data.get("mode", service.schedule.mode)
    for key in ("phase1_temp_c", "phase2_temp_c", "phase3_temp_c"):
        if key not in data:
            continue
        _guard_temperature(service, data[key], mode)

    if "wake_time" in data:
        hour, minute = (int(p) for p in data["wake_time"].split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise HTTPException(422, "wake_time must be a real time of day")
        data["wake_time"] = time(hour, minute)

    if "days_of_week" in data and any(d < 0 or d > 6 for d in data["days_of_week"]):
        raise HTTPException(422, "days_of_week must be 0 (Monday) to 6 (Sunday)")

    return schedule_json(service.update_schedule(data))


@router.post("/schedule/write")
async def post_schedule_write(request: Request) -> StreamingResponse:
    """Long running. Streams one JSON object per line as the presses go out.

    Only ever reached from an explicit action in the app. Never from the tick
    loop, and never from anything automatic.
    """
    service = _service(request)
    queue: asyncio.Queue[WriteProgress | None] = asyncio.Queue()

    async def run() -> None:
        try:
            await service.write_schedule(on_progress=queue.put_nowait)
        except CommandFailed as exc:
            queue.put_nowait(WriteProgress("failed", 0, 1, str(exc)))
        finally:
            queue.put_nowait(None)

    async def stream():
        task = asyncio.create_task(run())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield json.dumps(item.__dict__) + "\n"
        finally:
            await task

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.post("/schedule/arm")
async def post_arm(request: Request) -> dict[str, object]:
    service = _service(request)
    try:
        await service.arm_now()
    except CommandFailed as exc:
        raise HTTPException(502, str(exc)) from exc
    return state_json(service.state)


@router.post("/power/on")
async def post_power_on(request: Request) -> dict[str, object]:
    service = _service(request)
    await service.power_on()
    return state_json(service.state)


@router.post("/power/off")
async def post_power_off(request: Request) -> dict[str, object]:
    service = _service(request)
    await service.power_off()
    return state_json(service.state)


@router.post("/temperature")
async def post_temperature(request: Request, body: TemperatureBody) -> dict[str, object]:
    service = _service(request)
    mode = service.state.assumed_mode or service.schedule.mode
    _guard_temperature(service, body.target_c, mode)

    if not service.state.can_set_temperature:
        raise HTTPException(
            409,
            "The unit ignores temperature presses unless it is on and no schedule is running.",
        )
    try:
        await service.set_temperature(body.target_c)
    except CommandFailed as exc:
        raise HTTPException(502, str(exc)) from exc
    return state_json(service.state)


@router.post("/mode")
async def post_mode(request: Request, body: ModeBody) -> dict[str, object]:
    service = _service(request)
    try:
        await service.set_mode(body.mode)
    except CommandFailed as exc:
        raise HTTPException(502, str(exc)) from exc
    return state_json(service.state)


def _guard_temperature(service: Service, target_c: int, mode: Mode) -> None:
    """The hard safety cap, enforced here so nothing can route around it.

    This is a heater capable of 55C under a bed.
    """
    cap = service.settings.max_temperature_c
    if target_c > cap:
        raise HTTPException(422, f"{target_c}C is above the {cap}C safety cap")
    low, high = range_for(mode)
    if not low <= target_c <= high:
        raise HTTPException(422, f"{target_c}C is outside {mode.value}'s range of {low} to {high}")
