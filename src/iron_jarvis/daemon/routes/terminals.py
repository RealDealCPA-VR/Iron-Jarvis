"""Terminal routes: panes, WS stream, AI assist, transcript workflows.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from typing import Any

from ..app import _first_code_block, _ws_token_ok
from ..schemas import (
    TerminalAIBody,
    TerminalCreate,
    TerminalUpdate,
    TerminalWorkflowBody,
)

#: Screen-snippet uploads (Win+Shift+S -> a Build pane). Both Claude Code and
#: Codex cap an attached image around 5MB, so accepting more would only hand
#: the CLI a file it refuses; the Build page shrinks to fit before posting.
_SNIPPET_MAX_BYTES = 5 * 1024 * 1024

#: mime -> extension, and the SAFE EXTENSION ALLOWLIST. The extension comes
#: from the sniffed CONTENT, never from the pasted filename: the clipboard
#: hands us whatever the source app called it (often nothing at all).
_SNIPPET_MIME_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_SNIPPET_ALLOWED_EXT = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

#: Dotted subfolder of the PANE'S OWN cwd — see ``_store_snippet``.
_SNIPPET_DIR = ".ironjarvis/snippets"


def _sniff_snippet_mime(data: bytes) -> str | None:
    """Magic-byte sniff, reusing the avatar route's sniffer (one implementation
    of "is this an image", not two) and adding GIF, which the CLIs accept."""
    from .agents import _sniff_image  # lazy: sibling route module

    kind = _sniff_image(data)  # png / jpeg / webp
    if kind is None and data[:6] in (b"GIF87a", b"GIF89a"):
        kind = "gif"
    return f"image/{kind}" if kind else None


def _snippet_reference(cli: str, path) -> str:
    """How THIS cli wants an on-disk image named in its prompt.

    The per-CLI formatter lives in ``terminals/ai_clis.py`` (owned elsewhere).
    Until it lands we fall back to the bare absolute path — the one form
    documented to work on every CLI and every platform."""
    try:
        from ...terminals.ai_clis import image_reference  # type: ignore[attr-defined]
    except ImportError:
        image_reference = None  # type: ignore[assignment]
    if image_reference is not None:
        try:
            return str(image_reference(cli, str(path)))
        except Exception:  # noqa: BLE001 — a formatter must never lose the file
            pass
    text = str(path)
    return f'"{text}"' if " " in text else text


def _store_snippet(cwd: str, uploads, name: str, blob: bytes):
    """Write the snippet and say WHERE it landed. Blocking — call in a thread.

    Preference is the PANE'S OWN FOLDER: an AI CLI started with workspace
    confinement refuses to read a file outside its directory, which would break
    this feature in exactly the case it exists for. ``<home>/uploads`` is the
    fallback for a pane whose cwd is missing, protected, or unwritable, and the
    caller reports which of the two happened.

    A pane's cwd is normally a GIT REPO with an AI CLI running in it, and these
    snippets are screenshots of client material — so the dotted folder carries
    its own ``.gitignore`` (``*``) and can never be swept into a commit by a
    ``git add -A``. It is written before the snippet: if it cannot be written
    we take the uploads fallback rather than drop an untracked screenshot into
    the user's worktree.
    """
    from ...core.fs_policy import is_protected_path

    note = ""
    if (cwd or "").strip():
        base = Path(cwd)
        if not base.is_dir():
            note = f"the pane's folder does not exist ({cwd})"
        elif is_protected_path(str(base)):
            note = f"the pane's folder is protected ({cwd})"
        else:
            parts = _SNIPPET_DIR.split("/")
            folder = base.joinpath(*parts)
            try:
                folder.mkdir(parents=True, exist_ok=True)
                ignore = base / parts[0] / ".gitignore"
                if not ignore.exists():
                    ignore.write_text("*\n", encoding="utf-8")
                target = folder / name
                target.write_bytes(blob)
                return target, "pane", ""
            except OSError as exc:
                note = f"the pane's folder is not writable ({exc.__class__.__name__})"
    else:
        note = "this terminal has no working directory"
    uploads.mkdir(parents=True, exist_ok=True)
    target = uploads / name
    target.write_bytes(blob)
    return target, "uploads", note


def register(app: FastAPI, d) -> None:
    """Attach these routes to *app*; ``d`` is the create_app deps object."""
    @app.get("/terminals")
    def list_terminals() -> dict[str, Any]:
        return {"terminals": d.platform.terminals.list()}

    @app.get("/terminals/shells")
    def terminal_shells() -> dict[str, Any]:
        from ...terminals import available_shells

        return {"shells": available_shells()}

    @app.get("/terminals/ai-clis")
    def terminal_ai_clis() -> dict[str, Any]:
        """Which AI coding CLIs (Claude Code, Codex, Grok, opencode, …) are
        installed on this machine — so a terminal pane can offer a "Launch"
        dropdown that types the command for the user to run."""
        from ...terminals.ai_clis import detect_ai_clis

        return {"clis": detect_ai_clis()}

    @app.get("/terminals/activity")
    def terminal_activity() -> dict[str, Any]:
        """What the agent in each pane is DOING (v1.217.0).

        A SEPARATE, tiny endpoint rather than polling `GET /terminals`: the
        page loads its terminal list once and then mutates it locally on
        add/close, so re-polling that list would fight those local edits. This
        returns only the volatile part, which is exactly the shape the page
        already uses for its chat-status map.

        `seen` is deliberately NOT decided here. Whether the user has looked at
        a pane is a fact about the UI, so the daemon reports the settled state
        as `idle` and the client downgrades it to `done` when it knows the
        output arrived unwatched (herdr's rule: focusing marks a pane seen, a
        programmatic read does not).
        """
        panes = []
        for info in d.platform.terminals.list():
            panes.append(
                {
                    "id": info["id"],
                    "name": info.get("name"),
                    "agent_cli": info.get("agent_cli"),
                    "state": info.get("state"),
                    "state_line": info.get("state_line"),
                    "alive": info.get("alive"),
                }
            )
        return {"panes": panes}

    @app.post("/terminals")
    def create_terminal(body: TerminalCreate) -> dict[str, Any]:
        try:
            session = d.platform.terminals.create(
                cwd=body.cwd,
                shell=body.shell,
                cols=body.cols,
                rows=body.rows,
                name=getattr(body, "name", None),
                agent_cli=getattr(body, "agent_cli", None),
            )
        except RuntimeError as exc:  # session cap reached
            raise HTTPException(status_code=429, detail=str(exc))
        return session.info()

    @app.patch("/terminals/{term_id}")
    def update_terminal(term_id: str, body: TerminalUpdate) -> dict[str, Any]:
        """Rename a pane, or record which CLI was just launched into it.

        Both halves exist because the pane identity had no way IN. `name` was
        settable only by whoever created the pane — and the Build page's New
        terminal button does not ask for one — while `agent_cli` was known
        only to the browser: `launchCli` types the command into an already
        running shell, so the daemon never learned what started, and the
        classifier's "the catalog knows what it started" fallback was
        unreachable from the product. A feature only an API caller can use is
        not shipped.
        """
        session = d.platform.terminals.get(term_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such terminal")
        if body.name is not None:
            session.pane_name = body.name.strip() or None
        if body.agent_cli is not None:
            session.agent_cli = body.agent_cli.strip() or None
        # The exported identity follows the rename, so a CLI started AFTER it
        # sees the current name rather than the one the pane was born with.
        env = dict(session.pane_env_extra or {})
        for key, value in (
            ("IRONJARVIS_PANE_NAME", session.pane_name),
            ("IRONJARVIS_PANE_CLI", session.agent_cli),
        ):
            if value:
                env[key] = value
            else:
                env.pop(key, None)
        session.pane_env_extra = env or None
        d.platform.terminals.snapshot()  # a rename must survive a restart
        return session.info()

    @app.delete("/terminals/{term_id}")
    def kill_terminal(term_id: str) -> dict[str, Any]:
        return {"killed": d.platform.terminals.kill(term_id)}

    @app.websocket("/terminals/{term_id}/ws")
    async def terminal_ws(ws: WebSocket, term_id: str) -> None:
        if not _ws_token_ok(ws):
            await ws.close(code=1008)
            return
        session = d.platform.terminals.get(term_id)
        if session is None:
            await ws.close(code=1008)
            return
        await ws.accept()

        # Close code 4000 = "the shell itself exited" — the client shows the
        # Session-closed overlay and STOPS reconnecting (re-attaching to a dead
        # PTY put the pane in a crash->reconnect loop that also stole focus on
        # every cycle, killing open dropdowns — live-hit 2026-07-01).
        SHELL_EXITED = 4000
        exit_note = b"\r\n\x1b[33m[shell exited \xe2\x80\x94 close this pane or open a new terminal]\x1b[0m\r\n"

        async def close_exited() -> None:
            try:
                await ws.send_bytes(exit_note)
            except Exception:
                pass
            try:
                await ws.close(code=SHELL_EXITED)
            except Exception:
                pass

        if not session.alive:  # refuse a ZOMBIE attach outright
            await close_exited()
            return

        # This pane is now the live reader: the session's background auto-drain
        # (Creative Studio) steps aside while we're attached so we never race it
        # for the PTY's bytes. Balanced by remove_consumer() in the finally.
        session.add_consumer()

        # PERSISTENCE: replay the session's scrollback so a RE-ATTACHING pane
        # (the user switched tabs / navigated away and back) shows its history
        # instead of a blank screen. The shell itself never died — only the
        # browser's xterm buffer was lost — so we resend what it printed.
        history = session.scrollback_bytes()
        if history:
            try:
                await ws.send_bytes(history)
            except Exception:  # a client that drops mid-replay just reconnects
                pass

        async def pump_output() -> None:  # PTY -> client
            # 10ms idle poll: measured end-to-end, the shell's own echo is
            # ~50ms (ConPTY/PowerShell), so our added worst-case latency should
            # stay well under it. 100 wakeups/s per idle terminal is noise.
            while True:
                data = session.read()
                if data:
                    await ws.send_bytes(data)
                elif not session.alive:
                    await close_exited()  # tell the client WHY, then stop
                    break
                else:
                    await asyncio.sleep(0.01)

        out = asyncio.create_task(pump_output())
        # One repaint nudge per attach (armed until the pane's first resize,
        # which it always sends right after opening — see TerminalPane.onopen).
        repaint_pending = True
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                text = msg.get("text")
                try:
                    if text is not None:
                        try:
                            obj = json.loads(text)
                        except (ValueError, TypeError):
                            obj = None
                        if isinstance(obj, dict) and obj.get("type") == "resize":
                            cols, rows = int(obj["cols"]), int(obj["rows"])
                            # Scrollback replay can't reconstruct a FULL-SCREEN
                            # app's frame: a TUI (grok/claude/codex) paints only
                            # CHANGED cells, and its screen-setup sequences have
                            # long rolled out of the tail — a re-attached pane
                            # showed a blocky patchwork (live-hit 2026-07-16).
                            # A resize wiggle (one row down, then back) makes
                            # the app itself repaint every cell at the right
                            # size; a line-mode shell just ignores it.
                            if repaint_pending and rows > 1:
                                session.resize(cols, rows - 1)
                            repaint_pending = False
                            session.resize(cols, rows)
                        else:
                            session.write(text)
                    elif msg.get("bytes") is not None:
                        session.write(msg["bytes"])
                except Exception:  # writing to a dying PTY must never crash the WS
                    await close_exited()
                    break
        except WebSocketDisconnect:
            pass
        finally:
            session.remove_consumer()  # hand the PTY back to the background drain
            out.cancel()
            try:
                await ws.close()
            except Exception:
                pass

    @app.post("/terminals/{term_id}/ai")
    async def terminal_ai(term_id: str, body: TerminalAIBody) -> dict[str, Any]:
        """Per-terminal AI assist with a PER-PANE model choice.

        Sends the terminal's recent (ANSI-stripped) output tail + the user's
        question to the chosen model and returns the reply plus the first
        fenced code block as a suggested command. SUGGEST-ONLY: nothing is ever
        written into the shell here — running the suggestion is an explicit
        click in the UI, which types it through the normal WebSocket path.
        """
        session = d.platform.terminals.get(term_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such terminal")
        provider = body.provider or d.platform.config.default_provider
        model = body.model or d.platform.config.default_model
        try:
            adapter = d.platform.providers.get(provider, model)
        except Exception as exc:  # unknown provider / no credential
            raise HTTPException(status_code=400, detail=f"provider unavailable: {exc}")
        from ...providers.adapters.base import LLMMessage

        tail = session.output_tail()[-6000:]  # bound the context we bill for
        shell_os = "Windows" if os.name == "nt" else "POSIX"
        system = (
            "You are a terminal assistant embedded in a dashboard shell pane "
            f"(shell: {session.shell}, OS: {shell_os}). "
            "Answer the user's question about their recent terminal output "
            "briefly and concretely. When the best answer is a command to run, "
            "put EXACTLY ONE command alone in a fenced code block; explain in "
            "one or two sentences at most. Never invent output."
        )
        # Skills: make the WHOLE discovered library (builtin + user + Claude +
        # Codex) usable by ANY provider — as prompt injection, not tool calls,
        # so it works identically on models with weak/no tool support.
        skills_used: list[str] = []
        chosen = []
        want = (body.skill or "").strip()
        if want.lower() == "none":
            chosen = []
        elif want:
            sk = d.platform.skills.get(want)
            if sk is None:
                raise HTTPException(status_code=404, detail=f"no such skill: {want}")
            chosen = [sk]
        else:  # AUTO: best matches for the request (quietly none if no hit)
            try:
                chosen = d.platform.skills.search(body.prompt, k=2)
            except Exception:  # noqa: BLE001 — skills must never break assist
                chosen = []
        skill_block = ""
        for sk in chosen[:2]:
            skills_used.append(sk.name)
            skill_block += f"\n\n## Skill: {sk.name}\n{sk.instructions[:6000]}"
        if skill_block:
            system += (
                "\n\n# Skills\nThe user's skill library provides these playbooks — "
                "follow them when they apply to the request." + skill_block
            )

        # Cross-terminal sharing: fold in the recent output of OTHER terminals
        # the user selected, clearly labeled, so this pane's model (whichever
        # provider it is) can reason across sessions. Bounded: max 3, 4KB each.
        shared = ""
        for other_id in (body.include_terminals or [])[:3]:
            if other_id == term_id:
                continue
            other = d.platform.terminals.get(other_id)
            if other is None:
                continue
            other_tail = other.output_tail()[-4000:]
            if other_tail.strip():
                shared += (
                    f"\n\n--- Output from ANOTHER terminal "
                    f"({other.shell} @ {other.cwd}) ---\n{other_tail}"
                )

        user = (
            f"Recent terminal output (truncated):\n\n{tail}"
            f"{shared}\n\n"
            f"Request: {body.prompt}"
        )
        resp, used_provider, used_model = await d._one_shot_complete(
            provider,
            adapter,
            system=system,
            messages=[LLMMessage(role="user", content=user)],
        )
        return {
            "reply": resp.text,
            "command": _first_code_block(resp.text),
            "provider": used_provider,
            "model": used_model or model,
            "skills": skills_used,
        }

    @app.get("/terminals/{term_id}/context")
    def terminal_context(term_id: str) -> dict[str, Any]:
        """This terminal's recent activity as CLEAN text (ANSI-stripped), ready
        to paste into another terminal's AI CLI (claude/codex/…) or anywhere
        else — the universal way to share one session's context with any LLM."""
        session = d.platform.terminals.get(term_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such terminal")
        tail = session.output_tail()
        text = (
            f"[Context from an Iron Jarvis terminal — {session.shell} @ {session.cwd}]\n"
            f"{tail.strip() or '(no output yet)'}"
        )
        return {"text": text, "chars": len(text)}

    @app.post("/terminals/{term_id}/snippet")
    async def terminal_snippet(term_id: str, body: dict) -> dict[str, Any]:
        """Land a pasted screen snippet (Win+Shift+S) on disk FOR this pane.

        A ConPTY pane is a byte stream — there is no image channel to paste
        into. But every supported AI CLI reads images OFF DISK given a path,
        and the daemon runs on the same machine as the CLI child, so a path is
        genuinely shared state. So: decode -> sniff -> write -> hand back the
        path plus the `reference` string this CLI wants in its prompt. We do
        NOT synthesize a paste keystroke: that is CLI- and platform-specific,
        and Ctrl+V after Win+Shift+S is the documented no-op on Windows.
        """
        import base64
        import hashlib
        import re

        session = d.platform.terminals.get(term_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such terminal")

        content_b64 = str(body.get("content_b64") or "")
        if not content_b64:
            raise HTTPException(status_code=400, detail="content_b64 is required")
        limit_mb = _SNIPPET_MAX_BYTES // (1024 * 1024)
        # Reject on the base64 LENGTH first (4/3 expansion) so an oversized
        # body is never buffered as bytes at all.
        approx = (len(content_b64) * 3) // 4
        if approx > _SNIPPET_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    # One decimal, never floored: "too large (~5 MB); limit is
                    # 5 MB" is a refusal that states a size inside its own
                    # limit, which reads as a bug rather than an honest error.
                    f"snippet too large (~{approx / (1024 * 1024):.1f} MB); "
                    f"limit is {limit_mb} MB"
                ),
            )
        # Decoding megabytes on the event loop presents to the whole app as
        # "Daemon offline" — off the loop it goes.
        try:
            blob = await asyncio.to_thread(base64.b64decode, content_b64, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"invalid base64: {exc}")
        if not blob:
            raise HTTPException(status_code=400, detail="empty snippet")
        if len(blob) > _SNIPPET_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"snippet too large ({len(blob) / (1024 * 1024):.1f} MB); "
                    f"limit is {limit_mb} MB"
                ),
            )

        mime = _sniff_snippet_mime(blob)
        ext = _SNIPPET_MIME_EXT.get(mime or "", "")
        if mime is None or ext not in _SNIPPET_ALLOWED_EXT:
            raise HTTPException(
                status_code=415,
                detail="not an image — snippets must be PNG, JPEG, GIF or WebP",
            )

        # UNIQUE NAMES: a content digest suffix (the /creative/upload
        # convention). Two snippets both called "image.png" must never clobber
        # each other — the second one is usually the one the user just took.
        raw = str(body.get("filename") or "")
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", Path(raw).name).strip("._")
        stem = Path(stem).stem[:40] or "snippet"
        digest = hashlib.sha1(blob).hexdigest()[:8]
        name = f"{stem}-{digest}{ext}"

        uploads = d.platform.config.home / "uploads"
        path, location, note = await asyncio.to_thread(
            _store_snippet, session.cwd, uploads, name, blob
        )
        out: dict[str, Any] = {
            "path": str(path),
            "name": name,
            "bytes": len(blob),
            "mime": mime,
            "reference": _snippet_reference(str(body.get("cli") or ""), path),
            "location": location,  # "pane" (its own folder) or "uploads"
        }
        if note:  # a fallback always SAYS why it fell back
            out["note"] = note
        return out

    @app.post("/terminals/{term_id}/workflow")
    async def terminal_to_workflow(
        term_id: str, body: TerminalWorkflowBody
    ) -> dict[str, Any]:
        """Turn THIS terminal session into a repeatable workflow.

        Feeds the session's (ANSI-stripped) transcript to the same agent that
        powers the workflow builder, asking it to extract the meaningful commands
        into an ordered ``{name, steps}`` workflow. Saves + returns it so the
        dashboard can open it in the editor. Read-only w.r.t. the shell.
        """
        session = d.platform.terminals.get(term_id)
        if session is None:
            raise HTTPException(status_code=404, detail="no such terminal")
        tail = session.output_tail()[-8000:]
        if not tail.strip():
            raise HTTPException(
                status_code=400, detail="this terminal has no output to turn into a workflow yet"
            )
        note = (body.note or "").strip()
        description = (
            "Below is a transcript of a terminal session — the shell prompts, the "
            "commands that were run, and their output. Turn the MEANINGFUL commands "
            "into a repeatable workflow so this whole process can be run again from "
            "scratch. Ignore typos, failed/exploratory commands, and interactive "
            "noise; keep the steps concrete, in order, and parameterize obvious "
            "specifics (paths, names) in the task text where sensible.\n\n"
        )
        if note:
            description += f"What this session was doing: {note}\n\n"
        description += f"Terminal transcript:\n```\n{tail}\n```"
        result = await d._build_workflow(description, body.provider, body.model)
        # Never save/return an empty definition: if the model couldn't extract
        # any runnable steps from the transcript, surface an honest upstream
        # error rather than a hollow workflow.
        if not (result.get("steps") if isinstance(result, dict) else None):
            raise HTTPException(
                status_code=502,
                detail="could not extract any workflow steps from this terminal session",
            )
        return result
