#!/usr/bin/env python3
"""Turn notifications on, and prove they arrive.

    ./scripts/notify.py              # set one up, or show the one already set
    ./scripts/notify.py --test       # send one to the phone right now
    ./scripts/notify.py --off        # stop sending

There is no account and no key. A topic is a string you invent; anything posted
to it reaches every phone subscribed to it. That also means the topic name is the
only secret there is, so this generates a long random one rather than letting a
guessable one get typed in.

Writes one line to backend/.env, replacing any it already put there, so running
it twice does not leave two.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "backend" / ".env"
EXAMPLE = ROOT / "backend" / ".env.example"

KEY = "HS_NTFY_TOPIC"
SERVER_KEY = "HS_NTFY_SERVER"
DEFAULT_SERVER = "https://ntfy.sh"

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"


def read(env: Path, key: str, default: str = "") -> str:
    if not env.exists():
        return default
    match = re.search(rf"^\s*{key}\s*=\s*(.*?)\s*$", env.read_text(), re.M)
    if match is None:
        return default
    return match.group(1).split("#")[0].strip().strip("\"'") or default


def write(env: Path, key: str, value: str | None) -> None:
    """Set one key, or remove it when value is None. Leaves everything else."""
    if not env.exists():
        env.parent.mkdir(parents=True, exist_ok=True)
        env.write_text(EXAMPLE.read_text() if EXAMPLE.exists() else "")
    lines = [ln for ln in env.read_text().splitlines() if not re.match(rf"^\s*#?\s*{key}\s*=", ln)]
    while lines and not lines[-1].strip():
        lines.pop()
    if value is not None:
        lines += ["", "# Notifications, set by scripts/notify.py", f"{key}={value}"]
    env.write_text("\n".join(lines) + "\n")


def send(server: str, topic: str, title: str, message: str) -> None:
    request = urllib.request.Request(
        f"{server.rstrip('/')}/{topic}",
        data=message.encode(),
        headers={"Title": title, "Priority": "high", "Tags": "warning"},
    )
    with urllib.request.urlopen(request, timeout=10):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="store_true", help="send one now")
    parser.add_argument("--off", action="store_true", help="stop sending")
    parser.add_argument("--topic", default=None, help="use this instead of a generated one")
    parser.add_argument("--env", default=None, help="which .env to write (the Pi uses its own)")
    args = parser.parse_args()

    env = Path(args.env).expanduser() if args.env else ENV
    shown = env if args.env else env.relative_to(ROOT)
    server = read(env, SERVER_KEY, DEFAULT_SERVER)

    if args.off:
        write(env, KEY, None)
        print(f"\nNotifications off. {shown} updated.")
        print("Restart the service for it to take effect.")
        return 0

    topic = read(env, KEY)
    if args.topic:
        topic = args.topic
        write(env, KEY, topic)
    elif not topic:
        # Long and random on purpose: whoever knows this string can read the
        # notifications, and it is the only thing standing between them and it.
        topic = f"hydrosnooze-{secrets.token_hex(6)}"
        write(env, KEY, topic)
        print(f"\n{GREEN}Set up a new topic.{RESET} {DIM}Written to {shown}{RESET}")
    else:
        print(f"\n{DIM}Already set in {shown}{RESET}")

    print()
    print("  1. Install " + BOLD + "ntfy" + RESET + " on your phone, from the App Store")
    print("  2. Tap +, then Subscribe to topic")
    print("  3. Enter exactly this:")
    print()
    print(f"       {BOLD}{topic}{RESET}")
    print()
    print(f"{DIM}  Anyone who knows that string can read your notifications, so do not{RESET}")
    print(f"{DIM}  share it. Nothing else is needed: no account, no password.{RESET}")

    if args.test:
        print()
        try:
            send(server, topic, "HydroSnooze", "Test notification. Setup is working.")
        except (urllib.error.URLError, OSError) as exc:
            print(f"{RED}Could not send it:{RESET} {exc}")
            print(f"{DIM}Check this machine is online. The service is unaffected either way.{RESET}")
            return 1
        print(f"{GREEN}Sent.{RESET} It should arrive on the phone within a second or two.")
        print(f"{DIM}Nothing arriving means the app is subscribed to a different string.{RESET}")
    else:
        print()
        print(f"{DIM}  Once subscribed, prove it:  ./scripts/notify.py --test{RESET}")

    print()
    print("Restart the service for the change to take effect: Ctrl-C, then ./scripts/dev.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
