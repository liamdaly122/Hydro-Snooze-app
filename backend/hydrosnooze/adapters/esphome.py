"""The real infrared transmitter, over the ESPHome native API.

NOT YET TESTED AGAINST HARDWARE. Nothing here has ever spoken to a XIAO Smart IR
Mate, because at the time of writing one has not arrived. It is written in full
rather than stubbed so that switching to it really is one line of config, but
expect to correct it during step 6 of the roadmap.

It expects the ESPHome device to expose one button entity per remote button, with
object ids matching the names in `Button`: power, schedule, temp_up, temp_down,
cool, warm, timer, mute. See docs/esphome-hydrosnooze.yaml for a configuration
template to fill in with the captured codes.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging

from ..models import Button

log = logging.getLogger(__name__)


class TransmitterError(RuntimeError):
    pass


class EsphomeTransmitter:
    def __init__(
        self,
        host: str,
        port: int = 6053,
        encryption_key: str = "",
        *,
        connect_timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.encryption_key = encryption_key
        self.connect_timeout = connect_timeout
        self._client = None
        self._buttons: dict[str, int] = {}
        self._button_info: type = object
        self._lock = asyncio.Lock()

    async def _connect(self) -> None:
        if self._client is not None:
            return
        try:
            from aioesphomeapi import APIClient, ButtonInfo
        except ImportError as exc:  # pragma: no cover
            raise TransmitterError(
                "aioesphomeapi is not installed. Run: uv pip install -e '.[hardware]'"
            ) from exc

        client = APIClient(
            self.host,
            self.port,
            password=None,
            noise_psk=self.encryption_key or None,
        )
        await asyncio.wait_for(client.connect(login=True), timeout=self.connect_timeout)
        self._client = client
        self._button_info = ButtonInfo
        await self._load_buttons()

    async def _load_buttons(self) -> None:
        """Read the entity keys off the board. Also the cheapest real request
        there is, which is what makes it a usable health check."""
        assert self._client is not None
        entities, _services = await self._client.list_entities_services()
        self._buttons = {
            entity.object_id: entity.key
            for entity in entities
            if isinstance(entity, self._button_info)
        }
        missing = [b.value for b in Button if b.value not in self._buttons]
        if missing:
            log.warning(
                "ESPHome device %s is missing button entities: %s", self.host, ", ".join(missing)
            )

    async def _drop(self) -> None:
        """Throw the connection away so the next attempt builds a fresh one."""
        client, self._client, self._buttons = self._client, None, {}
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def reachable(self) -> bool:
        """Ask the board something and see whether it answers.

        This used to trust a cached button list, which meant it reported healthy
        for as long as the process had ever connected, including all night after
        the connection had dropped. The device bar showed green on a link that
        could not send a single press.

        So it makes a real request now. That proves the socket is alive rather
        than merely once having been, and it re-reads the entity keys, which
        change when the board is reflashed and would otherwise go stale.
        """
        try:
            async with self._lock:
                await self._connect()
                await self._load_buttons()
                return all(b.value in self._buttons for b in Button)
        except Exception as exc:
            log.debug("blaster unreachable: %r", exc)
            await self._drop()
            return False

    async def press(self, button: Button, note: str = "") -> None:
        """Send one press, reconnecting once if the link has gone.

        Presses are hours apart and the board is on Wi-Fi, so the connection
        being dead by the time one is needed is the normal case rather than the
        exception. aioesphomeapi does not reconnect on its own: it raises
        "Not connected", and every later press raises the same thing forever,
        because the client object still exists and looks fine.
        """
        async with self._lock:
            try:
                await self._send(button, note)
                return
            except Exception as first:
                log.warning("press %s failed (%r), reconnecting", button.value, first)
                await self._drop()

            try:
                await self._send(button, note)
            except Exception as second:
                # Always this type, so the layer above has one thing to catch.
                # An exception it does not expect escaping from here is what
                # turned one dropped connection into a stack trace every second
                # for four hours.
                raise TransmitterError(
                    f"Could not send {button.value} to {self.host}: {second}"
                ) from second

    async def _send(self, button: Button, note: str) -> None:
        await self._connect()
        key = self._buttons.get(button.value)
        if key is None:
            raise TransmitterError(
                f"No ESPHome button entity called '{button.value}' on {self.host}. "
                "Capture the code and add it to the device configuration."
            )
        assert self._client is not None
        result = self._client.button_command(key)
        # Older releases of aioesphomeapi return None here, newer ones return
        # a coroutine. Cope with both rather than pinning a version.
        if inspect.isawaitable(result):
            await result
        log.debug("sent %s %s", button.value, note)

    async def close(self) -> None:
        await self._drop()
