#!/usr/bin/env python3
"""Walk through capturing the eight infrared codes, one button at a time.

    ./scripts/capture.py

Flash docs/esphome-capture.yaml first, leave the board plugged in, and have the
HydroSnooze remote to hand. This starts the ESPHome log itself, asks for one
button at a time, waits for three presses that agree with each other, and says so
when they do not. At the end it writes a summary worth sending on.

What it checks that eyes cannot:

  - that three presses of one button really produced the same code, allowing for
    the jitter that stops raw timings ever repeating exactly
  - that holding a button a fraction too long is noticed and forgiven, rather
    than counted as a different code
  - that all eight decoded as the same protocol, and share one address
  - that no two buttons produced an identical code, which is what pressing the
    same button twice by mistake looks like

Nothing here talks to the board directly. It reads ESPHome's own log, so if this
script has a bad day the manual route still works: run `esphome logs` and write
the codes down by hand.
"""

from __future__ import annotations

import argparse
import os
import pty
import re
import select
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from hydrosnooze.ircodes import (
    BUTTONS,
    PRESS_WINDOW_S,
    Capture,
    Reading,
    best,
    cross_check,
    judge,
    parse_line,
    report,
)

DEFAULT_LOGS = "~/esphome/bin/esphome logs docs/esphome-capture.yaml"

#: Long enough that a slow first press does not look like a failure, short enough
#: that a genuinely dead setup does not leave someone waiting.
NUDGE_AFTER_S = 25.0

BOLD, DIM, GREEN, RED, AMBER, RESET = (
    "\033[1m",
    "\033[2m",
    "\033[32m",
    "\033[31m",
    "\033[33m",
    "\033[0m",
)


def say(text: str = "") -> None:
    print(text, flush=True)


class LogStream:
    """ESPHome's log, read through a pseudo-terminal.

    A pty rather than a pipe on purpose. Piped output gets block buffered, so
    lines would arrive in clumps of several kilobytes, which for something asking
    "press the button now" is useless.
    """

    def __init__(self, command: str) -> None:
        self.command = command
        self._buffer = ""
        parts = shlex.split(os.path.expanduser(command))
        self._master, slave = pty.openpty()
        try:
            self.proc = subprocess.Popen(
                parts, stdout=slave, stderr=slave, stdin=subprocess.DEVNULL, cwd=ROOT
            )
        except FileNotFoundError:
            os.close(self._master)
            os.close(slave)
            raise
        os.close(slave)

    def fileno(self) -> int:
        return self._master

    def read_lines(self) -> list[str]:
        try:
            chunk = os.read(self._master, 65536)
        except OSError:
            return []
        if not chunk:
            return []
        self._buffer += chunk.decode("utf-8", "replace")
        *lines, self._buffer = self._buffer.split("\n")
        return lines

    def close(self) -> None:
        with_suppress = (ProcessLookupError, OSError)
        try:
            self.proc.send_signal(signal.SIGINT)
            self.proc.wait(timeout=5)
        except with_suppress:
            pass
        except subprocess.TimeoutExpired:
            self.proc.kill()
        try:
            os.close(self._master)
        except OSError:
            pass


def collect_button(stream: LogStream, capture: Capture, wanted: int, log: list[str]) -> str:
    """Gather one button's presses. Returns "done", "redo" or "quit"."""
    capture.presses = []
    pending: list[Reading] = []
    pending_since = 0.0
    last_activity = time.monotonic()
    nudged = False
    interactive = sys.stdin.isatty()

    while True:
        watching = [stream]
        if interactive:
            watching.append(sys.stdin)
        ready, _, _ = select.select(watching, [], [], 0.2)

        if interactive and sys.stdin in ready:
            typed = sys.stdin.readline().strip().lower()
            if typed in ("q", "quit"):
                return "quit"
            say(f"{DIM}Starting {capture.name} again.{RESET}")
            return "redo"

        if stream in ready:
            for line in stream.read_lines():
                log.append(line)
                reading = parse_line(line)
                if reading is None:
                    continue
                if not pending:
                    pending_since = time.monotonic()
                pending.append(reading)
                last_activity = time.monotonic()
                nudged = False

        # A press is finished once the decoders stop describing it.
        if pending and time.monotonic() - pending_since > PRESS_WINDOW_S:
            chosen = best(pending)
            pending = []
            if chosen is None:
                continue
            capture.presses.append(chosen)
            n = len(capture.presses)
            say(f"    {GREEN}·{RESET} press {n} of {wanted}   {DIM}{chosen.describe()}{RESET}")

            if n >= wanted:
                verdict = judge(capture.presses, wanted)
                if verdict.ok:
                    say(f"    {GREEN}✓ {verdict.headline}{RESET}")
                    if verdict.detail:
                        say(f"      {DIM}{verdict.detail}{RESET}")
                    return "done"
                say(f"    {RED}✗ {verdict.headline}{RESET}")
                if verdict.detail:
                    say(f"      {AMBER}{verdict.detail}{RESET}")
                say(f"    {DIM}Trying {capture.name} again.{RESET}")
                say()
                return "redo"

        if not nudged and time.monotonic() - last_activity > NUDGE_AFTER_S:
            nudged = True
            say(
                f"    {AMBER}Nothing heard yet.{RESET} {DIM}Point the remote at the board from "
                f"about 10cm, square on. Enter to restart this button, q to stop.{RESET}"
            )

        if stream.proc.poll() is not None:
            say(f"{RED}The ESPHome log stopped. Is the board still plugged in?{RESET}")
            return "quit"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--presses", type=int, default=3, help="agreeing presses per button")
    parser.add_argument("--logs-cmd", default=DEFAULT_LOGS, help="how to start the ESPHome log")
    parser.add_argument(
        "--out",
        default=str(Path.home() / "Desktop" / "ir-capture.txt"),
        help="where to write the summary",
    )
    args = parser.parse_args()

    out = Path(os.path.expanduser(args.out))
    full = out.with_name(out.stem + "-full.log")

    say()
    say(f"{BOLD}HydroSnooze infrared capture{RESET}")
    say(f"{DIM}Three agreeing presses per button, eight buttons.{RESET}")
    say(f"{DIM}Enter restarts the button you are on. q stops and saves what you have.{RESET}")
    say()
    say(f"{DIM}Starting: {args.logs_cmd}{RESET}")

    try:
        stream = LogStream(args.logs_cmd)
    except FileNotFoundError:
        say(f"{RED}Could not run that.{RESET} Check ESPHome is installed:")
        say(f"  {DEFAULT_LOGS.split()[0]} version")
        return 1

    captures = [Capture(name, label) for name, label in BUTTONS]
    log: list[str] = []
    stopped = False

    try:
        for index, capture in enumerate(captures, 1):
            while True:
                say()
                say(
                    f"{BOLD}{index}/8  {capture.label}{RESET}"
                    f"   {DIM}saved as {capture.name}{RESET}"
                )
                say(f"    Press it {args.presses} times, about a second apart.")
                outcome = collect_button(stream, capture, args.presses, log)
                if outcome == "done":
                    break
                if outcome == "quit":
                    stopped = True
                    break
            if stopped:
                break
    except KeyboardInterrupt:
        stopped = True
        say()
        say(f"{DIM}Stopped. Saving what was captured.{RESET}")
    finally:
        stream.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report(captures, args.presses))
    full.write_text("\n".join(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line) for line in log))

    done = sum(1 for c in captures if judge(c.presses, args.presses).ok)
    say()
    say(f"{BOLD}{done} of 8 captured cleanly.{RESET}")

    problems = cross_check(captures)
    for problem in problems:
        say(f"{AMBER}  ! {problem}{RESET}")
    if done == 8 and not problems:
        say(f"{GREEN}  Nothing inconsistent across the eight.{RESET}")

    say()
    say(f"Summary   {out}")
    say(f"Full log  {full}")
    say(f"{DIM}Send the summary on. The full log is there if a code needs a second look.{RESET}")
    return 0 if done == 8 and not stopped else 1


if __name__ == "__main__":
    raise SystemExit(main())
