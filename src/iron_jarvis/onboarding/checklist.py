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

    Mirrors ``/voice/status`` (daemon/routes/voice.py) across the four backends
    it can report, in the daemon's own precedence order: a dedicated
    speech-to-text endpoint ``voice_transcribe_base_url`` ("stt") > an OpenAI
    API key ("openai") > a present Vosk model directory, meaning voice already
    works with no key and no internet ("local") > a configured custom
    OpenAI-compatible endpoint ("custom"). Never raises — a missing vault key
    or config attribute just reports "no backend".
    """
    # A DEDICATED transcription endpoint outranks everything in _voice_backend
    # (it is the self-hosted-whisper path the user configured on purpose).
    try:
        stt = (getattr(platform.config, "voice_transcribe_base_url", None) or "").strip()
    except Exception:  # noqa: BLE001
        stt = ""
    if stt:
        return True, "stt"
    try:
        if platform.secrets.get("openai_api_key"):
            return True, "openai"
    except Exception:  # noqa: BLE001 — vault miss = not available
        pass
    # The desktop app BUNDLES an offline Vosk model, so voice can already work
    # out of the box. The checklist must agree with /voice/status here, or a
    # packaged install nudges the user toward an OpenAI key for a feature that
    # already works. Imported at call time so tests can patch the source module.
    from ..voice import vosk_model_path

    if vosk_model_path(platform.config):
        return True, "local"
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
    from ..core.models import ChatThreadRecord, Session

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
            "now; for real answers, paste an API key, point at a local Ollama, "
            "or just be signed into the Claude or Codex app on this PC."
        ),
        "done": connected,
        "action": "Open the Connections page",
        "optional": False,
    }

    # 2. Give it your first task -------------------------------------------
    # Chat IS the product's hero surface (one chat surface, no mode picker), so
    # a chat thread counts as first value just like an agent session — keying
    # this off Session rows alone kept chat-only users "not started" forever
    # and nudged them into the Sessions lane. The key stays "first_session":
    # the dashboard maps checklist links by key.
    gave_task = _has_any(engine, Session) or _has_any(engine, ChatThreadRecord)
    step_session = {
        "key": "first_session",
        "title": "Give it your first task",
        "detail": (
            "You've given Iron Jarvis its first task — nice."
            if gave_task
            else "Ask anything in Chat — that counts. Bigger jobs escalate to a "
            "full agent all by themselves."
        ),
        "done": gave_task,
        "action": "Open Chat and ask anything",
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
        "action": "Ask in Chat to read or create a file",
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
            else "Tell it a preference in Chat (or rate a finished session); it "
            "becomes a lesson applied to every future task."
        ),
        "done": taught,
        # Chat has no thumbs affordance (feedback UI lives on session detail),
        # so the followable path from the hero surface is the
        # remember_preference tool — a typed preference becomes a lesson.
        "action": 'In Chat, ask it to remember a preference — e.g. "remember: '
        'I like short answers"',
        "optional": False,
    }

    # 5. Set up voice (OPTIONAL) ------------------------------------------
    # Voice is a nice-to-have, never a blocker: this step is marked optional so
    # readiness() never advertises it as next_step and it never keeps first_run
    # true. "done" reflects whether a real speech-to-text backend is present.
    voice_ready, voice_backend = voice_backend_present(platform)
    if voice_backend == "local":
        voice_detail = (
            "Voice works offline, out of the box — no key, no internet needed. "
            "Just press the mic and talk."
        )
    elif voice_backend == "stt":
        voice_detail = "Voice dictation is ready via your speech-to-text server."
    elif voice_ready:
        voice_detail = f"Voice dictation is ready via {voice_backend}."
    else:
        voice_detail = (
            "Optional: add an OpenAI API key or connect a speech-to-text server "
            "to talk to Iron Jarvis hands-free. You can skip this and set it up "
            "later."
        )
    step_voice = {
        "key": "set_up_voice",
        "title": "Set up voice (optional)",
        "detail": voice_detail,
        "done": voice_ready,
        "action": "Add an OpenAI key on the Connections page (or skip — voice is optional)",
        "optional": True,
    }

    return [step_connect, step_session, step_doc, step_learn, step_voice]
