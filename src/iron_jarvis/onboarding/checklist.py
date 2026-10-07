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


def default_is_mock(platform) -> bool:
    """True while the DEFAULT model is still the untouched offline mock (or
    blank) -- every answer is then the scripted demo, whatever is connected.

    v1.310.0: the trap this names is a signed-in Claude Code / Codex user (or
    a fresh Ollama) whose default never left ``mock``: "connected" was true,
    so the checklist ticked step 1 while every reply was the offline script.
    """
    try:
        current = str(getattr(platform.config, "default_provider", "") or "").strip()
    except Exception:  # noqa: BLE001 — unreadable config = the safe answer
        return True
    return current in ("", "mock")


#: v1.310.0: plain names for the "use this for answers" choices -- the words
#: a non-technical user knows their account by. API providers fall back to
#: their Connections display name; anything else to its id.
_PROVIDER_LABELS: dict[str, str] = {
    "claude-cli": "Claude (your Claude Code sign-in)",
    "codex-cli": "ChatGPT (your Codex sign-in)",
    "ollama": "Ollama (free, runs on this PC)",
    "custom": "Your own model server",
    "mock": "the offline demo",
    "auto": "Auto (picks a model for each task)",
}


def provider_label(platform, name: str) -> str:
    """The plain name a person reads for *name* (never raises)."""
    if name in _PROVIDER_LABELS:
        return _PROVIDER_LABELS[name]
    # An API name served THROUGH a signed-in CLI (no stored key) is the
    # subscription the user pressed, not a pay-per-use key: name the sign-in
    # (v1.310.0 review — the Claude door promotes to the inherited name).
    try:
        via = platform.providers.inherited_from(name)
    except Exception:  # noqa: BLE001 — a label is cosmetic
        via = None
    if via in _PROVIDER_LABELS:
        return _PROVIDER_LABELS[via]
    try:
        spec = platform.connections.get_spec(name)
        if spec is not None and getattr(spec, "display_name", ""):
            return str(spec.display_name)
    except Exception:  # noqa: BLE001 — a label is cosmetic
        pass
    return name or "a model"


def _one_press_choices(platform) -> bool:
    """True when the "use this for answers" press has something to choose
    (``readiness.model_choice()['usable']`` is non-empty) -- the ONE list the
    Overview card and the press route agree on, so step 1 never promises a
    press that would answer 409. Lazy import: readiness imports this module."""
    try:
        from .readiness import model_choice

        return bool(model_choice(platform)["usable"])
    except Exception:  # noqa: BLE001 — no promise when unsure
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
    """True once the USER has taught it something: a CONFIRMED lesson that is
    not an automatic reflection, or any rating on a finished session.

    v1.310.0: this used to be "any LessonRecord" -- but every finished
    session (the wizard's demo run included) writes a ``source="reflection"``
    "Worked well for ..." lesson, so the step struck itself through while the
    user had taught nothing. A noticed preference still WAITING for the user
    (``proposed``) or one they ``declined`` is not teaching either, so the
    same :func:`learning.engine.confirmed_clause` that gates prompt injection
    gates this tick.
    """
    try:
        from ..learning.engine import confirmed_clause
        from ..learning.models import FeedbackRecord, LessonRecord
    except Exception:  # noqa: BLE001 — learning slice not importable
        return False
    return _has_any(
        engine, LessonRecord, LessonRecord.source != "reflection", confirmed_clause()
    ) or _has_any(engine, FeedbackRecord)


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
    # v1.310.0: done means a REAL model ANSWERS -- connected AND chosen. A
    # signed-in Claude Code user whose default never left mock used to see
    # this ticked ("ready for full power") while every reply was the offline
    # script. The press that fixes it is explicit (POST /onboarding/use-model):
    # cloud-vs-local is the user's call, so nothing here switches silently.
    # And the mock is never called "working": until a model is chosen,
    # replies are a scripted demo, and the step says so.
    connected = _provider_connected(platform)
    on_mock = default_is_mock(platform)
    cfg = platform.config
    chosen = str(getattr(cfg, "default_provider", "") or "")
    # v1.310.0 (review): "chosen" is not "answering" either -- a user who
    # removed their key, or whose Ollama box is down, still has a non-mock
    # default the router REFUSES with a named error (v1.162.0). Cached
    # availability only; no probe.
    # "auto" is not a provider the manager knows by name (get() and the
    # router's _resolve_auto special-case it), so available("auto") is always
    # False -- asking it stranded every Auto user on a false "isn't reachable".
    # Auto answers through whatever real model is connected, so for Auto,
    # "up" IS "something real is connected".
    is_auto = chosen == "auto"
    try:
        chosen_up = (not on_mock) and (
            connected if is_auto else bool(platform.providers.available(chosen))
        )
    except Exception:  # noqa: BLE001 — an unreadable verdict is "not up"
        chosen_up = False
    answering = connected and chosen_up
    model_name = str(getattr(cfg, "default_model", "") or "")
    if answering:
        # Auto picks the model per task, so a single model name would mislead.
        model_note = f" ({model_name})" if model_name and not is_auto else ""
        connect_detail = (
            f"{provider_label(platform, chosen)} answers you now{model_note}. "
            "You can switch any time on Connections."
        )
        connect_action = "Open the Connections page"
    elif is_auto:
        # Auto with nothing real connected falls back to the offline mock
        # (the router's last resort), so here it IS the scripted demo -- and
        # the fix is connecting something, not "checking" Auto.
        connect_detail = (
            f"You chose {provider_label(platform, chosen)}, but no model is "
            "connected yet, so replies are a scripted demo. Sign in with Claude "
            "Code or Codex on this PC, start a free local Ollama, or paste an "
            "API key on the Connections page."
        )
        connect_action = "Open the Connections page"
    elif not on_mock:
        # Chosen, but not reachable: say WHICH one, never "scripted demo" --
        # the mock is not answering for it, the router is refusing.
        connect_detail = (
            f"You chose {provider_label(platform, chosen)} for answers, but it "
            "isn't reachable right now, so Chat will tell you so instead of "
            "answering. Check it on the Connections page."
        )
        connect_action = "Open the Connections page"
    elif connected and _one_press_choices(platform):
        connect_detail = (
            "A model is connected, but replies are still a scripted demo until "
            "you choose it for answers. One press does it."
        )
        connect_action = "Choose it for answers"
    elif connected:
        # v1.310.0 (review): something real is connected (a Grok or OpenCode
        # sign-in, a fleet node) that the one-press door can't choose for
        # you -- so no "one press" promise; point at where it CAN be chosen.
        connect_detail = (
            "A model is connected, but replies are still a scripted demo until "
            "it's chosen for answers. Pick which model answers you on the "
            "Connections page."
        )
        connect_action = "Open the Connections page"
    else:
        connect_detail = (
            "Until you connect a model, replies are a scripted demo, not real "
            "answers. Sign in with Claude Code or Codex on this PC, start "
            "a free local Ollama, or paste an API key."
        )
        connect_action = "Open the Connections page"
    step_connect = {
        "key": "connect_ai",
        "title": "Connect your AI",
        "detail": connect_detail,
        "done": answering,
        "action": connect_action,
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
            else "Tell it how you like things in Chat (or rate a finished "
            "session); it becomes a lesson applied to every future task."
        ),
        "done": taught,
        # Chat has no thumbs affordance (feedback UI lives on session detail),
        # so the followable path from the hero surface is the
        # remember_preference tool — a typed preference becomes a lesson.
        # v1.310.0: the example MUST arm that tool. The old "remember: I like
        # short answers" armed recall/ltm_search/ltm_append (a fact to file
        # away), so the user followed the step and no preference was kept.
        # "From now on, ..." is the vocabulary autoselect routes to
        # remember_preference (pinned by test_wave2_onboarding_v1310).
        "action": 'In Chat, tell it a preference — e.g. "From now on, keep '
        'answers short"',
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
