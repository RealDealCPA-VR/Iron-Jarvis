"""Projects and custom agents from chat (v1.435.0, chat-first plan Wave 3
group G, Part B2: "Make a project for the Johnson audit in D:\\Clients\\Johnson",
"rename…", "archive…", "switch to…"; "Create an agent called reviewer on GPT
that only reads", "give the builder agent a $20 monthly limit", "pause /
delete it").

Projects and agents were page-only: the Projects page made, renamed and
archived a project, and an agent could only be made through the Automation
agent's ``create_agent`` (no model, no team roles, no allowance). These eight
tools close that gap the way ``settings.records`` closes it for schedules and
goals:

* ``project_create``, ``project_update``, ``project_archive`` — THROUGH the
  Projects routes' own bodies (``daemon.routes.projects.create_project_record``
  / ``update_project_record``): the folder door is ``core.fs_policy.
  root_problem`` there and nowhere else, a project's default model is checked
  by the route's own ``_validate_project_model``, archiving the focused project
  takes the focus off it. A project's folder is a REFERENCE: no tool here makes,
  moves or deletes a file of the user's.
* ``project_focus`` — the "Focused project" setting (``active_project_id``)
  through THE settings writer (``settings.writer.ConfigWriter.apply``), whose
  own check refuses an unknown or archived project; its Undo is the writer's
  ``setting_restore`` row, exactly as a ``config_set`` of that key.
* ``agent_create``, ``agent_update``, ``agent_pause``, ``agent_delete`` —
  THROUGH the Agents routes' own bodies (``daemon.routes.agents.
  create_custom_agent`` / ``update_custom_agent`` / ``pause_custom_agent`` /
  ``resume_custom_agent`` / ``delete_custom_agent``): the employee fields are
  validated by ``employee_fields`` there, never here. Only an agent the user
  MADE can be changed; a built-in one is said to be built in.

Every tool asks first (ask tier — the ordinary approval card), and every
change has an Undo that puts back what THAT change did and nothing else (read
from the table; each tool records the row it found — ``prior`` — AND the row
it left — ``after`` — so the Undo knows which fields it touched): a changed
one gets those fields back (the focus too, only when the change took it); a
field the user changed AGAIN since, or a record removed or made again since,
refuses the Undo in one sentence rather than write over the user's newer work
(review, Wave 3 group G). A removed agent comes back with its own id and its
FOLDER — instructions, notes, earlier versions — moved back out of the trash
(``agent_delete`` records which trash folder the route moved it to); if that
folder is no longer in the trash, Undo says so and writes back the
instructions and notes the snapshot kept; it never claims more than it did. A
made agent edited since is not removed by its Undo. A made project is removed
only while it is exactly as made and nothing is filed in or bound to it (ANY
table carrying its id, or a schedule's payload); otherwise its Undo ARCHIVES
it and says so. A folder spelled as a network share is refused by its text
before anything touches the disk.

Refusals are ONE plain sentence (the route's own reason, worded for a person —
no field names). Arming (:func:`projects_tools_for`) reads the sentence the
user typed: a request to make, change, archive, focus, pause or remove a
project or an agent arms that family; an everyday sentence ("my project is due
Friday", "the agent at the bank said…", "create a project plan", "my insurance
agent") arms nothing, and ``records.record_tools_for`` drops every writer for
a question. Every pattern is LINEAR: the message is cut into clauses by plain
splits and each rule is an ANCHORED match with bounded repeats
(``tests/test_records_projects_v14350.py`` pins pathological input in a child
process with a deadline).

Snapshots and restores are BLOCKING (SQLite, the folder probe, a folder move);
the tools run them through ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any

from ..core.ids import new_id
from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from . import records as R

log = logging.getLogger("ironjarvis.settings")

PROJECT_TOOLS: tuple[str, ...] = ("project_create", "project_update", "project_archive", "project_focus")
AGENT_TOOLS: tuple[str, ...] = ("agent_create", "agent_update", "agent_pause", "agent_delete")

#: A tool needs the running app (the route bodies take its deps object).
NEEDS_THE_APP = "This can only be done while the Iron Jarvis app is running."
#: The kinds of agent a made agent can work like (the route refuses
#: "supervisor" — it would discard the agent's own instructions).
WORKS_LIKE: tuple[str, ...] = ("builder", "reviewer", "researcher", "planner", "memory", "automation", "guide", "maintainer")
#: What a made agent's "works like" means, for the model.
WORKS_LIKE_WORDS = (
    "reviewer only reads files and documents; builder reads and changes files; "
    "researcher reads and searches the web; planner plans and hands out work"
)

#: The Undo descriptor THIS call captured (review: every tool here records
#: what its change LEFT — ``after`` — beside what it found — ``prior`` — so the
#: Undo puts back only the fields this change touched, and refuses when the
#: user changed one of them again since). Set by ``capture_undo``, consumed by
#: :func:`_note` after a successful write; a direct ``execute`` (no capture)
#: finds ``None`` and journals nothing.
_DESC: contextvars.ContextVar["dict[str, Any] | None"] = contextvars.ContextVar(
    "ij_project_agent_desc", default=None
)


def _stash(desc: "dict[str, Any] | None") -> "dict[str, Any] | None":
    _DESC.set(desc)
    return desc


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


def _network_folder(text: Any) -> bool:
    """A folder spelled as a network share or a device path (``\\\\host\\x``,
    ``//host/x``, ``\\\\?\\…``) — judged on the TEXT, before anything touches
    the disk: looking at a share is a connection to that computer, which
    sends this PC's sign-in to it (the v1.379.0 lesson)."""
    t = str(text or "").strip().replace("/", "\\")
    return t.startswith("\\\\")


#: The refusal for a network folder (review, Wave 3 group G).
NETWORK_FOLDER = (
    "That folder is a network location on another computer; a project's folder must be on this PC "
    "(give its full location starting with its drive, for example D:\\Clients\\Johnson)."
)


# --------------------------------------------------------------------------- #
# words
# --------------------------------------------------------------------------- #


def _sentence(text: Any) -> str:
    """One plain sentence: trimmed, a capital first letter, a full stop."""
    s = " ".join(str(text or "").split())
    if not s:
        return "That could not be done."
    s = s[0].upper() + s[1:]
    return s if s[-1] in ".!?" else s + "."


def _q(text: Any) -> str:
    return "“" + " ".join(str(text or "").split())[:80] + "”"


def _route_words(exc: Any, *, what: str = "") -> str:
    """The route's own refusal, worded for a person (no field names)."""
    detail = str(getattr(exc, "detail", exc) or "")
    low = detail.lower()
    if getattr(exc, "status_code", None) == 409 and "already exists" in low:
        return f"There is already an agent called {_q(what)}; ask to change it instead."
    if low == "project name is required":
        return "A project needs a name."
    if low == "name is required":
        return "An agent needs a name."
    if low == "folder must be an absolute path":
        return "Give the folder's full location, starting with its drive (for example D:\\Clients\\Johnson)."
    if low.startswith("unknown provider '"):
        return _sentence(detail.replace("unknown provider", "Iron Jarvis has no model service called", 1)
                         .replace(" — pick one from the model list", "; pick one from the model list"))
    if low == "unarchive the project first":
        return f"{_q(what) if what else 'That project'} is archived; unarchive it first."
    if low == "no such project":
        return f"There is no project {_q(what)}." if what else "That project is no longer there."
    if low == "unknown agent":
        return f"There is no agent {_q(what)} that you made." if what else "That agent is no longer there."
    for field, words in (
        ("base_type", "the kind of agent"),
        ("allowance_tokens", "the monthly token limit"),
        ("allowance_usd", "the monthly dollar limit"),
        ("approval_mode", "the approval setting"),
        ("reports_to", "who it reports to"),
        ("roles must", "team roles must"),
    ):
        detail = detail.replace(field, words)
    return _sentence(detail)


def _d(platform: Any) -> Any:
    return R._d(platform)  # noqa: SLF001 — the records module's own deps lookup


# --------------------------------------------------------------------------- #
# projects
# --------------------------------------------------------------------------- #


def _project_rows(platform: Any) -> list[Any]:
    from sqlmodel import select

    from ..core.db import session_scope
    from ..core.models import Project

    with session_scope(platform.engine) as db:
        return list(db.exec(select(Project)))


def resolve_project(platform: Any, ref: str) -> tuple[str, str]:
    """``(project id, "")`` for a project named by its id or its exact name
    (case-insensitive, unique), else ``("", one plain sentence)``. Blocking."""
    from ..core.db import session_scope
    from ..core.models import Project

    ref = " ".join(str(ref or "").split())
    if not ref:
        return "", "Say which project."
    with session_scope(platform.engine) as db:
        if db.get(Project, ref) is not None:
            return ref, ""
    rows = _project_rows(platform)
    hits = [p for p in rows if (p.name or "").strip().casefold() == ref.casefold()]
    if len(hits) == 1:
        return hits[0].id, ""
    if len(hits) > 1:
        return "", f"More than one project is called {_q(ref)}; rename one on the Projects page first."
    names = [f"{_q(p.name)}{' (archived)' if p.status == 'archived' else ''}" for p in rows][:12]
    if not names:
        return "", f"There is no project {_q(ref)}; you have no projects yet."
    return "", f"There is no project {_q(ref)}. Your projects: {', '.join(names)}."


def snap_project(platform: Any, project_id: str) -> dict[str, Any] | None:
    """Every column of the project as it is, plus whether it is the focused
    one. Blocking."""
    from ..core.db import session_scope
    from ..core.models import Project

    with session_scope(platform.engine) as db:
        row = db.get(Project, project_id)
        if row is None:
            return None
        out = json.loads(json.dumps(row.model_dump(), default=R._iso))  # noqa: SLF001
    out["focused"] = getattr(platform.config, "active_project_id", None) == project_id
    return out


def _has_work(platform: Any, project_id: str) -> bool:
    """Has anything been filed in or bound to the project — a chat, a run,
    knowledge, a workflow pin or run, a goal, a rule, a project room, an
    assignment, a made file, a task schedule? EVERY table that carries a
    ``project_id`` is asked (review: the first cut asked three, so an Undo of
    a made project deleted it and silently unbound a schedule, a goal or a
    rule the user had filed in it since — ``delete_project_record`` untags
    them), plus the schedules, which carry it inside their payload. A table
    that cannot be read counts as holding something (fail-closed: the
    project is archived, never removed). Blocking."""
    from sqlalchemy import select as sa_select
    from sqlalchemy.exc import OperationalError
    from sqlmodel import SQLModel, select

    from ..core.db import session_scope

    with session_scope(platform.engine) as db:
        for table in list(SQLModel.metadata.sorted_tables):
            if table.name == "project" or "project_id" not in table.c:
                continue
            try:
                hit = db.exec(sa_select(table.c.project_id).where(table.c.project_id == project_id).limit(1)).first()
            except OperationalError:
                db.rollback()
                continue  # a table this build never made holds nothing
            except Exception:  # noqa: BLE001 — unreadable: keep the project
                return True
            if hit is not None:
                return True
        try:
            from ..scheduling.models import ScheduledTaskRecord

            for sched in db.exec(select(ScheduledTaskRecord)):
                if sched.decoded_payload().get("project_id") == project_id:
                    return True
        except OperationalError:
            pass
        except Exception:  # noqa: BLE001
            return True
    return False


def _set_focus(platform: Any, project_id: str) -> None:
    """Put the focus on ``project_id`` ("" = none) through THE settings writer
    (its own check refuses an archived project), unjournaled — an Undo's own
    row records the reversal."""
    writer = getattr(platform, "config_writer", None)
    if writer is None:
        return
    try:
        writer.apply({"active_project_id": project_id}, actor="undo", journal=False)
    except Exception:  # noqa: BLE001 — a focus that cannot come back stays off
        log.info("undo: the focus could not be put back on %s", project_id)


#: A project's columns an Undo may put back (``id`` and ``created_at``
#: never change), and what each is called in a sentence.
_PROJECT_WORDS = {
    "name": "its name",
    "root": "its folder",
    "brief": "its description",
    "instructions": "its instructions",
    "default_provider": "its default model",
    "default_model": "its default model",
    "memory_sources": "its memory bases",
    "status": "whether it is archived",
    "team_json": "its team",
    "brain_profile": "its memory profile",
}


def _project_cols() -> list[str]:
    from ..core.models import Project

    return [k for k in Project.model_fields if k not in ("id", "created_at")]


def _words_for(keys: list[str], table: dict[str, str]) -> str:
    out: list[str] = []
    for k in keys:
        w = table.get(k, k.replace("_", " "))
        if w not in out:
            out.append(w)
    return ", ".join(out)


def restore_project(
    platform: Any, project_id: str, prior: dict[str, Any] | None, desc: "dict[str, Any] | None" = None
) -> str:
    """``POST /undo`` for a project change. It puts back what THIS change did
    and nothing else (review, Wave 3 group G):

    * A MADE project is removed through the route's own delete only while
      it is exactly as the create left it and nothing is filed in or bound to
      it; otherwise it is ARCHIVED (its chats, runs, schedules and the user's
      later edits are kept) and the answer says so.
    * A changed or archived one gets back ONLY the fields the change touched
      (``prior`` vs the ``after`` the tool recorded). A field the user changed
      AGAIN since refuses the whole Undo in words (never written over); a
      project removed since is never brought back. The focus comes back only
      when THIS change took it; restoring "archived" takes the focus off it
      (the route's rule).

    Blocking."""
    from ..core.db import session_scope
    from ..core.models import Project
    from ..daemon.routes.projects import delete_project_record, update_project_record
    from ..daemon.schemas import ProjectPatch

    meta = desc if isinstance(desc, dict) else {}
    after_known = "after" in meta
    after = meta.get("after") if isinstance(meta.get("after"), dict) else None
    d = _d(platform)
    current = snap_project(platform, project_id)
    name = str((current or prior or {}).get("name") or project_id)
    cols = _project_cols()
    if prior is None:
        if current is None:
            return "undo: the project was already gone"
        if d is None:
            raise R.RecordError(NEEDS_THE_APP)
        changed = bool(after) and any(current.get(k) != after.get(k) for k in cols)
        if changed or _has_work(platform, project_id):
            if current.get("status") != "archived":
                update_project_record(d, project_id, ProjectPatch(status="archived"))
            why = "it was changed after it was made" if changed else "work was already filed in it"
            return f"undo: archived the project '{name}' ({why}, so it was kept)"
        delete_project_record(d, project_id)
        return f"undo: removed the project '{name}'"
    if current is None:
        if after_known:
            raise R.RecordError(
                f"The project “{name}” was removed after this change, so there is nothing to put back."
            )
        current = {}
    if after_known and after is not None:
        touched = [k for k in cols if prior.get(k) != after.get(k)]
        again = [k for k in touched if current.get(k) != after.get(k) and current.get(k) != prior.get(k)]
        if again:
            raise R.RecordError(
                f"The project “{name}” was changed again after this change ({_words_for(again, _PROJECT_WORDS)}), "
                "so Undo would undo that too; change it on the Projects page instead."
            )
        took_focus = bool(prior.get("focused")) and not after.get("focused")
    else:
        touched = [k for k in cols if k in prior]
        took_focus = bool(prior.get("focused"))
    todo = [k for k in touched if current.get(k) != prior.get(k)]
    focus = getattr(platform.config, "active_project_id", None)
    give_focus = took_focus and not focus
    if not todo and not give_focus:
        return f"undo: the project '{name}' was already back as it was"
    if todo:
        with session_scope(platform.engine) as db:
            row = db.get(Project, project_id)
            if row is None:
                row = Project(id=project_id, name=str(prior.get("name") or ""))
                created = R._dt(prior.get("created_at"))  # noqa: SLF001
                if created is not None:
                    row.created_at = created
            for key in todo:
                setattr(row, key, prior.get(key))
            db.add(row)
            db.commit()
    focus = getattr(platform.config, "active_project_id", None)
    if give_focus and not focus:
        _set_focus(platform, project_id)
    elif "status" in todo and prior.get("status") == "archived" and focus == project_id:
        _set_focus(platform, "")
    return f"undo: restored the project '{prior.get('name') or name}'"


def _model_words(provider: Any, model: Any) -> str:
    p, m = str(provider or "").strip(), str(model or "").strip()
    return " ".join(x for x in (m, f"({p})" if p and m else p) if x) or "the app's default"


def _describe_project(s: dict[str, Any] | None) -> str | None:
    if s is None:
        return None
    bits = [f"folder {s['root']}" if s.get("root") else "no folder"]
    if s.get("default_provider") or s.get("default_model"):
        bits.append(f"default model {_model_words(s.get('default_provider'), s.get('default_model'))}")
    if s.get("status") == "archived":
        bits.append("archived")
    if s.get("focused"):
        bits.append("focused")
    return ", ".join(bits)


def _project_change_words(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, str]:
    old: list[str] = []
    new: list[str] = []
    if before.get("name") != after.get("name"):
        old.append(f"named {_q(before.get('name'))}")
        new.append(f"named {_q(after.get('name'))}")
    if before.get("root") != after.get("root"):
        old.append(f"folder {before.get('root') or 'none'}")
        new.append(f"folder {after.get('root') or 'none'}")
    if before.get("brief") != after.get("brief"):
        old.append(f"description {_q(before.get('brief')) if before.get('brief') else 'none'}")
        new.append(f"description {_q(after.get('brief')) if after.get('brief') else 'none'}")
    if (before.get("default_provider"), before.get("default_model")) != (after.get("default_provider"), after.get("default_model")):
        old.append(f"default model {_model_words(before.get('default_provider'), before.get('default_model'))}")
        new.append(f"default model {_model_words(after.get('default_provider'), after.get('default_model'))}")
    return ", ".join(old) or "unchanged", ", ".join(new) or "unchanged"


class _ProjectTool(R._RecordTool):  # noqa: SLF001 — the records module's own base
    RECORD = "project"

    def _target(self, args: dict[str, Any]) -> str:
        return " ".join(str(args.get("project") or "").split())

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_project(self.platform, self._target(args))[0]

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _DESC.set(None)
        return _stash(await super().capture_undo(args, ctx))


class ProjectCreateTool(_ProjectTool):
    name = "project_create"
    description = (
        "Make a new project (the Projects page's New project): `name`, optionally the "
        "`folder` its files live in on this PC (a full path that already exists, such as "
        "D:\\Clients\\Johnson — nothing is created or moved there) and a short `description`. "
        "The first project made becomes the focused one. The user approves on a card; Undo "
        "takes the project away again."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "folder": {"type": "string", "description": "A full path that already exists; leave out for none."},
            "description": {"type": "string"},
        },
        "required": ["name"],
    }

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        # The store mints the id: captured "pending", named after the write.
        _DESC.set(None)
        return _stash(await R.capture_create_undo(self.platform, "project", "", pending=True))

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException

        from ..daemon.routes.projects import create_project_record
        from ..daemon.schemas import ProjectCreate

        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        name = " ".join(str(args.get("name") or "").split())
        if _network_folder(args.get("folder")):
            return ToolResult(ok=False, error=NETWORK_FOLDER)
        body = ProjectCreate(
            name=name, root=str(args.get("folder") or "").strip(), brief=str(args.get("description") or "").strip()
        )
        try:
            out = await asyncio.to_thread(create_project_record, d, body)
        except HTTPException as exc:
            return ToolResult(ok=False, error=_route_words(exc, what=name))
        pid = str(out.get("id") or "")
        R.bind_created(pid)
        after = await asyncio.to_thread(snap_project, self.platform, pid)
        _note(after=after)
        where = f" in {out['root']}" if out.get("root") else " with no folder"
        focus = " It is now the focused project, so new chats are filed in it." if out.get("active") else ""
        card = R.create_card("project", pid, _describe_project(after), label=f"Project {_q(name)}")
        data: dict[str, Any] = {"project_id": pid, "name": name}
        if card:
            data["record_change"] = card
        return ToolResult(ok=True, output=f"Made the project {_q(name)}{where}.{focus}", data=data)


class ProjectUpdateTool(_ProjectTool):
    name = "project_update"
    description = (
        "Change a project (`project` = its name): rename it (`new_name`), point it at another "
        "`folder` on this PC (a full path that already exists; \"\" for none — no file is moved), "
        "change its `description`, or its default model (`provider` and `model`, as the model "
        "list names them; \"\" for the app's default). Only the fields given change. The user "
        "approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project": {"type": "string"},
            "new_name": {"type": "string"},
            "folder": {"type": "string"},
            "description": {"type": "string"},
            "provider": {"type": "string"},
            "model": {"type": "string"},
        },
        "required": ["project"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException

        from ..daemon.routes.projects import update_project_record
        from ..daemon.schemas import ProjectPatch

        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        pid, problem = await asyncio.to_thread(resolve_project, self.platform, self._target(args))
        if not pid:
            return ToolResult(ok=False, error=problem)
        before = await asyncio.to_thread(snap_project, self.platform, pid)
        if before is None:
            return ToolResult(ok=False, error="That project is no longer there.")
        fields: dict[str, Any] = {}
        if args.get("new_name") is not None:
            new_name = " ".join(str(args.get("new_name") or "").split())
            if not new_name:
                return ToolResult(ok=False, error="A project needs a name.")
            fields["name"] = new_name
        if _network_folder(args.get("folder")):
            return ToolResult(ok=False, error=NETWORK_FOLDER)
        for arg, field in (("folder", "root"), ("description", "brief"), ("provider", "default_provider"), ("model", "default_model")):
            if args.get(arg) is not None:
                fields[field] = str(args.get(arg) or "")
        if not fields:
            return ToolResult(ok=False, error="Say what to change: its name, folder, description or default model.")
        try:
            await asyncio.to_thread(update_project_record, d, pid, ProjectPatch(**fields))
        except HTTPException as exc:
            return ToolResult(ok=False, error=_route_words(exc, what=before.get("name") or ""))
        after = await asyncio.to_thread(snap_project, self.platform, pid) or {}
        _note(after=after)
        old, new = _project_change_words(before, after)
        label = f"Project {_q(before.get('name'))}"
        return ToolResult(
            ok=True,
            output=f"Changed the project {_q(before.get('name'))}: {old} → {new}.",
            data={"record_change": self._card("Project changed", label, old, new, f"project.{pid}")},
        )


class ProjectArchiveTool(_ProjectTool):
    name = "project_archive"
    description = (
        "Archive a project (`project` = its name) so it takes no new work, or bring an archived "
        "one back with `archive` false. Archiving the focused project takes the focus off it. "
        "Nothing on disk changes. The user approves on a card; Undo puts it back as it was."
    )
    input_schema = {
        "type": "object",
        "properties": {"project": {"type": "string"}, "archive": {"type": "boolean"}},
        "required": ["project"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException

        from ..daemon.routes.projects import update_project_record
        from ..daemon.schemas import ProjectPatch

        d = _d(self.platform)
        if d is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        archive = args.get("archive") is not False
        pid, problem = await asyncio.to_thread(resolve_project, self.platform, self._target(args))
        if not pid:
            return ToolResult(ok=False, error=problem)
        before = await asyncio.to_thread(snap_project, self.platform, pid)
        if before is None:
            return ToolResult(ok=False, error="That project is no longer there.")
        want = "archived" if archive else "active"
        if before.get("status") == want:
            return ToolResult(
                ok=False, error=f"The project {_q(before.get('name'))} is already {'archived' if archive else 'in use'}."
            )
        try:
            await asyncio.to_thread(update_project_record, d, pid, ProjectPatch(status=want))
        except HTTPException as exc:
            return ToolResult(ok=False, error=_route_words(exc, what=before.get("name") or ""))
        after = await asyncio.to_thread(snap_project, self.platform, pid) or {}
        _note(after=after)
        old = "archived" if before.get("status") == "archived" else ("in use, focused" if before.get("focused") else "in use")
        new = "archived" if after.get("status") == "archived" else "in use"
        unfocused = " It is no longer the focused project." if before.get("focused") and not after.get("focused") else ""
        verb = "Archived" if archive else "Brought back"
        return ToolResult(
            ok=True,
            output=f"{verb} the project {_q(before.get('name'))}.{unfocused}",
            data={
                "record_change": self._card(
                    "Project archived" if archive else "Project brought back",
                    f"Project {_q(before.get('name'))}", old, new, f"project.{pid}",
                )
            },
        )


_NO_PROJECT = re.compile(r"^(?:none|no project|nothing|off|clear|no focus|-)?$", re.IGNORECASE)


class ProjectFocusTool(Tool):
    """Switch the FOCUSED project — the "Focused project" setting
    (``active_project_id``) through THE settings writer, whose own check
    refuses an unknown or archived project (v1.433.0, A3). Its Undo is the
    writer's ``setting_restore`` row, the same one ``config_set`` leaves."""

    name = "project_focus"
    description = (
        "Switch the focused project (`project` = its name): the project new chats are filed "
        "in and grounded on. \"none\" stops focusing on any project. The user approves on a "
        "card; the change has an Undo."
    )
    input_schema = {"type": "object", "properties": {"project": {"type": "string"}}, "required": ["project"]}
    reversibility = Reversibility.REVERSIBLE

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        R._CHANGE_ID.set("")  # noqa: SLF001 — never a previous call's id on this card
        writer = getattr(self.platform, "config_writer", None)
        if writer is None:
            return None
        try:
            prior = await asyncio.to_thread(writer.current, "active_project_id")
        except Exception:  # noqa: BLE001 — no snapshot, no undo row
            return None
        change_id = new_id("rec")
        R._CHANGE_ID.set(change_id)  # noqa: SLF001
        return {
            "kind": "setting_restore",
            "pre_inline": json.dumps({"prior": {"active_project_id": prior}, "device_id": "", "change_id": change_id}, default=str),
        }

    def _name_of(self, project_id: Any) -> str:
        if not project_id:
            return "none"
        snap = snap_project(self.platform, str(project_id))
        return _q(snap["name"]) if snap else "none"

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from .writer import SettingError

        writer = getattr(self.platform, "config_writer", None)
        if writer is None:
            return ToolResult(ok=False, error=NEEDS_THE_APP)
        ref = " ".join(str(args.get("project") or "").split())
        if _NO_PROJECT.match(ref):
            pid = ""
        else:
            pid, problem = await asyncio.to_thread(resolve_project, self.platform, ref)
            if not pid:
                return ToolResult(ok=False, error=problem)
        current = getattr(self.platform.config, "active_project_id", None) or ""
        if pid == current:
            return ToolResult(
                ok=False,
                error="No project is focused already." if not pid else f"{await asyncio.to_thread(self._name_of, pid)} is already the focused project.",
            )
        old = await asyncio.to_thread(self._name_of, current)
        try:
            await asyncio.to_thread(writer.apply, {"active_project_id": pid}, actor="chat", journal=False)
        except SettingError as exc:
            words = str(exc).split(": ", 1)[-1].rstrip(".")
            return ToolResult(ok=False, error=_route_words(words, what=ref))
        new = await asyncio.to_thread(self._name_of, pid)
        out = f"New chats are no longer filed in a project (it was {old})." if not pid else (
            f"{new} is now the focused project: new chats are filed in it."
        )
        return ToolResult(
            ok=True,
            output=out,
            data={
                "record_change": {
                    "title": "Focused project changed",
                    "key": "active_project_id",
                    "label": "Focused project",
                    "old": old,
                    "new": new,
                    "change_id": R._CHANGE_ID.get(),  # noqa: SLF001
                }
            },
        )


# --------------------------------------------------------------------------- #
# custom agents
# --------------------------------------------------------------------------- #


def _builtin_names() -> set[str]:
    from ..core.models import AgentType

    return {t.value for t in AgentType}


def resolve_agent(platform: Any, ref: str) -> tuple[str, str]:
    """``(name, "")`` for an agent the user made, named exactly or in other
    capitals (``custom:<name>`` too), else ``("", one plain sentence)`` —
    a built-in agent is said to be built in. Blocking."""
    raw = " ".join(str(ref or "").split())
    if raw.lower().startswith("custom:"):
        raw = raw[len("custom:"):].strip()
    bare = re.sub(r"^(?:the|my|our)\s+", "", raw, flags=re.IGNORECASE)
    bare = re.sub(r"\s+agent$", "", bare, flags=re.IGNORECASE).strip() or raw
    if not raw:
        return "", "Say which agent."
    reg = platform.agents_registry
    for cand in (raw, bare):
        if reg.get(cand) is not None:
            return cand, ""
    names = [r.name for r in reg.list()]
    for cand in (raw, bare):
        hits = [n for n in names if n.casefold() == cand.casefold()]
        if len(hits) == 1:
            return hits[0], ""
    if bare.casefold() in _builtin_names():
        return "", (
            f"The {bare.lower()} is one of Iron Jarvis's own agents; only agents you made can be "
            "changed, paused or removed."
        )
    if not names:
        return "", f"There is no agent {_q(raw)} that you made; you have not made any agents yet."
    return "", f"There is no agent {_q(raw)} that you made. Your agents: {', '.join(_q(n) for n in names[:12])}."


def _agent_row(platform: Any, name: str) -> Any:
    from sqlmodel import select

    from ..agents.dynamic_models import DynamicAgentRecord
    from ..core.db import session_scope

    with session_scope(platform.engine) as db:
        return db.exec(select(DynamicAgentRecord).where(DynamicAgentRecord.name == name)).first()


def snap_agent(platform: Any, name: str) -> dict[str, Any] | None:
    """Every column of the agent's row as stored (read from the table, never
    the registry's normalised copy), plus its notebook (``NOTES.md``, at most
    16 KiB) — written back only when the folder itself cannot come back.
    Blocking."""
    row = _agent_row(platform, name)
    if row is None:
        return None
    out = json.loads(json.dumps(row.model_dump(), default=R._iso))  # noqa: SLF001
    files = getattr(platform.agents_registry, "files", None)
    try:
        out["notes"] = files.read_notes(name) if files is not None else ""
    except Exception:  # noqa: BLE001
        out["notes"] = ""
    return out


def _trash_folders(platform: Any, name: str) -> set[str]:
    """The trash folders ``AgentFiles.remove`` could have moved ``name``'s
    folder to (``<home>/trash/agents-<slug>-<stamp>[-n]``)."""
    from ..agents.files import TRASH_DIRNAME, agent_slug

    files = getattr(platform.agents_registry, "files", None)
    if files is None:
        return set()
    trash = Path(files.home) / TRASH_DIRNAME
    if not trash.is_dir():
        return set()
    prefix = f"agents-{agent_slug(name)}-"
    return {str(p) for p in trash.iterdir() if p.name.startswith(prefix) and p.is_dir()}


def _move_back(platform: Any, name: str, trashed: str) -> bool:
    """Move ``name``'s folder back out of the trash. Only a folder directly in
    ``<home>/trash`` named for THIS agent, never a link, and only when no
    folder of that name is live again. Never raises."""
    from ..agents.files import TRASH_DIRNAME, agent_slug

    files = getattr(platform.agents_registry, "files", None)
    if files is None or not trashed:
        return False
    try:
        src = Path(trashed)
        trash = (Path(files.home) / TRASH_DIRNAME).resolve()
        live = files.folder(name)
        if (
            src.is_symlink()
            or not src.is_dir()
            or src.resolve().parent != trash
            or not src.name.startswith(f"agents-{agent_slug(name)}-")
            or live.exists()
        ):
            return False
        shutil.move(str(src), str(live))
        return True
    except Exception:  # noqa: BLE001 — the row comes back; the folder is said not to
        log.warning("undo: could not move %r's folder back out of the trash", name, exc_info=True)
        return False


#: An agent's columns an Undo may put back (``name`` is the key, ``id`` and
#: ``created_at`` never change on a live row), and what each is called.
_AGENT_WORDS = {
    "system_prompt": "its instructions",
    "tools_json": "its tools",
    "base_type": "what it works like",
    "description": "its description",
    "provider": "its model",
    "model": "its model",
    "approval_mode": "its approval setting",
    "max_steps": "its step limit",
    "allowance_tokens": "its monthly limit",
    "allowance_usd": "its monthly limit",
    "allowance_warned_month": "its monthly limit warning",
    "paused_reason": "whether it is paused",
    "paused_at": "whether it is paused",
    "reports_to": "who it reports to",
    "skills_json": "its skills",
    "deny_tools_json": "the tools it may never use",
    "roles_json": "its team roles",
    "account": "its account",
}
#: The columns the USER sets (the job card): a made agent whose card differs
#: from what the create left was edited since, so its Undo keeps it.
_AGENT_CARD = (
    "system_prompt", "tools_json", "base_type", "description", "provider", "model", "approval_mode",
    "max_steps", "allowance_tokens", "allowance_usd", "reports_to", "skills_json", "deny_tools_json",
    "roles_json", "account",
)


def _agent_cols() -> list[str]:
    from ..agents.dynamic_models import DynamicAgentRecord

    return [k for k in DynamicAgentRecord.model_fields if k not in ("name", "id", "created_at")]


def _write_agent(platform: Any, name: str, values: dict[str, Any], *, recreate_from: "dict[str, Any] | None" = None) -> None:
    """Write ``values`` onto the agent's row (or make the row again from
    ``recreate_from``, with its own id and created time) and refresh the
    registry's cached copy. Blocking."""
    from sqlmodel import select

    from ..agents.dynamic_models import DynamicAgentRecord
    from ..core.db import session_scope

    with session_scope(platform.engine) as db:
        row = db.exec(select(DynamicAgentRecord).where(DynamicAgentRecord.name == name)).first()
        if row is None:
            row = DynamicAgentRecord(name=name)
            if recreate_from is not None:
                if recreate_from.get("id"):
                    row.id = str(recreate_from["id"])
                created = R._dt(recreate_from.get("created_at"))  # noqa: SLF001
                if created is not None:
                    row.created_at = created
        for key, value in values.items():
            if key == "paused_at":
                value = R._dt(value)  # noqa: SLF001
            setattr(row, key, value)
        db.add(row)
        db.commit()
    # The registry's cached copy follows the row (a no-op write refreshes it).
    platform.agents_registry._update(name, lambda _row: None)  # noqa: SLF001 — the registry's own refresh


def restore_agent(
    platform: Any, name: str, prior: dict[str, Any] | None, desc: "dict[str, Any] | None" = None
) -> str:
    """``POST /undo`` for an agent change. It puts back what THIS change did
    and nothing else (review, Wave 3 group G):

    * A MADE agent is removed (its folder to the trash, as the Agents page's
      Delete does) only while its job card is exactly what the create left;
      edited since, the Undo refuses in words (the user's edits are kept).
    * A REMOVED one comes back with its own id and every column, and its
      folder out of the trash — unless an agent of that name was made since,
      which is never written over. If the folder is no longer in the trash,
      the instructions and notes the snapshot kept are written back and the
      answer says what did not come back.
    * A changed or paused one gets back ONLY the fields the change touched
      (``prior`` vs the ``after`` the tool recorded); a field the user changed
      AGAIN since refuses the whole Undo; an agent removed since is never
      brought back.

    Blocking."""
    meta = desc if isinstance(desc, dict) else {}
    after_known = "after" in meta
    after = meta.get("after") if isinstance(meta.get("after"), dict) else None
    reg = platform.agents_registry
    files = getattr(reg, "files", None)
    current = snap_agent(platform, name)
    if prior is None:
        if current is None:
            return "undo: the agent was already gone"
        if after is not None:
            edited = [k for k in _AGENT_CARD if current.get(k) != after.get(k)]
            if current.get("id") != after.get("id") or edited:
                what = _words_for(edited, _AGENT_WORDS) if current.get("id") == after.get("id") else "it was removed and made again"
                raise R.RecordError(
                    f"The agent “{name}” was changed after it was made ({what}), so Undo would take those "
                    "changes with it; remove it on the Agents page if you still want it gone."
                )
        reg.remove(name)
        return f"undo: removed the agent '{name}' (its folder is in the trash)"
    cols = _agent_cols()
    if after_known and after is None:
        # The change REMOVED the agent: bring it back.
        if current is not None:
            if current.get("id") == prior.get("id"):
                return f"undo: the agent '{name}' was already back"
            raise R.RecordError(
                f"A new agent called “{name}” was made since this removal, so Undo would overwrite it; "
                "rename or remove that one on the Agents page first."
            )
        return _bring_back(platform, name, prior, str(meta.get("trash") or prior.get("trash") or ""), cols)
    if current is None:
        if after_known:
            raise R.RecordError(f"The agent “{name}” was removed after this change, so there is nothing to put back.")
        return _bring_back(platform, name, prior, str(prior.get("trash") or ""), cols)
    if after is not None:
        touched = [k for k in cols if prior.get(k) != after.get(k)]
        again = [k for k in touched if current.get(k) != after.get(k) and current.get(k) != prior.get(k)]
        if again:
            raise R.RecordError(
                f"The agent “{name}” was changed again after this change ({_words_for(again, _AGENT_WORDS)}), "
                "so Undo would undo that too; change it on the Agents page instead."
            )
    else:
        touched = [k for k in cols if k in prior]
    todo = [k for k in touched if current.get(k) != prior.get(k)]
    if not todo:
        return f"undo: the agent '{name}' was already back as it was"
    _write_agent(platform, name, {k: prior.get(k) for k in todo})
    if "system_prompt" in todo and files is not None:
        try:
            files.write_instructions(name, str(prior.get("system_prompt") or ""), reason="undo")
        except Exception:  # noqa: BLE001
            log.warning("undo: could not write %r's instructions", name, exc_info=True)
    return f"undo: restored the agent '{name}'"


def _bring_back(platform: Any, name: str, prior: dict[str, Any], trashed: str, cols: list[str]) -> str:
    """Make a removed agent's row again — its own id, created time and every
    column — and move its folder back out of the trash. Blocking."""
    _write_agent(platform, name, {k: prior.get(k) for k in cols if k in prior}, recreate_from=prior)
    files = getattr(platform.agents_registry, "files", None)
    if _move_back(platform, name, trashed):
        return f"undo: brought back the agent '{name}' with its folder (instructions, notes and earlier versions)"
    if files is None:
        return f"undo: brought back the agent '{name}'"
    try:
        files.write_instructions(name, str(prior.get("system_prompt") or ""), reason="undo")
        if prior.get("notes"):
            files.write_notes(name, str(prior["notes"]))
    except Exception:  # noqa: BLE001
        log.warning("undo: could not write %r's folder", name, exc_info=True)
    return (
        f"undo: brought back the agent '{name}' with its instructions and notes; its folder was no "
        "longer in the trash, so earlier versions of its instructions did not come back"
    )


def _roles(a: dict[str, Any]) -> list[str]:
    try:
        data = json.loads(a.get("roles_json") or "[]")
    except (TypeError, ValueError):
        return []
    return [str(r) for r in data] if isinstance(data, list) else []


def _limit_words(a: dict[str, Any]) -> str:
    bits = []
    usd = float(a.get("allowance_usd") or 0)
    tokens = int(a.get("allowance_tokens") or 0)
    if usd:
        bits.append(f"${usd:,.2f} a month".replace(".00 ", " "))
    if tokens:
        bits.append(f"{tokens:,} tokens a month")
    return " and ".join(bits) or "no monthly limit"


def _describe_agent(a: dict[str, Any] | None) -> str | None:
    if a is None:
        return None
    bits = [f"model {_model_words(a.get('provider'), a.get('model'))}", f"works like the {a.get('base_type') or 'builder'}"]
    roles = _roles(a)
    if roles:
        bits.append(f"team roles: {', '.join(roles)}")
    bits.append(_limit_words(a))
    if a.get("paused_reason"):
        bits.append("paused")
    return ", ".join(bits)


def _agent_change_words(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, str]:
    old: list[str] = []
    new: list[str] = []
    if (before.get("provider"), before.get("model")) != (after.get("provider"), after.get("model")):
        old.append(f"model {_model_words(before.get('provider'), before.get('model'))}")
        new.append(f"model {_model_words(after.get('provider'), after.get('model'))}")
    if before.get("base_type") != after.get("base_type"):
        old.append(f"works like the {before.get('base_type') or 'builder'}")
        new.append(f"works like the {after.get('base_type') or 'builder'}")
    if _roles(before) != _roles(after):
        old.append(f"team roles: {', '.join(_roles(before)) or 'none'}")
        new.append(f"team roles: {', '.join(_roles(after)) or 'none'}")
    if _limit_words(before) != _limit_words(after):
        old.append(_limit_words(before))
        new.append(_limit_words(after))
    if before.get("system_prompt") != after.get("system_prompt"):
        old.append("its instructions")
        new.append("new instructions")
    if before.get("description") != after.get("description"):
        old.append(f"description {_q(before.get('description')) if before.get('description') else 'none'}")
        new.append(f"description {_q(after.get('description')) if after.get('description') else 'none'}")
    # Review: a raised limit also lifts an allowance pause (the route's own
    # rule) — the card says the agent is working again, never only the limit.
    if bool(before.get("paused_reason")) != bool(after.get("paused_reason")):
        old.append(f"paused ({before.get('paused_reason')})" if before.get("paused_reason") else "working")
        new.append(f"paused ({after.get('paused_reason')})" if after.get("paused_reason") else "working")
    return ", ".join(old) or "unchanged", ", ".join(new) or "unchanged"


def _check_provider(platform: Any, provider: str) -> str:
    """The Projects route's own model-service check (``routes.projects.
    _validate_project_model`` — the provider must be one Iron Jarvis has), in
    words. "" when fine or not given."""
    from fastapi import HTTPException

    from ..daemon.routes.projects import _validate_project_model

    d = _d(platform)
    if not str(provider or "").strip() or d is None:
        return ""
    try:
        _validate_project_model(d, provider, "")
    except HTTPException:
        try:
            known = sorted({str(r.get("provider")) for r in platform.providers.health() if r.get("provider")})
        except Exception:  # noqa: BLE001
            known = []
        tail = f" The ones it has: {', '.join(known)}." if known else ""
        return f"Iron Jarvis has no model service called {_q(provider)}.{tail}"
    return ""


_AGENT_ARGS = {
    "instructions": {"type": "string", "description": "What the agent is for and how it works, in its own words."},
    "description": {"type": "string"},
    "works_like": {"type": "string", "enum": list(WORKS_LIKE), "description": WORKS_LIKE_WORDS},
    "provider": {"type": "string", "description": "The model service it runs on, as the model list names it (\"\" = the app's default)."},
    "model": {"type": "string", "description": "The model it runs on (\"\" = that service's default)."},
    "roles": {"type": "array", "items": {"type": "string", "enum": ["read", "code", "review", "test"]},
              "description": "Team roles; [] takes it off the model team."},
    "monthly_limit_usd": {"type": "number", "minimum": 0, "description": "Dollars it may spend a month (0 = no limit)."},
    "monthly_limit_tokens": {"type": "integer", "minimum": 0, "description": "Tokens it may use a month (0 = no limit)."},
}
#: The tool's argument → the route body's field.
_FIELD_OF = {
    "instructions": "system_prompt",
    "description": "description",
    "works_like": "base_type",
    "provider": "provider",
    "model": "model",
    "roles": "roles",
    "monthly_limit_usd": "allowance_usd",
    "monthly_limit_tokens": "allowance_tokens",
}


def _agent_fields(args: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for arg, field in _FIELD_OF.items():
        value = args.get(arg)
        if value is None:
            continue
        if field in ("system_prompt", "description", "base_type", "provider", "model"):
            value = str(value).strip()
        elif field == "roles":
            value = [str(r) for r in value] if isinstance(value, list) else [str(value)]
        out[field] = value
    return out


class _AgentTool(R._RecordTool):  # noqa: SLF001 — the records module's own base
    RECORD = "agent"

    def _target(self, args: dict[str, Any]) -> str:
        return " ".join(str(args.get("agent") or "").split())

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_agent(self.platform, self._target(args))[0]

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _DESC.set(None)
        return _stash(await super().capture_undo(args, ctx))


class AgentCreateTool(_AgentTool):
    name = "agent_create"
    description = (
        "Make a new agent of the user's own (the Agents page's New agent): `name`, its "
        "`instructions`, what it `works_like` (reviewer = only reads), the model it runs on "
        "(`provider` and `model`), its model-team `roles` and a monthly limit "
        "(`monthly_limit_usd`, `monthly_limit_tokens`; 0 = none). The user approves on a "
        "card; Undo removes it again."
    )
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, **_AGENT_ARGS},
        "required": ["name"],
    }

    def _target(self, args: dict[str, Any]) -> str:
        return " ".join(str(args.get("name") or "").split())

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _DESC.set(None)
        return _stash(await R.capture_create_undo(self.platform, "agent", self._target(args)))

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException
        from pydantic import ValidationError

        from ..daemon.routes.agents import create_custom_agent
        from ..daemon.schemas import AgentCreate

        name = self._target(args)
        if not name:
            return ToolResult(ok=False, error="An agent needs a name.")
        fields = _agent_fields(args)
        problem = await asyncio.to_thread(_check_provider, self.platform, fields.get("provider", ""))
        if problem:
            return ToolResult(ok=False, error=problem)
        try:
            body = AgentCreate(name=name, system_prompt=fields.pop("system_prompt", ""), tools=[], **fields)
            rec = await asyncio.to_thread(create_custom_agent, self.platform, body)
        except HTTPException as exc:
            return ToolResult(ok=False, error=_route_words(exc, what=name))
        except ValidationError:
            return ToolResult(ok=False, error="One of those details is not in a form an agent can take; check the numbers and names.")
        after = await asyncio.to_thread(snap_agent, self.platform, rec.name)
        _note(after=after)
        words = _describe_agent(after)
        card = R.create_card("agent", rec.name, words, label=f"Agent {_q(rec.name)}")
        data: dict[str, Any] = {"name": rec.name}
        if card:
            data["record_change"] = card
        return ToolResult(ok=True, output=f"Made the agent {_q(rec.name)}: {words}.", data=data)


class AgentUpdateTool(_AgentTool):
    name = "agent_update"
    description = (
        "Change an agent the user made (`agent` = its name): its `instructions`, `description`, "
        "what it `works_like`, its model (`provider`, `model`), its model-team `roles` or its "
        "monthly limit (`monthly_limit_usd`, `monthly_limit_tokens`; 0 = none). Only the fields "
        "given change. The user approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {"agent": {"type": "string"}, **_AGENT_ARGS},
        "required": ["agent"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException
        from pydantic import ValidationError

        from ..daemon.routes.agents import update_custom_agent
        from ..daemon.schemas import AgentPatch

        name, problem = await asyncio.to_thread(resolve_agent, self.platform, self._target(args))
        if not name:
            return ToolResult(ok=False, error=problem)
        fields = _agent_fields(args)
        if not fields:
            return ToolResult(
                ok=False, error="Say what to change: its instructions, model, team roles, monthly limit or what it works like."
            )
        problem = await asyncio.to_thread(_check_provider, self.platform, fields.get("provider", ""))
        if problem:
            return ToolResult(ok=False, error=problem)
        before = await asyncio.to_thread(snap_agent, self.platform, name)
        try:
            await asyncio.to_thread(update_custom_agent, self.platform, name, AgentPatch(**fields))
        except HTTPException as exc:
            return ToolResult(ok=False, error=_route_words(exc, what=name))
        except ValidationError:
            return ToolResult(ok=False, error="One of those details is not in a form an agent can take; check the numbers and names.")
        after = await asyncio.to_thread(snap_agent, self.platform, name) or {}
        _note(after=after)
        old, new = _agent_change_words(before or {}, after)
        return ToolResult(
            ok=True,
            output=f"Changed the agent {_q(name)}: {old} → {new}.",
            data={"record_change": self._card("Agent changed", f"Agent {_q(name)}", old, new, f"agent.{name}")},
        )


class AgentPauseTool(_AgentTool):
    name = "agent_pause"
    description = (
        "Give an agent the user made a day off (`agent` = its name): nothing starts it until it "
        "is resumed (`pause` false resumes it). An optional `reason` is what it answers with "
        "meanwhile. The user approves on a card; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {"agent": {"type": "string"}, "pause": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["agent"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException

        from ..daemon.routes.agents import pause_custom_agent, resume_custom_agent

        name, problem = await asyncio.to_thread(resolve_agent, self.platform, self._target(args))
        if not name:
            return ToolResult(ok=False, error=problem)
        pause = args.get("pause") is not False
        before = await asyncio.to_thread(snap_agent, self.platform, name) or {}
        if bool(before.get("paused_reason")) == pause:
            return ToolResult(ok=False, error=f"The agent {_q(name)} is already {'paused' if pause else 'working'}.")
        try:
            if pause:
                await asyncio.to_thread(pause_custom_agent, self.platform, name, str(args.get("reason") or ""))
            else:
                await asyncio.to_thread(resume_custom_agent, self.platform, name)
        except HTTPException as exc:
            return ToolResult(ok=False, error=_route_words(exc, what=name))
        after = await asyncio.to_thread(snap_agent, self.platform, name) or {}
        _note(after=after)
        old = f"paused ({before.get('paused_reason')})" if before.get("paused_reason") else "working"
        new = f"paused ({after.get('paused_reason')})" if after.get("paused_reason") else "working"
        return ToolResult(
            ok=True,
            output=f"{'Paused' if pause else 'Resumed'} the agent {_q(name)}.",
            data={
                "record_change": self._card(
                    "Agent paused" if pause else "Agent resumed", f"Agent {_q(name)}", old, new, f"agent.{name}"
                )
            },
        )


class AgentDeleteTool(_AgentTool):
    name = "agent_delete"
    description = (
        "Remove an agent the user made (`agent` = its name), as the Agents page's Delete does: "
        "its folder (instructions, notes) moves to the trash, never deleted. The user approves "
        "on a card; Undo brings the agent and its folder back."
    )
    input_schema = {"type": "object", "properties": {"agent": {"type": "string"}}, "required": ["agent"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from fastapi import HTTPException

        from ..daemon.routes.agents import delete_custom_agent

        name, problem = await asyncio.to_thread(resolve_agent, self.platform, self._target(args))
        if not name:
            return ToolResult(ok=False, error=problem)
        before = await asyncio.to_thread(snap_agent, self.platform, name)
        had = await asyncio.to_thread(_trash_folders, self.platform, name)
        try:
            await asyncio.to_thread(delete_custom_agent, self.platform, name)
        except HTTPException as exc:
            return ToolResult(ok=False, error=_route_words(exc, what=name))
        moved = sorted((await asyncio.to_thread(_trash_folders, self.platform, name)) - had)
        # Which trash folder the route moved it to — journaled with this
        # call (the registry writes the descriptor after a successful
        # execute), so the Undo can move that very folder back.
        _note(after=None, trash=moved[0] if len(moved) == 1 else "")
        folder = " Its folder is in the trash; Undo brings the agent and its folder back." if moved else ""
        return ToolResult(
            ok=True,
            output=f"Removed the agent {_q(name)}.{folder}",
            data={"record_change": self._card("Agent removed", f"Agent {_q(name)}", _describe_agent(before), None, f"agent.{name}")},
        )


def project_agent_tools(platform: Any) -> list[Tool]:
    """The eight tools, for ``records.record_tools``."""
    return [
        ProjectCreateTool(platform),
        ProjectUpdateTool(platform),
        ProjectArchiveTool(platform),
        ProjectFocusTool(platform),
        AgentCreateTool(platform),
        AgentUpdateTool(platform),
        AgentPauseTool(platform),
        AgentDeleteTool(platform),
    ]


# --------------------------------------------------------------------------- #
# arming — which sentences offer these tools
# --------------------------------------------------------------------------- #

#: Where a clause ends: a sentence end, a line, a colon before a space (never
#: the one in ``D:\\``), or a joining word ("make a project and switch to it").
_CLAUSES = re.compile(r"[.!?;\n]+|:\s|\b(?:and\s+then|and|then|also|but)\b", re.IGNORECASE)
#: "I want" / "I need" / "I'd like" (either apostrophe).
_I_WANT = r"i(?:\s+(?:want|need|would\s+like)|['’]d\s+like)"
#: Words in front of the request ("please", "can you", "I want you to").
_FILLER = re.compile(
    r"(?:please|kindly|ok(?:ay)?|so|hey|hi|hello|jarvis|now|just|go\s+ahead\s+and|can\s+you|could\s+you"
    r"|would\s+you|will\s+you|" + _I_WANT + r"(?:\s+you)?\s+to|let['’]?s|help\s+me(?:\s+to)?)"
    r"\b[\s,]*",
    re.IGNORECASE,
)
_WORDS = r"(?P<pre>(?:[\w'.&-]+\s+){0,3}?)"
_DEF = r"(?:(?:the|my|our|this|that)\s+)"
_TAIL = r"(?P<tail>[^\n]{0,80})"

#: The thing after "project" that makes it a document or a file, not a
#: project of Iron Jarvis's ("create a project plan", "the project folder").
_PROJECT_THING = re.compile(
    r"\s*(?:-|plans?\b|timelines?\b|proposals?\b|budgets?\b|reports?\b|charters?\b|briefs?\b|schedules?\b"
    r"|status\b|updates?\b|summar(?:y|ies)\b|lists?\b|templates?\b|managers?\b|management\b|deadlines?\b"
    r"|meetings?\b|kick-?offs?\b|folders?\b|files?\b|director(?:y|ies)\b|docs?\b|documents?\b|notes\b"
    r"|pages?\b|views?\b|tabs?\b|panels?\b|screens?\b|boards?\b|menus?\b|settings\b|scope\b|risks?\b"
    r"|milestones?\b|tasks?\b|leads?\b|costs?\b|estimates?\b|goals?\b|requirements\b|specs?\b|code\b"
    r"|repo\b|repository\b|workspace\b|structure\b|outline\b|emails?\b|messages?\b|threads?\b|chats?\b"
    r"|photos?\b|pictures?\b|images?\b"
    # Review (Wave 3 group G): office things and a possessive ("rename the
    # project spreadsheet", "set up a meeting with the project team", "the
    # project's files") — the thing is the noun after, never the project.
    r"|spreadsheets?\b|workbooks?\b|sheets?\b|decks?\b|slides?\b|presentations?\b|memos?\b|letters?\b"
    r"|invoices?\b|calendars?\b|teams?\b|members?\b|['’]s\b)",
    re.IGNORECASE,
)
#: Review (Wave 3 group G): a preposition among the words BEFORE "project"
#: makes the made thing something else ("make a list of projects", "set up a
#: meeting with the project team", "make a copy of the Smith project").
_PRE_PREP = re.compile(
    r"\b(?:of|about|on|for|with|from|to|in|into|at|by|per|among|between|like|regarding)\b", re.IGNORECASE
)
#: A coding project or another app's project ("a new React project", "a
#: project in Asana", "Microsoft Project") — never one of Iron Jarvis's.
_CODE_WORDS = re.compile(
    r"\b(?:python|react|node|next\.?js|django|flask|rust|golang|java|kotlin|swift|typescript|javascript|js|ts"
    r"|vite|maven|gradle|cargo|npm|pnpm|yarn|xcode|android|ios|unity|godot|unreal|electron|tauri|flutter"
    r"|laravel|rails|ruby|php|spring|fastapi|express|git|github|gitlab|vs|visual|studio|coding|software"
    r"|web|website|app|mobile|docker|terraform|langchain|langgraph|crewai|autogen|microsoft|ms|asana|jira"
    r"|trello|notion|todoist|clickup|basecamp|smartsheet|airtable|code)\b",
    re.IGNORECASE,
)
_OTHER_PLACE = re.compile(
    r"\b(?:in|on|inside|into|with|using)\s+(?:asana|jira|trello|notion|todoist|monday|clickup|basecamp|linear"
    r"|smartsheet|wrike|airtable|teams|sharepoint|excel|microsoft\s+project|ms\s+project|github|gitlab"
    r"|bitbucket|azure\s+devops|vs\s*code|visual\s+studio|xcode|android\s+studio|pycharm|intellij|unity|godot"
    r"|python|langchain|langgraph|crewai|autogen|n8n|zapier|copilot\s+studio|power\s+automate|javascript"
    r"|typescript|node|react|(?:my|the|this)\s+(?:repo|repository|codebase|code))\b",
    re.IGNORECASE,
)

_P_CREATE = re.compile(
    r"(?P<verb>create|make|add|set\s*up|setup|start|open|spin\s+up|" + _I_WANT + r")\s+"
    r"(?:me\s+|us\s+)?(?P<det>a|an|new|another|one|the|my)\s+(?:new\s+)?" + _WORDS + r"projects?(?![\w-])" + _TAIL,
    re.IGNORECASE,
)
_P_NEW = re.compile(r"new\s+projects?(?:\s+(?:called|named|for)\b|\s*$)" + _TAIL, re.IGNORECASE)
_P_RENAME = re.compile(
    r"(?:rename|change\s+the\s+name\s+of)\s+" + _DEF + r"?" + _WORDS + r"projects?(?![\w-])" + _TAIL, re.IGNORECASE
)
_P_SET_FIELD = re.compile(
    r"(?:change|update|set|edit|point|switch)\s+" + _DEF + _WORDS + r"projects?(?:'s)?\s+"
    r"(?:folder|location|directory|description|brief|default\s+model|model|name|title)\b"
    r"|(?:change|update|set|edit)\s+(?:the\s+)?(?:folder|location|description|brief|default\s+model|model|name|title)"
    r"\s+(?:of|for)\s+" + _DEF + r"(?:[\w'.&-]+\s+){0,3}?projects?(?![\w-])"
    r"|(?:move|point)\s+" + _DEF + r"(?:[\w'.&-]+\s+){0,3}?projects?\s+(?:to|at)\s+"
    r"(?:[a-z]:[\\/]|\\\\|the\s+folder|a\s+(?:new\s+)?folder|folder)",
    re.IGNORECASE,
)
_P_ARCHIVE = re.compile(
    r"(?:archive|un-?archive|re-?open|bring\s+back)\s+" + _DEF + r"?" + _WORDS + r"projects?(?![\w-])" + _TAIL,
    re.IGNORECASE,
)
_P_FOCUS = re.compile(
    r"(?:(?:switch|change|go|move|jump|flip)\s+(?:over\s+|back\s+)?to|focus\s+on|work\s+in)\s+" + _DEF + _WORDS
    + r"projects?(?![\w-])" + _TAIL,
    re.IGNORECASE,
)
_P_FOCUS_SETTING = re.compile(
    r"make\s+(?:[\w'.&-]+\s+){1,4}?(?:the|my)\s+(?:active|focused|current|main)\s+project\b"
    r"|(?:set|change|clear|unset|reset|remove)\s+(?:the\s+|my\s+)?(?:active|focused|current)\s+project\b"
    r"|(?:stop\s+focusing\s+on|unfocus(?:\s+from)?)\s+(?:(?:the|a|my|this|any)\s+)?(?:[\w'.&-]+\s+){0,3}?projects?\b",
    re.IGNORECASE,
)

#: A person who is an "agent" ("my insurance agent", "a registered agent for
#: the LLC", "an enrolled agent"), never one of Iron Jarvis's.
_HUMAN_AGENT = frozenset(
    {
        "registered", "enrolled", "insurance", "travel", "estate", "realty", "booking", "sports", "leasing",
        "transfer", "paying", "payment", "escrow", "fbi", "irs", "cia", "secret", "double", "free", "cleaning",
        "chemical", "user", "listing", "claims", "election", "border", "federal", "special", "undercover",
        "rental", "shipping", "freight", "purchasing", "collection", "collections", "closing", "title", "patent",
        "literary", "talent", "casting", "press", "ticket", "process", "bank", "fiscal", "statutory", "process-server",
    }
)
_AGENT_THING = re.compile(
    r"\s*(?:-|list\b|filing\b|forms?\b|letters?\b|agreements?\b|fees?\b|commissions?\b|licen[cs]es?\b|contracts?\b"
    r"|portal\b|reports?\b|strings?\b|code\b|class(?:es)?\b|loops?\b|framework\b|sdk\b|librar(?:y|ies)\b"
    r"|functions?\b|toolkit\b|files?\b|ids?\b|of\s+(?:change|record|service)\b|for\s+(?:service|the\s+llc)\b"
    # Review (Wave 3 group G): what an agent SAID or LEFT ("delete the agent
    # notes", "pause the agent conversation") and a possessive ("remove the
    # agent's signature") — the thing is the noun after, never the agent.
    r"|notes?\b|minutes\b|transcripts?\b|logs?\b|conversations?\b|chats?\b|messages?\b|emails?\b|calls?\b"
    r"|threads?\b|replies\b|answers?\b|outputs?\b|['’]s\b)",
    re.IGNORECASE,
)
_A_CREATE = re.compile(
    r"(?P<verb>create|make|add|hire|set\s*up|setup|spin\s+up|" + _I_WANT + r")\s+"
    r"(?:me\s+|us\s+)?(?P<det>a|an|new|another|one)\s+(?:new\s+)?" + _WORDS + r"agents?(?![\w-])" + _TAIL,
    re.IGNORECASE,
)
_A_NEW = re.compile(
    r"(?:new\s+agents?(?:\s+(?:called|named|for)\b|\s*$)|(?:create|make|add|hire)\s+(?:an?\s+)?agents?\s+(?:called|named)\b)"
    + _TAIL,
    re.IGNORECASE,
)
_A_FIELD = re.compile(
    r"\b(?:model|gpt|chatgpt|claude|sonnet|opus|haiku|gemini|grok|codex|llama|qwen|glm|deepseek|mistral|ollama"
    r"|limit|allowance|budget|cap|monthly|a\s+month|per\s+month|roles?|only\s+reads?|read-?only|read\s+only"
    r"|team|instructions|description|prompt|works?\s+like)\b|\$\s?\d",
    re.IGNORECASE,
)
_A_CHANGE = re.compile(
    r"(?:change|update|edit|set|switch|move|put|give|make|let|turn|raise|lower|increase|decrease|bump|cap"
    r"|limit)\s++(?!sure\b)(?P<span>[^.?!\n]{0,60}?)\bagents?(?:'s)?(?![\w-])" + _TAIL,
    re.IGNORECASE,
)
_A_BUILTIN_CHANGE = re.compile(
    r"(?:give|set|change|switch|move|make|put|raise|lower|cap|limit)\s+(?:the|my)\s+"
    r"(?:builder|reviewer|researcher|planner|automation|guide|maintainer)\b" + _TAIL,
    re.IGNORECASE,
)
_A_STRONG_FIELD = re.compile(
    r"\b(?:monthly|a\s+month|per\s+month|each\s+month)\b[^\n]{0,24}?\b(?:limit|allowance|budget|cap)\b"
    r"|\b(?:limit|allowance|budget|cap)\b[^\n]{0,24}?\b(?:a\s+month|per\s+month|monthly|each\s+month)\b"
    r"|\b(?:model|gpt|chatgpt|claude|sonnet|opus|haiku|gemini|grok|codex)\b|\bteam\s+roles?\b",
    re.IGNORECASE,
)
_A_PAUSE = re.compile(
    r"(?:pause|resume|un-?pause|suspend|unsuspend|reactivate|re-?enable|disable|enable|turn\s+(?:off|on)"
    r"|switch\s+(?:off|on)|wake\s+up)\s+" + _DEF + r"?" + _WORDS + r"agents?(?![\w-])" + _TAIL
    + r"|give\s+" + _DEF + r"(?:[\w'.&-]+\s+){0,3}?agents?\s+(?:a|the)\s+day\s+off"
    + r"|(?:turn|switch)\s+" + _DEF + r"(?:[\w'.&-]+\s+){0,3}?agents?\s+(?:back\s+)?(?:on|off)\b",
    re.IGNORECASE,
)
_A_DELETE = re.compile(
    r"(?:delete|remove|fire|retire|get\s+rid\s+of|drop|dismiss|let\s+go\s+of|uninstall)\s+"
    r"(?:(?:the|my|our|that|this|all)\s+)?" + _WORDS + r"agents?(?![\w-])" + _TAIL,
    re.IGNORECASE,
)
#: "remove the agent from the Smith project's team" is the PROJECT's team
#: (the project page), never the agent itself — but "the model team" is the
#: agent's own roles.
_TO_A_TEAM = re.compile(
    r"\s*(?:to|from|on|off|out\s+of|onto)\s+(?:(?:the|my|our|this)\s+)?(?:(?!model\b)[\w'.&-]+\s+){0,3}?"
    r"(?:project|team|room|table|chat)s?\b",
    re.IGNORECASE,
)


def _clauses(text: str) -> list[str]:
    """The message cut into clauses, each with the words in front of the
    request ("please", "can you…") taken off. Linear: plain splits and
    ANCHORED matches only."""
    out: list[str] = []
    for part in _CLAUSES.split(text or ""):
        c = part.strip(" \t\r\"'`*•>,-")
        for _ in range(4):
            m = _FILLER.match(c)
            if m is None or not m.group(0):
                break
            c = c[m.end():]
        if c:
            out.append(c)
    return out


def _not_code(pre: str) -> bool:
    """A MADE thing whose words say code or another app ("a new React
    project", "a LangChain agent") is the coding chat's, never ours. Only a
    create reads this: an existing project or agent may be NAMED "GitHub
    migration"."""
    return _CODE_WORDS.search(pre or "") is None


def _project_ok(m: "re.Match[str]") -> bool:
    tail = m.group("tail") or ""
    return not _PROJECT_THING.match(tail) and _OTHER_PLACE.search(tail) is None


def _project_clause(c: str) -> bool:
    m = _P_CREATE.match(c)
    if m is not None:
        verb = m.group("verb").lower()
        det = m.group("det").lower()
        definite_ok = verb.startswith("create") or det not in ("the", "my")
        indefinite = verb not in ("start", "open") or det in ("a", "an", "new", "another", "one")
        pre = m.group("pre") or ""
        if definite_ok and indefinite and _not_code(pre) and not _PRE_PREP.search(pre) and _project_ok(m):
            return True
    m = _P_NEW.match(c)
    if m is not None and _OTHER_PLACE.search(m.group("tail")) is None:
        return True
    for rule in (_P_RENAME, _P_ARCHIVE, _P_FOCUS):
        m = rule.match(c)
        if m is not None and _project_ok(m):
            return True
    return _P_SET_FIELD.match(c) is not None or _P_FOCUS_SETTING.match(c) is not None


def _agent_ok(pre: str, tail: str) -> bool:
    """Not a PERSON ("my insurance agent", "a registered agent"), not a thing
    named after one ("an agent agreement", "the user agent string"), not
    another app's ("an agent in Copilot Studio")."""
    words = (pre or "").split()
    if words and words[-1].strip("'.&-").lower() in _HUMAN_AGENT:
        return False
    return not _AGENT_THING.match(tail or "") and _OTHER_PLACE.search(tail or "") is None


def _agent_clause(c: str) -> bool:
    m = _A_CREATE.match(c)
    if (
        m is not None
        and _not_code(m.group("pre"))
        and not _PRE_PREP.search(m.group("pre") or "")
        and _agent_ok(m.group("pre"), m.group("tail"))
    ):
        return True
    m = _A_NEW.match(c)
    if m is not None and _OTHER_PLACE.search(m.group("tail")) is None:
        return True
    m = _A_PAUSE.match(c)
    if m is not None and (m.group("pre") is None or _agent_ok(m.group("pre"), m.group("tail") or "")):
        return True
    m = _A_DELETE.match(c)
    if m is not None and _agent_ok(m.group("pre"), m.group("tail")) and not _TO_A_TEAM.match(m.group("tail")):
        return True
    m = _A_CHANGE.match(c)
    if m is not None:
        span, tail = m.group("span"), m.group("tail")
        if (
            _agent_ok(span, tail)
            and not _TO_A_TEAM.match(tail)
            and (_A_FIELD.search(span) is not None or _A_FIELD.search(tail) is not None)
        ):
            return True
    m = _A_BUILTIN_CHANGE.match(c)
    return m is not None and _A_STRONG_FIELD.search(m.group("tail")) is not None


_BUILTIN_WORDS = ("builder", "reviewer", "researcher", "planner", "automation", "guide", "maintainer")


def projects_tools_for(text: str) -> list[str]:
    """The project and agent tools a chat message asks for (v1.435.0, Wave 3
    group G) — read by ``records.record_tools_for`` (its question guard drops
    every writer for a question, so "how do I make a project?" arms none).
    A request to make, change, archive or focus a project arms the project
    family; to make, change, pause or remove an agent, the agent family. Each
    is ask tier: armed visible, it renders the card. Never raises."""
    t = text or ""
    low = t.lower()
    try:
        out: list[str] = []
        projects = "project" in low
        agents = "agent" in low or any(w in low for w in _BUILTIN_WORDS)
        if not (projects or agents):
            return out
        clauses = _clauses(t)
        if projects and any(_project_clause(c) for c in clauses):
            out += list(PROJECT_TOOLS)
        if agents and any(_agent_clause(c) for c in clauses):
            out += list(AGENT_TOOLS)
        return out
    except Exception:  # noqa: BLE001 — a matcher fault arms nothing new
        log.debug("project/agent arming failed", exc_info=True)
        return []
