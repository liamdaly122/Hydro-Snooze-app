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

The same board carries the three bedside buttons, so there are two jobs in here
now. The probes say what the bed is doing and the buttons say what I want, and
they are kept apart on purpose: one is a measurement and the other is a request,
and the check that catches a board with dead probes must not be satisfied by a
working button. See docs/buttons.md for how they are wired.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..clock import Clock

log = logging.getLogger(__name__)

#: The entity names the board publishes. Set by scripts/probes.py, so a mismatch
#: here means one of the two was changed without the other.
FLOW, RETURN, ROOM = "water_flow", "water_return", "room"
NAMES = (FLOW, RETURN, ROOM)

#: The three bedside buttons, on the same board, published as binary sensors
#: rather than sensors. Also set by scripts/probes.py, so the same rule applies:
#: a mismatch here means one of the two was changed without the other.
#:
#: They are a physical input, which in ESPHome is a `binary_sensor:`. The thing
#: called `button:` is a software control on a web page, which is not this.
BUTTON_WARMER = "button_warmer"
BUTTON_COOLER = "button_cooler"
BUTTON_POWER = "button_power"
BUTTON_NAMES = (BUTTON_WARMER, BUTTON_COOLER, BUTTON_POWER)

#: How hard the board is having to shout. Published by the board since the day it
#: was flashed and read by nothing until the buttons went on.
#:
#: It was worth ignoring while this link only carried temperatures, because a
#: missing reading is something the app can say out loud and work around. It is
#: not worth ignoring now. A press that the board registers perfectly and cannot
#: deliver is a button that does nothing at 3am, and the only warning of that is
#: this number falling.
RSSI = "wifi_rssi"

#: Which access point the board actually joined.
#:
#: The house has a hub and two boosters, and the board is configured to prefer
#: the hub and fall back to a booster. That fallback is what makes the
#: arrangement safe, and it is also what makes "which one is it on" a real
#: question rather than a setting anyone can read off. Without this the only way
#: to answer it was a serial cable and a boot banner.
NETWORK = "wifi_network"

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
        on_button: Callable[[str], None] | None = None,
    ) -> None:
        self.clock = clock
        self.host = host
        self.port = port
        self.encryption_key = encryption_key
        self.connect_timeout = connect_timeout
        #: Called once per press, with the button's name. Never called for a
        #: release. Runs on the library's own task, so it has to return quickly
        #: and must not raise: see `_on_button`.
        self.on_button = on_button
        self.readings: dict[str, Reading] = {}
        #: The last signal strength the board reported, in dBm, and when. Kept
        #: apart from `readings` on purpose: it is about the link rather than
        #: about the bed, and `missing()` asking after it would turn a board on a
        #: weak link into a board with a broken probe.
        self.signal_dbm: float | None = None
        self.signal_at: datetime | None = None
        #: The SSID the board is on, or None if it has not said. A text sensor
        #: rather than a number, which is why it is kept apart from the rest.
        self.network: str | None = None
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
        #: Presses heard since the service started. Not a reading and not used to
        #: decide anything, but it answers "did the board hear that" without
        #: reading a log, which is the first question when a button does nothing.
        self.presses = 0
        self._client = None
        self._keys: dict[int, str] = {}
        self._signal_key: int | None = None
        self._network_key: int | None = None
        self._buttons: dict[int, str] = {}
        #: What each button was last seen doing, so a release is not a press.
        #:
        #: Emptied on every connect. The library replays the current state of
        #: every entity when a subscription comes up, and that replay is not
        #: somebody's finger: a button missing from here has not been seen on
        #: this connection yet, and its first report is recorded and nothing
        #: else. Otherwise reconnecting at 3am on a weak link would set the bed
        #: going on its own.
        self._button_state: dict[str, bool] = {}
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
    def buttons_found(self) -> int:
        """How many of the three bedside buttons the board is publishing.

        Zero on a board flashed before they existed, which is not a fault: the
        probes are the reason this link exists and they work either way.
        """
        return len(self._buttons)

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
        from aioesphomeapi import (
            APIClient,
            BinarySensorInfo,
            SensorInfo,
            TextSensorInfo,
        )

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
        # A second dictionary rather than three more entries in the first, and
        # that is the whole reason it exists. The guard below means "I have
        # connected to a board that is not the probe board". Let buttons into
        # `_keys` and a board with three working buttons and three dead probes
        # sails past the one check written to catch it.
        self._buttons = {
            entity.key: entity.name
            for entity in entities
            if isinstance(entity, BinarySensorInfo) and entity.name in BUTTON_NAMES
        }
        self._button_state = {}
        self._signal_key = next(
            (entity.key for entity in entities if entity.name == RSSI), None
        )
        self._network_key = next(
            (
                entity.key
                for entity in entities
                if isinstance(entity, TextSensorInfo) and entity.name == NETWORK
            ),
            None,
        )
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
        log.info(
            "probe board at %s: %d sensors, %d buttons",
            self.host,
            len(self._keys),
            len(self._buttons),
        )

    def _on_state(self, state: object) -> None:
        """Called by the library whenever the board sends a reading.

        Never raises. This runs on the library's own task, and an exception here
        would take the subscription down rather than being reported anywhere
        useful.
        """
        try:
            key = state.key  # type: ignore[attr-defined]
            if key == self._signal_key:
                self._on_signal(state)
                return
            if key == self._network_key:
                # Same reasoning as the signal and the buttons: about the link,
                # not about the bed, so it does not touch the probe clocks.
                said = getattr(state, "state", None)
                self.network = str(said) if said else None
                return
            if key in self._buttons:
                self._on_button(self._buttons[key], state)
                return
            name = self._keys.get(key)
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

    def _on_signal(self, state: object) -> None:
        """How hard the board is shouting.

        Like a press, this does not touch `last_reading_at`. That clock is what
        decides whether the probes have gone quiet enough to rebuild the link,
        and a board reporting its own signal while saying nothing about the bed
        is still a board with a problem worth naming.
        """
        value = getattr(state, "state", None)
        if value is None or value != value:  # NaN fails this
            return
        self.signal_dbm = round(float(value))
        self.signal_at = self.clock.now()

    def _on_button(self, name: str, state: object) -> None:
        """A bedside button changed. Act on the press, ignore everything else.

        Deliberately does not touch `last_reading_at`. That clock decides whether
        the link has gone quiet enough to tear down and rebuild, and it is a
        question about the probes. A board whose 1-wire bus has died is still a
        board the app should be complaining about, and a press proving the Wi-Fi
        is fine would quietly stop it complaining.
        """
        down = bool(getattr(state, "state", False))
        was = self._button_state.get(name)
        self._button_state[name] = down
        if was is None:
            # First report on this connection: the library saying what it holds,
            # not a finger. Recorded, and nothing else.
            return
        if not down or was:
            # The release is a state too, and the board can repeat one. Either
            # way through here doubles a press, which at a degree each is a bed
            # two degrees out from what somebody asked for.
            return
        self.presses += 1
        log.info("bedside button: %s", name)
        if self.on_button is None:
            return
        # Whatever this does, it does it without taking the subscription down
        # with it. Same rule as the caller, one floor up.
        try:
            self.on_button(name)
        except Exception:  # noqa: BLE001  # pragma: no cover
            log.warning("could not act on %s", name, exc_info=True)

    async def _drop(self) -> None:
        if self._client is None:
            return
        with contextlib.suppress(Exception):
            await self._client.disconnect()
        self._client = None
        self._keys = {}
        self._signal_key = None
        self._network_key = None
        self._buttons = {}
        self._button_state = {}

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._drop()
        self.connected = False
