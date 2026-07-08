"""Creative routes: the gallery, media file serving, Pixio publish/upload.

The gallery lists media ARTIFACTS (pixio generations save into the store via
the artifact sink; screenshots and uploads live there too). File serving
carries proper content-types so the dashboard can render <img>/<video>/<audio>
directly (the token middleware already accepts ?token= for exactly this).
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from ..schemas import (
    CreativeIngestBody,
    CreativePublishBody,
    CreativeUploadBody,
    StudioSayBody,
    StudioStartBody,
)
from ...core.fs_policy import fs_read_ok
from ...creative.service import list_media, media_kind, mime_for

#: Per-CLI "run without permission prompts" LAUNCH FLAGS for studio autopilot.
#: Claude is deliberately NOT here — its auto mode is engaged the way a human
#: does it: Shift+Tab cycling after boot (see _engage_claude_automode), which
#: uses the milder auto-accept mode instead of --dangerously-skip-permissions.
_AUTOPILOT_FLAGS = {
    "codex": "--full-auto",
}

#: Shift+Tab as a terminal keystroke (CSI Z) — cycles Claude Code's permission
#: mode. The current cycle is: manual → accept-edits → plan → auto → manual.
_SHIFT_TAB = "\x1b[Z"

#: Permission-mode banners Claude Code paints (ANSI already stripped by
#: output_tail). CURRENT strings first, older aliases kept so a machine on an
#: earlier Claude still works. Longer alternatives precede their prefixes so the
#: regex prefers "auto-accept edits on" over the "accept edits on" inside it.
_MODE_RE = re.compile(
    r"auto-accept edits on"  # older Claude alias for accept-edits
    r"|accept edits on"  # current: auto-accepts file edits (the mild autopilot)
    r"|bypass permissions on"  # older Claude alias for full-auto
    r"|auto mode on"  # current: full auto, no prompts
    r"|plan mode on"  # read-only planning — NOT an autopilot mode
    r"|manual mode on"  # current default: asks every time
)

#: TRULY hands-off modes — the CLI runs file edits AND shell commands without
#: ever stopping to ask. Media skills run commands (python, ffmpeg, curl), so
#: the studio must reach one of these or a generation stalls at the first
#: command prompt. "accept edits on" is deliberately EXCLUDED: it auto-accepts
#: edits but still prompts on every command (live-hit 2026-07-07 — a PowerShell
#: write blocked on "Do you want to proceed?"). "plan"/"manual" also excluded.
_AUTO_MODES = frozenset({"auto mode on", "bypass permissions on"})

#: The mild mode Shift+Tab hits FIRST in the cycle (manual → accept-edits →
#: plan → auto). We cycle PAST it to full auto, but recognise it so the loop
#: knows it hasn't arrived yet.
_MILD_EDIT_MODES = frozenset({"accept edits on", "auto-accept edits on"})


def latest_claude_mode(tail: str) -> str | None:
    """The MOST RECENT permission-mode banner in the clean tail (the TUI
    repaints the banner each cycle, so the last match wins), or None when no
    banner has been seen (default mode)."""
    last: str | None = None
    for m in _MODE_RE.finditer(tail[-4000:]):
        last = m.group(0)
    return last


#: Painted by Claude Code (and Codex) in the live status bar while a turn is
#: actually RUNNING — the honest "working" signal, vs. guessing from idle time.
_WORKING_MARKER = "esc to interrupt"

#: A shell prompt as the LAST thing in the tail = the CLI has exited back to
#: the shell. PowerShell ("PS C:\...>") and cmd ("C:\...>") shapes.
_PROMPT_AT_END_RE = re.compile(r"(?:^|\n)(?:PS [^\n]*|[A-Za-z]:\\[^\n]*)>\s*$")


def derive_phase(
    full: str, *, ready: bool, output_age: float | None = None
) -> tuple[str, str | None]:
    """The engine's live lifecycle phase from its (ANSI-stripped) output tail.

    Returns ``(phase, status_line)`` where phase is one of:
    ``booting`` (CLI hasn't painted yet), ``thinking`` (a turn is running —
    the TUI shows its esc-to-interrupt status bar), ``exited`` (the CLI quit;
    the LAST thing in the tail is a bare shell prompt — typing another brief
    would run it as a SHELL COMMAND, so callers must refuse), or ``idle``
    (CLI up, waiting for input). ``status_line`` is the CLI's own live
    progress line while thinking (e.g. "✻ Cerebrating… (14s · esc to
    interrupt)"), else None.

    ``output_age`` (seconds since the terminal last printed anything) guards
    "thinking": a running TUI repaints its status bar about once a second, so
    a marker with STALE output is a leftover in the append-only tail, not a
    live turn. ``None`` = unknown → trust the marker.
    """
    if not ready:
        return "booting", None
    window = full[-600:]
    low = window.lower()
    if _WORKING_MARKER in low and (output_age is None or output_age < 6.0):
        # Surface the CLI's own status line — the last line carrying the marker.
        status: str | None = None
        for line in re.split(r"[\r\n]+", window):
            if _WORKING_MARKER in line.lower():
                cleaned = " ".join(line.split()).strip()
                if cleaned:
                    status = cleaned[:140]
        return "thinking", status
    if _PROMPT_AT_END_RE.search(full[-300:].rstrip()):
        return "exited", None
    return "idle", None


#: Never send an automode Shift+Tab within this many seconds of a studio_say —
#: a keystroke landing between a typed brief and its Enter would corrupt it.
_SAY_QUIET_SECONDS = 2.5


def _engage_claude_automode(session) -> None:
    """Background: wait for Claude Code to boot, then press Shift+Tab until the
    tail shows an autopilot mode engaged. Closed loop — each press is verified
    against the repainted banner, so it can't overshoot into plan mode. Keeps
    trying even after the user's first brief (a manual-mode run stalls at its
    first permission prompt), but NEVER keystrokes within _SAY_QUIET_SECONDS of
    a say — that's the window where a Shift+Tab could land inside a brief."""
    import time

    def quiet() -> bool:
        return time.time() - getattr(session, "_last_say_ts", 0.0) >= _SAY_QUIET_SECONDS

    deadline = time.time() + 45
    while time.time() < deadline:  # wait for the TUI to come up
        if not session.alive:
            return
        tail = session.output_tail()
        if "? for shortcuts" in tail or "shift+tab" in tail.lower():
            break
        time.sleep(1.0)
    # Bounded press-and-verify: 8 covers a full cycle-and-a-bit of the 4 modes.
    presses = 0
    while presses < 8:
        if not session.alive or time.time() > deadline + 30:
            return
        if latest_claude_mode(session.output_tail()) in _AUTO_MODES:
            return
        if not quiet():  # a brief is being typed — wait, don't keystroke over it
            time.sleep(0.5)
            continue
        session.write(_SHIFT_TAB)
        presses += 1
        time.sleep(1.5)


#: A full-screen CLI TUI (Claude Code) ingests a bulk write as a bracketed
#: PASTE, so a trailing "\r" lands as a newline INSIDE the composer instead of
#: submitting it. We therefore send the text, let the paste settle, then press
#: Enter as a SEPARATE keystroke. Without this the brief sits typed-but-unsent
#: and nothing ever generates — the exact "chat initialises then stops" failure.
_SUBMIT_SETTLE_SECONDS = 0.3


def _type_and_submit(session, text: str) -> None:
    """Type ``text`` into the CLI, then submit it with a distinct Enter press."""
    import time

    session.write(text)
    time.sleep(_SUBMIT_SETTLE_SECONDS)
    session.write("\r")


#: Media-generation skills the studio brief points the CLI at when the user
#: picks "Auto" — the CLI (e.g. Claude Code) discovers these from
#: ~/.claude/skills on this machine and routes by description.
_AUTO_SKILL_HINT = (
    "Pick the best media-generation skill you have for what I describe "
    "(pixio-story for narrative video, seedance-storyboard for cinematic "
    "clips, pixio-song for music, pixio-skill for single images/audio/video)."
)

_MISSING_KEY = (
    "Pixio isn't connected — add your key on the Connections page (or a secret "
    "named 'pixio') to publish media."
)


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""

    def _pixio_key() -> str | None:
        try:
            key = d.platform.secrets.get("pixio")
        except Exception:  # noqa: BLE001 — vault miss = not configured
            key = None
        return key or os.environ.get("PIXIO_API_KEY") or None

    @app.get("/creative/items")
    def creative_items(limit: int = 200) -> dict[str, Any]:
        """The gallery: every media artifact, newest first."""
        items = list_media(d.platform, limit=limit)
        return {"items": items, "count": len(items)}

    # `{name:path}` (not `{name}`): artifact names may legitimately contain
    # slashes (computer-use screenshots do) — a plain segment param 404s them.
    # Traversal-safe regardless: the store slugifies names into ONE segment.
    @app.delete("/creative/items/{name:path}")
    def creative_delete(name: str) -> dict[str, Any]:
        """Remove a gallery artifact for good — every stored version plus its
        records. GALLERY ARTIFACTS ONLY: there is deliberately no delete-by-
        filesystem-path anywhere in the daemon."""
        if not d.platform.artifacts.delete(name):
            raise HTTPException(status_code=404, detail="no such media")
        return {"deleted": name}

    @app.get("/creative/file/{name:path}")
    def creative_file(name: str, version: int | None = None):
        """Serve one gallery item's bytes with its real content-type.
        <img src>/<video src> can't send an Authorization header — the token
        middleware accepts ?token= (same pattern as the /events WebSocket)."""
        path = d.platform.artifacts.version_path(name, version)
        if path is None or not path.is_file():
            raise HTTPException(status_code=404, detail="no such media")
        if media_kind(path.name) is None:
            raise HTTPException(status_code=415, detail="not a media artifact")
        return FileResponse(path, media_type=mime_for(path.name))

    @app.get("/creative/thumb")
    def creative_thumb(path: str = "", name: str = "", version: int | None = None):
        """A small cached JPEG preview for any media item — gallery artifact
        (``name``) or local file (``path``). 404 = no thumbnail possible
        (audio/SVG/no-ffmpeg): the UI falls back to the original or a glyph."""
        from ...creative.service import thumbnail_for

        if bool(path.strip()) == bool(name.strip()):
            raise HTTPException(status_code=400, detail="give exactly one of path or name")
        if name.strip():
            p = d.platform.artifacts.version_path(name.strip(), version)
            if p is None or not p.is_file():
                raise HTTPException(status_code=404, detail="no such media")
        else:
            p = Path(path.strip())
            if not p.is_absolute():
                raise HTTPException(status_code=400, detail="absolute path required")
            if media_kind(p.name) is None:
                raise HTTPException(status_code=415, detail="not a media file")
            ok, reason = fs_read_ok(str(p))
            if not ok:
                raise HTTPException(status_code=403, detail=f"blocked: {reason}")
            if not p.is_file():
                raise HTTPException(status_code=404, detail="no such file")
        thumb = thumbnail_for(d.platform, p)
        if thumb is None:
            raise HTTPException(status_code=404, detail="no thumbnail for this file")
        return FileResponse(thumb, media_type="image/jpeg")

    @app.get("/creative/file-by-path")
    def creative_file_by_path(path: str):
        """Serve a LOCAL media file (chat replies embed generated media by its
        absolute workspace path). Media extensions only + the fs policy guard —
        never a vault key or arbitrary file."""
        p = Path((path or "").strip())
        if not p.is_absolute():
            raise HTTPException(status_code=400, detail="absolute path required")
        if media_kind(p.name) is None:
            raise HTTPException(status_code=415, detail="not a media file")
        ok, reason = fs_read_ok(str(p))
        if not ok:
            raise HTTPException(status_code=403, detail=f"blocked: {reason}")
        if not p.is_file():
            raise HTTPException(status_code=404, detail="no such file")
        return FileResponse(p, media_type=mime_for(p.name))

    @app.post("/creative/publish")
    async def creative_publish(body: CreativePublishBody) -> dict[str, Any]:
        """Publish media to Pixio's CDN → a clean, PERMANENT, PUBLIC url usable
        directly in generation params. Source: gallery name, local path, or a
        remote url to mirror. Honest 424 when Pixio isn't connected."""
        import asyncio

        from ...tools.pixio import PixioUploadTool, pixio_publish

        key = _pixio_key()
        if not key:
            raise HTTPException(status_code=424, detail=_MISSING_KEY)
        sources = [bool(body.name.strip()), bool(body.path.strip()), bool(body.url.strip())]
        if sum(sources) != 1:
            raise HTTPException(
                status_code=400, detail="give exactly one of name, path, or url"
            )
        endpoint = "images" if body.endpoint == "images" else "media"
        try:
            if body.url.strip():
                url = await asyncio.to_thread(
                    pixio_publish, key, url=body.url.strip(), endpoint=endpoint
                )
                return {"url": url}
            if body.name.strip():
                p = d.platform.artifacts.version_path(body.name.strip(), body.version)
                if p is None or not p.is_file():
                    raise HTTPException(status_code=404, detail="no such media")
            else:
                p = Path(body.path.strip())
                if not p.is_absolute() or not p.is_file():
                    raise HTTPException(status_code=404, detail="no such file")
                ok, reason = fs_read_ok(str(p))
                if not ok:
                    raise HTTPException(status_code=403, detail=f"blocked: {reason}")
            if media_kind(p.name) is None:
                raise HTTPException(
                    status_code=415,
                    detail="only image/video/audio may be published to the public CDN",
                )
            # Same cap as the pixio_upload TOOL — read_bytes() below buffers the
            # whole file in RAM, so refuse before reading. Class attr resolved at
            # call time so tests can shrink it.
            if p.stat().st_size > PixioUploadTool._MAX_UPLOAD:
                raise HTTPException(
                    status_code=413, detail="file too large to publish (200MB max)"
                )
            url = await asyncio.to_thread(
                pixio_publish,
                key,
                blob=p.read_bytes(),
                filename=p.name,
                mime=mime_for(p.name),
                endpoint=endpoint,
            )
            return {"url": url}
        except RuntimeError as exc:  # honest Pixio-side failure
            raise HTTPException(status_code=424, detail=str(exc))

    # --- Creative Studio: drive an AI CLI from the Creative page -----------
    # The user's real creative workflow is a CLI (Claude Code + the pixio
    # skills). The studio opens a MANAGED terminal (it appears on the Build
    # page like any other), launches the chosen CLI in the chosen destination
    # folder, and relays chat-style messages into it.

    @app.post("/creative/studio/start")
    def studio_start(body: StudioStartBody) -> dict[str, Any]:
        from ...terminals.ai_clis import detect_ai_clis

        cwd = Path((body.cwd or "").strip())
        if not cwd.is_absolute() or not cwd.is_dir():
            raise HTTPException(status_code=400, detail="cwd must be an existing folder")
        cli = next((c for c in detect_ai_clis() if c["id"] == body.cli), None)
        if cli is None:
            raise HTTPException(status_code=404, detail=f"unknown CLI '{body.cli}'")
        if not cli.get("installed"):
            raise HTTPException(
                status_code=424,
                detail=f"{cli['label']} isn't installed on this machine ({cli['url']})",
            )
        command = str(cli["command"]).strip()
        # Detection searches BEYOND the shell's PATH (~/.grok/bin, npm shims…)
        # — when the bare command wouldn't resolve in the spawned shell, launch
        # via the resolved executable instead of typing a name that errors.
        import shutil as _shutil

        exe = command.split()[0] if command else command
        if cli.get("path") and _shutil.which(exe) is None:
            rest = command[len(exe):]
            resolved = str(cli["path"])
            command = (
                f"& '{resolved}'{rest}" if os.name == "nt" else f"'{resolved}'{rest}"
            )
        flag = _AUTOPILOT_FLAGS.get(body.cli, "") if body.autopilot else ""
        if flag:
            command = f"{command} {flag}"
        # The connection nobody wires: the CLI's media skills need the Pixio
        # key as an ENV VAR, but the key lives in the daemon's vault. Inject it
        # into this terminal so generations work even when the user never set a
        # system-wide PIXIO_API_KEY — the Connections page becomes the single
        # source of truth for the whole studio.
        env = None
        key = _pixio_key()
        if key:
            env = {**os.environ, "PIXIO_API_KEY": key}
        try:
            session = d.platform.terminals.create(cwd=str(cwd), env=env)
        except RuntimeError as exc:  # session cap reached
            raise HTTPException(status_code=429, detail=str(exc))
        # CRITICAL: the studio drives this terminal purely over HTTP — no Build
        # WebSocket is attached — so start the background reader NOW, before the
        # CLI prints anything. Without it the output is never consumed: the tail
        # stays blank, auto-mode detection never sees Claude boot, and a
        # full-screen TUI stalls once its output buffer fills (= the "chat
        # initialises then nothing generates" failure this fixes).
        session.start_autodrain()
        if key:
            # studio_say adds a "your PIXIO_API_KEY is set" line to the first
            # brief so the CLI's skills use it instead of asking for a key.
            setattr(session, "_studio_pixio_env", True)
        # Type the launch into the shell ("\r" = Enter, same as a keystroke).
        session.write(command + "\r")
        # Claude's auto mode is engaged like a human does it: Shift+Tab cycles
        # after boot. Background + best-effort; /tail reports the live mode.
        automode_method = "flag" if flag else None
        if flag:
            # Flag-launched autopilot paints no mode banner — reflect it in the
            # sticky verdict so the badge doesn't dishonestly read "unverified".
            setattr(session, "_studio_automode", True)
        if body.autopilot and body.cli == "claude":
            import threading

            threading.Thread(
                target=_engage_claude_automode, args=(session,), daemon=True
            ).start()
            automode_method = "shift-tab"
        return {
            "terminal_id": session.id,
            "command": command,
            "cwd": str(cwd),
            "autopilot": bool(flag) or automode_method == "shift-tab",
            "automode_method": automode_method,
            "cli": cli["label"],
        }

    @app.post("/creative/studio/{terminal_id}/say")
    def studio_say(terminal_id: str, body: StudioSayBody) -> dict[str, Any]:
        session = d.platform.terminals.get(terminal_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such terminal")
        if not session.alive:
            raise HTTPException(status_code=409, detail="the terminal has exited")
        # SAFETY: a brief typed at a bare shell prompt runs as a SHELL COMMAND.
        # Refuse honestly whether the CLI exited (ready seen, prompt back) or
        # never came up at all (launch error, prompt still waiting).
        tail_now = session.output_tail()
        if getattr(session, "_studio_ready", False):
            ph, _ = derive_phase(tail_now, ready=True)
            if ph == "exited":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "the engine exited in this terminal — start a new session "
                        "(the terminal is still available on the Build page)"
                    ),
                )
        elif _PROMPT_AT_END_RE.search(tail_now[-300:].rstrip()):
            raise HTTPException(
                status_code=409,
                detail=(
                    "the engine hasn't started in this terminal — a bare shell "
                    "prompt is waiting, so a brief would run as a shell command. "
                    "Check the terminal on the Build page."
                ),
            )
        # Newlines would submit a CLI prompt early — flatten to one line.
        text = " ".join((body.text or "").split())
        if not text:
            raise HTTPException(status_code=400, detail="text is required")
        if body.first:
            skill = (body.skill or "").strip()
            skill_line = (
                f"Use your '{skill}' skill." if skill else _AUTO_SKILL_HINT
            )
            # The skills' own docs say "ask the user for a key" — preempt that:
            # the studio injected the vault key into this terminal's env.
            pixio_note = (
                " A Pixio API key is already set in the PIXIO_API_KEY "
                "environment variable — use it directly, never ask me for one."
                if getattr(session, "_studio_pixio_env", False)
                else ""
            )
            where = (body.save_dir or "").strip() or "the current working directory"
            text = (
                f"{skill_line}{pixio_note} Save every final media file into {where} "
                "(you are already in it). Work autonomously until the generation is "
                "fully complete — make reasonable creative choices instead of asking "
                f"me questions. Here is the brief: {text}"
            )
        # Open the quiet window BEFORE typing: the automode thread never sends a
        # Shift+Tab within _SAY_QUIET_SECONDS of this stamp, so a mode keystroke
        # can't land in the middle of the brief (or between it and its Enter).
        import time as _time

        setattr(session, "_last_say_ts", _time.time())
        _type_and_submit(session, text)
        return {"typed": True, "chars": len(text)}

    @app.get("/creative/studio/{terminal_id}/tail")
    def studio_tail(terminal_id: str, chars: int = 4000) -> dict[str, Any]:
        """Clean (ANSI-stripped) recent output for the studio's console preview
        — the full interactive pane lives on the Build page."""
        session = d.platform.terminals.get(terminal_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such terminal")
        import time as _time

        chars = max(200, min(int(chars), 32_000))
        full = session.output_tail()
        mode = latest_claude_mode(full)
        low = full.lower()
        # "The CLI has booted and is ready to accept a brief." A blind boot timer
        # let the first brief land in a not-yet-listening shell (→ dropped); the
        # UI gates on this instead. Claude paints "? for shortcuts" / an
        # "esc to interrupt" hint; any CLI that has printed a screenful BEYOND
        # the shell banner + echoed launch command is up. STICKY once true —
        # boot hints scroll out of the tail window, readiness doesn't regress.
        body_after_launch = "\n".join(full.strip().splitlines()[2:])
        if (
            bool(mode)
            or "? for shortcuts" in low
            or "shift+tab" in low
            or "esc to interrupt" in low
            or (
                len(body_after_launch.strip()) >= 80
                # Volume alone isn't readiness when the shell prompt is the
                # LAST thing painted — that's a CLI that errored/quit, not a
                # TUI waiting for input (derive_phase reports it as exited).
                and not _PROMPT_AT_END_RE.search(full[-300:].rstrip())
            )
        ):
            setattr(session, "_studio_ready", True)
        ready = bool(getattr(session, "_studio_ready", False))
        # Automode: the LATEST banner wins; when every banner has scrolled out
        # of the window, hold the last verified verdict instead of flickering.
        if mode is not None:
            setattr(session, "_studio_automode", mode in _AUTO_MODES)
        automode = bool(getattr(session, "_studio_automode", False))
        # Live lifecycle phase from the CLI's own output (freshness-guarded).
        loa = getattr(session, "last_output_at", 0.0)
        age = max(0.0, _time.monotonic() - loa) if loa else None
        phase, status_line = derive_phase(full, ready=ready, output_age=age)
        if not session.alive:
            phase = "exited"
        return {
            "tail": full[-chars:],
            "alive": session.alive,
            "exit_code": session.exit_code,
            # The LATEST permission-mode banner painted by the CLI (Claude):
            # lets the UI show an honest "auto mode engaged" badge.
            "mode": mode,
            "automode": automode,
            # Boot-readiness gate for the composer (see above).
            "ready": ready,
            # booting | thinking | idle | exited — the honest "is it working?"
            # signal (replaces the UI's old guess-from-idle-time fallback), plus
            # the CLI's own live progress line while a turn runs.
            "phase": phase,
            "status_line": status_line,
        }

    @app.get("/creative/studio-media")
    def studio_media(path: str, depth: int = 3) -> dict[str, Any]:
        """Recursively list media files under a studio destination (bounded
        walk). The Studio's new-media watcher uses this instead of a flat
        /fs/list so generations saved into SUBFOLDERS (pixio-story's normal
        output layout) still appear in the conversation."""
        import os as _os

        p = Path((path or "").strip())
        if not p.is_absolute():
            raise HTTPException(status_code=400, detail="absolute path required")
        ok, reason = fs_read_ok(str(p))
        if not ok:
            raise HTTPException(status_code=403, detail=f"blocked: {reason}")
        if not p.is_dir():
            raise HTTPException(status_code=404, detail="no such folder")
        depth = max(1, min(int(depth), 5))
        files: list[dict[str, Any]] = []
        scanned = 0
        truncated = False
        base_depth = len(p.parts)
        for root, dirs, names in _os.walk(str(p)):
            rp = Path(root)
            # Bound the walk: prune hidden dirs, stop descending past `depth`.
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            if len(rp.parts) - base_depth >= depth:
                dirs[:] = []
            for n in names:
                scanned += 1
                if scanned > 4000 or len(files) >= 800:
                    truncated = True
                    break
                if media_kind(n) is None:
                    continue
                fp = rp / n
                try:
                    st = fp.stat()
                except OSError:
                    continue
                files.append(
                    {
                        "path": str(fp),
                        "name": fp.relative_to(p).as_posix(),
                        "media": media_kind(n),
                        "size": st.st_size,
                        "mtime": st.st_mtime,
                    }
                )
            if truncated:
                break
        return {"files": files, "truncated": truncated}

    @app.post("/creative/ingest")
    def creative_ingest(body: CreativeIngestBody) -> dict[str, Any]:
        """Bring a local media file (a Studio generation on disk) into the
        durable gallery — the artifact store — so the Create tab's output shows
        up in Gallery, Share, and chat like every other creation. Idempotent:
        the artifact name embeds a content hash, so re-ingesting the same bytes
        returns the existing artifact instead of stacking versions."""
        import hashlib

        from ...tools.pixio import PixioUploadTool

        p = Path((body.path or "").strip())
        if not p.is_absolute():
            raise HTTPException(status_code=400, detail="absolute path required")
        if media_kind(p.name) is None:
            raise HTTPException(status_code=415, detail="not a media file")
        ok, reason = fs_read_ok(str(p))
        if not ok:
            raise HTTPException(status_code=403, detail=f"blocked: {reason}")
        if not p.is_file():
            raise HTTPException(status_code=404, detail="no such file")
        # read_bytes() buffers the whole file — refuse before reading (same cap
        # as publish/upload).
        if p.stat().st_size > PixioUploadTool._MAX_UPLOAD:
            raise HTTPException(status_code=413, detail="file too large to ingest (200MB max)")
        blob = p.read_bytes()
        digest = hashlib.sha1(blob).hexdigest()[:8]
        name = f"studio-{p.stem[:60]}-{digest}"
        kind = media_kind(p.name) or "file"
        existing = d.platform.artifacts.versions(name)
        if existing:
            return {"name": name, "version": existing[-1], "media": kind, "ingested": False}
        artifact = d.platform.artifacts.save(name, blob, kind=kind, filename=p.name)
        return {
            "name": artifact.name,
            "version": artifact.version,
            "media": kind,
            "size": artifact.size,
            "ingested": True,
        }

    @app.post("/creative/upload")
    async def creative_upload(body: CreativeUploadBody) -> dict[str, Any]:
        """Add a media file to the gallery (durable, versioned); optionally
        also publish it to Pixio's CDN for a permanent public url."""
        name = Path(body.filename).name.strip()
        if not name or media_kind(name) is None:
            raise HTTPException(
                status_code=415, detail="only image/video/audio files belong in the gallery"
            )
        try:
            blob = base64.b64decode(body.content_b64, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid base64: {exc}")
        if not blob:
            raise HTTPException(status_code=400, detail="empty file")
        from .. import app as _app

        if len(blob) > _app._MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="file too large")
        # A content-hash suffix keeps DIFFERENT files sharing a stem from
        # silently merging into one artifact (where Delete would nuke both).
        import hashlib

        digest = hashlib.sha1(blob).hexdigest()[:8]
        artifact = d.platform.artifacts.save(
            f"upload-{Path(name).stem[:60]}-{digest}",
            blob,
            kind=media_kind(name) or "file",
            filename=name,
        )
        out: dict[str, Any] = {
            "name": artifact.name,
            "version": artifact.version,
            "media": media_kind(name),
            "size": artifact.size,
        }
        if body.publish:
            import asyncio

            from ...tools.pixio import pixio_publish

            key = _pixio_key()
            if not key:
                out["publish_error"] = _MISSING_KEY
                return out
            try:
                out["url"] = await asyncio.to_thread(
                    pixio_publish, key, blob=blob, filename=name, mime=mime_for(name)
                )
            except RuntimeError as exc:
                out["publish_error"] = str(exc)
        return out
