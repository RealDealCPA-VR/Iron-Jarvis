"""A voice note from the phone, turned into words (10-a, decision D-10.1).

The ORDER is the decision:

1. The offline speech model on this PC (Vosk), through a short-lived child
   (:mod:`iron_jarvis.audio_worker`: ``ironjarvis.exe audio-worker`` when
   frozen, ``python -m iron_jarvis.audio_worker`` from source), started
   through ``core/procs`` like the OCR worker. PyAV decodes the phone's OGG,
   M4A, MP3 or WebM without a system ffmpeg; a malformed file can only crash
   the child. At most :data:`MAX_AUDIO_S` of audio and :data:`WORK_TIMEOUT_S`
   of work; past that the child's tree is killed.
2. Only when the offline model is missing or heard nothing: the speech
   service the user chose in Settings for dictation (``voice_backend``,
   ``voice_transcribe_base_url``, the custom endpoint), through the SAME
   ``routes/voice.transcribe_clip`` dictation uses. That choice was made for
   dictation, not for phone audio, so the phone asks ONCE first ("Use OpenAI
   for phone voice notes too?"); the answer is the setting
   ``phone_voice_service`` (ask, yes, no) with Undo. A yes said on the phone
   is kept WITH the service it was asked for (``phone_voice_service_for``,
   :func:`service_key`): when Settings later names another service, the
   phone asks again before a voice note goes to it. The reply names the
   service.
3. A Local only chat never sends a voice note off the network: the real
   ``transcribe_clip`` refuses it (``SpeechKeptHere``), and the words say so.

Nothing set up, a refusal, a stall: the user hears it in words, and the
recording stays kept in the chat's folder.

``deps`` is the daemon's deps object (``_vosk_model_path``,
``_voice_backend``, ``_VOICE_MAX_BYTES``, ``platform``), lent to the phone
poller as ``InboundPoller.voice_deps``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core.logging import get_logger

log = get_logger("comm.voice_note")

#: The frozen exe's hidden subcommand (``packaging/ironjarvis_entry._fast_path``).
WORKER_SUBCOMMAND = "audio-worker"
#: The most audio read from one voice note, in seconds.
MAX_AUDIO_S = 600
#: The most time the child may work on one voice note, in seconds.
WORK_TIMEOUT_S = 120.0
#: How long the child may take to start (imports) on top of the work.
WORKER_START_S = 15.0

#: The kinds of file heard as a voice note: a voice note always; an audio
#: file only when the message has no words of its own (a voice memo sent as
#: a file).
VOICE_KINDS = ("voice",)

ASK_KIND = "voice_service"
YES_WORDS = frozenset({"1", "yes", "y", "ok", "use it", "sure"})
NO_WORDS = frozenset({"2", "no", "n", "keep it on this pc", "only on this pc"})
#: The most recordings one question waits on (a voice note sent before the
#: answer joins the question; the oldest past this are left kept, unheard).
MAX_WAITING = 5


def waiting_paths(ref: str) -> list[str]:
    """The recordings a question waits on, from its ``ref_id`` (their paths,
    oldest first, one a line)."""
    return [p for p in str(ref or "").split("\n") if p.strip()]


@dataclass
class Heard:
    """What came of one voice note."""

    #: The words heard ("" when none).
    text: str = ""
    #: Who heard them: "" for this PC, else the service's name ("OpenAI").
    who: str = ""
    #: When the user must say yes first: the service's name to ask about.
    ask: str = ""
    #: When nothing was heard and nothing is asked: why, in words.
    problem: str = ""
    #: The kind of service asked about (``_voice_backend``'s label: "openai",
    #: "stt" or "custom"), for the question's words.
    ask_label: str = ""


@dataclass(frozen=True)
class Service:
    """The speech service chosen in Settings right now."""

    label: str
    url: str
    #: As the user knows it ("OpenAI", "the speech server at <host>").
    who: str
    #: Which service a yes is kept for (:func:`service_key`).
    key: str


def _worker_argv() -> list[str]:
    """How to start the worker: the frozen exe's hidden subcommand, else this
    interpreter."""
    if getattr(sys, "frozen", False):
        return [sys.executable, WORKER_SUBCOMMAND]
    return [sys.executable, "-m", "iron_jarvis.audio_worker"]


def _ask_worker(request: dict, timeout: float) -> "dict | None":
    """One request to a fresh worker; ``None`` on any failure or when the
    child runs past ``timeout`` (its whole tree is killed then). BLOCKING:
    never on the event loop. Never raises."""
    from ..core import procs

    try:
        result = procs.run(_worker_argv(), timeout=timeout,
                           input=json.dumps(request).encode("ascii"))
    except Exception:  # noqa: BLE001 — a missing program, a refused spawn
        log.warning("voice note: the audio worker could not start")
        return None
    if result.timed_out:
        log.warning("voice note: the audio worker ran past %.0f s and was stopped", timeout)
        return None
    if result.returncode != 0:
        log.warning("voice note: the audio worker ended with code %s", result.returncode)
        return None
    try:
        out = json.loads(result.stdout.decode("ascii", errors="replace") or "null")
    except Exception:  # noqa: BLE001
        return None
    return out if isinstance(out, dict) else None


def hear_offline(path: str, model: str) -> str:
    """The words the offline model hears in the recording at ``path`` ("" when
    none, or when the worker failed or stalled). BLOCKING."""
    out = _ask_worker(
        {"op": "transcribe", "path": str(path), "model": str(model), "max_audio_s": MAX_AUDIO_S},
        timeout=WORK_TIMEOUT_S + WORKER_START_S,
    )
    if not out or out.get("error"):
        if out and out.get("error"):
            log.info("voice note: the offline model could not read it: %s", str(out["error"])[:200])
        return ""
    return " ".join(str(out.get("text") or "").split())


def service_name(label: str, url: str) -> str:
    """The speech service as the user knows it: "OpenAI", or "the speech
    server at <host>" (with its port when the address names one, so two
    servers on one PC are two names, and the question names which)."""
    if label == "openai":
        return "OpenAI"
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if host and parts.port:
            host = f"{host}:{parts.port}"
    except Exception:  # noqa: BLE001
        host = ""
    return f"the speech server at {host}" if host else "your speech server"


def service_key(label: str, url: str) -> str:
    """Which speech service a yes on the phone was for: "openai", else its
    kind and server ("stt:speech.example.com", "custom:127.0.0.1:8080")."""
    if label == "openai":
        return "openai"
    try:
        parts = urlsplit(url)
        host, port = (parts.hostname or "").lower(), parts.port
    except Exception:  # noqa: BLE001
        host, port = "", None
    return f"{label}:{host}" + (f":{port}" if port else "")


async def current_service(deps: Any) -> "Service | None":
    """The speech service chosen in Settings now (``_voice_backend``), or
    ``None`` when there is none. Never raises."""
    try:
        backend = await asyncio.to_thread(deps._voice_backend) if deps is not None else None
    except Exception:  # noqa: BLE001
        backend = None
    if not backend:
        return None
    label, url = str(backend[0]), str(backend[1])
    return Service(label=label, url=url, who=service_name(label, url), key=service_key(label, url))


def _consent(config: Any, key: str = "") -> str:
    """``phone_voice_service`` for the service ``key``: "ask", "yes" or "no".

    D-10.1: a yes said on the phone was asked for ONE named service and is
    kept with it (``phone_voice_service_for``). When the service chosen in
    Settings is a different one now, that yes is not consent for it: the
    phone asks again. A yes chosen in Settings with no service kept follows
    the service chosen there, as its row says."""
    raw = str(getattr(config, "phone_voice_service", "ask") or "ask").strip().lower()
    if raw not in ("ask", "yes", "no"):
        return "ask"
    if raw == "yes":
        given = str(getattr(config, "phone_voice_service_for", "") or "").strip()
        if given and key and given != key:
            return "ask"
    return raw


async def transcribe_note(
    deps: Any, path: str, *, local_only: bool, kept_in: str, mime: str = "audio/ogg"
) -> Heard:
    """Turn the recording at ``path`` into words, in the order above. Never
    raises; ``kept_in`` is the folder as the phone hears it."""
    keep = f"The recording is kept in {kept_in}."
    if deps is None:
        return Heard(problem=(
            "I could not turn your voice note into words: voice notes can't be heard "
            f"on this PC right now. {keep}"))
    try:
        model = await asyncio.to_thread(deps._vosk_model_path)
    except Exception:  # noqa: BLE001
        model = None
    offline_tried = False
    if model:
        offline_tried = True
        text = await asyncio.to_thread(hear_offline, path, str(model))
        if text:
            return Heard(text=text)
    svc = await current_service(deps)
    if svc is None:
        if offline_tried:
            return Heard(problem=(
                "I could not turn your voice note into words on this PC, and no speech "
                f"service is chosen in Settings. {keep}"))
        return Heard(problem=(
            "I could not turn your voice note into words: there is no offline speech model "
            f"on this PC and no speech service is chosen in Settings. {keep}"))
    from ..daemon.routes import voice as voice_routes

    if local_only and _off_network(voice_routes, svc.label, svc.url):
        # LOCAL ONLY: the real transcribe_clip refuses a service off this
        # network before anything is read or sent, whatever was answered
        # before; nobody is asked about a service that could not be used.
        return await _by_service(deps, voice_routes, path, who=svc.who, local_only=True,
                                 keep=keep, mime=mime)
    consent = _consent(getattr(getattr(deps, "platform", None), "config", None), svc.key)
    if consent == "no":
        return Heard(problem=(
            "I could not turn your voice note into words on this PC, and phone voice notes "
            f"stay on this PC (Settings, Voice notes from your phone). {keep}"))
    if consent == "ask":
        return Heard(ask=svc.who, ask_label=svc.label)
    return await _by_service(deps, voice_routes, path, who=svc.who, local_only=local_only,
                             keep=keep, mime=mime)


def _off_network(voice_routes: Any, label: str, url: str) -> bool:
    try:
        voice_routes._refuse_off_network(label, url)
        return False
    except voice_routes.SpeechKeptHere:
        return True
    except Exception:  # noqa: BLE001 — unreadable: treat as off the network
        return True


async def _by_service(deps: Any, voice_routes: Any, path: str, *, who: str, local_only: bool,
                      keep: str, mime: str) -> Heard:
    """The chosen speech service, through the real ``transcribe_clip`` (which
    refuses one off the network under Local only)."""
    from fastapi import HTTPException

    try:
        data = await asyncio.to_thread(Path(path).read_bytes)
    except OSError:
        return Heard(problem=f"I could not read the recording back. {keep}")
    try:
        got = await voice_routes.transcribe_clip(deps, data=data, mime=mime, local_only=local_only)
    except voice_routes.SpeechKeptHere as exc:
        return Heard(problem=f"{exc} {keep}")
    except HTTPException as exc:
        return Heard(problem=f"I could not turn your voice note into words: {exc.detail} {keep}")
    except Exception as exc:  # noqa: BLE001 — never a crash over a voice note
        log.warning("voice note: the speech service failed (%s)", type(exc).__name__)
        return Heard(problem=f"I could not turn your voice note into words: {who} did not answer. {keep}")
    text = " ".join(str((got or {}).get("text") or "").split())
    if not text:
        return Heard(problem=f"{who[:1].upper() + who[1:]} heard no words in your voice note. {keep}")
    return Heard(text=text, who=who)


async def transcribe_with_service(deps: Any, path: str, *, local_only: bool, kept_in: str,
                                  mime: str = "audio/ogg", service: "Service | None" = None) -> Heard:
    """After the user said yes on the phone: ``service`` (the one the yes was
    for; default the one chosen now), without asking. Local only still
    refuses one off the network. Never raises."""
    keep = f"The recording is kept in {kept_in}."
    svc = service or await current_service(deps)
    if svc is None:
        return Heard(problem=f"No speech service is chosen in Settings any more. {keep}")
    from ..daemon.routes import voice as voice_routes

    return await _by_service(deps, voice_routes, path, who=svc.who, local_only=local_only,
                             keep=keep, mime=mime)


def mime_of(name: str) -> str:
    """The audio type a speech service is told, from the kept file's name."""
    from pathlib import PurePath

    return {
        ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".m4a": "audio/mp4",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".weba": "audio/webm", ".webm": "audio/webm",
    }.get(PurePath(str(name or "")).suffix.lower(), "audio/ogg")


def ask_question(who: str) -> str:
    """The question as the pending prompt keeps it (reminders quote it)."""
    return f"Use {who} for phone voice notes too?"


def ask_words(who: str, label: str = "", *, waiting: int = 1) -> str:
    """The one question asked before the first phone voice note goes to the
    chosen speech service (decision D-10.1). ``label`` "custom" is the model
    server set up for chat, which dictation falls back to: it is named as
    that, not as a speech service chosen for dictation. ``waiting`` > 1: an
    earlier voice note waits on the same answer, and is said to."""
    what = (
        "It is the model server you set up for chat in Settings (dictation uses it too)"
        if label == "custom" else
        "It is the speech service you chose for dictation"
    )
    also = (f" Your {waiting} voice notes wait on this one answer." if waiting > 1 else "")
    return (
        "I can't turn voice notes into words on this PC: the offline speech model "
        f"isn't here or heard nothing. {ask_question(who)} {what}, and each voice note "
        f"would be sent to it.{also} Reply 1 for yes or 2 for no."
    )


def changed_words(who: str, label: str = "", *, waiting: int = 1) -> str:
    """The speech service chosen in Settings changed between the question
    and the yes: the yes was for the other one, so the phone asks again."""
    return ("The speech service in Settings changed since I asked. "
            f"{ask_words(who, label, waiting=waiting)}")


def heard_line(heard: Heard) -> str:
    """"I heard: ..." (this PC) or "I heard (by OpenAI, which you chose for
    voice): ..."."""
    if heard.who:
        return f"I heard (by {heard.who}, which you chose for voice): {heard.text}"
    return f"I heard: {heard.text}"
