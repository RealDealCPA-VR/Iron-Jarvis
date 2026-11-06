"""Your preferences, lessons and personas from chat (v1.438.0, chat-first plan
Wave 4 group C, Part B2: "forget that preference about short answers",
"change my preference to bullet points", "add a lesson: always cite the IRS
section", "make a persona that answers like a CPA", "delete the CPA persona").

Before this, chat could only ADD a preference (``remember_preference``);
forgetting or rewording one, adding a lesson with an Undo, and every persona
change were the Memory page's and the chat menu's only. Seven tools close the
gap the way ``settings.records`` closes it for schedules and goals:

* ``preference_list`` — READ: the preferences and lessons you kept (the Memory
  page's "Kept" list, through ``learning.preferences.list_preferences`` — the
  ONE confirmed-only query; never a raw read of the lesson table), with the id
  each tool below takes. Allow.
* ``preference_edit`` / ``preference_forget`` — THROUGH the Memory page's own
  bodies (``learning.preferences.edit`` / ``forget``): the new words are
  checked by ``clean_text`` there (1–280 characters, scanned for an
  instruction planted for the assistant), only a KEPT preference can be
  changed, and a shared Build block re-renders (``profile.share``). Ask.
* ``lesson_add`` — THROUGH ``POST /lessons``' own body
  (``learning.preferences.add_lesson``), after ``clean_text``. Ask.
* ``persona_create`` / ``persona_update`` / ``persona_delete`` — THROUGH the
  persona routes' own bodies (``personas.store.create_persona_record`` /
  ``save_persona_record`` / ``delete_persona_record``); a persona's words go
  into every chat that uses it, so its title, description and instructions are
  scanned first (``core.promptguard.scan_context``). A built-in persona can be
  changed (that saves your own version, which wins) and reset (that removes
  your version); it is never removed. Ask.

Every change has an Undo that puts back what THAT change did and nothing else
(each tool records the row it found — ``prior`` — AND the row it left —
``after``): a changed preference or persona gets its old words back; a
forgotten preference, or a removed persona, comes back with its own id and
every column; an added lesson or a made persona is removed again. Something
changed AGAIN since, or made again under the same name since, refuses the
Undo in one sentence rather than write over newer work.

Arming (:func:`self_tools_for`) reads the sentence the user typed: a request
to forget, change or add a preference or a lesson, or to make, change or
remove a persona, arms that family; an everyday sentence ("I prefer coffee",
"my lesson plan for Tuesday", "forget it, never mind", "the persona of the
narrator in my novel", "a buyer persona for my marketing plan") arms nothing,
and a QUESTION keeps only the reader. Every pattern is LINEAR: the message is
cut into clauses by plain splits (``records_projects._clauses``) and each rule
is an ANCHORED match with bounded repeats (pinned with pathological input in a
child process with a deadline).

Snapshots and restores are BLOCKING (SQLite); the tools run them through
``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
from typing import Any

from ..tools.base import Reversibility, Tool, ToolContext, ToolResult
from . import records as R

log = logging.getLogger("ironjarvis.settings")

PREFERENCE_TOOLS: tuple[str, ...] = ("preference_edit", "preference_forget", "lesson_add")
PERSONA_TOOLS: tuple[str, ...] = ("persona_create", "persona_update", "persona_delete")
#: The reader (allow; never journaled, carries no card).
PREFERENCE_READER = "preference_list"
#: The tools that carry a card and an Undo, with their default permission
#: (``records.RECORD_TOOL_TIERS`` and ``core.config``'s table say the same).
SELF_TOOL_TIERS: dict[str, str] = {name: "ask" for name in (*PREFERENCE_TOOLS, *PERSONA_TOOLS)}
#: Every tool of this module and its tier.
ALL_SELF_TOOL_TIERS: dict[str, str] = {**SELF_TOOL_TIERS, PREFERENCE_READER: "allow"}

#: The record kinds this module's Undo serves (``records.restore``).
KINDS: tuple[str, ...] = ("preference", "persona")

#: How many kept preferences ``preference_list`` shows.
LIST_LIMIT = 40

#: The Undo descriptor THIS call captured (the records_projects pattern:
#: each tool records what its change LEFT — ``after`` — beside what it found
#: — ``prior``). Set by ``capture_undo``, consumed by :func:`_note` after a
#: successful write; a direct ``execute`` (no capture) finds ``None``.
_DESC: contextvars.ContextVar["dict[str, Any] | None"] = contextvars.ContextVar(
    "ij_self_record_desc", default=None
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


# --------------------------------------------------------------------------- #
# words
# --------------------------------------------------------------------------- #


def _q(text: Any, n: int = 80) -> str:
    t = " ".join(str(text or "").split())
    return "“" + (t if len(t) <= n else t[: n - 1].rstrip() + "…") + "”"


def _short(text: Any, n: int = 120) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


#: Words a preference reference may use that name nothing ("the preference
#: about…", "my lesson that…").
_REF_STOP = frozenset(
    "a an the my your our this that these those of for to in on at by with and or me i it is are be from as "
    "about which who what when one preference preferences lesson lessons kept rule rules thing things "
    "saying says said you jarvis please".split()
)
_REF_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def _stem(w: str) -> str:
    for suffix in ("ies", "es", "s"):
        if len(w) > 4 and w.endswith(suffix):
            return w[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return w


def _ref_words(text: str) -> set[str]:
    return {_stem(w) for w in _REF_WORD.findall((text or "").lower()) if w not in _REF_STOP and len(w) > 1}


# --------------------------------------------------------------------------- #
# preferences
# --------------------------------------------------------------------------- #


def _engine(platform: Any) -> Any:
    return platform.engine


def kept_preferences(platform: Any) -> list[dict[str, Any]]:
    """The kept preferences and lessons (the Memory page's list) — through
    THE one confirmed-only query. Blocking."""
    from ..learning.preferences import list_preferences

    return list(list_preferences(_engine(platform)).get("kept") or [])


def resolve_preference(platform: Any, ref: str) -> tuple[str, str]:
    """``(id, "")`` for the kept preference ``ref`` names — its id, its exact
    words, or the one whose words best match — else ``("", one sentence)``.
    Blocking."""
    ref = " ".join(str(ref or "").split())
    if not ref:
        return "", "Say which preference: its words, or its id from preference_list."
    kept = kept_preferences(platform)
    for p in kept:
        if p["id"] == ref or " ".join(p["text"].split()).casefold() == ref.casefold():
            return p["id"], ""
    want = _ref_words(ref)
    if want:
        scored = []
        for p in kept:
            have = _ref_words(p["text"])
            hit = len(want & have)
            if hit:
                scored.append((hit, p))
        if scored:
            scored.sort(key=lambda sp: -sp[0])
            best = scored[0][0]
            top = [p for hit, p in scored if hit == best]
            if len(top) == 1 and best * 2 >= min(len(want), 4):
                return top[0]["id"], ""
            if len(top) > 1:
                names = " and ".join(_q(p["text"], 60) for p in top[:2])
                return "", f"More than one kept preference matches ({names}); say which one."
    if not kept:
        return "", "You have no kept preferences or lessons yet."
    some = ", ".join(_q(p["text"], 50) for p in kept[:3])
    return "", f"No kept preference matches {_q(ref)}. Kept ones include {some}."


def snap_preference(platform: Any, pref_id: str) -> dict[str, Any] | None:
    """The lesson row as it is now (every column), or None. Blocking. A read
    BY ID — the row each Undo puts back — never a list of rows (lists go
    through ``list_preferences``)."""
    from ..core.db import session_scope
    from ..learning.models import LessonRecord

    with session_scope(_engine(platform)) as db:
        row = db.get(LessonRecord, pref_id) if pref_id else None
        if row is None:
            return None
        return {k: R._iso(v) for k, v in row.model_dump().items()}  # noqa: SLF001


def _write_preference(platform: Any, pref_id: str, values: dict[str, Any], *, create: bool = False) -> None:
    """Write ``values`` into the lesson row ``pref_id`` (``create``: make the
    row again with its own id). Blocking. Re-renders a shared Build block."""
    from ..core.db import session_scope
    from ..learning.models import LessonRecord

    with session_scope(_engine(platform)) as db:
        row = db.get(LessonRecord, pref_id)
        if row is None:
            if not create:
                return
            row = LessonRecord(id=pref_id)
        for k, v in values.items():
            if k == "id" or not hasattr(row, k):
                continue
            setattr(row, k, R._dt(v) if k in ("created_at", "decided_at") else v)  # noqa: SLF001
        db.add(row)
        db.commit()
    from ..profile.share import notify_changed

    notify_changed()


def _delete_preference(platform: Any, pref_id: str) -> None:
    from ..core.db import session_scope
    from ..learning.models import LessonRecord

    with session_scope(_engine(platform)) as db:
        row = db.get(LessonRecord, pref_id)
        if row is not None:
            db.delete(row)
            db.commit()
    from ..profile.share import notify_changed

    notify_changed()


def _same_words(a: Any, b: Any) -> bool:
    return " ".join(str(a or "").split()) == " ".join(str(b or "").split())


def restore_preference(platform: Any, pref_id: str, prior: dict[str, Any] | None, meta: dict[str, Any]) -> str:
    """``POST /undo`` for a preference or lesson change: what THIS change did,
    nothing else. Blocking."""
    after_known = "after" in meta
    after = meta.get("after") if isinstance(meta.get("after"), dict) else None
    current = snap_preference(platform, pref_id)
    if prior is None:
        # The change ADDED the lesson: remove it again — only as it was added.
        if current is None:
            return "undo: the lesson was already gone"
        if after is not None and not _same_words(current.get("text"), after.get("text")):
            raise R.RecordError(
                f"The lesson {_q(after.get('text'))} was changed after it was added, so Undo would take "
                "that change with it; forget it on the Memory page if you still want it gone."
            )
        _delete_preference(platform, pref_id)
        return f"undo: removed the lesson {_q(current.get('text'))}"
    if after_known and after is None:
        # The change FORGOT it: bring it back with its own id.
        if current is not None:
            if _same_words(current.get("text"), prior.get("text")):
                return "undo: the preference was already back"
            raise R.RecordError("A different preference was saved in its place since, so Undo would overwrite it.")
        # Review (v1.438.0): the user said it AGAIN since (a new row, a new
        # id) — bringing the old row back would keep the same words twice.
        if any(_same_words(str(p["text"]).casefold(), str(prior.get("text") or "").casefold())
               for p in kept_preferences(platform)):
            return f"undo: the preference {_q(prior.get('text'))} is already kept again"
        _write_preference(platform, pref_id, prior, create=True)
        return f"undo: the preference {_q(prior.get('text'))} is kept again"
    # The change REWORDED it.
    if current is None:
        raise R.RecordError("That preference was forgotten after this change, so there is nothing to put back.")
    if _same_words(current.get("text"), prior.get("text")):
        return "undo: the preference already reads as it did"
    if after is not None and not _same_words(current.get("text"), after.get("text")):
        raise R.RecordError(
            "That preference was changed again after this change, so Undo would undo that too; "
            "change it on the Memory page instead."
        )
    _write_preference(platform, pref_id, {"text": prior.get("text")})
    return f"undo: the preference reads {_q(prior.get('text'))} again"


# --------------------------------------------------------------------------- #
# personas
# --------------------------------------------------------------------------- #


def _builtins(platform: Any) -> dict[str, dict[str, str]]:
    d = R._d(platform)  # noqa: SLF001
    got = getattr(d, "_PERSONAS", None)
    if isinstance(got, dict):
        return got
    from ..personas.builtins import BUILTIN_PERSONAS

    return BUILTIN_PERSONAS


def all_personas(platform: Any) -> list[dict[str, Any]]:
    from ..personas import PersonaStore, merged

    return merged(PersonaStore(_engine(platform)), _builtins(platform))


_PERSONA_TAIL = re.compile(r"\s+(?:persona|personas)$", re.IGNORECASE)


def resolve_persona(platform: Any, ref: str) -> tuple[str, str]:
    """``(slug, "")`` for the persona ``ref`` names (its name or its title,
    "the CPA persona" → "CPA"), else ``("", one sentence)``. Blocking."""
    from ..personas import slugify

    raw = " ".join(str(ref or "").split())
    raw = re.sub(r"^(?:the|my|your)\s+", "", raw, flags=re.IGNORECASE)
    raw = _PERSONA_TAIL.sub("", raw).strip()
    if not raw:
        return "", "Say which persona (its name)."
    personas = all_personas(platform)
    slug = slugify(raw)
    for p in personas:
        if p["name"] == slug or slugify(p.get("title") or "") == slug:
            return p["name"], ""
    names = ", ".join(_q(p.get("title") or p["name"], 40) for p in personas[:8])
    return "", f"There is no persona {_q(raw)}. The personas are {names}."


def snap_persona(platform: Any, slug: str) -> dict[str, Any] | None:
    """The user's own saved row for ``slug`` (every column), or None — a
    built-in with no version of the user's own is None. Blocking."""
    from ..personas import PersonaStore

    row = PersonaStore(_engine(platform)).get(slug) if slug else None
    if row is None:
        return None
    return {k: R._iso(v) for k, v in row.model_dump().items()}  # noqa: SLF001


_PERSONA_FIELDS = ("title", "description", "prompt")


def _persona_same(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if a is None or b is None:
        return a is b
    return all(_same_words(a.get(k), b.get(k)) for k in _PERSONA_FIELDS)


def _persona_words(p: dict[str, Any] | None) -> str | None:
    if not p:
        return None
    title = p.get("title") or p.get("name") or "persona"
    what = p.get("description") or p.get("prompt") or ""
    return f"{title}: {_short(what, 100)}" if what else str(title)


def restore_persona(platform: Any, slug: str, prior: dict[str, Any] | None, meta: dict[str, Any]) -> str:
    """``POST /undo`` for a persona change: what THIS change did, nothing
    else. Blocking."""
    from ..personas import PersonaRecord, PersonaStore

    after_known = "after" in meta
    after = meta.get("after") if isinstance(meta.get("after"), dict) else None
    store = PersonaStore(_engine(platform))
    current = snap_persona(platform, slug)
    builtin = slug in _builtins(platform)
    if prior is None:
        # The change MADE this row (a new persona, or your first version of
        # a built-in): remove it again — only as it was made.
        if current is None:
            return "undo: the persona was already gone"
        if after is not None and not _persona_same(current, after):
            raise R.RecordError(
                f"The persona {_q(current.get('title') or slug)} was changed after this change, so Undo "
                "would take that change with it; change or remove it in the chat's persona menu instead."
            )
        # Review (v1.438.0): made the DEFAULT persona since — removing it
        # would leave every chat without a persona reading its name as plain
        # instructions. A built-in only loses your version (its own words
        # stay), so that is still fine.
        default = str(getattr(getattr(platform, "config", None), "default_persona", "") or "").strip()
        if not builtin and default == slug:
            raise R.RecordError(
                f"The persona {_q(current.get('title') or slug)} is your default persona now, so Undo would "
                "leave chats with no persona; change the default persona first."
            )
        store.delete(slug)
        if builtin:
            return f"undo: the built-in persona {_q(slug)} has its own words again"
        return f"undo: removed the persona {_q(current.get('title') or slug)}"
    if after_known and after is None:
        # The change REMOVED your row: make it again, with its own dates.
        if current is not None:
            if _persona_same(current, prior):
                return "undo: the persona was already back"
            raise R.RecordError(
                f"A persona called {_q(slug)} was saved again since this change, so Undo would overwrite it."
            )
        from ..core.db import session_scope

        with session_scope(_engine(platform)) as db:
            row = PersonaRecord(name=slug)
            for k in ("title", "description", "prompt", "created_at", "updated_at"):
                v = prior.get(k)
                setattr(row, k, R._dt(v) if k.endswith("_at") else (v or ""))  # noqa: SLF001
            db.add(row)
            db.commit()
        return f"undo: the persona {_q(prior.get('title') or slug)} is back"
    if current is None:
        raise R.RecordError("That persona was removed after this change, so there is nothing to put back.")
    if _persona_same(current, prior):
        return "undo: the persona already reads as it did"
    if after is not None and not _persona_same(current, after):
        raise R.RecordError(
            "That persona was changed again after this change, so Undo would undo that too; "
            "change it in the chat's persona menu instead."
        )
    store.upsert(slug, **{k: str(prior.get(k) or "") for k in _PERSONA_FIELDS})
    return f"undo: the persona {_q(prior.get('title') or slug)} reads as it did"


def snap_kind(kind: str) -> Any:
    return snap_preference if kind == "preference" else snap_persona


def restore_kind(platform: Any, kind: str, name: str, prior: Any, desc: dict[str, Any]) -> str:
    """``records.restore`` for this module's kinds. Blocking."""
    meta = desc if isinstance(desc, dict) else {}
    prior = prior if isinstance(prior, dict) else None
    if kind == "preference":
        return restore_preference(platform, name, prior, meta)
    if kind == "persona":
        return restore_persona(platform, name, prior, meta)
    raise R.RecordError(f"unknown record kind {kind!r}")


def _scan_problem(*texts: Any) -> str:
    """One sentence when words that will reach every chat read like an
    instruction planted for the assistant, else ""."""
    from ..core.promptguard import scan_context

    for t in texts:
        if str(t or "").strip() and scan_context(str(t), source="persona", cap=None).blocked:
            return (
                "Those words read like an instruction planted for the assistant, so the persona was not "
                "saved; say it in your own words."
            )
    return ""


#: A persona's words are kept to what the chat menu lets a person type.
PERSONA_TEXT_MAX = 4000


# --------------------------------------------------------------------------- #
# tools
# --------------------------------------------------------------------------- #


class _SelfTool(R._RecordTool):  # noqa: SLF001 — the records module's own base
    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _DESC.set(None)
        return _stash(await super().capture_undo(args, ctx))


class PreferenceListTool(Tool):
    name = PREFERENCE_READER
    description = (
        "List the preferences and lessons the user kept (what Iron Jarvis remembers about how "
        "to answer them), each with the id preference_edit and preference_forget take. Read-only."
    )
    input_schema = {"type": "object", "properties": {"query": {"type": "string"}}}
    reversibility = Reversibility.READONLY

    def __init__(self, platform: Any) -> None:
        self.platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        kept = await asyncio.to_thread(kept_preferences, self.platform)
        want = _ref_words(str(args.get("query") or ""))
        rows = [p for p in kept if not want or want & _ref_words(p["text"])][:LIST_LIMIT]
        if not rows:
            return ToolResult(
                ok=True,
                output="No kept preference matches." if want else "You have no kept preferences or lessons yet.",
                data={"preferences": []},
            )
        lines = [f"- {p['text']} (id {p['id']})" for p in rows]
        return ToolResult(
            ok=True,
            output="Kept preferences and lessons:\n" + "\n".join(lines),
            data={"preferences": [{"id": p["id"], "text": p["text"], "source": p["source"]} for p in rows]},
        )


class _PreferenceTool(_SelfTool):
    RECORD = "preference"

    def _target(self, args: dict[str, Any]) -> str:
        return " ".join(str(args.get("preference") or "").split())

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_preference(self.platform, self._target(args))[0]


class PreferenceEditTool(_PreferenceTool):
    name = "preference_edit"
    description = (
        "Change the words of a preference or lesson the user kept (`preference` = its id from "
        "preference_list, or its words; `text` = the new sentence, at most 280 characters). The "
        "user approves on a card that quotes both; the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {"preference": {"type": "string"}, "text": {"type": "string"}},
        "required": ["preference", "text"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..learning import preferences as P

        pref_id, problem = await asyncio.to_thread(resolve_preference, self.platform, self._target(args))
        if not pref_id:
            return ToolResult(ok=False, error=problem)
        before = await asyncio.to_thread(snap_preference, self.platform, pref_id) or {}
        try:
            row = await asyncio.to_thread(P.edit, _engine(self.platform), pref_id, args.get("text"))
        except P.PreferenceInvalid as exc:
            return ToolResult(ok=False, error=str(exc))
        except P.PreferenceConflict as exc:
            return ToolResult(ok=False, error=str(exc))
        except KeyError:
            return ToolResult(ok=False, error="That preference is no longer there.")
        after = await asyncio.to_thread(snap_preference, self.platform, pref_id)
        _note(after=after)
        return ToolResult(
            ok=True,
            output=f"Changed the preference {_q(before.get('text'))} to {_q(row.text)}.",
            data={
                "record_change": self._card(
                    "Preference changed", "Preference", before.get("text"), row.text, f"preference.{pref_id}"
                )
            },
        )


class PreferenceForgetTool(_PreferenceTool):
    name = "preference_forget"
    description = (
        "Forget a preference or lesson the user kept (`preference` = its id from preference_list, "
        "or its words), so no later answer follows it. The user approves on a card that quotes it; "
        "Undo keeps it again."
    )
    input_schema = {"type": "object", "properties": {"preference": {"type": "string"}}, "required": ["preference"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..learning import preferences as P

        pref_id, problem = await asyncio.to_thread(resolve_preference, self.platform, self._target(args))
        if not pref_id:
            return ToolResult(ok=False, error=problem)
        before = await asyncio.to_thread(snap_preference, self.platform, pref_id) or {}
        try:
            await asyncio.to_thread(P.forget, _engine(self.platform), pref_id)
        except P.PreferenceConflict as exc:
            return ToolResult(ok=False, error=str(exc))
        except KeyError:
            return ToolResult(ok=False, error="That preference is no longer there.")
        _note(after=None)
        return ToolResult(
            ok=True,
            output=f"Forgot the preference {_q(before.get('text'))}.",
            data={
                "record_change": self._card(
                    "Preference forgotten", "Preference", before.get("text"), None, f"preference.{pref_id}"
                )
            },
        )


class LessonAddTool(_SelfTool):
    name = "lesson_add"
    RECORD = "preference"
    description = (
        "Add a lesson the user states for every later answer (`text` = one sentence, at most 280 "
        "characters, in the user's words: \"Always cite the IRS section.\"). The user approves on a "
        "card that quotes it; Undo removes it again."
    )
    input_schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        _DESC.set(None)
        return _stash(await R.capture_create_undo(self.platform, "preference", "", pending=True))

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..learning import preferences as P

        try:
            text = P.clean_text(args.get("text"))
        except P.PreferenceInvalid as exc:
            return ToolResult(ok=False, error=str(exc))
        kept = await asyncio.to_thread(kept_preferences, self.platform)
        if any(_same_words(p["text"].casefold(), text.casefold()) for p in kept):
            return ToolResult(ok=False, error=f"{_q(text)} is already kept; nothing was added.")
        try:
            row = await asyncio.to_thread(P.add_lesson, _engine(self.platform), text)
        except P.PreferenceInvalid as exc:
            return ToolResult(ok=False, error=str(exc))
        R.bind_created(row.id)
        after = await asyncio.to_thread(snap_preference, self.platform, row.id)
        _note(after=after)
        data: dict[str, Any] = {"id": row.id, "text": row.text}
        if R._CHANGE_ID.get():  # noqa: SLF001 — a card only with an Undo
            data["record_change"] = self._card("Lesson added", "Lesson", None, row.text, f"preference.{row.id}")
        return ToolResult(ok=True, output=f"Added the lesson {_q(row.text)}; every later answer follows it.", data=data)


class _PersonaTool(_SelfTool):
    RECORD = "persona"

    def _target(self, args: dict[str, Any]) -> str:
        return " ".join(str(args.get("persona") or "").split())

    def _resolve(self, args: dict[str, Any]) -> str:
        return resolve_persona(self.platform, self._target(args))[0]


def _too_long(*texts: Any) -> str:
    for t in texts:
        if len(str(t or "")) > PERSONA_TEXT_MAX:
            return f"A persona's words are kept to {PERSONA_TEXT_MAX} characters; say it more briefly."
    return ""


class PersonaCreateTool(_SelfTool):
    name = "persona_create"
    RECORD = "persona"
    description = (
        "Make a new chat persona (a voice and manner the chat can answer in): `name` (shown in the "
        "chat's persona menu), `prompt` (how it answers, written to the assistant: \"You are a "
        "careful CPA…\") and an optional one-line `description`. The user approves on a card; Undo "
        "removes it again. To make it the default, change the default persona setting after."
    )
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "prompt": {"type": "string"}, "description": {"type": "string"}},
        "required": ["name", "prompt"],
    }

    def _target(self, args: dict[str, Any]) -> str:
        return " ".join(str(args.get("name") or "").split())

    async def capture_undo(self, args: dict[str, Any], ctx: ToolContext) -> dict[str, Any] | None:
        from ..personas import slugify

        _DESC.set(None)
        name = self._target(args)
        if not name:
            return None
        return _stash(await R.capture_create_undo(self.platform, "persona", slugify(name)))

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..personas import PersonaError, create_persona_record, slugify

        name = self._target(args)
        prompt = str(args.get("prompt") or "").strip()
        description = " ".join(str(args.get("description") or "").split())
        if not name:
            return ToolResult(ok=False, error="A persona needs a name.")
        if not prompt:
            return ToolResult(ok=False, error="Say how the persona answers (its instructions).")
        problem = _too_long(name, prompt, description) or _scan_problem(name, prompt, description)
        if problem:
            return ToolResult(ok=False, error=problem)
        slug = slugify(name)
        taken = await asyncio.to_thread(all_personas, self.platform)
        if any(p["name"] == slug for p in taken):
            return ToolResult(
                ok=False, error=f"There is already a persona called {_q(name)}; ask to change it instead."
            )
        try:
            out = await asyncio.to_thread(
                create_persona_record, _engine(self.platform), _builtins(self.platform),
                name=name, title=name, description=description, prompt=prompt,
            )
        except PersonaError as exc:
            return ToolResult(ok=False, error=f"{str(exc)[:1].upper()}{str(exc)[1:]}.")
        after = await asyncio.to_thread(snap_persona, self.platform, out["created"])
        _note(after=after)
        words = _persona_words(after)
        data: dict[str, Any] = {"name": out["created"]}
        if R._CHANGE_ID.get():  # noqa: SLF001
            data["record_change"] = self._card(
                "Persona created", f"Persona {_q(name)}", None, words, f"persona.{out['created']}"
            )
        return ToolResult(ok=True, output=f"Made the persona {_q(name)} ({_short(prompt, 80)}).", data=data)


class PersonaUpdateTool(_PersonaTool):
    name = "persona_update"
    description = (
        "Change a chat persona (`persona` = its name): its instructions (`prompt`), its `title` or "
        "its one-line `description`; only the fields given change. A built-in persona keeps its "
        "own words underneath: your version wins until it is reset. The user approves on a card; "
        "the change has an Undo."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "persona": {"type": "string"},
            "prompt": {"type": "string"},
            "title": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["persona"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..personas import PersonaError, save_persona_record

        slug, problem = await asyncio.to_thread(resolve_persona, self.platform, self._target(args))
        if not slug:
            return ToolResult(ok=False, error=problem)
        given = {k: args.get(k) for k in _PERSONA_FIELDS if isinstance(args.get(k), str) and args.get(k).strip()}
        if not given:
            return ToolResult(ok=False, error="Say what to change: its instructions, title or description.")
        problem = _too_long(*given.values()) or _scan_problem(*given.values())
        if problem:
            return ToolResult(ok=False, error=problem)
        current = next((p for p in await asyncio.to_thread(all_personas, self.platform) if p["name"] == slug), {})
        values = {k: str(given.get(k, current.get(k) or "")).strip() for k in _PERSONA_FIELDS}
        if _persona_same(values, current):
            return ToolResult(ok=False, error=f"The persona {_q(current.get('title') or slug)} already reads like that.")
        try:
            await asyncio.to_thread(
                save_persona_record, _engine(self.platform), _builtins(self.platform), slug, **values
            )
        except PersonaError as exc:
            return ToolResult(ok=False, error=f"{str(exc)[:1].upper()}{str(exc)[1:]}.")
        after = await asyncio.to_thread(snap_persona, self.platform, slug)
        _note(after=after)
        title = values["title"] or slug
        return ToolResult(
            ok=True,
            output=f"Changed the persona {_q(title)}.",
            data={
                "record_change": self._card(
                    "Persona changed", f"Persona {_q(title)}", _persona_words(current), _persona_words(after),
                    f"persona.{slug}",
                )
            },
        )


class PersonaDeleteTool(_PersonaTool):
    name = "persona_delete"
    description = (
        "Remove a chat persona the user made (`persona` = its name), or reset a built-in one to its "
        "own words. The user approves on a card; Undo brings it back."
    )
    input_schema = {"type": "object", "properties": {"persona": {"type": "string"}}, "required": ["persona"]}

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        from ..personas import delete_persona_record

        slug, problem = await asyncio.to_thread(resolve_persona, self.platform, self._target(args))
        if not slug:
            return ToolResult(ok=False, error=problem)
        builtin = slug in _builtins(self.platform)
        before = await asyncio.to_thread(snap_persona, self.platform, slug)
        if before is None:
            return ToolResult(
                ok=False,
                error=f"{_q(slug)} is a built-in persona with no changes of yours, so there is nothing to "
                "remove; ask to change it instead.",
            )
        try:
            await asyncio.to_thread(delete_persona_record, _engine(self.platform), _builtins(self.platform), slug)
        except KeyError:
            return ToolResult(ok=False, error="That persona is no longer there.")
        _note(after=None)
        title = before.get("title") or slug
        default = str(getattr(self.platform.config, "default_persona", "") or "").strip()
        warn = ""
        if not builtin and default == slug:
            warn = (
                f" It was the default persona, so chats without a persona now read {_q(slug)} as plain "
                "instructions; change the default persona setting."
            )
        verb = "Reset the built-in persona" if builtin else "Removed the persona"
        return ToolResult(
            ok=True,
            output=f"{verb} {_q(title)}.{warn}",
            data={
                "record_change": self._card(
                    "Persona reset" if builtin else "Persona removed", f"Persona {_q(title)}",
                    _persona_words(before), "its own words" if builtin else None, f"persona.{slug}",
                )
            },
        )


def self_tools(platform: Any) -> list[Tool]:
    """The seven tools, for ``records.record_tools``."""
    return [
        PreferenceListTool(platform),
        PreferenceEditTool(platform),
        PreferenceForgetTool(platform),
        LessonAddTool(platform),
        PersonaCreateTool(platform),
        PersonaUpdateTool(platform),
        PersonaDeleteTool(platform),
    ]


# --------------------------------------------------------------------------- #
# arming — which sentences offer these tools
# --------------------------------------------------------------------------- #

_SPAN = r"(?P<span>(?:[\w'’&-]+\s+){0,6}?)"
_TAIL = r"(?P<tail>[^\n]{0,100})"
_PREF_NOUN = r"(?P<noun>preferences?|lessons?)(?![\w-])"

#: A word right BEFORE "preference" / "lesson" that makes it someone else's
#: ("my seat preference", "cookie preferences", "a guitar lesson").
_NOT_OURS_BEFORE = frozenset(
    {
        "seat", "meal", "dietary", "food", "cookie", "cookies", "privacy", "email", "marketing",
        "communication", "communications", "notification", "notifications", "browser", "chrome", "edge",
        "firefox", "safari", "windows", "outlook", "gmail", "account", "user", "customer", "travel",
        "flight", "hotel", "room", "font", "display", "keyboard", "printer", "system", "sharing",
        "app", "apple", "ios", "android", "iphone", "phone", "shipping", "delivery", "payment",
        "contact", "language", "time", "date", "guitar", "piano", "violin", "drum", "drums", "music",
        "voice", "singing", "dance", "swim", "swimming", "driving", "tennis", "golf", "ski", "skiing",
        "yoga", "cooking", "art", "math", "maths", "history", "science", "spanish", "french", "german",
        "english", "chinese", "japanese", "online", "private", "zoom", "first", "next", "last",
        "today's", "tomorrow's", "sunday", "school", "bible", "life", "hard", "valuable", "big",
        "important", "biggest", "sexual", "gender", "political", "career", "investment", "risk",
        "tax", "client", "clients", "kid's", "kids", "son's", "daughter's", "student", "students",
    }
)
#: Right AFTER the noun: it is a document, a course or a lesson's own part.
_NOT_OURS_AFTER = re.compile(
    r"\s*(?:-|plans?\b|planning\b|planner\b|learned\b|learnt\b|notes?\b|books?\b|schedules?\b|slides?\b|decks?\b"
    r"|videos?\b|recordings?\b|times?\b|fees?\b|prices?\b|packages?\b|centers?\b|centres?\b|pages?\b|panes?\b"
    r"|menus?\b|settings\b|dialogs?\b|windows?\b|files?\b|folders?\b|templates?\b|modules?\b|units?\b"
    r"|of\b|from\s+(?:the|my|this|our|a|an|his|her|their)\b|in\s+(?:my|the|this|our|his|her|their|a|an)\s+"
    r"(?:browser|account|profile|app|course|class|calendar|deck|slides?|doc|document|notebook|file|form|survey"
    r"|spreadsheet|report|plan|syllabus|curriculum|booking|reservation|order|lms|website|site)\b"
    r"|in\s+(?:chrome|edge|firefox|safari|outlook|gmail|word|excel|windows|teams|zoom|canvas|moodle|notion)\b"
    # Review (v1.438.0): the noun is a MODIFIER of the thing that follows
    # ("the preference cookie banner", "the lesson column"), or a numbered
    # course unit ("lesson 3 slides").
    r"|\d|(?:cookies?|banners?|links?|columns?|fields?|rows?|forms?|surveys?|toggles?|checkbox(?:es)?"
    r"|tabs?|cards?|boxes|box|sections?|headers?|footers?|buttons?|options?|polls?|titles?|headings?)\b"
    r"|['’]s\b)",
    re.IGNORECASE,
)
#: A preposition among the words BEFORE the noun makes the thing something
#: else ("remove the first page of the lesson"); "all of" is still ours.
_PRE_PREP = re.compile(r"\b(?:of|about|on|for|with|from|to|in|into|at|by|per|like)\b", re.IGNORECASE)

_P_FORGET = re.compile(
    r"(?:forget|delete|remove|drop|erase|clear|discard|get\s+rid\s+of|unlearn|throw\s+(?:out|away)"
    r"|stop\s+(?:remembering|using|following|applying))\s+" + _SPAN + _PREF_NOUN + _TAIL,
    re.IGNORECASE,
)
_P_FORGET_THAT = re.compile(
    r"(?:forget|stop\s+remembering)\s+(?:that|what|how)\s+i\s+(?:said\s+about|told\s+you\s+about|like|liked"
    r"|prefer|preferred|want|wanted\s+(?:you\s+to\s+)?|hate|dislike|don['’]?t\s+like)\b" + _TAIL,
    re.IGNORECASE,
)
_P_EDIT = re.compile(
    r"(?:change|edit|update|reword|rewrite|fix|replace|modify|adjust|tweak|amend|correct)\s+" + _SPAN
    + _PREF_NOUN + _TAIL,
    re.IGNORECASE,
)
_P_ADD_LESSON = re.compile(
    r"(?:add|make|create|save|write|record|note|keep|store|remember)\s+(?:down\s+)?"
    r"(?:(?:this|that|it)\s+(?:as\s+)?)?(?:a|an|another|the|this|one|my|as\s+a)?\s*(?:new\s+)?lessons?(?![\w-])"
    + _TAIL,
    re.IGNORECASE,
)
#: Review (v1.438.0): a course, a class or teaching material ANYWHERE in the
#: bounded tail ("add a lesson about depreciation to my CPE course", "a new
#: lesson to our onboarding course") — the lesson is a teacher's.
_LESSON_FOR_A_COURSE = re.compile(
    r"\b(?:to|for|in|into|on)\s+(?:my|the|our|this|a|an|his|her|their|your|tomorrow'?s|next|each|every)\s+"
    r"(?:[\w'’&-]+\s+){0,3}?(?:course|courses|class|classes|curriculum|syllabus|module|unit|workshop"
    r"|webinar|seminar|training|binder|workbook|cpe|lms|onboarding|students?|kids|children|homeschool"
    r"|tutoring|lecture|outline|slides?|deck|textbook|lesson\s+plan|interns?|trainees?|hires?|staff"
    r"|employees?|associates?|apprentices?|juniors?)\b",
    re.IGNORECASE,
)
#: After "add a lesson": a teacher's lesson, not one for Jarvis.
_LESSON_FOR_PEOPLE = re.compile(
    r"\s*(?:(?:for|to|in|into|on|about)\s+(?:my|the|our|this|a|an|his|her|their|tomorrow'?s|next)?\s*"
    r"(?:students?|class(?:es)?|kids|children|course|curriculum|syllabus|module|unit|workshop|training|team"
    r"|calendar|planner|notebook|slides?|deck|lms|canvas|moodle|classroom|school|week|semester|term|grade"
    r"|homeschool|tutoring|session|piano|guitar|math|history|science)\b|plans?\b|planning\b|notes\b|learned\b)",
    re.IGNORECASE,
)
_PERSONA_NOUN = r"personas?(?![\w-])"
_P_PERSONA_CREATE = re.compile(
    r"(?:make|create|add|build|set\s*up|setup|design|write|draft|define|give\s+me|i\s+(?:want|need)"
    r"|i\s+would\s+like|i['’]d\s+like)\s+(?:me\s+|us\s+)?(?:a|an|new|another|one|my\s+own)\s+(?:new\s+)?"
    r"(?P<span>(?:[\w'’&-]+\s+){0,3}?)" + _PERSONA_NOUN + _TAIL,
    re.IGNORECASE,
)
_P_PERSONA_NEW = re.compile(
    r"(?:a\s+)?new\s+" + _PERSONA_NOUN
    + r"\s*(?:$|:|called\b|named\b|that\b|who\b|which\b|for\s+(?:you|jarvis|chat)\b)",
    re.IGNORECASE,
)
_P_PERSONA_CHANGE = re.compile(
    r"(?:change|edit|update|rename|tweak|rewrite|modify|adjust|improve|fix|reword)\s+"
    r"(?P<span>(?:[\w'’&-]+\s+){0,4}?)" + _PERSONA_NOUN + _TAIL,
    re.IGNORECASE,
)
_P_PERSONA_DELETE = re.compile(
    r"(?:delete|remove|drop|get\s+rid\s+of|reset|restore|revert|erase|discard)\s+"
    r"(?P<span>(?:[\w'’&-]+\s+){0,4}?)" + _PERSONA_NOUN + _TAIL,
    re.IGNORECASE,
)
#: A persona that is a marketing or a story's ("a buyer persona", "a user
#: persona", "the persona of the narrator").
_NOT_OUR_PERSONA_BEFORE = frozenset(
    {
        "buyer", "buyers", "customer", "customers", "user", "users", "marketing", "target", "audience",
        "brand", "ideal", "client", "clients", "shopper", "fictional", "character", "characters", "ux",
        "design", "player", "game", "b2b", "b2c", "sales", "patient", "donor", "student", "learner",
        "employee", "candidate", "investor", "public", "online", "social", "instagram", "tiktok",
        "linkedin", "twitter", "stage", "alter", "villain", "hero", "narrator", "persona",
    }
)
_NOT_OUR_PERSONA_AFTER = re.compile(
    r"\s*(?:of\b|-|templates?\b|cards?\b|docs?\b|documents?\b|sheets?\b|maps?\b|research\b|interviews?\b"
    r"|(?:columns?|fields?|rows?|photos?|pictures?|images?|sections?|slides?|names?\s+tags?|boxes|box"
    r"|headers?|tabs?|labels?|icons?|avatars?\s+files?)\b"
    r"|from\s+(?:the|my|our|this|a|an|his|her|their)\s+(?!(?:chat|chats|menu|list|app|jarvis|iron|personas?)\b)"
    r"|for\s+(?:(?:my|our|the|a|an|this|his|her|their|each|every|your|all)\s+(?:[\w'’&-]+\s+){0,3}?)?"
    r"(?:marketing|campaign|product|brand|business|company"
    r"|startup|website|site|app|novel|story|book|game|pitch|deck|ux|research|study|survey|ad|ads|newsletter"
    r"|blog|course|class|presentation|report|client|customers?|audience|launch|funnel|screenplay|script|play"
    r"|podcast|channel|dnd|d&d|campaign|segments?|markets?|personas?|demographics?|niches?)\b"
    r"|in\s+(?:my|the|this|our|his|her|their|a|an)\s+(?:deck|slides?|document|doc|file|report|novel|story|book"
    r"|script|screenplay|presentation|draft|brief|pitch|game|marketing|campaign|plan)\b)",
    re.IGNORECASE,
)
_LIST_RX = re.compile(
    r"\b(?:what|which|show|list|see|tell\s+me|read\s+me|give\s+me|review)\b[^.!?\n]{0,40}?\b"
    r"(?:preferences|lessons)\b(?![\w-])",
    re.IGNORECASE,
)
#: …and the list is the USER's kept one ("my preferences", "the lessons you
#: kept", "what preferences do you have about me") — never "what lessons did
#: you learn from the project", never "your preference, tea or coffee".
_MINE = re.compile(
    r"\b(?:my|mine|kept|saved|stored|remembered|you\s+(?:have|keep|kept|remember|know|hold|follow|use)"
    r"|about\s+me|for\s+me|of\s+mine)\b",
    re.IGNORECASE,
)


def _last_word(span: str) -> str:
    words = (span or "").split()
    return words[-1].strip("'.&-’").lower() if words else ""


#: Review (v1.438.0): a possessive among the words before the noun makes it
#: SOMEONE ELSE's ("forget the client's preference for paper statements",
#: "update Mrs. Lopez's mailing preference") — never the user's own kept one.
_POSSESSIVE = re.compile(r"[a-z0-9]['’]s?(?=\s|$)", re.IGNORECASE)
_OURS_POSSESSIVE = re.compile(r"\b(?:jarvis|iron\s+jarvis)['’]s?(?=\s|$)", re.IGNORECASE)


def _someone_elses(span: str) -> bool:
    return _POSSESSIVE.search(_OURS_POSSESSIVE.sub(" ", span or "")) is not None


def _pref_ok(span: str, tail: str) -> bool:
    if _last_word(span) in _NOT_OURS_BEFORE or _someone_elses(span):
        return False
    if _PRE_PREP.search(re.sub(r"\ball\s+of\b", " ", span or "", flags=re.IGNORECASE)):
        return False
    # Review (v1.438.0): "change the lesson title in the Canvas course".
    return _NOT_OURS_AFTER.match(tail or "") is None and _LESSON_FOR_A_COURSE.search(tail or "") is None


def _persona_ok(span: str, tail: str) -> bool:
    if _last_word(span) in _NOT_OUR_PERSONA_BEFORE or _someone_elses(span):
        return False
    if _PRE_PREP.search(span or ""):
        return False
    return _NOT_OUR_PERSONA_AFTER.match(tail or "") is None


def _clauses(text: str) -> list[str]:
    """The message cut into clauses, filler taken off (the projects module's
    own linear cutter, reused — never a second copy)."""
    from .records_projects import _clauses as cut

    return cut(text)


def _pref_writer_clause(c: str) -> bool:
    for rule in (_P_FORGET, _P_EDIT):
        m = rule.match(c)
        if m is not None and _pref_ok(m.group("span"), m.group("tail")):
            return True
    return _P_FORGET_THAT.match(c) is not None


def _lesson_add_clause(c: str) -> bool:
    m = _P_ADD_LESSON.match(c)
    tail = m.group("tail") or "" if m is not None else ""
    return (
        m is not None
        and _LESSON_FOR_PEOPLE.match(tail) is None
        and _NOT_OURS_AFTER.match(tail) is None
        and _LESSON_FOR_A_COURSE.search(tail) is None
    )


def _persona_clause(c: str) -> bool:
    m = _P_PERSONA_CREATE.match(c)
    if m is not None and _persona_ok(m.group("span"), m.group("tail")):
        return True
    if _P_PERSONA_NEW.match(c) is not None:
        return True
    for rule in (_P_PERSONA_CHANGE, _P_PERSONA_DELETE):
        m = rule.match(c)
        if m is not None and _persona_ok(m.group("span"), m.group("tail")):
            return True
    return False


def _lists(text: str) -> bool:
    """A request or question to SEE the kept preferences."""
    for m in _LIST_RX.finditer(text or ""):
        before = text[max(0, m.start()): m.end()]
        span = re.sub(r"(?i)\b(?:preferences?|lessons?)\b\W*$", "", before)
        tail = text[m.end(): m.end() + 100]
        if (
            _last_word(span) not in _NOT_OURS_BEFORE
            and _NOT_OURS_AFTER.match(tail) is None
            and _MINE.search(before + " " + tail[:40]) is not None
        ):
            return True
    return False


def self_tools_for(text: str) -> list[str]:
    """The tools of this module a chat message plausibly asks for (called
    ONCE by ``records.record_tools_for``). A QUESTION keeps only the reader
    ``preference_list``: "how do I make a persona?" arms no writer. Never
    raises."""
    try:
        t = str(text or "")[:4000]
        low = t.lower()
        if not any(w in low for w in ("preference", "lesson", "persona", "forget", "stop remembering")):
            return []
        clauses = _clauses(t)
        pref = any(_pref_writer_clause(c) for c in clauses)
        lesson = any(_lesson_add_clause(c) for c in clauses)
        persona = any(_persona_clause(c) for c in clauses)
        out: list[str] = []
        if pref or lesson or _lists(t):
            out.append(PREFERENCE_READER)
        if R.is_question(t):
            return out
        if pref:
            out += ["preference_edit", "preference_forget"]
        if lesson:
            out.append("lesson_add")
        if persona:
            out += list(PERSONA_TOOLS)
        return list(dict.fromkeys(out))
    except Exception:  # noqa: BLE001 — a matcher fault arms nothing new
        log.debug("preference/persona arming failed", exc_info=True)
        return []
