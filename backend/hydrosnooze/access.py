"""Who may drive the bed, now that it can be reached from outside the house.

Until Tailscale the only way to the app was the home Wi-Fi, and that was the
whole of the security: anything on the network could set a heater under a
mattress to 55C, and the network was the lock. Reaching it from anywhere means
the lock has to move into the app. Three rules, and each has a reason.

**One password, signed in once per device.** Typing a password at three in the
morning is the wrong price, so a device that has signed in stays signed in for
half a year from the last time it was used. Changing the password signs every
device out, because the moment somebody changes a password is the moment they
think somebody else has it.

**Nothing through Tailscale without a password.** With no password set the app
behaves exactly as it always has on the home network, and refuses everything
that arrives through Tailscale. So switching Tailscale on before setting the
password cannot open the bed to anything by accident. It fails shut.

**Never trust the Pi's own address.** `tailscale serve` hands every request to
the service from 127.0.0.1, so "it came from this machine" would mean "it came
from anywhere". The scripts on the Pi carry a key instead, and a request from
the Pi without the key is treated as having come through Tailscale.

Nothing in here is on the way to the bed. The scheduler, the bedside buttons
and the notifications never go through a request, so a login that breaks, or a
tunnel that goes down, costs the app on the phone and nothing else.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import secrets
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from .config import Settings
from .db import Database, StoredSession
from .events import EventLog
from .notify import Notifier

log = logging.getLogger(__name__)

#: The cookie a signed-in device carries. Its value is the token; only a hash of
#: the token is kept on the Pi.
COOKIE = "hydrosnooze_session"

#: How long a device stays signed in after it was last used. Long on purpose: a
#: phone that has to sign in again is a phone that gets a short password.
SESSION_LASTS = timedelta(days=180)

#: How often a session's last use is written down. Every request would be a
#: write to the SD card for the sake of a date nobody reads to the minute.
SEEN_EVERY = timedelta(hours=12)

#: Wrong passwords before signing in pauses, and for how long. Across every
#: device at once rather than per address: through Tailscale everything arrives
#: from the same address, so per address would be no limit at all.
TRIES = 10
TRIES_WITHIN = timedelta(minutes=15)
PAUSED_FOR = timedelta(minutes=15)

#: The shortest password scripts/password.py accepts. With ten guesses every
#: quarter of an hour, a random ten-letter password outlasts the Pi.
MIN_PASSWORD = 10

#: What the event log files sign-ins under.
ACCESS_KIND = "access"

#: The addresses Tailscale hands out. A device on the tailnet can reach the
#: service's own port directly, without going through `tailscale serve`, and
#: that has to count as outside the house too.
TAILNET = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))

#: scrypt's cost. About a tenth of a second on a Pi 4, which is nothing to sign
#: in once and a great deal to somebody trying a list of passwords.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1

Via = Literal["home", "tailscale"]

NO_PASSWORD = (
    "HydroSnooze has no password yet, so it only answers on the home network. "
    "Set one on the Pi with ./scripts/password.py, then restart the service."
)


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """The password as it is kept in .env: never the password itself.

    Colons between the parts rather than the usual dollar signs. The same .env
    is read by systemd, and a dollar sign in there is one misreading away from
    being taken for a variable. scripts/password.py writes the same shape, and a
    test holds the two together.
    """
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32
    )
    return f"scrypt:{SCRYPT_N}:{SCRYPT_R}:{SCRYPT_P}:{salt.hex()}:{digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split(":")
        if scheme != "scrypt":
            return False
        want = bytes.fromhex(digest)
        got = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(want),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def device_label(user_agent: str) -> str:
    """"iPhone, Safari", roughly, so a sign-in notification means something."""
    ua = user_agent or ""
    device = next(
        (
            name
            for key, name in (
                ("iPhone", "iPhone"),
                ("iPad", "iPad"),
                ("Android", "Android"),
                ("Macintosh", "Mac"),
                ("Windows", "Windows"),
                ("Linux", "Linux"),
            )
            if key in ua
        ),
        "",
    )
    # Order matters: Chrome says Safari too, and Edge says Chrome.
    browser = next(
        (
            name
            for key, name in (
                ("Edg/", "Edge"),
                ("CriOS", "Chrome"),
                ("Chrome/", "Chrome"),
                ("Firefox", "Firefox"),
                ("FxiOS", "Firefox"),
                ("Safari", "Safari"),
            )
            if key in ua
        ),
        "",
    )
    said = ", ".join(part for part in (device, browser) if part)
    return said or "an unknown browser"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Verdict:
    """Whether a request may go ahead, and which way it came in."""

    allowed: bool
    via: Via
    #: 401 means sign in; 403 means signing in would not help from here.
    status: int = 200
    reason: str = ""
    #: The scripts on the Pi, which sign in with the key rather than a password.
    script: bool = False


class SignInRefused(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


class Access:
    """Signing in, signing out, and whether a request may go ahead.

    Keeps real time rather than the service clock, the same way the Withings
    loop does. The simulator runs its clock up to 120 times too fast, and half a
    year of being signed in would be gone in a day and a half.
    """

    def __init__(
        self,
        settings: Settings,
        db: Database,
        events: EventLog,
        notifier: Notifier,
        *,
        wall: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.settings = settings
        self.db = db
        self.events = events
        self.notifier = notifier
        self._wall = wall
        self._wrong: deque[datetime] = deque()
        self._paused_until: datetime | None = None

    # --- What there is ------------------------------------------------------------

    @property
    def required(self) -> bool:
        """Whether a password is set, which is what makes signing in a thing."""
        return bool(self.settings.password_hash)

    @property
    def _mark(self) -> str:
        """Which password a session belongs to, without keeping any of it."""
        return hashlib.sha256(self.settings.password_hash.encode()).hexdigest()[:16]

    def _is_key(self, authorization: str | None) -> bool:
        key = self.settings.api_key
        if not key or not authorization:
            return False
        scheme, _, given = authorization.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(given.strip(), key)

    # --- Which way it came in -------------------------------------------------------

    def via(self, client_host: str | None, authorization: str | None = None) -> Via:
        """Home or Tailscale, from the address the request arrived from.

        Anything that is not an address at all is home: the test client calls
        itself "testclient", and a unix socket has no address. Neither is a way
        in from outside.
        """
        if self._is_key(authorization):
            return "home"
        try:
            ip = ipaddress.ip_address(client_host or "")
        except ValueError:
            return "home"
        if getattr(ip, "ipv4_mapped", None) is not None:
            ip = ip.ipv4_mapped  # type: ignore[assignment]
        if any(ip in net for net in TAILNET if ip.version == net.version):
            return "tailscale"
        if ip.is_loopback and self.settings.tunnel == "tailscale":
            return "tailscale"
        return "home"

    # --- Whether it may ---------------------------------------------------------------

    def admit(
        self,
        *,
        client_host: str | None,
        authorization: str | None = None,
        cookie: str | None = None,
    ) -> Verdict:
        via = self.via(client_host, authorization)
        if self._is_key(authorization):
            return Verdict(True, via, script=True)
        if not self.required:
            if via == "tailscale":
                return Verdict(False, via, 403, NO_PASSWORD)
            return Verdict(True, via)
        if self.session(cookie) is None:
            return Verdict(False, via, 401, "Sign in to HydroSnooze.")
        return Verdict(True, via)

    def session(self, cookie: str | None) -> StoredSession | None:
        """The device this cookie belongs to, if it is still signed in."""
        if not cookie or not self.required:
            return None
        stored = self.db.session(_hash_token(cookie))
        if stored is None:
            return None
        now = self._wall()
        if stored.password != self._mark or now - stored.seen_at > SESSION_LASTS:
            self.db.end_session(stored.token_hash)
            return None
        if now - stored.seen_at > SEEN_EVERY:
            self.db.session_seen(stored.token_hash, now)
        return stored

    # --- Signing in and out -----------------------------------------------------------

    def sign_in(self, password: str, *, client_host: str | None, user_agent: str = "") -> str:
        """A new token for this device, or SignInRefused saying why not."""
        via = self.via(client_host)
        if not self.required:
            raise SignInRefused(409 if via == "home" else 403, NO_PASSWORD)

        now = self._wall()
        if self._paused_until is not None and now < self._paused_until:
            left = max(1, round((self._paused_until - now).total_seconds() / 60))
            raise SignInRefused(
                429,
                f"Too many wrong passwords, so signing in is paused. Try again in {left} "
                f"minute{'s' if left != 1 else ''}.",
            )

        if not password_matches(password, self.settings.password_hash):
            self._wrong_password(now)
            raise SignInRefused(401, "That is not the password.")

        self._wrong.clear()
        token = secrets.token_urlsafe(32)
        label = device_label(user_agent)
        self.db.add_session(
            StoredSession(
                token_hash=_hash_token(token),
                password=self._mark,
                created_at=now,
                seen_at=now,
                via=via,
                label=label,
            )
        )
        where = "through Tailscale" if via == "tailscale" else "on the home network"
        self.events.info(ACCESS_KIND, f"Signed in {where}: {label}.")
        # Every time, because it is rare: once per device every half a year. A
        # sign-in nobody made is the one thing here worth knowing about at once.
        self.notifier.push(
            "HydroSnooze sign-in",
            f"{label} signed in {where}. If that was not you, change the password on the "
            "Pi with ./scripts/password.py, which signs every device out.",
            tag="key",
        )
        return token

    def _wrong_password(self, now: datetime) -> None:
        self._wrong.append(now)
        while self._wrong and now - self._wrong[0] > TRIES_WITHIN:
            self._wrong.popleft()
        if len(self._wrong) < TRIES:
            return
        self._wrong.clear()
        self._paused_until = now + PAUSED_FOR
        minutes = int(PAUSED_FOR.total_seconds() // 60)
        message = (
            f"{TRIES} wrong passwords in {int(TRIES_WITHIN.total_seconds() // 60)} minutes. "
            f"Signing in is paused for {minutes} minutes. The bed is unaffected."
        )
        self.events.warning(ACCESS_KIND, message)
        self.notifier.push("HydroSnooze sign-in", message, tag="warning")

    def sign_out(self, cookie: str | None) -> None:
        if cookie:
            self.db.end_session(_hash_token(cookie))

    def sign_out_everywhere(self) -> int:
        count = self.db.end_every_session()
        self.events.info(
            ACCESS_KIND,
            f"Signed out every device ({count}). Each one needs the password again.",
        )
        return count

    # --- The live socket ------------------------------------------------------------

    def same_origin(self, origin: str | None, host: str | None, forwarded_host: str | None) -> bool:
        """Whether a browser opening the live socket is the app's own page.

        A browser says which page opened a socket and a script cannot lie about
        it, so a page on some other site cannot read the bed's live feed with
        your cookie. Anything that is not a browser sends no origin and is let
        through to the sign-in check like any other request.

        Checked against the public address as well as the host header, because
        what `tailscale serve` passes on as the host is its business.
        """
        if not origin:
            return True
        said = urlsplit(origin).netloc.lower()
        allowed = {h.lower() for h in (host, forwarded_host) if h}
        if self.settings.public_url:
            allowed.add(urlsplit(self.settings.public_url).netloc.lower())
        return said in allowed

    def status(self, verdict: Verdict) -> dict[str, object]:
        """What the app asks before anything else: whether to show the sign-in."""
        return {
            "required": self.required,
            "signed_in": verdict.allowed,
            "via": verdict.via,
            "refused": None if verdict.allowed or verdict.status == 401 else verdict.reason,
        }
