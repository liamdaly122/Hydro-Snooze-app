"""A Withings that answers from the invented fixtures, for the tests.

It sits behind httpx's MockTransport, so everything above it is the real thing:
the real client building real form posts, the real parser, the real database.
It keeps the habits the real one was seen to have: tokens that stop working,
pages whose offset runs ahead of the rows, and statuses that mean no.
"""

from __future__ import annotations

import copy
import json
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from hydrosnooze.withings.client import WithingsClient

FIXTURES = Path(__file__).parent / "fixtures" / "withings"

#: After every invented night has been made and modified, so all of them count.
NOW = 1_793_102_400  # Tue 27 Oct 2026 12:00 UTC


def summaries() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "getsummary.json").read_bytes())["body"]["series"]


def intervals(wake_on: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / f"get-{wake_on}.json").read_bytes())["body"]["series"]


class FakeWithings:
    def __init__(self) -> None:
        self.nights = copy.deepcopy(summaries())
        self.calls: list[dict[str, Any]] = []
        self._issued = 0
        self.valid: set[str] = set()
        #: Statuses to answer with, in turn, before answering properly. Keyed by
        #: action: requesttoken, getsummary or get.
        self.refuse: dict[str, list[int]] = {}
        #: Rows per getsummary page, and rows the server drops after cutting a
        #: page, the way the real one did to page 2 of the demo account.
        self.page: int | None = None
        self.dropped: set[int] = set()
        #: Raise this from the transport instead of answering: the internet gone.
        self.unreachable: Exception | None = None
        #: Called with the token on every sleep call, before it is answered.
        self.on_sleep_call: Callable[[str | None], None] | None = None

    def client(self) -> WithingsClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self._answer))
        return WithingsClient("fake-id", "fake-secret", http=http)

    def calls_to(self, action: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["fields"].get("action") == action]

    def _reply(self, status: int, body: dict[str, Any] | None = None) -> httpx.Response:
        payload: dict[str, Any] = {"status": status}
        if status == 0:
            payload["body"] = body or {}
        else:
            payload["error"] = "the fake says no"
        return httpx.Response(200, json=payload)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable is not None:
            raise self.unreachable
        fields = dict(urllib.parse.parse_qsl(request.content.decode()))
        auth = request.headers.get("authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else None
        self.calls.append({"path": request.url.path, "fields": fields, "token": token})
        action = fields.get("action", "")
        queued = self.refuse.get(action) or []
        if queued:
            return self._reply(queued.pop(0))

        if request.url.path == "/v2/oauth2":
            self._issued += 1
            access = f"access-{self._issued}"
            self.valid.add(access)
            return self._reply(
                0,
                {
                    "userid": "fake-user",
                    "access_token": access,
                    "refresh_token": f"refresh-{self._issued}",
                    "expires_in": 10800,
                    "scope": "user.activity",
                    "token_type": "Bearer",
                },
            )

        if self.on_sleep_call is not None:
            self.on_sleep_call(token)
        if token not in self.valid:
            return self._reply(401)

        if action == "getsummary":
            since = int(fields["lastupdate"])
            # Modified at or after, which is the less forgiving of the two
            # readings: the newest night comes back every pass unless skipped.
            rows = [n for n in self.nights if n["modified"] >= since]
            offset = int(fields.get("offset", 0))
            if self.page is None:
                return self._reply(0, {"series": rows, "more": False, "offset": 0})
            cut = rows[offset : offset + self.page]
            more = offset + self.page < len(rows)
            shown = [n for i, n in enumerate(cut, offset) if i not in self.dropped]
            return self._reply(
                0, {"series": shown, "more": more, "offset": offset + self.page if more else 0}
            )

        if action == "get":
            start = int(fields["startdate"])
            night = next(n for n in self.nights if n["startdate"] == start)
            return self._reply(0, {"series": intervals(night["date"]), "model": 32})

        return self._reply(503)
