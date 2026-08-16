"""v1.174.0 P2 — job SIZING and DECOMPOSITION (Contract 4 + the bulk signal).

THE REAL RUN, READ OFF THE USER'S OWN INSTALL. Session ``session_8d66af4dc17b``:
provider ``fleet-custom``, BUILDER, workspace ``C:\\Users\\VR\\Downloads\\Test
Folder``, 18 tool invocations, ``stopped: reached max steps before completion``,
zero files renamed. What it does NOT show, and what the first cut of this file
wrongly claimed:

* the task stored on that session is **487 characters**, not 84 — the user's
  sentence reaches ``POST /projects/{id}/task`` and that route WRAPS it (a
  "working directly inside the project folder" preamble, ``Task:``, a
  Deliverable line, "Work autonomously to completion"). So
  ``is_plausibly_multi_step`` was ALREADY True for it, through the > 200-char
  branch, before the bulk signal existed. The bulk signal changes nothing for
  this run — :func:`test_the_stored_task_was_already_multi_step_before_the_bulk_signal`;
* and it would not have mattered either way: ``fleet-custom`` advertises native
  ``tool_use``, so ``should_decompose`` returns False and the entire decompose
  half of P2 never executes on that box —
  :func:`test_the_measured_run_never_reached_the_decompose_lane`;
* and the surface it was posted from carries no ``max_steps`` at all (that
  route calls ``create_session`` without one), so the budget this file pins
  end-to-end is unreachable from where the user actually stood —
  :func:`test_a_session_created_without_a_budget_runs_on_the_configured_default`.

What P2 therefore genuinely owns, and what is pinned below:
* the step budget end to end — request model → route → ``Session`` column →
  orchestrator (create/rerun/continue) → the plan cap and the mini-loop that
  spends it, 1..200 with a 422 (never a silent clamp);
* ONE resolution of that budget for BOTH lanes (Contract 4), asserted by
  running the flat loop's ``runtime.resolve_max_steps`` and the decomposed
  lane's ``session_step_budget`` over the same table;
* the bulk signal, both directions — correct for the SHORT, chat-length
  phrasing (chat, or a task posted straight to ``POST /sessions``), which is a
  real shape, just not the shape that produced the trace above.

Mutation-minded throughout: every number is asserted as a VALUE, absent-param
behavior is asserted to be byte-identical to pre-v1.174.0, and the planner's
system prompt is checked to NAME the raised cap (a clip alone cannot raise a
plan — a model told "at most 8" never writes 12 steps).

All offline: scripted adapters, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.agents import decompose
from iron_jarvis.agents.decompose import (
    DEFAULT_MAX_AGENT_STEPS,
    MAX_MINI_LOOP_CEILING,
    BULK_MINI_LOOP_STEPS,
    MAX_MINI_LOOP_STEPS,
    MAX_PLAN_STEPS,
    MAX_PLAN_STEPS_CEILING,
    MIN_PLAN_STEPS,
    PlanStep,
    StepResult,
    assemble,
    execute_plan,
    explicit_max_steps,
    is_bulk_task,
    is_plausibly_multi_step,
    mini_loop_budget,
    plan_step_cap,
    plan_task,
    session_step_budget,
    should_decompose,
)
from iron_jarvis.agents.orchestrator import normalize_max_steps
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import (
    SESSION_MAX_STEPS_MAX,
    SESSION_MAX_STEPS_MIN,
    AgentType,
    Session as SessionRow,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import SessionCreate
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse
from iron_jarvis.providers.manager import ProviderManager
from iron_jarvis.providers.router import ModelRouter
from iron_jarvis.core.events import EventBus

from fastapi.testclient import TestClient

#: What the USER typed into the project task strip — the short, one-clause bulk
#: phrasing. This exact string reaches the daemon unwrapped only from chat or a
#: direct POST /sessions.
MEASURED_SENTENCE = (
    "Rename all files in this folder to a name that is more appropriate "
    "given the content in the file."
)

#: A task that is emphatically NOT a collection — one file, one action.
#: v1.177.0 gave a BULK step its own unsized round budget (six rounds cannot
#: claim five worklist items, read them, act on them and report each — the
#: measured job died on exactly that, twice). The assertions below pin the
#: GENERIC budget arithmetic, which is unchanged; they happened to use the bulk
#: sentence as their fixture, which now selects the other branch. They use this
#: instead, and the bulk branch is pinned separately just below them.
NON_BULK_SENTENCE = "Fix the typo in README.md."

#: What the DAEMON actually stored on session_8d66af4dc17b — the wrapped 487
#: characters composed by routes/projects.py for every project task. Copied out
#: of the live row, not reconstructed: the whole point of this constant is that
#: it is the string the system evaluates, and the one an earlier version of this
#: file did not use.
MEASURED_TASK = (
    "You are working directly inside the project folder — it is your "
    "current directory. Read and create files here with plain relative "
    "paths.\n"
    f"Task: {MEASURED_SENTENCE}\n"
    "\n"
    "Deliverable: a clear, complete written answer in your final summary "
    "— the summary IS the deliverable. Don't create files unless the task "
    "itself requires them.\n"
    "Work autonomously to completion — make reasonable choices instead of "
    "asking questions."
)


def _client(tmp_path, **kw):
    return TestClient(create_app(str(tmp_path)), **kw)


def _row(client_app_root, session_id: str) -> SessionRow:
    """Read the persisted row — the response view is not the storage."""
    from iron_jarvis.core.db import make_engine

    engine = make_engine(Path(client_app_root) / ".ironjarvis" / "ironjarvis.db")
    with session_scope(engine) as db:
        row = db.get(SessionRow, session_id)
        assert row is not None
        db.expunge(row)
        return row


# --------------------------------------------------------------------------- #
# (1) The measured failure: what actually gated it, and what the bulk signal
#     really covers. Getting this wrong once is why the section is this long.
# --------------------------------------------------------------------------- #
def test_the_stored_task_is_the_wrapped_one_not_the_users_sentence():
    """The string the system evaluates is the one the ROUTE composed."""
    assert len(MEASURED_TASK) == 487
    assert MEASURED_SENTENCE in MEASURED_TASK
    assert MEASURED_TASK.startswith("You are working directly inside the project")
    assert MEASURED_TASK.rstrip().endswith("instead of asking questions.")


def test_the_stored_task_was_already_multi_step_before_the_bulk_signal(monkeypatch):
    """THE HEADLINE CORRECTION. The measured run's task trips the > 200-char
    branch on its own, so the bulk signal is NOT what would have engaged it —
    proved by switching the bulk signal off entirely and getting the same
    answer. A test that asserted the opposite was measuring a string the daemon
    never stores."""
    assert len(MEASURED_TASK) > decompose.MULTI_STEP_TASK_CHARS
    monkeypatch.setattr(decompose, "is_bulk_task", lambda task: False)
    assert is_plausibly_multi_step(MEASURED_TASK) is True


def test_the_measured_run_now_reaches_the_decompose_lane(platform):
    """The coordinator's v1.174.0 answer to the question P2 raised: a BULK job
    decomposes on EVERY provider, native tool-use included.

    P2 correctly refused to widen the prompted-mode gate on its own and pinned
    the limitation instead — this test is that pin, inverted, because the
    limitation was the whole reason the measured run died. A flat 12-step loop
    cannot rename 26 files however good the model's tool calling is; a bulk job
    is exactly the shape decomposition exists for (one plan, a fresh mini-budget
    per step, verification between them)."""
    platform.providers.register("fleet-custom", lambda model=None: _Native([], "fleet-custom"))
    session = _session_stub(task=MEASURED_TASK, provider="fleet-custom")
    assert is_plausibly_multi_step(session.task) is True
    assert is_bulk_task(session.task) is True
    assert platform.config.decompose_local_tasks is True
    assert should_decompose(platform, session) is True, (
        "the measured trace must now reach plan -> execute -> verify"
    )


def test_a_non_bulk_task_on_a_native_provider_still_runs_flat(platform):
    """The widening is BULK-ONLY: an ordinary multi-step ask on a native
    tool-use provider keeps the flat loop, byte for byte."""
    platform.providers.register("fleet-custom", lambda model=None: _Native([], "fleet-custom"))
    wordy = (
        "Write me a short note about the engagement letter and then tell me "
        "what you think of the wording, in your own voice, at length. " * 3
    )
    session = _session_stub(task=wordy, provider="fleet-custom")
    assert is_plausibly_multi_step(session.task) is True
    assert is_bulk_task(session.task) is False
    assert should_decompose(platform, session) is False


def test_the_bulk_signal_covers_the_unwrapped_sentence():
    """What the signal IS for: the same job typed into chat or posted straight
    to POST /sessions arrives unwrapped — short, one imperative clause — and
    both older heuristics miss it."""
    assert len(MEASURED_SENTENCE) < decompose.MULTI_STEP_TASK_CHARS
    clauses = [
        seg
        for seg in decompose._CLAUSE_SPLIT_RE.split(MEASURED_SENTENCE.lower())
        if seg.strip().split(" ", 1)[0] in decompose._IMPERATIVE_VERBS
    ]
    assert len(clauses) < 2, "the old 2-imperative heuristic must still miss it"
    assert is_bulk_task(MEASURED_SENTENCE) is True
    assert is_plausibly_multi_step(MEASURED_SENTENCE) is True


@pytest.mark.parametrize(
    "task",
    [
        MEASURED_SENTENCE,
        "Rename all files in this folder",
        "Summarize every document",
        "Convert every PDF in the directory to text",
        "Summarize each of the receipts",
        "Rename the files in this folder",  # folder + PLURAL noun
        "organize this folder",  # folder + folder-level verb
        "process the folder",
        "tidy up this directory",
        "Extract text from all 22 PDFs",  # a number between is not a stopper
        "Move each file into a year folder",
        "READ ALL OF THE DOCUMENTS",  # case-insensitive
    ],
)
def test_bulk_positives(task):
    assert is_bulk_task(task) is True, task


@pytest.mark.parametrize(
    "task",
    [
        "",
        "   ",
        "Say hello",
        "Summarize notes.txt",
        "Write a file listing milk and eggs",
        "rename this file to foo.txt",  # one file is not a collection
        "read the file in this folder",  # folder ref, singular, non-folder verb
        "look at the files in this folder",  # no action verb we act on
        "delete all of it",  # bare quantifier, no collection noun
        "give it all you have got",
        "check every box on the form",  # "box" is not a collection noun
        "write a summary of the meeting",
        "all files",  # a collection with NO verb is not a job
    ],
)
def test_bulk_negatives(task):
    assert is_bulk_task(task) is False, task


def test_bulk_signal_does_not_disturb_the_two_older_heuristics():
    """The pre-v1.174.0 answers, unchanged — the new branch is additive."""
    assert is_plausibly_multi_step("x" * 201) is True
    assert is_plausibly_multi_step("Read notes.txt then write a summary to out.md") is True
    assert is_plausibly_multi_step("Create a report and email it to Bob") is True
    assert is_plausibly_multi_step("Say hello") is False
    assert is_plausibly_multi_step("Summarize notes.txt") is False
    assert is_plausibly_multi_step("Write a file listing milk and eggs") is False
    assert is_plausibly_multi_step("") is False


def test_bulk_signal_is_pure_and_cheap():
    """No model, no filesystem, no mutation of its input — the same contract
    the other two heuristics carry (they run on EVERY session create)."""
    task = MEASURED_SENTENCE
    assert is_bulk_task(task) is is_bulk_task(task)
    assert task == MEASURED_SENTENCE


# --------------------------------------------------------------------------- #
# (2) should_decompose: the new signal fires through the SAME gates.
# --------------------------------------------------------------------------- #
class _TextOnly(LLMAdapter):
    """Text-only adapter → the router wraps it in the prompted scaffold."""

    def __init__(self, replies=(), provider="local-x", model="llama3"):
        self.provider, self.model = provider, model
        self._replies = list(replies)
        self.calls: list[tuple] = []

    def capabilities(self):
        return {
            "provider": self.provider,
            "model": self.model,
            "tool_use": False,
            "vision": False,
        }

    async def complete(self, *, system, messages, tools):
        self.calls.append((system, list(messages), list(tools)))
        return LLMResponse(
            text=self._replies.pop(0), usage={"input_tokens": 1, "output_tokens": 1}
        )


class _Native(_TextOnly):
    def capabilities(self):
        caps = super().capabilities()
        caps["tool_use"] = True
        return caps


def _session_stub(task=MEASURED_SENTENCE, provider="local-x", max_steps=None):
    return SimpleNamespace(
        id="sess-test", task=task, provider=provider, model=None, max_steps=max_steps
    )


def test_should_decompose_engages_on_a_bulk_task_through_the_existing_gates(platform):
    platform.providers.register("local-x", lambda model=None: _TextOnly())
    platform.providers.register("native-x", lambda model=None: _Native([], "native-x"))
    # Prompted (short-horizon) adapter + a bulk task → engaged.
    assert should_decompose(platform, _session_stub()) is True
    # Natively tool-capable + a BULK task → ALSO engaged (v1.174.0
    # coordinator decision). This is the deliberate widening: a flat loop
    # cannot finish 26 files however good the tool calling is. Non-bulk work
    # on a native provider still runs flat — pinned in its own test above.
    assert should_decompose(platform, _session_stub(provider="native-x")) is True
    # The master flag still governs everything, bulk included.
    platform.config.decompose_local_tasks = False
    assert should_decompose(platform, _session_stub()) is False
    assert should_decompose(platform, _session_stub(provider="native-x")) is False
    platform.config.decompose_local_tasks = True
    # …and decompose_all_tasks still lifts it onto every provider.
    assert platform.config.decompose_all_tasks is False, "default must stay OFF"
    platform.config.decompose_all_tasks = True
    assert should_decompose(platform, _session_stub(provider="native-x")) is True
    # A non-bulk simple task is still a flat loop even with the flag on.
    assert (
        should_decompose(platform, _session_stub(task="Say hello", provider="native-x"))
        is False
    )


# --------------------------------------------------------------------------- #
# (3) Contract 4, boundary by boundary: schema → route → column.
# --------------------------------------------------------------------------- #
def test_schema_default_is_none_and_bounds_are_the_shared_constants():
    assert SessionCreate(task="t").max_steps is None
    assert SessionCreate(task="t", max_steps=SESSION_MAX_STEPS_MIN).max_steps == 1
    assert SessionCreate(task="t", max_steps=SESSION_MAX_STEPS_MAX).max_steps == 200
    for bad in (0, -1, SESSION_MAX_STEPS_MAX + 1, 10_000):
        with pytest.raises(ValueError):
            SessionCreate(task="t", max_steps=bad)


def test_schema_rejects_a_boolean_budget():
    """``bool`` is an ``int`` subclass: a JSON ``true`` sliding through as a
    1-step budget would strand every run at its first tool call."""
    with pytest.raises(ValueError):
        SessionCreate(task="t", max_steps=True)


def test_post_sessions_persists_the_budget_on_the_row(tmp_path):
    with _client(tmp_path) as client:
        r = client.post("/sessions", json={"task": "big", "wait": True, "max_steps": 40})
        assert r.status_code == 200, r.text
        assert _row(tmp_path, r.json()["id"]).max_steps == 40


def test_post_sessions_without_the_field_leaves_the_column_null(tmp_path):
    """Absent = today's behavior. A 0 or a defaulted number here would silently
    change every existing caller's run length."""
    with _client(tmp_path) as client:
        r = client.post("/sessions", json={"task": "small", "wait": True})
        assert r.status_code == 200, r.text
        assert _row(tmp_path, r.json()["id"]).max_steps is None


@pytest.mark.parametrize("bad", [0, -1, 201, 1000, True, "twelve", 2.5])
def test_out_of_range_budget_is_a_422_never_a_silent_clamp(tmp_path, bad):
    with _client(tmp_path) as client:
        r = client.post("/sessions", json={"task": "x", "max_steps": bad})
        assert r.status_code == 422, f"max_steps={bad!r} must be rejected"


def test_the_422_names_the_range_so_the_user_can_fix_it(tmp_path):
    with _client(tmp_path) as client:
        r = client.post("/sessions", json={"task": "x", "max_steps": 5000})
        assert r.status_code == 422
        assert "200" in r.text and "1" in r.text


@pytest.mark.parametrize("edge", [SESSION_MAX_STEPS_MIN, SESSION_MAX_STEPS_MAX])
def test_the_boundaries_themselves_are_accepted(tmp_path, edge):
    with _client(tmp_path) as client:
        r = client.post("/sessions", json={"task": "x", "wait": True, "max_steps": edge})
        assert r.status_code == 200, r.text
        assert _row(tmp_path, r.json()["id"]).max_steps == edge


# --------------------------------------------------------------------------- #
# (4) The orchestrator: normalization, rerun, continue.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        (0, None),
        (-5, None),
        (True, None),  # bool is an int subclass — never a 1-step budget
        (False, None),
        ("nope", None),
        ([], None),
        (1, 1),
        (12, 12),
        ("40", 40),
        (3.7, 3),
        (200, 200),
        (201, 200),  # a DIRECT caller is clamped, not trusted
        (10_000, 200),
    ],
)
def test_normalize_max_steps(raw, expected):
    assert normalize_max_steps(raw) == expected


async def test_create_session_stores_the_budget_and_defaults_to_none(orchestrator):
    plain = await orchestrator.create_session("a", AgentType.BUILDER)
    assert plain.max_steps is None
    sized = await orchestrator.create_session("b", AgentType.BUILDER, max_steps=40)
    assert sized.max_steps == 40
    # Direct callers have no validator in front of them.
    clamped = await orchestrator.create_session("c", AgentType.BUILDER, max_steps=9999)
    assert clamped.max_steps == 200


async def test_rerun_carries_the_budget(orchestrator):
    """A big job re-run on the default budget would fail exactly the way the
    raised budget was set to prevent — and the user touched no control."""
    first = await orchestrator.create_session("big", AgentType.BUILDER, max_steps=45)
    again = await orchestrator.rerun_session(first.id)
    assert again.id != first.id
    assert again.max_steps == 45
    plain = await orchestrator.create_session("small", AgentType.BUILDER)
    assert (await orchestrator.rerun_session(plain.id)).max_steps is None


async def test_continue_carries_the_budget(orchestrator):
    """A follow-up turn is the SAME job — dropping back to the configured
    default would strand the continuation the user just asked for."""
    first = await orchestrator.create_session("big", AgentType.BUILDER, max_steps=45)
    await orchestrator.run_session(first.id)  # finish it: continue needs the ws free
    nxt = await orchestrator.continue_session(first.id, "keep going")
    assert nxt.max_steps == 45
    plain = await orchestrator.create_session("small", AgentType.BUILDER)
    await orchestrator.run_session(plain.id)
    assert (await orchestrator.continue_session(plain.id, "more")).max_steps is None


# --------------------------------------------------------------------------- #
# (5) The resolved budget: one number, both lanes.
# --------------------------------------------------------------------------- #
def _cfg(max_agent_steps=12):
    return SimpleNamespace(max_agent_steps=max_agent_steps)


def test_explicit_max_steps_reads_only_a_real_budget():
    assert explicit_max_steps(_session_stub(max_steps=40)) == 40
    assert explicit_max_steps(_session_stub(max_steps=None)) is None
    assert explicit_max_steps(_session_stub(max_steps=0)) is None
    assert explicit_max_steps(_session_stub(max_steps=-3)) is None
    assert explicit_max_steps(_session_stub(max_steps=True)) is None
    # A legacy/stub object with no such attribute reads as "not set", never 0.
    assert explicit_max_steps(SimpleNamespace(task="t")) is None


def test_session_step_budget_resolution():
    assert session_step_budget(_cfg(12), _session_stub(max_steps=None)) == 12
    assert session_step_budget(_cfg(12), _session_stub(max_steps=40)) == 40
    assert session_step_budget(_cfg(30), _session_stub(max_steps=None)) == 30
    # A session budget BELOW the configured default still wins — the point is
    # per-session control, in both directions.
    assert session_step_budget(_cfg(30), _session_stub(max_steps=4)) == 4
    # Junk config degrades to the shipped default rather than raising.
    assert session_step_budget(SimpleNamespace(), _session_stub()) == DEFAULT_MAX_AGENT_STEPS
    assert session_step_budget(_cfg("x"), _session_stub()) == DEFAULT_MAX_AGENT_STEPS


@pytest.mark.parametrize(
    "config_steps,session_steps",
    [
        (12, None),
        (12, 40),
        (30, None),
        (30, 4),
        (12, 200),
        (12, 1),
        (1, None),
        # The two divergences the first cut of this wave actually shipped:
        (0, None),  # a zeroed global cap
        (12, True),  # a bool — an int subclass, and the classic silent 1
    ],
)
def test_contract_4_has_exactly_ONE_resolution(config_steps, session_steps):
    """Contract 4 names ONE resolution. v1.174.0 briefly shipped two —
    ``runtime.resolve_max_steps`` (flat lane) and ``session_step_budget``
    (decomposed lane) — and they disagreed on a bool and on a zeroed config,
    which is the drift the frozen contract existed to stop. The decomposed lane
    now DELEGATES, so the only way this can fail again is by reintroducing a
    second copy of the arithmetic."""
    from iron_jarvis.agents.runtime import resolve_max_steps

    cfg, session = _cfg(config_steps), _session_stub(max_steps=session_steps)
    assert session_step_budget(cfg, session) == resolve_max_steps(session, cfg)


def test_a_zeroed_global_cap_does_not_become_a_bigger_budget():
    """``max_agent_steps = 0`` used to read as "missing" here and substitute 12
    (so mini-loops took SIX rounds) while the flat loop took none. Both lanes
    now read the same 0, and the mini-loop's own floor reproduces the
    pre-v1.174.0 ``max(1, min(6, 0))`` exactly."""
    from iron_jarvis.agents.runtime import resolve_max_steps

    cfg, session = _cfg(0), _session_stub(max_steps=None)
    assert session_step_budget(cfg, session) == 0 == resolve_max_steps(session, cfg)
    assert mini_loop_budget(cfg, session) == max(1, min(MAX_MINI_LOOP_STEPS, 0)) == 1


@pytest.mark.parametrize("configured", [1, 3, 6, 12, 30, 100])
def test_mini_loop_budget_is_byte_identical_when_no_session_budget(configured):
    """The pre-v1.174.0 formula, reproduced exactly: additive means additive."""
    old = max(1, min(MAX_MINI_LOOP_STEPS, configured))
    session = _session_stub(task=NON_BULK_SENTENCE, max_steps=None)
    assert mini_loop_budget(_cfg(configured), session) == old


@pytest.mark.parametrize(
    "explicit,expected",
    [
        (1, 1),  # never more than the run itself has
        (4, 4),
        (12, 6),  # a budget at the default keeps the default mini-loop
        (24, 6),  # 24//4 == 6
        (40, 10),
        (60, 15),
        (96, 24),
        (200, MAX_MINI_LOOP_CEILING),  # the ceiling holds
    ],
)
def test_mini_loop_budget_grows_with_an_explicit_budget(explicit, expected):
    assert mini_loop_budget(_cfg(12), _session_stub(max_steps=explicit)) == expected


def test_mini_loop_budget_never_exceeds_the_session_budget():
    for explicit in range(1, 30):
        assert mini_loop_budget(_cfg(12), _session_stub(max_steps=explicit)) <= explicit


@pytest.mark.parametrize(
    "explicit,expected",
    [
        (None, MAX_PLAN_STEPS),
        (12, MAX_PLAN_STEPS),  # the default budget keeps the default cap
        (24, MAX_PLAN_STEPS),  # 24//3 == 8
        (30, 10),
        (60, 20),
        (200, MAX_PLAN_STEPS_CEILING),
    ],
)
def test_plan_step_cap(explicit, expected):
    assert plan_step_cap(_session_stub(max_steps=explicit)) == expected


def test_plan_step_cap_ignores_a_globally_raised_config():
    """Keyed on the EXPLICIT per-session budget on purpose: a user who raised
    ``max_agent_steps`` in config under an older version must keep getting the
    plans they have been getting."""
    assert plan_step_cap(_session_stub(max_steps=None)) == MAX_PLAN_STEPS
    assert plan_step_cap(SimpleNamespace(task="t")) == MAX_PLAN_STEPS


# --------------------------------------------------------------------------- #
# (6) The caps reach the model and the loop (a clip alone raises nothing).
# --------------------------------------------------------------------------- #
def test_default_plan_prompt_text_is_unchanged():
    expected = (
        "You are a task planner. Split the user's task into 2-8 SMALL, "
        "concrete, independently verifiable steps for a coding/office "
        "assistant with tools.\n"
    )
    assert decompose._PLAN_SYSTEM.startswith(expected)
    assert decompose._plan_system() == decompose._PLAN_SYSTEM
    # The JSON shape survived the template's brace escaping.
    assert '{"steps": [{"goal": "<one small concrete action>"' in decompose._PLAN_SYSTEM
    assert '{"steps": []}' in decompose._PLAN_SYSTEM


def test_raised_plan_prompt_states_the_raised_cap():
    text = decompose._plan_system(20)
    assert f"into {MIN_PLAN_STEPS}-20 SMALL" in text
    assert "2-8 SMALL" not in text


def _stub_runtime(adapter):
    manager = ProviderManager()
    manager.register(adapter.provider, lambda model=None: adapter)
    bus = EventBus()
    return SimpleNamespace(
        p=SimpleNamespace(router=ModelRouter(manager, adapter.provider, bus), event_bus=bus)
    )


def _bare_run():
    return SimpleNamespace(id="run-1", steps=0, input_tokens=0, output_tokens=0)


async def test_plan_task_default_cap_clips_at_eight_and_says_eight():
    big = json.dumps({"steps": [{"goal": f"g{i}"} for i in range(14)]})
    adapter = _TextOnly([big])
    plan = await plan_task(
        _stub_runtime(adapter),
        _bare_run(),
        _session_stub(max_steps=None),
        get_agent_definition(AgentType.BUILDER),
    )
    assert plan is not None and len(plan) == MAX_PLAN_STEPS
    assert "2-8 SMALL" in adapter.calls[0][0]


async def test_plan_task_with_a_raised_budget_keeps_more_steps_and_asks_for_them():
    big = json.dumps({"steps": [{"goal": f"g{i}"} for i in range(14)]})
    adapter = _TextOnly([big])
    plan = await plan_task(
        _stub_runtime(adapter),
        _bare_run(),
        _session_stub(max_steps=60),
        get_agent_definition(AgentType.BUILDER),
    )
    assert plan is not None and len(plan) == 14  # cap is 20 → nothing clipped
    # The PROMPT carries the raised cap: clipping can only shorten a plan, so a
    # model told "at most 8" would never write the 12 steps a big job needs.
    assert "2-20 SMALL" in adapter.calls[0][0]
    assert "2-8 SMALL" not in adapter.calls[0][0]


class _RecordingRuntime:
    """Records the ``max_steps`` each mini-loop is given."""

    def __init__(self, platform):
        self.p = platform
        self.seen: list[int] = []

    async def perceive_act(self, run, session, agent_def, **kw):
        self.seen.append(kw["max_steps"])
        return True, "did the step"


async def _run_two_steps(platform, tmp_path, max_steps, task=MEASURED_SENTENCE):
    runtime = _RecordingRuntime(platform)
    session = SimpleNamespace(
        id="s-1",
        task=task,
        provider="mock",
        model=None,
        workspace_path=str(tmp_path),
        max_steps=max_steps,
    )
    plan = [PlanStep(goal="one"), PlanStep(goal="two")]  # no criteria → no judge
    results = await execute_plan(
        runtime,
        _bare_run(),
        session,
        get_agent_definition(AgentType.BUILDER),
        plan,
        system_prompt="sys",
        tool_specs=[],
        session_allow=set(),
        sink=None,
    )
    assert [r.ok for r in results] == [True, True]
    return runtime.seen


async def test_mini_loops_spend_the_configured_default_when_unsized(platform, tmp_path):
    platform.config.max_agent_steps = 12
    got = await _run_two_steps(platform, tmp_path, None, task=NON_BULK_SENTENCE)
    assert got == [6, 6]


async def test_an_unsized_BULK_step_gets_room_to_finish_a_chunk(platform, tmp_path):
    """v1.177.0. The other half of the same arithmetic: unsized, a bulk step
    takes BULK_MINI_LOOP_STEPS rather than six, because a `worklist_next`
    chunk is five items and finishing one means claim + read each + act on
    each + report each. Two live runs died reporting the six-round grant as
    the cause. There is no aggregate ceiling on this path, so the per-step
    figure is the only thing bounding the step."""
    platform.config.max_agent_steps = 12
    got = await _run_two_steps(platform, tmp_path, None, task=MEASURED_SENTENCE)
    assert got == [BULK_MINI_LOOP_STEPS, BULK_MINI_LOOP_STEPS]


async def test_mini_loops_spend_the_sessions_own_budget(platform, tmp_path):
    platform.config.max_agent_steps = 12
    # The session budget REACHES the loop that does the work — the whole point
    # of Contract 4. Pinned as a value: a mutation back to the old
    # min(6, config) would return [6, 6].
    assert await _run_two_steps(platform, tmp_path, 60) == [15, 15]
    assert await _run_two_steps(platform, tmp_path, 3) == [3, 3]


# --------------------------------------------------------------------------- #
# (7) The budget is spent ONCE — per run, not per stage.
# --------------------------------------------------------------------------- #
class _SpendingRuntime:
    """A runtime whose mini-loops actually CONSUME rounds. ``run.steps`` is
    incremented once per model round inside the real ``perceive_act``; anything
    measuring consumption has to read it, so the stub that tests the measurement
    has to move it."""

    def __init__(self, platform, cost: int | None = None):
        self.p = platform
        self.seen: list[int] = []
        self._cost = cost  # rounds actually burned; None = the whole grant

    async def perceive_act(self, run, session, agent_def, **kw):
        grant = kw["max_steps"]
        self.seen.append(grant)
        run.steps += grant if self._cost is None else min(grant, self._cost)
        return True, "did the step"


async def _run_plan(platform, tmp_path, *, steps, max_steps, cost=None, task=MEASURED_SENTENCE):
    runtime = _SpendingRuntime(platform, cost)
    run = _bare_run()
    session = SimpleNamespace(
        id="s-1",
        task=task,
        provider="mock",
        model=None,
        workspace_path=str(tmp_path),
        max_steps=max_steps,
    )
    results = await execute_plan(
        runtime,
        run,
        session,
        get_agent_definition(AgentType.BUILDER),
        [PlanStep(goal=f"step {i}") for i in range(steps)],
        system_prompt="sys",
        tool_specs=[],
        session_allow=set(),
        sink=None,
    )
    return runtime, run, results


async def test_a_typed_budget_is_a_CEILING_on_the_whole_plan(platform, tmp_path):
    """A "Max steps" of 12 meant up to 12 rounds in the flat lane and up to
    5x6 = 30 here — one label, two meanings. The plan now stops when the budget
    the user typed is gone, and the rounds are MEASURED (a step that finishes
    early is charged what it used, not what it was granted)."""
    platform.config.max_agent_steps = 12
    runtime, run, results = await _run_plan(platform, tmp_path, steps=5, max_steps=12)
    assert runtime.seen == [6, 6], "only two mini-loops had budget to run"
    assert run.steps == 12, "the run spent its budget and not one round more"
    assert [r.attempted for r in results] == [True, True, False, False, False]
    assert [r.ok for r in results] == [True, True, False, False, False]
    assert "12-step budget" in results[2].reason
    assert "not attempted" in results[2].reason


async def test_measured_consumption_not_an_assumed_charge(platform, tmp_path):
    """Each mini-loop here burns ONE round of its 6-round grant, so a 12-step
    budget carries all five steps. Charging the grant instead of the spend would
    strand steps 3-5 for work that never happened."""
    platform.config.max_agent_steps = 12
    runtime, run, results = await _run_plan(
        platform, tmp_path, steps=5, max_steps=12, cost=1
    )
    assert runtime.seen == [6, 6, 6, 6, 6]
    assert run.steps == 5
    assert all(r.attempted and r.ok for r in results)


async def test_the_last_step_is_granted_only_what_is_left(platform, tmp_path):
    """No overrun on the boundary: a 10-step budget with 6-round mini-loops
    grants 6 then 4, never 6 then 6."""
    platform.config.max_agent_steps = 12
    runtime, run, results = await _run_plan(platform, tmp_path, steps=3, max_steps=10)
    assert runtime.seen == [6, 4]
    assert run.steps == 10
    assert [r.attempted for r in results] == [True, True, False]


async def test_an_unsized_session_keeps_the_pre_v1174_per_stage_behaviour(
    platform, tmp_path
):
    """ADDITIVE MEANS ADDITIVE. ``config.max_agent_steps`` is a per-loop default,
    never a promise about a whole plan — retro-fitting an aggregate ceiling onto
    it would silently shorten every plan the local lane has been running since
    v1.132.0. Only a budget the user TYPED is a run-wide ceiling."""
    platform.config.max_agent_steps = 12
    runtime, run, results = await _run_plan(
        platform, tmp_path, steps=5, max_steps=None, task=NON_BULK_SENTENCE
    )
    assert runtime.seen == [6, 6, 6, 6, 6]
    assert run.steps == 30
    assert all(r.attempted and r.ok for r in results)


class _DeadRouter:
    async def complete(self, **kw):
        raise RuntimeError("no provider")


async def test_assemble_never_calls_unattempted_work_FAILED():
    """A step that never ran is not a step that failed its gate. Reporting it as
    "FAILED verification" would describe work nobody attempted, and reporting
    nothing would read as a completed plan."""
    runtime = SimpleNamespace(
        p=SimpleNamespace(router=_DeadRouter(), event_bus=EventBus())
    )
    session = SimpleNamespace(id="s", task=MEASURED_SENTENCE, provider="mock", model=None)
    results = [
        StepResult(index=0, goal="rename the first batch", ok=True, output="ok"),
        StepResult(
            index=1,
            goal="rename the rest",
            ok=False,
            reason="not attempted — the session's 12-step budget was spent by step 1",
            attempted=False,
        ),
    ]
    text = await assemble(runtime, _bare_run(), session, results)
    assert "NEVER ATTEMPTED" in text
    assert "rename the rest" in text
    assert "FAILED verification" not in text


# --------------------------------------------------------------------------- #
# (8) The gap this wave does NOT close (handoff, pinned by its consequence).
# --------------------------------------------------------------------------- #
async def test_a_session_created_without_a_budget_runs_on_the_configured_default(
    orchestrator, platform
):
    """KNOWN GAP — ``POST /projects/{id}/task``, the surface the measured job was
    actually posted from, has no ``max_steps`` in its body model and calls
    ``create_session`` without one (``routes/projects.py``, a coordinator-owned
    file). So a job posted from the project task strip lands on
    ``config.max_agent_steps`` no matter what the user wants, and re-running the
    acceptance job from there reproduces the original failure exactly. This test
    pins the CONSEQUENCE, which stays true either way; closing the gap is a
    handoff, not a P2 edit."""
    from iron_jarvis.agents.runtime import resolve_max_steps

    platform.config.max_agent_steps = 12
    session = await orchestrator.create_session(MEASURED_TASK, AgentType.BUILDER)
    assert session.max_steps is None
    assert resolve_max_steps(session, platform.config) == 12
    assert session_step_budget(platform.config, session) == 12
