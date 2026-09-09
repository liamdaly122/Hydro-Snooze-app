"""Telling someone, when the thing that went wrong happened at 2am.

The event log is thorough and completely useless while you are asleep. On the
night of 9 September the system knew within seconds that the blaster had gone,
and had no way at all to say so. That was found at 07:11 by looking.

ntfy.sh because it needs no account, no key and no service to run: a topic name
is the whole configuration, and the phone app subscribes to it. The topic name IS
the secret, so it wants to be long and unguessable rather than "hydrosnooze".

Nothing here is allowed to affect the night. A notifier that raised, blocked, or
retried into a dead network would be worse than no notifier, because it would
take the scheduler down with it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from .clock import Clock
from .events import Event

log = logging.getLogger(__name__)

#: How long the same message stays quiet after being sent once.
#:
#: Not a nicety. A stage retrying every thirty seconds for its whole window would
#: otherwise be twenty pushes about one problem, and the second one is already
#: less useful than the first.
REPEAT_AFTER = timedelta(minutes=30)

#: Warnings worth waking someone for. Everything at error level goes anyway;
#: these are the warnings that mean the night is not doing what it should.
#:
#: "The Pi is" catches the machine reporting on itself: under-voltage, throttling,
#: heat. Those are hardware going wrong underneath everything else, and they are
#: reported once per boot rather than hourly, so there is no risk of the phone
#: being buried by one bad power supply.
LOUD_WARNINGS = (
    "did not land",
    "not answering",
    "Unit was off",
    "cancelled out",
    "The Pi is",
    "The Pi has",
)

TITLES = {
    "error": "HydroSnooze problem",
    "warning": "HydroSnooze warning",
}


class Notifier:
    """Pushes the events worth knowing about to a phone.

    Silent by default. With no topic configured every method here does nothing,
    which is what the simulator and the tests get.
    """

    def __init__(
        self,
        clock: Clock,
        topic: str = "",
        server: str = "https://ntfy.sh",
        *,
        timeout: float = 5.0,
    ) -> None:
        self.clock = clock
        self.topic = topic.strip()
        self.server = server.rstrip("/")
        self.timeout = timeout
        self._sent: dict[str, datetime] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def enabled(self) -> bool:
        return bool(self.topic)

    def worth_sending(self, event: Event) -> bool:
        if not self.enabled:
            return False
        if event.level == "error":
            return True
        if event.level == "warning":
            return any(phrase in event.message for phrase in LOUD_WARNINGS)
        return False

    def on_event(self, event: Event) -> None:
        """Called from the event log. Never blocks and never raises."""
        if not self.worth_sending(event):
            return

        # Keyed by kind and the first few words, so a message carrying a
        # changing number does not read as a new problem every time.
        key = f"{event.kind}:{' '.join(event.message.split()[:6])}"
        last = self._sent.get(key)
        now = self.clock.now()
        if last is not None and now - last < REPEAT_AFTER:
            return
        self._sent[key] = now

        title = TITLES.get(event.level, "HydroSnooze")
        try:
            task = asyncio.get_running_loop().create_task(self._post(title, event.message))
        except RuntimeError:
            # No loop, so this is a test or a script. Nothing to do.
            return
        # Held so the loop does not garbage collect a task still in flight.
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _post(self, title: str, message: str) -> None:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                await client.post(
                    f"{self.server}/{self.topic}",
                    content=message.encode(),
                    headers={"Title": title, "Priority": "high", "Tags": "warning"},
                )
        except Exception as exc:  # noqa: BLE001
            # Swallowed on purpose. Failing to send a notification is not worth
            # taking the night down for, and the event log still has everything.
            log.warning("could not send notification: %r", exc)

    async def test(self) -> bool:
        """Send one on demand, so the setup can be proved rather than hoped."""
        if not self.enabled:
            return False
        await self._post("HydroSnooze", "Test notification. Setup is working.")
        return True

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()


#: How often to say "still here". Should be comfortably more often than the
#: period configured at the other end, so one missed ping is not an alarm.
HEARTBEAT_EVERY = timedelta(minutes=5)


class Heartbeat:
    """A dead man's switch, for the failure nothing inside the Pi can report.

    Notifications only arrive if the Pi is alive enough to send one. A power cut,
    a dead SD card, a router that never comes back: all of those produce silence,
    and silence is indistinguishable from a quiet night where nothing went wrong.

    So the Pi says "still here" on a timer to something outside the house, and
    that something raises the alarm when the saying stops. It is the only way to
    be told about a machine that cannot tell you anything.

    Deliberately reports liveness rather than health. A blaster wobble is
    something the Pi can report itself, and routing it here too would mean the
    "your bed controller is offline" alarm cried wolf, which is how an alarm
    stops being read.
    """

    def __init__(self, clock: Clock, url: str = "", *, timeout: float = 10.0) -> None:
        self.clock = clock
        self.url = url.strip()
        self.timeout = timeout
        self.last_ok_at: datetime | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def ping(self) -> bool:
        """Say we are here. Never raises: a missed ping is not worth a night."""
        if not self.enabled:
            return False
        try:
            import httpx

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(self.url)
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("heartbeat failed: %r", exc)
            return False
        self.last_ok_at = self.clock.now()
        return True
