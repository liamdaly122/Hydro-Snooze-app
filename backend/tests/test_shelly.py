"""Reading the plug over a weak Wi-Fi link.

The plug lives behind a bed, next to the unit's metal chassis, which is close to
the worst place in a house for 2.4GHz. Measured at -87 dBm, about one read in
twenty comes back empty. That matters more than the number suggests, because a
command sequence treats an unreachable plug as a reason to stop.
"""

from __future__ import annotations

import httpx
import pytest

from hydrosnooze.adapters import shelly
from hydrosnooze.adapters.shelly import ShellyPowerMonitor, search_network


def monitor(handler) -> ShellyPowerMonitor:
    return ShellyPowerMonitor(
        "192.168.1.194", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )


@pytest.mark.asyncio
async def test_a_good_read_returns_the_watts():
    plug = monitor(lambda _: httpx.Response(200, json={"id": 0, "apower": 166.9}))
    assert await plug.read_watts() == 166.9
    await plug.close()


@pytest.mark.asyncio
async def test_one_dropped_packet_is_retried_rather_than_failing():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("no route to host")
        return httpx.Response(200, json={"apower": 9.2})

    plug = monitor(handler)
    assert await plug.read_watts() == 9.2, "the second attempt answered"
    assert len(calls) == 2
    await plug.close()


@pytest.mark.asyncio
async def test_a_plug_that_is_really_gone_gives_up_after_two():
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("no route to host")

    plug = monitor(handler)
    assert await plug.read_watts() is None, "None, never zero"
    assert len(calls) == 2, "twice, not a loop"
    await plug.close()


@pytest.mark.asyncio
async def test_a_reply_with_no_reading_in_it_is_not_retried():
    """The plug answered, so the link is fine. Asking again would get the same
    answer, and None here means the same as it always does: we do not know."""
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"id": 0, "output": False})

    plug = monitor(handler)
    assert await plug.read_watts() is None
    assert len(calls) == 1
    await plug.close()


@pytest.mark.asyncio
async def test_zero_watts_is_a_reading_not_a_failure():
    plug = monitor(lambda _: httpx.Response(200, json={"apower": 0}))
    assert await plug.read_watts() == 0.0
    await plug.close()


# --- A plug the router keeps moving ---------------------------------------------------
#
# It went from .194 to .192 to .193 in a month, and each time an address in .env
# pointed at nothing. Given its name instead, it is looked up once, read by
# address after that, and only looked up again when it stops answering.

NAME = "shellypluspluguk-441d648dd41c.local"


async def nowhere(device_id: str, near: str | None) -> str | None:
    return None


def named(handler, *answers: str | None, search=nowhere, monotonic=None):
    """A plug known by name, whose lookups answer `answers` in turn.

    The search is a stand-in too, so no test here ever asks a real network.
    """
    asked: list[str] = []
    queue = list(answers)

    async def lookup(name: str) -> str | None:
        asked.append(name)
        return queue.pop(0) if queue else None

    extra = {"monotonic": monotonic} if monotonic else {}
    plug = ShellyPowerMonitor(
        NAME,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        lookup=lookup,
        search=search,
        **extra,
    )
    return plug, asked


def at(*answering: str):
    """A handler for a plug that answers only at these addresses."""
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host in answering:
            return httpx.Response(200, json={"apower": 12.5})
        raise httpx.ConnectError("All connection attempts failed")

    return handler, hosts


@pytest.mark.asyncio
async def test_a_name_is_looked_up_once_and_then_read_by_address():
    handler, hosts = at("192.168.1.193")
    plug, asked = named(handler, "192.168.1.193")
    for _ in range(3):
        assert await plug.read_watts() == 12.5
    assert asked == [NAME], "not a lookup on every read"
    assert hosts == ["192.168.1.193"] * 3
    await plug.close()


@pytest.mark.asyncio
async def test_a_plug_given_a_new_address_is_found_there_in_the_same_read():
    handler, hosts = at("192.168.1.193")
    plug, asked = named(handler, "192.168.1.192", "192.168.1.193")
    assert await plug.read_watts() == 12.5, "followed, not called unreachable"
    assert hosts == ["192.168.1.192", "192.168.1.192", "192.168.1.193"]
    assert plug.address == "192.168.1.193"

    assert await plug.read_watts() == 12.5
    assert hosts[-1] == "192.168.1.193" and len(asked) == 2, "and stays there"
    await plug.close()


@pytest.mark.asyncio
async def test_silent_where_its_name_still_points_is_simply_unreachable():
    handler, hosts = at()
    plug, asked = named(handler, "192.168.1.193", "192.168.1.193")
    assert await plug.read_watts() is None
    assert hosts == ["192.168.1.193"] * 2, "twice, and nothing more to try"
    assert len(asked) == 2
    await plug.close()


@pytest.mark.asyncio
async def test_a_lookup_that_fails_keeps_the_last_address_that_worked():
    up = [True]

    def handler(request: httpx.Request) -> httpx.Response:
        if up[0]:
            return httpx.Response(200, json={"apower": 12.5})
        raise httpx.ConnectTimeout("")

    plug, asked = named(handler, "192.168.1.193")
    assert await plug.read_watts() == 12.5

    # Silent for a moment, and the lookup gets nothing either.
    up[0] = False
    assert await plug.read_watts() is None
    assert len(asked) == 2 and plug.address == "192.168.1.193", "not forgotten"

    up[0] = True
    assert await plug.read_watts() == 12.5, "and read there again once it answers"
    await plug.close()


@pytest.mark.asyncio
async def test_a_name_nothing_answers_for_is_not_read_at_all():
    handler, hosts = at("192.168.1.193")
    plug, asked = named(handler, None)
    assert await plug.read_watts() is None
    assert hosts == [] and asked == [NAME]
    await plug.close()


@pytest.mark.asyncio
async def test_an_address_is_never_looked_up():
    asked: list[str] = []

    async def lookup(name: str) -> str | None:
        asked.append(name)
        return "192.168.1.193"

    plug = ShellyPowerMonitor(
        "192.168.1.192",
        client=httpx.AsyncClient(transport=httpx.MockTransport(at()[0])),
        lookup=lookup,
    )
    assert await plug.read_watts() is None
    assert asked == []
    await plug.close()


# --- When the name does not work either --------------------------------------------------
#
# After the hub was reset the name stopped answering altogether, while the plug
# sat at .193 answering everything else. A service restarted then had nothing to
# start from. The last resort is to ask the whole network for the plug's id.

ID = "shellypluspluguk-441d648dd41c"


def searching(*answers: str | None):
    """A stand-in search that answers `answers` in turn, and the calls it got."""
    calls: list[tuple[str, str | None]] = []
    queue = list(answers)

    async def search(device_id: str, near: str | None) -> str | None:
        calls.append((device_id, near))
        return queue.pop(0) if queue else None

    return search, calls


@pytest.mark.asyncio
async def test_a_restart_with_the_name_not_working_finds_the_plug_by_searching():
    handler, hosts = at("192.168.1.193")
    search, searched = searching("192.168.1.193")
    plug, asked = named(handler, None, search=search)
    assert await plug.read_watts() == 12.5, "found, not called unreachable"
    assert searched == [(ID, None)], "searched for by its id, once"
    assert asked == [NAME], "the name was tried first, and only once"

    assert await plug.read_watts() == 12.5
    assert hosts == ["192.168.1.193"] * 2 and len(searched) == 1, "and read there after"
    await plug.close()


@pytest.mark.asyncio
async def test_a_plug_silent_where_it_was_is_searched_for_near_there():
    handler, hosts = at("192.168.1.201")
    search, searched = searching("192.168.1.201")
    plug, _ = named(handler, "192.168.1.193", "192.168.1.193", search=search)
    assert await plug.read_watts() == 12.5
    assert searched == [(ID, "192.168.1.193")]
    assert hosts == ["192.168.1.193", "192.168.1.193", "192.168.1.201"]
    assert plug.address == "192.168.1.201"
    await plug.close()


@pytest.mark.asyncio
async def test_a_plug_that_is_really_gone_is_not_searched_for_on_every_reading():
    now = [1000.0]
    handler, _ = at()
    search, searched = searching()
    plug, _ = named(handler, "192.168.1.193", search=search, monotonic=lambda: now[0])
    assert await plug.read_watts() is None
    assert await plug.read_watts() is None
    assert len(searched) == 1, "once, not every thirty seconds"

    now[0] += shelly.SEARCH_EVERY_S
    assert await plug.read_watts() is None
    assert len(searched) == 2, "and again once enough time has gone by"
    await plug.close()


@pytest.mark.asyncio
async def test_a_plug_known_by_address_or_by_another_name_is_never_searched_for():
    for host in ("192.168.1.193", "hydrosnooze-plug.local"):
        search, searched = searching("192.168.1.201")

        async def lookup(name: str) -> str | None:
            return None

        plug = ShellyPowerMonitor(
            host,
            client=httpx.AsyncClient(transport=httpx.MockTransport(at()[0])),
            lookup=lookup,
            search=search,
        )
        assert await plug.read_watts() is None
        assert searched == [], host
        await plug.close()


def shellies(**by_host: str):
    """A network where these addresses answer /shelly with these ids."""

    def handler(request: httpx.Request) -> httpx.Response:
        device = by_host.get(request.url.host)
        if device is None or request.url.path != "/shelly":
            raise httpx.ConnectError("No route to host")
        return httpx.Response(200, json={"id": device, "gen": 2})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_the_search_finds_this_plug_and_not_another_shelly():
    client = shellies(**{"192.168.1.50": "shellyplus1-aaaaaaaaaaaa", "192.168.1.193": ID})
    assert await search_network(ID, "192.168.1.147", client=client) == "192.168.1.193"
    await client.aclose()


@pytest.mark.asyncio
async def test_a_search_that_finds_nothing_says_so():
    client = shellies(**{"192.168.1.50": "shellyplus1-aaaaaaaaaaaa"})
    assert await search_network(ID, "192.168.1.147", client=client) is None
    await client.aclose()


def test_only_the_home_network_is_searched(monkeypatch):
    hosts = shelly._neighbours("192.168.1.193")
    assert len(hosts) == 254 and hosts[0] == "192.168.1.1" and hosts[-1] == "192.168.1.254"

    monkeypatch.setattr(shelly, "_own_address", lambda: None)
    assert shelly._neighbours("8.8.8.8") == [], "never the internet"
    assert shelly._neighbours(None) == []
