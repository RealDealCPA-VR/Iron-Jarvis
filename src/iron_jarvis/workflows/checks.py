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

    Idea 09 (09-a): the walk itself is ``validate.problems``, which goes into
    the "If" and "For each" blocks (a step inside one is checked as one
    outside is, and the block for what only a block can get wrong). This is
    still the ONE function the doors call; the first sentence is the answer.
    For a workflow without blocks the words and their order are as before:
    the references first (``{{Ref.data}}`` naming no step, v1.231.0 AE10),
    then the kind, then whether the step can run at all (v1.225.0: a task, a
    tool, a tool this install has (v1.449.0), a message), then
    ``on_failure``.
    """
    from .validate import problems

    found = problems(steps, platform)
    return found[0] if found else None


def _step_texts(step: Any) -> list[str]:
    """The text of *step* that is filled in from other steps' values, by the
    step's kind: exactly what its runner renders, nothing else (a tool step's
    leftover ``task`` is never read, so a reference in it stops nothing)."""
    kind = str(getattr(step, "kind", "") or "agent")
    if kind == "if":
        # Idea 09: an If reads its condition's two sides.
        from .logic import condition_texts

        return condition_texts(getattr(step, "condition", None))
    if kind == "for_each":
        # Idea 09: a For each reads its list, or its folder and pattern.
        over = getattr(step, "over", None)
        over = over if isinstance(over, dict) else {}
        texts = [over["from"]] if isinstance(over.get("from"), str) else []
        files = over.get("files") if isinstance(over.get("files"), dict) else {}
        texts.extend(files[k] for k in ("folder", "glob") if isinstance(files.get(k), str))
        return texts
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
    from .engine import _TEMPLATE_RX, MISSING, UnreadableData, field_ref, read_field

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
            # Idea 09: ``{{Step.data.field}}`` / ``{{item.field}}`` — a field
            # that is not in the step's data is a value nothing gave. (A
            # failed step's fields stay "" as its ``.data`` does, and data
            # that cannot be read is said by :func:`unreadable_values`.)
            field = field_ref(ref, outs)
            if field is not None:
                base_out = field[1]
                if base_out.get("status") == "failed":
                    continue
                try:
                    if read_field(base_out, field[2]) is not MISSING:
                        continue
                except UnreadableData:
                    continue
            found.setdefault(ref, None)
    return list(found)


def no_value_words(names: "list[str]") -> str:
    """What a step that could not run for want of a value says."""
    return "could not run: no value for " + ", ".join(names)


def unreadable_values(step: Any, outputs: "dict[str, Any] | None") -> list[str]:
    """The ``{{Step.data.field}}`` names in *step* whose step's stored JSON
    cannot be read (idea 09): it was clipped to the record's bound, so no
    field can be read from it. In the order written, each once."""
    from .engine import _TEMPLATE_RX, UnreadableData, field_ref, read_field

    outs = outputs if isinstance(outputs, dict) else {}
    found: dict[str, None] = {}
    for text in _step_texts(step):
        for m in _TEMPLATE_RX.finditer(text or ""):
            ref = m.group(1).strip()
            if isinstance(outs.get(ref), dict):
                continue
            field = field_ref(ref, outs)
            if field is None or field[1].get("status") == "failed":
                continue
            try:
                read_field(field[1], field[2])
            except UnreadableData:
                found.setdefault(ref, None)
    return list(found)


def unreadable_words(names: "list[str]") -> str:
    """What a step says when a field it reads is in data too large to read."""
    return (
        "could not run: the data was too large to read a field from ("
        + ", ".join(names)
        + "). Pass a smaller result, or read the whole value without a field"
    )
