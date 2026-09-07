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
