"""TRY ONE TASK ON SEVERAL MODELS (v1.397.0).

The long-jobs plan's Phase 5 Part B, the same feature as IJ harness part 3's
Phase 8 (``docs/LONG-JOBS-AND-MODEL-TEAM-PLAN.md``, ``docs/IJ-HARNESS-PLAN-3.md``,
decision 8 — accepted): "Try this with Claude, Codex and the local model."
The session starts up to three ATTEMPTS, each on its own git branch in its
own worktree and on its own model; when they end, ONE card shows them side
by side (tests, files changed, time, usage or cost, why it stopped, the
finish check's verdict); the user presses **Keep** on one and only that
one's work joins the session's branch, while the others' folders go to the
trash with their branches kept. **Stop** ends all of them.

OPT-IN ONLY (decision 8). Attempts start from the USER's words in a message
(:func:`parse_request`: a sentence that starts "try this with A, B and C" or
"try it on two models") or from the box's "Try on…" control
(``BuildTurnBody.try_on``). Nothing a model does can start them: there is no
tool for it, and an attempt itself never arms the helper doors
(:data:`EXCLUDED_TOOLS`), so an attempt cannot start attempts or helpers.

THIS IS THE EXCEPTION to part 2's decision 4 ("always the session's own
model"), and only for a message that asks for it. Every rule that keeps a
model's work honest still holds, per attempt:

* its model is HELD for the attempt (``providers.hold``, armed in the
  attempt's own task context): no default fallback, no sideways failover, no
  capability reroute — a provider that cannot answer is the attempt's
  failure, never another model's work;
* its ACCOUNT follows the model team's rules (``agents/model_team``): the
  session's own account for the same vendor, else an account of the
  session's account group, else the vendor's first usable one — pinned —
  with the 95% floor (:func:`~iron_jarvis.agents.model_team.floor_refusal`)
  and, for a pay-per-use model that is not the session's own, the team's
  monthly pay-per-use limit;
* under LOCAL ONLY only models that stay on this network
  (``egress.stays_on_this_network``) — a cloud model in the list is refused
  in words and nothing starts;
* each attempt is a harness turn of the stream lane (``stream_chat_turn``
  with ``harness="auto"``) in its own worktree, under its own tool session
  ``build:<thread>~try<n>`` (its ledger rows, its programs —
  ``procs.end_session`` ends them when the attempt ends), so the FINISH CHECK
  runs inside each attempt and its record rides the attempt's ``done``;
* a folder that is not in a git repository is refused in words
  (:data:`NEEDS_GIT`). A session's work NOT COMMITTED yet (review: the agent
  asks before every commit, so a session that did anything has some) is
  CARRIED into every attempt (:func:`snapshot_commit`), and Keep then lands
  the kept result in the session's folder as changes not committed
  (:func:`land`) — only while the folder still holds what the attempts
  started from.

WHERE THE ATTEMPTS START (``branches.start`` with a start point): the
session's own branch head; a session that works in its folder directly
starts them from the folder's last commit (its base), and Keep then MOVES
the session onto the kept attempt's branch (``sessions.adopt_branch``) — the
work joins the session on a branch of its own and the user's main checkout
is never touched (a merge into the folder's branch would change it).

KEEP (:func:`keep`, a person's press, never the agent): Iron Jarvis commits
the kept attempt's work on its branch (``branches.commit``, the Commit
button's own call) and merges that branch into the session's branch
(``branches.merge`` — the merge machinery's own safety: a conflict is
checked first and refused in words with NOTHING changed, the session's
worktree must be clean, never a force). Only then do the attempts' folders
move to the trash (``branches.remove_worktree``; their branches stay). Keep
is refused while any attempt still runs.

The card (:func:`saved_card`) is kept on the reply line (``attempts``), so a
rejoin, a reload or a restart shows the same card; a crash mid-run leaves the
attempts the frames saw, marked cut off (``routes/build._ReplyWatch``).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator, Iterable

log = logging.getLogger(__name__)

#: At most this many attempts start for one message (the user's decision).
MAX_ATTEMPTS = 3
#: The side frame each attempt's row is drawn from.
EVENT = "attempt"
#: The reply line's key (``harness/sessions.LINE_KEYS``) and the done frame's.
LINE_KEY = "attempts"
#: An attempt's id prefix ("try1") and the tool session separator
#: (``build:<thread>~try1``).
ID_PREFIX = "try"
SID_SEP = "~"
#: The states an attempt's row is in.
STATES = ("starting", "running", "waiting", "done", "failed", "stopped", "limit")
FINAL_STATES = frozenset({"done", "failed", "stopped", "limit"})
#: Why an attempt ended (the card's words are :data:`WHY_WORDS`).
WHY = ("finished", "failed", "stopped", "limit", "cut_off")
WHY_WORDS = {
    "finished": "Finished",
    "failed": "Failed",
    "stopped": "Stopped",
    "limit": "Stopped at an account limit",
    "cut_off": "Cut off when Iron Jarvis stopped",
}
#: Never armed in an attempt: an attempt is ONE model's own try — it starts no
#: helpers (they would run on other models and muddy the comparison) and no
#: attempts of its own; the question card and the plan door belong to the
#: session's own turn (an attempt's row draws approval cards only).
EXCLUDED_TOOLS = frozenset({
    "delegate", "spawn_agent", "list_agents", "assign_work", "consult", "delegate_remote",
    "ask_user", "exit_plan_mode", "escalate_to_agent", "workflow_draft",
})
#: Caps on what a frame / the saved card carries.
LABEL_CHARS = 80
REPLY_CHARS = 1200
ERROR_CHARS = 400
TASK_CHARS = 600
MAX_FILES = 50
#: An attempt that streams words but no step still says it is working this
#: often (the reply's stall watch reads frames).
ALIVE_EVERY_S = 15.0
#: How long Stop waits for the attempts to unwind before it moves on (the
#: cancel reaches every tool call at once; this bounds a stuck one).
STOP_WAIT_S = 30.0

# --------------------------------------------------------------------------- #
# the words
# --------------------------------------------------------------------------- #

NEEDS_GIT = (
    "Trying on several models needs a git folder: each attempt works on its own branch. "
    "This session's folder is not in a git repository, so nothing started."
)
TOO_MANY = "At most three attempts run at once. Name up to three models; nothing started."
NO_TASK = (
    "Say what to try, for example: “Try this with Claude and Codex: fix the failing test.” "
    "Nothing started."
)
PLAN_MODE = (
    "This session is planning. Approve the plan (or press Plan first again) before you try the work "
    "on several models; nothing started."
)
NOTHING_TO_TRY = (
    "Not enough models are ready to try this on{local}. Name the models, for example: "
    "“Try this with Claude and Codex”; nothing started."
)
ATTEMPT_TAIL = " Name another model to try it on; nothing started."
KEEP_RUNNING = "Keep waits until every attempt has ended. Stop them, or wait for them to finish."
KEEP_NONE = "There are no attempts for that message."
KEEP_UNKNOWN = "That attempt is not one of this message's."
KEEP_GONE = "That attempt's folder is gone, so there is nothing to keep."
KEEP_NO_BRANCH = "That attempt has no branch to keep."
KEEP_SESSION_GONE = (
    "This session's own branch is gone (discarded or removed), so there is nothing to merge the "
    "attempt into."
)
KEEP_OTHER_BRANCH = (
    "These attempts started from the branch {base}, and this session works on {branch} now, so "
    "they cannot be kept here. Merge the attempt's branch yourself if you want it."
)
KEEP_MOVED = (
    "This session moved onto a branch since the attempts started, so the attempt was not kept. "
    "Merge its branch with git, or try again."
)
KEEP_SAVE_FAILED = "The attempt was kept, but the card could not be updated. Reload the session."
#: Review (v1.397.0): a card a session got when it was BRANCHED from another
#: one belongs to that other session — its folders are that session's.
KEEP_COPIED = (
    "These attempts belong to the session this one was branched from, so they are kept or "
    "discarded there."
)
#: Review (v1.397.0): the line of an attempts turn a Stop-less end cut off (a
#: restart or a crash) — never "press Retry": a retry would start every
#: attempt again (new branches, new spend) while a finished one can still be
#: kept, and a retry of the "Try on…" control's message was a plain turn.
CUT_OFF_LINE = (
    "Iron Jarvis stopped while the attempts worked (a restart or a crash), and nothing restarts on "
    "its own. The card shows what each attempt did; a finished one can still be kept."
)
DISCARD_NONE = "There is nothing to discard: every attempt's folder is already in the trash."
DISCARD_KEPT = "You kept one of these attempts; the others' folders are already in the trash."


class AttemptsRefused(Exception):
    """A request for attempts refused in ONE plain sentence, before anything
    started (``status`` is the HTTP status the route answers with)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _short(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------- #
# the request: the user's words, or the "Try on…" control
# --------------------------------------------------------------------------- #


@dataclass
class Request:
    """What the user asked for: the models by NAME (``names``), or how many
    (``count``), or exact menu rows from the "Try on…" control (``picks``),
    and the work to try (``task``, "" = the session's last question)."""

    names: list[str] = field(default_factory=list)
    count: int = 0
    picks: list[tuple[str, str]] = field(default_factory=list)
    task: str = ""
    #: The user's own words that asked (the sentence), for the question line.
    said: str = ""


#: Words that may come before "try" at the start of a sentence.
_LEAD = r"(?:(?:please|now|ok|okay|so|and|then|also)[,\s]+)*(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
_TRY_RX = re.compile(
    r"^\s*" + _LEAD
    + r"try\s+(?:(?:this|it|that|the\s+task|this\s+task|the\s+same|the\s+same\s+thing)\s+)?(?:out\s+)?"
    r"(?:with|on|using|across|against)\s+(?P<rest>\S.*)$",
    re.I | re.S,
)
#: "two models", "three different models", "several models" — the WHOLE names
#: part (review, v1.397.0): a count followed by more words ("two models first,
#: then roll it out") is someone's code talk, not a request. "both" is not a
#: count here: "both models" names two models the user already has (an ORM's,
#: an ML project's).
_COUNT_RX = re.compile(
    r"^(?:the\s+)?(?P<n>two|three|2|3|a\s+couple\s+of|a\s+few|several)\s+"
    r"(?:different\s+|other\s+|of\s+(?:my|your|the)\s+)?(?:ai\s+|llm\s+)?models?"
    r"(?:\s+(?:at\s+once|in\s+parallel|side\s+by\s+side))?$",
    re.I,
)
_COUNTS = {"two": 2, "2": 2, "a couple of": 2, "three": 3, "3": 3, "a few": 3, "several": 3}
#: Where the names end and the work begins: "…the local model: fix the test".
_SPLIT_TASK_RX = re.compile(r"\s*(?::|;|\s—\s|\s–\s|\s-\s)\s*")
_NAME_SPLIT_RX = re.compile(r"\s*(?:,|\band\b|\bor\b|&|\+|\bplus\b)\s*", re.I)
#: A sentence boundary (a period/question/exclamation mark followed by space,
#: or a line break). A model id like "claude-4.5" has no space after its dot.
_SENTENCE_RX = re.compile(r"(?<=[.!?])\s+(?=\S)|\n+")
#: A fenced block (``` or ~~~) — pasted code or output, never the user asking.
_FENCE_RX = re.compile(r"(^|\n)[ \t]*(```|~~~)[^\n]*\n.*?(?:\n[ \t]*\2[^\n]*(?=\n|$)|$)", re.S)
#: A quoted line ("> …") — someone else's words.
_QUOTE_RX = re.compile(r"(^|\n)[ \t]*>[^\n]*")


#: Words that end a list of names without being one ("…and Codex too",
#: "…Codex, please", "…in parallel").
_FILLER_TAIL_RX = re.compile(
    r"(?:\s+|^)(?:too|as\s+well|also|again|now|please|pls|instead|thanks|thank\s+you|"
    r"at\s+once|in\s+parallel|side\s+by\s+side|for\s+me|for\s+this(?:\s+one)?)$",
    re.I,
)
#: A list that says NOT NOW ("…and Codex later") is no request.
_NOT_NOW_RX = re.compile(r"\b(?:later|tomorrow|next\s+time|some\s*day|eventually|another\s+time)\b", re.I)


def _clean_name(raw: str) -> str:
    name = re.sub(r"[`\"“”‘’]", "", str(raw or "")).strip().strip(".,!?").strip()
    name = " ".join(name.split())
    while True:
        cut = _FILLER_TAIL_RX.sub("", name).strip()
        if cut == name:
            return name
        name = cut


def _without_pasted(body: str) -> str:
    """The message with fenced blocks and quoted lines blanked (each becomes a
    line break), so a pasted README or log that says "Try it with Claude and
    Codex" is never read as the user asking."""
    out = _FENCE_RX.sub(lambda m: m.group(1) + "\n", body)
    return _QUOTE_RX.sub(lambda m: m.group(1) + "\n", out)


def _task_without(body: str, sentence: str, after: str, *, last: bool) -> str:
    """The work to try: the request sentence's own words after its colon, and
    the REST of the message as written — its line breaks and pasted code kept
    (review, v1.397.0: the rest was joined into one line, so code pasted with
    the request reached every attempt flattened)."""
    at = body.rfind(sentence) if last else body.find(sentence)
    if at < 0:
        rest = ""
    else:
        rest = (body[:at] + body[at + len(sentence):]).strip()
    if after and rest:
        return f"{after}\n{rest}" if not last else f"{rest}\n{after}"
    return (after or rest).strip()


def parse_request(text: Any) -> "Request | None":
    """The attempts a MESSAGE asks for, or None for an ordinary message.

    Only the FIRST or the LAST sentence of the message can ask (review,
    v1.397.0: a pasted README line "Try it with Claude and Codex" in the
    middle started attempts), never a fenced block or a quoted line, and only
    a sentence that STARTS with "try" (after "please", "can you" …) and goes
    on "this/it with|on|using|across …": "Try this with Claude, Codex and the
    local model: fix the failing test." / "Fix the test. Try it on two
    models." Whether the names are models is decided later (:func:`resolve`):
    a sentence whose names are not two or more of the user's models ("try it
    with a smaller input", "try it with the User model") is an ordinary
    message. Pure; never raises."""
    try:
        body = str(text or "").strip()
        if not body or "try" not in body.lower():
            return None
        parts = [p.strip() for p in _SENTENCE_RX.split(_without_pasted(body)) if p and p.strip()]
        if not parts:
            return None
        order = [(0, False)] + ([(len(parts) - 1, True)] if len(parts) > 1 else [])
        for idx, last in order:
            sentence = parts[idx]
            m = _TRY_RX.match(sentence)
            if not m:
                continue
            rest = m.group("rest").strip()
            split = _SPLIT_TASK_RX.split(rest, maxsplit=1)
            names_part = split[0].strip().rstrip(".!?").strip()
            after = split[1].strip() if len(split) > 1 else ""
            if _NOT_NOW_RX.search(names_part):
                continue  # "try it with Claude and Codex later": not now
            req = Request(task=_task_without(body, sentence, after, last=last), said=sentence)
            cm = _COUNT_RX.match(_clean_name(names_part))
            if cm:
                req.count = _COUNTS.get(" ".join(cm.group("n").lower().split()), 2)
                return req
            names = [_clean_name(n) for n in _NAME_SPLIT_RX.split(names_part)]
            req.names = [n for n in names if n]
            if len(req.names) < 2:
                # One name is someone's code talk as often as a request ("try
                # it with Ollama", "try it with gpt-5.5"): the words ask for
                # SEVERAL models; one model is the "Try on…" control's.
                continue
            return req
    except Exception:  # noqa: BLE001 — a message that cannot be read is an ordinary one
        log.debug("reading a try-on request failed", exc_info=True)
    return None


# --------------------------------------------------------------------------- #
# the models: resolved against the model menu and the team
# --------------------------------------------------------------------------- #


@dataclass
class Target:
    """One model an attempt runs on."""

    label: str
    provider: str
    model: str
    #: The model team member it is (its name), or "".
    member: str = ""
    record: Any = None
    #: What the user wrote for it ("" for the control).
    said: str = ""


#: A word that names a PROVIDER (the model it means is the session's own
#: model when the session runs there, else that provider's default) — in the
#: order the providers are tried (the first one connected wins).
PROVIDER_WORDS: dict[str, tuple[str, ...]] = {
    "claude": ("claude-cli", "anthropic"),
    "claude code": ("claude-cli",),
    "anthropic": ("anthropic", "claude-cli"),
    "codex": ("codex-cli",),
    "chatgpt": ("codex-cli", "openai"),
    "openai": ("openai", "codex-cli"),
    "gpt": ("openai", "codex-cli"),
    "grok": ("grok-cli", "xai"),
    "gemini": ("google",),
    "google": ("google",),
    "ollama": ("ollama",),
    "opencode": ("opencode-cli",),
}
#: Words that mean "the local model".
LOCAL_WORDS = frozenset({
    "local", "local model", "a local model", "my local model", "the local model", "local one",
    "the local one", "this pc", "this computer", "my pc", "local llm", "the local llm",
})
#: A model id that means "the provider's own default".
_PLACEHOLDERS = frozenset({"", "subscription", "default", "auto"})


def _norm(text: Any) -> str:
    out = re.sub(r"[`\"“”‘’]", "", str(text or "")).lower()
    return " ".join(out.split())


def _bare(text: Any) -> str:
    """Without a leading article and a trailing "model" ("the Codex model")."""
    out = re.sub(r"^(?:the|my|your|our|a|an)\s+", "", _norm(text))
    out = re.sub(r"\s+models?$", "", out)
    return out.strip()


def _provider_label(platform: Any, provider: str) -> str:
    try:
        from ..providers.egress import friendly_name

        return friendly_name(provider, getattr(platform, "providers", None)) or provider
    except Exception:  # noqa: BLE001
        return provider


def _row_label(platform: Any, row: dict[str, Any]) -> str:
    """A menu row's name as a person reads it: its own label, a node's name,
    else "<model> (<provider>)" — the provider's own name for its default."""
    provider = str(row.get("provider") or "")
    model = str(row.get("model") or "")
    if model.lower() in _PLACEHOLDERS:
        return _provider_label(platform, provider)
    own = str(row.get("label") or "").strip()
    if own:
        return own
    node = str(row.get("name") or "").strip()
    return f"{model} ({node or _provider_label(platform, provider)})"


def _available(row: dict[str, Any]) -> bool:
    return row.get("available") is not False


def _row_keys(row: dict[str, Any]) -> set[str]:
    provider = _norm(row.get("provider"))
    model = _norm(row.get("model"))
    keys = {model, f"{provider}/{model}", f"{provider}:{model}", f"{provider} {model}"}
    for k in ("label",):
        if row.get(k):
            keys.add(_norm(row[k]))
    for alias in row.get("aliases") or []:
        keys.add(_norm(alias))
    return {k for k in keys if k and k not in _PLACEHOLDERS}


def _session_label(platform: Any, provider: str, model: str) -> str:
    if model and model.lower() not in _PLACEHOLDERS:
        return model
    return _provider_label(platform, provider)


def _local_row(row: dict[str, Any]) -> bool:
    return row.get("on_this_network") is True and str(row.get("provider") or "") != "mock"


@dataclass
class Menu:
    """What the names are resolved against: the model menu's rows
    (``routes.connections.selectable_models``), the team's members, and the
    session's own pair."""

    rows: list[dict[str, Any]]
    members: list[Any]
    session_provider: str
    session_model: str

    def pairs(self, rows: Iterable[dict[str, Any]]) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for r in rows:
            pair = (str(r.get("provider") or ""), str(r.get("model") or ""))
            if pair[0] and pair not in out:
                out.append(pair)
        return out


def _provider_default(menu: Menu, platform: Any, provider: str) -> "dict[str, Any] | None":
    """The row a PROVIDER's name means: the session's own model on it, else
    its own default (a placeholder row, or the adapter's default model), else
    its first row. Available rows only."""
    rows = [r for r in menu.rows if str(r.get("provider") or "") == provider and _available(r)]
    if not rows:
        return None
    if provider == menu.session_provider:
        own = next((r for r in rows if str(r.get("model") or "") == menu.session_model), None)
        if own is not None:
            return own
    placeholder = next((r for r in rows if str(r.get("model") or "").lower() in _PLACEHOLDERS), None)
    if placeholder is not None:
        return placeholder
    try:
        from ..providers.egress import default_model_of

        default = default_model_of(provider, getattr(platform, "providers", None))
    except Exception:  # noqa: BLE001
        default = ""
    if default:
        own = next((r for r in rows if str(r.get("model") or "") == default), None)
        if own is not None:
            return own
    return rows[0]


def _target_of(platform: Any, row: dict[str, Any], said: str) -> Target:
    return Target(
        label=_short(_row_label(platform, row), LABEL_CHARS),
        provider=str(row.get("provider") or ""),
        model=str(row.get("model") or ""),
        said=said,
    )


def _member_target(platform: Any, record: Any, said: str) -> Target:
    from ..agents import model_team as MT

    return Target(
        label=_short(str(getattr(record, "name", "") or said), LABEL_CHARS),
        provider=str(getattr(record, "provider", "") or "").strip(),
        model=MT.pinned_model(platform, record),
        member=str(getattr(record, "name", "") or ""),
        record=record,
        said=said,
    )


def _names(pairs: Iterable[str]) -> str:
    items = [p for p in pairs if p]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _resolve_one(platform: Any, menu: Menu, raw: str) -> "Target | str | None":
    """One name: a Target, a refusal sentence (ambiguous / not connected /
    a member with no model of its own), or None (not a model at all)."""
    norm = _norm(raw)
    bare = _bare(raw)
    if not norm:
        return None
    # 1. a model team member, by name
    for record in menu.members:
        name = _norm(getattr(record, "name", ""))
        if name and name in (norm, bare):
            if not str(getattr(record, "provider", "") or "").strip():
                return (
                    f"'{getattr(record, 'name', raw)}' has no model of its own (it runs on whoever hands it "
                    "work), so it cannot be one of the models to try. Name a model, or set its model in "
                    "Settings › Model team."
                )
            return _member_target(platform, record, raw)
    # 2. "the local model"
    if norm in LOCAL_WORDS or bare in LOCAL_WORDS:
        rows = [r for r in menu.rows if _local_row(r) and _available(r)]
        pairs = menu.pairs(rows)
        if not pairs:
            return (
                "No model on this PC or your network is connected, so there is no local model to try "
                "it on. Connect one in Settings › Connections; nothing started."
            )
        if (menu.session_provider, menu.session_model) in pairs:
            row = next(r for r in rows if (r.get("provider"), r.get("model")) == (menu.session_provider, menu.session_model))
            return _target_of(platform, row, raw)
        if len(pairs) == 1:
            return _target_of(platform, rows[0], raw)
        named = _names(_row_label(platform, r) for r in rows[:4])
        return f"There is more than one local model ({named}). Say which one; nothing started."
    # 3. a provider's name
    for word in (norm, bare):
        providers = PROVIDER_WORDS.get(word)
        if not providers:
            continue
        for provider in providers:
            row = _provider_default(menu, platform, provider)
            if row is not None:
                return _target_of(platform, row, raw)
        known = [p for p in providers if any(str(r.get("provider") or "") == p for r in menu.rows)]
        label = _provider_label(platform, known[0] if known else providers[0])
        return f"{label} is not connected on this PC. Connect it in Settings › Connections, or name another model; nothing started."
    # 4. a model of the menu, by its id or its name
    matches = [r for r in menu.rows if norm in _row_keys(r) or (bare and bare in _row_keys(r))]
    if not matches:
        return None
    ready = [r for r in matches if _available(r)] or matches
    pairs = menu.pairs(ready)
    if len(pairs) > 1:
        same = [r for r in ready if str(r.get("provider") or "") == menu.session_provider]
        if len(menu.pairs(same)) == 1:
            ready = same
            pairs = menu.pairs(same)
    if len(pairs) > 1:
        named = _names(f"{p}/{m}" for p, m in pairs[:4])
        return f"“{raw}” could be {named}. Name one of them; nothing started."
    row = ready[0]
    if not _available(row):
        label = _provider_label(platform, str(row.get("provider") or ""))
        return f"{label} is not connected on this PC. Connect it in Settings › Connections, or name another model; nothing started."
    return _target_of(platform, row, raw)


def _cost_order(platform: Any, target: Target) -> int:
    from ..agents import model_team as MT

    cost = MT.cost_type(platform, target.provider, target.model)
    return {MT.COST_FREE: 0, MT.COST_PLAN: 1}.get(cost, 2)


def _auto_pick(platform: Any, menu: Menu, count: int, local_only: bool) -> list[Target]:
    """"Try it on two models": the session's own model first, then the team's
    members with a model of their own, then one model of each other connected
    provider — local first, then your plans; never a pay-per-use model (it is
    named, or not tried). Under Local only, only local models."""
    from ..agents import model_team as MT

    out: list[Target] = []
    seen: set[tuple[str, str]] = set()

    def add(t: "Target | None") -> None:
        if t is None or len(out) >= count:
            return
        pair = (t.provider, t.model)
        if not t.provider or pair in seen or t.provider == "mock":
            return
        if local_only and not _stays(platform, t):
            return
        if MT.cost_type(platform, t.provider, t.model) == MT.COST_PAY:
            return
        seen.add(pair)
        out.append(t)

    own = next(
        (r for r in menu.rows
         if (r.get("provider"), r.get("model")) == (menu.session_provider, menu.session_model) and _available(r)),
        None,
    )
    if own is not None:
        add(_target_of(platform, own, ""))
    elif menu.session_provider and menu.session_provider != "auto":
        add(Target(label=_session_label(platform, menu.session_provider, menu.session_model),
                   provider=menu.session_provider, model=menu.session_model))
    members = sorted(
        (r for r in menu.members if str(getattr(r, "provider", "") or "").strip()),
        key=lambda r: _cost_order(platform, _member_target(platform, r, "")),
    )
    for record in members:
        if MT.provider_ready(platform, str(getattr(record, "provider", "") or "")) is False:
            continue
        add(_member_target(platform, record, ""))
    providers: list[str] = []
    for r in menu.rows:
        p = str(r.get("provider") or "")
        if p and p not in providers and _available(r):
            providers.append(p)
    candidates = [row for row in (_provider_default(menu, platform, p) for p in providers) if row is not None]
    candidates.sort(key=lambda r: _cost_order(platform, _target_of(platform, r, "")))
    for row in candidates:
        add(_target_of(platform, row, ""))
    return out


def _stays(platform: Any, target: Target) -> bool:
    from ..providers.egress import stays_on_this_network

    try:
        return stays_on_this_network(
            target.provider, getattr(platform, "providers", None), getattr(platform, "config", None),
            target.model,
        )
    except Exception:  # noqa: BLE001 — fail closed
        return False


def resolve(platform: Any, req: Request, menu: Menu, *, local_only: bool = False) -> "list[Target] | None":
    """The models a request names — or None when the message is an ordinary
    one after all (none of its names is a model, and it says no "model").
    Raises :class:`AttemptsRefused` in words for: more than
    :data:`MAX_ATTEMPTS`, an unknown name beside known ones, an ambiguous
    name, a provider that is not connected, the same model twice, and
    "two models" when fewer are ready. Never a guess. BLOCKING-free (cached
    reads only)."""
    if req.picks:
        if len(req.picks) > MAX_ATTEMPTS:
            raise AttemptsRefused(TOO_MANY)
        out: list[Target] = []
        for provider, model in req.picks:
            row = next(
                (r for r in menu.rows if str(r.get("provider") or "") == provider and str(r.get("model") or "") == model),
                None,
            )
            if row is None:
                raise AttemptsRefused(
                    f"{provider}/{model} is not in the model menu any more. Pick again; nothing started."
                )
            if not _available(row):
                raise AttemptsRefused(
                    f"{_provider_label(platform, provider)} is not connected on this PC. Connect it in "
                    "Settings › Connections, or pick another model; nothing started."
                )
            out.append(_target_of(platform, row, ""))
        return _no_twins(out)
    if req.count:
        if req.count > MAX_ATTEMPTS:
            raise AttemptsRefused(TOO_MANY)
        picked = _auto_pick(platform, menu, req.count, local_only)
        if len(picked) < req.count:
            raise AttemptsRefused(NOTHING_TO_TRY.format(local=" that stay on this network" if local_only else ""))
        return picked
    resolved: list["Target | str | None"] = [_resolve_one(platform, menu, n) for n in req.names]
    known = [r for r in resolved if isinstance(r, (Target, str))]
    unknown = [n for n, r in zip(req.names, resolved) if r is None]
    # THE OPT-IN (review, v1.397.0): the words ask only when they name at
    # least two models. A name that is not a model ("the User model", "see
    # what happens", "Codex in two terminals") makes it an ordinary message —
    # unless it reads like a model's NAME beside a known one ("Codex and
    # Mistral Large"), which is said in words. When unsure: an ordinary turn.
    if len(known) < 2 and not (known and unknown and all(_looks_like_a_name(n) for n in unknown)):
        return None
    if unknown and not all(_looks_like_a_name(n) for n in unknown):
        return None
    if len(req.names) > MAX_ATTEMPTS:
        raise AttemptsRefused(TOO_MANY)
    for name, got in zip(req.names, resolved):
        if isinstance(got, str):
            raise AttemptsRefused(got)
        if got is None:
            raise AttemptsRefused(
                f"There is no model called “{name}” in your model menu or on your model team. Name a model "
                "from the model menu (or “the local model”); nothing started."
            )
    return _no_twins([t for t in resolved if isinstance(t, Target)])


#: Words a NAME never starts with: an article or a possessive (a thing of the
#: user's — "the User model", "my cache"), or a verb (a clause — "see what
#: happens", "let me know").
_NOT_A_NAME_START = frozenset({
    "the", "a", "an", "my", "your", "our", "this", "that", "these", "those", "its", "their", "his", "her",
    "see", "let", "tell", "check", "show", "make", "run", "compare", "report", "pick", "keep", "give",
    "find", "fix", "write", "add", "then", "also", "and", "or", "if", "when", "so", "use", "do", "try",
    "other", "some", "any", "all", "both", "each", "every", "more", "different", "same", "another",
})


def _looks_like_a_name(raw: str) -> bool:
    """Does an unknown name read like a MODEL's name ("Mistral Large",
    "llama-3.3", "Qwen3 Coder")? One to three words, never an article, a
    possessive or a verb first, never the word "model". Pure."""
    words = _norm(raw).split()
    if not 1 <= len(words) <= 3:
        return False
    if words[0] in _NOT_A_NAME_START or any(w in ("model", "models") for w in words):
        return False
    return not any("'" in w for w in words)


def _no_twins(targets: list[Target]) -> list[Target]:
    seen: dict[tuple[str, str], Target] = {}
    for t in targets:
        pair = (t.provider, t.model)
        if pair in seen:
            first = seen[pair]
            a = first.said or first.label
            b = t.said or t.label
            raise AttemptsRefused(
                f"{a} and {b} are the same model ({t.label}). Name different models; nothing started."
                if _norm(a) != _norm(b) else
                f"{a} is named twice. Name different models; nothing started."
            )
        seen[pair] = t
    if not targets:
        raise AttemptsRefused("Name at least one model to try it on; nothing started.")
    return targets


# --------------------------------------------------------------------------- #
# the rules each attempt follows: Local only, connected, money, the account
# --------------------------------------------------------------------------- #


def _stand_in(target: Target) -> Any:
    """A model from the menu as the model team's account rules read a member
    (``agents/model_team``: a name, a provider, a model, no account of its
    own, a role) — the rules are the team's, not a copy of them."""
    if target.record is not None:
        return target.record
    return SimpleNamespace(
        name=target.label, provider=target.provider, model=target.model, account="",
        roles_json=json.dumps(["code"]), tools_json="[]", base_type="builder",
    )


def _copy_pin(pin: Any) -> Any:
    """A FRESH pin for one attempt (a pin carries facts back — its limit — so
    two attempts on one account, or an attempt on the session's own account,
    never share one object)."""
    if pin is None:
        return None
    from ..iron_proxy.pin import AccountPin

    return AccountPin(profile_id=str(pin.profile_id), ij_provider=str(pin.ij_provider),
                      title=str(getattr(pin, "title", "") or ""))


def check_target(platform: Any, target: Target, *, local_only: bool, session_pair: tuple[str, str],
                 caller_pin: Any, scope: str) -> Any:
    """Apply the rules to one model; returns its account pin (or None — a
    local model, an API key, Iron-Proxy off) or raises
    :class:`AttemptsRefused` in words. BLOCKING (cached reads, one month's
    spend for a pay-per-use model)."""
    from ..agents import model_team as MT

    if local_only and not _stays(platform, target):
        raise AttemptsRefused(
            f"This session is Local only, so nothing may leave your network, and {target.label} is not on "
            "this PC or your network. Name models that run locally; nothing started."
        )
    if MT.provider_ready(platform, target.provider) is False:
        raise AttemptsRefused(
            f"{_provider_label(platform, target.provider)} is not connected on this PC. Connect it in "
            "Settings › Connections, or name another model; nothing started."
        )
    record = _stand_in(target)
    if (target.provider, target.model) != session_pair:
        cost = MT.cost_type(platform, target.provider, target.model)
        if cost == MT.COST_PAY:
            try:
                cap = max(0.0, float(getattr(getattr(platform, "config", None), "team_paid_cap_usd", 0.0) or 0.0))
            except (TypeError, ValueError):
                cap = 0.0
            if cap <= 0:
                raise AttemptsRefused(
                    f"{target.label} is pay per use, and your pay-per-use limit is $0 a month "
                    f"({MT.PAY_PLACE}), so nothing is tried on it. Raise the limit there, or name another "
                    "model; nothing started."
                )
            spend = MT.paid_spend_this_month(platform)
            if spend >= cap:
                raise AttemptsRefused(
                    f"{target.label} is pay per use, and this month's pay-per-use spend (${spend:.2f}) has "
                    f"reached your ${cap:.2f} limit ({MT.PAY_PLACE}). Name another model; nothing started."
                )
    if not MT.runs_elsewhere(record):
        return None
    led = MT._placements(platform)  # noqa: SLF001 — the team's own ledger: bursts count
    with led.lock:
        group = MT.work_group(platform, caller_pin, scope)
        pin, why = MT.member_account(platform, record, caller_pin, coding=True, group=group,
                                     recent=led.recent)
        if why:
            if target.record is None:
                why = why.replace(_MEMBER_ACCOUNT_TAIL, "")
            raise AttemptsRefused(why.rstrip() + " Nothing started.")
        floor = MT.floor_refusal(platform, record, pin, tail=ATTEMPT_TAIL)
        if floor:
            raise AttemptsRefused(floor)
        if pin is not None:
            led.note(scope, str(getattr(pin, "profile_id", "") or ""))
    return _copy_pin(pin)


# --------------------------------------------------------------------------- #
# git: where the attempts start, their worktrees, what they changed
# --------------------------------------------------------------------------- #


@dataclass
class StartPoint:
    """Where the attempts start: the folder their worktrees are made for, the
    commit, the branch they name as their base (the session's own, or the
    folder's), and whether Keep MOVES the session onto the kept branch (a
    session that worked in its folder directly).

    ``carried`` (review, v1.397.0): the session had work NOT COMMITTED yet
    (the agent asks before every commit, so a session that did anything has
    some) — ``sha`` is then a commit Iron Jarvis made of the session's CURRENT
    files on top of ``head`` (:func:`snapshot_commit`), every attempt starts
    from it, and Keep lands the kept result in the session's folder instead
    of merging (:func:`land`)."""

    checkout: str
    sha: str
    base: str
    adopt: bool
    folder: str
    head: str = ""
    carried: bool = False


#: The subject of the commit that carries a session's work not committed yet
#: into its attempts (on the ATTEMPTS' branches only — never the session's).
CARRIED_SUBJECT = "Work not committed yet in the session, carried into this attempt"


def _gi(args: list[str], cwd: Path, index: "Path | None" = None, *, timeout: float = 300.0,
        input: "bytes | None" = None) -> Any:
    """One git call through the branch module's own runner (bounded, the tree
    killed on a timeout, no terminal prompt), with a THROWAWAY index file when
    ``index`` is given — the folder's own index is never written. BLOCKING."""
    from ..git.integration import git_env, run_git

    env = git_env({"GIT_INDEX_FILE": str(index)} if index is not None else None)
    return run_git(["-c", "core.quotepath=off", *args], Path(cwd), timeout=timeout, env=env,
                   identity=False, input=input, encoding="utf-8")


def _copy_own_index(cwd: Path, to: Path) -> bool:
    """Copy the folder's own index to ``to`` (READ only: its file stats let
    ``git add -A`` skip every file that did not change instead of hashing the
    whole repository). False when there is none to copy. BLOCKING."""
    import shutil

    got = _gi(["rev-parse", "--git-path", "index"], cwd)
    if not got.ok or not got.out.strip():
        return False
    own = Path(got.out.strip())
    if not own.is_absolute():
        own = Path(cwd) / own
    try:
        shutil.copyfile(own, to)
        return True
    except OSError:
        return False


def folder_tree(cwd: Path, head: str) -> str:
    """The tree of the folder's CURRENT files — committed or not, new files
    included, ignored ones left out (``git add -A`` into a THROWAWAY copy of
    its index, else one read from ``head``). The folder's own index, files
    and refs are not touched. "" when it cannot be read. BLOCKING."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="ij-attempt-index-") as tmp:
        index = Path(tmp) / "index"
        for copied in (True, False):
            if copied:
                if not _copy_own_index(cwd, index):
                    continue
            else:
                with contextlib.suppress(OSError):
                    index.unlink()
                if not _gi(["read-tree", head], cwd, index).ok:
                    return ""
            if not _gi(["add", "-A", "--", "."], cwd, index).ok:
                continue
            got = _gi(["write-tree"], cwd, index)
            if got.ok and got.out.strip():
                return got.out.strip()
        return ""


def _tree_of(cwd: Path, commit: str) -> str:
    got = _gi(["rev-parse", "--verify", "-q", f"{commit}^{{tree}}"], cwd)
    return got.out.strip() if got.ok else ""


def snapshot_commit(cwd: Path, head: str) -> str:
    """A commit OBJECT of the folder's current files on top of ``head`` (no
    branch, no ref moves: nothing of the user's changes) — "" when the folder
    has nothing that is not committed. Raises :class:`AttemptsRefused` when
    the files cannot be read. BLOCKING."""
    from ..git.integration import default_identity, git_env, has_identity

    tree = folder_tree(cwd, head)
    if not tree:
        raise AttemptsRefused(
            "The session's files could not be read to start the attempts from them; nothing started.", 409,
        )
    if tree == _tree_of(cwd, head):
        return ""
    ident = [] if has_identity(cwd, git_env()) else default_identity()
    made = _gi([*ident, "commit-tree", tree, "-p", head, "-F", "-"], cwd,
               input=(CARRIED_SUBJECT + "\n").encode("utf-8"))
    sha = made.out.strip() if made.ok else ""
    if not sha:
        raise AttemptsRefused(
            "The session's work not committed yet could not be carried into the attempts; nothing started.", 409,
        )
    return sha


def start_point(row: Any) -> StartPoint:
    """The session's start point, or :class:`AttemptsRefused` in words: no
    git, not a repository, a branch that cannot be made. Work not committed
    yet is CARRIED into the attempts (:func:`snapshot_commit`). BLOCKING (a
    few git reads, one ``git add -A`` into a throwaway index)."""
    from . import branches as B
    from . import sessions as S

    if not B.git_path():
        raise AttemptsRefused(NEEDS_GIT, 409)
    facts = S.git_of(row)
    folder = S.folder_of(row)
    if facts is not None:
        wt = Path(facts.get("worktree", ""))
        if facts.get("removed_at") or not wt.is_dir():
            raise AttemptsRefused(B.GONE, 409)
        try:
            B._require_on_branch(facts, wt)  # noqa: SLF001 — the branch module's own check
        except B.GitRefused as exc:
            raise AttemptsRefused(str(exc), 409) from None
        head = B._ref_sha(wt, "HEAD")  # noqa: SLF001
        if not head:
            raise AttemptsRefused(NEEDS_GIT, 409)
        carried = snapshot_commit(wt, head)
        return StartPoint(checkout=facts.get("checkout") or facts.get("repo", ""), sha=carried or head,
                          base=facts.get("branch", ""), adopt=False, folder=folder, head=head,
                          carried=bool(carried))
    if not folder:
        raise AttemptsRefused(NEEDS_GIT, 409)
    info = B.inspect_folder(folder)
    if not info.get("repo"):
        raise AttemptsRefused(NEEDS_GIT, 409)
    if not info.get("can_branch"):
        raise AttemptsRefused(f"{info.get('note') or NEEDS_GIT} Nothing started.", 409)
    root = Path(info["root"])
    head = B._ref_sha(root, "HEAD")  # noqa: SLF001
    if not head:
        raise AttemptsRefused(NEEDS_GIT, 409)
    carried = snapshot_commit(root, head)
    return StartPoint(checkout=folder, sha=carried or head, base=str(info.get("branch") or ""), adopt=True,
                      folder=folder, head=head, carried=bool(carried))


KEEP_CHANGED_SINCE = (
    "This session's files changed since the attempts started, so keeping one now would lose those "
    "changes. Nothing was changed. Commit or undo the newer changes, or try the work again."
)
KEEP_SESSION_DIRTY = (
    "This session has changes that are not committed yet, made after the attempts started, and "
    "keeping one would merge over them. Nothing was changed. Commit them first (the Branch pane), "
    "then press Keep again."
)
KEEP_LAND_FAILED = (
    "The kept attempt's files could not be put in this session's folder ({why}). Nothing was changed."
)


def land(session_wt: Path, from_tree: str, to_tree: str) -> None:
    """KEEP for attempts that CARRIED the session's work not committed yet:
    the session's folder goes from ``from_tree`` (what the attempts started
    from — the folder must still hold exactly that, :func:`folder_tree`) to
    ``to_tree`` (the kept attempt's result), as changes NOT COMMITTED — the
    session's own commits stay the user's to make. One two-tree
    ``read-tree -m -u`` through a throwaway index: git refuses before it
    writes anything when a file is not what it should be. The session's own
    index and branch are not touched. Raises :class:`AttemptsRefused`.
    BLOCKING."""
    import tempfile

    head = _gi(["rev-parse", "--verify", "-q", "HEAD"], session_wt)
    if not head.ok or folder_tree(session_wt, head.out.strip()) != from_tree:
        raise AttemptsRefused(KEEP_CHANGED_SINCE, 409)
    with tempfile.TemporaryDirectory(prefix="ij-attempt-keep-") as tmp:
        index = Path(tmp) / "index"
        # A copy of the session's own index keeps the stats of every file
        # that is unchanged (a single-tree ``read-tree -m`` reuses them).
        if not (_copy_own_index(session_wt, index) and _gi(["read-tree", "-m", from_tree], session_wt, index).ok):
            with contextlib.suppress(OSError):
                index.unlink()
            if not _gi(["read-tree", from_tree], session_wt, index).ok:
                raise AttemptsRefused(KEEP_LAND_FAILED.format(why="git could not read the attempts' start"), 409)
        steps = (
            ["update-index", "-q", "--refresh"],
            ["read-tree", "-m", "-u", from_tree, to_tree],
        )
        for args in steps:
            got = _gi(args, session_wt, index)
            if not got.ok and args[0] != "update-index":
                from .branches import _masked

                said = (got.err or got.out or "").strip()
                why = _masked(said, 300).splitlines()[0] if said else "git refused"
                raise AttemptsRefused(KEEP_LAND_FAILED.format(why=why), 409)


def make_worktrees(start: StartPoint, targets: list[Target]) -> list[dict[str, str]]:
    """One branch + worktree per attempt (``branches.start`` from the start
    point, naming its base). All or nothing: a failure undoes the ones made
    (they hold nothing of the user's yet) and raises in words. BLOCKING."""
    from . import branches as B

    made: list[dict[str, str]] = []
    try:
        for t in targets:
            made.append(B.start(start.checkout, title=f"try {t.label}", start_point=start.sha, base=start.base))
    except B.GitRefused as exc:
        for got in made:
            with contextlib.suppress(Exception):
                B._undo_start(got.get("repo", ""), Path(got.get("worktree", "")),  # noqa: SLF001
                              got.get("branch", ""), start.sha)
        raise AttemptsRefused(f"{exc} Nothing started.", exc.status) from None
    return made


def changed_files(facts: "dict[str, Any] | None", base_commit: str) -> list[str]:
    """The files an attempt changed against where it started — committed and
    not, new files included (the folder's ``.gitignore`` respected), caches
    and build droppings left out. BLOCKING; [] when it cannot be read."""
    from . import branches as B
    from .folder_changes import _skip_dirs, cache_dir, cache_file

    try:
        if not facts:
            return []
        wt = Path(str(facts.get("worktree") or ""))
        if not wt.is_dir():
            return []
        names: list[str] = []
        try:
            changed = B._name_status(wt, base_commit or "HEAD")  # noqa: SLF001
        except B.GitRefused:
            changed = []
        for _code, _old, new in changed:
            if new and new not in names:
                names.append(new)
        for rel in B._untracked(wt):  # noqa: SLF001
            if rel and rel not in names:
                names.append(rel)
        skip = _skip_dirs()
        out = []
        for rel in names:
            bits = rel.replace("\\", "/").split("/")
            if any(b in skip or cache_dir(b) for b in bits[:-1]) or cache_file(bits[-1]):
                continue
            out.append(rel)
        return sorted(out)[:MAX_FILES]
    except Exception:  # noqa: BLE001
        log.debug("reading an attempt's changes failed", exc_info=True)
        return []


# --------------------------------------------------------------------------- #
# one attempt, running
# --------------------------------------------------------------------------- #


@dataclass
class Attempt:
    """One attempt: its model, account, branch and what happened."""

    id: str
    target: Target
    pin: Any
    git: dict[str, str]
    folder: str
    sid: str
    turn_id: str
    cost: str = ""
    state: str = "starting"
    why: str = ""
    words: str = ""
    steps: int = 0
    since: int = field(default_factory=_now_ms)
    ended: "int | None" = None
    t0: float = 0.0
    ms: "int | None" = None
    ask: "dict[str, Any] | None" = None
    reply: str = ""
    error: str = ""
    finish: "dict[str, Any] | None" = None
    files: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    served_provider: str = ""
    served_model: str = ""
    done: bool = False
    errored: bool = False
    cancelled: bool = False
    last_said: float = 0.0
    quiet_since_said: bool = False
    #: Review: its metered spend was written for the pay-per-use limit.
    spend_kept: bool = False
    #: Review: the session's cap ended it ({kind, limit}).
    cap_met: "dict[str, Any] | None" = None

    def metered_usd(self) -> float:
        """The METERED dollars this attempt spent (a subscription's list-price
        equivalent and a local model's nothing are 0)."""
        if self.usage.get("list_price_equivalent") is True:
            return 0.0
        try:
            return max(0.0, float(self.usage.get("cost_usd") or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def account_fields(self) -> dict[str, Any]:
        if self.pin is None:
            return {}
        return {"account": str(self.pin.profile_id), "account_name": str(getattr(self.pin, "title", "") or "")}

    def frame(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "label": self.target.label,
            "provider": self.target.provider,
            "model": self.target.model,
            "cost": self.cost,
            "branch": self.git.get("branch", ""),
            "state": self.state,
            "steps": self.steps,
            "since": self.since,
            "git": dict(self.git),
            "folder": self.folder,
            **self.account_fields(),
        }
        if self.target.member:
            out["member"] = self.target.member
        if self.ended is not None:
            out["ended"] = self.ended
        if self.ms is not None:
            out["ms"] = self.ms
        out["ask"] = self.ask
        if self.why:
            out["why"] = self.why
            out["words"] = self.words
        if self.finish is not None:
            out["finish"] = self.finish
            out["tests"] = self.finish.get("tests", "")
        if self.files:
            out["files"] = list(self.files)
        out["usage"] = dict(self.usage)
        if self.reply:
            out["reply"] = self.reply
        if self.error:
            out["error"] = self.error
        if self.served_provider:
            out["served_provider"] = self.served_provider
        if self.served_model:
            out["served_model"] = self.served_model
        return out

    # -- what the attempt's own frames say ------------------------------- #

    def fold(self, event: str, raw: str) -> bool:
        """Fold one frame of the attempt's turn; True when the row changed
        enough to be said again."""
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return False
        if not isinstance(data, dict):
            return False
        if self.state == "starting" and event not in ("phase",):
            self.state = "running"
        if event == "tool_call":
            if data.get("status") == "finished":
                self.steps += 1
            return True
        if event == "approval":
            self.ask = {
                "id": str(data.get("id") or ""),
                "tool": str(data.get("tool") or ""),
                "args": data.get("args") if isinstance(data.get("args"), dict) else {},
            }
            for key in ("count", "examples", "cost_note", "once_only", "once_reason", "can_always",
                        "reversible", "local_only"):
                if key in data:
                    self.ask[key] = data[key]
            self.state = "waiting"
            return True
        if event == "approval_resolved":
            if self.ask is not None and str(data.get("id") or "") == self.ask.get("id"):
                self.ask = None
                self.state = "running"
                return True
            return False
        if event == "model_call":
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            self._add_usage(usage)
            if data.get("provider"):
                self.served_provider = str(data["provider"])[:120]
            if data.get("model"):
                self.served_model = str(data["model"])[:200]
            return self._alive()
        if event == "meta":
            if data.get("provider"):
                self.served_provider = str(data["provider"])[:120]
            if data.get("model"):
                self.served_model = str(data["model"])[:200]
            return False
        if event in ("token", "thinking", "round"):
            return self._alive()
        if event == "done":
            self.done = True
            self.reply = _short(data.get("reply"), REPLY_CHARS)
            if isinstance(data.get("cap"), dict):
                self.cap_met = dict(data["cap"])
            from .finish import saved_record

            self.finish = saved_record(data.get("finish"))
            route = data.get("route") if isinstance(data.get("route"), dict) else {}
            if route.get("provider"):
                self.served_provider = str(route["provider"])[:120]
            if route.get("model"):
                self.served_model = str(route["model"])[:200]
            return True
        if event == "error":
            self.errored = True
            title = str(data.get("title") or "").strip()
            fix = str(data.get("fix") or "").strip()
            if title and fix and title[-1] not in ".!?":
                title += "."
            words = " ".join(p for p in (title, fix) if p) or str(data.get("detail") or "The attempt failed.")
            self.error = _short(words, ERROR_CHARS)
            return True
        return False

    def _alive(self) -> bool:
        now = time.monotonic()
        if now - self.last_said >= ALIVE_EVERY_S:
            return True
        self.quiet_since_said = True
        return False

    def _add_usage(self, usage: dict[str, Any]) -> None:
        for key in ("input_tokens", "output_tokens"):
            try:
                self.usage[key] = int(self.usage.get(key) or 0) + int(usage.get(key) or 0)
            except (TypeError, ValueError):
                pass
        if isinstance(usage.get("cost_usd"), (int, float)) and not isinstance(usage.get("cost_usd"), bool):
            self.usage["cost_usd"] = round(float(self.usage.get("cost_usd") or 0.0) + float(usage["cost_usd"]), 9)
        if usage.get("list_price_equivalent") is True:
            self.usage["list_price_equivalent"] = True

    def settle(self) -> None:
        """The attempt's turn is over: how it ended, and why, in words."""
        self.ask = None
        self.ended = _now_ms()
        if self.t0:
            self.ms = max(0, int((time.monotonic() - self.t0) * 1000))
        limit = getattr(self.pin, "limit", None) if self.pin is not None else None
        if self.cancelled and not self.done:
            self.state, self.why, self.words = "stopped", "stopped", WHY_WORDS["stopped"]
        elif self.done and self.cap_met:
            from .reply_cap import limit_words

            self.state, self.why = "stopped", "stopped"
            kind = "cost" if self.cap_met.get("kind") == "cost" else "time"
            self.words = f"Stopped at this session's {kind} limit ({limit_words(self.cap_met)})"
        elif self.done:
            self.state, self.why, self.words = "done", "finished", WHY_WORDS["finished"]
        elif isinstance(limit, dict) and limit:
            from ..daemon.routes.build import limit_words

            said = _attempt_limit_words(limit_words(limit))
            self.state, self.why = "limit", "limit"
            self.words = f"{WHY_WORDS['limit']}: {said}" if said else WHY_WORDS["limit"]
        else:
            self.state, self.why = "failed", "failed"
            self.words = f"{WHY_WORDS['failed']}: {self.error}" if self.error else WHY_WORDS["failed"]


#: The SESSION's way out in an account's own limit sentence
#: (``accounts.pinned_words``) — an attempt is not the session's turn, so its
#: card says only what happened to the account.
_SESSION_CHOICE_RX = re.compile(
    r"(?:,\s*or|\s+—)\s+choose another account for this session\.?$|\s*Wait for it to reset,?\s*$",
)


def _attempt_limit_words(said: str) -> str:
    """The account's own limit sentence for an attempt's card: "The Claude
    account "Work Max" is at its request limit until 1:31 PM." — without the
    session's "or choose another account for this session". Never raises."""
    out = str(said or "").strip()
    out = _SESSION_CHOICE_RX.sub("", out).strip()
    out = re.sub(r"\.\s*Wait for it to reset$", ".", out).strip()
    if out and out[-1] not in ".!?":
        out += "."
    return out


#: The member-only way out in the model team's group refusal: a model from the
#: menu has no "own account" to set.
_MEMBER_ACCOUNT_TAIL = ", or set the member's own account in Settings › Model team"


# --------------------------------------------------------------------------- #
# the plan: everything decided before anything starts
# --------------------------------------------------------------------------- #


@dataclass
class Plan:
    thread_id: str
    turn_id: str
    task: str
    history: list[Any]
    summary: str
    setup: dict[str, Any]
    project_id: str
    start: StartPoint
    attempts: list[Attempt]
    command_rule: Any = None
    ceiling: "frozenset[str] | None" = None
    rounds: "int | None" = None
    local_only: bool = False
    #: When the attempts turn began (epoch seconds) — the session's time cap
    #: counts from here.
    started_at: float = field(default_factory=time.time)

    def by_id(self, aid: str) -> "Attempt | None":
        return next((a for a in self.attempts if a.id == aid), None)


def attempt_sid(thread_id: str, aid: str) -> str:
    """An attempt's own tool session: ``build:<thread>~try<n>``."""
    from .sessions import session_id_for

    return f"{session_id_for(thread_id)}{SID_SEP}{aid}"


def thread_of_attempt(session_id: Any) -> str:
    """The Build session an attempt's tool session belongs to ("" else)."""
    from .sessions import thread_of

    thread = thread_of(str(session_id or ""))
    return thread.split(SID_SEP, 1)[0] if SID_SEP in thread else ""


def ceiling_for(platform: Any) -> "frozenset[str] | None":
    """What an attempt may arm: every registered tool but
    :data:`EXCLUDED_TOOLS`. An EMPTY ceiling (nothing armed) when the
    registry cannot be read — never a wider set."""
    try:
        names = set(platform.registry.names())
    except Exception:  # noqa: BLE001
        return frozenset()
    return frozenset(n for n in names if n not in EXCLUDED_TOOLS)


# --------------------------------------------------------------------------- #
# the live registry (Stop on archive, delete and shutdown)
# --------------------------------------------------------------------------- #

_LIVE: dict[str, set[str]] = {}
_LIVE_LOCK = threading.Lock()


def running_for(thread_id: str) -> list[str]:
    """The attempts turns running for a session (their turn ids)."""
    with _LIVE_LOCK:
        return sorted(_LIVE.get(str(thread_id or ""), set()))


def _note_live(thread_id: str, turn_id: str, on: bool) -> None:
    with _LIVE_LOCK:
        live = _LIVE.setdefault(thread_id, set())
        if on:
            live.add(turn_id)
        else:
            live.discard(turn_id)
            if not live:
                _LIVE.pop(thread_id, None)


def _end_hook(session_id: "str | None") -> list[int]:
    """``procs.end_session`` / ``end_all``: a session archived, deleted or
    shut down leaves no attempt running (its turn's Stop ends them all, each
    attempt's own programs included)."""
    from ..core.turns import TURNS
    from .sessions import thread_of

    with _LIVE_LOCK:
        if session_id is None:
            turns = [t for live in _LIVE.values() for t in live]
        else:
            thread = thread_of(str(session_id or ""))
            turns = list(_LIVE.get(thread, set())) if thread and SID_SEP not in thread else []
    for turn in turns:
        with contextlib.suppress(Exception):
            TURNS.stop(turn)
    return []


def _register_end_hook() -> None:
    try:
        from ..core import procs

        procs.add_end_hook(_end_hook)
    except Exception:  # noqa: BLE001
        pass


_register_end_hook()


# --------------------------------------------------------------------------- #
# the driver: every attempt at once, their rows, the card
# --------------------------------------------------------------------------- #


def _sse(event: str, data: Any) -> str:
    from ..daemon.durable_turns import sse

    return sse(event, data)


def _arm(plan: Plan, a: Attempt) -> contextvars.Context:
    """The context ONE attempt's task is created in: its own account pin
    (None too — it never inherits another's), its model HELD (no fallback,
    no failover), its own branch, a Build session's failure words, the
    session's command rule and standing summary."""
    from ..iron_proxy.pin import set_pin
    from ..providers import failure_words as _fw
    from ..providers.hold import set_hold
    from . import branches as B
    from . import command_shell as _cmd
    from . import compact as _compact
    from . import team as _team

    ctx = contextvars.copy_context()

    def arm() -> None:
        set_pin(a.pin)
        set_hold(a.target.provider, a.target.model)
        B.set_turn_branch(a.git, a.folder)
        _fw.set_build_surface()
        _cmd.set_turn_rule(plan.command_rule)
        _compact.set_summary(plan.summary)
        # v1.403.0: each attempt does the WHOLE task alone — never as the
        # session's team lead (Try on several models stays separate).
        _team.set_team(None)

    ctx.run(arm)
    return ctx


def _body(plan: Plan, a: Attempt) -> Any:
    from ..daemon.schemas import ChatBody, ChatMessageBody

    setup = plan.setup
    same = a.target.provider == str(setup.get("provider") or "")
    return ChatBody(
        messages=[*plan.history, ChatMessageBody(role="user", content=plan.task)],
        provider=a.target.provider,
        model=a.target.model,
        # The session's reasoning level belongs to its own provider.
        reasoning=str(setup.get("reasoning") or "") if same else "",
        approval_mode=str(setup.get("approval_mode") or ""),
        granted_tools=list(setup.get("granted_tools") or []),
        workspace_dir=a.folder,
        project_id=plan.project_id,
        thread_id=plan.thread_id,
        turn_id=a.turn_id,
        mcp_cards=False,
        local_only=plan.local_only,
    )


class SharedCap:
    """THE SESSION'S CAP for one attempt (review, v1.397.0): the optional
    per-session cap (``harness/reply_cap``, decision 8 — off unless the user
    set one) bounds the attempts TOGETHER, as one reply: its time since the
    attempts turn began, and its list-price cost — this attempt's own so far
    plus what the OTHER attempts have spent (their ``model_call`` frames).
    The attempts ignored the cap entirely before: three attempts of a capped
    session ran unbounded."""

    def __init__(self, plan: "Plan", me: "Attempt", minutes: "int | None", usd: "float | None") -> None:
        self.plan = plan
        self.me = me
        self.minutes = minutes
        self.usd = usd

    def met(self, cost_so_far: float) -> "dict[str, Any] | None":
        from .reply_cap import ReplyCap

        others = 0.0
        for a in self.plan.attempts:
            if a is self.me:
                continue
            with contextlib.suppress(TypeError, ValueError):
                others += max(0.0, float(a.usage.get("cost_usd") or 0.0))
        cap = ReplyCap(minutes=self.minutes, usd=self.usd, started_at=self.plan.started_at, spent_usd=others)
        return cap.met(cost_so_far)


def attempt_cap(plan: "Plan", a: "Attempt") -> "SharedCap | None":
    """The session's cap for one attempt (None = the session has none)."""
    from .reply_cap import SETUP_KEY, clean

    rec = clean(plan.setup.get(SETUP_KEY)) if isinstance(plan.setup, dict) else None
    if rec is None:
        return None
    return SharedCap(plan, a, rec.get("minutes"), rec.get("usd"))


async def _end_programs(sid: str) -> None:
    """An attempt is over: its programs end (``procs.end_session`` — its
    process tools' end hook too) and its agent browser closes. Off the loop;
    never raises."""
    from ..core import procs

    with contextlib.suppress(Exception):
        await asyncio.to_thread(procs.end_session, sid)
    with contextlib.suppress(Exception):
        from . import page_browser as _pb

        if _pb.BROWSERS.is_open(sid):
            await asyncio.to_thread(_pb.close_for_stop, sid)


async def _run_one(platform: Any, personas: dict, plan: Plan, a: Attempt, queue: "asyncio.Queue",
                   stopped: Any) -> None:
    """ONE attempt's turn (a harness turn of the stream lane, in its own
    worktree, under its own tool session); every frame goes to ``queue``."""
    from ..daemon.chat_stream import stream_chat_turn
    from ..daemon.durable_turns import parse_chunk

    a.t0 = time.monotonic()
    try:
        try:
            gen = await stream_chat_turn(
                platform, personas, _body(plan, a),
                should_stop=stopped,
                suggest_preferences=False,
                harness="auto",
                round_budget=plan.rounds,
                stay_in_chat=True,
                session_id=a.sid,
                tool_ceiling=plan.ceiling,
                **({"reply_cap": cap} if (cap := attempt_cap(plan, a)) is not None else {}),
            )
        except Exception as exc:  # noqa: BLE001 — the lane's eager refusal is this attempt's failure
            queue.put_nowait(("frame", a.id, "error", json.dumps({"detail": str(getattr(exc, "detail", "") or exc)})))
            return
        async with contextlib.aclosing(gen) as frames:
            async for chunk in frames:
                for event, data in parse_chunk(chunk):
                    queue.put_nowait(("frame", a.id, event, data))
    except asyncio.CancelledError:
        a.cancelled = True
        raise
    except Exception as exc:  # noqa: BLE001 — said on the row
        log.warning("an attempt (%s) broke outside its stream", a.id, exc_info=True)
        queue.put_nowait(("frame", a.id, "error", json.dumps({"detail": f"The attempt stopped on an internal error ({type(exc).__name__})."})))
    finally:
        with contextlib.suppress(BaseException):
            await asyncio.shield(_end_programs(a.sid))
        queue.put_nowait(("end", a.id, "", ""))


async def _keep_spend(platform: Any, plan: Plan) -> None:
    """Write each ended attempt's METERED spend once (``harness/attempt_spend``)
    — the model team's pay-per-use limit counts it. Off the loop; never
    raises."""
    from . import attempt_spend

    engine = getattr(platform, "engine", None)
    for a in plan.attempts:
        if a.spend_kept:
            continue
        a.spend_kept = True
        cost = a.metered_usd()
        if cost <= 0:
            continue
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                attempt_spend.record, engine, thread_id=plan.thread_id, turn_id=plan.turn_id, attempt=a.id,
                provider=a.served_provider or a.target.provider, model=a.served_model or a.target.model,
                cost_usd=cost,
            )


def summary_words(card: dict[str, Any]) -> str:
    """The reply line's words for the card (the next turn's model reads them
    in its history; the screen shows the card above them)."""
    items = card.get("items") or []
    lines = [f"Tried this on {len(items)} model{'s' if len(items) != 1 else ''}, each on its own branch:"]
    for it in items:
        bits = [str(it.get("words") or WHY_WORDS.get(str(it.get("why") or ""), "Ended"))]
        tests = str(it.get("tests") or "")
        if tests in ("passed", "failed"):
            bits.append(f"tests {tests}")
        files = it.get("files") or []
        if files:
            bits.append(f"{len(files)} file{'s' if len(files) != 1 else ''} changed")
        lines.append(f"- {it.get('label')}: " + "; ".join(bits) + ".")
    if any(str(it.get("why")) == "finished" for it in items):
        lines.append(
            "Press Keep on the one you want: only its work joins this session's branch, and the other "
            "attempts' folders go to the trash with their branches kept."
        )
    return "\n".join(lines)


def card_head(plan: Plan) -> dict[str, Any]:
    """The card's OWN facts (not one attempt's): the work, where the attempts
    started, whether Keep moves the session (``adopt``) or lands the result
    in its folder (``carried``). Review (v1.397.0): the first ``attempt``
    frame of each attempt carries them too (``card``), so a card rebuilt from
    the frames after a Stop or a crash can still be kept the right way — it
    had none, read as "merge into the session's branch", and a session with
    no branch of its own was told its branch was gone."""
    out: dict[str, Any] = {
        "task": _short(plan.task, TASK_CHARS),
        "base": plan.start.base,
        "base_commit": plan.start.sha[:40],
        "adopt": plan.start.adopt,
    }
    if plan.start.carried:
        # Review: they started from the session's work not committed yet.
        out["carried"] = True
    return out


def card_of(plan: Plan) -> dict[str, Any]:
    """The card as the done frame and the reply line carry it."""
    return {**card_head(plan), "items": [_saved_item(a.frame()) for a in plan.attempts]}


async def run_attempts(platform: Any, personas: dict, plan: Plan) -> AsyncIterator[str]:
    """THE ATTEMPTS TURN: every attempt at once, each in its own task (its
    context armed by :func:`_arm`); their rows as ``attempt`` frames; Stop on
    this turn (``POST /chat/turns/{id}/stop``, archive, delete, shutdown)
    ends every attempt — the cancel reaches each tool call (a command's
    process tree is killed) and each attempt's programs end; then ONE ``done``
    frame carrying the card. The caller wraps it in ``routes/build.
    recorded_turn``, which writes the reply line (``attempts``)."""
    from ..core.turns import TURNS

    handle = TURNS.register(plan.turn_id)
    # An attempts turn takes no notes while it works: each attempt is its
    # model's own try at the SAME words, and a note one of them read would
    # make the side-by-side unfair. Closed at once, so the steer route says
    # 404 and the page keeps the words in the box.
    handle.close_steers()
    _note_live(plan.thread_id, plan.turn_id, True)
    queue: asyncio.Queue = asyncio.Queue()
    tasks: dict[str, asyncio.Task] = {}
    loop = asyncio.get_running_loop()

    def stopped() -> bool:
        return handle.stopped

    async def stop_all() -> None:
        for a in plan.attempts:
            with contextlib.suppress(Exception):
                TURNS.stop(a.turn_id)
        for t in tasks.values():
            if not t.done():
                t.cancel()
        live = [t for t in tasks.values() if not t.done()]
        if live:
            with contextlib.suppress(BaseException):
                await asyncio.wait(live, timeout=STOP_WAIT_S)
        for a in plan.attempts:
            with contextlib.suppress(BaseException):
                await asyncio.shield(_end_programs(a.sid))

    try:
        head = card_head(plan)
        for a in plan.attempts:
            a.last_said = time.monotonic()
            yield _sse(EVENT, {**a.frame(), "card": head})
        for a in plan.attempts:
            tasks[a.id] = loop.create_task(
                _run_one(platform, personas, plan, a, queue, stopped), context=_arm(plan, a),
            )
        open_ids = set(tasks)
        stop_wait = asyncio.ensure_future(handle.wait_stopped())
        try:
            while open_ids:
                getter = asyncio.ensure_future(queue.get())
                done, _pending = await asyncio.wait({getter, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
                if getter not in done:
                    getter.cancel()
                    with contextlib.suppress(BaseException):
                        await getter
                    await stop_all()
                    # Every attempt is over now: what each one said last.
                    while not queue.empty():
                        kind, aid, event, data = queue.get_nowait()
                        a = plan.by_id(aid)
                        if a is None:
                            continue
                        if kind == "frame":
                            a.fold(event, data)
                        else:
                            open_ids.discard(aid)
                    for a in plan.attempts:
                        if a.id in open_ids:
                            a.cancelled = True
                            open_ids.discard(a.id)
                        if a.ended is None:
                            a.settle()
                    break
                kind, aid, event, data = getter.result()
                a = plan.by_id(aid)
                if a is None:
                    continue
                if kind == "end":
                    open_ids.discard(aid)
                    a.settle()
                    a.files = await asyncio.to_thread(changed_files, a.git, plan.start.sha)
                    a.last_said = time.monotonic()
                    yield _sse(EVENT, a.frame())
                    continue
                if a.fold(event, data):
                    a.last_said = time.monotonic()
                    yield _sse(EVENT, a.frame())
        finally:
            stop_wait.cancel()
        for a in plan.attempts:
            if not a.files:
                a.files = await asyncio.to_thread(changed_files, a.git, plan.start.sha)
        # Review: what each attempt spent counts toward the pay-per-use limit.
        await _keep_spend(platform, plan)
        card = card_of(plan)
        yield _sse("done", {"reply": summary_words(card), LINE_KEY: card})
    finally:
        # Cut off (a shutdown cancels this turn) or closed early: nothing an
        # attempt started may outlive it.
        if any(not t.done() for t in tasks.values()):
            with contextlib.suppress(BaseException):
                await asyncio.shield(stop_all())
        # Review: money spent before a cut-off still counts — the calls the
        # driver had not read yet are folded first.
        with contextlib.suppress(Exception):
            while not queue.empty():
                kind, aid, event, data = queue.get_nowait()
                left = plan.by_id(aid)
                if left is not None and kind == "frame":
                    left.fold(event, data)
        with contextlib.suppress(BaseException):
            await asyncio.shield(_keep_spend(platform, plan))
        TURNS.release(plan.turn_id, handle)
        _note_live(plan.thread_id, plan.turn_id, False)


# --------------------------------------------------------------------------- #
# the card on the reply line
# --------------------------------------------------------------------------- #

_ITEM_STRINGS = {
    "id": 20, "label": LABEL_CHARS, "provider": 120, "model": 200, "member": 80, "cost": 10,
    "branch": 200, "state": 20, "why": 20, "words": 600, "tests": 20, "reply": REPLY_CHARS,
    "error": ERROR_CHARS, "account": 120, "account_name": 80, "folder": 1000,
    "served_provider": 120, "served_model": 200, "trash": 1000, "note": 400,
}


def _saved_item(raw: Any) -> "dict[str, Any] | None":
    """One attempt as the line keeps it: the known keys with the right types
    (its live ``ask`` never — a saved card waits on nobody)."""
    if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
        return None
    from . import branches as B
    from .finish import saved_record

    out: dict[str, Any] = {}
    for key, cap in _ITEM_STRINGS.items():
        v = raw.get(key)
        if isinstance(v, str) and v:
            out[key] = v[:cap]
    for key in ("steps", "since", "ended", "ms"):
        v = raw.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
            out[key] = v
    files = raw.get("files")
    if isinstance(files, list):
        out["files"] = [str(f)[:500] for f in files if isinstance(f, str) and f][:MAX_FILES]
    usage = raw.get("usage")
    if isinstance(usage, dict):
        u: dict[str, Any] = {}
        for key in ("input_tokens", "output_tokens"):
            v = usage.get(key)
            u[key] = int(v) if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0
        cost = usage.get("cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
            u["cost_usd"] = round(float(cost), 9)
        if usage.get("list_price_equivalent") is True:
            u["list_price_equivalent"] = True
        out["usage"] = u
    fin = saved_record(raw.get("finish"))
    if fin is not None:
        out["finish"] = fin
        out["tests"] = fin["tests"]
    git = B.clean_git_record(raw.get("git"))
    if git is not None:
        out["git"] = git
    for key in ("trashed", "kept"):
        if raw.get(key) is True:
            out[key] = True
    if out.get("state") not in STATES:
        out["state"] = "stopped"
    return out


def saved_card(raw: Any, *, cut_off: bool = False) -> "dict[str, Any] | None":
    """The card as the reply line keeps it (``attempts``), or None. An attempt
    still running when the line is written (a crash's rebuild: ``cut_off``)
    reads as cut off, never as running."""
    if not isinstance(raw, dict):
        return None
    items = [i for i in (_saved_item(x) for x in (raw.get("items") or [])[:MAX_ATTEMPTS]) if i]
    if not items:
        return None
    for it in items:
        if it.get("state") not in FINAL_STATES:
            it["state"] = "stopped"
            it["why"] = "cut_off" if cut_off else "stopped"
            it["words"] = WHY_WORDS[it["why"]]
    out: dict[str, Any] = {"items": items}
    for key, cap in (("task", TASK_CHARS), ("base", 200), ("base_commit", 40), ("kept", 20),
                     ("kept_at", 40), ("merged_into", 200), ("merge_commit", 40), ("keep_note", 600)):
        v = raw.get(key)
        if isinstance(v, str) and v:
            out[key] = v[:cap]
    out["adopt"] = raw.get("adopt") is True
    for key in ("carried", "landed"):
        if raw.get(key) is True:
            out[key] = True
    for key, cap in (("from_session", 80), ("discarded_at", 40)):
        v = raw.get(key)
        if isinstance(v, str) and v:
            out[key] = v[:cap]
    return out


def card_from_frames(frames: dict[str, dict[str, Any]], *, cut_off: bool) -> "dict[str, Any] | None":
    """The card from the ``attempt`` frames a turn sent (it never reached its
    ``done``: Stop before the card, or a crash) — what each row last said,
    and the card's own facts its first frames carried (``card``)."""
    items = list(frames.values())
    if not items:
        return None
    head = next((i["card"] for i in items if isinstance(i.get("card"), dict)), {})
    return saved_card({**head, "items": items}, cut_off=cut_off)


# --------------------------------------------------------------------------- #
# Keep
# --------------------------------------------------------------------------- #


def _keep_message(card: dict[str, Any], item: dict[str, Any]) -> str:
    title = _short(str(card.get("task") or "Work from an attempt").split("\n", 1)[0], 72) or "Work from an attempt"
    pair = "/".join(p for p in (item.get("provider"), item.get("model")) if p)
    return (
        f"{title}\n\nKept from the attempt on {item.get('label')} ({pair}), one of "
        f"{len(card.get('items') or [])} tried in Iron Jarvis."
    )


def keep(platform: Any, thread_id: str, turn_id: str, attempt_id: str) -> dict[str, Any]:
    """KEEP ONE ATTEMPT (a person's press). BLOCKING (git); the route runs it
    off the loop under the session's git lock, refused while the session runs.

    1. The attempt's work is committed on ITS branch (``branches.commit``,
       the Commit button's own call) when anything is not committed yet.
    2. A session on its own branch: that branch is MERGED into the session's
       branch (``branches.merge`` — a conflict, a session worktree with
       changes not committed, nothing to merge: refused in words, NOTHING
       moved). A session that worked in its folder directly MOVES onto the
       kept branch (``sessions.adopt_branch``); the main checkout is never
       touched either way.
    3. Only then the attempts' folders move to the trash (the kept one's too
       once it is merged — its work is on the session's branch now); every
       branch stays. A folder in use is left where it is and said.
    4. The card on the reply line says what was kept."""
    from . import branches as B
    from . import sessions as S

    row = S.get_row(platform.engine, thread_id)
    if row is None:
        raise AttemptsRefused("no such Build session", 404)
    if getattr(row, "archived_at", None) is not None:
        raise AttemptsRefused("This session is archived. Bring it back before you keep an attempt.", 409)
    line = next(
        (m for m in reversed(S._messages(row))  # noqa: SLF001
         if m.get("role") == "assistant" and m.get("turnId") == turn_id),
        None,
    )
    card = saved_card(line.get(LINE_KEY)) if isinstance(line, dict) else None
    if card is None:
        raise AttemptsRefused(KEEP_NONE, 404)
    item = next((i for i in card["items"] if i.get("id") == attempt_id), None)
    if item is None:
        raise AttemptsRefused(KEEP_UNKNOWN, 404)
    if card.get("from_session"):
        raise AttemptsRefused(KEEP_COPIED, 409)
    if card.get("kept"):
        kept = next((i for i in card["items"] if i.get("id") == card["kept"]), {})
        raise AttemptsRefused(f"You already kept the attempt on {kept.get('label') or card['kept']}.", 409)
    if any(i.get("state") not in FINAL_STATES for i in card["items"]):
        raise AttemptsRefused(KEEP_RUNNING, 409)
    facts = B.clean_git_record(item.get("git"))
    if facts is None:
        raise AttemptsRefused(KEEP_NO_BRANCH, 409)
    wt = Path(facts["worktree"])
    if not wt.is_dir() or not B.looks_like_worktree(facts):
        raise AttemptsRefused(KEEP_GONE, 409)
    label = str(item.get("label") or attempt_id)
    adopt = card.get("adopt") is True
    session_facts = S.git_of(row)
    if adopt and session_facts is not None:
        raise AttemptsRefused(KEEP_MOVED, 409)
    if not adopt and (session_facts is None or session_facts.get("removed_at")):
        raise AttemptsRefused(KEEP_SESSION_GONE, 409)
    if not adopt and session_facts is not None and session_facts.get("branch") != facts.get("base"):
        # A session BRANCHED from this one carries the card in its transcript.
        raise AttemptsRefused(KEEP_OTHER_BRANCH.format(
            base=facts.get("base") or "another branch", branch=session_facts.get("branch") or "its own branch",
        ), 409)
    out: dict[str, Any] = {"kept": attempt_id}
    before = B._ref_sha(wt, "HEAD")  # noqa: SLF001
    made = ""
    carried = card.get("carried") is True
    try:
        if B._porcelain(wt):  # noqa: SLF001
            got = B.commit(facts, _keep_message(card, item))
            made = B._ref_sha(wt, "HEAD")  # noqa: SLF001
            out["commit"] = str(got.get("sha", ""))
        if carried and not adopt:
            # Review (v1.397.0): the attempts started from the session's work
            # NOT COMMITTED yet. Its branch cannot take a merge over that
            # work, and committing the session's own work is the user's call:
            # the kept result goes into the session's folder as changes not
            # committed yet — only while the folder still holds exactly what
            # the attempts started from (nothing of later work is lost).
            start_tree = _tree_of(wt, str(card.get("base_commit") or ""))
            kept_tree = _tree_of(wt, "HEAD")
            if not start_tree or not kept_tree:
                raise AttemptsRefused(KEEP_NO_BRANCH, 409)
            if start_tree == kept_tree:
                raise AttemptsRefused(f"Nothing to keep: the attempt on {label} made no changes.", 409)
            session_wt = Path(str((session_facts or {}).get("worktree") or ""))
            if not session_wt.is_dir():
                raise AttemptsRefused(KEEP_SESSION_GONE, 409)
            land(session_wt, start_tree, kept_tree)
            out["landed"] = True
        elif adopt:
            was = S.folder_of(row)
            if not S.adopt_branch(platform.engine, thread_id, facts, str(item.get("folder") or facts["worktree"]),
                                  was_folder=was):
                raise AttemptsRefused(KEEP_MOVED, 409)
            out["adopted"] = facts.get("branch", "")
        else:
            # Review (v1.397.0): the attempts started from a CLEAN session,
            # and the session has changes not committed since — a merge over
            # them is refused; said in the session's words (branches.merge
            # names "your checkout of <branch>", the session's own worktree).
            session_wt = Path(str((session_facts or {}).get("worktree") or ""))
            if session_wt.is_dir() and B._porcelain(session_wt, untracked=False):  # noqa: SLF001
                raise AttemptsRefused(KEEP_SESSION_DIRTY, 409)
            try:
                merged = B.merge(facts)
            except B.GitRefused as exc:
                if str(exc) == B.NOTHING_TO_MERGE.format(base=facts.get("base", "")):
                    raise AttemptsRefused(
                        f"Nothing to keep: the attempt on {label} made no changes.", 409,
                    ) from None
                raise
            out["merged_into"] = merged.get("into", "")
            out["merge_commit"] = merged.get("commit", "")
    except (B.GitRefused, AttemptsRefused) as exc:
        # NOTHING MOVES on a refusal: the commit Iron Jarvis made a moment ago
        # on the attempt's OWN branch is taken back (its work is uncommitted
        # in its folder again, exactly as before the press) — only while the
        # branch still points at that commit.
        if made and before and B._ref_sha(wt, "HEAD") == made:  # noqa: SLF001
            with contextlib.suppress(Exception):
                B._g(["reset", "-q", "--mixed", before], wt)  # noqa: SLF001
        if isinstance(exc, AttemptsRefused):
            raise
        raise AttemptsRefused(str(exc), exc.status) from None
    # The folders to the trash — the kept one's too once its work is merged
    # (a session that MOVED onto the kept branch works in that folder now).
    home = getattr(getattr(platform, "config", None), "home", None)
    notes: list[str] = []
    for it in card["items"]:
        if it.get("id") == attempt_id and adopt:
            continue
        f = B.clean_git_record(it.get("git"))
        if f is None or not Path(f["worktree"]).exists():
            continue
        try:
            moved = B.remove_worktree(home, f)
            it["trashed"] = True
            if moved.get("trash"):
                it["trash"] = str(moved["trash"])
        except B.GitRefused as exc:
            notes.append(f"The folder of the attempt on {it.get('label')} stayed at {f['worktree']}: {exc}")
    for it in card["items"]:
        if it.get("id") == attempt_id:
            it["kept"] = True
    card["kept"] = attempt_id
    from ..core.ids import utcnow

    card["kept_at"] = utcnow().isoformat()
    if out.get("merged_into"):
        card["merged_into"] = out["merged_into"]
        card["merge_commit"] = str(out.get("merge_commit") or "")
    if out.get("landed"):
        card["landed"] = True
    if adopt and carried:
        # The folder's own changes not committed stay in the folder (it is
        # never touched); the session's branch has them too now.
        notes.append(
            f"The changes not committed in {S.folder_of(row) or 'your folder'} are still there as well; "
            "this session's branch has them too."
        )
    if notes:
        card["keep_note"] = " ".join(notes)[:600]
    if adopt:
        said = (f"Kept the attempt on {label}: this session now works on its branch "
                f"{facts.get('branch')}.")
    elif out.get("landed"):
        said = (f"Kept the attempt on {label}: its result is in this session's folder, not committed "
                "yet (the attempts started from this session's work that was not committed).")
    else:
        said = f"Kept the attempt on {label}: merged into {out.get('merged_into') or facts.get('base')}."
    content = str(line.get("content") or "").rstrip()
    saved = S.amend_reply(
        platform.engine, thread_id, turn_id,
        {LINE_KEY: card, "content": (content + "\n\n" + said).strip()},
        expect=lambda cur: not (isinstance(cur.get(LINE_KEY), dict) and cur[LINE_KEY].get("kept")),
    )
    out.update({"attempts": card, "words": said})
    if notes:
        out["note"] = " ".join(notes)
    if not saved:
        out["note"] = (out.get("note", "") + " " + KEEP_SAVE_FAILED).strip()
    return out


# --------------------------------------------------------------------------- #
# the attempts' folders: a branched copy, Discard all, a deleted session, a
# rewind (review, v1.397.0)
# --------------------------------------------------------------------------- #


def copied_lines(lines: list[dict[str, Any]], origin: str) -> list[dict[str, Any]]:
    """A session BRANCHED from ``origin`` gets its conversation — and the
    attempts cards in it point at ``origin``'s folders. Each copied card
    names where it came from (``from_session``) and keeps no attempt's git
    facts, so the copy can never Keep, discard or delete them (a branched
    session that works in its folder directly ADOPTED the origin's attempt
    branch: two sessions in one worktree). Pure."""
    out: list[dict[str, Any]] = []
    for m in lines or []:
        card = m.get(LINE_KEY) if isinstance(m, dict) else None
        if not isinstance(card, dict):
            out.append(m)
            continue
        copy = saved_card(card)
        if copy is None:
            out.append({k: v for k, v in m.items() if k != LINE_KEY})
            continue
        copy["from_session"] = copy.get("from_session") or str(origin or "")[:80]
        for it in copy["items"]:
            it.pop("git", None)
            it.pop("folder", None)
        out.append({**m, LINE_KEY: copy})
    return out


def _session_worktree(row: Any) -> str:
    from . import sessions as S

    facts = S.git_of(row)
    return str((facts or {}).get("worktree") or "")


def live_items(card: dict[str, Any], row: Any) -> list[dict[str, Any]]:
    """The items of ``card`` whose folder is still beside the repository —
    not in the trash, not the folder the session itself works in now (a
    session that ADOPTED a kept attempt), never a copied card's. BLOCKING
    (a stat per folder)."""
    from . import branches as B

    if card.get("from_session"):
        return []
    own = _norm_path(_session_worktree(row))
    out = []
    for it in card.get("items") or []:
        facts = B.clean_git_record(it.get("git"))
        if facts is None or it.get("trashed"):
            continue
        wt = Path(facts["worktree"])
        if own and _norm_path(str(wt)) == own:
            continue
        if wt.exists():
            out.append(it)
    return out


def _norm_path(path: str) -> str:
    import os

    try:
        return os.path.normcase(os.path.normpath(path)) if path else ""
    except (TypeError, ValueError):
        return str(path or "")


def _trash_items(home: Any, items: list[dict[str, Any]]) -> list[str]:
    """Move each item's folder to the trash (``branches.remove_worktree``:
    the branch stays); mark the moved ones; return a sentence per folder
    that stayed. BLOCKING."""
    from . import branches as B

    notes: list[str] = []
    for it in items:
        f = B.clean_git_record(it.get("git"))
        if f is None:
            continue
        try:
            moved = B.remove_worktree(home, f)
            it["trashed"] = True
            if moved.get("trash"):
                it["trash"] = str(moved["trash"])
        except B.GitRefused as exc:
            notes.append(f"The folder of the attempt on {it.get('label')} stayed at {f['worktree']}: {exc}")
    return notes


def discard(platform: Any, thread_id: str, turn_id: str) -> dict[str, Any]:
    """DISCARD ALL (a person's press): every attempt's folder of that message
    goes to the trash; every BRANCH stays (its commits kept until the user
    deletes it). The card says so. Refused for a copied card, and when
    nothing is left to discard. BLOCKING (git); the route runs it off the
    loop under the session's git lock, refused while the session runs."""
    from . import sessions as S

    row = S.get_row(platform.engine, thread_id)
    if row is None:
        raise AttemptsRefused("no such Build session", 404)
    line = next(
        (m for m in reversed(S._messages(row))  # noqa: SLF001
         if m.get("role") == "assistant" and m.get("turnId") == turn_id),
        None,
    )
    card = saved_card(line.get(LINE_KEY)) if isinstance(line, dict) else None
    if card is None:
        raise AttemptsRefused(KEEP_NONE, 404)
    if card.get("from_session"):
        raise AttemptsRefused(KEEP_COPIED, 409)
    items = live_items(card, row)
    if not items:
        raise AttemptsRefused(DISCARD_KEPT if card.get("kept") else DISCARD_NONE, 409)
    home = getattr(getattr(platform, "config", None), "home", None)
    notes = _trash_items(home, items)
    from ..core.ids import utcnow

    moved = [it for it in items if it.get("trashed")]
    if moved:
        card["discarded_at"] = utcnow().isoformat()
    if notes:
        card["keep_note"] = " ".join(notes)[:600]
    names = _names(str(it.get("label") or it.get("id")) for it in moved)
    said = (f"Discarded the attempts on {names}: their folders are in the trash, and their branches stay."
            if moved else "")
    content = str(line.get("content") or "").rstrip()
    saved = S.amend_reply(
        platform.engine, thread_id, turn_id,
        {LINE_KEY: card, **({"content": (content + "\n\n" + said).strip()} if said else {})},
    )
    out: dict[str, Any] = {"discarded": [it.get("id") for it in moved], "attempts": card, "words": said}
    if notes:
        out["note"] = " ".join(notes)
    if not saved:
        out["note"] = (out.get("note", "") + " The card could not be updated. Reload the session.").strip()
    return out


def discard_for_delete(platform: Any, row: Any) -> str:
    """DELETING a session takes its attempts' folders with it to the trash
    (their branches stay — the session's own worktree's rule, v1.375.0). ""
    when every one moved (or there were none); else the sentence naming the
    folder that stayed (the delete is then refused, nothing deleted; what
    moved is said on its card). BLOCKING (git)."""
    from . import sessions as S

    home = getattr(getattr(platform, "config", None), "home", None)
    notes: list[str] = []
    for m in S._messages(row):  # noqa: SLF001
        if m.get("role") != "assistant" or not isinstance(m.get(LINE_KEY), dict):
            continue
        card = saved_card(m.get(LINE_KEY))
        if card is None:
            continue
        items = live_items(card, row)
        if not items:
            continue
        left = _trash_items(home, items)
        notes.extend(left)
        if any(it.get("trashed") for it in items):
            S.amend_reply(platform.engine, row.id, str(m.get("turnId") or ""), {LINE_KEY: card})
    return " ".join(notes)


def rewind_facts(messages: list[dict[str, Any]], turns: list[str], row: Any) -> "tuple[list[dict[str, Any]], str]":
    """What a rewind over ``turns`` means for their attempts: a PROBLEM (the
    rewind is refused, nothing touched) for attempts whose folders are still
    beside the repository — the card that holds Keep and Discard all would be
    cut away and the folders left with nothing that knows them — and a NOTE
    for a kept attempt (its work stays in the session: a rewind does not take
    back what Keep brought in). BLOCKING (a stat per folder); never raises."""
    problems: list[dict[str, Any]] = []
    notes: list[str] = []
    try:
        cut = set(turns or [])
        for m in messages or []:
            if m.get("role") != "assistant" or m.get("turnId") not in cut:
                continue
            card = saved_card(m.get(LINE_KEY))
            if card is None or card.get("from_session"):
                continue
            live = live_items(card, row)
            if live:
                names = _names(str(it.get("label") or it.get("id")) for it in live)
                problems.append({"reason": (
                    f"the attempts on {names} are still in their folders: keep one or press Discard all on "
                    "their card first"
                )})
            if card.get("kept"):
                kept = next((i for i in card["items"] if i.get("id") == card["kept"]), {})
                notes.append(
                    f"The attempt you kept on {kept.get('label') or card['kept']} stays in this session: a "
                    "rewind does not take back what Keep brought in."
                )
    except Exception:  # noqa: BLE001 — the rewind's own checks still run
        log.debug("reading a rewind's attempts failed", exc_info=True)
    return problems, " ".join(notes)


__all__ = [
    "Attempt", "AttemptsRefused", "EVENT", "EXCLUDED_TOOLS", "FINAL_STATES", "LINE_KEY",
    "MAX_ATTEMPTS", "Menu", "NEEDS_GIT", "Plan", "Request", "StartPoint", "Target", "TOO_MANY",
    "attempt_sid", "card_from_frames", "card_of", "ceiling_for", "changed_files", "check_target", "keep",
    "make_worktrees", "parse_request", "resolve", "run_attempts", "running_for", "saved_card",
    "start_point", "summary_words", "thread_of_attempt",
]
