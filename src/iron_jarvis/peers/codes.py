"""The short pairing code one computer shows and the other types (v1.497.0).

The code is the user's consent to pair, so it is held to these rules:

* ONE code at a time, made only when the user presses "Show a pairing code";
  a new press replaces the old one, Cancel drops it.
* It lasts :data:`CODE_TTL_S` (10 minutes) and works ONCE.
* :data:`MAX_WRONG` (5) wrong tries end it: someone on the network guessing
  has five chances in a million, and then needs the user to make a new one.
* It lives in this process's memory only: a restart forgets it.
* Comparing is constant-time.

The plain code is kept (in memory, for its ten minutes) so the screen can show
it again after a reload; nothing here is written to disk or the log.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

#: How long a code works.
CODE_TTL_S = 600
#: Wrong tries before a code stops working.
MAX_WRONG = 5
#: How many digits a code has.
DIGITS = 6
#: Pairing tries (right or wrong) taken per minute, from anyone.
TRIES_PER_MINUTE = 10

#: Why a try did not pair, each answered in its own words by the route.
NO_CODE = "no_code"
EXPIRED = "expired"
WRONG = "wrong"
USED_UP = "used_up"
OK = "ok"


def normalize(raw: object) -> str:
    """The digits a person typed: spaces, dashes and dots dropped. Anything
    else (letters, a wrong length) reads as "" and never matches."""
    text = "".join(ch for ch in str(raw or "") if ch not in " -.\t")
    if len(text) != DIGITS or not text.isdigit() or not text.isascii():
        return ""
    return text


def shown(code: str) -> str:
    """How the code is shown: two groups of three ("482 917")."""
    half = len(code) // 2
    return f"{code[:half]} {code[half:]}"


@dataclass
class _Code:
    code: str
    made: float
    wrong: int = 0


class PairingCodes:
    """The one active pairing code of this computer, and the try limit."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], datetime] | None = None) -> None:
        self._clock = clock
        self._wall = wall or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._code: _Code | None = None
        self._tries: deque[float] = deque()
        self._made_wall: datetime | None = None

    def make(self) -> tuple[str, datetime]:
        """Make a new code (the old one stops working). Returns the code and
        when it stops working."""
        code = "".join(str(secrets.randbelow(10)) for _ in range(DIGITS))
        with self._lock:
            self._code = _Code(code=code, made=self._clock())
            self._made_wall = self._wall()
            return code, self._made_wall + timedelta(seconds=CODE_TTL_S)

    def cancel(self) -> None:
        with self._lock:
            self._code = None
            self._made_wall = None

    def active(self) -> tuple[str, datetime] | None:
        """The code still waiting to be typed, with when it ends; None when
        there is none (never made, used, expired, or tried wrong too often)."""
        with self._lock:
            c = self._code
            if c is None or self._clock() - c.made >= CODE_TTL_S or self._made_wall is None:
                return None
            return c.code, self._made_wall + timedelta(seconds=CODE_TTL_S)

    def take_try(self) -> bool:
        """Count one pairing try against the per-minute limit. False when the
        limit is reached (nothing is counted then)."""
        now = self._clock()
        with self._lock:
            while self._tries and now - self._tries[0] > 60.0:
                self._tries.popleft()
            if len(self._tries) >= TRIES_PER_MINUTE:
                return False
            self._tries.append(now)
            return True

    def check(self, typed: object) -> str:
        """Check a typed code. :data:`OK` USES the code up (it works once);
        a wrong one counts, and the :data:`MAX_WRONG`-th ends the code."""
        candidate = normalize(typed)
        with self._lock:
            c = self._code
            if c is None:
                return NO_CODE
            if self._clock() - c.made >= CODE_TTL_S:
                self._code = None
                return EXPIRED
            # Constant-time, and always over the same length (a malformed
            # candidate is compared as zeros and still counts as wrong).
            given = (candidate or "0" * DIGITS).encode("ascii")
            match = hmac.compare_digest(given, c.code.encode("ascii")) and bool(candidate)
            if match:
                self._code = None
                return OK
            c.wrong += 1
            if c.wrong >= MAX_WRONG:
                self._code = None
                return USED_UP
            return WRONG
