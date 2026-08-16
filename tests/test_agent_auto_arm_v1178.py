"""An agent run is armed for ITS TASK, not only for its type (v1.178.0).

THE PATTERN THIS FILE EXISTS TO BREAK. Five releases in a row failed because
the tool the run needed was not on the agent definition's static roster, and a
tool absent from a roster does not exist to the model: `rename_file`
(v1.177.2 — the acceptance job was "rename all files in this folder" and the
agent shelled out and renamed nothing), the worklist (v1.177.0), `view_image`
(v1.174.0), `workflow_list` (v1.172.0), `history_search` (v1.142.0). Every one
was repaired by editing `agents/types.py` after the fact — which only fixes the
case that already burned. Chat has read the request and armed what it needs
since v1.101 (`tools/autoselect.select_auto_tools`); the agent lane resolved
its roster at DEFINITION time and never looked at the task text.

WHAT THESE TESTS ARE CAREFUL ABOUT.

* The seam test asserts on the tool SPECS the router actually received during a
  real `AgentRuntime.run` — not on the helper's return value. A helper that
  computes the right list and is never called at the seam is the exact shape of
  the five failures above, and it must go red here.
* Additivity is asserted as a PREFIX plus new names, so a change that reorders
  or drops a granted tool fails even though the set still "contains" it.
* The safety argument is asserted against `AUTO_SAFE_TOOLS` itself and against
  the named dangerous tools, so widening that frozenset can never silently hand
  an agent `shell`.
"""

from __future__ import annotations

from types import SimpleNamespace

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import (
    _AUTO_ARM_CAP,
    _WRITE_TIER,
    AgentRuntime,
    arm_for_task,
)
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.models import AgentState, AgentType
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS

#: The REVIEWER is the honest subject: its roster is the narrowest of the
#: built-ins (read/search + the brain pair), so a tool that shows up in its
#: armed list demonstrably came from the task text and not from the definition.
_REVIEWER = ["read_file", "list_files", "grep", "read_document", "extract_pdf",
             "memory_search", "skill_search", "recall_lessons", "recall",
             "ltm_search", "blackboard_post", "blackboard_read", "message_agent"]

#: Names an agent must never acquire by typing a task. Each is either the
#: measured wrong door (v1.174.0: five `shell` calls where `read_file` was
#: sitting right there) or a consent-gated capability.
_NEVER = ("shell", "repl", "edit_file", "browse", "web_action", "mcp_call",
          "computer_use", "code_run", "workflow_run", "delegate")


def _added(platform, task: str, roster=None, **kw) -> list[str]:
    """What arming ADDED to *roster* for *task*, in order."""
    base = list(_REVIEWER if roster is None else roster)
    armed = arm_for_task(platform, task, base, **kw)
    assert armed[: len(base)] == base, "the roster must ride at the front, unchanged"
    return list(armed[len(base) :])


# =============================================================================
# 1. The task decides — and only ever ADDS
# =============================================================================
def test_a_task_that_names_the_web_arms_web_search_the_reviewer_never_had(platform):
    """The reviewer definition carries no way to reach the internet. A run whose
    task says "look it up online" gets one, without an edit to types.py."""
    assert "web_search" not in _REVIEWER
    added = _added(platform, "look up the latest IRS mileage rate online")
    assert "web_search" in added


def test_a_spreadsheet_task_arms_the_spreadsheet_engine(platform):
    """The v1.142/v1.172/v1.174 shape, avoided: the tool arrives because the
    TASK argues for it. Engine-computed figures instead of model arithmetic is
    the whole reason `excel_*` exists (the local-model failure mode)."""
    added = _added(platform, "check the formulas in the quarterly spreadsheet")
    assert "excel_formula_check" in added
    assert "excel_query" in added


def test_no_signal_leaves_the_roster_BYTE_IDENTICAL(platform):
    """Additive by default: a task with no tool signal must produce exactly the
    list that shipped before this feature — same names, same order, nothing
    appended."""
    for task in ("say hello to the team", "how are you today", ""):
        assert arm_for_task(platform, task, list(_REVIEWER)) == _REVIEWER


def test_a_granted_tool_is_never_re_listed(platform):
    """`read_document` is BOTH on the reviewer roster and a strong match for a
    document task. Arming it twice would spend a slot of the cap on a tool the
    model already has."""
    added = _added(platform, "read the pdf report in my documents folder")
    assert "read_document" not in added
    armed = arm_for_task(
        platform, "read the pdf report in my documents folder", list(_REVIEWER)
    )
    assert len(armed) == len(set(armed)), "no duplicate names reach the specs"


def test_an_empty_roster_stays_empty(platform):
    """A definition that grants no tools is a text-only run. Arming six
    file/document tools onto it would be a capability grant nobody asked for —
    and an empty DYNAMIC record means "not specified" and inherits its base
    type's roster before it ever reaches the runtime."""
    assert arm_for_task(platform, "summarize the pdf and check the formulas", []) == []


# =============================================================================
# 2. The cap is the point (a local model + more schema = another wrong door)
# =============================================================================
def test_additions_are_capped(platform):
    """A task that trips half the rule table still must not hand the model the
    whole safe set."""
    busy = (
        "rename every file in the folder to match its contents, summarize the "
        "pdf report, check the spreadsheet formulas, redact the pii, look it up "
        "online and search our history for what we decided"
    )
    added = _added(platform, busy)
    assert 0 < len(added) <= _AUTO_ARM_CAP
    assert len(_added(platform, busy, cap=2)) == 2
    assert _added(platform, busy, cap=0) == []


def test_only_auto_safe_tools_can_ever_be_added(platform):
    """The safety argument, asserted against the frozenset itself: `shell` and
    friends stay a definition-time/consent decision, whatever the task says."""
    for task in (
        "run a powershell script to rename every file and then edit the code",
        "browse to the client portal and download the spreadsheet",
        "use the mcp server to send the email, then redact the pii in the pdf",
    ):
        added = _added(platform, task)
        assert set(added) <= AUTO_SAFE_TOOLS
        armed = arm_for_task(platform, task, list(_REVIEWER))
        for banned in _NEVER:
            assert banned not in armed


# =============================================================================
# 2b. Arming widens a run's VOCABULARY, never its TIER
# =============================================================================
#: The tasks that measurably armed a writer onto the read-only reviewer before
#: the tier gate existed, with the tool each one produced.
_WRITEY_TASKS = [
    ("review the draft report and save a corrected version as a docx", "write_document"),
    ("review the workbook and fix the formulas in the sheet", "excel_edit"),
    ("review the K-1 pdf and redact the pii before we send it", "redact_pii"),
    ("assess the merge of these pdfs and split the scan into pages", "pdf_arrange"),
    ("assess the merge of these pdfs and split the scan into pages", "pdf_split"),
    ("write a python script to rename the files and save it as a .py", "write_file"),
    ("remember that the client prefers pdf and note it for next time", "ltm_append"),
]


def test_a_read_only_definition_never_gains_a_writer(platform):
    """`AUTO_SAFE_TOOLS` was curated for CHAT, where arming is an interactive
    per-turn grant the user makes with the Auto toggle and can see — chat even
    passes the armed names as `session_allow` on that reasoning. An agent run has
    no toggle and nobody watching, and the REVIEWER definition is 13 read-only
    tools on purpose. Reading a task is not consent to author files with the one
    agent the user picked BECAUSE it only reads."""
    for task, tool in _WRITEY_TASKS:
        armed = arm_for_task(platform, task, list(_REVIEWER))
        assert tool not in armed, f"{task!r} handed the reviewer {tool}"
        assert not (set(armed) & _WRITE_TIER), f"{task!r} handed the reviewer a writer"


def test_the_write_tier_names_every_armable_tool_that_writes():
    """Pinned BY NAME. Every assertion above phrases the rule as "no member of
    `_WRITE_TIER`", which moves with the constant — deleting an entry would leave
    them all green while the tool it named quietly became armable onto a
    read-only agent. This is the list itself, checked against the set arming
    actually draws from."""
    for name in (
        "write_file", "write_document", "excel_edit", "redact_pii",
        "pdf_arrange", "pdf_split", "ltm_append", "remember_preference",
    ):
        assert name in AUTO_SAFE_TOOLS, f"{name} is not armable; stale entry"
        assert name in _WRITE_TIER, f"{name} writes and must be gated"


def test_the_supervisor_is_read_only_too(platform):
    """The other definition that grants no writer. It delegates the work — the
    subagent it spawns has the writers, and is itself armed for its own task."""
    roster = get_agent_definition(AgentType.SUPERVISOR).tools
    assert not (set(roster) & _WRITE_TIER)
    armed = arm_for_task(platform, _WRITEY_TASKS[0][0], list(roster))
    assert not (set(armed) & _WRITE_TIER)


def test_the_gate_is_a_NO_OP_for_an_agent_that_already_writes(platform):
    """The gate must close one hole, not neuter the feature: a definition that
    already grants writers is a writing agent, so the tier is nothing new. Every
    built-in except REVIEWER/SUPERVISOR qualifies."""
    builder = get_agent_definition(AgentType.BUILDER).tools
    assert set(builder) & _WRITE_TIER, "the builder is the writing agent"
    task = "save a corrected version of the report as a new docx"
    # The builder already HOLDS every write-tier tool, so the gate is invisible
    # there by construction — nothing is stripped and `write_document` is still
    # in the specs it takes to the model.
    assert "write_document" in arm_for_task(platform, task, list(builder))
    # The predicate itself is what needs pinning: ONE writer on the roster makes
    # it a writing agent, and the same task then reaches the rest of the tier.
    assert "write_document" not in _added(platform, task)
    assert "write_document" in _added(platform, task, roster=[*_REVIEWER, "edit_file"])


def test_the_cap_still_fills_with_usable_tools_under_the_gate(platform):
    """The write tier is excluded BEFORE selection, not filtered after it — a
    post-filter would silently return fewer than `cap` tools on exactly the
    write-heavy tasks, quietly shrinking the feature."""
    busy = (
        "rename every file in the folder to match its contents, summarize the "
        "pdf report, check the spreadsheet formulas, redact the pii, look it up "
        "online and search our history for what we decided"
    )
    added = _added(platform, busy)
    assert len(added) == _AUTO_ARM_CAP
    assert not (set(added) & _WRITE_TIER)


# =============================================================================
# 3. Only what this install actually serves
# =============================================================================
def test_a_name_the_registry_does_not_serve_is_dropped(platform, monkeypatch):
    """Selection is a static table; the registry is what this build actually
    holds. Verified rather than assumed — the dropped name must reach neither
    the armed list NOR the specs."""
    import iron_jarvis.tools.autoselect as _auto

    monkeypatch.setattr(
        _auto, "select_auto_tools", lambda *a, **k: ["ghost_tool", "web_search"]
    )
    armed = arm_for_task(platform, "anything", list(_REVIEWER))
    assert "ghost_tool" not in armed and "web_search" in armed
    assert "ghost_tool" not in {s["name"] for s in platform.registry.specs(armed)}


def test_arming_never_breaks_a_run(platform, monkeypatch):
    """Arming is an optimisation. If selection blows up, the run proceeds with
    exactly the roster it had before — never with no tools at all."""
    import iron_jarvis.tools.autoselect as _auto

    def _boom(*a, **k):
        raise RuntimeError("selection exploded")

    monkeypatch.setattr(_auto, "select_auto_tools", _boom)
    assert arm_for_task(platform, "check the formulas", list(_REVIEWER)) == _REVIEWER


def test_the_shared_definition_is_never_mutated(platform):
    """`agents/types._DEFINITIONS` holds module-level singletons: appending in
    place would rewrite that agent type's roster for the life of the process
    (the `_spec_with_store_as` deep-copy lesson)."""
    roster = get_agent_definition(AgentType.REVIEWER).tools
    before = list(roster)
    arm_for_task(platform, "look up the latest guidance online", roster)
    assert get_agent_definition(AgentType.REVIEWER).tools == before


# =============================================================================
# 4. THE SEAM — what the model is actually offered during a real run
# =============================================================================
def _capture_router(platform, seen: dict):
    """Stand in for the router's token stream, recording the tool specs each
    call was given and finalizing immediately (no tool calls)."""

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        seen.setdefault("tools", []).append([s["name"] for s in tools])
        yield {
            "type": "final",
            "response": LLMResponse(text="done", tool_calls=[], usage={}),
            "provider": "mock",
            "model": "mock",
        }

    platform.router.stream = fake_stream
    return seen


async def test_a_real_run_offers_the_model_the_task_selected_tool(platform):
    """The call site itself. A reviewer session whose task names the web must
    see `web_search` in the specs the router hands the model — the reviewer
    definition has no such tool, so this can only come from arming at run
    start."""
    seen = _capture_router(platform, {})
    sess = await Orchestrator(platform).create_session(
        "look up the latest IRS mileage rate online", AgentType.REVIEWER
    )
    run = await AgentRuntime(platform).run(sess, get_agent_definition(AgentType.REVIEWER))

    assert run.state is AgentState.COMPLETED
    offered = seen["tools"][0]
    assert "web_search" in offered, "the task-selected tool never reached the model"
    # ...and everything the definition grants is still there, plus a bounded
    # number of additions.
    for granted in ("read_file", "read_document", "recall", "ltm_search"):
        assert granted in offered
    assert len(offered) <= len(_REVIEWER) + _AUTO_ARM_CAP


async def test_a_run_with_no_signal_is_offered_exactly_the_definition(platform):
    """The other half of additivity, at the seam: a task with no tool signal
    must produce the same spec set this ran with before v1.178.0."""
    seen = _capture_router(platform, {})
    sess = await Orchestrator(platform).create_session(
        "say hello to the team", AgentType.REVIEWER
    )
    await AgentRuntime(platform).run(sess, get_agent_definition(AgentType.REVIEWER))

    expected = {
        s["name"]
        for s in platform.registry.specs(get_agent_definition(AgentType.REVIEWER).tools)
    }
    assert set(seen["tools"][0]) == expected


async def test_arming_reads_the_session_task_not_the_agent_type(platform):
    """Two runs of the SAME agent type diverge on task text alone — which is
    the entire claim this feature makes."""
    seen = _capture_router(platform, {})
    orch, rt = Orchestrator(platform), AgentRuntime(platform)
    definition = get_agent_definition(AgentType.REVIEWER)

    await rt.run(
        await orch.create_session("check the spreadsheet formulas", AgentType.REVIEWER),
        definition,
    )
    await rt.run(
        await orch.create_session("look it up online", AgentType.REVIEWER), definition
    )

    first, second = set(seen["tools"][0]), set(seen["tools"][1])
    assert "excel_formula_check" in first and "excel_formula_check" not in second
    assert "web_search" in second and "web_search" not in first


async def test_the_runtime_consults_arming_with_this_session_task(platform, monkeypatch):
    """Belt and braces on the seam: `arm_for_task` must be what `run` consults,
    and it must be handed THIS session's task and THIS definition's roster — a
    call site that still passes `agent_def.tools` straight to `registry.specs`
    fails here."""
    import iron_jarvis.agents.runtime as _rt

    seen: dict = {}
    real = _rt.arm_for_task

    def spy(platform_, task, roster, **kw):
        seen["task"], seen["roster"] = task, list(roster)
        return real(platform_, task, roster, **kw)

    monkeypatch.setattr(_rt, "arm_for_task", spy)
    _capture_router(platform, {})
    sess = await Orchestrator(platform).create_session(
        "summarize the pdf", AgentType.BUILDER
    )
    await AgentRuntime(platform).run(sess, get_agent_definition(AgentType.BUILDER))

    assert seen["task"] == "summarize the pdf"
    assert seen["roster"] == get_agent_definition(AgentType.BUILDER).tools


async def test_the_DECOMPOSED_lane_gets_the_same_armed_specs(platform, monkeypatch):
    """`run` builds `tool_specs` ONCE and both lanes take it — but only the flat
    lane is exercised above, so a change that rebuilt the specs from
    `agent_def.tools` for the decomposition branch would ship silently. That is
    the worse half to lose: decomposition exists for the LOCAL model, which is
    the model that cannot recover from a missing tool."""
    import iron_jarvis.agents.decompose as _dec

    seen: dict = {}

    async def fake_decomposed(_rt, _run, _sess, _def, *, tool_specs, **kw):
        seen["tools"] = [s["name"] for s in tool_specs]
        return "done"

    monkeypatch.setattr(_dec, "should_decompose", lambda *a, **k: True)
    monkeypatch.setattr(_dec, "run_decomposed", fake_decomposed)
    sess = await Orchestrator(platform).create_session(
        "look up the latest IRS mileage rate online", AgentType.REVIEWER
    )
    await AgentRuntime(platform).run(sess, get_agent_definition(AgentType.REVIEWER))

    assert "web_search" in seen["tools"], "the decomposed lane ran unarmed"
    assert "read_file" in seen["tools"]


def test_arming_is_offline_and_cheap(platform):
    """No model call, no I/O: `select_auto_tools` is pure regex scoring, so a
    platform whose router would explode if touched still arms fine. This is why
    it is safe to run inline at run start (v1.153.1: nothing blocking on the
    loop)."""
    boom = SimpleNamespace(
        registry=platform.registry,
        router=SimpleNamespace(
            complete=lambda **k: (_ for _ in ()).throw(AssertionError("router used")),
        ),
    )
    added = _added(boom, "look up the latest guidance online")
    assert "web_search" in added
