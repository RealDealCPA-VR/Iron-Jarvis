"""A CODING REPLY WAITS OUT A PASSING PROVIDER ERROR (v1.422.0, Wave 2 "Never
lose work", gap B1 of ``docs/HARNESS-BUILD-AUDIT-2026-11-01.md``).

THE GAP. A coding session's reply runs for an hour or more, one model call per
round. Every model call can fail for a moment — the provider is overloaded
(529), busy (429 that is not the account's own limit), a 5xx, a timeout, a
connection that dropped mid-answer — and the reply ended there: at round 35 of
a 40-minute reply, one 529 was the end. Nothing below the lane waits long
enough to ride it out: Claude Code's own retries are OFF by design (the
one-request relay, ``claude_native/transport``), and the router tries the same
adapter at most twice more within seconds (``ModelRouter._stream_one``).

WHAT THIS DOES. In a HARNESS turn only (a coding session, the benchmark, an
attempt — every turn with ``harness=``), a round whose model call fails with a
TRANSIENT error (:func:`why_waited`) is asked again — the SAME call, so the
same provider, model and account (the turn's hold and pin stay armed: the
owner's decision D1, failover never leaves what the user chose) — after
:data:`WAITS_S` (5, 15, 30, 60 s: up to four more tries), a server's
``Retry-After`` (or the breaker's cooldown) honoured up to
:data:`RETRY_AFTER_CAP_S`, and never more than :data:`PATIENCE_BUDGET_S` of
waiting in one round. While it waits the reply says so on its live line (a
``provider_wait`` frame: "Claude Code is busy (overloaded). Trying again in
30 s, attempt 2 of 5."), and the next try ends that line.

NOTHING RUNS TWICE. A round's tools run only after its model call RETURNS (the
lane reads the calls off the ``final`` frame), so a try that failed ran no
tool, and a failure after the ``final`` frame is never tried again. Words the
failed try had already streamed are taken back first (a ``reset`` frame with
``drop`` = exactly that try's characters: the earlier rounds' words stay).

NEVER WAITED OUT: a safety refusal (the same conversation is declined again),
a sign-in or key problem (401/403, a CLI that is signed out), the account's
own usage limit (its card is the way out), a "too long for this model"
refusal (the refit owns it), an error marked ``no_failover`` (Iron-Proxy's
account sentences, Local only, a held provider refused), anything else that
is not transient, and the user's Stop (a cancel, never an ``Exception``).

PLAIN CHAT IS UNCHANGED: :func:`round_frames` hands back the lane's own
generator, untouched, when the turn is not a harness turn.

When the waiting is over and the round still fails, the failure carries how
many tries were made (``patience_tries``), and the reply's failure line says so
(:func:`tried_words`, read by ``chat_turn.error_payload`` — both lanes).

MIRROR NOTE (lock-step): the stream lane (``routes/chat.chat_stream``) uses
:func:`round_frames`; the POST lane (``chat_turn.run_chat_turn``) uses
:func:`patient_call` — the same :class:`RoundPatience`, the same rules.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
from typing import Any, AsyncIterator, Awaitable, Callable

log = logging.getLogger(__name__)

#: The wait before each new try of a failed round (seconds): up to four more
#: tries, five in all.
DEFAULT_WAITS_S: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0)
#: The waits a round uses (read when its :class:`RoundPatience` is made). The
#: test suite sets it to ``()`` for every test that does not ask for the
#: patience (``tests/conftest.py``: an older pin that makes a coding turn fail
#: once would otherwise wait out real seconds); the patience's own pins set it
#: back to :data:`DEFAULT_WAITS_S`.
WAITS_S: tuple[float, ...] = DEFAULT_WAITS_S
#: A server's ``Retry-After`` (or the breaker's cooldown) is honoured up to
#: this long — never past it: a longer one is the account's limit, not a
#: passing moment.
RETRY_AFTER_CAP_S = 120.0
#: All the waiting ONE round may do, at most (about four minutes).
PATIENCE_BUDGET_S = 240.0
#: The live line's SSE event (``{state: "waiting", words, attempt, of,
#: wait_s, why, provider}``, then ``{state: "trying", attempt, of}``).
FRAME = "provider_wait"
#: The internal frame type :func:`patient_frames` yields (the lane turns it
#: into :data:`FRAME`).
PATIENCE_TYPE = "patience"
#: The ``reset`` reason a retried round's take-back carries.
RESET_REASON = "retry"

#: The waiting is measured with this sleep (a test swaps it for one it
#: controls — never a bound on a real sleep).
SLEEP: Callable[[float], Awaitable[Any]] = asyncio.sleep

#: How long the chain of causes is followed.
_CHAIN = 6


# --------------------------------------------------------------------------- #
# which failures are waited out
# --------------------------------------------------------------------------- #


def _chain(exc: BaseException | None) -> list[BaseException]:
    out: list[BaseException] = []
    cur = exc
    while cur is not None and len(out) < _CHAIN:
        out.append(cur)
        try:
            cur = cur.__cause__ or cur.__context__
        except Exception:  # noqa: BLE001
            break
    return out


def _status(exc: BaseException | None) -> "int | None":
    for cur in _chain(exc):
        st = getattr(cur, "status_code", None)
        if isinstance(st, int) and not isinstance(st, bool):
            return st
    return None


def _says_overloaded(exc: BaseException | None) -> bool:
    return any("overload" in str(cur).lower() for cur in _chain(exc))


#: v1.422.0 review: the words of an ACCOUNT's own limit, whatever the typed
#: facts say — Codex's ``usageLimitExceeded`` with no rate-limit snapshot (a
#: 429 with no ``Retry-After``: "busy" by the facts), an OpenAI key out of
#: quota (``insufficient_quota``), Claude Code's own limit lines ("You've hit
#: your session limit", "You're out of usage credits"). A passing rate limit
#: ("per-minute rate limit", "Rate limit reached for requests per min") is
#: none of these and is still waited out.
_ACCOUNT_LIMIT_RX = re.compile(
    r"\busage limit\b|\b(?:session|weekly|daily|monthly|5-hour|five-hour) limit\b"
    r"|you(?:'ve| have) (?:hit|reached) your\b|\bout of (?:usage )?credits\b|\bcredit balance\b"
    r"|insufficient[_ ]quota|\bquota (?:exceeded|exhausted)\b",
    re.IGNORECASE,
)


def _says_account_limit(exc: BaseException | None) -> bool:
    return any(_ACCOUNT_LIMIT_RX.search(str(cur) or "") for cur in _chain(exc))


def why_waited(exc: BaseException | None) -> str:
    """Why a round's failure is WAITED OUT, as a short word — ``overloaded``,
    ``busy``, ``server``, ``timeout``, ``dropped``, ``unreachable``,
    ``cooldown`` or ``passing`` — or "" when it is not (see the module doc
    for what never is). Reads the typed facts (``failure_words.
    failure_facts``, the router's classification), never loose words alone.
    Never raises."""
    try:
        if exc is None or not isinstance(exc, Exception):
            return ""
        from ..providers.adapters.base import TRANSIENT_STATUS, refusal_of
        from ..providers.failure_words import failure_facts
        from ..providers.router import is_no_failover, is_transient_error

        links = _chain(exc)
        if refusal_of(exc) is not None:
            return ""
        if any(is_no_failover(c) for c in links):
            return ""
        try:
            from ..daemon.chat_turn import is_context_overflow

            if is_context_overflow(exc):
                return ""
        except Exception:  # noqa: BLE001 — unsure: read on
            pass
        f = failure_facts(exc)
        if f.kind in ("refusal", "signed_out"):
            return ""
        if f.limit or _says_account_limit(exc):
            # The account's own usage limit (a 429 marked not transient, or
            # one whose reset is minutes away — v1.422.0 review: or one whose
            # own words say so, its reset not known): its card is the way out.
            return ""
        status = f.status if f.status is not None else _status(exc)
        if status in (401, 403):
            return ""
        if f.kind == "cooldown":
            return "cooldown"
        if f.kind == "timeout":
            return "timeout"
        if f.kind == "interrupted":
            return "dropped"
        if f.kind == "unreachable":
            # A model server or an API that did not answer may be back in a
            # minute (a restart, a network blip); a subscription CLI that is
            # "not connected" is not installed or not set up — waiting for it
            # is minutes of nothing.
            who = str(f.provider or "")
            return "" if who.endswith("-cli") else "unreachable"
        if status is not None:
            if status not in TRANSIENT_STATUS:
                return ""
            if status == 529 or _says_overloaded(exc):
                return "overloaded"
            if status == 429:
                return "busy"
            if 500 <= status <= 599:
                return "server"
            return "passing"
        if f.kind == "answered_error":
            # An endpoint that answered with an error the policy keeps on
            # it: waited out only when its own status said "try again".
            return ""
        if f.transport == "timeout":
            return "timeout"
        if f.transport == "interrupted":
            return "dropped"
        if f.transport == "unreachable":
            return "unreachable"
        for cur in links:
            if isinstance(cur, Exception) and is_transient_error(cur):
                return "overloaded" if _says_overloaded(exc) else "passing"
        return ""
    except Exception:  # noqa: BLE001 — unsure: not waited out (as before)
        return ""


def _retry_hint(exc: BaseException | None) -> float:
    """Seconds the failure itself asks for (``Retry-After``, the breaker's
    ``retry_in``), 0 when it names none."""
    best = 0.0
    for cur in _chain(exc):
        for attr in ("retry_after", "retry_in"):
            try:
                v = float(getattr(cur, attr, 0.0) or 0.0)
            except (TypeError, ValueError):
                v = 0.0
            if v > best and math.isfinite(v):
                best = v
    return best


# --------------------------------------------------------------------------- #
# the words
# --------------------------------------------------------------------------- #

_WHAT = {
    "overloaded": "{Name} is busy (overloaded)",
    "busy": "{Name} is busy (too many requests)",
    "server": "{Name} had a server error",
    "timeout": "{Name} did not answer in time",
    "dropped": "The connection to {name} dropped",
    "unreachable": "{Name} is not answering",
    "cooldown": "{Name} failed several times in a row",
    "passing": "{Name} had a passing error",
}


def _names(provider: str, manager: Any = None) -> tuple[str, str]:
    try:
        from ..providers.failure_words import heading, plain_name

        name = plain_name(str(provider or ""), manager).strip()
    except Exception:  # noqa: BLE001 — wording only
        name = ""
    if not name:
        return "the model", "The model"
    try:
        return name, heading(name)
    except Exception:  # noqa: BLE001
        return name, name


def seconds_words(seconds: float) -> str:
    """``30 s`` / ``2 min`` / ``1 min 30 s`` — a wait, short."""
    s = max(0, int(math.ceil(float(seconds))))
    if s < 60:
        return f"{s} s"
    m, r = divmod(s, 60)
    return f"{m} min" + (f" {r} s" if r else "")


def waiting_words(why: str, seconds: float, attempt: int, of: int, *,
                  provider: str = "", manager: Any = None) -> str:
    """The live line: "<Claude Code is busy (overloaded)>. Trying again in
    30 s, attempt 2 of 5." Never raises."""
    name, Name = _names(provider, manager)
    what = _WHAT.get(why, _WHAT["passing"]).format(name=name, Name=Name)
    return f"{what}. Trying again in {seconds_words(seconds)}, attempt {attempt} of {of}."


def tried_words(exc: BaseException | None) -> str:
    """"Iron Jarvis tried 5 times over 1 min 50 s." for a failure that was
    waited out (``patience_tries`` ≥ 2), else "". Read by ``chat_turn.
    error_payload`` (both lanes) and put before the fix. Never raises."""
    try:
        tries = int(getattr(exc, "patience_tries", 0) or 0)
        waited = float(getattr(exc, "patience_waited_s", 0.0) or 0.0)
    except (TypeError, ValueError):
        return ""
    if tries < 2:
        return ""
    return f"Iron Jarvis tried {tries} times over {seconds_words(waited)}."


# --------------------------------------------------------------------------- #
# the patience of one round
# --------------------------------------------------------------------------- #


class Wait:
    """One wait before the next try."""

    __slots__ = ("seconds", "attempt", "of", "why")

    def __init__(self, seconds: float, attempt: int, of: int, why: str) -> None:
        self.seconds = float(seconds)
        self.attempt = int(attempt)
        self.of = int(of)
        self.why = str(why)


class RoundPatience:
    """How long ONE round of a reply may wait out a passing failure. The first
    try is under way when it is made; :meth:`next_wait` answers the wait
    before the next try, or None to give up (not transient, out of tries, or
    out of the round's budget)."""

    def __init__(self, *, waits: "tuple[float, ...] | None" = None,
                 budget_s: "float | None" = None) -> None:
        self.waits = tuple(WAITS_S if waits is None else waits)
        self.budget_s = float(PATIENCE_BUDGET_S if budget_s is None else budget_s)
        #: Tries made so far (the first is under way).
        self.tries = 1
        #: Seconds waited so far.
        self.waited = 0.0

    @property
    def of(self) -> int:
        return 1 + len(self.waits)

    def next_wait(self, exc: BaseException | None) -> "Wait | None":
        why = why_waited(exc)
        if not why:
            return None
        k = self.tries - 1
        if k >= len(self.waits):
            return None
        seconds = self.waits[k]
        hint = _retry_hint(exc)
        if hint > 0:
            # A server's Retry-After, or the breaker's cooldown (a little
            # past it, so the next try finds the circuit half-open).
            seconds = max(seconds, min(hint + (1.0 if why == "cooldown" else 0.0), RETRY_AFTER_CAP_S))
        if self.waited + seconds > self.budget_s:
            return None
        self.tries += 1
        self.waited += seconds
        return Wait(seconds, self.tries, self.of, why)

    def stamp(self, exc: BaseException) -> None:
        """Say on the failure how many tries were made (when more than one),
        for :func:`tried_words`. Never raises."""
        if self.tries < 2:
            return
        with contextlib.suppress(Exception):
            exc.patience_tries = self.tries  # type: ignore[attr-defined]
            exc.patience_waited_s = self.waited  # type: ignore[attr-defined]


def _provider_of(exc: BaseException | None, fallback: str) -> str:
    try:
        from ..providers.failure_words import failure_facts

        return failure_facts(exc).provider or str(fallback or "")
    except Exception:  # noqa: BLE001
        return str(fallback or "")


def frame_data(frame: dict[str, Any]) -> dict[str, Any]:
    """What the lane sends as the :data:`FRAME` event for one internal
    :data:`PATIENCE_TYPE` frame (its keys, without ``type``)."""
    return {k: v for k, v in frame.items() if k != "type"}


async def patient_frames(
    make: Callable[[], AsyncIterator[dict[str, Any]]],
    *,
    provider: str = "",
    manager: Any = None,
    patience: "RoundPatience | None" = None,
) -> AsyncIterator[dict[str, Any]]:
    """The model's frames for ONE round (``make()`` — the lane's own
    ``_router_frames`` call), tried again after a transient failure (see the
    module doc). Between tries it yields, in order: ``{"type": "reset",
    "reason": "retry", "drop": N}`` when the failed try had streamed ``N``
    characters of words, ``{"type": "patience", "state": "waiting", ...}``,
    then — once the wait is over — ``{"type": "patience", "state":
    "trying", ...}``. A failure after the ``final`` frame, or one that is not
    waited out, is raised as it is (with :meth:`RoundPatience.stamp` when the
    round waited)."""
    pat = patience if patience is not None else RoundPatience()
    while True:
        shown = 0
        finished = False
        wait: "Wait | None" = None
        failure: "BaseException | None" = None
        try:
            async with contextlib.aclosing(make()) as frames:
                async for frame in frames:
                    kind = frame.get("type") if isinstance(frame, dict) else None
                    if kind == "text":
                        shown += len(frame.get("text") or "")
                    elif kind == "final":
                        finished = True
                    elif kind == "reset":
                        # The router's own take-back (a failover before the
                        # first word): what it took back is not ours to drop.
                        shown = 0
                    yield frame
            return
        except Exception as exc:  # noqa: BLE001 — classified; re-raised unless waited out
            if finished:
                raise
            wait = pat.next_wait(exc)
            if wait is None:
                pat.stamp(exc)
                raise
            failure = exc
        who = _provider_of(failure, provider)
        log.info(
            "a coding reply's model call failed (%s, %s); trying again in %.0f s (attempt %d of %d)",
            type(failure).__name__, wait.why, wait.seconds, wait.attempt, wait.of,
        )
        failure = None  # never held across the wait
        if shown:
            yield {"type": "reset", "reason": RESET_REASON, "drop": shown}
        yield {
            "type": PATIENCE_TYPE,
            "state": "waiting",
            "words": waiting_words(wait.why, wait.seconds, wait.attempt, wait.of,
                                   provider=who, manager=manager),
            "attempt": wait.attempt,
            "of": wait.of,
            "wait_s": wait.seconds,
            "why": wait.why,
            "provider": who,
        }
        await SLEEP(wait.seconds)
        yield {"type": PATIENCE_TYPE, "state": "trying", "attempt": wait.attempt, "of": wait.of}


def round_frames(
    patient: bool,
    make: Callable[[], AsyncIterator[dict[str, Any]]],
    *,
    provider: str = "",
    manager: Any = None,
) -> AsyncIterator[dict[str, Any]]:
    """The stream lane's frames for one round: ``make()`` itself — the very
    generator, byte for byte as before — unless ``patient`` (a harness turn),
    then :func:`patient_frames` around it."""
    if not patient:
        return make()
    return patient_frames(make, provider=provider, manager=manager)


async def patient_call(call: Callable[[], Awaitable[Any]], *, patient: bool,
                       should_stop: "Callable[[], Any] | None" = None) -> Any:
    """The POST lane's twin of :func:`round_frames` (lock-step): ``call()``,
    tried again after a transient failure in a harness turn (no frames: the
    POST lane streams nothing). ``should_stop`` (sync or async) is read after
    each wait: a stopped turn gives up with the last failure. Not
    ``patient``: ``await call()``, exactly as before."""
    if not patient:
        return await call()
    pat = RoundPatience()
    while True:
        try:
            return await call()
        except Exception as exc:  # noqa: BLE001 — classified; re-raised unless waited out
            wait = pat.next_wait(exc)
            if wait is None:
                pat.stamp(exc)
                raise
            last = exc
        await SLEEP(wait.seconds)
        if should_stop is not None:
            res = should_stop()
            if asyncio.iscoroutine(res) or isinstance(res, asyncio.Future):
                res = await res
            if res:
                pat.stamp(last)
                raise last


__all__ = [
    "DEFAULT_WAITS_S", "FRAME", "PATIENCE_BUDGET_S", "PATIENCE_TYPE", "RESET_REASON", "RETRY_AFTER_CAP_S", "RoundPatience",
    "SLEEP", "WAITS_S", "Wait", "frame_data", "patient_call", "patient_frames", "round_frames",
    "seconds_words", "tried_words", "waiting_words", "why_waited",
]
