"""A turn's failed steps, read from what its tools ANSWERED (v1.481.0).

A non-zero exit, a traceback or an ImportError is a failed step, whatever the
model says next. ``shell`` already answers ok=False for a non-zero exit; what
it cannot see is a command that HID its failure ("py x.py 2>&1 || echo
NEED_SCRIPT" exits 0 over a ``ModuleNotFoundError``). :func:`error_line` reads
the output for the error a program printed; :func:`failed_step_note` tells the
model in the call's own result; :class:`StepLog` keeps what failed and was never
done again, and the helper scripts written and never run, for the note under
the reply.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The tools whose output is a PROGRAM's output: only these are read for a
#: printed error (a file a read tool shows may hold a traceback: that is
#: the file, not a failure).
RUNS_PROGRAMS = frozenset({"shell", "process_start", "process_read", "run_code"})
#: Tools whose file argument may be a helper script.
WRITES_FILES = frozenset({"write_file", "write_document"})
_SCRIPT_EXTS = frozenset({".py", ".pyw", ".ps1", ".bat", ".cmd", ".sh", ".js", ".mjs", ".vbs"})

_TAIL_CHARS = 6_000
_TRACEBACK = "Traceback (most recent call last):"
#: A Python error line: ``ModuleNotFoundError: No module named 'base'``.
_ERROR_LINE_RX = re.compile(
    r"(?m)^[ \t]*([A-Za-z_][\w.]{0,80}(?:Error|Exception)):[ \t]?([^\n]{0,300})$"
)
#: Failures a program prints without a traceback.
_PRINTED_FAILURE_RX = re.compile(
    r"(No module named ['\"]?[\w.]{1,80}['\"]?"
    r"|can't open file [^\n]{1,300}"
    r"|is not recognized as an internal or external command"
    r"|[\w.-]{1,80}: command not found)",
)
_LABEL_CHARS = 140


def error_line(text: Any) -> str:
    """The error a program printed in *text*, else "". Linear; reads only the
    end of the output (a long run's own error is at its end)."""
    tail = str(text or "")[-_TAIL_CHARS:]
    at = tail.rfind(_TRACEBACK)
    if at >= 0:
        last = None
        for m in _ERROR_LINE_RX.finditer(tail, at):
            last = m
        if last is not None:
            return (last.group(1) + ": " + last.group(2)).strip().rstrip(":")[:300]
        return "a Python traceback"
    m = _PRINTED_FAILURE_RX.search(tail)
    return m.group(1).strip()[:300] if m else ""


def failed_step_note(name: str, result: Any) -> str:
    """The line added to a program's result when it answered ok but printed an
    error, else ""."""
    if name not in RUNS_PROGRAMS or result is None or not getattr(result, "ok", False):
        return ""
    found = error_line(getattr(result, "output", ""))
    if not found:
        return ""
    return (
        f"\n\n[Iron Jarvis: this step FAILED. The output shows an error ({found}) although "
        "the command's exit status hid it. If that error is the program's own (not text "
        "from a file it printed), the step did not work: fix it and run it again, or say "
        "it failed. Do not carry on as if it worked.]"
    )


def _mask(text: str) -> str:
    try:
        from ..detections.redact import mask

        return mask(text)
    except Exception:  # noqa: BLE001 - never let the record fail on a mask
        return text


def _label(name: str, args: dict) -> str:
    for key in ("command", "code", "path", "target", "output"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            one = " ".join(value.split())
            one = _mask(one)
            if len(one) > _LABEL_CHARS:
                one = one[: _LABEL_CHARS - 1] + "…"
            return f"{name} `{one}`"
    return name


def _key(name: str, args: dict) -> str:
    for key in ("command", "code", "path"):
        value = args.get(key)
        if isinstance(value, str):
            return f"{name}\x00{' '.join(value.split()).casefold()}"
    return name


@dataclass
class _Failed:
    label: str
    error: str


@dataclass
class StepLog:
    """The turn's steps that matter for the note under the reply."""

    failed: dict = field(default_factory=dict)        # key -> _Failed
    refused: list = field(default_factory=list)       # (label, words)
    scripts: list = field(default_factory=list)       # (index, name, path)
    commands: list = field(default_factory=list)      # (index, text casefolded)
    _n: int = 0

    def note(self, name: str, args: Any, result: Any) -> None:
        """Record one settled call (never raises)."""
        try:
            self._note(name, args if isinstance(args, dict) else {}, result)
        except Exception:  # noqa: BLE001 - the record is a summary, never a gate
            pass

    def _note(self, name: str, args: dict, result: Any) -> None:
        self._n += 1
        if result is None:
            return
        ok = bool(getattr(result, "ok", False))
        error = str(getattr(result, "error", "") or "")
        key = _key(name, args)
        if name in RUNS_PROGRAMS:
            text = " ".join(str(args.get("command") or args.get("code") or "").split()).casefold()
            if text:
                self.commands.append((self._n, text))
        if not ok and error.startswith("NOTHING WAS WRITTEN."):
            self.refused.append((_label(name, args), error[len("NOTHING WAS WRITTEN. "):][:220]))
            return
        printed = error_line(getattr(result, "output", "")) if ok and name in RUNS_PROGRAMS else ""
        if not ok:
            why = (
                error_line(getattr(result, "output", "")) or error_line(error) or error or "it failed"
            )
            why = " ".join(why.split())
            self.failed[key] = _Failed(_label(name, args), _mask(why)[:220])
        elif printed:
            self.failed[key] = _Failed(_label(name, args), _mask(printed)[:220])
        else:
            self.failed.pop(key, None)
        if ok and name in WRITES_FILES:
            raw = args.get("path")
            if isinstance(raw, str) and Path(raw).suffix.lower() in _SCRIPT_EXTS:
                self.scripts.append((self._n, Path(raw).name, raw))

    def failed_steps(self) -> list[tuple[str, str]]:
        return [(f.label, f.error) for f in self.failed.values()]

    def never_run(self) -> list[str]:
        """Helper scripts this turn wrote and no later command mentions."""
        out: list[str] = []
        for index, base, _raw in self.scripts:
            low = base.casefold()
            if not any(i > index and low in text for i, text in self.commands):
                if base not in out:
                    out.append(base)
        return out
