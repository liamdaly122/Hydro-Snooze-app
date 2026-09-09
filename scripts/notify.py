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


def ask_the_service(port: int = 8000) -> bool:
    """Have the running service send it, rather than sending it ourselves.

    Better than a direct send for two reasons. It is plain HTTP to localhost, so
    it sidesteps TLS entirely. And it proves the thing that actually matters:
    that the service picked the topic up out of .env. Sending from here would
    only ever prove that this terminal can reach ntfy.
    """
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/notify/test", data=b"", method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return response.status == 200
    except Exception:
        return False


def trusted_context():
    """An SSL context that trusts the CA bundle pip installed, if there is one.

    A Python from python.org does not use the macOS keychain: it ships its own
    OpenSSL with an empty trust store until `Install Certificates.command` has
    been run. That produces CERTIFICATE_VERIFY_FAILED for every HTTPS call from
    a stock script, while the service is unaffected because httpx carries
    certifi's bundle with it. Borrowing that same bundle costs nothing.
    """
    import ssl

    for path in (ROOT / "backend" / ".venv" / "lib").glob(
        "python*/site-packages/certifi/cacert.pem"
    ):
        return ssl.create_default_context(cafile=str(path))
    return None


def send(server: str, topic: str, title: str, message: str) -> None:
    request = urllib.request.Request(
        f"{server.rstrip('/')}/{topic}",
        data=message.encode(),
        headers={"Title": title, "Priority": "high", "Tags": "warning"},
    )
    with urllib.request.urlopen(request, timeout=10, context=trusted_context()):
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
        # The running service first. It proves more and needs no TLS.
        if ask_the_service():
            print(f"{GREEN}Sent by the service.{RESET} It should arrive within a second or two.")
            print(f"{DIM}That also confirms the service read the topic out of .env.{RESET}")
        else:
            print(f"{DIM}The service is not answering, sending directly instead.{RESET}")
            try:
                send(server, topic, "HydroSnooze", "Test notification. Setup is working.")
            except Exception as exc:
                print(f"{RED}Could not send it:{RESET} {exc}")
                if "CERTIFICATE_VERIFY" in str(exc):
                    print()
                    print("That is this Mac's Python having no certificates rather than")
                    print("anything being wrong with the setup. Fix it once, for every")
                    print("script you ever run:")
                    print()
                    print(f'  {BOLD}open "/Applications/Python 3.13/Install Certificates.command"{RESET}')
                    print()
                    print(f"{DIM}Adjust the version to match. The service is unaffected either{RESET}")
                    print(f"{DIM}way: it carries its own bundle, so it can already send.{RESET}")
                else:
                    print(f"{DIM}Check this machine is online. The service is unaffected.{RESET}")
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
