"""The loop that fetches sleep, beside the bed and never in its way.

**The rule that outranks the rest: the bed never depends on any of this.** This
runs as its own task, takes its own lock and never the command lock, and every
way it can fail ends in a missing chart rather than a cold bed. It does not push
to the phone either. Withings being unreachable at 3am is not worth waking
anybody for, so nothing it says contains a phrase the notifier treats as loud.

It keeps real time, not the service's clock. The simulator runs that clock at up
to 120 times real speed, and "every half hour" on it would be every fifteen
seconds against somebody else's server.

Every half hour, while connected:

    1. wait for a clock that has genuinely been set, however long that takes
    2. refresh the token if it is close to running out, and keep the new pair
       on disk before using it
    3. ask getsummary for every night modified since the newest one held
    4. for each night that changed, ask get for its detail, and replace the
       stored night whole

The nights grow. One exists a minute or two after the first time out of bed and
is modified again the following night, so the same night comes back several
times, and every time it does it replaces what was there.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, TypeVar

from .. import clocksync
from ..config import Settings
from ..db import Database, StoredNight
from ..events import EventLog
from . import parse
from .client import REDIRECT_HOSTS, WithingsClient, WithingsError, redirect_uri

log = logging.getLogger(__name__)

#: What goes in the event log. Never a loud phrase: see notify.LOUD_WARNINGS.
KIND = "withings"

#: How often to ask. Inside the documented limit of once per ten minutes, and far
#: more often than a night changes.
SYNC_EVERY_S = 30 * 60

#: After a start, before the first pass. Everything to do with the bed comes up
#: first.
FIRST_SYNC_AFTER_S = 60

#: Refresh this long before the access token runs out, rather than waiting for a
#: refusal: a refusal is a wasted call, and it reads the same as a broken one.
REFRESH_BEFORE_S = 10 * 60

#: How far back the first pass after connecting looks.
BACKFILL_S = 30 * 24 * 60 * 60

#: The least time between two passes somebody asked for. Pulling to refresh
#: repeatedly should not be what gets this service rate-limited.
MANUAL_GAP_S = 10 * 60

#: What 601, too many requests, costs.
RATE_LIMITED_FOR_S = 60 * 60

#: How long a sign-in started from the app stays good for.
SIGN_IN_LASTS_S = 10 * 60

T = TypeVar("T")


class ConnectProblem(Exception):
    """Something the person connecting can act on, in words they can act on."""


class WithingsSync:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        events: EventLog,
        *,
        client: WithingsClient | None = None,
        wall: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.db = db
        self.events = events
        self.configured = bool(settings.withings_client_id and settings.withings_client_secret)
        if client is None and self.configured:
            client = WithingsClient(settings.withings_client_id, settings.withings_client_secret)
        self._client = client
        self._wall = wall
        self._sleep = sleep
        self._pass_lock = asyncio.Lock()
        #: Sign-ins in flight: state to (when it runs out, where to come back to).
        self._signing_in: dict[str, tuple[int, str]] = {}

        self.last_attempt_at: int | None = None
        self.last_sync_at: int | None = None
        self.last_error: str | None = None
        self.waiting_for_clock = False
        self._rested_until: int | None = None
        #: Passes started by sync_soon, held so they are not collected mid-flight.
        self._background: set[asyncio.Task[bool]] = set()

    def _now(self) -> int:
        return int(self._wall())

    # --- Connecting -------------------------------------------------------------

    def begin_connect(self, host: str) -> str:
        """Where to send the browser to sign in, for a browser that reached us at `host`.

        The way back has to be one of the addresses registered with Withings,
        and it has to be the one this browser can reach. So it comes from the
        address the browser used, and anything else is refused here, in words,
        rather than by Withings with a page that says nothing useful.
        """
        if not self.configured or self._client is None:
            raise ConnectProblem(
                "Withings is not set up on this machine. Put HS_WITHINGS_CLIENT_ID and "
                "HS_WITHINGS_CLIENT_SECRET in its .env and restart it."
            )
        if host not in REDIRECT_HOSTS:
            raise ConnectProblem(
                f"Withings can only send you back to {' or '.join(REDIRECT_HOSTS)}, and this "
                f"page was opened at {host}. Open the app at http://{REDIRECT_HOSTS[0]} "
                "and connect from there."
            )
        now = self._now()
        self._signing_in = {s: v for s, v in self._signing_in.items() if v[0] > now}
        state = secrets.token_urlsafe(24)
        back = redirect_uri(host)
        self._signing_in[state] = (now + SIGN_IN_LASTS_S, back)
        return self._client.authorize_url(back, state)

    async def finish_connect(self, code: str, state: str) -> None:
        """The browser is back with a code. Trade it in at once: it lasts thirty
        seconds, and then keep the tokens before anything else happens."""
        pending = self._signing_in.pop(state, None)
        now = self._now()
        if pending is None or pending[0] < now or self._client is None:
            raise ConnectProblem(
                "That sign-in was not started here, or took longer than ten minutes. "
                "Press Connect again."
            )
        try:
            tokens = await self._client.exchange(code, pending[1], now)
        except WithingsError as exc:
            raise ConnectProblem(f"Withings did not accept the sign-in ({exc}).") from exc

        again = self.db.withings_account() is not None
        self.db.save_withings_tokens(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            scope=tokens.scope,
            user_id=tokens.user_id,
            now=now,
        )
        account = self.db.withings_account()
        if account is not None and account.last_update is None:
            self.db.set_withings_last_update(now - BACKFILL_S)
        self.last_error = None
        self.events.info(
            KIND,
            "Withings reconnected."
            if again
            else "Withings connected. The last month of sleep is on its way.",
        )

    def refused(self, why: str) -> None:
        """A sign-in that did not work, kept for the app to say."""
        self.last_error = why

    def disconnect(self) -> None:
        if self.db.withings_account() is None:
            return
        self.db.forget_withings()
        self.last_error = None
        self.events.info(KIND, "Withings disconnected. The nights already here stay.")

    def sync_soon(self) -> None:
        """A pass now, in the background. For just after connecting, which is the
        one moment somebody is sitting there waiting to see their sleep."""
        task = asyncio.get_running_loop().create_task(self.sync(), name="withings-now")
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # --- Fetching ---------------------------------------------------------------

    async def run(self) -> None:
        if not self.configured:
            return
        await self._sleep(FIRST_SYNC_AFTER_S)
        while True:
            await self.sync()
            await self._sleep(SYNC_EVERY_S)

    async def sync(self, *, manual: bool = False) -> bool:
        """One pass. Never raises. False if it did not ask Withings anything.

        A manual pass inside MANUAL_GAP_S of the last attempt does nothing and
        says so, and so does one while another is already running.
        """
        if self._pass_lock.locked():
            return False
        if manual and self.last_attempt_at is not None:
            if self._now() - self.last_attempt_at < MANUAL_GAP_S:
                return False
        async with self._pass_lock:
            try:
                return await self._pass()
            except asyncio.CancelledError:
                raise
            except WithingsError as exc:
                self._failed(exc)
            except Exception as exc:  # noqa: BLE001
                # Anything at all. This loop is not allowed to take anything down
                # with it, and it is not allowed to stop either.
                log.exception("Withings sync failed")
                self.last_error = f"Something went wrong reading the sleep that came back: {exc!r}"
            return True

    async def _pass(self) -> bool:
        if self._client is None or self.db.withings_account() is None:
            return False

        # The clock, strictly, and with no giving up. The bed stops waiting after
        # ten minutes and runs on whatever time it has, because no night at all is
        # worse than a night at the wrong hour. Nothing here is worth that. Token
        # expiry and lastupdate are both times, and a clock that is days out
        # refreshes tokens that are fine and asks for nights that have not
        # happened. A Pi with no NTP most likely has no internet either, so
        # waiting costs nothing.
        if clocksync.synchronised() is False:
            self.waiting_for_clock = True
            return False
        self.waiting_for_clock = False

        now = self._now()
        if self._rested_until is not None and now < self._rested_until:
            return False
        self.last_attempt_at = now

        account = self.db.withings_account()
        since = account.last_update if account and account.last_update else now - BACKFILL_S
        client = self._client

        summaries = await self._authorised(lambda token: client.summaries(token, since))
        arrived: list[str] = []
        for summary in sorted(summaries, key=lambda s: int(s.get("modified") or 0)):
            modified = int(summary["modified"])
            held = self.db.sleep_night_modified(int(summary["id"]))
            if held is None or held < modified:
                start, end = int(summary["startdate"]), int(summary["enddate"])
                intervals = await self._authorised(
                    lambda token, start=start, end=end: client.intervals(token, start, end)
                )
                night = parse.night(summary, intervals)
                # Looked up by start rather than id: a night under a new id is
                # still the same night, and is not news a second time.
                before = self.db.sleep_night_starting(night.start_at)
                self._observe(before, night)
                self.db.save_sleep_night(night)
                if before is None:
                    arrived.append(night.wake_on)
                else:
                    log.info("Withings: the night ending %s changed, replaced", night.wake_on)
            # Night by night rather than at the end, so a pass cut short keeps
            # what it already has and does not ask for it all again.
            account = self.db.withings_account()
            if account is not None and modified > (account.last_update or 0):
                self.db.set_withings_last_update(modified)

        self.last_sync_at = now
        self.last_error = None
        if arrived:
            said = ", ".join(_day(d) for d in arrived[-3:])
            more = f" and {len(arrived) - 3} more" if len(arrived) > 3 else ""
            self.events.info(KIND, f"Sleep arrived from Withings for {said}{more}.")
        return True

    def _observe(self, before: StoredNight | None, night: parse.Night) -> None:
        """Write down what an unfinished night looks like, as the loop meets one.

        Two things docs/withings.md lists as unknown can only be seen while a
        night is still going: whether `completed` is ever false, and whether a
        night keeps its id as it grows. The loop fetches every half hour, so a
        trip out of bed in the small hours puts an unfinished night in front of
        it without anybody setting an alarm. Each answer goes to the journal,
        once, and never to the phone:

            journalctl -u hydrosnooze | grep "Withings observed"
        """
        ends = _day(night.wake_on)
        if night.completed is False and (before is None or before.completed is not False):
            log.info(
                "Withings observed: the night ending %s is not completed yet. "
                "In bed from %s, last out at %s so far.",
                ends, _clock(night.start_at), _clock(night.end_at),
            )
        if before is None:
            return
        if before.completed is False and night.completed:
            log.info(
                "Withings observed: the night ending %s is completed now. It ran on "
                "from %s to %s after it was first seen.",
                ends, _clock(before.end_at), _clock(night.end_at),
            )
        if before.completed and night.end_at > before.end_at:
            log.info(
                "Withings observed: the night ending %s grew after it was marked "
                "completed, from %s to %s. Completed does not mean finished.",
                ends, _clock(before.end_at), _clock(night.end_at),
            )
        if before.id != night.id:
            log.info(
                "Withings observed: the night ending %s came back under a new id, "
                "%s where it was %s. The same night, kept once.",
                ends, night.id, before.id,
            )

    async def _authorised(self, call: Callable[[str], Awaitable[list[T]]]) -> list[T]:
        """A call with a good token, and once more with a new one if refused.

        Status 100 is ambiguous: one Withings document says an expired token, the
        other says no data. Refreshing once is right under both, and if 100 comes
        back again it is taken as no data. That is never a reason to call the
        connection broken.
        """
        token = await self._token()
        try:
            return await call(token)
        except WithingsError as exc:
            if exc.kind != "auth":
                raise
        token = await self._token(force=True)
        try:
            return await call(token)
        except WithingsError as exc:
            if exc.status == 100:
                return []
            raise

    async def _token(self, *, force: bool = False) -> str:
        account = self.db.withings_account()
        if account is None or self._client is None:
            raise WithingsError(None, "disconnected while fetching")
        now = self._now()
        if not force and account.expires_at - now > REFRESH_BEFORE_S:
            return account.access_token
        try:
            tokens = await self._client.refresh(account.refresh_token, now)
        except WithingsError as exc:
            if exc.kind not in ("unreachable", "rate"):
                self._needs_reconnect(account.needs_reconnect)
            raise
        # On disk before it is used. See Database.save_withings_tokens.
        self.db.save_withings_tokens(
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at,
            scope=tokens.scope,
            user_id=tokens.user_id,
            now=now,
        )
        if account.needs_reconnect:
            self.events.info(KIND, "Withings is accepting the connection again.")
        return tokens.access_token

    def _needs_reconnect(self, already: bool) -> None:
        """Said once. Still tried every pass: a refusal can be a passing thing,
        and an old refresh token stays good for eight hours after a rotation
        whose answer never arrived."""
        if already:
            return
        self.db.set_withings_needs_reconnect(True)
        self.events.warning(
            KIND,
            "Withings has stopped accepting this connection, so no new sleep will arrive "
            "until it is reconnected from the Health Report.",
        )

    def _failed(self, exc: WithingsError) -> None:
        log.warning("Withings sync: %s", exc)
        if exc.kind == "unreachable":
            self.last_error = "Could not reach Withings. Trying again in half an hour."
        elif exc.kind == "rate":
            self._rested_until = self._now() + RATE_LIMITED_FOR_S
            self.last_error = "Withings asked for a rest. Trying again in an hour."
        else:
            if exc.kind == "unauthorised":
                account = self.db.withings_account()
                self._needs_reconnect(bool(account and account.needs_reconnect))
            said = f": {exc.message}" if exc.message else ""
            self.last_error = f"Withings said no (status {exc.status}{said})."

    # --- Saying where it is up to -------------------------------------------------

    def status(self) -> dict[str, Any]:
        account = self.db.withings_account()
        latest = self.db.latest_sleep_night()
        return {
            "configured": self.configured,
            "connected": account is not None,
            "needs_reconnect": bool(account and account.needs_reconnect),
            "waiting_for_clock": self.waiting_for_clock,
            "last_sync_at": _iso(self.last_sync_at),
            "last_error": self.last_error,
            "latest_night": latest.wake_on if latest else None,
        }

    async def close(self) -> None:
        for task in list(self._background):
            task.cancel()
        if self._client is not None:
            await self._client.close()


def _iso(ts: int | None) -> str | None:
    return None if ts is None else datetime.fromtimestamp(ts).astimezone().isoformat()


def _clock(ts: int) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%H:%M")


def _day(wake_on: str) -> str:
    """"Wed 23 Sep". Built by hand because %-d is not on every platform."""
    d = datetime.strptime(wake_on, "%Y-%m-%d")
    return f"{d:%a} {d.day} {d:%b}"
