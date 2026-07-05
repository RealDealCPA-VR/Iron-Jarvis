"""Terminal routes: panes, WS stream, AI assist, transcript workflows.

Moved verbatim from daemon/app.py's create_app; closure-local state is
reached through ``d`` (see the deps object built in create_app).
"""

from __future__ import annotations

import asyncio
import json
import os

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from typing import Any

from ..app import _first_code_block, _ws_token_ok
from ..schemas import TerminalAIBody, TerminalCreate, TerminalWorkflowBody


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

    @app.post("/terminals")
    def create_terminal(body: TerminalCreate) -> dict[str, Any]:
        try:
            session = d.platform.terminals.create(
                cwd=body.cwd, shell=body.shell, cols=body.cols, rows=body.rows
            )
        except RuntimeError as exc:  # session cap reached
            raise HTTPException(status_code=429, detail=str(exc))
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
                            session.resize(int(obj["cols"]), int(obj["rows"]))
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
        return await d._build_workflow(description, body.provider, body.model)
