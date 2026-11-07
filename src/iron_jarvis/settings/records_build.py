"""The night shift and a folder's coding-session defaults and team from chat
(v1.438.0, chat-first plan Wave 4 group A, Part B2: "Queue 'update the
README' for tonight", "what's queued for tonight?", "cancel the README task",
"move it to the top of tonight's list"; "Use Codex for this repo", "Codex
writes, Claude reviews in D:\\code\\shop", "stop using the team for this
folder").

Both were page-only: the Tonight list (Coding sessions › Tonight) queued,
cancelled and ordered night tasks, and the start composers saved a folder's
defaults and team. These nine tools close that gap the way
``settings.records`` closes it for schedules and goals:

* ``night_task_add`` — THROUGH ``POST /build/night``'s own body
  (``daemon.routes.night.add_night_task``): the folder must be usable and in a
  repository that can branch, the models in the model menu, the account one
  of that vendor's. A night task runs UNATTENDED, so its card says when it
  runs (the night window from Settings) — every time, in every posture but
  "Don't ask" (``spend_check``: a wider answer never covers the next one).
  Undo takes it off the list only while it is still queued and unchanged.
* ``night_task_list`` — the Tonight list in words (read only).
* ``night_task_cancel`` — THROUGH ``POST /build/night/{id}/cancel``'s own body
  (``cancel_night_task``): a queued task never starts; a running one is
  stopped. Undo puts a CANCELLED QUEUED task back (in its place) only while it
  is still exactly as the cancel left it and the user did not queue the same
  words again; a stopped RUNNING task is never started by an Undo.
* ``night_task_move`` — a queued task to the top, up, down or the bottom
  (``harness.night.move_task``, the page's own move). Undo puts the order
  back only while the queue is still exactly in the order the move left.
* ``build_folder_show`` — a folder's defaults and team in words (read only).
* ``build_defaults_set`` / ``build_defaults_clear`` — a folder's defaults for
  NEW coding sessions (``harness.folder_defaults``, the start composers' own
  store and checks): "Don't ask" and Full access are never a folder default
  (refused in words, the store's own rule). An account is never kept as a
  folder default (the store has no such field) — said, never dropped quietly.
* ``build_team_set`` / ``build_team_clear`` — a folder's coding team
  (``harness.team.validate`` + ``set_folder_team``, the route's own rules); a
  model named for a seat goes through ``POST /team/members/from-model``'s own
  body (``daemon.routes.team.member_for_model``): an existing member on that
  model is reused, else one is made — said in the reply, and KEPT by an Undo
  (an Undo puts back the folder's team, never a member of the model team).

Every change asks first (ask tier, the ordinary card; ``night_task_add`` its
own every time) and has an Undo that puts back what THAT change did and
nothing else, refusing in one sentence over anything changed since — never
writing over the user's newer work.

THE FOLDER is the one the user names (a full path), else the folder of the
project this chat is in. With neither, the tool REFUSES and asks which folder
— it never guesses "this repo". A folder spelled as a network share is
refused by its TEXT before anything touches the disk (the v1.379.0 lesson).

Arming (:func:`build_tools_for`) reads the sentence the user typed: a request
about tonight's list arms the night family, a request about a folder's
defaults or coding team the folder family; a QUESTION keeps only the two
readers; an everyday sentence ("I'm working late tonight", "my team at
work", "default settings on my phone") arms nothing. Every pattern is LINEAR:
plain splits into clauses and ANCHORED matches with bounded repeats
(``tests/test_records_build_v14380.py`` pins pathological input in a child
process with a deadline).

Snapshots and restores are BLOCKING (SQLite); the tools run them through
``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
from typing import Any

from ..core.ids import new_id
from ..tools.base import Reversibility, SpendCheck, Tool, ToolContext, ToolResult
from . import records as R

log = logging.getLogger("ironjarvis.settings")

NIGHT_TOOLS: tuple[str, ...] = ("night_task_add", "night_task_list", "night_task_cancel", "night_task_move")
FOLDER_TOOLS: tuple[str, ...] = (
    "build_folder_show", "build_defaults_set", "build_defaults_clear", "build_team_set", "build_team_clear",
)
#: The two that only READ: a question still arms them.
READERS = frozenset({"night_task_list", "build_folder_show"})
#: Each tool's default permission (``core/config.py`` holds the same rows).
TIERS: dict[str, str] = {
    "night_task_add": "ask",
    "night_task_list": "allow",
    "night_task_cancel": "ask",
    "night_task_move": "ask",
    "build_folder_show": "allow",
    "build_defaults_set": "ask",
    "build_defaults_clear": "ask",
    "build_team_set": "ask",
    "build_team_clear": "ask",
}
#: The record kinds this module's Undo serves (``records.restore``).
KINDS: tuple[str, ...] = ("night_task", "night_order", "build_defaults", "build_team")

#: A tool needs the running app (the route bodies take its deps object).
NEEDS_THE_APP = "This can only be done while the Iron Jarvis app is running."
NETWORK_FOLDER = (
    "That folder is a network location on another computer; give a folder on this PC, starting "
    "with its drive (for example D:\\code\\shop)."
)
FULL_PATH = "Give the folder's full location, starting with its drive (for example D:\\code\\shop)."
WHICH_FOLDER = (
    "Say which folder (its full location, for example D:\\code\\shop): this chat is not in a project "
    "with a folder, so Iron Jarvis will not guess which one you mean."
)
NO_SUCH_FOLDER = "There is no folder {folder} on this PC."
ACCOUNT_NOT_KEPT = (
    " An account is not kept as a folder default: pick the account when a session starts "
    "(the model menu in the start box)."
)

#: The Undo descriptor THIS call captured (set by ``capture_undo``, read by
#: :func:`_note` after a successful write; a direct ``execute`` finds None).
_DESC: contextvars.ContextVar["dict[str, Any] | None"] = contextvars.ContextVar("ij_build_record_desc", default=None)


def _note(**fields: Any) -> None:
    """Add ``fields`` to the descriptor this call captured (the registry
    journals it only after a SUCCESSFUL execute). No-op with no capture."""
    desc = _DESC.get()
    _DESC.set(None)
    if not desc:
        return
    try:
        meta = json.loads(desc.get("pre_inline") or "{}")
    except (TypeError, ValueError):
        return
    if not isinstance(meta, dict):
        return
    meta.update(fields)
    desc["pre_inline"] = json.dumps(meta, default=str)


def _capture(record: str, name: str, prior: Any) -> dict[str, Any]:
    """A ``record_restore`` descriptor for ``record`` / ``name`` (its change id
    on the card), stashed for :func:`_note`."""
    change_id = new_id("rec")
    R._CHANGE_ID.set(change_id)  # noqa: SLF001 — the records module's own card id
    desc = {
        "kind": R.RECORD_RESTORE,
        "pre_inline": json.dumps({"record": record, "name": name, "prior": prior, "change_id": change_id}, default=str),
    }
    _DESC.set(desc)
    return desc


def _q(text: Any, cap: int = 80) -> str:
    words = " ".join(str(text or "").split())
    return "“" + (words if len(words) <= cap else words[: cap - 1] + "…") + "”"


def _d(platform: Any) -> Any:
    return R._d(platform)  # noqa: SLF001 — the records module's own deps lookup


# --------------------------------------------------------------------------- #
# the folder
# --------------------------------------------------------------------------- #


def _network(text: str) -> bool:
    from ..harness.sessions import _network_spelling

    return _network_spelling(text)


def _absolute(text: str) -> bool:
    if os.name == "nt":
        return re.match(r"^[A-Za-z]:[\\/]", text) is not None
    return text.startswith("/")


def _project_root(platform: Any, project_id: Any) -> str:
    if not project_id:
        return ""
    try:
        from ..core.db import session_scope
        from ..core.models import Project

        with session_scope(platform.engine) as db:
            row = db.get(Project, str(project_id))
            return str(getattr(row, "root", "") or "").strip() if row is not None else ""
    except Exception:  # noqa: BLE001 — no project: no folder
        return ""


def resolve_folder(platform: Any, ctx: Any, raw: Any) -> tuple[str, str]:
    """``(folder, "")`` — the folder the user named, else the folder of the
    project this chat is in — or ``("", one plain sentence)``. A network
    spelling is refused by its text before anything touches the disk; with no
    folder at all the answer asks which one (never a guess). Blocking (one
    project read)."""
    from ..harness.sessions import resolved_folder

    text = str(raw or "").strip().strip("\"'")
    if not text:
        text = _project_root(platform, getattr(ctx, "project_id", None))
        if not text:
            return "", WHICH_FOLDER
    if _network(text):
        return "", NETWORK_FOLDER
    if not _absolute(text):
        return "", FULL_PATH
    return resolved_folder(text), ""


def _existing_folder(platform: Any, ctx: Any, raw: Any) -> tuple[str, str]:
    """:func:`resolve_folder`, and the folder must exist on this PC. Blocking."""
    folder, problem = resolve_folder(platform, ctx, raw)
    if problem:
        return "", problem
    if not os.path.isdir(folder):
        return "", NO_SUCH_FOLDER.format(folder=folder)
    return folder, ""


# --------------------------------------------------------------------------- #
# night tasks — snapshots and the Undo
# --------------------------------------------------------------------------- #

#: The fields of a queued task its Undo compares (what the user may edit).
_TASK_FIELDS = ("words", "folder", "provider", "model", "team", "account", "try_on")


def _engine(platform: Any) -> Any:
    return platform.engine


def snap_night_task(platform: Any, task_id: str) -> "dict[str, Any] | None":
    from ..harness import night as N

    return N.get_task(_engine(platform), task_id)


def _queued_ids(platform: Any) -> list[str]:
    from ..harness import night as N

    return [t["id"] for t in N.rows_in(_engine(platform), N.QUEUED)]


def snap_night_order(platform: Any, _name: str) -> list[str]:
    return _queued_ids(platform)


def _put_back_queued(platform: Any, task_id: str, prior: dict[str, Any]) -> None:
    """A cancelled QUEUED task back on the list exactly as it was (its place,
    its notes). Blocking."""
    from ..core.db import session_scope
    from ..core.ids import utcnow
    from ..harness import night as N

    with session_scope(_engine(platform)) as db:
        row = db.get(N.NightTaskRecord, task_id)
        if row is None:
            return
        row.state = N.QUEUED
        row.ended_at = None
        row.notes_json = json.dumps(prior.get("notes") or []) if prior.get("notes") else ""
        row.position = int(prior.get("position") or 0)
        row.updated_at = utcnow()
        db.add(row)
        db.commit()


def _same_task(a: dict[str, Any], b: dict[str, Any]) -> bool:
    from ..harness.sessions import folder_key

    return folder_key(a.get("folder") or "") == folder_key(b.get("folder") or "") and (
        " ".join(str(a.get("words") or "").split()) == " ".join(str(b.get("words") or "").split())
    )


def restore_night_task(platform: Any, task_id: str, prior: "dict[str, Any] | None", desc: dict[str, Any]) -> str:
    """``POST /undo`` for a night task queued or cancelled from chat. Blocking.

    * A QUEUED one (``prior`` None) is taken off the list only while it is
      still queued and unchanged; one that started, ended or was edited since
      is left alone, said in words.
    * A CANCELLED one goes back on the list only when the cancel took it off
      the queue (never a running task the cancel stopped), it is still exactly
      as the cancel left it, and the same words were not queued again since."""
    from ..harness import night as N

    after = desc.get("after") if isinstance(desc.get("after"), dict) else None
    current = snap_night_task(platform, task_id)
    if prior is None:
        if current is None:
            return "undo: the night task was already gone"
        words = _q(current.get("words"))
        if current.get("state") == N.RUNNING:
            raise R.RecordError(
                f"The night task {words} has already started, so Undo cannot take it off the list; "
                "cancel it from the Tonight list instead."
            )
        if current.get("state") != N.QUEUED:
            raise R.RecordError(
                f"The night task {words} has already run, so Undo leaves it and what it did as they are."
            )
        if after is not None and any(current.get(k) != after.get(k) for k in _TASK_FIELDS):
            raise R.RecordError(
                f"The night task {words} was changed after it was queued, so Undo leaves it; remove it "
                "from the Tonight list instead."
            )
        N.remove_task(_engine(platform), task_id)
        return f"undo: took {words} off tonight's list"
    words = _q(prior.get("words"))
    if prior.get("state") != N.QUEUED:
        raise R.RecordError(
            f"The night task {words} was running when it was stopped; Undo cannot start it again. "
            "Queue it again for another night instead."
        )
    if current is None:
        raise R.RecordError(f"The night task {words} was removed after it was cancelled, so there is nothing to put back.")
    if after is not None and (
        current.get("state") != after.get("state") or current.get("updated_at") != after.get("updated_at")
    ):
        raise R.RecordError(
            f"The night task {words} was changed after it was cancelled, so Undo leaves it; queue it again "
            "from the Tonight list instead."
        )
    if current.get("state") == N.QUEUED:
        return f"undo: {words} was already back on tonight's list"
    if any(_same_task(t, prior) for t in N.rows_in(_engine(platform), N.QUEUED)):
        raise R.RecordError(f"You queued {words} again after it was cancelled, so Undo leaves the list as it is.")
    _put_back_queued(platform, task_id, prior)
    return f"undo: put {words} back on tonight's list"


def restore_night_order(platform: Any, _name: str, prior: Any, desc: dict[str, Any]) -> str:
    """``POST /undo`` for a move: the order comes back only while the queue is
    exactly in the order the move left. Blocking."""
    from ..core.db import session_scope
    from ..harness import night as N

    after = desc.get("after") if isinstance(desc.get("after"), list) else None
    current = _queued_ids(platform)
    if not isinstance(prior, list):
        raise R.RecordError("This change has no order to put back.")
    if current == prior:
        return "undo: tonight's order was already back as it was"
    if after is None or current != after:
        raise R.RecordError(
            "Tonight's list changed after this move (a task was added, started, moved or removed), so "
            "Undo leaves the order as it is; move the tasks on the Tonight list instead."
        )
    with session_scope(_engine(platform)) as db:
        for n, task_id in enumerate(prior, start=1):
            row = db.get(N.NightTaskRecord, task_id)
            if row is not None:
                row.position = n
                db.add(row)
        db.commit()
    return "undo: put tonight's order back"


# --------------------------------------------------------------------------- #
# a folder's defaults and team — snapshots and the Undo
# --------------------------------------------------------------------------- #

#: The tools' words for the two coding views (``harness.folder_defaults``).
VIEW_OF = {"chat_code": "code", "build_code": "tool"}
VIEW_WORDS = {"code": "Chat › Code", "tool": "Build › Code"}
_POSTURE_WORDS = {
    "always_ask": "Ask before every action",
    "approve_for_me": "Ask before commands",
    "accept_edits": "Accept edits",
    "auto": "Auto",
}
_MODE_WORDS = {"ask": "Ask first", "edits": "Auto edits", "plan": "Plan", "auto": "Auto", "own": "the tool's own rules"}


def snap_build_defaults(platform: Any, folder: str) -> dict[str, Any]:
    from ..harness import folder_defaults as FD

    return FD.read(_engine(platform), folder)


def snap_build_team(platform: Any, folder: str) -> "dict[str, Any] | None":
    from ..harness import team as HT

    return HT.folder_team(_engine(platform), folder)


def _tool_label(harness: str) -> str:
    try:
        from ..hosted.words import LABELS

        return LABELS.get(harness, harness)
    except Exception:  # noqa: BLE001
        return harness


def _view_words(view: str, value: "dict[str, Any] | None") -> str:
    if not value:
        return "none"
    if view == "code":
        bits = []
        if value.get("model") or value.get("provider"):
            bits.append(" ".join(x for x in (value.get("model"), f"({value['provider']})" if value.get("provider") else "") if x))
        if value.get("reasoning"):
            bits.append(f"reasoning {value['reasoning']}")
        if value.get("approval_mode"):
            bits.append(_POSTURE_WORDS.get(value["approval_mode"], value["approval_mode"]))
        return ", ".join(bits) or "none"
    bits = [_tool_label(str(value.get("harness") or ""))]
    if value.get("model"):
        bits.append(f"model {value['model']}")
    if value.get("effort"):
        bits.append(f"effort {value['effort']}")
    if value.get("mode"):
        bits.append(_MODE_WORDS.get(value["mode"], value["mode"]))
    return ", ".join(b for b in bits if b) or "none"


def _defaults_words(defaults: "dict[str, Any] | None", views: "tuple[str, ...]" = ("code", "tool")) -> str:
    d = defaults or {}
    parts = [f"{VIEW_WORDS[v]}: {_view_words(v, d.get(v))}" for v in views if d.get(v)]
    return "; ".join(parts) or "none"


def _team_words(team: "dict[str, Any] | None") -> str:
    if not team or not team.get("workers"):
        return "none"
    out = f"{', '.join(team['workers'])} {'writes' if len(team['workers']) == 1 else 'write'}"
    return out + (f", {team['reviewer']} reviews" if team.get("reviewer") else ", no reviewer")


def restore_build_defaults(platform: Any, folder: str, prior: Any, desc: dict[str, Any]) -> str:
    """``POST /undo`` for a folder's defaults changed from chat: each view the
    change touched goes back — only while it is still what the change left.
    Blocking."""
    from ..harness import folder_defaults as FD

    prior = prior if isinstance(prior, dict) else {}
    after = desc.get("after") if isinstance(desc.get("after"), dict) else {}
    views = [v for v in (desc.get("views") or []) if v in FD.VIEWS]
    current = snap_build_defaults(platform, folder)
    again = [v for v in views if current.get(v) != after.get(v) and current.get(v) != prior.get(v)]
    if again:
        raise R.RecordError(
            f"The defaults for new coding sessions in {folder} were changed again after this change "
            f"({', '.join(VIEW_WORDS[v] for v in again)}), so Undo leaves them; change them in the start box instead."
        )
    todo = [v for v in views if current.get(v) != prior.get(v)]
    if not todo:
        return f"undo: the defaults for {folder} were already back as they were"
    for v in todo:
        FD.save(_engine(platform), folder, v, prior.get(v))
    return f"undo: put back the defaults for new coding sessions in {folder}"


def restore_build_team(platform: Any, folder: str, prior: Any, desc: dict[str, Any]) -> str:
    """``POST /undo`` for a folder's team changed from chat: the team the
    folder had comes back only while it is still the one the change left. The
    model-team members the change made are KEPT (they are on Settings › Model
    team; remove one there). Blocking."""
    from ..harness import team as HT

    prior = prior if isinstance(prior, dict) else None
    after = desc.get("after") if isinstance(desc.get("after"), dict) else None
    current = snap_build_team(platform, folder)
    if current == prior:
        return f"undo: the coding team for {folder} was already back as it was"
    if current != after:
        raise R.RecordError(
            f"The coding team for {folder} was changed again after this change, so Undo leaves it; "
            "change it in the start box instead."
        )
    HT.set_folder_team(_engine(platform), folder, prior)
    made = [m for m in desc.get("made") or [] if isinstance(m, str)]
    kept = f" (the team members it made — {', '.join(made)} — stay on your model team)" if made else ""
    return f"undo: put back the coding team for {folder}{kept}"


SNAP = {
    "night_task": snap_night_task,
    "night_order": snap_night_order,
    "build_defaults": snap_build_defaults,
    "build_team": snap_build_team,
}
_RESTORE = {
    "night_task": restore_night_task,
    "night_order": restore_night_order,
    "build_defaults": restore_build_defaults,
    "build_team": restore_build_team,
}


def restore_kind(platform: Any, record: str, name: str, prior: Any, desc: dict[str, Any]) -> str:
    """The Undo of one of :data:`KINDS` (called by ``records.restore``)."""
    return _RESTORE[record](platform, name, prior, desc)


# --------------------------------------------------------------------------- #
# the tools
# --------------------------------------------------------------------------- #


class _BuildTool(Tool):
    """A REVERSIBLE tool over a night task or a folder's coding settings."""

    reversibility = Reversibility.REVERSIBLE
    RECORD = ""

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    def _card(self, title: str, label: str, old: Any, new: Any, key: str) -> dict[str, Any]:
        return {"title": title, "key": key, "label": label, "old": old, "new": new, "change_id": R._CHANGE_ID.get()}  # noqa: SLF001

    async def _begin(self) -> None:
        R._CHANGE_ID.set("")  # noqa: SLF001 — never a previous call's id on this card
        R._PENDING.set(None)  # noqa: SLF001
        _DESC.set(None)


def _night_words(platform: Any) -> tuple[str, str, bool]:
    """``(start, end, open now)`` of the night window, in words."""
    from ..harness import night as N

    v = N.window_view(platform.config)
    return str(v.get("start_words") or ""), str(v.get("end_words") or ""), bool(v.get("open"))


def _emergency_on(platform: Any) -> bool:
    try:
        from ..agents.orchestrator import emergency_stop_on

        return bool(emergency_stop_on(getattr(platform, "config", None)))
    except Exception:  # noqa: BLE001 — unknown: nothing said
        return False


#: v1.438.0 review: a Local only chat's words never leave the network at night.
LOCAL_ONLY_NIGHT = (
    "This chat is Local only, so a night task queued from it runs only on a model on this PC or your own "
    "network, and {model} is not one. Pick a local model for it, or queue it from a chat that is not Local only."
)
LOCAL_ONLY_NIGHT_TEAM = (
    "This chat is Local only, so a night task queued from it cannot hand pieces to your model team (a member "
    "may run on a cloud model). Queue it without the team, or from a chat that is not Local only."
)


def _local_only_night_problem(platform: Any, args: dict[str, Any]) -> str:
    """v1.438.0 review: in a Local only chat a night task's words go only to a
    model that stays on the user's network — its own model, else the app's
    default (what its session's first turn runs on), and every model it is
    tried on; never a team. "" when the chat is not Local only or every model
    stays. BLOCKING-free (no I/O beyond the provider registry)."""
    from ..providers import egress

    if not egress.current_scope().on:
        return ""
    if args.get("team"):
        return LOCAL_ONLY_NIGHT_TEAM
    cfg = getattr(platform, "config", None)
    manager = getattr(platform, "providers", None)
    pairs: list[tuple[str, str]] = []
    picks = [p for p in (args.get("try_on") or []) if isinstance(p, dict)]
    if picks:
        pairs = [(str(p.get("provider") or "").strip(), str(p.get("model") or "").strip()) for p in picks]
    else:
        provider = str(args.get("provider") or "").strip()
        model = str(args.get("model") or "").strip()
        if not provider:
            provider = str(getattr(cfg, "default_provider", "") or "")
            model = str(getattr(cfg, "default_model", "") or "")
        pairs = [(provider, model)]
    for provider, model in pairs:
        try:
            stays = egress.stays_on_this_network(provider, manager, cfg, model)
        except Exception:  # noqa: BLE001 — unknown: it does not stay
            stays = False
        if not stays:
            shown = f"{provider} · {model}" if model else (provider or "the default model")
            return LOCAL_ONLY_NIGHT.format(model=shown)
    return ""


def _refusal_words(exc: Any) -> str:
    s = " ".join(str(exc or "").split()) or "That could not be done."
    return s if s[-1] in ".!?" else s + "."


class NightTaskAddTool(_BuildTool):
    name = "night_task_add"
    RECORD = "night_task"
    description = (
        "Queue a coding task for tonight's night shift (Coding sessions › Tonight): `words` is what the "
        "task should do; `folder` the full location of the repository it works in (leave it out to use "
        "the folder of the project this chat is in — if there is none, ASK the user which folder, never "
        "guess). It runs on its own, unattended, inside the night window from Settings, on a branch of "
        "its own. Optional: `provider` and `model` (as the model list names them), `account` (an account "
        "of that model's vendor), `team` true to let the model hand pieces to the user's model team, or "
        "`try_on` [{provider, model}] (2 or 3) to try it on several models. The user approves on a card "
        "that says when it runs; Undo takes it off the list while it has not started."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "words": {"type": "string"},
            "folder": {"type": "string"},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "account": {"type": "string"},
            "team": {"type": "boolean"},
            "try_on": {
                "type": "array",
                "items": {"type": "object", "properties": {"provider": {"type": "string"}, "model": {"type": "string"}}},
            },
        },
        "required": ["words"],
    }

    async def spend_check(self, args: dict[str, Any], ctx: ToolContext) -> SpendCheck | None:
        """The card says WHEN it runs (unattended work), every time — once
        only, in every posture but "Don't ask"; an unattended run refuses."""
        try:
            folder, problem = await asyncio.to_thread(resolve_folder, self.platform, ctx, args.get("folder"))
            if problem or not str(args.get("words") or "").strip():
                return None  # execute refuses in words
            start, end, open_now = _night_words(self.platform)
        except Exception:  # noqa: BLE001 — unsure: still this card (v1.438.0 review: never the
            # ordinary card, which the Auto posture's judge may approve for unattended work)
            return SpendCheck(
                ask=True,
                words=f"Queue {_q(args.get('words'))} for the night shift: it runs on its own inside the night window.",
                dont_ask_skips=True,
                attended_only=True,
            )
        when = f"between {start} and {end}" if start and end else "inside the night window"
        now = " The window is open now, so it may start within a minute." if open_now else ""
        words = (
            f"Queue {_q(args.get('words'))} for the night shift: it runs on its own {when} in {folder}, "
            f"on a branch of its own.{now}"
        )
        return SpendCheck(ask=True, words=words, dont_ask_skips=True, attended_only=True)

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        await self._begin()
        # The store mints the id: captured with no name, named after the write.
        desc = await R.capture_create_undo(self.platform, self.RECORD, "", pending=True)
        _DESC.set(desc)
        return desc

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from pydantic import ValidationError

        from ..daemon.routes.night import NightTaskBody, TryOnBody, add_night_task
        from ..harness import night as N

        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        folder, problem = await asyncio.to_thread(resolve_folder, self.platform, ctx, args.get("folder"))
        if problem:
            return ToolResult(ok=False, error=problem)
        local_problem = _local_only_night_problem(self.platform, args)
        if local_problem:
            return ToolResult(ok=False, error=local_problem)
        try:
            picks = [TryOnBody(provider=str(p.get("provider") or ""), model=str(p.get("model") or ""))
                     for p in (args.get("try_on") or []) if isinstance(p, dict)]
            body = NightTaskBody(
                folder=folder, words=str(args.get("words") or ""), provider=str(args.get("provider") or ""),
                model=str(args.get("model") or ""), team=bool(args.get("team")), try_on=picks,
                account=str(args.get("account") or ""),
            )
        except ValidationError:
            return ToolResult(ok=False, error="That task could not be read: its words, model or account are too long.")
        try:
            task = await add_night_task(d, body)
        except N.NightRefused as exc:
            return ToolResult(ok=False, error=_refusal_words(exc))
        R.bind_created(task["id"])
        _note(after=task)
        start, end, open_now = _night_words(self.platform)
        when = f"between {start} and {end}" if start and end else "inside the night window"
        stop = " Emergency stop is on, so it will not start until it is turned off." if _emergency_on(self.platform) else ""
        soon = " The night window is open now, so it may start within a minute." if open_now else ""
        new = f"{_q(task['words'])} in {task['folder']}, runs {when}"
        data: dict[str, Any] = {"task_id": task["id"]}
        if R._CHANGE_ID.get():  # noqa: SLF001 — a card only when an Undo was captured
            data["record_change"] = self._card("Night task queued", "Tonight's list", None, new, f"night.{task['id']}")
        return ToolResult(
            ok=True,
            output=(
                f"Queued {_q(task['words'])} for the night shift in {task['folder']}: it runs on its own {when}, "
                f"on a branch of its own.{soon}{stop}"
            ),
            data=data,
        )


_STATE_WORDS = {
    "queued": "queued", "running": "running now", "done": "done", "failed": "failed",
    "needs_you": "needs you", "stopped": "stopped",
}


class NightTaskListTool(Tool):
    """Tonight's list in words — read only."""

    name = "night_task_list"
    description = (
        "List the night shift (Coding sessions › Tonight): the night window, the tasks queued for "
        "tonight in the order they start, the ones running, and the ones that ended in the last days — "
        "each with its id, folder and state."
    )
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..harness import night as N

        try:
            tasks = await asyncio.to_thread(N.list_tasks, _engine(self.platform))
            window = await asyncio.to_thread(N.window_view, self.platform.config)
        except Exception:  # noqa: BLE001 — said, never an empty list
            return ToolResult(ok=False, error="The Tonight list could not be read right now.")
        lines = [str(window.get("words") or "").strip()]
        if _emergency_on(self.platform):
            lines.append("Emergency stop is on: no night task starts until it is turned off.")
        if not tasks:
            lines.append("Nothing is queued for the night shift.")
        queued = [t for t in tasks if t.get("state") == N.QUEUED]
        for n, t in enumerate(queued, start=1):
            lines.append(f"{n}. {_q(t.get('words'))} — queued, in {t.get('folder')} (id {t.get('id')})")
        for t in tasks:
            if t.get("state") == N.QUEUED:
                continue
            lines.append(
                f"- {_q(t.get('words'))} — {_STATE_WORDS.get(str(t.get('state')), str(t.get('state')))}, "
                f"in {t.get('folder')} (id {t.get('id')})"
            )
        return ToolResult(
            ok=True,
            output="\n".join(x for x in lines if x),
            data={"count": len(tasks), "queued": [t.get("id") for t in queued]},
        )


def resolve_night_task(platform: Any, ref: str, *, states: "tuple[str, ...]") -> tuple[str, str]:
    """``(task id, "")`` for a night task named by its id or by words its
    task holds (unique among the tasks in ``states``), else ``("", one
    plain sentence)``. Blocking."""
    from ..harness import night as N

    ref = " ".join(str(ref or "").split())
    if not ref:
        return "", "Say which night task (its words or its id)."
    engine = _engine(platform)
    exact = N.get_task(engine, ref)
    if exact is not None:
        if exact.get("state") not in states:
            return "", _ended_words(exact)
        return exact["id"], ""
    tasks = [t for t in N.list_tasks(engine)]
    words = ref.casefold().strip("“”\"'")
    hits = [t for t in tasks if t.get("state") in states and words in " ".join(str(t.get("words") or "").split()).casefold()]
    if len(hits) == 1:
        return hits[0]["id"], ""
    if len(hits) > 1:
        names = ", ".join(_q(t.get("words"), 40) for t in hits[:6])
        return "", f"More than one night task matches {_q(ref)}: {names}. Say which one."
    ended = [t for t in tasks if words in str(t.get("words") or "").casefold()]
    if ended:
        return "", _ended_words(ended[0])
    open_tasks = [t for t in tasks if t.get("state") in states]
    if not open_tasks:
        return "", "Nothing is queued for the night shift."
    names = ", ".join(_q(t.get("words"), 40) for t in open_tasks[:6])
    return "", f"No night task matches {_q(ref)}. Tonight's list: {names}."


def _ended_words(task: dict[str, Any]) -> str:
    from ..harness import night as N

    if task.get("state") == N.RUNNING:
        return f"The night task {_q(task.get('words'))} is running now."
    return f"The night task {_q(task.get('words'))} has already ended."


class NightTaskCancelTool(_BuildTool):
    name = "night_task_cancel"
    RECORD = "night_task"
    description = (
        "Cancel a night task (`task` = its id from night_task_list, or words from it): a queued one never "
        "starts; a running one is stopped where it is. The user approves on a card; Undo puts a cancelled "
        "QUEUED task back on tonight's list (a stopped running one is not started again)."
    )
    input_schema = {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        from ..harness import night as N

        await self._begin()
        try:
            task_id, _problem = await asyncio.to_thread(
                resolve_night_task, self.platform, str(args.get("task") or ""), states=(N.QUEUED, N.RUNNING)
            )
            if not task_id:
                return None
            prior = await asyncio.to_thread(snap_night_task, self.platform, task_id)
        except Exception:  # noqa: BLE001 — no snapshot, no undo row
            return None
        if prior is None:
            return None
        return _capture(self.RECORD, task_id, prior)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..daemon.routes.night import cancel_night_task
        from ..harness import night as N

        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        task_id, problem = await asyncio.to_thread(
            resolve_night_task, self.platform, str(args.get("task") or ""), states=(N.QUEUED, N.RUNNING)
        )
        if not task_id:
            return ToolResult(ok=False, error=problem)
        try:
            out = await asyncio.to_thread(cancel_night_task, d, task_id)
        except N.NightRefused as exc:
            return ToolResult(ok=False, error=_refusal_words(exc))
        after = out.get("task") or {}
        _note(after=after)
        words = _q(after.get("words"))
        was_running = bool(out.get("stopped_turn")) or N.CANCELLED_RUNNING in json.dumps(after.get("notes") or [])
        old = "running" if was_running else "queued for tonight"
        new = "stopped" if was_running else "cancelled (it will not start)"
        output = (
            f"Stopped the running night task {words}; what it did so far stays on its branch."
            if was_running
            else f"Cancelled {words}: it will not start tonight."
        )
        return ToolResult(
            ok=True,
            output=output,
            data={"record_change": self._card("Night task cancelled", f"Night task {words}", old, new, f"night.{task_id}")},
        )


class NightTaskMoveTool(_BuildTool):
    name = "night_task_move"
    RECORD = "night_order"
    description = (
        "Change the order tonight's queued tasks start in: `task` (its id or words from it) moves `to` "
        "\"top\", \"up\" (one place), \"down\" (one place) or \"bottom\". The user approves on a card; "
        "Undo puts the order back."
    )
    input_schema = {
        "type": "object",
        "properties": {"task": {"type": "string"}, "to": {"type": "string", "enum": ["top", "up", "down", "bottom"]}},
        "required": ["task", "to"],
    }

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        await self._begin()
        try:
            prior = await asyncio.to_thread(snap_night_order, self.platform, "queue")
        except Exception:  # noqa: BLE001
            return None
        return _capture(self.RECORD, "queue", prior)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..harness import night as N

        to = str(args.get("to") or "").strip().lower()
        if to not in ("top", "up", "down", "bottom"):
            return ToolResult(ok=False, error="Say where it moves: top, up, down or bottom.")
        task_id, problem = await asyncio.to_thread(
            resolve_night_task, self.platform, str(args.get("task") or ""), states=(N.QUEUED,)
        )
        if not task_id:
            return ToolResult(ok=False, error=problem)
        before = await asyncio.to_thread(_queued_ids, self.platform)

        def _move() -> list[str]:
            steps = 1 if to in ("up", "down") else len(before)
            direction = "up" if to in ("top", "up") else "down"
            for _ in range(max(1, steps)):
                N.move_task(_engine(self.platform), task_id, direction)
            return _queued_ids(self.platform)

        try:
            after = await asyncio.to_thread(_move)
        except N.NightRefused as exc:
            return ToolResult(ok=False, error=_refusal_words(exc))
        _note(after=after)
        tasks = {t["id"]: t for t in await asyncio.to_thread(N.rows_in, _engine(self.platform), N.QUEUED)}
        words = _q((tasks.get(task_id) or {}).get("words"))
        place = after.index(task_id) + 1 if task_id in after else 0
        if after == before:
            return ToolResult(ok=True, output=f"{words} is already number {place} of {len(after)} on tonight's list.")
        data = {
            "record_change": self._card(
                "Tonight's order changed", f"Night task {words}",
                f"number {before.index(task_id) + 1} of {len(before)}", f"number {place} of {len(after)}", "night.order",
            )
        }
        return ToolResult(ok=True, output=f"Moved {words} to number {place} of {len(after)} on tonight's list.", data=data)


# --- a folder's coding settings


class BuildFolderShowTool(Tool):
    """A folder's defaults and team for new coding sessions — read only."""

    name = "build_folder_show"
    description = (
        "Show what NEW coding sessions in a folder start with: its defaults for Chat › Code (Iron Jarvis's "
        "own coding sessions: model, reasoning, approval level) and for Build › Code (the user's own Claude "
        "Code, Codex, Pi, OpenCode, Grok or Antigravity program: tool, model, effort, mode), and its coding team. "
        "`folder` is a full location; leave it out for the folder of the project this chat is in."
    )
    input_schema = {"type": "object", "properties": {"folder": {"type": "string"}}}
    reversibility = Reversibility.READONLY

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        folder, problem = await asyncio.to_thread(resolve_folder, self.platform, ctx, args.get("folder"))
        if problem:
            return ToolResult(ok=False, error=problem)
        defaults = await asyncio.to_thread(snap_build_defaults, self.platform, folder)
        team = await asyncio.to_thread(snap_build_team, self.platform, folder)
        lines = [
            f"New coding sessions in {folder}:",
            f"- Chat › Code defaults: {_view_words('code', defaults.get('code'))}",
            f"- Build › Code defaults: {_view_words('tool', defaults.get('tool'))}",
            f"- Coding team: {_team_words(team)}",
        ]
        return ToolResult(ok=True, output="\n".join(lines), data={"folder": folder, "defaults": defaults, "team": team})


class _FolderTool(_BuildTool):
    """A change to one folder's coding settings (``RECORD`` names which)."""

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        await self._begin()
        try:
            folder, problem = await asyncio.to_thread(_existing_folder, self.platform, ctx, args.get("folder"))
            if problem:
                return None
            prior = await asyncio.to_thread(SNAP[self.RECORD], self.platform, folder)
        except Exception:  # noqa: BLE001 — no snapshot, no undo row
            return None
        return _capture(self.RECORD, folder, prior)


def _menu(platform: Any) -> "set[tuple[str, str]] | None":
    d = _d(platform)
    if d is None:
        return None
    try:
        from ..daemon.routes.build_attempts import menu_rows

        return {(str(r.get("provider") or ""), str(r.get("model") or "")) for r in menu_rows(d)}
    except Exception:  # noqa: BLE001 — unreadable: said by the caller
        return None


_VIEW_REFUSAL = (
    "Say which coding view: \"chat_code\" (Iron Jarvis's own coding sessions in Chat › Code) or "
    "\"build_code\" (the user's own Claude Code, Codex, Pi, OpenCode, Grok or Antigravity in Build › Code). If the user "
    "did not say, ask them."
)


class BuildDefaultsSetTool(_FolderTool):
    name = "build_defaults_set"
    RECORD = "build_defaults"
    description = (
        "Set what NEW coding sessions in a folder start with (a session already made keeps its own). "
        "`folder` is the repository's full location; leave it out for the folder of the project this chat "
        "is in — if there is none, ASK which folder, never guess. `view` is \"chat_code\" (Iron Jarvis's "
        "own coding sessions in Chat › Code: `provider` and `model` as the model list names them — Codex is "
        "provider codex-cli, Claude Code is claude-cli — `reasoning` low|medium|high|xhigh|off, "
        "`approval_mode` always_ask|approve_for_me|accept_edits|auto) or \"build_code\" (the user's own "
        "program in Build › Code: `tool` claude-code|codex|pi|opencode|grok, its `model`, `effort` and "
        "`mode` ask|edits|plan|auto|own). If the user did not say which view, ask. Only the fields given "
        "change (\"\" clears one). \"Don't ask\" and Full access are never a folder default. An account is "
        "not kept as a folder default. The user approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "folder": {"type": "string"},
            "view": {"type": "string", "enum": ["chat_code", "build_code"]},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "reasoning": {"type": "string"},
            "approval_mode": {"type": "string"},
            "tool": {"type": "string"},
            "effort": {"type": "string"},
            "mode": {"type": "string"},
            "account": {"type": "string", "description": "Not kept: an account is picked when a session starts."},
        },
        "required": ["view"],
    }

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        desc = await super().capture_undo(args, ctx)
        view = VIEW_OF.get(str(args.get("view") or ""))
        if desc is not None and view:
            meta = json.loads(desc["pre_inline"])
            meta["views"] = [view]
            desc["pre_inline"] = json.dumps(meta, default=str)
        return desc

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..harness import folder_defaults as FD

        view = VIEW_OF.get(str(args.get("view") or "").strip())
        if view is None:
            return ToolResult(ok=False, error=_VIEW_REFUSAL)
        folder, problem = await asyncio.to_thread(_existing_folder, self.platform, ctx, args.get("folder"))
        if problem:
            return ToolResult(ok=False, error=problem)
        fields = ("provider", "model", "reasoning", "approval_mode") if view == "code" else ("tool", "model", "effort", "mode")
        given = {f: str(args[f]).strip() for f in fields if args.get(f) is not None}
        if view == "code":
            other = [f for f in ("tool", "effort", "mode") if args.get(f)]
        else:
            other = [f for f in ("provider", "reasoning", "approval_mode") if args.get(f)]
        if other:
            return ToolResult(ok=False, error=_VIEW_REFUSAL)
        if not given:
            return ToolResult(ok=False, error="Say what new coding sessions here should start with.")
        before = await asyncio.to_thread(snap_build_defaults, self.platform, folder)
        if view == "code":
            value = dict(before.get("code") or {})
        else:
            value = dict(before.get("tool") or {})
            given["harness"] = given.pop("tool", value.get("harness", ""))
            if given["harness"] and given["harness"] != value.get("harness"):
                value = {}  # another tool: its own model, effort and mode start empty
        for k, v in given.items():
            if v:
                value[k] = v
            else:
                value.pop(k, None)
        if view == "code" and value.get("provider"):
            pairs = await asyncio.to_thread(_menu, self.platform)
            if pairs is None:
                return ToolResult(ok=False, error="The model menu could not be read, so the model was not checked; try again.")
            provider, model = value.get("provider", ""), value.get("model", "")
            if not ((provider, model) in pairs or (not model and provider in {p for p, _m in pairs})):
                shown = f"{provider} · {model}" if model else provider
                return ToolResult(ok=False, error=f"That model is not in your model menu: {shown}.")
        try:
            saved = await asyncio.to_thread(FD.save, _engine(self.platform), folder, view, value or None)
        except FD.DefaultsRefused as exc:
            return ToolResult(ok=False, error=_refusal_words(exc))
        _note(after=saved, views=[view])
        old, new = _view_words(view, before.get(view)), _view_words(view, saved.get(view))
        account = ACCOUNT_NOT_KEPT if str(args.get("account") or "").strip() else ""
        return ToolResult(
            ok=True,
            output=f"New {VIEW_WORDS[view]} sessions in {folder} now start with: {new}.{account}",
            data={
                "record_change": self._card(
                    "Folder defaults set", f"New {VIEW_WORDS[view]} sessions in {folder}", old, new,
                    f"build_defaults.{folder}.{view}",
                )
            },
        )


class BuildDefaultsClearTool(_FolderTool):
    name = "build_defaults_clear"
    RECORD = "build_defaults"
    description = (
        "Clear a folder's defaults for NEW coding sessions, so they start with the app's own defaults again. "
        "`folder` as for build_defaults_set (ask if unknown); `view` \"chat_code\", \"build_code\" or "
        "\"both\" (default both). The user approves on a card; Undo puts them back."
    )
    input_schema = {
        "type": "object",
        "properties": {"folder": {"type": "string"}, "view": {"type": "string", "enum": ["chat_code", "build_code", "both"]}},
    }

    def _views(self, args: dict[str, Any]) -> "list[str] | None":
        raw = str(args.get("view") or "both").strip()
        if raw == "both":
            return ["code", "tool"]
        view = VIEW_OF.get(raw)
        return [view] if view else None

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        desc = await super().capture_undo(args, ctx)
        views = self._views(args)
        if desc is not None and views:
            meta = json.loads(desc["pre_inline"])
            meta["views"] = views
            desc["pre_inline"] = json.dumps(meta, default=str)
        return desc

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..harness import folder_defaults as FD

        views = self._views(args)
        if views is None:
            return ToolResult(ok=False, error=_VIEW_REFUSAL)
        folder, problem = await asyncio.to_thread(_existing_folder, self.platform, ctx, args.get("folder"))
        if problem:
            return ToolResult(ok=False, error=problem)
        before = await asyncio.to_thread(snap_build_defaults, self.platform, folder)
        todo = [v for v in views if before.get(v)]
        if not todo:
            return ToolResult(ok=False, error=f"New coding sessions in {folder} have no defaults of their own to clear.")
        saved = before
        for v in todo:
            saved = await asyncio.to_thread(FD.save, _engine(self.platform), folder, v, None)
        _note(after=saved, views=todo)
        return ToolResult(
            ok=True,
            output=f"Cleared the defaults for new coding sessions in {folder}: they start with the app's own defaults.",
            data={
                "record_change": self._card(
                    "Folder defaults cleared", f"New coding sessions in {folder}",
                    _defaults_words(before, tuple(todo)), "the app's own defaults", f"build_defaults.{folder}",
                )
            },
        )


def _model_spec(spec: str, providers: "set[str]") -> "tuple[str, str] | None":
    """``(provider, model)`` when a seat is written as a model ("codex-cli",
    "claude-cli/sonnet"); None for a member's name."""
    text = spec.strip()
    if "/" in text:
        p, _, m = text.partition("/")
        if p.strip():
            return p.strip(), m.strip()
    if text in providers:
        return text, ""
    return None


class BuildTeamSetTool(_FolderTool):
    name = "build_team_set"
    RECORD = "build_team"
    description = (
        "Set the coding team NEW coding sessions in a folder start with (\"Codex writes, Claude reviews\"): "
        "`workers` (1 or 2) write the code, `reviewer` (optional) checks each change. Each seat is a member "
        "of the user's model team by NAME, or a model written provider/model (\"codex-cli\", "
        "\"claude-cli/sonnet\") — a member on that model is reused, else one is made on the model team. "
        "`folder` as for build_defaults_set (ask if unknown). The user approves on a card; Undo puts back "
        "the folder's team (members it made stay on the model team)."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "folder": {"type": "string"},
            "workers": {"type": "array", "items": {"type": "string"}},
            "reviewer": {"type": "string"},
        },
        "required": ["workers"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..agents import model_team as MT
        from ..harness import team as HT

        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        folder, problem = await asyncio.to_thread(_existing_folder, self.platform, ctx, args.get("folder"))
        if problem:
            return ToolResult(ok=False, error=problem)
        workers = [str(w).strip() for w in (args.get("workers") or []) if str(w or "").strip()]
        reviewer = str(args.get("reviewer") or "").strip()
        if not workers:
            return ToolResult(
                ok=False, error="A team needs a worker who writes the code; to stop using a team here, clear it instead."
            )
        seats = [*workers, *([reviewer] if reviewer else [])]
        if len({s.casefold() for s in seats}) != len(seats):
            return ToolResult(ok=False, error="The same seat is named twice; a worker cannot also be the reviewer.")
        # v1.438.0 review: the registry read OFF the loop (SQLite).
        members = {
            getattr(r, "name", "").casefold(): getattr(r, "name", "")
            for r in await asyncio.to_thread(MT.team_members, self.platform)
        }
        pairs = await asyncio.to_thread(_menu, self.platform)
        providers = {p for p, _m in (pairs or set())}
        # v1.438.0 review: the team's OWN rules (how many workers, a member
        # named, its role) checked BEFORE a member is made for a model seat —
        # a team that could never be saved leaves nothing on the model team.
        pending = frozenset(" ".join(s.split()) for s in seats if s.casefold() not in members and _model_spec(s, providers) is not None)
        try:
            await asyncio.to_thread(
                HT.validate, self.platform,
                {"workers": [members.get(w.casefold(), w) for w in workers],
                 "reviewer": members.get(reviewer.casefold(), reviewer) if reviewer else ""},
                pending=pending,
            )
        except HT.TeamRefused as exc:
            return ToolResult(ok=False, error=_refusal_words(exc))
        from ..providers import egress

        local_only = bool(egress.current_scope().on)
        made: list[str] = []
        said: list[str] = []

        def _seat(spec: str, role: str) -> str:
            from ..daemon.routes.team import member_for_model

            if spec.casefold() in members:
                return members[spec.casefold()]
            pm = _model_spec(spec, providers)
            if pm is None:
                return spec  # the team's own check names it in words
            out = member_for_model(d, provider=pm[0], model=pm[1], role=role, local_only=local_only)
            if out.get("created"):
                made.append(out["name"])
                said.append(f"made the model-team member {out['name']} ({out.get('label') or spec})")
            return str(out["name"])

        try:
            names = [await asyncio.to_thread(_seat, w, "worker") for w in workers]
            rev = await asyncio.to_thread(_seat, reviewer, "reviewer") if reviewer else ""
            team = await asyncio.to_thread(HT.validate, self.platform, {"workers": names, "reviewer": rev})
        except HT.TeamRefused as exc:
            kept = f" (It already {'; '.join(said)}; they stay on your model team.)" if said else ""
            return ToolResult(ok=False, error=_refusal_words(exc) + kept)
        before = await asyncio.to_thread(snap_build_team, self.platform, folder)
        try:
            saved = await asyncio.to_thread(HT.set_folder_team, _engine(self.platform), folder, team)
        except HT.TeamRefused as exc:
            kept = f" (It already {'; '.join(said)}; they stay on your model team.)" if said else ""
            return ToolResult(ok=False, error=_refusal_words(exc) + kept)
        _note(after=saved, made=made)
        new = _team_words(saved)
        extra = f" It {'; '.join(said)}." if said else ""
        return ToolResult(
            ok=True,
            output=f"New coding sessions in {folder} now start with this team: {new}.{extra}",
            data={
                "record_change": self._card(
                    "Folder team set", f"Coding team in {folder}", _team_words(before), new, f"build_team.{folder}",
                )
            },
        )


class BuildTeamClearTool(_FolderTool):
    name = "build_team_clear"
    RECORD = "build_team"
    description = (
        "Stop using a coding team for NEW coding sessions in a folder (they start solo). `folder` as for "
        "build_defaults_set (ask if unknown). The members stay on the model team. The user approves on a "
        "card; Undo puts the team back."
    )
    input_schema = {"type": "object", "properties": {"folder": {"type": "string"}}}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..harness import team as HT

        folder, problem = await asyncio.to_thread(_existing_folder, self.platform, ctx, args.get("folder"))
        if problem:
            return ToolResult(ok=False, error=problem)
        before = await asyncio.to_thread(snap_build_team, self.platform, folder)
        if not before:
            return ToolResult(ok=False, error=f"New coding sessions in {folder} have no team to stop using.")
        try:
            await asyncio.to_thread(HT.set_folder_team, _engine(self.platform), folder, None)
        except HT.TeamRefused as exc:
            return ToolResult(ok=False, error=_refusal_words(exc))
        _note(after=None)
        return ToolResult(
            ok=True,
            output=f"New coding sessions in {folder} no longer start with a team (the members stay on your model team).",
            data={
                "record_change": self._card(
                    "Folder team removed", f"Coding team in {folder}", _team_words(before), "none", f"build_team.{folder}",
                )
            },
        )


def build_record_tools(platform: Any) -> list[Tool]:
    """The nine tools, for ``records.record_tools``."""
    return [
        NightTaskAddTool(platform),
        NightTaskListTool(platform),
        NightTaskCancelTool(platform),
        NightTaskMoveTool(platform),
        BuildFolderShowTool(platform),
        BuildDefaultsSetTool(platform),
        BuildDefaultsClearTool(platform),
        BuildTeamSetTool(platform),
        BuildTeamClearTool(platform),
    ]


# --------------------------------------------------------------------------- #
# arming — which sentences offer these tools
# --------------------------------------------------------------------------- #

#: Where a clause ends: a sentence end, a line, a colon before a space (never
#: the one in ``D:\\``), or a joining word ("queue it for tonight and use
#: Codex for this repo"). A writes-and-reviews pair is read on the WHOLE
#: message (:data:`_TEAM_PAIR`).
#: v1.438.0 review: a dot ends a clause only before a space or the end
#: ("utils.py" is one word: "queue 'add docstrings to utils.py' for tonight").
_CLAUSES = re.compile(r"[.!?]+(?=\s|$)|[;\n]+|:\s|\b(?:and\s+then|and|then|also|but)\b", re.IGNORECASE)
_FILLER = re.compile(
    r"(?:please|kindly|ok(?:ay)?|so|hey|hi|hello|jarvis|now|just|also|then|and|go\s+ahead\s+and|can\s+you"
    r"|could\s+you|would\s+you|will\s+you|i\s+(?:want|need|would\s+like)\s+you\s+to|i['’]d\s+like\s+you\s+to"
    r"|let['’]?s|help\s+me(?:\s+to)?)\b[\s,]*",
    re.IGNORECASE,
)
#: "I want to queue…", "I'd like to use Codex for…" — the user's wish for an
#: APP action is a request; "I need to fix the tests tonight" is about the
#: user's own evening, never a task for the night shift.
_I_WANT_TO = re.compile(
    r"i(?:\s+(?:want|would\s+like)|['’]d\s+like)\s+to\s+(?=(?:queue|line\s+up|add|put|cancel|remove|drop|delete"
    r"|unqueue|move|bump|use|set|make|switch|change|clear|reset|forget|stop|start|turn\s+off|go\s+solo)\b)",
    re.IGNORECASE,
)

# --- the night shift
#: Tonight's list by its words ("tonight", "tonight's list", "the night
#: shift", "overnight").
_NIGHT = (
    r"(?:tonight(?:['’]s)?|to-night|overnight|over\s+night|(?:the\s+)?night\s+shift(?:['’]s)?"
    r"|(?:the\s+)?night\s+(?:list|queue)|while\s+i(?:['’]m|\s+am)?\s+(?:sleep(?:ing)?|asleep))"
)
_NIGHT_RX = re.compile(r"\b" + _NIGHT + r"(?![\w-])", re.IGNORECASE)
#: Tonight's LIST by name ("tonight's list", "the night shift", "the night
#: queue") — the place tasks wait in, never "tonight" alone.
_NIGHT_LIST = re.compile(
    r"\b(?:tonight['’]s\s+(?:list|queue|tasks?|night\s+shift|work)|(?:the\s+)?night\s+shift"
    r"|(?:the\s+)?night\s+(?:list|queue|tasks?)|(?:the\s+)?tonight\s+(?:list|queue))\b",
    re.IGNORECASE,
)
#: Everyday things done tonight — never a coding task ("a reservation for
#: tonight", "dinner tonight", "an alarm for tonight").
_EVERYDAY_TONIGHT = re.compile(
    r"\b(?:dinner|lunch|supper|reservation|table|alarm|reminder|remind|call|meeting|appointment|movie|film"
    r"|show|tv|party|date|game|match|flight|ride|uber|lyft|pizza|food|order|delivery|workout|gym|run\s+club"
    r"|bath|nap|bed|plans?|drinks?|concert|tickets?|babysitter|walk|homework|laundry|groceries|errands?"
    r"|event|events|calendar|cook|recipe|weather|news|emails?|messages?|posts?|tweets?|newsletters?|songs?|videos?"
    r"|playlists?|episodes?|agenda|itinerary)\b",
    re.IGNORECASE,
)
#: Words that make a task a coding one ("the README", "the tests", a path).
_CODE_THING = re.compile(
    r"\b(?:readme|tests?|test\s+suite|bugs?|code|codebase|repo(?:sitory)?|branch|refactor(?:ing)?|lint(?:ing)?"
    r"|typos?|docs?|documentation|dependenc(?:y|ies)|migrations?|functions?|modules?|build|ci|pull\s+request"
    r"|pr|changelog|types?|logging|api|endpoints?|scripts?|components?|styles?)\b"
    r"|[a-z]:[\\/]|\b[\w-]{1,60}\.(?:py|ts|tsx|js|jsx|md|json|toml|yaml|yml|css|html|go|rs|java|cs|cpp|rb)\b",
    re.IGNORECASE,
)
_QUEUE_VERB = re.compile(r"(?:queue(?:\s+up)?|enqueue|line\s+up)\b", re.IGNORECASE)
_ADD_VERB = re.compile(
    r"(?:add|put|schedule|set\s+up|have\s+(?:it|jarvis|you|iron\s+jarvis)\s+(?:do|work\s+on|run|handle|take\s+care\s+of)"
    r"|run|do|work\s+on|handle)\b",
    re.IGNORECASE,
)
#: "have it …" / "have Jarvis …" in front of the work ("have it update the
#: README overnight") — a request, never the question "have it …?".
_HAVE_IT = re.compile(r"^have\s+(?:it|jarvis|you|iron\s+jarvis)\s+", re.IGNORECASE)
#: v1.448.0 (Wave A, A1): "have my coding sessions run with a reviewer…" —
#: the sessions ordered about, at the head of the message.
_HAVE_SESSIONS = re.compile(
    r"^have\s+(?:(?:my|the|all|every|each|new)\s+){0,3}(?:coding|code|build)\s+sessions?\s+", re.IGNORECASE
)
_CODE_VERB = re.compile(
    r"(?:fix|update|refactor|write|clean\s+up|tidy|implement|migrate|upgrade|rewrite|document|test|lint|format"
    r"|rename|remove\s+dead|add\s+tests|add\s+types|bump|port)\b",
    re.IGNORECASE,
)
_CANCEL_VERB = re.compile(
    r"(?P<verb>cancel|remove|drop|delete|unqueue|dequeue|scratch|take|pull|skip|stop|kill|abort|halt)\s+(?:off\s+|out\s+)?"
    r"(?P<det>the|that|this|my|tonight['’]s)\s+(?P<pre>(?:[\w'’.&/\\-]+\s+){0,6}?)(?P<night>night\s+)?tasks?\b(?P<tail>[^\n]{0,80})",
    re.IGNORECASE,
)
#: Before "task": a schedule's or another app's task ("the scheduled task",
#: "my Asana task", "the Windows task").
_NOT_A_NIGHT_TASK = re.compile(
    r"\b(?:scheduled|recurring|cron|windows|asana|jira|todoist|trello|clickup|outlook|teams|planner|to-?do"
    r"|monday|notion|google|reminders?|calendar|chores?|house|homework)\b",
    re.IGNORECASE,
)
_OTHER_PLACE_TAIL = re.compile(
    r"\s*(?:from|in|on|off)\s+(?:(?:my|the|our)\s+)?(?:to-?do|asana|jira|todoist|trello|clickup|outlook|teams"
    r"|planner|calendar|notion|google|list\s+of|reminders?|board|backlog|sprint)\b",
    re.IGNORECASE,
)
_MOVE = re.compile(
    r"(?:move|bump|put|push)\s+(?P<obj>[^\n]{0,80}?)\s*(?:to\s+the\s+(?:top|front|bottom|end|back)|up|down|first|last"
    r"|higher|lower|ahead)\b",
    re.IGNORECASE,
)
#: v1.438.0 review: adding a task ONTO tonight's list ("add a night task to
#: bump the version") — the list words carry it, never "do the night tasks".
_ADD_ONTO = re.compile(r"(?:add|put|schedule|set\s+up|create|make)\b", re.IGNORECASE)
#: "tonight, refactor the auth module": the night word in front of the work.
_LEAD_NIGHT = re.compile(
    r"^(?:tonight|overnight|over\s+night|while\s+i(?:['’]m|\s+am)?\s+(?:sleep(?:ing)?|asleep))\s*,?\s*", re.IGNORECASE
)
#: "take 'x' off the night shift" (bounded: the words between are capped).
_TAKE_OFF = re.compile(r"(?:take|pull)\b[^\n]{0,80}?\boff\b", re.IGNORECASE)
_NIGHT_LIST_ASK = re.compile(
    r"(?:show|list|read|tell\s+me|give\s+me|what(?:['’]s|\s+is|\s+are)|which|anything)\b[^\n]{0,40}?"
    r"(?:queued|lined\s+up|waiting|on\s+(?:the\s+)?(?:night\s+shift|tonight['’]s)|tonight['’]s|night\s+shift|night\s+tasks?)",
    re.IGNORECASE,
)
#: v1.448.0 (Wave A "chat configures everything", A1): a NIGHT TIME in front
#: of coding work — "Every night at 11 run the tests in C:\Projects\ClosePilot
#: and fix what breaks" — is a night task beside the schedule the recurring
#: time also offers (``settings.records``). Anchored at the clause's start,
#: the time bounded; "the night shift at the hospital starts at 7" and "every
#: night I run the tests" (the user's own evening) never read as one.
_NIGHT_TIME = re.compile(
    r"(?:(?:every|each)\s+(?:week)?night|nightly|every\s+evening|at\s+night|after\s+(?:hours|midnight"
    r"|i\s+(?:go\s+to\s+)?(?:bed|sleep)))"
    r"(?:\s*,?\s+(?:at|around|by|from)\s+(?:\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?|midnight))?\s*,?\s*",
    re.IGNORECASE,
)


def _clauses(text: str) -> list[str]:
    """The message cut into clauses, each with the words in front of the
    request ("please", "can you…") taken off. Linear: plain splits and
    ANCHORED matches only."""
    out: list[str] = []
    for part in _CLAUSES.split(text or ""):
        c = part.strip(" \t\r\"'`*•>,-")
        for _ in range(6):
            m = _FILLER.match(c)
            if m is None or not m.group(0):
                break
            c = c[m.end():]
        m = _I_WANT_TO.match(c)
        if m is not None:
            c = c[m.end():]
        if c:
            out.append(c)
    return out


def _night_request(c: str) -> bool:
    """One clause asks to queue, cancel or move a night task."""
    night = _NIGHT_RX.search(c) is not None
    listed = _NIGHT_LIST.search(c) is not None
    everyday = _EVERYDAY_TONIGHT.search(c) is not None
    if _QUEUE_VERB.match(c) and (night or listed or _CODE_THING.search(c)) and not (everyday and not listed):
        return True
    if night and _ADD_VERB.match(c) and (listed or _CODE_THING.search(c)) and not (everyday and not listed):
        return True
    if listed and _ADD_ONTO.match(c) and not everyday:
        return True  # v1.438.0 review: "add a night task to bump the version"
    bare = _LEAD_NIGHT.sub("", _HAVE_IT.sub("", c, count=1), count=1)
    if night and _CODE_VERB.match(bare) and _CODE_THING.search(c) and not everyday:
        return True
    # v1.448.0 (Wave A, A1): "every night at 11 run the tests in <path> …" —
    # a night time, then the work (run / fix / update …) on a coding thing.
    m_time = _NIGHT_TIME.match(c)
    if m_time is not None and m_time.end() > 0 and not everyday:
        timed = c[m_time.end():]
        if (_ADD_VERB.match(timed) or _CODE_VERB.match(timed)) and _CODE_THING.search(timed):
            return True
    m = _CANCEL_VERB.match(c)
    if (
        m is not None
        and not _NOT_A_NIGHT_TASK.search(m.group("pre") or "")
        and not _OTHER_PLACE_TAIL.match(m.group("tail") or "")
        and (listed or not everyday)
        # v1.438.0 review: a task is a NIGHT task only by its words — "the
        # night task", tonight's list, a coding thing ("the README task") or
        # a queue verb; a bare "cancel the task" is any task of the user's.
        and (
            listed or night or bool(m.group("night")) or m.group("verb").lower() in ("unqueue", "dequeue")
            or _CODE_THING.search(m.group("pre") or "") is not None
            or _CODE_THING.search(m.group("tail") or "") is not None
        )
    ):
        return True
    if (listed or night) and re.match(
        r"(?:cancel|remove|drop|delete|unqueue|clear|empty|scratch|take\s+off|pull)\b", c, re.IGNORECASE
    ) and not everyday:
        return True
    if listed and _TAKE_OFF.match(c) and not everyday:
        return True  # v1.438.0 review: "take 'upgrade pytest' off the night shift"
    m = _MOVE.match(c)
    return m is not None and (listed or "night" in c.lower())


# --- a folder's defaults and team
#: "this repo", "the folder", a full path — the folder a change is about.
_FOLDER_REF = (
    r"(?:(?:this|the|my|that|our)\s+(?:repo(?:sitory)?|folder|codebase|project\s+folder|checkout|workspace)"
    r"|[a-z]:[\\/][^\s,;]{0,240})"
)
#: v1.448.0 (Wave A, A1): the folder NAMED — "the ClosePilot folder", "my
#: shop repo", "the Johnson project" (one or two words between the determiner
#: and the noun; "project" counts only here, named).
_NAMED_FOLDER_REF = (
    r"(?:(?:this|the|my|that|our)\s+(?:[A-Za-z0-9_.'’-]+\s+){1,2}?"
    r"(?:repo(?:sitory)?|folder|codebase|project|checkout|workspace))"
)
#: A model or coding tool by the name a person uses for it.
_MODEL = (
    r"(?:codex|claude(?:\s+code)?|sonnet|opus|haiku|gpt[\w.-]{0,20}|chatgpt|gemini[\w.-]{0,20}|grok|opencode"
    r"|open\s+code|glm[\w.-]{0,20}|qwen[\w.-]{0,20}|llama[\w.-]{0,20}|deepseek[\w.-]{0,20}|mistral|ollama|pi"
    r"|(?:a|my|the)\s+local\s+model)"
)
_DEFAULTS_SET = re.compile(
    r"(?:use|pick|choose|switch\s+to|go\s+with|default\s+to|start\s+with|run)\s+" + _MODEL
    + r"\b[^\n]{0,120}?\b(?:for|in|on|with)\s+" + _FOLDER_REF
    + r"|(?:make|set)\s+" + _MODEL + r"\s+(?:as\s+)?(?:the\s+)?(?:default|go-?to)\b[^\n]{0,80}?\b(?:for|in|on)\s+" + _FOLDER_REF
    + r"|(?:make|set)\s+" + _MODEL + r"\s+" + _FOLDER_REF + r"['’]s\s+default"
    + r"|(?:set|change|update|make|switch)\s+(?:the\s+)?(?:default(?:s)?|default\s+(?:model|tool|mode|approval(?:\s+level)?"
    r"|effort|reasoning(?:\s+level)?|permission(?:s)?))\s+(?:for|in|of|on)\s+" + _FOLDER_REF
    + r"|(?:set|change|update|switch)\s+" + _FOLDER_REF + r"['’]s\s+defaults?"
    + r"|(?:new\s+)?(?:coding\s+)?sessions?\s+(?:in|for)\s+" + _FOLDER_REF + r"\s+(?:should|will|must|to|always)\s+"
    r"(?:start|begin|open|run|use|be)"
    + r"|(?:start|open|begin)\s+(?:new\s+)?(?:coding\s+)?sessions?\s+(?:in|for)\s+" + _FOLDER_REF + r"\s+(?:with|on|in)\b"
    + r"|(?:in|for)\s+" + _FOLDER_REF + r"\s*,?\s+(?:use|start\s+with|default\s+to)\s+" + _MODEL + r"\b"
    # v1.442.0 (chat-first plan Wave 7, group A): the VIEW named as what
    # starts with the model — "Make Build › Code use Codex on my work account
    # in Full access for this repo" (the tool keeps the account out and
    # refuses Full access, each in its own words).
    + r"|(?:make|set|have|let|configure)\s+(?:build\s*(?:›|>|-)?\s*code|chat\s*(?:›|>)\s*code|the\s+code\s+view"
    r"|(?:my\s+|the\s+)?(?:new\s+)?(?:coding\s+)?sessions?)\s+(?:always\s+)?(?:use|start\s+with|default\s+to"
    r"|run(?:\s+on)?|open\s+(?:with|in))\s+" + _MODEL + r"\b[^\n]{0,120}?\b(?:for|in|on)\s+" + _FOLDER_REF
    # v1.448.0 (Wave A "chat configures everything", A1): the folder NAMED
    # between its determiner and its noun, and "always" / "by default" in
    # front of the model — "For the ClosePilot folder, always use Codex in
    # Auto mode", "in the Johnson project, by default use Claude Code".
    + r"|(?:in|for)\s+(?:" + _FOLDER_REF + r"|" + _NAMED_FOLDER_REF + r")\s*,?\s+(?:always\s+|by\s+default\s+)?"
    r"(?:use|start\s+with|default\s+to|run(?:\s+on)?|go\s+with|open\s+(?:with|in))\s+" + _MODEL + r"\b",
    re.IGNORECASE,
)
_DEFAULTS_CLEAR = re.compile(
    r"(?:clear|reset|remove|forget|drop|delete|undo|wipe)\s+(?:the\s+|its\s+)?(?:defaults?|default\s+\w+)\s+"
    r"(?:for|in|of|on)\s+" + _FOLDER_REF
    + r"|(?:clear|reset|remove|forget|drop|delete|wipe)\s+" + _FOLDER_REF + r"['’]s\s+defaults?"
    + r"|stop\s+using\s+" + _MODEL + r"\s+(?:by\s+default\s+)?(?:for|in|on)\s+" + _FOLDER_REF,
    re.IGNORECASE,
)
_WRITES = r"(?:writes|codes|builds|does\s+the\s+(?:code|coding|work|writing)|implements)"
_REVIEWS = r"(?:reviews|checks|reviews\s+it|does\s+the\s+review(?:ing)?|is\s+the\s+reviewer)"
_TEAM_PAIR = re.compile(
    r"\b" + _MODEL + r"\s+" + _WRITES + r"\b[^\n.!?]{0,60}?\b" + _MODEL + r"\s+" + _REVIEWS + r"\b"
    + r"|\b" + _MODEL + r"\s+" + _REVIEWS + r"\b[^\n.!?]{0,60}?\b" + _MODEL + r"\s+" + _WRITES + r"\b"
    + r"|\b(?:have|let)\s+" + _MODEL + r"\s+(?:write|code|build)\b[^\n.!?]{0,60}?\b" + _MODEL + r"\s+(?:review|check)\b",
    re.IGNORECASE,
)
_TEAM_SET = re.compile(
    r"(?:use|set\s*up|make|give|add|start|form|create)\s+(?:a\s+|the\s+|my\s+)?(?:coding\s+|model\s+)?team\b"
    r"[^\n]{0,120}?\b(?:for|in|on)\s+" + _FOLDER_REF
    + r"|(?:make|set)\s+" + _MODEL + r"\s+(?:the\s+)?(?:reviewer|worker|writer)\b[^\n]{0,80}?\b(?:for|in|on)\s+" + _FOLDER_REF
    + r"|(?:in|for)\s+" + _FOLDER_REF + r"\s*,?\s+(?:use\s+(?:a|the)\s+team|" + _MODEL + r"\s+" + _WRITES + r")\b"
    # v1.448.0 (Wave A "chat configures everything", A1): a REVIEWER asked
    # for the coding sessions without the word "team" — "Have my coding
    # sessions run with a reviewer that checks every change", "add a code
    # reviewer to my coding sessions", "have Claude review every change in
    # my coding sessions". The sessions must be the CODING ones (never "the
    # reviewer at the Times", never "have the reviewer check the sessions at
    # the conference"; _PEOPLE_TEAM still guards).
    + r"|(?:have|make|let|give|set\s+up|run|start)\s+(?:(?:my|the|all|every|each|new)\s+){0,3}(?:coding|code|build)\s+sessions?\b"
    r"[^\n]{0,40}?(?:\b(?:with|use|using|get|have|include|add|run\s+with)\s+)?(?:(?:a|an|the|its|their|my)\s+)?"
    r"(?:(?:code|second|separate|dedicated|automatic)\s+)?(?:reviewer|review\s+step|second\s+pair\s+of\s+eyes"
    r"|" + _MODEL + r"\s+(?:as\s+)?(?:the\s+)?(?:reviewer|reviewing))\b"
    + r"|(?:add|give|put|assign|attach)\s+(?:(?:a|an|the)\s+)?(?:code\s+)?reviewer\s+(?:to|on|for|into)\s+"
    r"(?:(?:my|the|all|every|each|new)\s+){0,3}(?:coding|code|build)\s+sessions?\b"
    + r"|(?:have|let|make)\s+" + _MODEL + r"\s+(?:review|check|look\s+over|vet)\s+(?:every|each|all|the|any)\s+"
    r"(?:changes?|edits?|diffs?|commits?|code)\b[^\n]{0,60}?\b(?:(?:coding|code|build)\s+sessions?|" + _FOLDER_REF + r")",
    re.IGNORECASE,
)
_TEAM_CLEAR = re.compile(
    r"(?:stop|quit)\s+using\s+(?:the|a|my)\s+(?:coding\s+|model\s+)?team\b(?:[^\n]{0,80}?\b(?:for|in|on)\s+" + _FOLDER_REF + r")?"
    + r"|(?:turn\s+off|remove|clear|drop|disable|get\s+rid\s+of|forget)\s+(?:the\s+)?(?:coding\s+)?team\s+(?:for|in|on|from)\s+"
    + _FOLDER_REF
    + r"|(?:no\s+(?:more\s+)?team|work\s+solo|go\s+solo)\s+(?:for|in|on)\s+" + _FOLDER_REF,
    re.IGNORECASE,
)
_FOLDER_SHOW = re.compile(
    r"(?:what|which|show|list|tell\s+me)\b[^\n]{0,60}?\b(?:defaults?|team|start\s+with|starts\s+with)\b[^\n]{0,60}?"
    + _FOLDER_REF
    + r"|(?:what|which)\s+(?:model|tool)\s+(?:does|do|will)\s+" + _FOLDER_REF + r"\s+(?:start|use)",
    re.IGNORECASE,
)
#: Someone else's team or a sports team ("my team at work", "the team
#: reviews the codebase every Friday" — a sentence about people).
_PEOPLE_TEAM = re.compile(r"\b(?:at\s+work|the\s+office|meeting|standup|stand-up|colleagues?|coworkers?)\b", re.IGNORECASE)


#: v1.438.0 review: a sentence comparing models ("Claude reviews and Codex
#: writes better than people think") is talk about them, never a team.
_COMPARE = re.compile(r"\b(?:better|worse|than|faster|slower|prefer|prefers|preferred)\b", re.IGNORECASE)


def _folder_request(text: str, clauses: list[str]) -> bool:
    if _TEAM_PAIR.search(text) is not None and _PEOPLE_TEAM.search(text) is None and _COMPARE.search(text) is None:
        return True
    for c in clauses:
        if _DEFAULTS_SET.match(c) or _DEFAULTS_CLEAR.match(c) or _TEAM_CLEAR.match(c):
            return True
        if _TEAM_SET.match(c) and _PEOPLE_TEAM.search(c) is None:
            return True
    return False


def build_tools_for(text: str) -> list[str]:
    """The tools of this module a chat message asks for (called ONCE by
    ``records.record_tools_for``). A request about tonight's list arms the
    night family; one about a folder's defaults or coding team, the folder
    family. A QUESTION keeps only the readers (``night_task_list``,
    ``build_folder_show``): "what's queued for tonight?" lists. Each writer is
    ask tier: armed visible, it renders the card. Never raises."""
    try:
        t = (text or "")[:4000]
        low = t.lower()
        out: list[str] = []
        nightish = any(w in low for w in ("tonight", "night", "overnight", "task", "queue", "sleep", "move", "bump"))
        folderish = any(
            w in low for w in ("repo", "folder", "codebase", "checkout", "workspace", ":\\", ":/", "team", "default", "session", "writes",
                "reviews", "review")
        )
        if not (nightish or folderish):
            return out
        # "Have it…" / "Have my coding sessions…" (v1.448.0) at the head of a
        # message with no "?" is an order, never the question "have my
        # sessions…?" that records.is_question reads it as.
        question = R.is_question(t) and not (
            (_HAVE_IT.match(t.strip()) or _HAVE_SESSIONS.match(t.strip())) and not t.rstrip().endswith("?")
        )
        clauses = _clauses(t)
        if nightish:
            asks_list = any(_NIGHT_LIST_ASK.match(c) for c in clauses) or (
                question and (_NIGHT_LIST.search(t) is not None or ("queued" in low and _NIGHT_RX.search(t) is not None))
            )
            if question:
                if asks_list:
                    out.append("night_task_list")
            elif any(_night_request(c) for c in clauses):
                out += ["night_task_add", "night_task_cancel", "night_task_move", "night_task_list"]
            elif asks_list:
                out.append("night_task_list")
        if folderish:
            shows = any(_FOLDER_SHOW.match(c) for c in clauses)
            if question:
                if shows:
                    out.append("build_folder_show")
            elif _folder_request(t, clauses):
                out += ["build_defaults_set", "build_defaults_clear", "build_team_set", "build_team_clear",
                        "build_folder_show"]
            elif shows:
                out.append("build_folder_show")
        return list(dict.fromkeys(out))
    except Exception:  # noqa: BLE001 — a matcher fault arms nothing new
        log.debug("night/folder arming failed", exc_info=True)
        return []


__all__ = [
    "FOLDER_TOOLS", "KINDS", "NIGHT_TOOLS", "READERS", "SNAP", "TIERS", "build_record_tools",
    "build_tools_for", "resolve_folder", "restore_kind",
]
