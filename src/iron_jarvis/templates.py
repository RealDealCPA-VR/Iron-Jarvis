"""Durable store for saved prompts / task templates (daily-driver UX).

:class:`TemplateStore` persists :class:`~iron_jarvis.core.models.SavedPromptRecord`
rows so a user can re-run a frequent task with one click instead of retyping it.
Mirrors :class:`~iron_jarvis.workflows.store.WorkflowStore` (refresh-before-detach
so the returned record stays usable after the session closes).
"""

from __future__ import annotations

import re

from sqlalchemy import Engine
from sqlmodel import select

from .core.db import session_scope
from .core.models import AgentType, SavedPromptRecord


def _normalize_agent_type(raw: object) -> str:
    """Any agent identifier -> its canonical string (v1.128.0).

    The pickers offer builtin AND dynamic agent names, so this must accept any
    non-empty string — the old ``AgentType(...)`` cast 400'd on every dynamic
    agent. Legacy rows persisted the enum NAME ("BUILDER"); map those back to
    the value ("builder") so old templates keep working after the column went
    plain-string."""
    if isinstance(raw, AgentType):
        return raw.value
    text = str(raw or "").strip()
    if not text:
        return AgentType.BUILDER.value
    if text in AgentType.__members__:  # legacy enum NAME from old rows
        return AgentType[text].value
    return text


class TemplateStore:
    """Persist / list / fetch / remove saved task templates."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def create(
        self,
        name: str,
        task: str,
        agent_type: AgentType | str = AgentType.BUILDER,
        provider: str | None = None,
        model: str | None = None,
        description: str = "",
    ) -> SavedPromptRecord:
        with session_scope(self.engine) as db:
            row = SavedPromptRecord(
                name=name.strip() or "Untitled",
                task=task,
                agent_type=_normalize_agent_type(agent_type),
                provider=provider,
                model=model,
                description=(description or "").strip(),
            )
            db.add(row)
            db.commit()
            db.refresh(row)  # un-expire attrs so the detached record stays usable
            return row

    def update(
        self,
        prompt_id: str,
        *,
        name: str | None = None,
        task: str | None = None,
        agent_type: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        description: str | None = None,
        clear_model: bool = False,
    ) -> SavedPromptRecord | None:
        """Edit a template in place (v1.128.0 — fixing one used to mean delete +
        retype everything). ``None`` leaves a field alone; ``clear_model=True``
        drops a pinned provider/model back to the session default (None can't
        express that)."""
        with session_scope(self.engine) as db:
            row = db.get(SavedPromptRecord, prompt_id)
            if row is None:
                return None
            if name is not None and name.strip():
                row.name = name.strip()
            if task is not None and task.strip():
                row.task = task
            if agent_type is not None:
                row.agent_type = _normalize_agent_type(agent_type)
            if clear_model:
                row.provider = None
                row.model = None
            else:
                if provider is not None:
                    row.provider = provider or None
                if model is not None:
                    row.model = model or None
            if description is not None:
                row.description = description.strip()
            db.add(row)
            db.commit()
            db.refresh(row)
            return row

    def suggest_from_history(
        self, *, min_count: int = 3, limit: int = 5
    ) -> list[dict]:
        """WATCH-ME-WORK mining: find task patterns the user keeps repeating
        in their session history and suggest each as a one-click template.

        Groups session tasks by token-set similarity (Jaccard ≥ 0.55), keeps
        groups with ``min_count``+ occurrences, and drops anything already
        covered by an existing template. Suggest-only — nothing is created
        until the user clicks save."""
        from .core.models import Session as SessionModel

        def toks(text: str) -> frozenset[str]:
            words = re.findall(r"[a-z]{3,}", (text or "").lower())
            stop = {"the", "and", "for", "with", "that", "this", "from", "into",
                    "please", "then", "them", "your", "file", "files"}
            return frozenset(w for w in words if w not in stop)

        def sim(a: frozenset, b: frozenset) -> float:
            if not a or not b:
                return 0.0
            return len(a & b) / len(a | b)

        with session_scope(self.engine) as db:
            tasks = [
                s.task for s in db.exec(select(SessionModel))
                if s.task and "[Continuing an earlier session" not in s.task
            ]
        existing = [toks(t.task) for t in self.list()]
        groups: list[dict] = []  # {sig, example, count}
        for task in tasks:
            sig = toks(task)
            if len(sig) < 3:
                continue
            for g in groups:
                if sim(sig, g["sig"]) >= 0.55:
                    g["count"] += 1
                    if len(task) < len(g["example"]):
                        g["example"] = task  # keep the crispest phrasing
                    break
            else:
                groups.append({"sig": sig, "example": task, "count": 1})
        out = []
        for g in sorted(groups, key=lambda x: -x["count"]):
            if g["count"] < min_count:
                continue
            if any(sim(g["sig"], e) >= 0.5 for e in existing):
                continue  # already a template
            words = [w for w in re.findall(r"[A-Za-z]{3,}", g["example"])][:4]
            out.append({
                "name": " ".join(words).title() or "Repeated task",
                "task": g["example"][:500],
                "count": g["count"],
            })
            if len(out) >= limit:
                break
        return out

    def seed_starters(self) -> int:
        """First-run only: when the store is EMPTY, add a few self-explanatory
        starter templates (each says when to use it). Returns how many were
        added — 0 whenever the user already has any template, so this never
        re-adds deleted starters. Seeds only the starters that need NO extra
        connection — a first-run template must work on click one."""
        if self.list():
            return 0
        seeded = 0
        for entry in STARTER_CATALOG:
            if entry.get("seed"):
                self.create(entry["name"], entry["task"], description=entry["description"])
                seeded += 1
        return seeded

    def list(self) -> list[SavedPromptRecord]:
        """Return every saved template, newest first. Legacy rows stored the
        agent enum NAME ("BUILDER") — normalize so callers only ever see
        canonical strings."""
        with session_scope(self.engine) as db:
            rows = list(
                db.exec(
                    select(SavedPromptRecord).order_by(
                        SavedPromptRecord.created_at.desc()
                    )
                )
            )
        for row in rows:
            row.agent_type = _normalize_agent_type(row.agent_type)
        return rows

    def get(self, prompt_id: str) -> SavedPromptRecord | None:
        with session_scope(self.engine) as db:
            row = db.get(SavedPromptRecord, prompt_id)
        if row is not None:
            row.agent_type = _normalize_agent_type(row.agent_type)
        return row

    def remove(self, prompt_id: str) -> bool:
        """Delete a template by id; returns False if it was absent."""
        with session_scope(self.engine) as db:
            row = db.get(SavedPromptRecord, prompt_id)
            if row is None:
                return False
            db.delete(row)
            db.commit()
            return True


# --- Starter catalog (v1.128.0) ---------------------------------------------
# A browsable library the user can add from ANY time — not just the empty-store
# first-run seed. Entries with ``seed: True`` are the connection-free trio the
# first run installs automatically. Requirement detection (below) annotates
# each entry live, so the page can say "needs a Pixio key — add it in Secrets"
# instead of letting the run fail.

STARTER_CATALOG: list[dict] = [
    {
        "id": "daily-briefing",
        "name": "Daily briefing",
        "task": (
            "Summarize my day so far: recent sessions and their outcomes, "
            "anything pending review or approval, and suggest the 3 most "
            "useful next actions."
        ),
        "description": "Use each morning (or after time away) to get oriented in one click.",
        "agent_type": "builder",
        "seed": True,
    },
    {
        "id": "summarize-document",
        "name": "Summarize a document",
        "task": (
            "Read the file I mention (or the newest file in my workspace) and "
            "produce a one-page summary: purpose, key numbers, decisions "
            "needed, and action items."
        ),
        "description": (
            "Use when you receive a long PDF/Word/Excel file and want the "
            "essence without reading it all."
        ),
        "agent_type": "builder",
        "seed": True,
    },
    {
        "id": "client-follow-up",
        "name": "Client follow-up email",
        "task": (
            "Draft a polite, professional follow-up email to a client about "
            "the topic I describe. Under 150 words, warm but direct, with a "
            "clear next step."
        ),
        "description": "Use when a client has gone quiet or you need a quick, well-worded nudge.",
        "agent_type": "builder",
        "seed": True,
    },
    {
        "id": "meeting-actions",
        "name": "Meeting notes to action items",
        "task": (
            "Turn the meeting notes I paste or attach into: decisions made, "
            "action items with owners and due dates, and a 5-line recap I can "
            "send to attendees."
        ),
        "description": "Use right after a meeting while the notes are still fresh.",
        "agent_type": "builder",
    },
    {
        "id": "excel-health-check",
        "name": "Excel workbook health check",
        "task": (
            "Open the Excel file I mention, profile each sheet, run a formula "
            "check for errors and broken references, and summarize any "
            "anomalies or numbers worth my attention."
        ),
        "description": "Use before sending or relying on a spreadsheet someone else built.",
        "agent_type": "builder",
    },
    {
        "id": "weekly-review",
        "name": "Weekly review",
        "task": (
            "Review this week's sessions and schedules: what shipped, what "
            "failed, what's still pending, and propose next week's top 3 "
            "priorities with a one-line rationale each."
        ),
        "description": "Use Friday afternoon or Monday morning to reset the week.",
        "agent_type": "builder",
    },
    {
        "id": "web-research-brief",
        "name": "Web research brief",
        "task": (
            "Research the topic I describe on the web and produce a sourced "
            "brief: key facts and numbers, notable disagreements, and a "
            "bottom line — with links to every source."
        ),
        "description": "Use when you need a grounded answer, not a from-memory guess.",
        "agent_type": "researcher",
    },
    {
        "id": "inbox-triage",
        "name": "Inbox triage",
        "task": (
            "Check my unread emails, group them by urgency, summarize each "
            "group in a line, and draft replies for the two most important."
        ),
        "description": "Use when the inbox got away from you.",
        "agent_type": "builder",
    },
    {
        "id": "generate-image",
        "name": "Generate an image",
        "task": (
            "Generate an image from my description: ask me for subject, style, "
            "and where it will be used if I haven't said, then produce two "
            "options."
        ),
        "description": "Use for quick logos, illustrations, and social posts.",
        "agent_type": "builder",
    },
    {
        "id": "telegram-status",
        "name": "Telegram status update",
        "task": (
            "Send me a Telegram message summarizing what finished today, "
            "anything that failed, and whatever is waiting on my approval."
        ),
        "description": "Use to get a pocket summary without opening the app.",
        "agent_type": "builder",
    },
]


# --- Schedule templates (v1.143.0) -------------------------------------------
# STARTER_CATALOG above is the *prompt* library (one-click tasks). This is the
# *schedule* library: ready-made recurring jobs the user can install with one
# click. Both are OPT-IN — nothing here is ever created on boot. ``seed_starters``
# deliberately does not look at this list, and no caller creates a schedule from
# it without an explicit user action (the Memory page's "Schedule weekly review"
# button POSTs /schedules with this exact payload).

#: The words a memory-review fire hands to a real agent session. The steward's
#: ``build_task`` composes a sharper, window-aware version of this at run time;
#: this is the durable, self-contained phrasing a schedule can carry on its own.
#: Note the second half: ADDING memory is free, REVISING it is suggest-only.
MEMORY_REVIEW_TASK = (
    "Review my recent conversations and tidy my long-term memory.\n\n"
    "1. Read what I've discussed since the last review and write any durable "
    "facts, decisions, and preferences into my memory as notes (use ltm_append "
    "— adding is always safe, I can undo any note).\n"
    "2. Then look over the notes themselves and PROPOSE housekeeping by calling "
    "`memory_propose` once per suggestion, using these four kinds: duplicate "
    "(two notes saying the same thing), stale (a note the facts have moved "
    "past), contradiction (two notes that disagree), merge (several notes that "
    "should be one).\n\n"
    "Never delete or rewrite an existing note yourself — file every such change "
    "with `memory_propose` for me to approve on the Memory page, with a "
    "one-line reason and the exact text you'd keep. Writing the suggestion in "
    "your summary instead of calling the tool means I never see it. Finish with "
    "a short summary of what you added and what you're proposing."
)

#: The one schedule template this release ships. ``name`` is the scheduler's
#: unique key, so it doubles as the "is it already installed?" check.
MEMORY_REVIEW_SCHEDULE: dict = {
    "id": "memory-review-weekly",
    "label": "Memory review — weekly",
    "name": "memory-review-weekly",
    "kind": "task",
    # Monday 9am — the same slot the Schedules page offers as "Weekly Mon 9am".
    "cron": "0 9 * * 1",
    "task": MEMORY_REVIEW_TASK,
    "description": (
        "Once a week, an agent reads your recent conversations, saves what's "
        "worth remembering, and suggests memory cleanups for you to approve."
    ),
}

#: Browsable schedule templates. OPT-IN: adding one is always a user click.
SCHEDULE_TEMPLATES: list[dict] = [MEMORY_REVIEW_SCHEDULE]


def schedule_template(template_id: str) -> dict | None:
    """One schedule template by id (or by its scheduler ``name``), else None."""
    wanted = (template_id or "").strip()
    for entry in SCHEDULE_TEMPLATES:
        if wanted in (entry["id"], entry["name"]):
            return entry
    return None


# --- Requirement detection (v1.128.0) ----------------------------------------
# "This template needs a connection you don't have yet" — said BEFORE the run
# fails, with a link to the exact page that fixes it. Checks are deliberately
# conservative: only flag needs we can map to a real tool/config on this
# install, and only when the task text actually implies the capability
# ("draft an email" needs nothing; "check my inbox" needs an email plug-in).

_MEDIA_RE = re.compile(
    r"\b(generate|create|make|design|produce|render)\b[^.]{0,60}"
    r"\b(image|images|logo|picture|photo|video|videos|song|music|jingle|"
    r"artwork|illustration|banner|thumbnail)\b",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(
    r"\b(check|read|scan|triage|send|reply to|forward|search)\b[^.]{0,60}"
    r"\b(email|emails|inbox|gmail|outlook|mailbox)\b"
    r"|\b(unread emails|my inbox)\b",
    re.IGNORECASE,
)
_WEB_RE = re.compile(
    r"\b(search the web|research|browse|look up|latest news|on the web|online sources)\b",
    re.IGNORECASE,
)


def analyze_requirements(
    task: str,
    provider: str | None,
    model: str | None,
    agent_type: str | None,
    *,
    selectable_models: list[dict],
    live_tools: list[str],
    has_secret,
    comm_config: dict,
    agent_names: list[str],
) -> list[dict]:
    """Annotate one template with everything it needs to actually run.

    Returns ``[{key, label, ok, detail, setup_path, setup_label}, ...]`` —
    only for requirements the task/pin actually implies, each carrying the
    dashboard page that sets it up. Pure function; the route injects live
    platform state so this stays trivially testable."""
    reqs: list[dict] = []
    text = task or ""

    # 1) Pinned provider/model must still be connected — the most common way a
    #    template silently rots (saved against an endpoint that moved/expired).
    if provider and model:
        ok = any(
            m.get("provider") == provider and m.get("model") == model
            for m in selectable_models
        )
        reqs.append({
            "key": "model",
            "label": f"Pinned model {provider} - {model}",
            "ok": ok,
            "detail": (
                "Connected and selectable." if ok else
                f"This template is pinned to {provider} - {model}, which isn't "
                "connected right now. Reconnect it, or edit the template to use "
                "the session default."
            ),
            "setup_path": "/connections",
            "setup_label": "Connections",
        })

    # 2) The agent it targets must still exist (dynamic agents can be deleted).
    agent = (agent_type or "").strip()
    if agent and agent_names and agent not in agent_names:
        reqs.append({
            "key": "agent",
            "label": f"Agent '{agent}'",
            "ok": False,
            "detail": (
                f"The agent type '{agent}' no longer exists. Recreate it, "
                "or edit the template to use a built-in agent."
            ),
            "setup_path": "/agents",
            "setup_label": "Agents",
        })

    # 3) Generative media -> the Pixio key must resolve.
    if _MEDIA_RE.search(text):
        ok = bool(has_secret("pixio"))
        reqs.append({
            "key": "media",
            "label": "Image/video/music generation (Pixio)",
            "ok": ok,
            "detail": (
                "Pixio key found." if ok else
                "Generating media needs a Pixio API key saved as the "
                "'pixio' secret."
            ),
            "setup_path": "/secrets",
            "setup_label": "Secrets",
        })

    # 4) Reading/sending real email -> an email-capable plug-in must be live.
    #    (Drafting text needs nothing — the verb list deliberately omits it.)
    if _EMAIL_RE.search(text):
        ok = any(
            any(hint in t.lower() for hint in ("mail", "gmail", "outlook", "graph"))
            for t in live_tools
        )
        reqs.append({
            "key": "email",
            "label": "Email access",
            "ok": ok,
            "detail": (
                "An email-capable plug-in is connected." if ok else
                "Reading or sending email needs an email plug-in (e.g. Gmail or "
                "Outlook via MCP). Connect one under Tools -> Plug-ins."
            ),
            "setup_path": "/tools",
            "setup_label": "Tools → Plug-ins",
        })

    # 5) Web research -> the built-in web_search tool must be registered
    #    (DuckDuckGo fallback means no key is required — this only flags a
    #    build that somehow lost the tool).
    if _WEB_RE.search(text):
        ok = any(t == "web_search" or t.endswith("web_search") for t in live_tools)
        reqs.append({
            "key": "web",
            "label": "Web search",
            "ok": ok,
            "detail": (
                "Web search is available." if ok else
                "The web_search tool isn't loaded on this install."
            ),
            "setup_path": "/tools",
            "setup_label": "Tools",
        })

    # 6) Chat-channel delivery -> that channel must be configured.
    lowered = text.lower()
    for channel in ("telegram", "slack"):
        if channel in lowered:
            ok = bool((comm_config or {}).get(channel))
            reqs.append({
                "key": channel,
                "label": f"{channel.title()} connection",
                "ok": ok,
                "detail": (
                    f"{channel.title()} is set up." if ok else
                    f"Delivering to {channel.title()} needs it configured under "
                    "Notifications."
                ),
                "setup_path": "/channels",
                "setup_label": "Notifications",
            })

    return reqs
