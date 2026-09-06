"""The real power monitor, over the Shelly Gen3 local HTTP RPC.

NOT YET TESTED AGAINST HARDWARE, for the same reason as the transmitter.

Reads `Switch.GetStatus` and returns the `apower` field, which is the instantaneous
draw in watts. Everything is local: no Shelly cloud account, no internet.

A plug that cannot be reached returns None rather than zero. That distinction
matters, because zero watts means the unit is off and None means we do not know,
and the app is built to say "unknown" instead of guessing.
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)


class ShellyPowerMonitor:
    def __init__(self, host: str, *, timeout: float = 4.0, switch_id: int = 0) -> None:
        self.host = host
        self.switch_id = switch_id
        self._client = httpx.AsyncClient(timeout=timeout)

    async def read_watts(self) -> float | None:
        url = f"http://{self.host}/rpc/Switch.GetStatus"
        try:
            response = await self._client.get(url, params={"id": self.switch_id})
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Shelly at %s unreachable: %s", self.host, exc)
            return None

        watts = payload.get("apower")
        if watts is None:
            log.warning("Shelly at %s returned no apower field: %s", self.host, payload)
            return None
        return float(watts)

    async def close(self) -> None:
        await self._client.aclose()
