"""The ONE check of a workflow's steps before it is saved, and the words for a
step that refers to a value nothing gave (the intelligence plan, release 3).

Before this module the Workflows page checked a workflow's steps
(``daemon/routes/workflows._validate_step_shapes``) and the chat tool that
saves one (``workflows/tools.WorkflowCreateTool``) saved whatever it was
handed: a workflow made in chat that could not work was found out at its
turn, mid-run, weeks later. Both doors now ask :func:`step_problem` and
refuse with the SAME sentence.

The sentence keeps the page's form, ``steps[i] (label): …``: the editor
reads the index out of it to mark the step
(``dashboard/lib/workflowStepProblems.ts``).
"""

from __future__ import annotations

from typing import Any

#: The way out a "no tool named X" refusal names (v1.449.0, Wave D item 42).
#: The editor's tool box lists this install's tools; Settings › Tools adds one.
NO_TOOL_FIX = "pick one of this install's tools in the step, or add the tool in Settings › Tools first"


def tool_missing(platform: Any, tool_name: str) -> bool:
    """True when the live registry has NO tool of that name. An unreadable
    registry answers False (nothing is refused on a guess)."""
    if not tool_name:
        return False
    try:
        names = set(platform.registry.names())
    except Exception:  # noqa: BLE001 — the fire would fail by itself; the save is not the place to guess
        return False
    if not names or tool_name in names:
        return False
    # AN review (v1.449.0): an extension's tool (``mcp__<pack>__<tool>``)
    # is in the registry only once its pack has STARTED — at boot packs
    # load in the background, and a failing or switched-off pack lists
    # none. Refuse it only when its pack is loaded and running and still
    # has no such tool; anything less is not known yet.
    if tool_name.startswith("mcp__"):
        try:
            from ..mcp.tools import load_statuses

            for server, rec in load_statuses().items():
                if tool_name.startswith(f"mcp__{server}__"):
                    return str((rec or {}).get("state") or "") == "ok" and not (
                        (rec or {}).get("last_error")
                    )
        except Exception:  # noqa: BLE001 — unsure = refuse nothing
            return False
        return False
    return True


def step_problem(steps: Any, platform: Any = None) -> "str | None":
    """Why this list of steps may not be saved, in one sentence, or None.

    SAVE-time strictness (v1.170.0): a step whose ``kind``/``on_failure``
    the loader would SILENTLY rewrite to the default is refused naming the
    field — a def that says ``kind: "tools"`` and runs as an agent step is
    a misconfiguration discovered mid-run, weeks later. Loading stays
    lenient on purpose (old rows keep working); only the save gate is
    strict. Absent/empty values still mean "the default", exactly as the
    loader treats them.

    *platform* is where the live tool list is read (:func:`tool_missing`);
    None skips only that one check.
    """
    from .engine import _TEMPLATE_RX, ON_FAILURE, STEP_KINDS

    if steps is not None and not isinstance(steps, list):
        return "steps: a workflow's steps are a list, one entry for each step"
    step_names = {
        str(raw.get("name") or "").strip()
        for raw in (steps or [])
        if isinstance(raw, dict)
    }
    for i, raw in enumerate(steps or []):
        if not isinstance(raw, dict):
            continue  # the loader ignores non-dict entries; keep parity
        # REFERENCES (v1.231.0, AE10): ``{{Ref.data}}`` resolves only a
        # STEP's recorded data — a run input or the reflex ``Trigger``
        # never carries ``data`` — so one whose ``Ref`` names no step
        # (``{{Scna.data}}``) renders "" on every run, forever, and the
        # run completes green on it. Refused at save time, naming the
        # step and the reference. A bare ``{{Name}}`` naming no step is
        # left alone: it is how a run input (``POST /workflows/run
        # inputs`` / the ``workflow_run`` tool) is referenced, and a def
        # declares none. (At its turn a bare name that nothing gave stops
        # the step by name: :func:`missing_values`.)
        label = str(raw.get("name") or "").strip() or f"#{i + 1}"
        templated = [str(raw.get("task") or ""), str(raw.get("message") or "")]
        args = raw.get("args")
        for v in (args.values() if isinstance(args, dict) else ()):
            if isinstance(v, str):
                templated.append(v)
            elif isinstance(v, list):
                templated.extend(x for x in v if isinstance(x, str))
        for text in templated:
            for m in _TEMPLATE_RX.finditer(text):
                ref = m.group(1).strip()
                if not ref.endswith(".data") or ref in step_names:
                    continue  # a bare ref, or a step literally named X.data
                base = ref[: -len(".data")].strip()
                if base not in step_names:
                    return (
                        f"steps[{i}] ({label}): {{{{{ref}}}}} refers to "
                        f"a step named {base!r} that this workflow does not "
                        "have — .data resolves only a step's recorded data; "
                        "check the spelling against the step names"
                    )
        kind_raw = raw.get("kind")
        kind = "agent"
        if kind_raw:  # falsy = absent/empty = the default, never a rewrite
            kind = str(kind_raw).strip().lower()
            if kind not in STEP_KINDS:
                return f"steps[{i}].kind: {kind_raw!r} is not one of " + "|".join(STEP_KINDS)
        # CONTENT (v1.225.0): a step that cannot possibly run is refused at
        # save time, naming the step — an agent step with no task ran an
        # agent on an empty instruction, a tool step with no tool failed
        # at its turn mid-run, an ask with no question parked the run on
        # "Continue past “x”?". Each used to be discovered minutes into a
        # run instead of at the moment of saving.
        has_task = bool(str(raw.get("task") or "").strip())
        has_msg = bool(str(raw.get("message") or "").strip())
        if kind == "agent" and not has_task and not str(raw.get("name") or "").strip():
            return f"steps[{i}] ({label}): an agent step needs a task"
        if kind == "tool" and not str(raw.get("tool") or "").strip():
            return f"steps[{i}] ({label}): a tool step needs a tool name"
        # v1.449.0 (Wave D item 42): a tool step naming a tool this install
        # does not have fails at its turn, mid-run — refused at SAVE time
        # by name, so the editor can say it beside the step. The live
        # registry is the one list (``GET /templates`` shows the same
        # ``live_tools``); a registry that cannot be read refuses nothing.
        if kind == "tool" and platform is not None:
            tool_name = str(raw.get("tool") or "").strip()
            if tool_missing(platform, tool_name):
                return f"steps[{i}] ({label}): no tool named {tool_name!r} — " + NO_TOOL_FIX
        if kind in ("ask", "notify") and not has_msg and not has_task:
            return f"steps[{i}] ({label}): an {kind} step needs a message"
        failure_raw = raw.get("on_failure")
        if failure_raw:
            on_failure = str(failure_raw).strip().lower()
            if on_failure not in ON_FAILURE:
                return (
                    f"steps[{i}].on_failure: {failure_raw!r} is not "
                    "one of " + "|".join(ON_FAILURE)
                )
    return None


def _step_texts(step: Any) -> list[str]:
    """The text of *step* that is filled in from other steps' values, by the
    step's kind: exactly what its runner renders, nothing else (a tool step's
    leftover ``task`` is never read, so a reference in it stops nothing)."""
    kind = str(getattr(step, "kind", "") or "agent")
    if kind == "tool":
        texts: list[str] = []
        for v in (getattr(step, "args", None) or {}).values():
            if isinstance(v, str):
                texts.append(v)
            elif isinstance(v, list):
                texts.extend(x for x in v if isinstance(x, str))
        return texts
    if kind in ("ask", "notify"):
        return [str(getattr(step, "message", "") or getattr(step, "task", "") or "")]
    return [str(getattr(step, "task", "") or "")]


def missing_values(step: Any, outputs: "dict[str, Any] | None") -> list[str]:
    """The names *step* refers to that NOTHING gave: no step that has run, no
    input the run was started with, no signal. In the order written, each
    once.

    A name that matches nothing used to be filled in as an empty string
    without a word (``engine.render_template``): an agent step ran on half an
    instruction, a tool step on an empty argument, and the run said it
    finished. The engine now stops such a step before it runs
    (:func:`no_value_words`).

    Deliberately NOT missing, as before: a step that FAILED and was skipped
    (its bare name reads ``[step X failed: …]`` and its ``.data`` is empty,
    v1.231.0), a step that finished with no ``data``, and an input given as
    an empty text. Each of those is a recorded answer, not an absent one.
    """
    from .engine import _TEMPLATE_RX

    outs = outputs if isinstance(outputs, dict) else {}
    found: dict[str, None] = {}
    for text in _step_texts(step):
        for m in _TEMPLATE_RX.finditer(text or ""):
            ref = m.group(1).strip()
            if isinstance(outs.get(ref), dict):
                continue
            if ref.endswith(".data") and isinstance(
                outs.get(ref[: -len(".data")].strip()), dict
            ):
                continue
            found.setdefault(ref, None)
    return list(found)


def no_value_words(names: "list[str]") -> str:
    """What a step that could not run for want of a value says."""
    return "could not run: no value for " + ", ".join(names)
