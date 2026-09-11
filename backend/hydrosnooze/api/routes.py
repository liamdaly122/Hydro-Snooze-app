"""The HTTP surface, one route per line in Part 3 of the brief."""

from __future__ import annotations

from dataclasses import replace
from datetime import time, timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..models import (
    MIN_STAGE_MINUTES,
    Mode,
    SleepStage,
    Stage,
    minutes_between,
    mode_for_target,
    modes_for,
    range_for,
)
from ..sequences import CommandFailed
from ..service import Service
from .schemas import health_json, profile_json, state_json

router = APIRouter(prefix="/api")


def _service(request: Request) -> Service:
    return request.app.state.service


# --- Bodies -------------------------------------------------------------------


class StagePatch(BaseModel):
    stage: Stage
    duration_minutes: int = Field(ge=5, le=720)
    temp_c: int


class SchedulePatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    days_of_week: list[int] | None = None
    wake_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    bed_time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")
    stages: list[StagePatch] | None = None
    cooling_speed: Mode | None = None


class TemperatureBody(BaseModel):
    target_c: int


class ModeBody(BaseModel):
    mode: Mode


class RehearsalBody(BaseModel):
    #: Bounded at both ends. Below the floor the stages cannot finish their own
    #: presses; above it this stops being a test you stand and watch.
    seconds: int = Field(default=300, ge=120, le=1800)


class NewProfile(BaseModel):
    #: Long enough to say "Summer, spare room", short enough to fit a list row.
    name: str = Field(min_length=1, max_length=40)


# --- Reads --------------------------------------------------------------------


@router.get("/info")
async def get_info(request: Request) -> dict[str, object]:
    service = _service(request)
    return {
        "fake_transmitter": service.settings.transmitter == "fake",
        "fake_power_monitor": service.settings.power_monitor == "fake",
        "max_temperature_c": service.settings.max_temperature_c,
        "notifications": service.notifier.enabled,
        # Changes whenever the built frontend does. The app watches it and
        # reloads itself rather than carrying on with the code it loaded days
        # ago against a service that has moved on.
        "build": getattr(request.app.state, "build", "dev"),
    }


@router.get("/state")
async def get_state(request: Request) -> dict[str, object]:
    return state_json(_service(request).state)


@router.get("/schedule")
async def get_schedule(request: Request) -> dict[str, object]:
    return _service(request).schedule_as_shown()


@router.get("/events")
async def get_events(request: Request, limit: int = 100) -> list[dict[str, object]]:
    return [e.as_dict() for e in _service(request).events.recent(min(limit, 500))]


@router.get("/health")
async def get_health(request: Request) -> list[dict[str, object]]:
    """One entry per thing that can independently stop working.

    The service itself is not in here on purpose. If this request answered at
    all, the service is up; whether the app can reach it is something only the
    app can know, and it knows it from whether its live socket is connected.
    """
    return health_json(_service(request).health())


@router.get("/power")
async def get_power(request: Request, hours: int = 24) -> list[dict[str, object]]:
    """What the unit drew, and what the bed was doing while it drew it.

    The degrees are null on any beat the probe board was quiet, and on every beat
    recorded before the probes existed. Null rather than a carried-forward value,
    because a chart that draws a flat line through a gap is a chart that lies
    about the one thing it was built to show.
    """
    service = _service(request)
    since = service.clock.now() - timedelta(hours=min(hours, 168))
    return [
        {
            "at": s.at.isoformat(),
            "watts": s.watts,
            "flow_c": s.flow_c,
            "return_c": s.return_c,
            "room_c": s.room_c,
        }
        for s in service.db.night_history(since)
    ]


# --- Writes -------------------------------------------------------------------


@router.put("/schedule")
async def put_schedule(request: Request, patch: SchedulePatch) -> dict[str, object]:
    """Saves locally. Deliberately does NOT touch the unit."""
    service = _service(request)
    data = patch.model_dump(exclude_none=True)

    if "cooling_speed" in data and not Mode(data["cooling_speed"]).is_cooling:
        raise HTTPException(422, "cooling_speed must be one of quiet, standard or turbo")

    if "stages" in data:
        speed = Mode(data.get("cooling_speed", service.schedule.cooling_speed))
        seen = set()
        for raw in data["stages"]:
            stage = Stage(raw["stage"])
            if stage in seen:
                raise HTTPException(422, f"{stage.value} appears twice")
            seen.add(stage)
        data["stages"] = [
            SleepStage(Stage(r["stage"]), r["duration_minutes"], r["temp_c"])
            for r in data["stages"]
        ]
        # Which mode a stage lands in depends on the stage before it, so the night
        # is resolved as a sequence and each temperature checked against the range
        # of the mode it actually ends up in.
        for stage, mode in zip(data["stages"], modes_for(data["stages"], speed), strict=True):
            _guard_temperature(service, stage.temp_c, mode)

    for field in ("wake_time", "bed_time"):
        if field in data:
            hour, minute = (int(p) for p in data[field].split(":"))
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise HTTPException(422, f"{field} must be a real time of day")
            data[field] = time(hour, minute)

    # Bedtime and the wake time fix how long the night is, and every stage needs
    # room to exist inside it. Checked before the schedule is built, because the
    # schedule itself would silently squeeze them to fit.
    would_be = replace(service.schedule, **data)
    floor = MIN_STAGE_MINUTES * len(would_be.stages)
    if minutes_between(would_be.bed_time, would_be.wake_time) < floor:
        raise HTTPException(
            422,
            f"A night of {len(would_be.stages)} stages needs at least {floor} minutes "
            f"between going to bed and waking up.",
        )

    if "days_of_week" in data and any(d < 0 or d > 6 for d in data["days_of_week"]):
        raise HTTPException(422, "days_of_week must be 0 (Monday) to 6 (Sunday)")

    service.update_schedule(data)
    return service.schedule_as_shown()


@router.post("/power/on")
async def post_power_on(request: Request) -> dict[str, object]:
    service = _service(request)
    await service.power_on()
    return state_json(service.state)


@router.post("/power/press")
async def post_power_press(request: Request) -> dict[str, object]:
    """One press of power, which is what the button in the app sends.

    Deliberately not power/on or power/off. Those are absolute, verified against
    the plug, and right for the schedule. This is the remote's button, for
    someone standing in front of the bed who can see the answer for themselves.
    """
    service = _service(request)
    await service.press_power()
    return state_json(service.state)


@router.post("/power/off")
async def post_power_off(request: Request) -> dict[str, object]:
    service = _service(request)
    await service.power_off()
    return state_json(service.state)


@router.post("/temperature")
async def post_temperature(request: Request, body: TemperatureBody) -> dict[str, object]:
    service = _service(request)
    # Asked of the service, so the guard checks the range of the mode the command
    # will really use rather than one worked out a second way.
    _guard_temperature(service, body.target_c, service.mode_for_now(body.target_c))

    if not service.state.can_set_temperature:
        raise HTTPException(409, "The unit ignores every button but power while it is off.")
    try:
        await service.set_temperature(body.target_c)
    except CommandFailed as exc:
        raise HTTPException(502, str(exc)) from exc
    return state_json(service.state)


@router.post("/rehearsal")
async def post_rehearsal(request: Request, body: RehearsalBody) -> dict[str, object]:
    """Run tonight's whole night, compressed, starting now.

    For answering the one question the simulator cannot: does every stage
    boundary really land on the unit. Same scheduler, same sequences, same plug
    checks, short durations.
    """
    service = _service(request)
    try:
        plan = await service.start_rehearsal(body.seconds)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "ends_at": plan.wake_at.isoformat(),
        "steps": [
            {
                "stage": step.stage.value,
                "starts_at": step.starts_at.isoformat(),
                "temp_c": step.temp_c,
                "mode": step.mode.value,
            }
            for step in plan.steps
        ],
    }


@router.delete("/rehearsal")
async def delete_rehearsal(request: Request) -> dict[str, object]:
    """Stop early. Always leaves the unit off, which is where the night ends."""
    service = _service(request)
    await service.stop_rehearsal()
    return state_json(service.state)


@router.post("/notify/test")
async def post_notify_test(request: Request) -> dict[str, object]:
    """Send one notification on demand.

    Setting a topic and hoping is not the same as knowing it arrives, and the
    first real notification should not be the one at 2am.
    """
    service = _service(request)
    if not await service.notifier.test():
        raise HTTPException(
            409, "No notification topic set. Add HS_NTFY_TOPIC to .env and restart."
        )
    return {"sent": True}


# --- Saved nights ---------------------------------------------------------------


@router.get("/profiles")
async def get_profiles(request: Request) -> list[dict[str, object]]:
    service = _service(request)
    return [profile_json(p, service.schedule) for p in service.db.profiles()]


@router.post("/profiles")
async def post_profile(request: Request, body: NewProfile) -> list[dict[str, object]]:
    """Save the night the schedule is currently holding, under a name.

    Snapshots what is there rather than taking stages in the body, because the
    thing anyone wants to save is the night they have just finished tuning.
    """
    service = _service(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "A profile needs a name")
    service.db.save_profile(name, service.schedule, service.clock.now())
    return [profile_json(p, service.schedule) for p in service.db.profiles()]


@router.post("/profiles/{profile_id}/activate")
async def post_activate_profile(request: Request, profile_id: int) -> dict[str, object]:
    """Copy a saved night into the schedule.

    Only the stages and the cooling speed. The wake time and the days of the week
    stay exactly as they are: loading "Summer" should never move an alarm.
    """
    service = _service(request)
    profile = service.db.profile(profile_id)
    if profile is None:
        raise HTTPException(404, "No profile with that id")
    for stage in profile.stages:
        _guard_temperature(
            service,
            stage.temp_c,
            mode_for_target(stage.temp_c, profile.cooling_speed),
        )
    service.update_schedule(
        {"stages": list(profile.stages), "cooling_speed": profile.cooling_speed}
    )
    return service.schedule_as_shown()


@router.delete("/profiles/{profile_id}")
async def delete_profile(request: Request, profile_id: int) -> list[dict[str, object]]:
    service = _service(request)
    if not service.db.delete_profile(profile_id):
        raise HTTPException(404, "No profile with that id")
    return [profile_json(p, service.schedule) for p in service.db.profiles()]


@router.post("/blaster/restart")
async def post_blaster_restart(request: Request) -> dict[str, object]:
    """Restart the blaster board, for when it is answering but not transmitting.

    Infrared is one-way, so nothing here can tell a working transmitter from a
    wedged one. This does not diagnose that, it just makes the cure reachable
    from a phone instead of from behind the bed.
    """
    service = _service(request)
    try:
        await service.reboot_blaster()
    except CommandFailed as exc:
        raise HTTPException(502, str(exc)) from exc
    return state_json(service.state)


@router.post("/mute")
async def post_mute(request: Request) -> dict[str, object]:
    """Toggle the unit's button beep. A one-time setup action, never automatic.

    The unit remembers this across power cycles, so firing it on a schedule would
    unmute it every other night.
    """
    service = _service(request)
    if not service.state.can_set_temperature:
        raise HTTPException(409, "The unit ignores every button but power while it is off.")
    await service.mute()
    return state_json(service.state)


@router.post("/mode")
async def post_mode(request: Request, body: ModeBody) -> dict[str, object]:
    service = _service(request)
    # The same guard the temperature and mute routes have always had, and this
    # one was missing it. A mode press on a unit that is off does nothing at all,
    # because power is the only button it answers, but the app would have gone on
    # to believe the mode had changed. Believing something unconfirmed is the one
    # thing this project is built not to do.
    if not service.state.can_set_temperature:
        raise HTTPException(409, "The unit ignores every button but power while it is off.")
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
