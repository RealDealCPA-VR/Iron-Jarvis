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
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

#: WHERE the words are read (v1.359.0, wave 9). A chat's fix says what to do
#: in the chat ("Press Retry, or pick another model for this chat."); a RUN —
#: a schedule's fire, a background job, a helper agent — is read on its
#: Schedules row, its run page, the bell and the phone, where there is no
#: Retry button and no "this chat", so its fix says what to do there ("Run it
#: again, or choose another model in Settings."). ``Orchestrator.run_session``
#: sets "run" for the length of every run (:func:`run_surface`), so a failure
#: worded inside one — the run's summary, the background-job banner the
#: router publishes, an alert — reads the run's words; a chat turn never sets
#: it and keeps the chat's sentence byte for byte.
SURFACE_CHAT = "chat"
SURFACE_RUN = "run"
#: v1.378.0: a Build session's turn (the IJ harness). Every sentence reads as
#: the chat's except the ONE whose way out differs: a safety refusal is undone
#: in a session with Rewind, in a chat by editing an earlier message.
SURFACE_BUILD = "build"
#: v1.378.0 review: the PHONE (a messaging channel's chat lane, and a desktop
#: reply delivered into a phone thread) and a Build PANE's chat (one
#: conversation per terminal). Each reads as the chat's except a safety
#: refusal's way out, which must be one that surface can take: the phone has
#: "/new" and no Edit; a pane has neither a new-chat press nor Edit, and a
#: new terminal IS a new conversation.
SURFACE_PHONE = "phone"
SURFACE_PANE = "pane"
_SURFACE: ContextVar[str] = ContextVar("ij_failure_surface", default=SURFACE_CHAT)
#: Where a run's model is chosen (the default model lives in Settings).
RUN_MODEL_PLACE = "Settings"


@contextmanager
def run_surface() -> Iterator[None]:
    """Word every failure inside this block for a RUN (see :data:`_SURFACE`).
    The context rides every task and worker thread the run makes."""
    token = _SURFACE.set(SURFACE_RUN)
    try:
        yield
    finally:
        _SURFACE.reset(token)


@contextmanager
def phone_surface() -> Iterator[None]:
    """Word every failure inside this block for the PHONE (v1.378.0 review):
    ``comm.inbound`` arms it around the phone's chat turn, and the desktop's
    reply into a phone thread around its own."""
    token = _SURFACE.set(SURFACE_PHONE)
    try:
        yield
    finally:
        _SURFACE.reset(token)


def set_build_surface() -> Any:
    """Word failures as a Build session's from here (v1.378.0); returns the
    token for :func:`reset_surface`. ``routes/build.start_build_turn`` arms it
    while the turn's driver task is created (the task copies the context,
    like the account pin)."""
    return _SURFACE.set(SURFACE_BUILD)


def reset_surface(token: Any) -> None:
    """Undo :func:`set_build_surface`. Never raises."""
    try:
        _SURFACE.reset(token)
    except Exception:  # noqa: BLE001
        pass


def current_surface() -> str:
    """"run" inside :func:`run_surface`, "build" inside a Build turn
    (:func:`set_build_surface`), "phone" inside :func:`phone_surface`, else
    "chat". Never raises."""
    try:
        value = _SURFACE.get()
        return value if value in (SURFACE_RUN, SURFACE_BUILD, SURFACE_PHONE) else SURFACE_CHAT
    except Exception:  # noqa: BLE001
        return SURFACE_CHAT

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
    # v1.358.0: the two local model servers the dashboard now names (lane
    # 8D's FRIENDLY rows) — kept equal by tests/test_integrate_wave4_v13540.
    "lmstudio": "LM Studio",
    "llamacpp": "llama.cpp",
    "mock": "the demo model",
    "auto": "Auto",
}

#: Brands spelled in lower case on purpose — never capitalised at the start
#: of a sentence ("llama.cpp couldn't finish this answer").
_LOWER_CASE_BRANDS: frozenset[str] = frozenset({"llama.cpp"})

#: The subscription CLIs a user SIGNS IN to (Accounts has a Sign in button
#: for each, v1.353.0 G33) — a refusal from one is about the sign-in, not a key.
SIGN_IN_CLIS: frozenset[str] = frozenset({"claude-cli", "codex-cli"})

#: The words when the provider is unknown.
_SOMEONE = "the AI service"

#: The kinds ``ModelRouter._unavailable_error`` stamps on its refusals — and
#: (v1.378.0) ``"refusal"``, a provider's safety check declining the
#: conversation (``adapters.base.ProviderRefusal``).
REFUSAL_KINDS: frozenset[str] = frozenset(
    {"signed_out", "answered_error", "unreachable", "timeout", "interrupted", "cooldown", "refusal"}
)

#: v1.378.0: a vendor's refusal CATEGORY in plain words, for the ones whose
#: meaning is plain (Anthropic's ``stop_details.category``, Codex's
#: ``cyberPolicy``). Any other category — or none — is left out of the
#: sentence (it stays in the technical fold); a topic is never guessed.
REFUSAL_TOPICS: dict[str, str] = {
    "cyber": "computer security",
    "bio": "biology",
    "military_weapons": "weapons",
    "general_harms": "harmful content",
}

#: The way out of a declined conversation, per surface (v1.378.0). Retry can
#: never help (the same conversation is judged again), so none says "Retry".
REFUSAL_WAY_OUT: dict[str, str] = {
    SURFACE_CHAT: "Start a new chat, or edit an earlier message to go back before the part it flagged.",
    SURFACE_BUILD: "Rewind to before the message that set it off, or start a new session.",
    SURFACE_RUN: "Change the task, then run it again.",
    # v1.378.0 review: a way out each surface can really take.
    SURFACE_PHONE: "Send /new to start a new conversation.",
    SURFACE_PANE: "Open a new terminal to start a new conversation.",
}

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
    if first != first.lower() or first in _LOWER_CASE_BRANDS:
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


def _transport_kind(text: str) -> "tuple[str, bool]":
    """``(kind, partial)`` for an error the Claude transport made, read off
    the transport's OWN constants (``claude_native.transport.TIMEOUT_PREFIX``
    / ``INCOMPLETE_PREFIX`` / ``DROPPED``), never loose words — the fallback
    for an error that arrives without the ``kind`` the transport stamps on it
    (v1.358.0: a Claude Code timeout read as an anonymous "couldn't finish").
    ``("", False)`` when it is not one. Never raises."""
    if not text:
        return "", False
    try:
        from .adapters.claude_native.transport import DROPPED, INCOMPLETE_PREFIX, TIMEOUT_PREFIX

        t = text.lstrip()
        if t.startswith(TIMEOUT_PREFIX):
            return "timeout", False
        if t.startswith(INCOMPLETE_PREFIX):
            return "interrupted", True
        if t.startswith(DROPPED):
            return "interrupted", False
    except Exception:  # noqa: BLE001 — wording only
        return "", False
    return "", False


#: ``"<provider id>: …"`` — the prefix an adapter puts on its own errors
#: ("claude-cli: CLI timed out …"). Counted only for a provider id this
#: module knows (:data:`PLAIN_NAMES`), so an unrelated "error: …" names no one.
_PROVIDER_PREFIX_RX = re.compile(r"^\s*(?P<p>[a-z][a-z0-9_.\-]{1,40}):\s")


def _provider_error_type() -> "type | tuple":
    """The adapters' typed failure class (``()`` if it cannot be imported —
    ``isinstance(x, ())`` is simply False)."""
    try:
        from .adapters.base import ProviderError

        return ProviderError
    except Exception:  # noqa: BLE001 — wording only
        return ()


def _prefix_provider(text: str) -> str:
    m = _PROVIDER_PREFIX_RX.match(text or "")
    if m is None:
        return ""
    p = m.group("p")
    return p if p in PLAIN_NAMES and p not in ("auto", "mock") else ""


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
            if not f.kind:
                # v1.358.0: the Claude transport's own timeout / cut-off
                # answer, by its constants, when it arrived untyped.
                tkind, tpartial = _transport_kind(str(cur) or "")
                if tkind:
                    f.kind = tkind
                    if tpartial:
                        f.partial = True
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
            if not f.provider and isinstance(cur, _provider_error_type()):
                # v1.358.0: an adapter's own "<provider id>: …" prefix names
                # the provider that failed ("claude-cli: CLI timed out").
                f.provider = _prefix_provider(str(cur) or "")
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
    # v1.358.0: a wrapper's "claude-cli: claude-cli: …" is said once.
    return undoubled(str(text or ""))[:2000]


def _is_local(provider: str) -> bool:
    try:
        from .local import is_local_provider

        return bool(provider) and is_local_provider(provider)
    except Exception:  # noqa: BLE001
        return False


def refusal_words(
    exc: BaseException | None, *, name: str = "", surface: str | None = None
) -> "tuple[str, str]":
    """``(title, fix)`` for a provider's SAFETY REFUSAL (v1.378.0,
    ``adapters.base.ProviderRefusal``) — one sentence the user can act on,
    never "Press Retry" (the same conversation is judged again). Read off the
    refusal's typed fields: ``partial`` (some of the answer arrived),
    ``whole_conversation`` (the vendor judges the whole conversation — every
    new message in it is declined too) and ``category`` (said in plain words
    only when :data:`REFUSAL_TOPICS` knows it). The way out depends on WHERE
    it is read (:data:`REFUSAL_WAY_OUT`). Never raises."""
    try:
        from .adapters.base import refusal_of

        refused = refusal_of(exc)
    except Exception:  # noqa: BLE001 — wording only
        refused = None
    where = surface or current_surface()
    if where not in REFUSAL_WAY_OUT:
        where = SURFACE_CHAT
    run = where == SURFACE_RUN
    Name = heading(name or _SOMEONE)
    partial = bool(getattr(refused, "partial", False))
    whole = bool(getattr(refused, "whole_conversation", False))
    topic = REFUSAL_TOPICS.get(str(getattr(refused, "category", "") or "").strip().lower(), "")
    check = f"Its safety check on {topic}" if topic else "Its safety check"
    if run:
        title = f"{Name} declined to finish this task" if partial else f"{Name} declined to do this task"
        flagged = (
            f"{check} flagged something in this task, so running it again as it is will be declined too."
            if whole
            else f"{check} declined this answer and may decline it again."
        )
        lead = "Its answer is incomplete. " if partial else ""
    else:
        title = (
            f"{Name} declined to finish this answer" if partial
            else f"{Name} declined to answer this conversation"
        )
        flagged = (
            f"{check} flagged something in this conversation, so every new message here will be declined too."
            if whole
            else f"{check} declined this answer and may decline new messages in this conversation too."
        )
        lead = "The reply above is incomplete. " if partial else ""
    return (title, f"{lead}{flagged} {REFUSAL_WAY_OUT[where]}")


def refusal_reason(exc: BaseException | None, *, manager: Any = None) -> str:
    """The run's plain sentence (:func:`plain_reason`) when *exc* is — or was
    raised from — a provider's SAFETY REFUSAL, else "" (v1.378.0 review).

    The ONE rule for every place that used to print a failed run's exception
    raw ("ProviderRefusal: claude-cli: … stop_reason refusal …"): a phone-
    started job, a workflow's agent step, a delegated / spawned helper's
    report to its parent. Nothing reached anyone, so ``partial`` is read as
    False. Any other failure is the caller's to word, exactly as before.
    Never raises."""
    try:
        from .adapters.base import refusal_of

        refused = refusal_of(exc)
        if refused is None:
            return ""
        refused.partial = False
        return plain_reason(exc, manager=manager)
    except Exception:  # noqa: BLE001 — wording only
        return ""


def classify(
    exc: BaseException | None,
    *,
    provider: str = "",
    manager: Any = None,
    surface: str | None = None,
) -> "tuple[str, str] | None":
    """``(title, fix)`` for a failure this module RECOGNISES, else ``None``
    (the caller decides the generic words). Neither ever carries the
    server's own text — that is ``technical``'s job.

    ``surface`` (v1.359.0) says where the words are read: "chat" (the
    chat's own sentence, byte for byte as before) or "run" (a run read
    outside the chat: "Run it again, or choose another model in Settings.");
    None reads :func:`current_surface`. The headline never changes."""
    f = failure_facts(exc)
    who = f.provider or str(provider or "").strip()
    name = plain_name(who, manager) or _SOMEONE
    Name = heading(name)
    kind = f.kind
    run = (surface or current_surface()) == SURFACE_RUN
    other = (
        f"choose another model in {RUN_MODEL_PLACE}" if run else "pick another model for this chat"
    )
    again = "run it again" if run else "retry"

    if kind == "refusal":
        return refusal_words(exc, name=name, surface=surface)
    if kind == "signed_out":
        cli = f.cli or who
        cname = plain_name(cli, manager) or name
        return (
            f"{heading(cname)} isn't signed in",
            f"Press Sign in beside {cname} in {ACCOUNTS_PLACE}, then {again}.",
        )
    if kind == "answered_error":
        return (
            f"{Name} answered with an error",
            f"Check it, or {other}, and {again}. Iron Jarvis"
            " won't switch to a different AI without asking. You can allow that in"
            " Settings.",
        )
    if kind == "cooldown":
        secs = int(math.ceil(max(0.0, f.retry_in))) if f.retry_in else 0
        when = f"in {secs} s" if secs else "in a moment"
        lead = "Run it again" if run else "Try again"
        return (
            f"{Name} failed several times in a row",
            f"{lead} {when}, or {other}.",
        )
    transport = kind if kind in ("timeout", "interrupted", "unreachable") else ""
    if not kind and f.status is None:
        transport = f.transport
    if transport == "timeout":
        if _is_local(who):
            fix = f"Give it time to finish loading, or {other}, and {again}."
        elif run:
            fix = f"Run it again, or {other}."
        else:
            fix = "Press Retry, or pick another model for this chat."
        return (f"{Name} didn't answer in time", fix)
    if transport == "interrupted":
        if f.partial:
            return (
                f"The connection to {name} dropped mid-answer",
                "Its answer is incomplete. Run it again."
                if run
                else "The reply above is incomplete. Press Retry.",
            )
        return (
            f"The connection to {name} dropped",
            ("Run it again." if run else "Press Retry.")
            + f" If it keeps happening, check your internet connection or {other}.",
        )
    if transport == "unreachable":
        if _is_local(who):
            fix = f"Bring it back up, or {other}, and {again}."
        else:
            fix = f"Check your internet connection, or {other}, and {again}."
        return (f"{Name} isn't connected right now", fix)

    status = f.status
    if status in (401, 403):
        if who in SIGN_IN_CLIS:
            return (
                f"{Name} refused your sign-in",
                f"Press Sign in beside {name} in {ACCOUNTS_PLACE}, then {again}.",
            )
        return (f"{Name} refused your key", f"Update it in {ACCOUNTS_PLACE}.")
    if status == 429 or (status is not None and 500 <= status <= 599):
        lead = "Run it again shortly" if run else "Try again shortly"
        return (f"{Name} is busy", f"{lead}, or {other}.")
    if status is not None:
        return (
            f"{Name} answered with an error",
            f"{'Run it again' if run else 'Retry'}, or {other}.",
        )
    return None


#: The generic words when nothing typed says more.
GENERIC_TITLE = "Iron Jarvis couldn't finish this answer"
GENERIC_FIX = "Press Retry. If it happens again, pick another model for this chat."
#: v1.359.0: the generic fix read OUTSIDE the chat (a run, see ``_SURFACE``).
GENERIC_FIX_RUN = f"Run it again. If it happens again, choose another model in {RUN_MODEL_PLACE}."
#: v1.358.0 (the re-audit's confirmed regression): the generic headline's
#: ending — said after the provider's own name when the failure came from a
#: provider it knows ("Claude Code couldn't finish this answer"), never an
#: anonymous "Iron Jarvis" over a model's failure.
GENERIC_TITLE_TAIL = " couldn't finish this answer"
#: Readiness 12A (G28): the generic headline READ OUTSIDE THE CHAT. A run is
#: a task, not an answer: "Codex couldn't finish this task" on its run page,
#: its Schedules row, the bell and the phone — where the raw text ("codex-cli:
#: CLI exited 1: error: unexpected status 400 …") used to stand.
GENERIC_TITLE_RUN_TAIL = " couldn't finish this task"
GENERIC_TITLE_RUN = f"Iron Jarvis{GENERIC_TITLE_RUN_TAIL}"
#: Readiness 12A (G28): the run's generic fix when NO model caused the
#: failure (Iron Jarvis's own fault): there is no other model to choose, so it
#: says where the folded technical text is — the run page's "Details" fold
#: (``Session.failure_detail``), which every surface's row links to.
GENERIC_FIX_RUN_OWN = "Run it again. If it happens again, the run's Details in Iron Jarvis say what went wrong."


def generic_title(name: str = "", *, run: bool = False) -> str:
    """The generic headline: "<Name> couldn't finish this answer" for a
    provider it knows, else :data:`GENERIC_TITLE`. ``run`` (readiness 12A,
    G28): read outside the chat, "… couldn't finish this task". Pure."""
    n = " ".join(str(name or "").split())
    tail = GENERIC_TITLE_RUN_TAIL if run else GENERIC_TITLE_TAIL
    if n:
        return f"{heading(n)}{tail}"
    return GENERIC_TITLE_RUN if run else GENERIC_TITLE


def is_generic_title(title: Any) -> bool:
    """Is *title* the generic headline (named or not, a chat's or a run's)?
    The stream lane asks, to say a failure before the answer began as what
    it is. Pure."""
    t = str(title or "")
    return (
        t in (GENERIC_TITLE, GENERIC_TITLE_RUN)
        or t.endswith(GENERIC_TITLE_TAIL)
        or t.endswith(GENERIC_TITLE_RUN_TAIL)
    )


def _generic_name(exc: BaseException | None, provider: str, manager: Any) -> str:
    """Whose name the generic headline carries: the provider the failure
    itself names (the router's ``failed_provider``, an adapter's own
    "<provider>: …" / "<provider> API error" text), else the caller's hint
    — but only for a PROVIDER's failure (a typed ``ProviderError`` in the
    chain); Iron Jarvis's own fault, or Auto, stays "Iron Jarvis". Never
    raises."""
    try:
        f = failure_facts(exc)
        who = f.provider
        if not who:
            pe = _provider_error_type()
            cur, seen = exc, 0
            while cur is not None and seen < _CHAIN:
                if isinstance(cur, pe):
                    who = str(provider or "").strip()
                    break
                cur = cur.__cause__ or cur.__context__
                seen += 1
        if not who or who.lower() in ("auto",):
            return ""
        return plain_name(who, manager)
    except Exception:  # noqa: BLE001 — wording only
        return ""


#: "claude-cli: claude-cli: …" — a wrapper that prefixes the provider onto
#: an error that already carries it (``router.stream``'s "busy" sentence).
_DOUBLED_PREFIX_RX = re.compile(r"(?<![\w.-])([a-z][a-z0-9_.\-]{1,40}):\s+\1:")


def undoubled(text: str) -> str:
    """*text* with a provider prefix said twice in a row said once
    ("claude-cli: claude-cli: CLI timed out" -> "claude-cli: CLI timed
    out"). Pure; never raises."""
    try:
        prev = None
        out = str(text or "")
        while prev != out:  # "a: a: a:" folds all the way
            prev = out
            out = _DOUBLED_PREFIX_RX.sub(r"\1:", out)
        return out
    except Exception:  # noqa: BLE001
        return str(text or "")


def friendly_error(
    exc: BaseException | None,
    *,
    provider: str = "",
    manager: Any = None,
    technical: str | None = None,
    surface: str | None = None,
) -> dict[str, str]:
    """``{title, fix, technical}`` for *exc* (see the module docstring).
    *provider* is the caller's hint (the provider the turn asked for), used
    only when the failure names none itself. A failure the words cannot
    classify still names the provider it came from (v1.358.0,
    :func:`generic_title`); the technical text stays in ``technical``.
    *surface* (v1.359.0): where the fix is read — None reads
    :func:`current_surface` (a chat turn: the chat's words). Never raises."""
    run = (surface or current_surface()) == SURFACE_RUN
    try:
        words = classify(exc, provider=provider, manager=manager, surface=surface)
    except Exception:  # noqa: BLE001 — words are never a new failure
        words = None
    if words is not None:
        title, fix = words
    elif run:
        # Readiness 12A (G28): the run's own generic words (see
        # :func:`_run_generic_words`), so the router's background-task banner
        # and the run's summary say one thing.
        title, fix = _run_generic_words(exc, provider, manager)
    else:
        title = generic_title(_generic_name(exc, provider, manager))
        fix = GENERIC_FIX
    return {"title": title, "fix": fix, "technical": technical_text(exc, technical)}


def _run_generic_words(exc: BaseException | None, provider: str, manager: Any) -> "tuple[str, str]":
    """``(title, fix)`` for a RUN's failure nothing recognised (readiness 12A,
    G28): WHAT failed — the provider by its plain name when a provider's
    error is in the chain ("Codex couldn't finish this task"), else Iron
    Jarvis itself — and what to do next: choose another model when a model
    failed, else where the details are. Never the failure's own text (that is
    the technical detail, kept apart). Never raises."""
    try:
        name = _generic_name(exc, provider, manager)
    except Exception:  # noqa: BLE001 — wording only
        name = ""
    return generic_title(name, run=True), (GENERIC_FIX_RUN if name else GENERIC_FIX_RUN_OWN)


def worded_message(exc: BaseException | None) -> str:
    """The failure's OWN sentence when it was already written for a person,
    else "" (readiness 12A, G28). Those failures carry a marker, never a
    guess from their words:

    * ``no_failover`` — the refusals worded BY KIND for the user (Iron-Proxy's
      account sentences, a local-only chat's refusal, a held Build session,
      Codex's own session cap);
    * ``plain_words`` — Iron Jarvis's own refusals written as one sentence
      (``agents.orchestrator.EmergencyStopOn``).

    A safety refusal (:class:`~.adapters.base.ProviderRefusal`) is not one
    here: :func:`classify` words it for where it is read. The adapter's
    "<provider id>: " prefix is left out (an engine id, never the user's
    word), secret-shaped values masked, cut to 400. Never raises."""
    seen = 0
    cur = exc
    while cur is not None and seen < _CHAIN:
        try:
            marked = getattr(cur, "no_failover", False) is True or getattr(
                cur, "plain_words", False
            ) is True
            if marked and str(getattr(cur, "kind", "") or "") != "refusal":
                text = " ".join(str(cur or "").split())
                m = _PROVIDER_PREFIX_RX.match(text)
                if m is not None and m.group("p") in PLAIN_NAMES:
                    text = text[m.end():].strip()
                if text:
                    try:
                        from ..detections.redact import mask

                        text = mask(text)
                    except Exception:  # noqa: BLE001
                        pass
                    return undoubled(text)[:400]
        except Exception:  # noqa: BLE001 — a hostile __str__ is no reason to fail
            pass
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return ""


def _hint(provider: str) -> str:
    """A caller's provider hint that may NAME a failure: never Auto (a choice,
    not a provider) or the demo model (it never fails a call)."""
    p = " ".join(str(provider or "").split())
    return "" if p.lower() in ("auto", "mock") else p


def run_failure_words(
    exc: BaseException | None, *, manager: Any = None, provider: str = ""
) -> "tuple[str, str]":
    """``(reason, technical)`` for a failed RUN (readiness 12A, G28) — the ONE
    rule every surface outside the chat reads (the run's summary, so its run
    page, its Schedules row, the bell, the phone, the computer a task came
    from):

    * ``reason`` — one plain sentence, never the server's or the adapter's
      raw text: a recognised failure's headline and fix (:func:`classify`,
      worded for a run); a failure already written for a person, as it is
      (:func:`worded_message`); else WHAT failed and what to do next
      (:func:`_run_generic_words`: "Codex couldn't finish this task. Run it
      again. If it happens again, choose another model in Settings.");
    * ``technical`` — the failure's own text (masked, bounded) for the run
      page's folded Details, or "" when the reason already IS that text.

    ``provider`` is the run's provider, a hint used only when the failure
    names none itself. Never raises; the reason is never empty."""
    hint = _hint(provider)
    try:
        words = classify(exc, provider=hint, manager=manager, surface=SURFACE_RUN)
    except Exception:  # noqa: BLE001
        words = None
    try:
        technical = technical_text(exc)
    except Exception:  # noqa: BLE001
        technical = ""
    if words is not None:
        return f"{words[0]}. {words[1]}", technical
    worded = worded_message(exc)
    if worded:
        return worded, ""
    title, fix = _run_generic_words(exc, hint, manager)
    return f"{title}. {fix}", technical


def plain_reason(exc: BaseException | None, *, manager: Any = None, provider: str = "") -> str:
    """One plain sentence for a run summary ("The run failed: <this>"):
    :func:`run_failure_words`' reason — the headline and its fix when the
    failure is recognised, a failure already worded for a person as it is,
    else what failed and what to do next. Never the exception's CLASS and,
    since readiness 12A (G28), never the failure's raw text (that rides the
    run's folded Details). Never raises; never empty.

    v1.359.0 (wave 9): a run's summary is read OUTSIDE the chat (its
    Schedules row, its run page, the bell, the phone), so its fix is always
    the run's ("Run it again, or choose another model in Settings."), never
    "Press Retry, or pick another model for this chat."."""
    try:
        return run_failure_words(exc, manager=manager, provider=provider)[0]
    except Exception:  # noqa: BLE001
        return f"{GENERIC_TITLE_RUN}. {GENERIC_FIX_RUN_OWN}"
