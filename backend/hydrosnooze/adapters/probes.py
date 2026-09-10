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
#: The board reports flow and return every 30s and the room every 60s, so two
#: minutes is several missed readings rather than one unlucky one. Past this the
#: value is not wrong, it is simply not news, and the difference matters when
#: something downstream is about to decide which mode to run on the strength of
#: it.
STALE_AFTER = timedelta(minutes=2)

#: How long to wait before trying the connection again after it drops.
RECONNECT_AFTER = 30.0


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
                # _connect returns once subscribed. The library delivers states
                # on its own task from here, so there is nothing to poll.
                while self.connected:
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("probe board at %s: %s", self.host, exc)
            self.connected = False
            await self._drop()
            await asyncio.sleep(RECONNECT_AFTER)

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
            self.readings[name] = Reading(round(float(value), 2), self.clock.now())
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
