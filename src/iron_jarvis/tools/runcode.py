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
from typing import Any

from .base import Reversibility, Tool, ToolContext, ToolResult

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

        if lang == "python":
            interp = _python_interpreter()
            if not interp:
                script.unlink(missing_ok=True)
                return ToolResult(
                    ok=False,
                    error="no Python interpreter on this machine (packaged "
                    "install) — use language 'powershell' instead, or install "
                    "Python and retry",
                )
            argv = [interp, str(script)]
        elif lang == "powershell":
            exe = "powershell" if sys.platform == "win32" else "pwsh"
            argv = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                    "Bypass", "-File", str(script)]
        else:  # bash
            argv = ["bash", str(script)]

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
        header = f"exit {rc}" + (f" · kept {kept_rel}" if kept_rel else " · script discarded")
        return ToolResult(
            ok=rc == 0,
            output=f"{header}\n{combined}".strip(),
            error=None if rc == 0 else f"script exited {rc}: {err.strip()[:400] or out.strip()[:400]}",
            data={"exit_code": rc, "kept": kept_rel, "language": lang},
        )
