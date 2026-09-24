#!/usr/bin/env python3
"""Save the last few nights off the Sleep Analyzer, exactly as Withings sends them.

    ./scripts/withings-capture.py             # the last week, user.activity only
    ./scripts/withings-capture.py --days 2
    ./scripts/withings-capture.py --scope user.info,user.metrics,user.activity

The backend gets written against a real night from the real mat, not against
the demo account's Aura Sensor V2, so this runs first. It signs in once in the
browser, asks getsummary for every night Withings has touched in the last few
days, asks get for the detail of each one, and writes every response to disk
byte for byte.

Then it prints what those responses settled: whether night_events came back,
which fields a UK Sleep Analyzer fills in, whether the intervals cover the night
without gaps, and how long after getting up the night appeared. Structure and
durations only, never a heart rate or a breathing rate, so the printout is the
thing to paste into a conversation. The files are not: they are a night of
vitals, and they carry the device hash on every entry.

Everything lands in backend/data/withings/, which .gitignore already keeps out
of the repository. The tokens are never written anywhere. Running this again is
ten seconds in a browser, and a refresh token sitting in a file is a thing that
can leak.

Only user.activity is asked for by default. Which scope carries sleep is one of
the open questions in docs/withings.md, and asking for it on its own answers it.
If the sleep calls come back unauthorised, that is the answer, and running again
with the wider --scope above gets the night anyway.

Needs nothing but Python. The client ID and secret come from backend/.env as
HS_WITHINGS_CLIENT_ID and HS_WITHINGS_CLIENT_SECRET, and are asked for if they
are not there. The browser is sent back to http://127.0.0.1:8910/callback, which
is registered on the Withings dashboard for exactly this.
"""

from __future__ import annotations

import argparse
import collections
import getpass
import http.server
import json
import os
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "backend" / ".env"
OUT = ROOT / "backend" / "data" / "withings"

AUTHORIZE = "https://account.withings.com/oauth2_user/authorize2"
TOKEN = "https://wbsapi.withings.net/v2/oauth2"
SLEEP = "https://wbsapi.withings.net/v2/sleep"

PORT = 8910
REDIRECT = f"http://127.0.0.1:{PORT}/callback"

#: Long enough to find the tab and type a password. Past it, something is wrong.
SIGN_IN_WITHIN_S = 300

#: getsummary pages at 300 nights. A week is one page; this only stops a loop
#: that the server keeps saying `more` to from running forever.
MAX_PAGES = 10

#: Every getsummary field the aiowithings client asks for, which Home Assistant
#: sends every day. A field name Withings does not know could fail the whole
#: call, so nothing goes in here that has not been asked for in anger.
SUMMARY_FIELDS = (
    "nb_rem_episodes",
    "sleep_efficiency",
    "sleep_latency",
    "total_sleep_time",
    "total_timeinbed",
    "wakeup_latency",
    "waso",
    "apnea_hypopnea_index",
    "breathing_disturbances_intensity",
    "asleepduration",
    "deepsleepduration",
    "hr_average",
    "hr_min",
    "hr_max",
    "lightsleepduration",
    "mvt_active_duration",
    "mvt_score_avg",
    "night_events",
    "out_of_bed_count",
    "remsleepduration",
    "rr_average",
    "rr_min",
    "rr_max",
    "sleep_score",
    "snoring",
    "snoringepisodecount",
    "wakeupcount",
    "wakeupduration",
    "withings_index",
)

#: Every get field asked for against the demo account on 13 September, without
#: complaint. Four of them came back missing on the Aura Sensor V2, and whether
#: they come back from the Sleep Analyzer is one of the things this is for.
SERIES_FIELDS = (
    "hr",
    "rr",
    "snoring",
    "sdnn_1",
    "rmssd",
    "hrv_quality",
    "mvt_score",
    "chest_movement_rate",
    "withings_index",
    "breathing_sounds",
)

#: What a summary and an interval carried on the demo account. Anything else is
#: new, and worth knowing about before the parser is written.
SUMMARY_KEYS = {
    "id", "startdate", "enddate", "date", "timezone", "hash_deviceid",
    "model", "model_id", "created", "modified", "completed", "data",
}
INTERVAL_KEYS = {"startdate", "enddate", "state", "hash_deviceid", "model", "model_id"}

STATES = {0: "awake", 1: "light", 2: "deep", 3: "rem", 4: "manual", 5: "unspecified",
          15: "out of bed"}

#: Which summary duration each state should add up to, if the intervals tile.
STATE_TOTALS = {
    "awake": "wakeupduration",
    "light": "lightsleepduration",
    "deep": "deepsleepduration",
    "rem": "remsleepduration",
}

#: Statuses that mean the token does not open this door. On a scope question
#: this is the answer, not a fault.
REFUSED = {100, 101, 102, 200, 214, 277, 401, 2553, 2554, 2555}

BOLD, DIM, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[0m"


# --- Talking to Withings -------------------------------------------------------


def setting(name: str) -> str:
    """Read a setting the way the service will, from .env if it is there."""
    if os.environ.get(name):
        return os.environ[name].strip()
    if ENV.exists():
        match = re.search(rf"^\s*{name}\s*=\s*(.*?)\s*$", ENV.read_text(), re.M)
        if match:
            return match.group(1).split("#")[0].strip().strip("\"'")
    return ""


def _tls() -> ssl.SSLContext:
    """certifi's roots if they are installed, the system's if not.

    Python from python.org on a Mac ships without any roots until its Install
    Certificates script has been run, and fails every HTTPS call until then.
    The backend's virtual environment has certifi, so running this with that
    Python gets round it.
    """
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def post(url: str, fields: dict[str, object], token: str | None = None) -> tuple[bytes, dict]:
    """One call, and both the bytes as they arrived and what they say."""
    request = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(), method="POST"
    )
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30, context=_tls()) as response:
            raw = response.read()
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError):
            sys.exit(
                f"{RED}Could not check Withings' certificate.{RESET} Run it with the "
                "backend's Python instead:\n  backend/.venv/bin/python scripts/withings-capture.py"
            )
        sys.exit(f"{RED}Could not reach {url}:{RESET} {exc}")
    return raw, json.loads(raw)


class _Callback(http.server.BaseHTTPRequestHandler):
    """Catches the one redirect Withings sends the browser back with."""

    got: dict[str, str] | None = None

    def do_GET(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        if url.path != "/callback":
            # The browser asks for a favicon too. Not the redirect.
            self.send_error(404)
            return
        type(self).got = dict(urllib.parse.parse_qsl(url.query))
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<p>Got it. You can close this tab and go back to the terminal.</p>")

    def log_message(self, *args: object) -> None:
        # The terminal is for the report.
        pass


def sign_in(client_id: str, client_secret: str, scope: str) -> dict:
    """The browser half of OAuth, and the exchange straight after it.

    The code in the redirect dies thirty seconds after it is issued, so nothing
    happens between catching it and trading it in.
    """
    state = secrets.token_urlsafe(16)
    url = AUTHORIZE + "?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "scope": scope,
            "redirect_uri": REDIRECT,
            "state": state,
        }
    )
    try:
        server = http.server.HTTPServer(("127.0.0.1", PORT), _Callback)
    except OSError:
        sys.exit(f"{RED}Port {PORT} is already in use.{RESET} Is the old spike still running?")
    server.timeout = 1

    print(f"Opening the Withings sign-in in the browser, asking for {BOLD}{scope}{RESET}.")
    print(f"{DIM}If nothing opens, paste this in:{RESET}\n  {url}\n")
    webbrowser.open(url)

    deadline = time.monotonic() + SIGN_IN_WITHIN_S
    while _Callback.got is None:
        if time.monotonic() > deadline:
            server.server_close()
            sys.exit(f"{RED}Nothing came back from the browser in five minutes.{RESET}")
        server.handle_request()
    server.server_close()

    got = _Callback.got
    if got.get("state") != state:
        sys.exit(f"{RED}The redirect was for a different sign-in.{RESET} Close old tabs and rerun.")
    if "code" not in got:
        sys.exit(f"{RED}Withings did not authorise:{RESET} {got.get('error', 'no code in the redirect')}")

    _, answer = post(
        TOKEN,
        {
            "action": "requesttoken",
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": got["code"],
            "redirect_uri": REDIRECT,
        },
    )
    if answer.get("status") != 0:
        sys.exit(
            f"{RED}Token exchange failed, status {answer.get('status')}:{RESET} "
            f"{answer.get('error', '')}"
        )
    return answer["body"]


def check(answer: dict, what: str, scope: str) -> None:
    """Stop, and say why, on anything but a clean answer. The file is saved first."""
    status = answer.get("status")
    if status == 0:
        return
    print(f"{RED}{what} came back with status {status}:{RESET} {answer.get('error', '')}")
    if status in REFUSED and scope == "user.activity":
        print(
            f"\n{BOLD}That answers the scope question:{RESET} user.activity on its own does not "
            "open sleep. Run it again with\n"
            "  --scope user.info,user.metrics,user.activity"
        )
    elif status == 601:
        print("Rate limited. Leave it an hour.")
    sys.exit(1)


def save(path: Path, raw: bytes) -> None:
    path.write_bytes(raw)
    path.chmod(0o600)


def capture(token: str, scope: str, days: int, folder: Path) -> list[tuple[dict, list[dict]]]:
    """Every night touched in the last `days`, and the detail of each."""
    since = int(time.time()) - days * 86400
    nights: list[dict] = []
    offset = 0
    for page in range(1, MAX_PAGES + 1):
        fields: dict[str, object] = {
            "action": "getsummary",
            "lastupdate": since,
            "data_fields": ",".join(SUMMARY_FIELDS),
        }
        if offset:
            fields["offset"] = offset
        raw, answer = post(SLEEP, fields, token)
        save(folder / f"getsummary-{page}.json", raw)
        check(answer, "getsummary", scope)
        body = answer.get("body") or {}
        nights += body.get("series") or []
        # Loop on `more`, and take the offset the server gives. The last page
        # comes back with offset 0, and rows are filtered after the page is cut,
        # so neither the offset nor a count of rows is a safe loop condition.
        if not body.get("more"):
            break
        offset = body.get("offset", 0)

    out = []
    for n, night in enumerate(nights, 1):
        raw, answer = post(
            SLEEP,
            {
                "action": "get",
                "startdate": night["startdate"],
                "enddate": night["enddate"],
                "data_fields": ",".join(SERIES_FIELDS),
            },
            token,
        )
        save(folder / f"get-{night.get('date', 'undated')}-{n}.json", raw)
        check(answer, "get", scope)
        out.append((night, (answer.get("body") or {}).get("series") or []))
    return out


# --- Saying what came back -----------------------------------------------------


def shape(value: object, depth: int = 0) -> str:
    """What a value looks like, without what it says."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        # night_events arrives as JSON packed inside a string. Describing that
        # as "a string" hid the most important answer the first real run had.
        if value.startswith(("{", "[")):
            try:
                return f"JSON inside a string, {shape(json.loads(value), depth)}"
            except ValueError:
                pass
        return "a string"
    if isinstance(value, list):
        if not value:
            return "an empty list"
        return f"a list of {len(value)}, the first {shape(value[0], depth + 1)}"
    if isinstance(value, dict):
        if not value:
            return "an empty object"
        keys = list(value)
        if all(k.isdigit() and len(k) >= 9 for k in keys):
            return f"{len(keys)} timestamps, each mapping to {shape(value[keys[0]], depth + 1)}"
        if depth >= 2:
            return f"an object with keys {', '.join(keys[:10])}"
        inner = "; ".join(f"{k}: {shape(v, depth + 1)}" for k, v in list(value.items())[:10])
        more = f"; and {len(keys) - 10} more" if len(keys) > 10 else ""
        return "{" + inner + more + "}"
    return type(value).__name__


def span(seconds: float) -> str:
    minutes = round(seconds / 60)
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def zone(name: str | None) -> tzinfo | None:
    try:
        return ZoneInfo(name) if name else None
    except ZoneInfoNotFoundError:
        return None


def clock(ts: int, tz: tzinfo | None) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%a %d %b %H:%M")


def report_night(night: dict, entries: list[dict]) -> list[str]:
    tz = zone(night.get("timezone"))
    data = night.get("data") or {}
    lines = [
        f"{BOLD}Night of {night.get('date')}{RESET}  ({night.get('timezone')})",
        (
            f"  in bed {clock(night['startdate'], tz)} to {clock(night['enddate'], tz)}, "
            f"{night.get('model')} (model_id {night.get('model_id')}), "
            f"completed={night.get('completed')!r}"
        ),
    ]
    if isinstance(night.get("created"), int):
        # Usually before, not after. A night is created a minute or two after
        # the first time out of bed and then stretched each time I get back in,
        # so a 4am trip to the bathroom creates it hours before the morning.
        after = night["created"] - night["enddate"]
        lines.append(
            f"  created {span(abs(after))} {'after' if after >= 0 else 'before'} the last time "
            "out of bed; last modified "
            f"{clock(night['modified'], tz) if isinstance(night.get('modified'), int) else '?'}"
        )

    # The one this whole exercise hinges on. Absent and null are different
    # answers, and the demo account gave the second.
    if "night_events" not in data:
        events = "absent"
    else:
        events = shape(data["night_events"])
    lines.append(f"  {BOLD}night_events:{RESET} {events}")

    present = [f for f in SUMMARY_FIELDS if data.get(f) is not None]
    null = [f for f in SUMMARY_FIELDS if f in data and data[f] is None]
    absent = [f for f in SUMMARY_FIELDS if f not in data]
    extra = sorted(set(data) - set(SUMMARY_FIELDS))
    lines.append(f"  summary fields: {len(present)} of {len(SUMMARY_FIELDS)} came back with a value")
    if null:
        lines.append(f"    null:   {', '.join(null)}")
    if absent:
        lines.append(f"    absent: {', '.join(absent)}")
    if extra:
        lines.append(f"    not asked for, sent anyway: {', '.join(extra)}")
    unexpected = sorted(set(night) - SUMMARY_KEYS)
    if unexpected:
        lines.append(f"    new keys on the summary itself: {', '.join(unexpected)}")

    if not entries:
        lines.append(f"  {RED}get returned no intervals for this night{RESET}")
        return lines

    entries = sorted(entries, key=lambda e: e["startdate"])
    lines.append(
        f"  detail: {len(entries)} intervals, "
        f"{clock(entries[0]['startdate'], tz)} to {clock(entries[-1]['enddate'], tz)}"
    )

    per_state: dict[str, list[int]] = collections.defaultdict(list)
    for e in entries:
        per_state[STATES.get(e.get("state"), f"state {e.get('state')}")].append(
            e["enddate"] - e["startdate"]
        )
    lines.append(
        "    states:  "
        + ", ".join(f"{s} {len(v)} ({span(sum(v))})" for s, v in sorted(per_state.items()))
    )
    said = [
        f"{s} {span(data[f])}" for s, f in STATE_TOTALS.items() if isinstance(data.get(f), int)
    ]
    if said:
        lines.append(f"    summary: {', '.join(said)}")

    # Do the intervals tile the night? Gaps and overlaps between neighbours, and
    # how the whole run lines up with the summary's own window.
    gaps, overlaps = [], []
    for a, b in zip(entries, entries[1:]):
        step = b["startdate"] - a["enddate"]
        if step > 0:
            gaps.append(step)
        elif step < 0:
            overlaps.append(-step)
    lines.append(
        "    gaps:    "
        + (f"{len(gaps)}, {span(sum(gaps))} in all, longest {span(max(gaps))}" if gaps else "none")
    )
    lines.append(
        "    overlaps: "
        + (f"{len(overlaps)}, longest {span(max(overlaps))}" if overlaps else "none")
    )
    early = night["startdate"] - entries[0]["startdate"]
    late = entries[-1]["enddate"] - night["enddate"]
    if early == 0 and late == 0:
        lines.append("    against the summary: starts and ends exactly with it")
    else:
        lines.append(
            f"    against the summary: starts {span(abs(early))} "
            f"{'before' if early >= 0 else 'after'}, "
            f"ends {span(abs(late))} {'after' if late >= 0 else 'before'}"
        )

    # Each metric: on how many intervals, how far apart its samples are, and
    # whether they stay inside the interval that carries them.
    for field in SERIES_FIELDS:
        maps = [e[field] for e in entries if isinstance(e.get(field), dict)]
        nulls = sum(1 for e in entries if field in e and e[field] is None)
        if not maps:
            # Null and absent both mean "not available", but they are not the
            # same answer from the server, so they are counted apart.
            absent = len(entries) - nulls
            if not nulls:
                said = "absent on every interval"
            elif not absent:
                said = "null on every interval"
            else:
                said = f"never a value: null on {nulls}, absent on {absent}"
            lines.append(f"    {field:<20} {said}")
            continue
        keys_bad = sum(1 for m in maps for k in m if not str(k).isdigit())
        stamps = sorted(int(k) for m in maps for k in m if str(k).isdigit())
        steps = collections.Counter(b - a for a, b in zip(stamps, stamps[1:]))
        common = steps.most_common(1)[0][0] if steps else None
        outside = sum(
            1
            for e in entries
            if isinstance(e.get(field), dict)
            for k in e[field]
            if str(k).isdigit() and not e["startdate"] <= int(k) <= e["enddate"]
        )
        empty = sum(1 for m in maps for v in m.values() if v is None)
        note = f"on {len(maps)}/{len(entries)}, {len(stamps)} samples"
        if common is not None:
            odd = sum(n for s, n in steps.items() if s != common)
            note += f", every {common}s" + (f" ({odd} other steps)" if odd else "")
        if outside:
            note += f", {outside} outside their interval"
        if empty:
            note += f", {empty} null"
        if nulls:
            note += f", null on {nulls} intervals"
        if keys_bad:
            note += f", {keys_bad} keys that are not timestamps"
        lines.append(f"    {field:<20} {note}")

    hashed = sum(1 for e in entries if "hash_deviceid" in e)
    lines.append(f"    hash_deviceid on {hashed} of {len(entries)} intervals")
    unexpected = sorted({k for e in entries for k in e} - INTERVAL_KEYS - set(SERIES_FIELDS))
    if unexpected:
        lines.append(f"    new keys on the intervals: {', '.join(unexpected)}")
    return lines


# --- The whole thing -----------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=7, help="how far back to look (default 7)")
    parser.add_argument(
        "--scope",
        default="user.activity",
        help="what to ask for (default user.activity, which is the scope question)",
    )
    args = parser.parse_args()

    client_id = setting("HS_WITHINGS_CLIENT_ID") or input("Withings client ID: ").strip()
    client_secret = setting("HS_WITHINGS_CLIENT_SECRET") or getpass.getpass(
        "Withings client secret (not shown): "
    ).strip()
    if not client_id or not client_secret:
        sys.exit("Both the client ID and the secret are needed.")

    tokens = sign_in(client_id, client_secret, args.scope)
    print(
        f"{GREEN}Signed in.{RESET} Granted {BOLD}{tokens.get('scope')}{RESET}, "
        f"access token good for {tokens.get('expires_in')}s.\n"
    )

    folder = OUT / f"capture-{datetime.now():%Y%m%d-%H%M%S}"
    folder.mkdir(parents=True)
    folder.chmod(0o700)
    # What was asked, beside what came back, so a fixture built from these later
    # knows its own request. Never the tokens.
    (folder / "asked.json").write_text(
        json.dumps(
            {
                "scope_asked": args.scope,
                "scope_granted": tokens.get("scope"),
                "days": args.days,
                "summary_fields": SUMMARY_FIELDS,
                "series_fields": SERIES_FIELDS,
            },
            indent=2,
        )
        + "\n"
    )

    nights = capture(tokens["access_token"], args.scope, args.days, folder)
    if not nights:
        print(
            f"{RED}No nights came back from the last {args.days} days.{RESET} Check last night "
            "shows in the Withings app. If it does, it has not reached the API yet."
        )
        return

    lines = [f"{len(nights)} night(s), scope {tokens.get('scope')}", ""]
    for night, entries in nights:
        lines += report_night(night, entries) + [""]
    text = "\n".join(lines)
    print(text)
    (folder / "report.txt").write_text(re.sub(r"\033\[\d+m", "", text))
    print(
        f"{DIM}Raw responses and this report are in {folder.relative_to(ROOT)}. "
        f"Nothing there is in git.{RESET}"
    )


if __name__ == "__main__":
    main()
