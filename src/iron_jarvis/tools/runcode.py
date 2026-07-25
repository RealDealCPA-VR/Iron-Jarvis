"""Disposable code execution (v1.90.0) — the agent's escape hatch.

When no tool reliably handles a task, the agent writes a small script, runs
it, and reads the output. The script is DISPOSABLE by default: it executes
from a scratch folder inside the workspace and is deleted after the run.
``keep=true`` retains it under ``<workspace>/scripts/`` for ongoing use, and
the tool's description steers the agent to record a PROVEN solution with
``skill_create`` so future sessions reference how the problem was solved.

Honesty on a frozen install: the packaged daemon carries no Python
interpreter. ``python`` runs when a real interpreter exists (the dev venv, or
one on PATH); otherwise the tool says so and suggests PowerShell — which is
always present on Windows. Execution is workspace-cwd'd, time-boxed, and
output-capped; the permission tier matches ``shell`` ("ask" — arming it in
chat is the explicit consent).
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .base import Reversibility, Tool, ToolContext, ToolResult

#: ``(name, language, code, session_id, exit_code, output) -> None`` — the
#: durable sink for executed scripts (wired to the Code Lab store in platform).
CodeArtifactSink = Callable[[str, str, str, "str | None", int, str], None]

_MAX_OUTPUT = 12_000
_MAX_TIMEOUT = 300


def _python_interpreter() -> "str | None":
    """A REAL python interpreter: the running one when not frozen, else the
    first on PATH. None = honestly unavailable (frozen install, no python)."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    import shutil

    return shutil.which("python") or shutil.which("python3")


_LANGS = {
    "python": {"suffix": ".py"},
    "powershell": {"suffix": ".ps1"},
    "bash": {"suffix": ".sh"},
}


class ScriptRunFailed(Exception):
    """Execution could not be attempted (no interpreter, timeout, missing exe).

    Carries a human-readable reason; the caller decides whether that is a tool
    error or an HTTP 400/503.
    """


def script_argv(language: str, script: "Path") -> list[str]:
    """The argv that runs ``script``. Raises :class:`ScriptRunFailed` when the
    interpreter honestly isn't on this machine (frozen install with no Python)."""
    if language == "python":
        interp = _python_interpreter()
        if not interp:
            raise ScriptRunFailed(
                "no Python interpreter on this machine (packaged install) — use "
                "language 'powershell' instead, or install Python and retry"
            )
        return [interp, str(script)]
    if language == "powershell":
        exe = "powershell" if sys.platform == "win32" else "pwsh"
        return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                "Bypass", "-File", str(script)]
    return ["bash", str(script)]


async def execute_script(
    language: str, code: str, cwd: "Path", timeout_s: int = 60
) -> "tuple[int, str]":
    """Write ``code`` to a temp script under ``cwd`` and run it; return
    ``(exit_code, combined_output)``.

    The RE-RUN path (Code Lab). It differs from :class:`RunCodeTool` only in
    file handling — the tool honors ``keep`` and names the script, while a
    re-run always writes a throwaway file because the source of truth is the
    stored record. Everything that MUST agree between them is shared code:
    :func:`script_argv` for interpreter detection, ``_MAX_TIMEOUT`` for the
    ceiling, ``_MAX_OUTPUT`` for the cap.
    """
    if language not in _LANGS:
        raise ScriptRunFailed(f"language must be one of {', '.join(_LANGS)}")
    if not (code or "").strip():
        raise ScriptRunFailed("code is required")
    timeout = min(max(int(timeout_s or 60), 1), _MAX_TIMEOUT)
    cwd = Path(cwd)
    cwd.mkdir(parents=True, exist_ok=True)
    script = cwd / f".rerun_{int(time.time() * 1000)}{_LANGS[language]['suffix']}"
    script.write_text(code, encoding="utf-8")
    try:
        argv = script_argv(language, script)

        def _run() -> "tuple[int, str, str]":
            proc = subprocess.run(
                argv, cwd=str(cwd), capture_output=True, text=True,
                timeout=timeout, shell=False,
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""

        try:
            rc, out, err = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            raise ScriptRunFailed(f"script timed out after {timeout}s")
        except FileNotFoundError:
            raise ScriptRunFailed(f"{argv[0]} is not available on this machine")
    finally:
        script.unlink(missing_ok=True)  # the SOURCE lives in the store, not on disk
    combined = out + (("\n[stderr]\n" + err) if err.strip() else "")
    if len(combined) > _MAX_OUTPUT:
        combined = combined[:_MAX_OUTPUT] + f"\n[output clipped at {_MAX_OUTPUT} chars]"
    return rc, combined


class RunCodeTool(Tool):
    name = "run_code"
    reversibility = Reversibility.IRREVERSIBLE  # a script can do anything
    returns_untrusted_content = True  # its output may echo untrusted file text
    description = (
        "Write and execute a small DISPOSABLE script when no other tool can "
        "reliably do the job (odd file formats, bulk transforms, gnarly "
        "parsing). Languages: python (needs an interpreter on the machine), "
        "powershell (always available on Windows), bash. Runs inside the "
        "workspace with a timeout; the script is deleted after the run unless "
        "keep=true (then it stays under scripts/ for ongoing use). If the "
        "script SOLVED a hard problem, save the approach + code as a skill "
        "with skill_create so future sessions know how it was done."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "language": {"type": "string", "enum": list(_LANGS)},
            "code": {"type": "string"},
            "keep": {
                "type": "boolean",
                "description": "Keep the script under scripts/ (default: delete after run)",
            },
            "filename": {"type": "string", "description": "Script name when kept"},
            "timeout_s": {"type": "integer", "description": "Seconds (default 60, max 300)"},
        },
        "required": ["language", "code"],
    }

    def __init__(self, sink: "CodeArtifactSink | None" = None) -> None:
        #: Called after every COMPLETED run with
        #: ``(name, language, code, session_id, exit_code, output)`` — the
        #: platform wires this to the Code Lab store so the script outlives the
        #: session workspace it ran in. A failing sink never breaks a run: the
        #: agent's task matters more than the bookkeeping.
        self._sink = sink

    def _record(
        self, name: str, lang: str, code: str, ctx: ToolContext, rc: int, output: str
    ) -> None:
        if self._sink is None:
            return
        try:
            self._sink(name, lang, code, getattr(ctx, "session_id", None), rc, output)
        except Exception:  # noqa: BLE001 — bookkeeping never breaks the task
            pass

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        lang = str(args.get("language", "")).strip().lower()
        code = str(args.get("code") or "")
        if lang not in _LANGS:
            return ToolResult(
                ok=False, error=f"language must be one of {', '.join(_LANGS)}"
            )
        if not code.strip():
            return ToolResult(ok=False, error="code is required")
        keep = bool(args.get("keep"))
        timeout = min(max(int(args.get("timeout_s") or 60), 1), _MAX_TIMEOUT)

        ws = Path(ctx.workspace)
        folder = ws / ("scripts" if keep else ".scratch")
        folder.mkdir(parents=True, exist_ok=True)
        raw_name = str(args.get("filename") or "").strip()
        stem = "".join(ch for ch in raw_name if ch.isalnum() or ch in "._-").strip(
            "._"
        ) or f"run_{int(time.time())}"
        suffix = _LANGS[lang]["suffix"]
        script = folder / (stem if stem.endswith(suffix) else stem + suffix)
        script.write_text(code, encoding="utf-8")

        # Shared with the Code Lab re-run endpoint, so interpreter detection
        # (and the honest "no Python on a frozen install" message) can never
        # drift between the two paths.
        try:
            argv = script_argv(lang, script)
        except ScriptRunFailed as exc:
            script.unlink(missing_ok=True)
            return ToolResult(ok=False, error=str(exc))

        def _run() -> "tuple[int, str, str]":
            proc = subprocess.run(
                argv, cwd=str(ws), capture_output=True, text=True,
                timeout=timeout, shell=False,
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""

        try:
            rc, out, err = await asyncio.to_thread(_run)
        except subprocess.TimeoutExpired:
            if not keep:
                script.unlink(missing_ok=True)
            return ToolResult(ok=False, error=f"script timed out after {timeout}s")
        except FileNotFoundError:
            script.unlink(missing_ok=True)
            return ToolResult(
                ok=False, error=f"{argv[0]} is not available on this machine"
            )
        except Exception as exc:  # noqa: BLE001 — report, never crash the loop
            if not keep:
                script.unlink(missing_ok=True)
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        finally:
            if not keep:
                script.unlink(missing_ok=True)

        combined = out + (("\n[stderr]\n" + err) if err.strip() else "")
        if len(combined) > _MAX_OUTPUT:
            combined = combined[:_MAX_OUTPUT] + f"\n[output clipped at {_MAX_OUTPUT} chars]"
        kept_rel = f"scripts/{script.name}" if keep else None
        # The script file is gone (or dies with the workspace) — persist the
        # SOURCE so it stays browsable + re-runnable from the Artifacts page.
        self._record(script.stem, lang, code, ctx, rc, combined)
        header = f"exit {rc}" + (f" · kept {kept_rel}" if kept_rel else " · script discarded")
        return ToolResult(
            ok=rc == 0,
            output=f"{header}\n{combined}".strip(),
            error=None if rc == 0 else f"script exited {rc}: {err.strip()[:400] or out.strip()[:400]}",
            data={"exit_code": rc, "kept": kept_rel, "language": lang},
        )
