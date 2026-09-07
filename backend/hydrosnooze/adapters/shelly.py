"""The real power monitor, over the Shelly Gen3 local HTTP RPC.

NOT YET TESTED AGAINST HARDWARE, for the same reason as the transmitter.

Reads `Switch.GetStatus` and returns the `apower` field, which is the instantaneous
draw in watts. Everything is local: no Shelly cloud account, no internet.

A plug that cannot be reached returns None rather than zero. That distinction
matters, because zero watts means the unit is off and None means we do not know,
and the app is built to say "unknown" instead of guessing.

Every read is tried twice before giving up. See read_watts.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

#: How long to wait before the one retry. Short, because the caller is usually a
#: command sequence waiting to find out whether the unit came on.
RETRY_GAP_S = 0.5


class ShellyPowerMonitor:
    def __init__(
        self,
        host: str,
        *,
        timeout: float = 4.0,
        switch_id: int = 0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.host = host
        self.switch_id = switch_id
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def read_watts(self) -> float | None:
        """The instantaneous draw, or None if the plug could not be reached.

        Tried twice. The plug sits behind a bed next to a metal chassis, which is
        a poor spot for 2.4GHz, and measured at -87 dBm here: about one read in
        twenty comes back empty. One dropped packet should not become a failed
        power command, because the caller treats an unreachable plug as a reason
        to stop rather than a reason to guess.

        Twice, not a loop. If the plug is genuinely gone, saying so within a
        second beats blocking a button sequence while it hopes.
        """
        url = f"http://{self.host}/rpc/Switch.GetStatus"
        for attempt in (1, 2):
            try:
                response = await self._client.get(url, params={"id": self.switch_id})
                response.raise_for_status()
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == 1:
                    await asyncio.sleep(RETRY_GAP_S)
                    continue
                log.warning("Shelly at %s unreachable, twice: %s", self.host, exc)
                return None

            watts = payload.get("apower")
            if watts is None:
                log.warning("Shelly at %s returned no apower field: %s", self.host, payload)
                return None
            return float(watts)
        return None

    async def close(self) -> None:
        await self._client.aclose()
