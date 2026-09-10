"""The board and the app agreeing about how often a reading arrives.

These are two halves of one number and they live in different languages, in
different files, in different repositories of knowledge in my head. They drifted
apart for a fortnight and nothing noticed until the event log was added:

    the board    update_interval: 30s, median send_every: 5   -> a value every 150s
    the app      STALE_AFTER = 2 minutes                      -> expires at 120s

So every reading the board sent had already gone stale before its replacement
arrived, and the probes flickered in and out all evening. Nothing was broken.
Nothing threw. The two numbers were simply written six days apart by someone who
had forgotten that `send_every` is not `window_size`.

That is what this file is for. It reads the configuration that actually gets
flashed and checks it against the constant the app actually uses, so the two can
never quietly disagree again.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from hydrosnooze.adapters.probes import SILENT_TOO_LONG, STALE_AFTER

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "probes.py"


def _script():
    """Load scripts/probes.py, which is not a package and never will be."""
    spec = importlib.util.spec_from_file_location("probes_script", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: address, name, id, update_interval, then the median block's send_every.
SENSOR = re.compile(
    r"- platform: dallas_temp.*?"
    r'name: "(?P<name>[^"]+)".*?'
    r"update_interval: (?P<every>\d+)s.*?"
    r"send_every: (?P<send>\d+)",
    re.DOTALL,
)


def publish_seconds() -> dict[str, int]:
    """How often each probe actually puts a value on the wire.

    Not `update_interval`. That is how often the sensor is read; the median
    filter then publishes one value every `send_every` readings, so the interval
    that matters downstream is the two multiplied together. Getting that wrong is
    the whole reason this file exists.
    """
    body = _script().real_config(
        "ba0000002618c028", "5c00000000e73f28", "2900000025d73f28"
    )
    found = {
        m.group("name"): int(m.group("every")) * int(m.group("send"))
        for m in SENSOR.finditer(body)
    }
    assert set(found) == {"water_flow", "water_return", "room"}, found
    return found


@pytest.mark.parametrize("name", ["water_flow", "water_return", "room"])
def test_a_reading_arrives_before_the_last_one_expires(name):
    """The bug, stated as the thing it broke.

    A probe whose next value arrives after the last one has expired is a probe
    the app considers missing most of the time, however healthy the board and the
    Wi-Fi are.
    """
    gap = publish_seconds()[name]
    assert gap < STALE_AFTER.total_seconds(), (
        f"{name} publishes every {gap}s and the app calls a reading stale after "
        f"{int(STALE_AFTER.total_seconds())}s, so every value expires before it "
        "is replaced"
    )


@pytest.mark.parametrize("name", ["water_flow", "water_return", "room"])
def test_and_with_room_to_spare_rather_than_only_just(name):
    """Two missed reports should not be enough to go red.

    Landing a second inside the limit would be technically correct and would go
    red on any single late packet, on a board whose whole problem is a bedroom
    Wi-Fi link.
    """
    gap = publish_seconds()[name]
    assert gap * 2 <= STALE_AFTER.total_seconds()


def test_the_link_is_not_rebuilt_before_the_readings_are_even_doubted():
    """Ordering between the two windows, which has to hold in this direction.

    Tearing down a link that is merely slow would turn a board that is coping
    into one that never finishes connecting.
    """
    assert SILENT_TOO_LONG > STALE_AFTER


def test_silence_means_several_missed_reports_on_every_sensor():
    slowest = max(publish_seconds().values())
    assert SILENT_TOO_LONG.total_seconds() >= slowest * 3


def test_the_median_still_throws_away_a_bad_one_wire_read():
    """Publishing more often must not cost the noise rejection.

    -127 and 85 are what a bad read on a long 1-Wire cable looks like, and a
    window of one would put both of them straight into the app.
    """
    body = _script().real_config(
        "ba0000002618c028", "5c00000000e73f28", "2900000025d73f28"
    )
    windows = [int(w) for w in re.findall(r"window_size: (\d+)", body)]
    assert windows and all(w >= 3 for w in windows)
