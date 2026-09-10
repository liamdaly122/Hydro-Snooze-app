#!/usr/bin/env python3
"""Give the probe board its own API key, without anyone having to see it.

    ./scripts/probe-key.py          # make one, or say one already exists
    ./scripts/probe-key.py --show   # print it, for pasting into .env later

Every ESPHome device gets its own key. Sharing the blaster's would mean one
leaked string opens both, and they are different devices doing different jobs in
different parts of the room.

The alternative to this script is generating 44 random characters, opening a
hidden file in a terminal editor, and pasting them in without a typo. That is a
poor way to spend an evening, and a typo here fails much later as an unhelpful
"invalid encryption key" at flash time.

Writes to docs/secrets.yaml, which is gitignored and stays that way. Running it
twice never makes a second key.
"""

from __future__ import annotations

import argparse
import base64
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "docs" / "secrets.yaml"
EXAMPLE = ROOT / "docs" / "secrets.yaml.example"

#: The name the probe config expects. Must match `!secret` in the YAML.
KEY_NAME = "hydrosnooze_temp_api_key"

BOLD, DIM, GREEN, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[0m"


def read_key(text: str) -> str:
    match = re.search(rf'^\s*{KEY_NAME}\s*:\s*["\']?([^"\'\s]+)', text, re.M)
    return match.group(1) if match else ""


def mask(value: str) -> str:
    return f"{value[:4]}...{value[-4:]} ({len(value)} chars)" if len(value) > 12 else "set"


def die(*lines: str) -> None:
    for line in lines:
        print(line, file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--show",
        action="store_true",
        help="print the key in full, for pasting into backend/.env",
    )
    args = parser.parse_args()

    if not SECRETS.exists():
        die(
            f"{SECRETS.relative_to(ROOT)} does not exist.",
            f"Copy {EXAMPLE.relative_to(ROOT)} to it and fill in the Wi-Fi lines first.",
        )

    text = SECRETS.read_text()
    existing = read_key(text)

    if existing:
        print()
        print(f"{DIM}Already set in {SECRETS.relative_to(ROOT)}{RESET}")
        print(f"  {KEY_NAME}: {mask(existing)}")
        if args.show:
            print()
            print(f"  {BOLD}{existing}{RESET}")
        else:
            print()
            print(f"{DIM}  Add --show to print it in full.{RESET}")
        print()
        print("Nothing to do. The probe config can use it as it is.")
        return 0

    # 32 random bytes, base64 encoded. Exactly what `openssl rand -base64 32`
    # produces, and what ESPHome's api.encryption.key expects: 44 characters
    # ending in '='.
    key = base64.b64encode(secrets.token_bytes(32)).decode()

    body = text.rstrip("\n")
    body += (
        "\n\n"
        "# The temperature probe board. Its own key, not the blaster's: one leaked\n"
        "# string should not open two devices. Set by scripts/probe-key.py.\n"
        f"{KEY_NAME}: \"{key}\"\n"
    )
    SECRETS.write_text(body)

    print()
    where = SECRETS.relative_to(ROOT)
    print(f"{GREEN}Made a key for the probe board.{RESET} {DIM}Written to {where}{RESET}")
    print(f"  {KEY_NAME}: {mask(key)}")
    print()
    print("That file is gitignored and the key is never printed unless asked for,")
    print("so there is nothing to copy and nothing to keep safe by hand.")
    print()
    print(f"{DIM}Next: the probe config refers to it as !secret {KEY_NAME},{RESET}")
    print(f"{DIM}so flashing will pick it up with no further steps.{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
