"""The FastAPI application.

Serves the API, the live feed, and the built frontend as static files. One
process, one port, no reverse proxy, because on a Pi that is one fewer thing to
go wrong at 3am.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import dev, routes
from .api.schemas import health_json, schedule_json, state_json
from .config import get_settings
from .service import Service

logging.basicConfig(
    level=os.environ.get("HS_LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
)
# httpx logs a line per request at INFO, which is one every thirty seconds for
# the plug alone, forever. The events this service records are the signal; that
# is noise, and on the Pi it would bury `journalctl -u hydrosnooze -f` at 3am.
logging.getLogger("httpx").setLevel(logging.WARNING)


class QuietExpectedDisconnects(logging.Filter):
    """Drop one specific stack trace for a condition that is handled.

    When the blaster goes away mid-connection, aioesphomeapi tries to hang up
    politely, gets no answer, and logs a full traceback at ERROR. That is not an
    error here. It is what unplugging the blaster looks like from the inside, the
    reconnect deals with it, and the health check reports it in words.

    A journal already full of tracebacks for expected things is a journal in
    which a real one is harder to see, and this one is read at 7am after a bad
    night. Everything else aioesphomeapi has to say still comes through.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "disconnect request failed" not in record.getMessage()


logging.getLogger("aioesphomeapi.connection").addFilter(QuietExpectedDisconnects())

log = logging.getLogger("hydrosnooze")


def build_id() -> str:
    """Something that changes whenever the built frontend does.

    The app compares this against the one it started with and reloads itself
    when they differ. Without it a phone with the app already open keeps running
    the JavaScript it loaded days ago: a deploy replaces the files and restarts
    the service, and the page in front of you carries on calling endpoints that
    changed underneath it. That cost an evening twice, once looking for a bug in
    a power button that had already been fixed.

    Hashed from index.html rather than from a version number, because index.html
    names the hashed asset bundles and so changes on every build that changes
    anything, and never on one that does not.
    """
    static = static_dir()
    index = static / "index.html" if static else None
    if index is None or not index.exists():
        return "dev"
    return hashlib.sha256(index.read_bytes()).hexdigest()[:12]


def static_dir() -> Path | None:
    """Where the built frontend lives.

    Built on the Mac and copied across, never built on the Pi.
    """
    override = os.environ.get("HS_STATIC_DIR")
    candidates = [Path(override)] if override else []
    here = Path(__file__).resolve().parent
    candidates += [here.parent.parent / "frontend" / "dist", here.parent / "static"]
    return next((c for c in candidates if (c / "index.html").exists()), None)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    service = Service(settings)
    app.state.service = service
    # Read once here rather than per request: a deploy replaces these files and
    # restarts the service, so startup is exactly when it changes.
    app.state.build = build_id()
    await service.start()
    # Naming the address, not just the mode. "power=shelly" looks like success
    # whether or not the host was ever set, and a missing host falls back to a
    # default that is nobody's real plug.
    transmitter = settings.transmitter
    if transmitter != "fake":
        transmitter += f" at {settings.esphome_host}"
    power = settings.power_monitor
    if power != "fake":
        power += f" at {settings.shelly_host}"
    log.info("HydroSnooze up. transmitter=%s, power=%s", transmitter, power)

    # The scheduler works in naive local time, so a machine on the wrong timezone
    # runs the whole night at the wrong hour and nothing in the app can tell.
    # A Pi also has no battery-backed clock and only knows the time because it
    # asked the network. Both failures are silent and both surface at 2am, so the
    # first thing in the log is what this thinks the time is.
    now = datetime.now().astimezone()
    offset = now.strftime("%z")
    log.info(
        "Local time is %s (%s, UTC%s:%s). Stage times are read in this timezone.",
        now.strftime("%a %d %b %H:%M"),
        now.tzname(),
        offset[:3],
        offset[3:],
    )
    try:
        yield
    finally:
        await service.stop()


app = FastAPI(title="HydroSnooze", lifespan=lifespan)
app.include_router(routes.router)
app.include_router(dev.router)


@app.websocket("/api/live")
async def live(websocket: WebSocket) -> None:
    """Push state changes so the app is never stale."""
    service: Service = websocket.app.state.service
    await websocket.accept()
    queue = service.subscribe()
    try:
        await websocket.send_json(
            {
                "state": state_json(service.state),
                "schedule": schedule_json(service.schedule),
                "health": health_json(service.health()),
            }
        )
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=25)
            except asyncio.TimeoutError:
                # Keep the connection alive through a phone's idle timeouts.
                await websocket.send_json({"ping": True})
                continue
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover
        log.debug("live socket closed", exc_info=True)
    finally:
        service.unsubscribe(queue)
        with contextlib.suppress(Exception):
            await websocket.close()


_static = static_dir()
if _static is not None:
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str) -> FileResponse:
        """Serve the app shell, and any file next to it, but never for /api."""
        candidate = (_static / path).resolve()
        if path and _static in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_static / "index.html")

else:  # pragma: no cover
    log.warning("No built frontend found. Run: npm --prefix frontend run build")
