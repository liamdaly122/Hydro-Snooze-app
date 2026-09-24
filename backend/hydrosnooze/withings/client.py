"""Talking to Withings, and nothing else.

Every call is a POST of a form, and every answer is `{"status": n, "body": ...}`.
A status other than 0 is raised as a WithingsError that says which family it
belongs to, so the loop can decide what to do without knowing the numbers.

The token exchange uses the client secret, not a signature: the specification
offers both, and the unsigned one was proven against the live API. See
docs/withings.md.

Nothing here keeps anything. Tokens are handed back to the caller, which has to
store them before using them.
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

AUTHORIZE = "https://account.withings.com/oauth2_user/authorize2"
API = "https://wbsapi.withings.net"

#: Enough for sleep, and nothing more. Proven on the real mat on 24 September.
SCOPE = "user.activity"

#: The only addresses Withings will send the browser back to, because they are
#: the ones registered on the dashboard. Which one is right depends on where the
#: browser is, not where the service is.
REDIRECT_HOSTS = ("hydrosnooze.local:8000", "localhost:8000")
CALLBACK_PATH = "/api/withings/callback"

#: Every summary field a UK Sleep Analyzer actually fills in. asleepduration and
#: withings_index were asked for and never came back.
SUMMARY_FIELDS = (
    "nb_rem_episodes", "sleep_efficiency", "sleep_latency", "total_sleep_time",
    "total_timeinbed", "wakeup_latency", "waso", "apnea_hypopnea_index",
    "breathing_disturbances_intensity", "deepsleepduration", "hr_average", "hr_min",
    "hr_max", "lightsleepduration", "mvt_active_duration", "mvt_score_avg", "night_events",
    "out_of_bed_count", "remsleepduration", "rr_average", "rr_min", "rr_max", "sleep_score",
    "snoring", "snoringepisodecount", "wakeupcount", "wakeupduration",
)

#: Per-minute fields worth the bytes. Not chest_movement_rate, which is rr again,
#: and not withings_index or breathing_sounds, which never come back.
SERIES_FIELDS = ("hr", "rr", "snoring", "sdnn_1", "rmssd", "hrv_quality", "mvt_score")

#: getsummary pages at 300 nights. Fifty pages is forty years; past that the
#: server is saying `more` for some reason of its own, and a loop that trusted it
#: would never end.
MAX_PAGES = 50

#: Which family a status belongs to. Only these decide anything; everything else
#: is logged and tried again next time.
#:
#: The 5xx line is provisional. The table in docs/withings.md says timeout; the
#: client Home Assistant uses files most of 501 to 533 as bad parameters and only
#: 522 as a timeout. Both end in "try again next pass", so the loop does the
#: same either way until openapi.yaml settles which is right.
AUTH_FAILED = frozenset((100, 101, 102, 200, 401))
UNAUTHORISED = frozenset((214, 277, 2553, 2554, 2555))
RATE_LIMITED = 601


class WithingsError(Exception):
    """Withings said no, or could not be reached. `status` is None for the second."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(f"status {status}: {message}" if status is not None else message)
        self.status = status
        self.message = message

    @property
    def kind(self) -> str:
        if self.status is None:
            return "unreachable"
        if self.status in AUTH_FAILED:
            return "auth"
        if self.status in UNAUTHORISED:
            return "unauthorised"
        if self.status == RATE_LIMITED:
            return "rate"
        return "other"


@dataclass(frozen=True)
class Tokens:
    """What a token exchange or refresh hands back. Keep it before using it."""

    access_token: str
    refresh_token: str
    expires_at: int
    scope: str | None
    user_id: str | None


def redirect_uri(host: str) -> str:
    return f"http://{host}{CALLBACK_PATH}"


class WithingsClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        http: httpx.AsyncClient | None = None,
        timeout: float = 20.0,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._http = http or httpx.AsyncClient(timeout=timeout)

    def authorize_url(self, redirect: str, state: str) -> str:
        return AUTHORIZE + "?" + urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "scope": SCOPE,
                "redirect_uri": redirect,
                "state": state,
            }
        )

    async def _call(self, path: str, fields: dict[str, Any], token: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = await self._http.post(API + path, data=fields, headers=headers)
            response.raise_for_status()
            reply = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # %r, because a failed DNS lookup has an empty message and would
            # otherwise say nothing at all.
            raise WithingsError(None, f"could not reach Withings: {exc!r}") from exc
        status = reply.get("status")
        if status != 0:
            raise WithingsError(status, str(reply.get("error") or ""))
        return reply.get("body") or {}

    async def _tokens(self, fields: dict[str, Any], now: int) -> Tokens:
        body = await self._call(
            "/v2/oauth2",
            {
                "action": "requesttoken",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                **fields,
            },
        )
        return Tokens(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            expires_at=now + int(body.get("expires_in", 10800)),
            scope=body.get("scope"),
            user_id=None if body.get("userid") is None else str(body["userid"]),
        )

    async def exchange(self, code: str, redirect: str, now: int) -> Tokens:
        """The code from the redirect, for a pair of tokens. The code lasts thirty
        seconds, so this has to be the very next thing the callback does."""
        return await self._tokens(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect}, now
        )

    async def refresh(self, refresh_token: str, now: int) -> Tokens:
        """A new pair. The refresh token handed in stops working eight hours later."""
        return await self._tokens(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}, now
        )

    async def summaries(self, token: str, since: int) -> list[dict[str, Any]]:
        """Every night Withings has changed since `since`, all pages of them.

        Loops on `more` and takes the offset the server gives. The last page
        comes back with offset 0, and rows are filtered after a page is cut, so
        neither a non-zero offset nor a count of rows is a safe way to go on.
        """
        nights: list[dict[str, Any]] = []
        offset = 0
        for _ in range(MAX_PAGES):
            fields: dict[str, Any] = {
                "action": "getsummary",
                "lastupdate": since,
                "data_fields": ",".join(SUMMARY_FIELDS),
            }
            if offset:
                fields["offset"] = offset
            body = await self._call("/v2/sleep", fields, token)
            nights += body.get("series") or []
            if not body.get("more"):
                return nights
            offset = int(body.get("offset") or 0)
        raise WithingsError(None, f"getsummary still said there was more after {MAX_PAGES} pages")

    async def intervals(self, token: str, start: int, end: int) -> list[dict[str, Any]]:
        """The detail of one night. One night per call, always: past 24 hours
        Withings cuts the answer short without saying so."""
        body = await self._call(
            "/v2/sleep",
            {
                "action": "get",
                "startdate": start,
                "enddate": end,
                "data_fields": ",".join(SERIES_FIELDS),
            },
            token,
        )
        return body.get("series") or []

    async def close(self) -> None:
        await self._http.aclose()
