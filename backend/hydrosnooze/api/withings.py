"""Withings and the Health Report, over HTTP.

A router of its own, the way the loop is a module of its own: nothing the bed
does is reachable from here, and nothing here can reach the bed.

    GET    /api/withings            where the connection is up to
    GET    /api/withings/connect    sends the browser to sign in
    GET    /api/withings/callback   where Withings sends it back
    POST   /api/withings/sync       fetch now, at most every ten minutes
    DELETE /api/withings            disconnect, keeping the nights
    GET    /api/health-report       one night, as the Health Report draws it
    GET    /api/sleep-timing        when I really sleep, against the schedule's parts
    POST   /api/sleep-timing/forget start counting nights again
    GET    /api/scoreboard          each setting each part has run at, and the sleep on it
    GET    /api/suggestion          tonight's suggested Deep and REM, and where it is up to
    POST   /api/suggestion/accept   use it for tonight
    POST   /api/suggestion/decline  not tonight
    POST   /api/suggestion/reach    how many degrees either side it may go
"""

from __future__ import annotations

# Aliased: the route takes a parameter called `date`, which is what the app sends.
from datetime import date as _date

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel

from ..sequences import CommandFailed
from ..service import Service
from ..withings import health, scoreboard, timing
from ..withings.sync import ConnectProblem

router = APIRouter(prefix="/api")


def _service(request: Request) -> Service:
    return request.app.state.service


@router.get("/withings")
async def get_withings(request: Request) -> dict[str, object]:
    return _service(request).withings.status()


@router.get("/withings/connect")
async def connect(request: Request) -> Response:
    """A link, not a button's fetch: the browser has to go to Withings itself."""
    service = _service(request)
    # Through Tailscale the way back is the Tailscale address, which has to be
    # registered with Withings like the other two. See docs/tailscale.md.
    public = getattr(request.state, "via", "home") == "tailscale"
    try:
        url = service.withings.begin_connect(
            request.headers.get("host", ""),
            public_url=service.settings.public_url if public else None,
            outside=public,
        )
    except ConnectProblem as exc:
        return PlainTextResponse(str(exc), status_code=400)
    return RedirectResponse(url, status_code=302)


@router.get("/withings/callback")
async def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Withings sends the browser here with a code that lasts thirty seconds.

    Back to the app either way, with a word in the address saying how it went.
    What went wrong, if anything, is in /api/withings for the app to show.
    """
    sync = _service(request).withings
    if error or not code or not state:
        sync.refused(f"Withings did not connect: {error or 'no code came back'}.")
        return RedirectResponse("/?withings=failed", status_code=302)
    try:
        await sync.finish_connect(code, state)
    except ConnectProblem as exc:
        sync.refused(str(exc))
        return RedirectResponse("/?withings=failed", status_code=302)
    sync.sync_soon()
    return RedirectResponse("/?withings=connected", status_code=302)


@router.post("/withings/sync")
async def sync_now(request: Request) -> dict[str, object]:
    """Fetch now. `asked` is false when it was too soon, or already fetching."""
    sync = _service(request).withings
    asked = await sync.sync(manual=True)
    return {"asked": asked, **sync.status()}


@router.delete("/withings")
async def disconnect(request: Request) -> dict[str, object]:
    sync = _service(request).withings
    sync.disconnect()
    return sync.status()


@router.get("/health-report")
async def get_health_report(
    request: Request,
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
) -> dict[str, object]:
    """The night ending on `date`, or the latest one.

    404 when there is no sleep at all yet, the same way /api/autopilot does: a
    screen with a way to say "nothing yet" is better than one drawing zeroes.
    """
    if date is not None:
        # The pattern only checks the shape. 2026-13-45 has the right one.
        try:
            _date.fromisoformat(date)
        except ValueError:
            raise HTTPException(422, f"{date} is not a date.") from None
    built = health.report(_service(request).db, date)
    if built is None:
        raise HTTPException(404, "No sleep from Withings yet.")
    return built


@router.get("/sleep-timing")
async def get_sleep_timing(request: Request) -> dict[str, object]:
    """The recent nights against the schedule as it stands now.

    Always answers, with no nights at all included: "12 more nights" is the
    thing worth saying on the first morning, not a 404.
    """
    service = _service(request)
    return timing.timing(service.db, service.schedule, service.clock.now().date())


@router.post("/sleep-timing/forget")
async def forget_sleep_timing(request: Request) -> dict[str, object]:
    """Start again, for a routine that has changed.

    The nights are kept. They stop counting towards the Sleep timing card, the
    same way Start again on the Learning card sets nights aside without deleting
    a record of a night that happened.
    """
    service = _service(request)
    today = service.clock.now().date()
    set_aside = timing.timing(service.db, service.schedule, today)["nights"]
    timing.start_again(service.db, today)
    service.events.info(
        "sleep_timing",
        f"Sleep timing starting again: {set_aside} "
        f"{'night' if set_aside == 1 else 'nights'} set aside. The nights themselves are kept.",
    )
    return timing.timing(service.db, service.schedule, today)


@router.get("/scoreboard")
async def get_scoreboard(request: Request) -> dict[str, object]:
    """Each temperature each part of the night has run at, and what the mat
    measured on those nights. Always answers, with nothing recorded included."""
    service = _service(request)
    return scoreboard.scoreboard(service.db, service.clock.now().date())


@router.get("/suggestion")
async def get_suggestion(request: Request) -> dict[str, object]:
    """Tonight's suggested Deep and REM. See suggest.py and Service.suggestion."""
    return _service(request).suggestion()


@router.post("/suggestion/accept")
async def accept_suggestion(request: Request) -> dict[str, object]:
    """Use it for tonight. Changes tonight only; the routine is untouched."""
    try:
        return await _service(request).accept_suggestion()
    except CommandFailed as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/suggestion/decline")
async def decline_suggestion(request: Request) -> dict[str, object]:
    try:
        return _service(request).decline_suggestion()
    except CommandFailed as exc:
        raise HTTPException(409, str(exc)) from exc


class ReachBody(BaseModel):
    reach: int


@router.post("/suggestion/reach")
async def set_suggestion_reach(request: Request, body: ReachBody) -> dict[str, object]:
    """How many degrees either side of the usual Deep and REM it may go."""
    try:
        return _service(request).set_suggestion_reach(body.reach)
    except CommandFailed as exc:
        raise HTTPException(422, str(exc)) from exc
