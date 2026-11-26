"""What an agent run used, step by step (the intelligence plan, release 6).

A chat reply's usage row has said what the reply was sent since v1.572.0; an
agent run's said nothing. Three small pieces put the same facts on a run's
row (``AgentRun``), written by the save each step already makes, and on the
step's own event (``llm.completed``):

* :class:`PromptParts` names the parts of the run's prompt WHILE it is built,
  by observation only (it looks at what was appended; it never changes it);
* :func:`note_prompt` keeps those names for the run;
* :func:`note_step` is called once per step, after the model answered: it
  adds the step to the run's columns and returns the step event's extra keys.

Sizes and names only: never the text of any part, a file's content or a
tool's arguments. The part strings are held in memory only until the run's
first step has measured them (``note_step`` lets them go there, and
``settle`` lets go of any a run never measured); they are saved nowhere.
Every size is an ESTIMATE (``context.budget.estimate_tokens``) unless the
provider reported it (the cache counts). Nothing here raises into a run.

``note_step`` estimates a whole step's prompt, so it is BLOCKING work: the
runtime calls it on the worker thread that makes the step's own save
(``AgentRuntime._note_step_and_save``), never on the event loop.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

#: The parts an agent run's prompt is named by (``context.breakdown``'s keys).
_KEYS = ("memory", "project", "rules", "skills", "summary", "attachments")


class PromptParts:
    """Names what is appended to a prompt, part by part. ``claim(key,
    prompt)`` says: everything added since the last claim belongs to *key*.
    Text that was not simply appended (a rewrite) is claimed by nobody and
    counts as instructions. Reads only; never raises."""

    def __init__(self, start: Any = "") -> None:
        self._prev = start if isinstance(start, str) else ""
        self.pieces: dict[str, list[str]] = {}

    def claim(self, key: str, prompt: Any) -> None:
        try:
            if not isinstance(prompt, str):
                return
            if key in _KEYS and len(prompt) > len(self._prev) and prompt.startswith(self._prev):
                self.pieces.setdefault(key, []).append(prompt[len(self._prev):])
            self._prev = prompt
        except Exception:  # noqa: BLE001 — a record never breaks a run
            pass

    def skip(self, prompt: Any) -> None:
        """Everything added since the last claim is the run's instructions."""
        if isinstance(prompt, str):
            self._prev = prompt


def note_prompt(facts: Any, parts: "PromptParts | None", run: Any = None) -> None:
    """The run's prompt is built: keep its named parts for the first step's
    measure (memory only: the strings are saved nowhere), and say whose
    facts these are (:func:`settle` writes them on that run only)."""
    try:
        if facts is None:
            return
        if run is not None:
            facts.run_id = getattr(run, "id", None)
        if parts is not None:
            facts.pieces = {k: list(v) for k, v in parts.pieces.items()}
    except Exception:  # noqa: BLE001
        pass


def settle(run: Any, facts: Any) -> None:
    """The run is ending: its row takes the model's WHOLE time (a split
    task's planning, checking and write-up calls are timed by the router
    too, after the last step set the columns) and the first word. Only for
    the run these facts belong to, and only what is known. Never raises."""
    try:
        if facts is None or run is None or getattr(facts, "run_id", None) != getattr(run, "id", object()):
            return
        fields = facts.fields()
        if "model_ms" in fields:
            run.model_ms = fields["model_ms"]
            run.model_calls = fields["model_calls"]
        if "first_word_ms" in fields:
            run.first_word_ms = fields["first_word_ms"]
        record = _record_json(facts)
        if record is not None:
            run.parts_json = record
        # A run that ended before a step measured its prompt (a split task
        # that never reached its loop) lets its part strings go here.
        facts.pieces = None
    except Exception:  # noqa: BLE001 — a record never breaks a run
        pass


def _tok(usage: Mapping, key: str) -> int:
    try:
        return max(0, int(usage.get(key) or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _record_json(facts: Any) -> "str | None":
    """The run's record as its row keeps it (``AgentRun.parts_json``): the
    parts as the run began, where its tools came from, the estimates summed
    over its steps, and the input of the calls that reported a cache figure.
    None when nothing was measured."""
    from ..context.breakdown import RECORD_VERSION

    state = getattr(facts, "run_state", None) or {}
    record: dict[str, Any] = dict(getattr(facts, "parts", None) or {})
    if state.get("sent", {}).get("steps"):
        record["sent"] = dict(state["sent"])
    if state.get("origins"):
        record["tools"] = state["origins"]
    if not record:
        return None
    if int(getattr(facts, "cache_input", 0) or 0) > 0:
        record["cache_input"] = int(facts.cache_input)
    record.setdefault("v", RECORD_VERSION)
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def note_call(run: Any, usage: Any, facts: Any = None) -> "dict[str, int]":
    """ONE MODEL CALL of a run answered: the provider's own cache counts
    (parts of ``input_tokens``) are added to the run's columns, ONLY when the
    provider reported one (the columns stay None otherwise, never a made-up
    0). Called wherever a run adds a call's ``input_tokens``: each step of
    its loop (:func:`note_step`), a split task's planning, checking and
    write-up calls (``decompose._complete``) and the quick check, so the
    share reused can be read against ``input_tokens``. Returns the two
    counts for the call's event (``{}`` when none was reported). Never
    raises."""
    try:
        if run is None or not isinstance(usage, Mapping):
            return {}
        if "cache_read_input_tokens" not in usage and "cache_creation_input_tokens" not in usage:
            return {}
        read, wrote = _tok(usage, "cache_read_input_tokens"), _tok(usage, "cache_creation_input_tokens")
        run.cache_read_input_tokens = int(getattr(run, "cache_read_input_tokens", None) or 0) + read
        run.cache_creation_input_tokens = int(getattr(run, "cache_creation_input_tokens", None) or 0) + wrote
        # The input these counts are parts of (only some of a run's calls
        # may report one): kept on the run's own facts, saved in its record.
        if facts is None:
            from ..eval import reply_facts

            facts = reply_facts.current()
            if facts is not None and getattr(facts, "run_id", None) != getattr(run, "id", object()):
                facts = None
        if facts is not None:
            facts.cache_input = int(getattr(facts, "cache_input", 0) or 0) + _tok(usage, "input_tokens")
        return {"cache_read_input_tokens": read, "cache_creation_input_tokens": wrote}
    except Exception:  # noqa: BLE001 — a record never breaks a run
        return {}


def _tools_key(specs: "list[Any]") -> "tuple[Any, ...]":
    """What tells one step's tool list from another's: how many, and their
    NAMES in order. Never ``id()`` of the list: a list changed in place keeps
    its id, and a freed list's id is handed to the next one."""
    names = []
    for spec in specs:
        fn = spec.get("function") if isinstance(spec, Mapping) else None
        names.append(str((spec.get("name") if isinstance(spec, Mapping) else "")
                         or (fn.get("name") if isinstance(fn, Mapping) else "") or ""))
    return (len(specs), tuple(names))


def note_step(
    run: Any, facts: Any, *, usage: Any, tool_specs: Any, system: Any, messages: Any,
    window: Any = None, ratio: "float | None" = None, model_ms_before: float = 0.0,
) -> "dict[str, Any]":
    """ONE STEP of an agent run answered. BLOCKING (it estimates the whole
    step's prompt): called on the worker thread that makes the step's own
    save, never on the event loop. Adds the step to the run's columns (saved
    by that same save, so nothing extra is written or read) and
    returns the extra keys for the step's ``llm.completed`` event:

    * ``cache_read_input_tokens`` / ``cache_creation_input_tokens``: only when
      the provider reported one (the run's columns stay None otherwise);
    * ``tools`` (how many definitions the step sent) and ``tool_spec_tokens``
      (their estimated size); the run keeps the NAMES and the summed size;
    * ``sent``: the estimated size of the step's instructions and messages;
    * ``model_ms``: how long the model took for this step, when it was timed.

    The run's ``parts_json`` is the size of each part when the run BEGAN (its
    first step), plus ``sent``: the estimates summed over its steps. Never
    raises; ``{}`` on any fault."""
    out: dict[str, Any] = {}
    if facts is None or run is None or not getattr(facts, "for_run", False):
        return out  # no run's own facts are open (never a chat reply's)
    try:
        from ..context.breakdown import _message_tokens, sent_record, tool_origins
        from ..context.budget import estimate_tokens
        from ..eval.pricing import UsageTally

        usage = usage if isinstance(usage, Mapping) else {}
        state = getattr(facts, "run_state", None)
        if state is None:
            state = facts.run_state = {"tally": UsageTally(), "sent": {"system": 0, "messages": 0, "tools": 0, "steps": 0},
                                       "tools_key": None, "tools_tokens": 0, "origins": None}
        tally: UsageTally = state["tally"]

        # The provider's own cache counts (parts of ``input_tokens``).
        out.update(note_call(run, usage, facts))

        # The tool definitions this step sent: measured again only when the
        # list changed (it rarely does within a run), told by the tools'
        # names and how many there are.
        specs = list(tool_specs or ())
        key = _tools_key(specs)
        if state["tools_key"] != key:
            state["tools_key"] = key
            blob = json.dumps(specs, ensure_ascii=False, sort_keys=True, default=str) if specs else ""
            state["tools_tokens"] = estimate_tokens(blob, ratio) if blob else 0
            try:
                state["origins"] = tool_origins(specs, ratio)
            except Exception:  # noqa: BLE001 — not measured: absent
                state["origins"] = None
        tally.note_tools(specs, state["tools_tokens"], state["origins"])
        run.tools_sent_json = json.dumps(list(tally.tools_sent), ensure_ascii=False)
        run.tool_spec_tokens = int(tally.tool_spec_tokens)
        out["tools"] = len(specs)
        out["tool_spec_tokens"] = int(state["tools_tokens"])

        # What this step sent, by size (estimates): the instructions and the
        # messages counted with the SAME ratio (the model's measured one
        # where there is one), so the two can be added and compared.
        sys_tokens = estimate_tokens(system or "", ratio) if isinstance(system, str) else 0
        msg_tokens = _message_tokens(messages or (), ratio)
        out["sent"] = {"system": sys_tokens, "messages": msg_tokens}
        summed = state["sent"]
        summed["system"] += sys_tokens
        summed["messages"] += msg_tokens
        summed["tools"] += int(state["tools_tokens"])
        summed["steps"] += 1

        # The parts, once: the run as it BEGAN.
        if getattr(facts, "parts", None) is None and not state.get("parts_tried"):
            state["parts_tried"] = True
            try:
                facts.note_parts(sent_record(
                    system=system if isinstance(system, str) else "", messages=messages or (),
                    window=int(window or 0), window_known=bool(window),
                    pieces=getattr(facts, "pieces", None) or {},
                ))
            except Exception:  # noqa: BLE001 — not measured: absent
                pass
            finally:
                # Measured (or not measurable): the part STRINGS are let go.
                # Only sizes stay on the facts for the rest of the run.
                facts.pieces = None
        run.parts_json = _record_json(facts)

        # The model's time (the router's seam added this step's call).
        fields = facts.fields()
        if "model_ms" in fields:
            run.model_ms = fields["model_ms"]
            run.model_calls = fields["model_calls"]
            step_ms = int(round(float(facts.model_ms) - float(model_ms_before or 0.0)))
            if step_ms >= 0:
                out["model_ms"] = step_ms
        if "first_word_ms" in fields:
            run.first_word_ms = fields["first_word_ms"]
    except Exception:  # noqa: BLE001 — a record never breaks a run
        return {}
    return out


__all__ = ["PromptParts", "note_call", "note_prompt", "note_step", "settle"]
