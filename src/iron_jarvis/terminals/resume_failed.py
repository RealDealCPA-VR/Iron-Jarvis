"""Could the NEXT account continue the conversation it was handed? (v1.303.2)

"Continue on the next account" (v1.303.0) carries a Claude Code conversation
file into another account's home and types ``claude --resume <id>`` there. The
one thing this PC cannot prove ahead of time is that the other account's
Anthropic side ACCEPTS that conversation (another organisation, another plan,
a model it has no access to). When it does not, the pane must not leave the
user stuck: it says so and offers "start fresh with a handoff".

ONLY THE CARRIED FILE RAISES IT (review): the copy in the next account's home,
a record written AFTER the pane was opened, by the assistant (not a
sidechain), with ``isApiErrorMessage: true`` — Claude Code's own, exact record
of an API error. The newest such assistant record decides, and a later real
answer clears it. BLOCKING (a bounded tail read): :func:`check_file` runs only
on the threadpool (the activity route), never in ``info()``, which async pane
tools call on the event loop.

THE SCREEN NEVER RAISES IT. A first cut read ``API Error: 4xx`` lines on
screen, and a TOOL's output sits under the same ``⎿`` gutter Claude Code prints
its own errors in — ``curl`` printing "API Error: 400 Bad Request from
upstream", a test printing "expected 'invalid api key'" — so a working Claude
would have been offered (and given) ``/exit``. The screen is used only to
CLEAR: while the pane shows Claude working again, nothing is reported.

WHAT COUNTS (a record must SAY it is the conversation the account refuses):
a 4xx status (not 429 — the limit flow — and not 401 — sign-in), Claude
Code's own ``invalid_request`` code, or recognised non-transient words
(``invalid_request_error``, "does not have access", "prompt is too long",
a thinking-block ``signature``). A record with no status and an unknown error
("API Error: Request timed out.", "fetch failed") is NOT a failure — this PC's
real non-limit errors were ``server_error`` and ``authentication_failed`` only.

NOT A RESUME FAILURE: ``rate_limit`` / 429 (the existing limit flow); a
sign-in problem (401, "Please run /login", an expired or revoked token) —
reported as ``sign_in`` so the pane says THAT, judged by words and status and
never by ``error: "authentication_failed"`` alone (Claude Code stamps that on
every upstream 403, including "your organization does not have access to this
model", which IS a conversation this account cannot continue); a transient
fault (``server_error``, ``max_output_tokens``, any 5xx) and ``billing_error``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from ..providers.cli_auth import is_sign_in_refusal
from .limit_state import _LINE_CAP

__all__ = ["Failure", "check_file", "classify_record"]

#: How much of the carried file's END is read on each check.
TAIL_BYTES = 256 * 1024

FAILED = "failed"
SIGN_IN = "sign_in"

#: Words that mean "this account is not signed in", beyond the CLIs' own
#: refusals (``cli_auth.is_sign_in_refusal``): the shapes Claude Code prints.
_SIGN_IN_WORDS = re.compile(
    r"login expired|invalid api key|oauth token (?:has )?(?:expired|been revoked|revoked)"
    r"|token (?:has )?(?:been )?(?:expired|revoked)|authentication_error",
    re.I,
)
#: Words that say the ACCOUNT refuses THIS conversation (not a passing fault).
_CONVERSATION_WORDS = re.compile(
    r"invalid_request_error|does not have access|prompt is too long|\bsignature\b",
    re.I,
)


@dataclass(frozen=True)
class Failure:
    """``kind`` is ``failed`` (the account cannot continue this conversation)
    or ``sign_in`` (the account needs signing in); ``line`` is the matched
    message only; ``when`` the record's own timestamp."""

    kind: str
    line: str
    when: str | None = None


def _signed_out(text: str) -> bool:
    return is_sign_in_refusal(text) or bool(_SIGN_IN_WORDS.search(text or ""))


def _text_of(rec: Mapping[str, Any]) -> str:
    msg = rec.get("message")
    content = msg.get("content") if isinstance(msg, Mapping) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(b.get("text") or "")
            for b in content
            if isinstance(b, Mapping) and b.get("type") == "text"
        )
    return ""


def _status_of(rec: Mapping[str, Any]) -> int | None:
    value = rec.get("apiErrorStatus")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


#: Claude Code ``error`` codes a fresh conversation would hit again.
_NOT_FIXED_BY_FRESH_START = frozenset({"billing_error", "server_error", "max_output_tokens"})


def classify_record(rec: Mapping[str, Any]) -> str | None:
    """``failed`` / ``sign_in`` for an API-error record that is one, else None
    (an answer, the limit, a transient or unrecognised fault)."""
    if rec.get("isApiErrorMessage") is not True:
        return None
    error = str(rec.get("error") or "")
    status = _status_of(rec)
    if error == "rate_limit" or status == 429:
        return None  # the next account's own limit: the limit flow
    text = _text_of(rec)
    if status == 401 or _signed_out(text):
        return SIGN_IN
    # A fault a fresh start cannot fix is never a resume failure, whatever its
    # status (a 400 carrying billing_error; a 408 timeout) -- checked BEFORE the
    # 4xx range so the code agrees with the module docstring.
    if error in _NOT_FIXED_BY_FRESH_START or status == 408:
        return None
    if status is not None:
        return FAILED if 400 <= status < 500 else None
    if error == "invalid_request" or _CONVERSATION_WORDS.search(text):
        return FAILED
    # No status and no recognised words — server_error, max_output_tokens,
    # billing_error, "Request timed out", "fetch failed": not proven to be the
    # conversation, so not a resume failure.
    return None


def _clip(text: str) -> str:
    return " ".join(str(text or "").split())[:_LINE_CAP]


def _time(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _tail_records(path: Path, max_bytes: int = TAIL_BYTES) -> list[dict[str, Any]]:
    """The whole JSON records in the last ``max_bytes`` of ``path`` (a cut
    first line and a torn last write are skipped). Never raises."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(0, size - max_bytes)
            fh.seek(start)
            data = fh.read()
    except OSError:
        return []
    lines = data.splitlines()
    if start > 0 and lines:
        lines = lines[1:]  # began mid-record
    out: list[dict[str, Any]] = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def check_file(path: Path | str, after: str, *, max_bytes: int = TAIL_BYTES) -> Failure | None:
    """The resume failure (or sign-in problem) the carried file records AFTER
    ``after`` (ISO), or None. The newest assistant record written after it
    decides: an answer clears an earlier error. BLOCKING; never raises."""
    floor = _time(after)
    if floor is None:
        return None
    found: Failure | None = None
    for rec in _tail_records(Path(path), max_bytes):
        if rec.get("type") != "assistant" or rec.get("isSidechain") is True:
            continue
        when = _time(rec.get("timestamp"))
        if when is None or when <= floor:
            continue
        kind = classify_record(rec)
        found = Failure(kind, _clip(_text_of(rec)), str(rec.get("timestamp"))) if kind else None
    return found
