"""Every problem with a workflow's steps, in plain words (idea 09, 09-a).

``checks.step_problem`` (the ONE function every door that saves calls:
``POST /workflows``, ``workflow_create``, ``workflow_update`` and
``POST /workflows/generate``) asks :func:`problems` and refuses with the first
sentence, so the four doors still refuse in the same words. The walk goes
into the "If" and "For each" blocks: a step inside one is checked exactly as
a step outside one is, and the block itself is checked for what only a block
can get wrong (no condition, nothing to work through, an empty block, too
deep, a limit out of range).

Each sentence starts with where the step is, ``steps[i] (label): …`` for a
step of the workflow itself and ``steps[i].steps[j] (label): …`` (or
``.else_steps[j]``) for one inside a block: the editor reads the first index
to open the step (``dashboard/lib/workflowStepProblems.ts``).

:func:`problems` with ``structure_only=True`` is what the engine asks before
it starts a run (``WorkflowEngine.create_record``): only what would make a
block run wrongly, so a run of an older workflow is never refused for
anything it was not refused for before.
"""

from __future__ import annotations

from typing import Any

#: The two blocks (idea 09). "Wait until" (09-c) is not in this version.
BLOCK_KINDS: tuple[str, ...] = ("if", "for_each")
#: How deep blocks may sit inside one another.
MAX_NESTING = 3
#: D-09.1: how many items one For each works through before it stops.
DEFAULT_MAX_ITEMS = 200
#: The most a For each may be set to (the same as a run's step limit).
MAX_ITEMS_CAP = 2000
#: Light on the PC (north star 7): at most this many items at once.
MAX_PARALLEL = 4
#: Names a For each gives the steps inside it.
ITEM_NAMES: tuple[str, ...] = ("item", "index")


#: What a model is told about the two blocks: ONE text, used by every door
#: that lets a model write a workflow (chat's ``workflow_draft``, the
#: ``workflow_create`` tool, ``POST /workflows/generate``), so they cannot
#: drift apart.
BLOCKS_FOR_MODELS = (
    "Two kinds hold steps of their own. if: runs `steps` when `condition` "
    "{left, op, right} holds, else `else_steps` (op: is, is_not, contains, "
    "over, under, at_least, at_most, empty, not_empty, matches; \"5,000.00\" "
    "reads as a number). for_each: runs `steps` once for each item of `over`, "
    "either {files: {folder, glob}} or {from: \"{{List.data}}\"} (a JSON list "
    "an earlier step gave); inside it {{item}}, {{item.name}} and {{index}} are "
    "the item. {{Step.data.total}} reads one field of a step's data. Example: "
    "{name: \"Each PDF\", kind: \"for_each\", over: {files: {folder: \"C:/Intake\", "
    "glob: \"*.pdf\"}}, steps: [{name: \"Big?\", kind: \"if\", condition: {left: "
    "\"{{Read.data.total}}\", op: \"over\", right: \"5000\"}, steps: [...], "
    "else_steps: [...]}]}."
)


class BlockRefused(ValueError):
    """A run refused before it started because a block would run wrongly
    (``WorkflowEngine.create_record``). Its text is the sentence the route
    answers with."""


def _ref_base(ref: str) -> str:
    """The step a ``{{ref}}`` names: ``Read`` for ``Read``, ``Read.data`` and
    ``Read.data.total``; ``item`` for ``item.name``."""
    if ".data." in ref:
        return ref.split(".data.", 1)[0].strip()
    if ref.endswith(".data"):
        return ref[: -len(".data")].strip()
    head = ref.split(".", 1)[0].strip()
    if head in ITEM_NAMES:
        return head
    return ref.strip()


def _texts(raw: dict) -> list[str]:
    """Every text of one step that is filled in from other values (what the
    engine renders for that kind, and a block's condition and list)."""
    texts = [str(raw.get("task") or ""), str(raw.get("message") or "")]
    args = raw.get("args")
    for v in (args.values() if isinstance(args, dict) else ()):
        if isinstance(v, str):
            texts.append(v)
        elif isinstance(v, list):
            texts.extend(x for x in v if isinstance(x, str))
    kind = str(raw.get("kind") or "").strip().lower()
    if kind == "if":
        from .logic import condition_texts

        texts.extend(condition_texts(raw.get("condition")))
    if kind == "for_each":
        over = raw.get("over") if isinstance(raw.get("over"), dict) else {}
        if isinstance(over.get("from"), str):
            texts.append(over["from"])
        files = over.get("files") if isinstance(over.get("files"), dict) else {}
        for key in ("folder", "glob"):
            if isinstance(files.get(key), str):
                texts.append(files[key])
    return texts


def _inner(raw: dict) -> list[tuple[str, list]]:
    """The step lists inside a block, with the key each is kept under."""
    out = []
    for key in ("steps", "else_steps"):
        val = raw.get(key)
        if isinstance(val, list):
            out.append((key, val))
    return out


def _names_in(steps: Any) -> set[str]:
    """Every step name in *steps*, inside blocks too."""
    names: set[str] = set()
    for raw in steps or []:
        if not isinstance(raw, dict):
            continue
        nm = str(raw.get("name") or "").strip()
        if nm:
            names.add(nm)
        for _key, inner in _inner(raw):
            names |= _names_in(inner)
    return names


def _has_ask(steps: Any) -> bool:
    for raw in steps or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("kind") or "").strip().lower() == "ask":
            return True
        if any(_has_ask(inner) for _k, inner in _inner(raw)):
            return True
    return False


def _whole(value: Any) -> int | None:
    """*value* as a whole number, or None (``5`` and ``"5"``, never ``5.5``
    or ``True``)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _inner_of(steps: Any, out: "dict[str, str] | None" = None) -> dict[str, str]:
    """Each step name inside a block -> the block it sits in (the nearest)."""
    out = {} if out is None else out
    for raw in steps or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        for _key, inner in _inner(raw):
            for s in inner:
                if isinstance(s, dict) and str(s.get("name") or "").strip():
                    out.setdefault(str(s.get("name")).strip(), name or "a block")
            _inner_of(inner, out)
    return out


def _reference_problem(
    raw: dict, *, every: set[str], later: set[str], known: set[str], in_each: bool, rx: Any,
    own: str = "", inner_of: "dict[str, str] | None" = None, here_block: str = "",
) -> str | None:
    """The first ``{{reference}}`` of *raw* that can never be filled in, in
    words, or None.

    REFERENCES (v1.231.0, AE10): ``{{Ref.data}}`` (and, idea 09,
    ``{{Ref.data.field}}``) resolves only a STEP's recorded data — a run
    input or the reflex ``Trigger`` never carries ``data`` — so one whose
    ``Ref`` names no step renders "" on every run. A bare ``{{Name}}``
    naming no step is left alone: it is how a run input is referenced. (At
    its turn a bare name that nothing gave stops the step by name:
    ``checks.missing_values``.) Idea 09 adds two: ``{{item}}`` outside a For
    each, and a step that runs AFTER this one (its value is never there
    when this one runs)."""
    for text in _texts(raw):
        for m in rx.finditer(text):
            ref = m.group(1).strip()
            # A step literally named "X.data" owns its name (the reserved-
            # name rule of ``render_template``).
            base = ref if ref in every else _ref_base(ref)
            if (ref.endswith(".data") or ".data." in ref) and base not in every:
                return (
                    f"{{{{{ref}}}}} refers to a step named {base!r} that this workflow "
                    "does not have — .data resolves only a step's recorded data; check "
                    "the spelling against the step names"
                )
            if base in ITEM_NAMES and not in_each and base not in every:
                return (
                    f"{{{{{ref}}}}} is the item a For each is working on, and this "
                    "step is not inside a For each"
                )
            if base in later and base not in known:
                return (
                    f"{{{{{ref}}}}} is the step “{base}”, which runs after this one; a "
                    "step can use only what an earlier step found"
                )
            if base in every and base not in known and base not in ITEM_NAMES:
                if own and base == own:
                    return (
                        f"{{{{{ref}}}}} is this step itself; a step can use only what an "
                        "earlier step found"
                    )
                block = (inner_of or {}).get(base)
                if block and block == here_block:
                    return (
                        f"{{{{{ref}}}}} is the step “{base}” on the other lane of “{block}”, "
                        "which does not run when this one does"
                    )
                if block:
                    return (
                        f"{{{{{ref}}}}} is the step “{base}” inside “{block}”, which this "
                        f"step cannot see; a step outside it can use {{{{{block}}}}}, what "
                        "the block did"
                    )
    return None


def problems(steps: Any, platform: Any = None, *, structure_only: bool = False) -> list[str]:
    """Every problem with *steps*, one sentence each, in step order (a block's
    own problems before those of the steps inside it). Empty when the steps
    can be saved. *platform* is where the live tool list is read; None skips
    only that one check."""
    if steps is not None and not isinstance(steps, list):
        return ["steps: a workflow's steps are a list, one entry for each step"]
    out: list[str] = []
    every = _names_in(steps)
    inner_of = _inner_of(steps)
    if not structure_only:
        # A step inside a block keeps its output under "<block>/<n>/<step>"
        # (or "<block>/then/<step>"); a step NAMED like that would be taken
        # for it, and never run.
        for block in sorted(set(inner_of.values())):
            clash = sorted(n for n in every if n.startswith(f"{block}/"))
            if clash:
                out.append(
                    f"steps: the step name “{clash[0]}” starts with “{block}/”, which is how "
                    f"the steps inside “{block}” are kept; rename it"
                )
                break
    _walk(
        steps or [], "steps", depth=0, platform=platform, out=out, every=every,
        before=set(), after_outer=set(), in_each=False, parallel_above=False,
        structure_only=structure_only, inner_of=inner_of, here_block="",
    )
    return out


def _walk(
    steps: list, where: str, *, depth: int, platform: Any, out: list[str],
    every: set[str], before: set[str], after_outer: set[str], in_each: bool,
    parallel_above: bool, structure_only: bool,
    inner_of: "dict[str, str] | None" = None, here_block: str = "",
) -> None:
    from .checks import NO_TOOL_FIX, tool_missing
    from .engine import _TEMPLATE_RX, ON_FAILURE, STEP_KINDS
    from .logic import condition_problem

    names = [
        str(raw.get("name") or "").strip() if isinstance(raw, dict) else ""
        for raw in steps
    ]
    seen: set[str] = set()
    for i, raw in enumerate(steps):
        if not isinstance(raw, dict):
            continue  # the loader skips non-dict entries; keep parity
        label = names[i] or f"#{i + 1}"
        here = f"{where}[{i}]"
        kind_raw = raw.get("kind")
        kind = str(kind_raw).strip().lower() if kind_raw else "agent"
        is_block = kind in BLOCK_KINDS
        later = set(n for n in names[i + 1:] if n) | after_outer
        if is_block:
            later |= _names_in([s for _k, lst in _inner(raw) for s in lst])
        known = before | set(n for n in names[:i] if n)

        if not structure_only:
            if depth and names[i] and names[i] in seen:
                out.append(
                    f"{here} ({label}): two steps in the same list are both named "
                    f"“{names[i]}”; give each step its own name"
                )
            problem = _reference_problem(
                raw, every=every, later=later, known=known, in_each=in_each,
                rx=_TEMPLATE_RX, own=names[i], inner_of=inner_of, here_block=here_block,
            )
            if problem:
                out.append(f"{here} ({label}): {problem}")
        if kind_raw and kind not in STEP_KINDS:
            out.append(f"{here}.kind: {kind_raw!r} is not one of " + "|".join(STEP_KINDS))
            seen.add(names[i])
            continue
        if not structure_only:
            # CONTENT (v1.225.0): a step that cannot possibly run is refused
            # at save time, naming the step.
            has_task = bool(str(raw.get("task") or "").strip())
            has_msg = bool(str(raw.get("message") or "").strip())
            if kind == "agent" and not has_task and not names[i]:
                out.append(f"{here} ({label}): an agent step needs a task")
            if kind == "tool" and not str(raw.get("tool") or "").strip():
                out.append(f"{here} ({label}): a tool step needs a tool name")
            elif kind == "tool" and platform is not None:
                tool_name = str(raw.get("tool") or "").strip()
                if tool_missing(platform, tool_name):
                    out.append(f"{here} ({label}): no tool named {tool_name!r} — " + NO_TOOL_FIX)
            if kind in ("ask", "notify") and not has_msg and not has_task:
                out.append(f"{here} ({label}): an {kind} step needs a message")
            failure_raw = raw.get("on_failure")
            if failure_raw:
                on_failure = str(failure_raw).strip().lower()
                if on_failure not in ON_FAILURE:
                    out.append(
                        f"{here}.on_failure: {failure_raw!r} is not "
                        "one of " + "|".join(ON_FAILURE)
                    )
        if names[i]:
            seen.add(names[i])
        if not is_block:
            continue
        _block(
            raw, kind, here, label, depth=depth, platform=platform, out=out,
            every=every, before=known, after_outer=set(n for n in names[i + 1:] if n) | after_outer,
            in_each=in_each, parallel_above=parallel_above, structure_only=structure_only,
            condition_problem=condition_problem, inner_of=inner_of,
        )


def _block(
    raw: dict, kind: str, here: str, label: str, *, depth: int, platform: Any,
    out: list[str], every: set[str], before: set[str], after_outer: set[str],
    in_each: bool, parallel_above: bool, structure_only: bool, condition_problem,
    inner_of: "dict[str, str] | None" = None,
) -> None:
    word = "an If" if kind == "if" else "a For each"
    if depth + 1 > MAX_NESTING:
        out.append(
            f"{here} ({label}): blocks go at most {MAX_NESTING} deep (an If or a For "
            "each inside another); this one is deeper"
        )
        return
    if str(raw.get("group") or "").strip():
        out.append(
            f"{here} ({label}): {word} runs on its own, so it cannot be in a parallel "
            "group; remove its group"
        )
    if str(raw.get("on_failure") or "").strip().lower() == "retry":
        out.append(
            f"{here} ({label}): {word} can stop the run or skip what failed and go on; "
            "to try a step again, set Retry on that step inside it"
        )
    inner = dict(_inner(raw))
    then = inner.get("steps") or []
    other = inner.get("else_steps") or []
    parallel_here = False
    if kind == "if":
        problem = condition_problem(raw.get("condition"))
        if problem:
            out.append(f"{here} ({label}): {problem}")
        if not then and not other:
            out.append(
                f"{here} ({label}): an If needs at least one step, under Then or "
                "under Otherwise"
            )
    else:
        over = raw.get("over")
        over = over if isinstance(over, dict) else {}
        source_from = over.get("from")
        files = over.get("files")
        if source_from is not None and files is not None:
            out.append(
                f"{here} ({label}): a For each works through one list: the files in a "
                "folder, or a list an earlier step found, not both"
            )
        elif isinstance(files, dict):
            if not str(files.get("folder") or "").strip():
                out.append(f"{here} ({label}): a For each over files needs a folder")
        elif isinstance(source_from, str) and source_from.strip():
            pass
        else:
            out.append(
                f"{here} ({label}): a For each needs a list to work through: the files "
                "in a folder, or a list an earlier step found (for example {{List.data}})"
            )
        if not then:
            out.append(f"{here} ({label}): a For each needs at least one step inside it")
        if other:
            out.append(
                f"{here} ({label}): a For each has one list of steps; Otherwise belongs "
                "to an If"
            )
        max_raw = raw.get("max_items")
        if max_raw is not None:
            n = _whole(max_raw)
            if n is None or not 1 <= n <= MAX_ITEMS_CAP:
                out.append(
                    f"{here} ({label}): max_items is how many items it works through, "
                    f"a whole number from 1 to {MAX_ITEMS_CAP:,}"
                )
        par_raw = raw.get("parallel")
        if par_raw is not None:
            n = _whole(par_raw)
            if n is None or not 1 <= n <= MAX_PARALLEL:
                out.append(
                    f"{here} ({label}): parallel is how many items it works on at once, "
                    f"1 to {MAX_PARALLEL}"
                )
            elif n > 1:
                parallel_here = True
                if parallel_above:
                    out.append(
                        f"{here} ({label}): only one For each in a chain may work on "
                        "several items at once; set this one's parallel to 1"
                    )
                if _has_ask(then):
                    out.append(
                        f"{here} ({label}): a For each that asks you something works on "
                        "one item at a time; set its parallel to 1"
                    )
    for key, lst in (("steps", then), ("else_steps", other)):
        if not lst:
            continue
        _walk(
            lst, f"{here}.{key}", depth=depth + 1, platform=platform, out=out,
            every=every, before=before, after_outer=after_outer,
            in_each=in_each or kind == "for_each",
            parallel_above=parallel_above or parallel_here,
            structure_only=structure_only,
            inner_of=inner_of, here_block=label,
        )
