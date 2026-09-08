#!/usr/bin/env python3
"""Run the service, the blaster's log and the plug as one stream in one window.

Started by ./scripts/dev.sh once the service is driving real hardware. Nothing
here is needed against the simulator, where there is only one thing to watch.

Three Terminal tabs is a poor way to follow a night, because the interesting
moments are exactly the ones where the three disagree. Tagged and interleaved,
they read in the order things actually happened:

    app     what the service decided, and what it believes the unit is on
    ir      that the infrared really left the board
    plug    watts the unit is drawing, the one honest measurement here

Each child gets a pseudo-terminal rather than a pipe. Piped output is block
buffered, so lines would arrive in clumps of several kilobytes, which for
something meant to be read as it happens is useless. Each also gets its own
process group, so one Ctrl-C stops all three rather than leaving the web server
holding port 8000.
"""

from __future__ import annotations

import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "backend" / ".env"
ESPHOME = Path.home() / "esphome" / "bin" / "esphome"

RESET = "\033[0m"
DIM = "\033[2m"

#: How long to give every child to stop politely before insisting.
GRACE_S = 2.0

#: ESPHome's log is already coloured. A second set of codes inside a line makes a
#: mess of the tag colour, so the tag is closed before the line begins.
ANSI_RESET_HUNT = re.compile(r"\x1b\[0?m\s*$")


def env(name: str, default: str = "") -> str:
    """One value out of backend/.env, without needing pydantic or the venv."""
    if not ENV.exists():
        return default
    pattern = re.compile(rf"^\s*{name}\s*=\s*(.*?)\s*$", re.M)
    match = pattern.search(ENV.read_text())
    if match is None:
        return default
    return match.group(1).split("#")[0].strip().strip("\"'")


class Stream:
    """One child process, its pty, and the tag its lines get."""

    def __init__(self, tag: str, colour: str, command: list[str], cwd: Path) -> None:
        self.tag = tag
        self.label = f"{colour}{tag:<4}{RESET}"
        self.buffer = ""
        self.done = False
        master, slave = pty.openpty()
        self.proc = subprocess.Popen(
            command,
            stdout=slave,
            stderr=slave,
            stdin=subprocess.DEVNULL,
            cwd=str(cwd),
            # Its own process group, so stopping it stops what it started.
            start_new_session=True,
        )
        os.close(slave)
        self.fd = master

    def fileno(self) -> int:
        return self.fd

    def pump(self) -> None:
        try:
            chunk = os.read(self.fd, 65536)
        except OSError:
            chunk = b""
        if not chunk:
            self.done = True
            return
        self.buffer += chunk.decode("utf-8", "replace")
        *lines, self.buffer = self.buffer.split("\n")
        for line in lines:
            print(f"{self.label} {line.rstrip()}", flush=True)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def signal(self, sig: int) -> None:
        try:
            os.killpg(os.getpgid(self.proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            pass


def build(python: str) -> list[Stream]:
    streams: list[Stream] = []

    # The service. Runs from backend/ because db_path is relative to it.
    streams.append(
        Stream(
            "app",
            "\033[36m",
            [python, "-u", "-m", "uvicorn", "hydrosnooze.main:app",
             "--host", "0.0.0.0", "--port", "8000"],
            ROOT / "backend",
        )
    )

    config = ROOT / "docs" / "esphome-hydrosnooze.yaml"
    if ESPHOME.exists() and config.exists():
        streams.append(Stream("ir", "\033[35m", [str(ESPHOME), "logs", str(config)], ROOT))
    else:
        note(f"No ESPHome at {ESPHOME}, so the blaster's own log is not shown.")

    host = env("HS_SHELLY_HOST")
    if host:
        streams.append(
            Stream("plug", "\033[32m", [python, "-u", str(ROOT / "scripts" / "plug.py"), host], ROOT)
        )
    else:
        note("No HS_SHELLY_HOST in backend/.env, so the plug is not shown.")

    return streams


def stop_all(streams: list[Stream]) -> None:
    """Signal every child at once, escalating only if some ignore it.

    One at a time would add each child's grace period together, so a Ctrl-C
    could sit there for the better part of a minute before the prompt came back.
    """
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        alive = [s for s in streams if s.alive()]
        if not alive:
            return
        for stream in alive:
            stream.signal(sig)
        deadline = time.monotonic() + GRACE_S
        while time.monotonic() < deadline and any(s.alive() for s in alive):
            time.sleep(0.05)


def note(text: str) -> None:
    print(f"{DIM}     {text}{RESET}", flush=True)


def _terminated(*_: object) -> None:
    """Being shut down should tidy up exactly as being interrupted does.

    Without this the web server outlives the window it was started from, and the
    next run fails on an address already in use, which reads like a bug in the
    service rather than a leftover from the last one.
    """
    raise KeyboardInterrupt


def main() -> int:
    signal.signal(signal.SIGTERM, _terminated)

    python = sys.executable
    print()
    print(f"{DIM}     One window, three views. app = what it decided, "
          f"ir = what was sent, plug = what was drawn.{RESET}")
    print(f"{DIM}     Ctrl-C stops all of them.{RESET}")
    print()

    streams = build(python)
    app = streams[0]

    try:
        while True:
            live = [s for s in streams if not s.done]
            if not live:
                break
            ready, _, _ = select.select(live, [], [], 0.4)
            for stream in ready:
                stream.pump()
            if app.proc.poll() is not None and app.done:
                # The service is the point. Without it the other two are noise.
                print()
                note("The service stopped, so the rest is stopping too.")
                break
    except KeyboardInterrupt:
        print()
    finally:
        stop_all(streams)

    print()
    print("Stopped.")
    return app.proc.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
