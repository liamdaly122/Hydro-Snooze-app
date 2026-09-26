#!/usr/bin/env python3
"""Set the app's password, which is what lets it be reached through Tailscale.

    ./scripts/password.py --env /opt/hydrosnooze/.env      # on the Pi
    ./scripts/password.py                                   # on the Mac
    ./scripts/password.py --off --env /opt/hydrosnooze/.env # no password again

Asks for the password twice and writes only a hash of it into .env, so a copy of
that file does not give the password away. The first time, it also writes a key
for the other scripts on the Pi, which cannot type a password.

Changing the password signs every device out. That is on purpose: the moment
somebody changes a password is the moment they think somebody else has it.

Needs nothing but Python, like notify.py, so it runs on the Pi without the
service's own packages. The hash is the same shape backend/hydrosnooze/access.py
checks, and a test holds the two together.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "backend" / ".env"
EXAMPLE = ROOT / "backend" / ".env.example"

HASH_KEY = "HS_PASSWORD_HASH"
API_KEY = "HS_API_KEY"

#: The same as access.MIN_PASSWORD. With ten guesses allowed every quarter of an
#: hour, ten random letters outlast the Pi; a word from the dictionary does not.
MIN_PASSWORD = 10

#: The same as access.py. Changing one without the other would lock the door.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:{salt.hex()}:{digest.hex()}"


def read(env: Path, key: str) -> str:
    if not env.exists():
        return ""
    match = re.search(rf"^\s*{key}\s*=\s*(.*?)\s*$", env.read_text(), re.M)
    if match is None:
        return ""
    return match.group(1).split("#")[0].strip().strip("\"'")


def write(env: Path, key: str, value: str | None, comment: str) -> None:
    """Set one key, or remove it when value is None. Leaves everything else."""
    if not env.exists():
        env.parent.mkdir(parents=True, exist_ok=True)
        env.write_text(EXAMPLE.read_text() if EXAMPLE.exists() else "")
    lines = [ln for ln in env.read_text().splitlines() if not re.match(rf"^\s*#?\s*{key}\s*=", ln)]
    lines = [ln for ln in lines if ln.strip() != f"# {comment}"]
    while lines and not lines[-1].strip():
        lines.pop()
    if value is not None:
        lines += ["", f"# {comment}", f"{key}={value}"]
    env.write_text("\n".join(lines) + "\n")


def ask() -> str | None:
    first = getpass.getpass("New password: ")
    if len(first) < MIN_PASSWORD:
        print(f"{RED}Too short.{RESET} At least {MIN_PASSWORD} characters. Three or four")
        print("unrelated words is easy to type on a phone and hard to guess.")
        return None
    if getpass.getpass("Same again: ") != first:
        print(f"{RED}Those did not match.{RESET} Nothing was changed.")
        return None
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=None, help="which .env to write (the Pi uses /opt/hydrosnooze/.env)")
    parser.add_argument("--off", action="store_true", help="remove the password")
    parser.add_argument("--new-key", action="store_true", help="replace the scripts' key too")
    args = parser.parse_args()

    env = Path(args.env).expanduser() if args.env else ENV
    shown = env if args.env else env.relative_to(ROOT)

    if args.off:
        write(env, HASH_KEY, None, "The app's password, set by scripts/password.py")
        print(f"\nPassword removed from {shown}.")
        print("The app answers on the home network with no password again, and refuses")
        print("everything that comes through Tailscale until one is set.")
        print("\nRestart the service for it to take effect:  sudo systemctl restart hydrosnooze")
        return 0

    password = ask()
    if password is None:
        return 1

    had = bool(read(env, HASH_KEY))
    write(env, HASH_KEY, hash_password(password), "The app's password, set by scripts/password.py")
    if args.new_key or not read(env, API_KEY):
        write(
            env,
            API_KEY,
            secrets.token_urlsafe(32),
            "What the scripts on this machine sign in with, set by scripts/password.py",
        )

    print()
    print(f"{GREEN}Password {'changed' if had else 'set'}.{RESET} {DIM}A hash of it is in {shown}{RESET}")
    if had:
        print("Every device is signed out and needs the new password.")
    print()
    print("Restart the service for it to take effect:")
    print()
    print(f"  {BOLD}sudo systemctl restart hydrosnooze{RESET}      {DIM}on the Pi{RESET}")
    print(f"  {DIM}or Ctrl-C and ./scripts/dev.sh on the Mac{RESET}")
    print()
    print(f"{DIM}Forgotten it? Run this again on the Pi. Nothing else is needed.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
