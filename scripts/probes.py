#!/usr/bin/env python3
"""Write the probe configuration, at whichever stage of setup this is.

    ./scripts/probes.py                                  # stage 1: find the probes
    ./scripts/probes.py --label                          # stage 2: name them probe_1..3
    ./scripts/probes.py --flow A --return B --room C     # stage 3: the real one

Setting up three probes means writing the same file three times: once with no
sensors to discover what is on the wire, once with neutral names so each one can
be identified by warming it, and once for real. Each version differs from the last
by a few lines in the middle of eighty.

Hand-editing that three times, with sixteen-character hex addresses that have to
be exact, is a good way to spend an evening on a typo. So this writes it instead,
and validates it with ESPHome before saying it is done.

Addresses can be given in any shape. Paste the whole log block if that is easier:

    ./scripts/probes.py --label < the-log-i-copied.txt
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "docs" / "esphome-probes.yaml"
ESPHOME = Path.home() / "esphome" / "bin" / "esphome"

#: The 1-Wire data pin. GPIO2, 8 and 9 are strapping pins on the ESP32-C3 and 8
#: also carries the onboard LED, so a probe on any of them stops the board
#: booting for reasons that look nothing like the cause.
PIN = "GPIO4"

#: The SuperMini and the genuine Seeed board are the same chip with different
#: antennas, so the configuration differs by this one line. Kept here rather than
#: as something to edit afterwards, because this script rewrites the file.
BOARDS = {"supermini": "esp32-c3-devkitm-1", "seeed": "seeed_xiao_esp32c3"}

#: A DS18B20 address as ESPHome writes it: 0x then sixteen hex characters.
ADDRESS = re.compile(r"(?:0x)?([0-9a-fA-F]{16})")

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"

HEADER = """\
# The temperature probes. Written by scripts/probes.py, so the addresses below
# are the ones this board actually reported rather than the ones I typed.
#
#   ~/esphome/bin/esphome run docs/esphome-probes.yaml
#
# Full guide: docs/temperature-probes.md

esphome:
  name: hydrosnooze-temp
  friendly_name: HydroSnooze probes

esp32:
  board: __BOARD__
  framework:
    type: esp-idf

logger:
api:
  encryption:
    key: !secret hydrosnooze_temp_api_key
  # No client for fifteen minutes and the board restarts itself. Nothing here
  # holds state worth keeping, so a reboot costs nothing and clears the whole
  # class of faults where the board is on the Wi-Fi and the API is wedged.
  reboot_timeout: 15min
ota:
  - platform: esphome
wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

  # The three lines below are why this board stays on the network, and they are
  # here because it did not. It went quiet for twenty minutes one evening and
  # came back on its own, which is the signature of the radio dropping off and
  # the default fifteen minute reboot timer eventually clearing it.

  # The big one. An ESP32 defaults to light power save: the radio naps between
  # beacons to save a few milliamps, misses packets, and eventually the access
  # point gives up on it. This board is on a USB charger, not a battery, so
  # there is nothing to save and a great deal to lose.
  power_save_mode: none

  # Skip the scan and go straight to the access point it knows. Faster to come
  # back, and this board never moves. Take this line out if it ever ends up
  # somewhere with two access points on the same name, because it will hold on
  # to the first one it saw rather than the nearest.
  fast_connect: true

  # Two minutes of failing to connect and start again from scratch. The default
  # is fifteen, which is most of an evening with no readings.
  reboot_timeout: 2min
"""

WEB = """
# A page at http://hydrosnooze-temp.local showing every reading, so a probe can
# be checked while standing next to the bed. The service talks over the API
# rather than this, so nothing in the running system depends on it.
web_server:
  version: 3
  port: 80
"""

BUS = f"""
one_wire:
  - platform: gpio
    pin: {PIN}
"""

RSSI = """
  # This board's antenna is its weak point. Worth watching from day one rather
  # than discovering it during a bad night.
  - platform: wifi_signal
    name: "wifi_rssi"
    update_interval: 60s

  # How long since it last started. The one number that separates "the link
  # dropped" from "the board rebooted", and they want different fixes. If this
  # keeps resetting, the power supply or the Wi-Fi is the problem rather than
  # anything in the app.
  - platform: uptime
    name: "uptime"
    update_interval: 60s
"""

RESTART = """
# So the board can be restarted from http://hydrosnooze-temp.local without
# anyone reaching behind a bed for a USB plug.
button:
  - platform: restart
    name: "restart"
"""


def sensor(address: str, name: str, every: str, window: int, note: str = "") -> str:
    comment = f"      # {note}\n" if note else ""
    return f"""
  - platform: dallas_temp
    address: 0x{address}
    name: "{name}"
    id: {name}
    update_interval: {every}
    accuracy_decimals: 1
    filters:
{comment}      - median:
          window_size: {window}
          send_every: {window}
      - filter_out: nan
"""


def die(*lines: str) -> None:
    for line in lines:
        print(f"{RED}{line}{RESET}" if line is lines[0] else line, file=sys.stderr)
    raise SystemExit(1)


def already_written() -> dict[str, str]:
    """The addresses in the file this script wrote last time, by role.

    The point is not saving typing. It is that changing the template, which is
    what a Wi-Fi fix means, should not cost an evening finding three sixteen
    character serials again, or worse, a repeat of the squeezing in step 4.
    """
    if not CONFIG.exists():
        return {}
    found: dict[str, str] = {}
    address = ""
    for line in CONFIG.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("address:"):
            match = ADDRESS.search(stripped)
            address = match.group(1).lower() if match else ""
        elif stripped.startswith("name:") and address:
            role = stripped.split(":", 1)[1].strip().strip('"')
            if role in ("water_flow", "water_return", "room"):
                found[role] = address
            address = ""
    return found if len(found) == 3 else {}


def clean(raw: list[str]) -> list[str]:
    """Pull addresses out of whatever was given: bare, prefixed, or a whole log."""
    found: list[str] = []
    for chunk in raw:
        for match in ADDRESS.finditer(chunk):
            value = match.group(1).lower()
            if value not in found:
                found.append(value)
    return found


def validate(path: Path) -> bool:
    """Ask ESPHome, rather than hoping. Skipped if it is not installed here."""
    if not ESPHOME.exists():
        return True
    done = subprocess.run(
        [str(ESPHOME), "config", str(path)], capture_output=True, text=True
    )
    if done.returncode == 0:
        print(f"{DIM}  ESPHome says the configuration is valid.{RESET}")
        return True
    print(f"{RED}ESPHome rejected it:{RESET}", file=sys.stderr)
    print(done.stderr.strip()[-1200:] or done.stdout.strip()[-1200:], file=sys.stderr)
    return False


def write(body: str, what: str, then: list[str], board: str = "supermini") -> int:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(body.replace("__BOARD__", BOARDS[board]))
    print()
    print(f"{GREEN}Wrote {CONFIG.relative_to(ROOT)}{RESET}  {DIM}({what}){RESET}")
    if not validate(CONFIG):
        return 1
    print()
    for line in then:
        print(line)
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--label",
        action="store_true",
        help="stage 2: name the found probes probe_1, probe_2, probe_3 so each "
        "can be identified by warming it",
    )
    parser.add_argument("addresses", nargs="*", help="addresses, or a pasted log")
    # On the hoses rather than in the bed. The flow probe reads the water the
    # unit is circulating, which is the thing its setpoint actually refers to, so
    # it checks the unit against what it was told. The return reads that water
    # after the bed has had it, and the difference between the two is the heat
    # actually moving, which no probe taped under a sheet could tell us.
    parser.add_argument("--flow", help="stage 3: on the hose going to the bed")
    parser.add_argument(
        "--return", dest="water_return", help="stage 3: on the hose coming back"
    )
    parser.add_argument("--room", help="stage 3: air temperature, away from the bed")
    parser.add_argument(
        "--seeed",
        action="store_true",
        help="build for the genuine Seeed XIAO ESP32C3 rather than the SuperMini",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="rewrite with the addresses already in the file, for when this "
        "script changes and the board needs reflashing",
    )
    args = parser.parse_args()
    board = "seeed" if args.seeed else "supermini"

    roles = (args.flow, args.water_return, args.room)

    if args.keep and not any(roles):
        known = already_written()
        if not known:
            die(
                f"No three named probes in {CONFIG.relative_to(ROOT)} to keep.",
                "Give the addresses instead:",
                "",
                "  ./scripts/probes.py --flow 0x.. --return 0x.. --room 0x..",
            )
        roles = (known["water_flow"], known["water_return"], known["room"])
        print()
        print(f"{DIM}Keeping the three addresses already in the file.{RESET}")

    # --- Stage 3: the real thing ---------------------------------------------
    if any(roles):
        if not all(roles):
            die("Stage 3 needs all three: --flow, --return and --room.")
        found = clean(list(roles))
        if len(found) != 3:
            die(
                "Those are not three different addresses.",
                "Each is 0x followed by sixteen hex characters, and no two probes share one.",
            )
        flow, back, room = found
        body = (
            HEADER
            + WEB
            + BUS
            + "\nsensor:"
            + sensor(
                flow,
                "water_flow",
                "30s",
                5,
                "Not decoration. A bad read on a long 1-Wire cable arrives as -127"
                "\n      # or 85, and a median of five throws it away silently.",
            )
            # Named water_return rather than return, because ESPHome turns an id
            # into a C++ variable and `return` is a keyword there.
            + sensor(back, "water_return", "30s", 5)
            + sensor(room, "room", "60s", 3)
            + RSSI
            + RESTART
        )
        return write(
            body,
            "the real configuration",
            [
                f"{BOLD}~/esphome/bin/esphome run docs/esphome-probes.yaml{RESET}",
                "",
                f"{DIM}With the unit off, all three should read within about a degree of{RESET}",
                f"{DIM}each other. Once it is running, flow should track the setpoint and{RESET}",
                f"{DIM}return should differ from it by however hard the bed is working.{RESET}",
            ],
            board,
        )

    # --- Stage 2: neutral names, so each can be identified --------------------
    if args.label:
        raw = args.addresses or ([sys.stdin.read()] if not sys.stdin.isatty() else [])
        if not raw:
            die(
                "No addresses given.",
                "Paste the three from the flash log, in any form:",
                "",
                "  ./scripts/probes.py --label 0x1c00... 0x3a00... 0x9b00...",
            )
        found = clean(raw)
        if len(found) < 2:
            die(
                f"Only found {len(found)} address in that.",
                "Each is 0x followed by sixteen hex characters.",
            )
        body = HEADER + WEB + BUS + "\nsensor:"
        for i, address in enumerate(found, start=1):
            body += sensor(address, f"probe_{i}", "10s", 3)
        body += RSSI + RESTART
        return write(
            body,
            f"{len(found)} probes, named probe_1 to probe_{len(found)}",
            [
                f"{BOLD}~/esphome/bin/esphome run docs/esphome-probes.yaml{RESET}",
                "",
                "Then, watching the log:",
                "",
                f"  1. {BOLD}Squeeze one probe in a fist.{RESET} Within a few seconds one",
                "     reading climbs. That is the one being held.",
                "  2. Put tape on that lead and write probe_1, or whichever it was.",
                "  3. Let it cool, then do the next.",
                "",
                f"{DIM}Reading every 10s here rather than 30s, so a warming probe shows up{RESET}",
                f"{DIM}while the hand is still on it.{RESET}",
                "",
                "Then stage 3:",
                "",
                f"  {BOLD}./scripts/probes.py --flow 0x.. --return 0x.. --room 0x..{RESET}",
            ],
            board,
        )

    # --- Stage 1: find out what is on the wire --------------------------------
    body = HEADER + BUS
    return write(
        body,
        "discovery, no sensors",
        [
            f"{BOLD}~/esphome/bin/esphome run docs/esphome-probes.yaml{RESET}",
            "",
            f"{DIM}If it will not flash: hold BOOT, tap RST, release BOOT.{RESET}",
            "",
            "Within thirty seconds of it starting, the log prints:",
            "",
            f"{DIM}  [one_wire] Found devices:{RESET}",
            f"{DIM}    0x1c0000031edd2828{RESET}",
            f"{DIM}    0x3a00000320f18b28{RESET}",
            f"{DIM}    0x9b000003215c4f28{RESET}",
            "",
            "Three means the wiring is right. Fewer means a loose joint. None at",
            f"all means the data wire is not on {PIN}, or the pull-up is missing.",
            "",
            "Then, pasting those three straight in:",
            "",
            f"  {BOLD}./scripts/probes.py --label 0x1c00... 0x3a00... 0x9b00...{RESET}",
        ],
        board,
    )


if __name__ == "__main__":
    if not shutil.which("python3"):  # pragma: no cover
        pass
    raise SystemExit(main())
