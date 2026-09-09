"""The machine underneath, reporting on itself.

Under-voltage is the commonest reason a Pi behaves as though the software is
broken. It leaves no other trace: the machine stays up, the network stutters,
reads fail, and every symptom points at the code. The setup notes have warned
about it from the start, which is not the same as noticing it.
"""

from __future__ import annotations

from hydrosnooze import pi
from hydrosnooze.config import Settings
from hydrosnooze.notify import Notifier
from hydrosnooze.service import Service

UNDERVOLTAGE_NOW = 0x1
UNDERVOLTAGE_SINCE_BOOT = 0x10000
TOO_HOT_NOW = 0x8


def service(tmp_path) -> Service:
    return Service(Settings(db_path=str(tmp_path / "s.db")), echo=False)


# --- Reading it ---------------------------------------------------------------


def test_anything_that_is_not_a_pi_is_left_alone(monkeypatch):
    monkeypatch.setattr(pi.shutil, "which", lambda _: None)
    assert pi.throttled() is None


def test_the_mask_is_parsed_out_of_what_vcgencmd_prints(monkeypatch):
    monkeypatch.setattr(pi.shutil, "which", lambda _: "/usr/bin/vcgencmd")

    class Said:
        returncode = 0
        stdout = "throttled=0x50005\n"

    monkeypatch.setattr(pi.subprocess, "run", lambda *a, **k: Said())
    assert pi.throttled() == 0x50005


def test_output_it_cannot_read_is_not_a_guess(monkeypatch):
    monkeypatch.setattr(pi.shutil, "which", lambda _: "/usr/bin/vcgencmd")

    class Odd:
        returncode = 0
        stdout = "who knows\n"

    monkeypatch.setattr(pi.subprocess, "run", lambda *a, **k: Odd())
    assert pi.throttled() is None


# --- Saying it ----------------------------------------------------------------


def test_a_healthy_pi_has_nothing_to_say():
    assert pi.describe(0x0) == ""


def test_it_names_the_cable_rather_than_the_bit():
    """"throttled=0x50005" is a fact about a register. What is wanted at 7am is
    which thing to go and change."""
    said = pi.describe(0x50005)
    assert "not getting enough power" in said
    assert "power supply or the cable" in said
    assert "0x" not in said


def test_right_now_reads_differently_from_since_boot():
    assert "right now" in pi.describe(UNDERVOLTAGE_NOW)
    assert "since it was switched on" in pi.describe(UNDERVOLTAGE_SINCE_BOOT)


def test_heat_is_not_blamed_on_the_power_supply():
    said = pi.describe(TOO_HOT_NOW)
    assert "temperature" in said
    assert "power supply" not in said


# --- Reporting it -------------------------------------------------------------


def test_it_reaches_the_phone():
    """Hardware going wrong underneath everything else is worth waking someone
    for. Most warnings are not, which is why the filter exists."""
    from datetime import datetime

    from hydrosnooze.clock import VirtualClock
    from hydrosnooze.events import EventLog

    events = EventLog(VirtualClock(datetime(2026, 9, 9, 3, 0)))
    events.warning("service", pi.describe(0x50005))
    notifier = Notifier(VirtualClock(datetime(2026, 9, 9, 3, 0)), topic="t")
    assert all(notifier.worth_sending(e) for e in events.recent(5))


def test_one_bad_power_supply_is_one_warning_not_one_an_hour(monkeypatch, tmp_path):
    """The bits at 16 and up stay set until the next boot. Saying it again every
    hour would be one problem and a hundred pushes, and by the third night I
    would have muted the app that was telling me the truth."""
    s = service(tmp_path)
    monkeypatch.setattr(pi, "throttled", lambda: 0x50005)

    for _ in range(24):
        s._check_the_pi()

    warned = [e for e in s.events.recent(50) if "The Pi is" in e.message]
    assert len(warned) == 1


def test_something_new_going_wrong_is_still_reported(monkeypatch, tmp_path):
    """The other half. Reporting once must not mean reporting once ever."""
    s = service(tmp_path)

    monkeypatch.setattr(pi, "throttled", lambda: UNDERVOLTAGE_NOW)
    s._check_the_pi()
    monkeypatch.setattr(pi, "throttled", lambda: UNDERVOLTAGE_NOW | TOO_HOT_NOW)
    s._check_the_pi()

    said = [e for e in s.events.recent(50) if "The Pi is" in e.message]
    assert len(said) == 2
    assert "temperature" in said[0].message


def test_a_healthy_pi_says_nothing_at_all(monkeypatch, tmp_path):
    s = service(tmp_path)
    monkeypatch.setattr(pi, "throttled", lambda: 0x0)
    s._check_the_pi()
    assert not [e for e in s.events.recent(50) if "Pi" in e.message]


def test_not_a_pi_says_nothing_at_all(monkeypatch, tmp_path):
    s = service(tmp_path)
    monkeypatch.setattr(pi, "throttled", lambda: None)
    s._check_the_pi()
    assert not [e for e in s.events.recent(50) if "Pi" in e.message]
