"""Reading the plug over a weak Wi-Fi link.

The plug lives behind a bed, next to the unit's metal chassis, which is close to
the worst place in a house for 2.4GHz. Measured at -87 dBm, about one read in
twenty comes back empty. That matters more than the number suggests, because a
command sequence treats an unreachable plug as a reason to stop.
"""

from __future__ import annotations

import httpx
import pytest

from hydrosnooze.adapters.shelly import ShellyPowerMonitor


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


def named(handler, *answers: str | None) -> tuple[ShellyPowerMonitor, list[str]]:
    """A plug known by name, whose lookups answer `answers` in turn."""
    asked: list[str] = []
    queue = list(answers)

    async def lookup(name: str) -> str | None:
        asked.append(name)
        return queue.pop(0) if queue else None

    plug = ShellyPowerMonitor(
        NAME,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        lookup=lookup,
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
