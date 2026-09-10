"""The temperature probes, over the ESPHome native API.

Three DS18B20s on one wire, on a second ESP32 board of their own:

    water_flow    the hose going to the bed
    water_return  the hose coming back
    room          air temperature, away from the bed

This is the second real measurement in the project and the first about the bed
rather than about the machine. Everything else the app holds is belief, because
infrared is one-way: `DeviceState` says so in its own field names, where one
value is prefixed `observed_` and the rest are `assumed_`.

Sensors push rather than answer. Unlike the transmitter, which asks the board to
do something and waits, this subscribes once and the board sends a reading
whenever it takes one. So the adapter's job is to hold the latest of each, with
the time it arrived, and to be honest about staleness rather than serving a
number from an hour ago as though it were current.

Nothing here is allowed to affect a night. A probe board that has fallen off the
Wi-Fi means the app knows less, not that it stops working: the schedule ran for
weeks before these existed and must carry on running if they go away.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..clock import Clock

log = logging.getLogger(__name__)

#: The entity names the board publishes. Set by scripts/probes.py, so a mismatch
#: here means one of the two was changed without the other.
FLOW, RETURN, ROOM = "water_flow", "water_return", "room"
NAMES = (FLOW, RETURN, ROOM)

#: How old a reading may be before it stops counting as current.
#:
#: Past this the value is not wrong, it is simply not news, and the difference
#: matters when something downstream is about to decide which mode to run on the
#: strength of it.
#:
#: This has to be read against how often the board actually publishes, and that
#: is the pair of numbers this constant got wrong for a fortnight. A sensor read
#: every 30s through a median with send_every: 5 does not report every 30s, it
#: reports every 150s, and the room probe reported every 180s. Both were longer
#: than the two minutes this used to allow, so every reading expired before its
#: replacement arrived and the probes flickered in and out all evening. The event
#: log caught it within ten minutes of being added, which is the entire argument
#: for saying things out loud rather than colouring a dot in.
#:
#: The board publishes on every read now: 30s for the hoses, 60s for the room. So
#: three minutes is six missed reports on the hoses and three on the room, which
#: is a real outage rather than one unlucky reading.
STALE_AFTER = timedelta(minutes=3)

#: How long to wait before trying the connection again after it drops.
RECONNECT_AFTER = 30.0

#: How long the board may say nothing at all before the link counts as dead.
#:
#: This adapter never sends anything. It subscribes once and waits, which means
#: it has no request that could fail and no exception to catch when the socket
#: quietly dies. aioesphomeapi does not reconnect on its own and does not tell us
#: it has stopped: the client object carries on existing and looking healthy. The
#: transmitter survives that because every press is a real request that raises;
#: here, silence is the only symptom there is.
#:
#: Five minutes is ten missed reports on the fastest sensor, so it cannot be one
#: unlucky reading, and it is comfortably past STALE_AFTER, so the app has
#: already stopped believing the numbers well before anything is torn down. That
#: ordering is deliberate: rebuilding a link that is merely slow would turn a
#: board that is coping into one that never finishes connecting.
SILENT_TOO_LONG = timedelta(minutes=5)

#: How often to check for that silence. Cheap, and nothing is waiting on it.
CHECK_EVERY = 10.0


@dataclass
class Reading:
    celsius: float
    at: datetime


class Probes:
    """Latest readings from the probe board, or nothing if it is not there."""

    def __init__(
        self,
        clock: Clock,
        host: str,
        port: int = 6053,
        encryption_key: str = "",
        *,
        connect_timeout: float = 10.0,
    ) -> None:
        self.clock = clock
        self.host = host
        self.port = port
        self.encryption_key = encryption_key
        self.connect_timeout = connect_timeout
        self.readings: dict[str, Reading] = {}
        self.connected = False
        #: When anything last arrived from the board, whichever sensor it was.
        #: Separate from the readings themselves: a board reporting only the room
        #: probe is a wiring problem, and a board reporting nothing at all is a
        #: link problem, and the two want different answers.
        self.last_reading_at: datetime | None = None
        #: How many times the link has had to be built again since start. Shown
        #: on the device bar, because a board that reconnects every few minutes
        #: is a Wi-Fi problem long before it becomes a missing reading.
        self.rebuilds = 0
        self._client = None
        self._keys: dict[int, str] = {}
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.host)

    # --- What the rest of the app asks --------------------------------------

    def fresh(self, name: str) -> float | None:
        """A reading, or None if it is missing or too old to be called current."""
        reading = self.readings.get(name)
        if reading is None:
            return None
        if self.clock.now() - reading.at > STALE_AFTER:
            return None
        return reading.celsius

    @property
    def flow_c(self) -> float | None:
        return self.fresh(FLOW)

    @property
    def return_c(self) -> float | None:
        return self.fresh(RETURN)

    @property
    def room_c(self) -> float | None:
        return self.fresh(ROOM)

    @property
    def moving_c(self) -> float | None:
        """Return minus flow: the heat actually going into the bed, or out of it.

        Positive means the bed is putting heat into the water, so it wants
        cooling. Negative means the water is giving heat up to the bed. Around
        zero means nothing is moving and the bed is at temperature.

        This is the number a probe taped under a sheet could never give, because
        it does not depend on where anything was placed or whether someone is
        lying on it. It is the exchange itself.

        The sign depends on which probe went on which hose, and that is decided
        with a roll of tape behind a bed. Anything reading the sign should check
        it against a known mode first: heating with the return warmer than the
        flow means the two are the wrong way round, not that the physics is. The
        size is safe either way, which is why the pre-conditioning rule uses only
        the size.
        """
        flow, back = self.flow_c, self.return_c
        if flow is None or back is None:
            return None
        return round(back - flow, 2)

    @property
    def bed_c(self) -> float | None:
        """The closest thing to the bed's own temperature.

        The return hose, because that water has just been through the bed and
        carries whatever the bed did to it. The flow hose if the return probe is
        not reporting: that is the unit's own water rather than the bed's, and a
        degree or two off, but it is a great deal better than nothing.
        """
        back = self.return_c
        return back if back is not None else self.flow_c

    def missing(self) -> list[str]:
        """Which probes are not reporting anything current."""
        return [name for name in NAMES if self.fresh(name) is None]

    @property
    def quiet_for(self) -> timedelta | None:
        """How long since anything at all arrived, or None if nothing ever has."""
        if self.last_reading_at is None:
            return None
        return self.clock.now() - self.last_reading_at

    # --- Staying connected ---------------------------------------------------

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._stay_connected(), name="probes")

    async def _stay_connected(self) -> None:
        """Connect, subscribe, and keep doing so.

        A loop rather than a one-off, because the board is on the far side of a
        bedroom Wi-Fi link and the failure that cost a night in September was a
        connection that dropped and was never rebuilt.
        """
        while True:
            try:
                await self._connect()
                await self._until_it_goes_quiet()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("probe board at %s: %s", self.host, exc)
            self.connected = False
            await self._drop()
            await self.clock.sleep(RECONNECT_AFTER)

    async def _until_it_goes_quiet(self) -> None:
        """Sit on a live subscription until it stops delivering, then say so.

        `self.connected` is not the thing to watch. It is set when the subscribe
        call returns and nothing ever clears it, because nothing here would
        notice: the library pushes states on its own task and raises nothing when
        that task's socket dies. Waiting on that flag is waiting forever, at one
        wakeup a second, on a link that stopped working hours ago.

        So the flag is not the signal. Silence is.
        """
        while self.connected:
            # The clock rather than asyncio, so a test can drive three minutes of
            # silence without spending three minutes on it.
            await self.clock.sleep(CHECK_EVERY)
            quiet = self.quiet_for
            if quiet is not None and quiet > SILENT_TOO_LONG:
                self.rebuilds += 1
                raise TimeoutError(
                    f"nothing for {int(quiet.total_seconds())}s, rebuilding the link"
                )

    async def _connect(self) -> None:
        from aioesphomeapi import APIClient, SensorInfo

        client = APIClient(
            self.host, self.port, password=None, noise_psk=self.encryption_key or None
        )
        await asyncio.wait_for(client.connect(login=True), timeout=self.connect_timeout)
        self._client = client

        entities, _ = await client.list_entities_services()
        self._keys = {
            entity.key: entity.name
            for entity in entities
            if isinstance(entity, SensorInfo) and entity.name in NAMES
        }
        if not self._keys:
            raise RuntimeError(
                f"connected but found none of {', '.join(NAMES)}. "
                "Reflash with scripts/probes.py --flow ... --return ... --room ..."
            )

        client.subscribe_states(self._on_state)
        self.connected = True
        # Start the silence clock here rather than leaving it wherever the last
        # connection left it, so a fresh link gets a full window to deliver
        # something before it is torn down again.
        self.last_reading_at = self.clock.now()
        log.info("probe board at %s: %d sensors", self.host, len(self._keys))

    def _on_state(self, state: object) -> None:
        """Called by the library whenever the board sends a reading.

        Never raises. This runs on the library's own task, and an exception here
        would take the subscription down rather than being reported anywhere
        useful.
        """
        try:
            name = self._keys.get(state.key)  # type: ignore[attr-defined]
            if name is None:
                return
            value = state.state  # type: ignore[attr-defined]
            if value is None or value != value:  # NaN fails this
                return
            now = self.clock.now()
            self.last_reading_at = now
            self.readings[name] = Reading(round(float(value), 2), now)
        except Exception:  # noqa: BLE001  # pragma: no cover
            log.debug("could not read a probe state", exc_info=True)

    async def _drop(self) -> None:
        if self._client is None:
            return
        with contextlib.suppress(Exception):
            await self._client.disconnect()
        self._client = None
        self._keys = {}

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._drop()
        self.connected = False
