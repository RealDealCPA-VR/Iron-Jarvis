"""Schedule KNOBS (v1.299.0): the optional payload keys a task schedule may
carry, validated ONCE here for ``POST /schedules``, ``PATCH /schedules/{name}``
and the agent-made ``schedule_create`` tool, and read back here by the fire
(``platform._dispatch_scheduled``) and the agent runtime.

The five knobs, all optional:

- ``skills: [str]`` — names that must exist in the skills registry; the fire
  builds the run's definition with them added (``agents/types.with_skills``
  copies, so the shared builtin/dynamic definition is never mutated).
- ``workspace_root: str`` — the folder the run works IN, validated exactly like
  the spawn route (``fs_policy.usable_workspace_root``, same words); the fire
  also flags the session (``options["folder_rules"]``) so the runtime loads
  the folder's ``AGENTS.md`` / ``.ironjarvis.md`` as "# Folder rules", scanned
  through ``core/promptguard`` (source ``folder rules <file>``).
- ``context_from: str`` — the NAME of another schedule whose last session's
  ``summary`` is appended to the task prompt as "Earlier result from <name>
  (<when>):". A missing schedule/session is a one-line note in the prompt and
  on the row's ``last_detail``; it never stops the fire.
- ``script: {command, timeout_s (≤ 120), cwd: workspace|home}`` — a PRE-RUN
  data-collection command run through the SAME confinement the ``shell`` tool
  uses (``SandboxManager`` with the configured policy; Docker preferred under
  an isolating policy or low trust; refused under low trust with no Docker,
  exactly like the shell tool). Output is capped (head + tail, 8,000 chars),
  scanned (source ``pre-run script``) and appended as "Pre-run data:". A
  non-zero exit is appended as a note and the fire proceeds. REFUSED when the
  request did not come from a user: the agent-made tool cannot set it.
- ``skip_memory: bool`` — the runtime skips the lessons, the memory index and
  the fabric grounding for that session (profile and voice still inject).
  Only the INJECTION is skipped: the run's memory TOOLS and its post-run
  learning/summary writes are untouched.

Every validator raises :class:`KnobError` with ONE sentence and the status the
route should answer with (422, or 400 for the folder, the spawn route's code).
Functions marked BLOCKING probe the disk or run a process: callers hop them
off the event loop (``asyncio.to_thread``), the v1.153.1 rule.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: The payload keys this module owns, in display order.
KNOB_KEYS: tuple[str, ...] = (
    "skills",
    "workspace_root",
    "context_from",
    "script",
    "skip_memory",
)
#: A pre-run script may run this long at most (seconds).
SCRIPT_TIMEOUT_MAX = 120
#: …and defaults to this when the knob names no timeout.
SCRIPT_TIMEOUT_DEFAULT = 60
#: Head + tail cap on the script's output before it rides the prompt.
SCRIPT_OUTPUT_CAP = 8_000
#: Head + tail cap on an earlier result (``context_from``).
EARLIER_RESULT_CAP = 4_000
#: Head + tail cap on a folder's rules file.
FOLDER_RULES_CAP = 8_000
#: How much of a rules file is READ at all (the scan caps it further).
_RULES_READ_CHARS = 64_000
#: The rules files looked for in a working folder, first found wins.
FOLDER_RULES_FILES: tuple[str, ...] = ("AGENTS.md", ".ironjarvis.md")
#: The two places a script may run.
SCRIPT_CWDS: tuple[str, ...] = ("workspace", "home")

#: The spawn route's words (routes/agents.py), kept byte-identical here so the
#: two doors refuse a folder in the same voice.
WORKSPACE_ROOT_DETAIL = (
    "workspace_root must be an existing, absolute, non-protected folder this "
    "app may write in (missing, protected, not a directory, or not writable): "
    "{root} — pick a folder you can save files in"
)
#: The one sentence the agent-made tool answers with when it tries to set a script.
SCRIPT_FROM_AGENT_REFUSAL = (
    "a pre-run 'script' can only be set by the user from the Schedules page — "
    "an agent-made schedule cannot carry one"
)


class KnobError(ValueError):
    """A knob the request cannot have, with the HTTP status it earns."""

    def __init__(self, detail: str, status: int = 422) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


@dataclass(frozen=True)
class Script:
    command: str
    timeout_s: int = SCRIPT_TIMEOUT_DEFAULT
    cwd: str = "workspace"

    def as_payload(self) -> dict[str, Any]:
        return {"command": self.command, "timeout_s": self.timeout_s, "cwd": self.cwd}


# --------------------------------------------------------------------------- #
# decoding — what the fire and the list read
# --------------------------------------------------------------------------- #


def _str_or_empty(value: Any) -> str:
    """A payload string, or "" — never coerced (the v1.171.0 rule: a
    stringified number could phantom-match a real name)."""
    return value.strip() if isinstance(value, str) else ""


def decode_script(value: Any) -> Script | None:
    """The script knob as a :class:`Script`, or None when absent/malformed.
    Lenient on purpose (a legacy row must not break the list): the STRICT
    shape is enforced by :func:`validate_knobs` at add/patch time."""
    if not isinstance(value, dict):
        return None
    command = _str_or_empty(value.get("command"))
    if not command:
        return None
    timeout = value.get("timeout_s", SCRIPT_TIMEOUT_DEFAULT)
    try:
        timeout_i = int(timeout)
    except (TypeError, ValueError):
        timeout_i = SCRIPT_TIMEOUT_DEFAULT
    timeout_i = max(1, min(timeout_i, SCRIPT_TIMEOUT_MAX))
    cwd = _str_or_empty(value.get("cwd")) or "workspace"
    if cwd not in SCRIPT_CWDS:
        cwd = "workspace"
    return Script(command=command, timeout_s=timeout_i, cwd=cwd)


def decode_knobs(payload: Any) -> dict[str, Any]:
    """The five knobs as the GET /schedules row shows them — additive keys,
    absent/garbage decoded to the empty value, never coerced. ``script``
    carries the command, timeout and cwd only, never any output."""
    p = payload if isinstance(payload, dict) else {}
    raw_skills = p.get("skills")
    skills = (
        [s.strip() for s in raw_skills if isinstance(s, str) and s.strip()]
        if isinstance(raw_skills, list)
        else []
    )
    script = decode_script(p.get("script"))
    return {
        "skills": skills,
        "workspace_root": _str_or_empty(p.get("workspace_root")),
        "context_from": _str_or_empty(p.get("context_from")),
        "script": script.as_payload() if script is not None else None,
        "skip_memory": p.get("skip_memory") is True,
    }


def has_knobs(payload: Any) -> bool:
    """True when the payload carries at least one knob key."""
    return isinstance(payload, dict) and any(k in payload for k in KNOB_KEYS)


# --------------------------------------------------------------------------- #
# validation — ONE set of rules for add, patch and the agent tool
# --------------------------------------------------------------------------- #


def validate_knobs(
    payload: Any,
    *,
    name: str,
    kind: str,
    scheduler: Any,
    skills: Any,
    from_user: bool = True,
) -> None:
    """Refuse a payload whose knobs the fire could not honour, with one
    sentence each (:class:`KnobError`). ``name`` is the schedule's own name
    (``context_from`` may not be itself); ``from_user`` False is the agent-made
    tool, which may not set ``script``. BLOCKING when ``workspace_root`` is
    set (the writability probe creates a file): hop it off the loop."""
    if not isinstance(payload, dict) or not has_knobs(payload):
        return
    if kind != "task":
        present = ", ".join(k for k in KNOB_KEYS if k in payload)
        raise KnobError(
            f"{present}: these schedule knobs apply to task schedules only — "
            f"this one is a {kind!r} schedule"
        )
    if "skills" in payload:
        raw = payload.get("skills")
        if not isinstance(raw, list) or not all(isinstance(s, str) and s.strip() for s in raw):
            raise KnobError("skills must be a list of skill names (non-empty strings)")
        for skill_name in raw:
            found = None
            try:
                found = skills.get(skill_name.strip()) if skills is not None else None
            except Exception:  # noqa: BLE001 — a broken registry refuses, never guesses
                found = None
            if found is None:
                raise KnobError(
                    f"unknown skill {skill_name!r} — the skills registry has no skill by "
                    "that name (Skills page)"
                )
    if "workspace_root" in payload:
        raw_root = payload.get("workspace_root")
        if raw_root not in (None, ""):
            if not isinstance(raw_root, str) or not raw_root.strip():
                raise KnobError("workspace_root must be a folder path (a string)")
            from ..core.fs_policy import usable_workspace_root

            root = raw_root.strip()
            if not usable_workspace_root(root):  # BLOCKING — see the docstring
                raise KnobError(WORKSPACE_ROOT_DETAIL.format(root=root), status=400)
    if "context_from" in payload:
        raw_from = payload.get("context_from")
        if raw_from not in (None, ""):
            if not isinstance(raw_from, str) or not raw_from.strip():
                raise KnobError("context_from must be the name of another schedule")
            other = raw_from.strip()
            if other == (name or "").strip():
                raise KnobError("context_from cannot name the schedule itself")
            if scheduler is None or scheduler.get(other) is None:
                raise KnobError(
                    f"context_from names no existing schedule {other!r} — add that "
                    "schedule first"
                )
    if "script" in payload:
        raw_script = payload.get("script")
        if raw_script not in (None, {}):
            if not from_user:
                raise KnobError(SCRIPT_FROM_AGENT_REFUSAL)
            if not isinstance(raw_script, dict):
                raise KnobError(
                    "script must be an object with 'command' (and optional "
                    "'timeout_s', 'cwd')"
                )
            command = raw_script.get("command")
            if not isinstance(command, str) or not command.strip():
                raise KnobError("script needs a non-empty 'command' string")
            timeout = raw_script.get("timeout_s", SCRIPT_TIMEOUT_DEFAULT)
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, int)
                or timeout < 1
                or timeout > SCRIPT_TIMEOUT_MAX
            ):
                raise KnobError(
                    f"script timeout_s must be a whole number of seconds from 1 to "
                    f"{SCRIPT_TIMEOUT_MAX}"
                )
            cwd = raw_script.get("cwd", "workspace")
            if cwd not in SCRIPT_CWDS:
                raise KnobError("script cwd must be 'workspace' or 'home'")
    if "skip_memory" in payload and not isinstance(payload.get("skip_memory"), bool):
        raise KnobError("skip_memory must be true or false")


def merge_payload(current: Any, payload_set: Any, payload_unset: Any) -> dict[str, Any]:
    """The PATCH merge: ``current`` with ``payload_set``'s keys written over
    it and ``payload_unset``'s keys removed. Unset wins over set for the same
    key — removing is the explicit intent."""
    merged = dict(current) if isinstance(current, dict) else {}
    if isinstance(payload_set, dict):
        merged.update(payload_set)
    for key in payload_unset or []:
        merged.pop(str(key), None)
    return merged


# --------------------------------------------------------------------------- #
# session options — the row's ``options_json`` as a dict
# --------------------------------------------------------------------------- #


def session_options(session: Any) -> dict[str, Any]:
    """The decoded ``Session.options_json`` (v1.299.0) — ``{}`` for a legacy
    row, a stub without the column, or junk in the column (the fail-closed
    direction: no option means no knob)."""
    raw = getattr(session, "options_json", None)
    if not raw:
        return {}
    try:
        out = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


# --------------------------------------------------------------------------- #
# fire-time readers (BLOCKING — the fire hops them off the loop)
# --------------------------------------------------------------------------- #


def _when(prev: Any) -> str:
    stamp = getattr(prev, "finished_at", None) or getattr(prev, "created_at", None)
    if isinstance(stamp, datetime):
        return f"{stamp:%Y-%m-%d %H:%M}"
    return "earlier"


def earlier_result(
    scheduler: Any,
    orchestrator: Any,
    other: str,
    *,
    event_bus: Any = None,
    session_id: str | None = None,
) -> tuple[str, str]:
    """``(prompt_block, note)`` for ``context_from``: the other schedule's last
    session summary, capped to :data:`EARLIER_RESULT_CAP` and scanned under
    the source ``earlier result from <name>``. When the schedule, its session
    or its summary is missing the block is the one-line note and ``note``
    carries the same sentence for ``last_detail``. BLOCKING (two DB reads)."""
    from ..core.promptguard import guard

    missing = f"no earlier result from {other} yet"
    rec = scheduler.get(other) if scheduler is not None else None
    sid = str(getattr(rec, "last_session_id", "") or "") if rec is not None else ""
    prev = orchestrator.get_session(sid) if (sid and orchestrator is not None) else None
    summary = str(getattr(prev, "summary", "") or "").strip() if prev is not None else ""
    if not summary:
        return f"({missing})", missing
    text = guard(
        summary,
        source=f"earlier result from {other}",
        event_bus=event_bus,
        session_id=session_id,
        cap=EARLIER_RESULT_CAP,
    ).strip()
    return f"Earlier result from {other} ({_when(prev)}):\n{text}", ""


def run_pre_run_script(
    script: Script,
    *,
    workspace: str | Path,
    home: str | Path,
    config: Any,
    trust: str = "",
    event_bus: Any = None,
    session_id: str | None = None,
) -> tuple[str, str]:
    """Run the pre-run script through the shell tool's OWN confinement and
    return ``(prompt_block, note)``. BLOCKING (a Docker probe + a process).

    Lock-step with ``sandbox/shell_tool.SandboxedShellTool.execute``: the
    policy comes from ``config.sandbox``, Docker is preferred under an
    isolating policy or when the run is low trust, and low trust with no
    Docker runs NOTHING (``LOW_TRUST_SHELL_REFUSAL``). Output is head+tail
    capped and scanned (source ``pre-run script``); a timeout or a non-zero
    exit is a note under the block, never a failed fire."""
    from ..core.promptguard import guard
    from ..core.trust import LOW_TRUST_SHELL_REFUSAL, TRUST_LOW, normalize_trust
    from ..sandbox.manager import SandboxManager
    from ..sandbox.native import NativeSandbox
    from ..sandbox.policy import SandboxPolicy
    from ..sandbox.shell_tool import _is_isolating

    policy = SandboxPolicy.from_config(getattr(config, "sandbox", {}) or {})
    prefer = getattr(config, "sandbox_runtime", "native") or "native"
    must_isolate = normalize_trust(trust) == TRUST_LOW
    if (_is_isolating(policy) or must_isolate) and prefer != "docker":
        prefer = "docker"
    sandbox = SandboxManager(policy, prefer=prefer).get()
    if must_isolate and isinstance(sandbox, NativeSandbox):
        note = f"pre-run script refused: {LOW_TRUST_SHELL_REFUSAL}"
        return f"Pre-run data:\n({note})", note
    cwd = Path(workspace) if script.cwd == "workspace" else Path(home)
    try:
        result = sandbox.run(script.command, cwd=cwd, timeout=float(script.timeout_s))
    except Exception as exc:  # noqa: BLE001 — a script that cannot start is a note
        note = f"pre-run script could not run: {type(exc).__name__}: {exc}"
        return f"Pre-run data:\n({note})", note
    raw = (result.combined or "").strip()
    text = guard(
        raw,
        source="pre-run script",
        event_bus=event_bus,
        session_id=session_id,
        cap=SCRIPT_OUTPUT_CAP,
    )
    block = "Pre-run data:\n```\n" + text + "\n```"
    note = ""
    if getattr(result, "timed_out", False):
        note = f"the pre-run script timed out after {script.timeout_s}s"
    elif int(getattr(result, "returncode", 0) or 0) != 0:
        note = f"the pre-run script exited {int(result.returncode)}"
    if note:
        block += f"\n({note})"
    return block, note


def folder_rules_block(
    workspace: str | Path, *, event_bus: Any = None, session_id: str | None = None
) -> str:
    """The "# Folder rules (<file>)" block for a working folder, or "".

    Looks for :data:`FOLDER_RULES_FILES` in that order and takes the FIRST
    found; the text rides through ``promptguard.guard`` (source ``folder
    rules <file>``, cap :data:`FOLDER_RULES_CAP`) so an injected line becomes
    the placeholder and a ``context.blocked`` lands. BLOCKING (a file read):
    the runtime hops it off the loop, and ONLY for a session whose options
    carry ``folder_rules`` — a folder nobody asked about is never read."""
    from ..core.promptguard import guard

    root = Path(str(workspace or "")) if workspace else None
    if root is None:
        return ""
    for fname in FOLDER_RULES_FILES:
        path = root / fname
        try:
            if not path.is_file():
                continue
            raw = path.read_text(encoding="utf-8", errors="replace")[:_RULES_READ_CHARS]
        except OSError:
            log.debug("folder rules %s unreadable", path, exc_info=True)
            continue
        text = guard(
            raw,
            source=f"folder rules {fname}",
            event_bus=event_bus,
            session_id=session_id,
            cap=FOLDER_RULES_CAP,
        ).strip()
        if not text:
            return ""
        return f"# Folder rules ({fname})\n{text}"
    return ""


def set_session_task(engine: Any, session_id: str, task: str) -> None:
    """Rewrite a session's ``task`` (the fire appends the knobs' blocks AFTER
    the row exists, because the pre-run script needs the row's workspace as
    its cwd). BLOCKING (one SQLite write)."""
    from ..core.db import session_scope
    from ..core.models import Session

    with session_scope(engine) as db:
        row = db.get(Session, session_id)
        if row is None:
            return
        row.task = task
        db.add(row)
        db.commit()


__all__ = [
    "EARLIER_RESULT_CAP",
    "FOLDER_RULES_CAP",
    "FOLDER_RULES_FILES",
    "KNOB_KEYS",
    "KnobError",
    "SCRIPT_FROM_AGENT_REFUSAL",
    "SCRIPT_OUTPUT_CAP",
    "SCRIPT_TIMEOUT_MAX",
    "Script",
    "WORKSPACE_ROOT_DETAIL",
    "decode_knobs",
    "decode_script",
    "earlier_result",
    "folder_rules_block",
    "has_knobs",
    "merge_payload",
    "run_pre_run_script",
    "session_options",
    "set_session_task",
    "validate_knobs",
]
