"""Whether a device is healthy, and the difference between a wobble and a loss.

Three of the four devices in this system can fail silently. The plug just stops
answering. The blaster falls off the Wi-Fi and nothing notices until a stage
boundary hours later. Neither raises anything, so the only way to know is to ask
and to remember when the last answer came.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from hydrosnooze.models import DEGRADED_AFTER, DeviceHealth, Health

NOW = datetime(2026, 9, 8, 23, 15)


def judge(*, ok_now, last_ok_at=None, note=""):
    return DeviceHealth.judge(
        "plug", now=NOW, last_ok_at=last_ok_at, ok_now=ok_now, where="the plug", note=note
    )


def test_answering_is_healthy():
    assert judge(ok_now=True).health is Health.OK


def test_never_asked_is_not_a_colour():
    """Green on a device nobody has spoken to yet is exactly the confident lie
    this project exists not to tell."""
    assert judge(ok_now=None).health is Health.UNKNOWN


def test_a_brief_silence_is_a_wobble_not_a_loss():
    """Wi-Fi drops a packet. Waking someone for that would make the bar useless."""
    verdict = judge(ok_now=False, last_ok_at=NOW - timedelta(seconds=40))
    assert verdict.health is Health.DEGRADED
    assert "40s" in verdict.detail


def test_a_long_silence_is_a_loss():
    verdict = judge(ok_now=False, last_ok_at=NOW - DEGRADED_AFTER - timedelta(seconds=1))
    assert verdict.health is Health.DOWN
    assert "power" in verdict.detail and "Wi-Fi" in verdict.detail


def test_failing_with_no_success_ever_is_a_loss_straight_away():
    """Nothing to fall back on, so there is no wobble to give it the benefit of."""
    assert judge(ok_now=False).health is Health.DOWN


def test_the_boundary_belongs_to_degraded():
    just_inside = judge(ok_now=False, last_ok_at=NOW - DEGRADED_AFTER + timedelta(seconds=1))
    assert just_inside.health is Health.DEGRADED


def test_a_healthy_device_says_something_useful_rather_than_just_ok():
    verdict = judge(ok_now=True, note="Reading 166.2 W")
    assert verdict.detail == "Reading 166.2 W"


def test_the_time_it_last_answered_is_carried_through():
    when = NOW - timedelta(seconds=10)
    assert judge(ok_now=False, last_ok_at=when).last_ok_at == when


# --- What the service reports -------------------------------------------------


def test_a_simulated_setup_says_so_rather_than_showing_green(monkeypatch):
    """Simulated is not healthy and it is not broken. It is a third thing, and
    a bar that showed green here would be describing hardware that is not
    plugged in."""
    from hydrosnooze.config import Settings
    from hydrosnooze.service import Service

    service = Service(Settings(db_path=":memory:"), echo=False)
    names = {d.name: d for d in service.health()}
    assert names["plug"].health is Health.SIMULATED
    assert names["blaster"].health is Health.SIMULATED
    assert "invented" in names["plug"].detail


def test_real_hardware_starts_unknown_rather_than_assuming(monkeypatch):
    from hydrosnooze.config import Settings
    from hydrosnooze.service import Service

    service = Service(
        Settings(
            db_path=":memory:",
            transmitter="esphome",
            power_monitor="shelly",
            shelly_host="192.0.2.1",
            esphome_host="192.0.2.2",
        ),
        echo=False,
    )
    assert all(d.health is Health.UNKNOWN for d in service.health())
