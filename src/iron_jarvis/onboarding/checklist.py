"""The "first 4 steps to value" checklist.

A dynamic getting-started list whose every ``done`` flag is computed live from
real platform state — sessions run, documents touched, lessons learned, models
connected — so the first-run overlay and CLI always tell the truth. Everything
here is best-effort and offline: any query that can't run is treated as "not yet
done" rather than raising.
"""

from __future__ import annotations

from ..core.db import session_scope

#: Tool names that count as "worked with a document" (§ documents subsystem).
_DOC_TOOLS = {"read_document", "write_document", "create_document", "extract_pdf"}


def voice_backend_present(platform) -> tuple[bool, str | None]:
    """``(available, backend_label)`` for server-side dictation — read-only.

    Mirrors the daemon's ``_voice_backend`` PRESENCE check without touching the
    network: an OpenAI API key wins ("openai"), else a configured custom
    OpenAI-compatible endpoint ("custom"). Never raises — a missing vault key or
    config attribute just reports "no backend".
    """
    try:
        if platform.secrets.get("openai_api_key"):
            return True, "openai"
    except Exception:  # noqa: BLE001 — vault miss = not available
        pass
    try:
        base = (getattr(platform.config, "custom_base_url", None) or "").strip()
    except Exception:  # noqa: BLE001
        base = ""
    if base:
        return True, "custom"
    return False, None


def _provider_connected(platform) -> bool:
    """True if any *real* (non-mock) provider is available or logged in.

    The mock model is always available (offline), so it never counts as a real
    connection — only an Anthropic key or a logged-in browser/API provider does.
    """
    try:
        for row in platform.providers.health():
            if (
                row.get("available")
                and row.get("provider") != "mock"
                and row.get("class") != "mock"
            ):
                return True
    except Exception:  # noqa: BLE001 — health is best-effort
        pass
    return False


def _has_any(engine, model, *where) -> bool:
    """True if at least one row of ``model`` exists (optionally filtered)."""
    try:
        from sqlmodel import select

        stmt = select(model)
        for clause in where:
            stmt = stmt.where(clause)
        with session_scope(engine) as db:
            return db.exec(stmt.limit(1)).first() is not None
    except Exception:  # noqa: BLE001 — table may not exist on a partial install
        return False


def _document_touched(platform) -> bool:
    """Best-effort: has the user produced or read any document/artifact yet?"""
    # 1) Any stored artifact on disk.
    try:
        if platform.artifacts.list_names():
            return True
    except Exception:  # noqa: BLE001
        pass
    # 2) Anything written into the daemon's documents dir.
    try:
        docdir = platform.config.home / "documents"
        if docdir.is_dir() and any(docdir.iterdir()):
            return True
    except Exception:  # noqa: BLE001
        pass
    # 3) A document tool was actually invoked in some session.
    try:
        from ..core.models import ToolInvocation

        if _has_any(
            platform.engine, ToolInvocation, ToolInvocation.tool.in_(_DOC_TOOLS)
        ):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _taught_style(engine) -> bool:
    """True if any lesson/feedback exists (the learning loop has signal)."""
    try:
        from ..learning.models import FeedbackRecord, LessonRecord
    except Exception:  # noqa: BLE001 — learning slice not importable
        return False
    return _has_any(engine, LessonRecord) or _has_any(engine, FeedbackRecord)


def getting_started(platform) -> list[dict]:
    """The first-steps-to-value, each with a live ``done`` flag.

    Returns a list of ``{key, title, detail, done, action, optional}`` dicts in
    order: four core steps followed by the OPTIONAL ``set_up_voice`` step. The
    optional step never gates ``first_run`` and is never surfaced as ``next_step``
    (see :func:`readiness`), so voice can be skipped without blocking onboarding.
    """
    from ..core.models import Session

    engine = platform.engine

    # 1. Connect an AI ----------------------------------------------------
    connected = _provider_connected(platform)
    step_connect = {
        "key": "connect_ai",
        "title": "Connect your AI (or try the built-in offline model)",
        "detail": (
            "A real model is connected — you're ready for full power."
            if connected
            else "No external model yet. The built-in offline model works right "
            "now; add an Anthropic API key or log into a browser model for "
            "real answers."
        ),
        "done": connected,
        "action": "Open Connections (or set ANTHROPIC_API_KEY)",
        "optional": False,
    }

    # 2. Run your first session -------------------------------------------
    ran_session = _has_any(engine, Session)
    step_session = {
        "key": "first_session",
        "title": "Run your first session",
        "detail": (
            "You've run at least one session — nice."
            if ran_session
            else "Kick off a task and watch Iron Jarvis plan, act, and produce a "
            "result in a disposable workspace."
        ),
        "done": ran_session,
        "action": 'Click "New Session" (or run `ironjarvis run "..."`)',
        "optional": False,
    }

    # 3. Work with a document ---------------------------------------------
    touched_doc = _document_touched(platform)
    step_doc = {
        "key": "work_with_document",
        "title": "Work with a document",
        "detail": (
            "You've read or produced a document/artifact."
            if touched_doc
            else "Ask Iron Jarvis to read or create a file — PDF, Word, Excel, "
            "PowerPoint, CSV, or Markdown all work."
        ),
        "done": touched_doc,
        "action": "Open or create a document (read_document / write_document)",
        "optional": False,
    }

    # 4. Teach it your style ----------------------------------------------
    taught = _taught_style(engine)
    step_learn = {
        "key": "teach_style",
        "title": "Teach it your style",
        "detail": (
            "Iron Jarvis has started learning how you like to work."
            if taught
            else "Leave a thumbs-up/down or save a preference; it becomes a lesson "
            "applied to every future task."
        ),
        "done": taught,
        "action": "Give feedback or save a preference (remember_preference)",
        "optional": False,
    }

    # 5. Set up voice (OPTIONAL) ------------------------------------------
    # Voice is a nice-to-have, never a blocker: this step is marked optional so
    # readiness() never advertises it as next_step and it never keeps first_run
    # true. "done" reflects whether a real speech-to-text backend is present.
    voice_ready, voice_backend = voice_backend_present(platform)
    step_voice = {
        "key": "set_up_voice",
        "title": "Set up voice (optional)",
        "detail": (
            f"Voice dictation is ready via {voice_backend}."
            if voice_ready
            else "Optional: add an OpenAI API key (or a custom endpoint serving "
            "/v1/audio/transcriptions) to talk to Iron Jarvis hands-free. You can "
            "skip this and set it up later."
        ),
        "done": voice_ready,
        "action": "Add an OpenAI key on Connections (or skip — voice is optional)",
        "optional": True,
    }

    return [step_connect, step_session, step_doc, step_learn, step_voice]
