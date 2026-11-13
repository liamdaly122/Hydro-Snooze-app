"""The real power monitor, over the Shelly Gen3 local HTTP RPC.

Reads `Switch.GetStatus` and returns the `apower` field, which is the instantaneous
draw in watts. Everything is local: no Shelly cloud account, no internet.

A plug that cannot be reached returns None rather than zero. That distinction
matters, because zero watts means the unit is off and None means we do not know,
and the app is built to say "unknown" instead of guessing.

Every read is tried twice before giving up. See read_watts.

**Named, not numbered.** HS_SHELLY_HOST can be the plug's address or its name
(`shellypluspluguk-<id>.local`). The name is the one to use. The router hands the
plug a new address whenever it likes, and the plug had three in its first month:
.194, then .192, then .193. Each time it moved, the address in .env pointed at
nothing, and the night ran blind until somebody noticed.

A name is looked up once and the address it gives is kept, so an ordinary read
never waits on a lookup. Only when the plug stops answering there is the name
asked again, and if it now points somewhere else the read is tried there before
anything is called unreachable. A lookup that fails changes nothing: the last
address that worked is kept.

**And when the name does not work either.** A .local name is answered by the
plug itself, by multicast, and multicast is exactly what the booster the plug
sits behind is bad at passing on. After the hub was reset in November the name
stopped answering altogether while the plug sat at the same address answering
everything else, and a service restarted in that state had no address to start
from. So the last resort is to ask the whole network: every Shelly answers
`/shelly` with its own id, and the name carries the id, so one pass over the
local addresses finds this plug and no other. It takes a couple of seconds and
is done at most once every SEARCH_EVERY, so a plug that is really gone is not
searched for on every reading.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

log = logging.getLogger(__name__)

#: How long to wait before the one retry. Short, because the caller is usually a
#: command sequence waiting to find out whether the unit came on.
RETRY_GAP_S = 0.5

#: How long a lookup of the plug's name may take. A .local name is answered by
#: the plug itself over the Wi-Fi, so it is either quick or not coming.
LOOKUP_TIMEOUT_S = 3.0

#: How long each address gets to answer during a search. Every address is asked
#: at once, so this is roughly how long the whole search takes.
SEARCH_TIMEOUT_S = 2.0

#: The least time between two searches. A plug that is unplugged would otherwise
#: be searched for on every reading, two hundred and fifty requests a time.
SEARCH_EVERY_S = 300.0

Lookup = Callable[[str], Awaitable[str | None]]
#: Given the plug's id and the last address it had, where it is now.
Search = Callable[[str, str | None], Awaitable[str | None]]


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


async def look_up(name: str) -> str | None:
    """The address a name has now, or None if nothing answered for it."""
    loop = asyncio.get_running_loop()
    try:
        found = await asyncio.wait_for(
            loop.getaddrinfo(name, 80, family=socket.AF_INET, type=socket.SOCK_STREAM),
            LOOKUP_TIMEOUT_S,
        )
    except (TimeoutError, OSError):
        return None
    return str(found[0][4][0]) if found else None


def _own_address() -> str | None:
    """This machine's address on the home network.

    Connecting a UDP socket sends nothing; it only asks the kernel which address
    it would send from, which is the one on the network with the router.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return str(s.getsockname()[0])
    except OSError:
        return None


def _neighbours(near: str | None) -> list[str]:
    """Every address on the same home network as `near`, or as this machine.

    A home network is a /24 in practice, and searching anything bigger would take
    too long to be worth it. Only private addresses are searched: this is for
    finding a plug in the house, never for knocking on the internet.
    """
    for candidate in (near, _own_address()):
        if candidate is None:
            continue
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if ip.version == 4 and ip.is_private and not ip.is_loopback:
            network = ipaddress.ip_network(f"{ip}/24", strict=False)
            return [str(host) for host in network.hosts()]
    return []


async def search_network(
    device_id: str, near: str | None, *, client: httpx.AsyncClient | None = None
) -> str | None:
    """The address on the home network where the Shelly with this id answers."""
    hosts = _neighbours(near)
    if not hosts:
        return None
    own = client is None
    if client is None:
        client = httpx.AsyncClient(
            timeout=SEARCH_TIMEOUT_S, limits=httpx.Limits(max_connections=len(hosts))
        )

    async def ask(host: str) -> str | None:
        try:
            response = await client.get(f"http://{host}/shelly", timeout=SEARCH_TIMEOUT_S)
            body = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        if isinstance(body, dict) and str(body.get("id", "")).lower() == device_id:
            return host
        return None

    tasks = [asyncio.create_task(ask(host)) for host in hosts]
    try:
        for finished in asyncio.as_completed(tasks):
            found = await finished
            if found is not None:
                return found
        return None
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if own:
            await client.aclose()


class _Silent(Exception):
    """Both tries at one address got nothing back."""


class ShellyPowerMonitor:
    def __init__(
        self,
        host: str,
        *,
        timeout: float = 4.0,
        switch_id: int = 0,
        client: httpx.AsyncClient | None = None,
        lookup: Lookup | None = None,
        search: Search | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.host = host
        self.switch_id = switch_id
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._lookup = lookup or look_up
        self._search_with = search or search_network
        self._monotonic = monotonic
        #: The plug's name, when it was given one. None for an address, which
        #: is read as it is and never looked up.
        self._name = None if _is_address(host) else host
        #: The id the plug gives when asked, which is its name without .local.
        #: Only a name that is a Shelly's own has one, and only a plug with one
        #: can be searched for.
        self._device_id: str | None = None
        if self._name and self._name.lower().startswith("shelly"):
            self._device_id = self._name.lower().removesuffix(".local")
        #: Where reads go: the address as configured, or the last one the name
        #: or a search gave. None until the plug has been found the first time.
        self.address: str | None = None if self._name else host
        self._searched_at: float | None = None

    async def read_watts(self) -> float | None:
        """The instantaneous draw, or None if the plug could not be reached.

        Tried twice. The plug sits behind a bed next to a metal chassis, which is
        a poor spot for 2.4GHz, and measured at -87 dBm here: about one read in
        twenty comes back empty. One dropped packet should not become a failed
        power command, because the caller treats an unreachable plug as a reason
        to stop rather than a reason to guess.

        Twice, not a loop. If the plug is genuinely gone, saying so within a
        second beats blocking a button sequence while it hopes. The exception is
        a named plug that is not where it was: then it is looked for, by name and
        then by searching the network, and asked once more wherever it turns up.
        """
        # A name that found nothing a moment ago is not asked again in the same
        # read. One that found an address is: if the plug is silent there, the
        # name may well point somewhere new by now.
        name_failed = False
        if self.address is None and self._name is not None:
            name_failed = await self._find() is None

        last = self.address
        failure: BaseException | None = None
        if last is not None:
            try:
                return self._watts(await self._ask_twice(last))
            except _Silent as silent:
                failure = silent.__cause__

        # Silent where it was, or never found at all. Its name first, which is
        # quick, then the whole network.
        for how in ("its name", "searching the network"):
            if how == "its name":
                found = await self._find() if self._name and not name_failed else None
            else:
                found = await self._search(last)
            if found is None or found == last:
                continue
            self.address = found
            log.warning("Shelly %s found at %s by %s (it was at %s)", self._name, found, how, last)
            try:
                return self._watts(await self._ask(found))
            except (httpx.HTTPError, ValueError) as exc:
                failure, last = exc, found

        if last is None:
            log.warning("Shelly %s could not be found on the network", self.host)
        else:
            # %r not %s: a failed connection can arrive with a message that is an
            # empty string, which logs as a colon and nothing.
            log.warning("Shelly at %s unreachable, twice: %r", last, failure)
        return None

    def _watts(self, payload: dict[str, Any]) -> float | None:
        watts = payload.get("apower")
        if watts is None:
            log.warning("Shelly at %s returned no apower field: %s", self.address, payload)
            return None
        return float(watts)

    async def _ask_twice(self, address: str) -> dict[str, Any]:
        try:
            return await self._ask(address)
        except (httpx.HTTPError, ValueError):
            await asyncio.sleep(RETRY_GAP_S)
        try:
            return await self._ask(address)
        except (httpx.HTTPError, ValueError) as exc:
            raise _Silent from exc

    async def _ask(self, address: str) -> dict[str, Any]:
        response = await self._client.get(
            f"http://{address}/rpc/Switch.GetStatus", params={"id": self.switch_id}
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}

    async def _find(self) -> str | None:
        """Look the name up again. Only a found address replaces the old one."""
        assert self._name is not None
        found = await self._lookup(self._name)
        if found is not None:
            self.address = found
        return found

    async def _search(self, near: str | None) -> str | None:
        """Ask the whole network, if this plug can be searched for and has not been lately."""
        if self._device_id is None:
            return None
        now = self._monotonic()
        if self._searched_at is not None and now - self._searched_at < SEARCH_EVERY_S:
            return None
        self._searched_at = now
        return await self._search_with(self._device_id, near)

    async def reachable(self) -> bool:
        return await self.read_watts() is not None

    async def close(self) -> None:
        await self._client.aclose()
