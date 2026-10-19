"""Plain words for a failed model call (v1.354.0, G28).

Every chat error used to be ONE string — the adapter's own
``"openrouter API error 401: No auth credentials found"``, a router refusal
that named raw ids ("anthropic runs through claude-cli…") and config keys
("local_primary_policy=refuse"), or an agent run's
``"Session failed: ProviderError: …"``. A new user cannot act on any of them.

This module turns a failure into three fields, deterministically, from what
the failure already CARRIES (no model call ever explains an error — the
audit's "do not build" list):

* ``title`` — the headline in plain words, never the server's text
  ("OpenRouter refused your key", "Claude Code isn't signed in");
* ``fix`` — what the user can do next, naming the real place in the app;
* ``technical`` — the original detail, secret-shaped values masked
  (``detections.redact.mask``), for whoever wants it.

What it reads, in order (the typed fields, never string guesses where a type
exists):

1. ``kind`` — stamped by ``ModelRouter._unavailable_error`` on its own
   refusals (``signed_out`` / ``answered_error`` / ``unreachable`` /
   ``timeout`` / ``interrupted`` / ``cooldown``), with ``failed_provider``,
   ``cli``, ``partial`` and ``retry_in`` beside it;
2. ``status_code`` — a typed :class:`~.adapters.base.ProviderError`
   (401/403 → the key, 429/5xx → busy);
3. ``failed_provider`` — stamped by the router on the primary's failure, so
   the headline names the provider that actually failed (an Auto turn's
   pick, a default's), else the adapter's own ``"<provider> API error"``
   prefix, else the caller's hint;
4. :func:`~.router.local_failure_kind` — a transport shape with no status.

The walk follows ``__cause__`` / ``__context__`` (bounded), because the
router wraps "every connected model is busy" around the primary's error.

Never raises: a failure to find words is the generic headline, and the
technical text is always there.
"""

from __future__ import annotations

import math
import re
from typing import Any

#: Where accounts, sign-ins and API keys live in the app: Settings' own
#: Connections group, whose first row holds the Accounts page (its Sign in
#: buttons, v1.353.0 G33, and every key). Named only to the GROUP on
#: purpose: the rows inside it are named like their pages (v1.354.0, G67),
#: so a deeper path would go stale with the next rename.
ACCOUNTS_PLACE = "Settings › Connections"

#: The name a person knows each provider by. The twin of the dashboard's
#: ``lib/onboarding`` FRIENDLY table (``providerDisplay``), so a headline and
#: the model menu say the same word ("claude-cli" → "Claude Code").
PLAIN_NAMES: dict[str, str] = {
    "claude-cli": "Claude Code",
    "codex-cli": "Codex",
    "grok-cli": "Grok CLI",
    "opencode-cli": "OpenCode",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google Gemini",
    "xai": "xAI",
    "openrouter": "OpenRouter",
    "ollama": "Ollama",
    "custom": "your own endpoint",
    "mock": "the demo model",
    "auto": "Auto",
}

#: The subscription CLIs a user SIGNS IN to (Accounts has a Sign in button
#: for each, v1.353.0 G33) — a refusal from one is about the sign-in, not a key.
SIGN_IN_CLIS: frozenset[str] = frozenset({"claude-cli", "codex-cli"})

#: The words when the provider is unknown.
_SOMEONE = "the AI service"

#: The kinds ``ModelRouter._unavailable_error`` stamps on its refusals.
REFUSAL_KINDS: frozenset[str] = frozenset(
    {"signed_out", "answered_error", "unreachable", "timeout", "interrupted", "cooldown"}
)

#: ``"<provider> API error <status>: <detail>"`` — the one message shape
#: ``adapters.base.provider_error_from_response`` builds. The provider may
#: carry a parenthesised note ("openai (ChatGPT backend)").
_API_ERROR_RX = re.compile(
    r"^\s*(?P<p>[A-Za-z0-9_.\-]+)(?:\s*\([^)]{0,60}\))?\s+API error\s+(?P<s>\d{3})\b"
)

#: How far the cause chain is walked (the router wraps once; a lane may wrap
#: again). Bounded so a cyclic chain can never spin.
_CHAIN = 6


def plain_name(provider: str, manager: Any = None) -> str:
    """The plain name for *provider* ("" when there is none). A fleet node
    reads as the label the user gave it (``egress.friendly_name``); an
    adapter's ``"openai (ChatGPT backend)"`` reads as its first word's name;
    an unknown id reads as itself. Never raises."""
    p = " ".join(str(provider or "").split())
    if not p:
        return ""
    key = p.lower()
    if key in PLAIN_NAMES:
        return PLAIN_NAMES[key]
    head = key.split(" (", 1)[0].strip()
    if head in PLAIN_NAMES:
        return PLAIN_NAMES[head]
    if key.startswith("fleet-"):
        try:
            from .egress import friendly_name

            return friendly_name(p, manager) or p
        except Exception:  # noqa: BLE001 — wording only
            return p
    return p


def heading(name: str) -> str:
    """*name* at the start of a sentence: OUR lower-case phrases ("your own
    endpoint") get a capital; a brand keeps its own casing ("xAI", never
    "XAI" — the v1.353.0 lane 3A review rule)."""
    if not name:
        return name
    first = name.split(" ")[0]
    if first != first.lower():
        return name
    return name[0].upper() + name[1:]


class _Facts:
    """What a failure carries, read once off its cause chain."""

    __slots__ = ("kind", "provider", "cli", "status", "partial", "retry_in", "transport")

    def __init__(self) -> None:
        self.kind = ""
        self.provider = ""
        self.cli = ""
        self.status: int | None = None
        self.partial = False
        self.retry_in = 0.0
        self.transport = ""


def _cli_sign_in_refusal(text: str) -> str:
    """The CLI provider whose OWN sign-in refusal *text* carries ("" when
    none). Every CLI sign-in failure (``cli_auth.cli_failure_message``, the
    Claude transport's ``signed_out_message``) puts ``cli_auth.SIGN_IN_FIX``
    verbatim in its message: matched against that one constant, never
    guessed from loose words. Never raises."""
    if not text:
        return ""
    try:
        from .cli_auth import CLI_BINARIES, SIGN_IN_FIX

        for provider, binary in CLI_BINARIES.items():
            fix = SIGN_IN_FIX.get(binary, "")
            if fix and fix in text:
                return provider
    except Exception:  # noqa: BLE001 — wording only
        return ""
    return ""


def failure_facts(exc: BaseException | None) -> _Facts:
    """The typed fields of *exc* and what it was raised from (the first value
    found for each field wins, outermost first). Never raises."""
    f = _Facts()
    seen = 0
    cur = exc
    while cur is not None and seen < _CHAIN:
        try:
            if not f.kind:
                # Only the router's own refusal kinds count: another
                # exception's unrelated ``kind`` attribute must not steer
                # the words.
                k = str(getattr(cur, "kind", "") or "")
                f.kind = k if k in REFUSAL_KINDS else ""
            if not f.kind:
                # The CLI's OWN sign-in refusal (a mid-turn "not logged
                # in" — the probe had not said so yet) carries no typed
                # kind, only cli_auth's one remedy sentence (v1.354.0
                # review): read as signed out, never "press Retry".
                cli = _cli_sign_in_refusal(str(cur) or "")
                if cli:
                    f.kind = "signed_out"
                    if not f.cli:
                        f.cli = cli
            if not f.provider:
                f.provider = str(getattr(cur, "failed_provider", "") or "")
            if not f.cli:
                f.cli = str(getattr(cur, "cli", "") or "")
            if not f.partial:
                f.partial = bool(getattr(cur, "partial", False) is True)
            if not f.retry_in:
                try:
                    f.retry_in = float(getattr(cur, "retry_in", 0.0) or 0.0)
                except (TypeError, ValueError):
                    f.retry_in = 0.0
            if f.status is None:
                st = getattr(cur, "status_code", None)
                if isinstance(st, int) and not isinstance(st, bool):
                    f.status = st
            m = _API_ERROR_RX.match(str(cur) or "")
            if m is not None:
                if not f.provider:
                    f.provider = m.group("p")
                if f.status is None:
                    f.status = int(m.group("s"))
            if not f.transport:
                try:
                    from .router import local_failure_kind

                    if isinstance(cur, Exception):
                        f.transport = local_failure_kind(cur) or ""
                except Exception:  # noqa: BLE001 — wording only
                    f.transport = ""
        except Exception:  # noqa: BLE001 — a hostile __str__/attr is no reason to fail
            pass
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return f


def technical_text(exc: BaseException | None, detail: str | None = None) -> str:
    """The original detail (*detail* when the caller already built it — the
    chat lanes' ``_error_detail``), secret-shaped values masked, bounded."""
    text = detail if detail is not None else ""
    if not text and exc is not None:
        try:
            text = str(exc).strip()
        except Exception:  # noqa: BLE001
            text = ""
        if not text:
            try:
                from .router import failure_reason

                text = f"{type(exc).__name__}: {failure_reason(exc)}"  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                text = type(exc).__name__
    try:
        from ..detections.redact import mask

        text = mask(text)
    except Exception:  # noqa: BLE001 — never fail on wording
        pass
    return str(text or "")[:2000]


def _is_local(provider: str) -> bool:
    try:
        from .local import is_local_provider

        return bool(provider) and is_local_provider(provider)
    except Exception:  # noqa: BLE001
        return False


def classify(
    exc: BaseException | None, *, provider: str = "", manager: Any = None
) -> "tuple[str, str] | None":
    """``(title, fix)`` for a failure this module RECOGNISES, else ``None``
    (the caller decides the generic words). Neither ever carries the
    server's own text — that is ``technical``'s job."""
    f = failure_facts(exc)
    who = f.provider or str(provider or "").strip()
    name = plain_name(who, manager) or _SOMEONE
    Name = heading(name)
    kind = f.kind

    if kind == "signed_out":
        cli = f.cli or who
        cname = plain_name(cli, manager) or name
        return (
            f"{heading(cname)} isn't signed in",
            f"Press Sign in beside {cname} in {ACCOUNTS_PLACE}, then retry.",
        )
    if kind == "answered_error":
        return (
            f"{Name} answered with an error",
            "Check it, or pick another model for this chat, and retry. Iron Jarvis"
            " won't switch to a different AI without asking. You can allow that in"
            " Settings.",
        )
    if kind == "cooldown":
        secs = int(math.ceil(max(0.0, f.retry_in))) if f.retry_in else 0
        when = f"in {secs} s" if secs else "in a moment"
        return (
            f"{Name} failed several times in a row",
            f"Try again {when}, or pick another model for this chat.",
        )
    transport = kind if kind in ("timeout", "interrupted", "unreachable") else ""
    if not kind and f.status is None:
        transport = f.transport
    if transport == "timeout":
        if _is_local(who):
            fix = "Give it time to finish loading, or pick another model for this chat, and retry."
        else:
            fix = "Press Retry, or pick another model for this chat."
        return (f"{Name} didn't answer in time", fix)
    if transport == "interrupted":
        if f.partial:
            return (
                f"The connection to {name} dropped mid-answer",
                "The reply above is incomplete. Press Retry.",
            )
        return (
            f"The connection to {name} dropped",
            "Press Retry. If it keeps happening, check your internet connection or"
            " pick another model for this chat.",
        )
    if transport == "unreachable":
        if _is_local(who):
            fix = "Bring it back up, or pick another model for this chat, and retry."
        else:
            fix = "Check your internet connection, or pick another model for this chat, and retry."
        return (f"{Name} isn't connected right now", fix)

    status = f.status
    if status in (401, 403):
        if who in SIGN_IN_CLIS:
            return (
                f"{Name} refused your sign-in",
                f"Press Sign in beside {name} in {ACCOUNTS_PLACE}, then retry.",
            )
        return (f"{Name} refused your key", f"Update it in {ACCOUNTS_PLACE}.")
    if status == 429 or (status is not None and 500 <= status <= 599):
        return (f"{Name} is busy", "Try again shortly, or pick another model for this chat.")
    if status is not None:
        return (
            f"{Name} answered with an error",
            "Retry, or pick another model for this chat.",
        )
    return None


#: The generic words when nothing typed says more.
GENERIC_TITLE = "Iron Jarvis couldn't finish this answer"
GENERIC_FIX = "Press Retry. If it happens again, pick another model for this chat."


def friendly_error(
    exc: BaseException | None,
    *,
    provider: str = "",
    manager: Any = None,
    technical: str | None = None,
) -> dict[str, str]:
    """``{title, fix, technical}`` for *exc* (see the module docstring).
    *provider* is the caller's hint (the provider the turn asked for), used
    only when the failure names none itself. Never raises."""
    try:
        words = classify(exc, provider=provider, manager=manager)
    except Exception:  # noqa: BLE001 — words are never a new failure
        words = None
    title, fix = words if words is not None else (GENERIC_TITLE, GENERIC_FIX)
    return {"title": title, "fix": fix, "technical": technical_text(exc, technical)}


def plain_reason(exc: BaseException | None, *, manager: Any = None) -> str:
    """One plain sentence for a run summary ("The run failed: <this>"): the
    headline and its fix when the failure is recognised, else the failure's
    own message (masked) — never the exception's CLASS, which goes to the
    log. Never raises; never empty."""
    try:
        words = classify(exc, manager=manager)
    except Exception:  # noqa: BLE001
        words = None
    if words is not None:
        return f"{words[0]}. {words[1]}"
    raw = ""
    try:
        raw = " ".join(str(exc or "").split())
    except Exception:  # noqa: BLE001
        raw = ""
    if raw:
        try:
            from ..detections.redact import mask

            raw = mask(raw)
        except Exception:  # noqa: BLE001
            pass
        return raw[:400]
    try:
        from .router import failure_reason

        return f"it stopped with an error ({failure_reason(exc)})"  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        return "it stopped with an error"
