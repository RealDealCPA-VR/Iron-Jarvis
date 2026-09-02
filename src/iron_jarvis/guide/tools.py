"""The Guide agent's tools (v1.224.0) — how the built-in Iron Jarvis expert
LOOKS THINGS UP instead of guessing.

Three read-only tools, all default ``allow``:

* ``guide_search`` — the reference: bundled docs + live catalogs (version,
  providers, tools, skills, personas, agent types, API routes), ranked.
* ``guide_read`` — the full text of one section (by the label ``guide_search``
  returned) or a whole doc, so an answer can quote the real wording.
* ``app_search`` — the user's OWN things inside this install: projects, saved
  workflows, schedules, reflex rules, goals, skills, custom agents, chat
  threads, recent sessions, memory bases, custom tools — each hit with the
  dashboard path that opens it. This is "where is my …?", answered from the
  stores themselves rather than from memory of what the user once said.
* ``app_status`` — the one-screen overview of this install: version, home,
  connected models, active project, and how many of each thing exist.

Nothing here writes, spawns, or leaves the machine. A store that cannot be
read contributes nothing and is NAMED in the result as unreadable, so the
agent never reports "you have no schedules" because the scheduler was down.
"""

from __future__ import annotations

import logging
from typing import Any

from ..tools.base import Tool, ToolContext, ToolResult
from .corpus import GuideIndex, index_for, tokens

log = logging.getLogger("iron_jarvis.guide.tools")

#: Where each kind of thing opens in the dashboard.
_OPEN: dict[str, str] = {
    "project": "/projects/{id}",
    "workflow": "/workflows",
    "schedule": "/schedules",
    "reflex": "/reflex",
    "goal": "/goals",
    "skill": "/skills",
    "agent": "/agents",
    "thread": "/chat?thread={id}",
    "session": "/sessions/{id}",
    "memory_base": "/memory?scope=longterm",
    "custom_tool": "/tools",
    "persona": "/you",
}

#: Search order and the kinds a caller may ask for.
APP_KINDS: tuple[str, ...] = tuple(_OPEN)


#: Query words that name nothing ("do I have a schedule" → "schedule").
_QUERY_STOP = frozenset(
    "a an the and or of to in on at for by with is are was were be do does did "
    "how what which who why when where can could should would will i me my we "
    "our you your it its this that these those there here from into about as "
    "if than then so not no yes any some all have has had find show me open".split()
)
#: A hit needs at least this share of the meaningful query terms. Matching
#: ONE common word out of four ("thing") used to surface every skill in the
#: catalog for a query about nothing.
_MIN_MATCH = 0.6


def _score(query_tokens: list[str], *fields: Any) -> float:
    """Cheap relevance: the share of MEANINGFUL query terms present across
    the fields (0 unless at least ``_MIN_MATCH`` of them are), plus a bonus
    when the whole query appears verbatim."""
    terms = [t for t in query_tokens if t not in _QUERY_STOP and len(t) > 1] or list(query_tokens)
    if not terms:
        return 0.0
    text = " ".join(str(f or "") for f in fields).lower()
    hits = sum(1 for t in terms if t in text)
    if hits == 0 or hits / len(terms) < _MIN_MATCH:
        return 0.0
    score = hits / len(terms)
    if " ".join(terms) in text:
        score += 0.5
    return score


class GuideSearchTool(Tool):
    name = "guide_search"
    description = (
        "Search the Iron Jarvis reference — the app's own docs (Handbook, "
        "settings guides, vocabulary, spec, architecture) and live catalogs of "
        "THIS install (version, connected models, every tool, skill, persona, "
        "agent type and API route). Returns ranked sections with a label to "
        "pass to guide_read for the full text. Use it before answering any "
        "question about how Iron Jarvis works."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "the question or topic"},
            "k": {"type": "integer", "description": "max sections (default 6)"},
        },
        "required": ["query"],
    }

    def __init__(self, platform) -> None:
        self._platform = platform

    def _index(self) -> GuideIndex:
        return index_for(self._platform)

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, error="query is required")
        k = max(1, min(int(args.get("k") or 6), 20))
        idx = self._index()
        hits = idx.search(query, k=k)
        if not hits:
            return ToolResult(
                ok=True,
                output="(nothing in the reference matches — say so, and name the closest page to look at)",
                data={"hits": [], "missing_docs": idx.missing},
            )
        lines = []
        rows = []
        for score, s in hits:
            preview = " ".join(s.text.split())[:280]
            lines.append(f"[{s.label}] (score {score:.1f}{', live' if s.live else ''})\n  {preview}")
            rows.append(
                {"label": s.label, "doc": s.doc, "live": s.live,
                 "score": round(score, 2), "preview": preview}
            )
        note = ""
        if idx.missing:
            names = ", ".join(m["file"] for m in idx.missing)
            note = f"\n\n(note: {len(idx.missing)} reference file(s) missing from this install: {names})"
        return ToolResult(
            ok=True,
            output="\n\n".join(lines) + note,
            data={"hits": rows, "missing_docs": idx.missing},
        )


class GuideReadTool(Tool):
    name = "guide_read"
    description = (
        "Read the full text of one reference section — pass the exact label "
        "guide_search returned (e.g. 'The Handbook › Automation') — or a whole "
        "doc by slug (handbook, recommended-settings, local-models, reflex, "
        "computer-use, readme, vocabulary, spec, operating-manual). Quote from "
        "it rather than paraphrasing a setting or a hotkey from memory."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "a section label from guide_search"},
            "doc": {"type": "string", "description": "a doc slug, for the whole document"},
        },
    }

    #: A whole doc is capped so one call cannot flood the context.
    MAX_CHARS = 12000

    def __init__(self, platform) -> None:
        self._platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        label = str(args.get("label") or "").strip()
        doc = str(args.get("doc") or "").strip().lower()
        idx = index_for(self._platform)
        if label:
            for s in idx.sections():
                if s.label == label:
                    return ToolResult(
                        ok=True, output=f"## [{s.label}]\n{s.text}",
                        data={"label": s.label, "doc": s.doc, "chars": len(s.text)},
                    )
            near = [s.label for s in idx.sections() if label.lower() in s.label.lower()][:5]
            hint = f"; close labels: {near}" if near else ""
            return ToolResult(ok=False, error=f"no reference section labelled {label!r}{hint}")
        if doc:
            secs = [s for s in idx.docs if s.doc == doc]
            if not secs:
                known = sorted({s.doc for s in idx.docs})
                return ToolResult(ok=False, error=f"no doc {doc!r}; known docs: {known}")
            body = "\n\n".join(f"## [{s.label}]\n{s.text}" for s in secs)
            truncated = len(body) > self.MAX_CHARS
            if truncated:
                body = body[: self.MAX_CHARS].rstrip() + "\n…(truncated — ask for a section by label)"
            return ToolResult(
                ok=True, output=body,
                data={"doc": doc, "sections": len(secs), "truncated": truncated},
            )
        return ToolResult(ok=False, error="pass a section label or a doc slug")


class AppSearchTool(Tool):
    name = "app_search"
    description = (
        "Find the user's OWN things inside this Iron Jarvis install by name or "
        "topic: projects, saved workflows, schedules, reflex rules, goals, "
        "skills, custom agents, chat threads, recent sessions, memory bases, "
        "custom tools, personas. Each hit carries the dashboard path that "
        "opens it. Use it for 'where is my…', 'do I have a…', 'which schedule "
        "runs…'. Read-only. Optional kinds filter: "
        + ", ".join(APP_KINDS)
        + "."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "kinds": {
                "type": "array",
                "items": {"type": "string"},
                "description": "restrict to these kinds (default: all)",
            },
            "k": {"type": "integer", "description": "max hits (default 12)"},
        },
        "required": ["query"],
    }

    def __init__(self, platform) -> None:
        self._platform = platform

    # -- readers: each returns (kind, id, name, blurb) rows, or raises --------

    def _rows(self, kind: str) -> list[tuple[str, str, str, str]]:  # noqa: C901
        p = self._platform
        out: list[tuple[str, str, str, str]] = []
        if kind == "project":
            from sqlmodel import select

            from ..core.db import session_scope
            from ..core.models import Project

            with session_scope(p.engine) as db:
                for r in db.exec(select(Project)):
                    out.append((kind, r.id, r.name, f"{r.status}; folder: {r.root or 'none'}; {r.brief[:120]}"))
        elif kind == "workflow":
            from ..workflows.store import WorkflowStore

            for r in WorkflowStore(p.engine).list():
                out.append((kind, r.name, r.name, r.description or ""))
        elif kind == "schedule":
            for r in p.scheduler.list():
                payload = r.decoded_payload()
                what = payload.get("task") or payload.get("workflow") or payload.get("name") or ""
                out.append((kind, r.name, r.name, f"{r.kind}; {'enabled' if r.enabled else 'disabled'}; {str(what)[:120]}"))
        elif kind == "reflex":
            for r in p.reflex.list():
                out.append((kind, r.id, r.name or r.id, f"{r.source} '{r.match}' → {r.action} {r.target}"))
        elif kind == "goal":
            eng = getattr(p, "goal_engine", None)
            for r in (eng.store.list() if eng is not None else []):
                out.append((kind, r.id, r.name or r.id, f"{getattr(r, 'state', '')}; {r.contract_text[:120]}"))
        elif kind == "skill":
            for s in p.skills.list():
                out.append((kind, s.name, s.name, s.description or ""))
        elif kind == "agent":
            reg = getattr(p, "agents_registry", None)
            for r in (reg.list() if reg is not None else []):
                out.append((kind, r.name, r.name, r.description or ""))
        elif kind == "thread":
            from sqlmodel import select

            from ..core.db import session_scope
            from ..core.models import ChatThreadRecord

            with session_scope(p.engine) as db:
                stmt = select(ChatThreadRecord).order_by(ChatThreadRecord.updated_at.desc()).limit(300)  # type: ignore[attr-defined]
                for r in db.exec(stmt):
                    out.append((kind, r.id, r.title or "(untitled)", f"persona {r.persona or 'default'}"))
        elif kind == "session":
            from sqlmodel import select

            from ..core.db import session_scope
            from ..core.models import Session as SessionModel

            with session_scope(p.engine) as db:
                stmt = select(SessionModel).order_by(SessionModel.created_at.desc()).limit(300)  # type: ignore[attr-defined]
                for r in db.exec(stmt):
                    out.append((kind, r.id, r.task[:100], f"{r.status.value}; {r.agent_type.value}; {(r.summary or '')[:120]}"))
        elif kind == "memory_base":
            for name in p.ltm.sources():
                out.append((kind, name, name, "long-term memory base"))
        elif kind == "custom_tool":
            for spec in p.registry.specs():
                n = str(spec.get("name") or "")
                if n.startswith("custom:"):
                    out.append((kind, n, n, str(spec.get("description") or "")[:120]))
        elif kind == "persona":
            from ..personas.builtins import BUILTIN_PERSONAS

            for n, spec in BUILTIN_PERSONAS.items():
                out.append((kind, n, n, spec.get("description", "")))
        return out

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        query = str(args.get("query") or "").strip()
        if not query:
            return ToolResult(ok=False, error="query is required")
        k = max(1, min(int(args.get("k") or 12), 50))
        wanted = args.get("kinds") or list(APP_KINDS)
        kinds = [str(x).strip() for x in wanted if str(x).strip() in APP_KINDS]
        if not kinds:
            return ToolResult(ok=False, error=f"kinds must be among: {', '.join(APP_KINDS)}")
        qt = [t for t in tokens(query) if t not in _QUERY_STOP and len(t) > 1] or tokens(query)
        hits: list[tuple[float, str, str, str, str]] = []
        unreadable: list[str] = []
        for kind in kinds:
            try:
                rows = self._rows(kind)
            except Exception:  # noqa: BLE001 — one dead store must not hide the rest
                log.exception("app_search: %s store unreadable", kind)
                unreadable.append(kind)
                continue
            for k_, id_, name, blurb in rows:
                sc = _score(qt, name, blurb, id_)
                if sc > 0.0:
                    hits.append((sc, k_, id_, name, blurb))
        hits.sort(key=lambda h: h[0], reverse=True)
        hits = hits[:k]
        rows_out = [
            {
                "kind": k_,
                "id": id_,
                "name": name,
                "detail": blurb,
                "open": _OPEN[k_].replace("{id}", id_),
                "score": round(sc, 2),
            }
            for sc, k_, id_, name, blurb in hits
        ]
        if not rows_out:
            msg = f"(nothing in this install matches {query!r} across {', '.join(kinds)})"
        else:
            msg = "\n".join(
                f"- {r['kind']}: {r['name']} — {r['detail'][:140]}  (open: {r['open']})"
                for r in rows_out
            )
        if unreadable:
            msg += f"\n\n(could not read: {', '.join(unreadable)} — say so rather than claiming they are empty)"
        return ToolResult(ok=True, output=msg, data={"hits": rows_out, "unreadable": unreadable})


class AppStatusTool(Tool):
    name = "app_status"
    description = (
        "One-screen overview of THIS Iron Jarvis install: version, whether it "
        "is the packaged app, the state folder, default model, connected model "
        "providers, the active project, and counts of projects, workflows, "
        "schedules, reflex rules, goals, skills, custom agents, memory bases. "
        "Read-only; call it first for 'what's my setup' questions."
    )
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, platform) -> None:
        self._platform = platform

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        import sys

        from .. import __version__

        p = self._platform
        cfg = p.config
        data: dict[str, Any] = {
            "version": __version__,
            "frozen": bool(getattr(sys, "frozen", False)),
            "home": str(getattr(cfg, "home", "")),
            "default_provider": getattr(cfg, "default_provider", ""),
            "default_model": getattr(cfg, "default_model", ""),
            "active_project_id": getattr(cfg, "active_project_id", None),
        }
        try:
            data["providers"] = [
                {"provider": r.get("provider"), "class": r.get("class"), "available": bool(r.get("available"))}
                for r in p.providers.health()
            ]
        except Exception:  # noqa: BLE001
            data["providers"] = "unreadable"
        counts: dict[str, Any] = {}
        searcher = AppSearchTool(p)
        for kind in ("project", "workflow", "schedule", "reflex", "goal", "skill", "agent", "memory_base", "custom_tool"):
            try:
                counts[kind] = len(searcher._rows(kind))
            except Exception:  # noqa: BLE001
                counts[kind] = "unreadable"
        data["counts"] = counts
        lines = [
            f"Iron Jarvis {data['version']} ({'packaged app' if data['frozen'] else 'running from source'})",
            f"state home: {data['home']}",
            f"default model: {data['default_provider']} / {data['default_model'] or '(provider default)'}",
            f"active project: {data['active_project_id'] or 'none'}",
        ]
        if isinstance(data["providers"], list):
            live = [r["provider"] for r in data["providers"] if r["available"]]
            lines.append(f"providers available now: {', '.join(live) or 'none'}")
        else:
            lines.append("providers: unreadable")
        lines.append("counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
        return ToolResult(ok=True, output="\n".join(lines), data=data)


def guide_tools(platform) -> list[Tool]:
    """The Guide's tool set, bound to the assembled platform."""
    return [
        GuideSearchTool(platform),
        GuideReadTool(platform),
        AppSearchTool(platform),
        AppStatusTool(platform),
    ]


GUIDE_TOOL_NAMES: tuple[str, ...] = ("guide_search", "guide_read", "app_search", "app_status")
