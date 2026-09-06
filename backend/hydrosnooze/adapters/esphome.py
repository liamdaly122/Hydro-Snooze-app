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
        entities, _services = await client.list_entities_services()
        self._buttons = {
            entity.object_id: entity.key for entity in entities if isinstance(entity, ButtonInfo)
        }
        missing = [b.value for b in Button if b.value not in self._buttons]
        if missing:
            log.warning(
                "ESPHome device %s is missing button entities: %s", self.host, ", ".join(missing)
            )
        self._client = client

    async def press(self, button: Button, note: str = "") -> None:
        async with self._lock:
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
        if self._client is not None:
            await self._client.disconnect()
            self._client = None
            self._buttons = {}
