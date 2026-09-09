"""Every command sent, every state change, every failure.

This is the only way to work out what went wrong at 3am, so nothing is summarised
away and nothing is dropped quietly.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable, Literal

from .clock import Clock

log = logging.getLogger(__name__)

Level = Literal["info", "warning", "error"]

#: How each level lands in the journal.
TO_LOG: dict[Level, int] = {
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


@dataclass
class Event:
    id: int
    at: datetime
    level: Level
    kind: str
    message: str

    def as_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["at"] = self.at.isoformat()
        return d


class EventLog:
    def __init__(self, clock: Clock, capacity: int = 500) -> None:
        self.clock = clock
        self._events: deque[Event] = deque(maxlen=capacity)
        self._next_id = 1
        self._listeners: list[Callable[[Event], None]] = []

    def subscribe(self, listener: Callable[[Event], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def add(self, level: Level, kind: str, message: str) -> Event:
        event = Event(
            id=self._next_id, at=self.clock.now(), level=level, kind=kind, message=message
        )
        self._next_id += 1
        self._events.append(event)
        # Also to the journal, which until now recorded devices breaking and not
        # devices mending: "the blaster is not answering" was there and "the
        # blaster is answering again" was not, because one came from a logger and
        # the other only from here. Half a story is worse than none at 7am, when
        # the journal is what you have over SSH and the app is not.
        log.log(TO_LOG[level], "%s: %s", kind, message)
        for listener in list(self._listeners):
            listener(event)
        return event

    def info(self, kind: str, message: str) -> Event:
        return self.add("info", kind, message)

    def warning(self, kind: str, message: str) -> Event:
        return self.add("warning", kind, message)

    def error(self, kind: str, message: str) -> Event:
        return self.add("error", kind, message)

    def recent(self, limit: int = 100) -> list[Event]:
        return list(reversed(self._events))[:limit]

    def seed(self, events: list[Event]) -> None:
        """Load events back from the database on startup."""
        for event in events:
            self._events.appendleft(event)
            self._next_id = max(self._next_id, event.id + 1)
