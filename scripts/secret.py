#!/usr/bin/env python3
"""What the boards need from docs/secrets.yaml, and a safe way to fill it in.

    ./scripts/secret.py                           # what is needed, what is set
    ./scripts/secret.py wifi_ssid_hub VM1876778   # set one
    ./scripts/secret.py wifi_password             # set one, typed hidden

Written because "ESPHome will refuse to compile without it" is a poor way to
find out a key is missing. ESPHome's error names the file and the line, not the
key, and the line it names is in a file that scripts/probes.py generates, so the
obvious fix is to edit the wrong file.

This reads every `!secret` the configurations actually ask for and says which of
them are not there yet. It is the question worth asking before a flash rather
than during one.

**No value is ever printed.** The Wi-Fi password and both API keys live in this
file. Listing shows names and whether each is set, and nothing else, because a
tool whose output gets pasted into a chat window has no business reading secrets
out loud.
"""

from __future__ import annotations

import getpass
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "docs" / "secrets.yaml"
EXAMPLE = ROOT / "docs" / "secrets.yaml.example"

#: Where a `!secret name` can appear. scripts/probes.py rather than the file it
#: generates, because the generated one is per-machine and may not exist yet.
SOURCES = ("scripts/probes.py", "docs/esphome-hydrosnooze.yaml", "docs/esphome-capture.yaml")

WANTED = re.compile(r"!secret\s+([A-Za-z_][A-Za-z0-9_]*)")
#: A top-level key. Indented lines are values inside a block and not ours.
KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:")

RED, GREEN, DIM, RESET = "\033[31m", "\033[32m", "\033[2m", "\033[0m"


def needed() -> list[str]:
    """Every secret the configurations ask for, in the order first seen."""
    out: list[str] = []
    for name in SOURCES:
        path = ROOT / name
        if not path.exists():
            continue
        for found in WANTED.findall(path.read_text()):
            if found not in out:
                out.append(found)
    return out


def held() -> set[str]:
    """Which keys the file defines. Names only: the values are never read out."""
    if not SECRETS.exists():
        return set()
    return {
        m.group(1)
        for line in SECRETS.read_text().splitlines()
        if (m := KEY.match(line))
    }


def quoted(value: str) -> str:
    """A YAML double-quoted scalar. Passwords contain punctuation."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def put(key: str, value: str) -> None:
    """Set one key, keeping every comment and every other line as it was.

    Line-based rather than a YAML round trip on purpose. Loading and dumping
    would rewrite the file from the parsed structure and throw away every
    comment in it, and the comments in that file are what say which network is
    which.
    """
    lines = SECRETS.read_text().splitlines()
    # Beside the original, which .gitignore covers as of 21 September. It did
    # not before, and a backup of this file holds the Wi-Fi password.
    shutil.copy2(SECRETS, SECRETS.with_suffix(".yaml.bak"))

    for i, line in enumerate(lines):
        found = KEY.match(line)
        if found and found.group(1) == key:
            lines[i] = f"{key}: {quoted(value)}"
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"{key}: {quoted(value)}")

    SECRETS.write_text("\n".join(lines) + "\n")

    # Prove it still parses before walking away, because the next thing to read
    # it is a compiler that will blame a different file.
    try:
        import yaml
    except ImportError:
        return
    loader = type("L", (yaml.SafeLoader,), {})
    loader.add_constructor("!secret", lambda l, n: str(n.value))
    try:
        yaml.load(SECRETS.read_text(), Loader=loader)
    except Exception as exc:  # noqa: BLE001
        shutil.copy2(SECRETS.with_suffix(".yaml.bak"), SECRETS)
        raise SystemExit(f"{RED}That would not parse, so nothing was changed: {exc}{RESET}")


def report() -> int:
    want, have = needed(), held()
    if not SECRETS.exists():
        print(f"{RED}{SECRETS.relative_to(ROOT)} does not exist.{RESET}\n")
        print(f"  cp {EXAMPLE.relative_to(ROOT)} {SECRETS.relative_to(ROOT)}\n")
        return 1

    print(f"\n{DIM}What the configurations ask for, and whether it is set.")
    print(f"Values are never printed.{RESET}\n")
    missing = [k for k in want if k not in have]
    for key in want:
        ok = key in have
        mark = f"{GREEN}set{RESET}" if ok else f"{RED}MISSING{RESET}"
        print(f"  {key:<28} {mark}")

    spare = sorted(have - set(want))
    if spare:
        print(f"\n{DIM}Also in the file and not asked for by anything:{RESET}")
        for key in spare:
            print(f"  {key}")

    if missing:
        print(f"\n{RED}Add {'it' if len(missing) == 1 else 'them'} before flashing:{RESET}")
        for key in missing:
            print(f"  ./scripts/secret.py {key} <value>")
        print()
        return 1
    print(f"\n{GREEN}Nothing missing.{RESET}\n")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        return report()

    key = argv[0]
    if not KEY.match(f"{key}:"):
        raise SystemExit(f"{RED}{key!r} is not a usable key name.{RESET}")
    if not SECRETS.exists():
        return report()

    value = argv[1] if len(argv) > 1 else getpass.getpass(f"{key}: ")
    if not value:
        raise SystemExit(f"{RED}Nothing given, so nothing was changed.{RESET}")

    was = key in held()
    put(key, value)
    print(f"\n{GREEN}{'Changed' if was else 'Added'} {key}.{RESET} "
          f"{DIM}Backup beside it as secrets.yaml.bak, gitignored.{RESET}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
