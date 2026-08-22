"""Tool arming runs OFF the event loop, in BOTH chat lanes (v1.196.0).

THE INCIDENT THIS PREVENTS, measured on this machine before the fix:

    input                     select_auto_tools
    4,000-char prose                   7.4 ms
    1,000 blank lines                906.0 ms
    4,000 newlines                17,127.0 ms      <- 2,314x prose

`_resolve_armed_tools` is pure regex scoring. It cost ~2 ms for most of this
app's life and nobody minded that both chat lanes called it synchronously.
v1.196.0 fronted fourteen rules with the imperative-position test, and a run of
whitespace — a pasted document with blank lines — drove it quadratic. Possessive
quantifiers cut the worst case ~90x, and `test_change_intent_guard_v1196.py`
pins that ratio. This file pins the OTHER half: even the reduced cost must not
be paid on the loop.

Why that matters more than the milliseconds suggest (CLAUDE.md, v1.153.1): the
daemon is ONE asyncio loop, so CPU-bound work on it freezes every request in the
app — and it does not present as a slow reply. It presents as "Daemon offline",
because the dashboard's fetch times out, `lib/api.ts` maps a dead fetch to
status 0, and Retry issues another request onto the same blocked loop. That was
a real four-hour outage on the user's install.

THE TWO LANES ARE LOCK-STEP. `daemon/chat_turn.run_chat_turn` and the streaming
mirror in `daemon/routes/chat.chat_stream` both call this function, and CLAUDE.md
requires them edited together or not at all. Both are asserted here, because
"chat has it, the stream doesn't" is exactly the shape this repo keeps paying
for.

The assertion is STRUCTURAL — which thread the work runs on — never a duration.
A wall-clock threshold measures the CI runner, not the code.
"""

from __future__ import annotations

import ast
import pathlib
import threading

#: Source paths resolve from THIS FILE, never the cwd — the repo convention
#: (27 other test files use it; an earlier draft of this line said 37, which
#: was the number of grep MATCHES, not of files — a line count written up as a
#: file count, which is the ninth confident-but-wrong comment this wave has
#: produced and the reason each one now carries its own measurement). The first cut of this file used bare
#: ``pathlib.Path("src/...")``, which dies with FileNotFoundError the moment
#: pytest is invoked from anywhere but the repo root; a reviewer proved it by
#: running from another directory and getting 5 failed / 1 passed.
_REPO = pathlib.Path(__file__).resolve().parents[1]

import pytest

LANES = {
    "chat_turn.run_chat_turn": (
        "src/iron_jarvis/daemon/chat_turn.py",
        "run_chat_turn",
    ),
    "routes/chat.chat_stream": (
        "src/iron_jarvis/daemon/routes/chat.py",
        "chat_stream",
    ),
}


def _call_is_awaited_in_a_thread(path: str, func: str) -> bool:
    """True iff `_resolve_armed_tools` is reached via `asyncio.to_thread`
    inside *func* — and NOT called bare anywhere in it.

    Parsed rather than grepped so a call hidden in a branch cannot pass by the
    string appearing somewhere else in the file.
    """
    tree = ast.parse((_REPO / path).read_text(encoding="utf-8"))
    target = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == func
    )
    offloaded = bare = False
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        src = ast.unparse(node)
        if "_resolve_armed_tools" not in src:
            continue
        if src.startswith("asyncio.to_thread(_resolve_armed_tools"):
            offloaded = True
        elif src.startswith("_resolve_armed_tools("):
            bare = True
    assert not bare, (
        f"{path}::{func} calls _resolve_armed_tools directly — that is CPU-bound "
        f"regex scoring on the daemon's single event loop"
    )
    return offloaded


@pytest.mark.parametrize("lane", sorted(LANES))
def test_the_lane_arms_off_the_event_loop(lane):
    path, func = LANES[lane]
    assert _call_is_awaited_in_a_thread(path, func), (
        f"{lane} must reach _resolve_armed_tools through asyncio.to_thread"
    )


def test_both_lanes_agree():
    """The lock-step half. If someone offloads one lane and not the other, the
    parametrized tests above still half-pass; this one names the divergence."""
    got = {
        lane: _call_is_awaited_in_a_thread(*LANES[lane]) for lane in LANES
    }
    assert len(set(got.values())) == 1, (
        f"the two chat lanes disagree about offloading the arming pass: {got}. "
        f"CLAUDE.md requires them edited together — the streaming lane is the "
        f"one the user watches."
    )


def test_the_SECOND_scorer_caller_is_offloaded_too():
    """`_resolve_armed_tools` is not the only caller, and a partial fix is worse
    than none — it leaves the stall reachable while the comment at the other
    site claims it is closed.

    `_prepare_attachments` is `async def` and reaches the same scorer through
    `change_verbs_wanted` (the attachment consent gate). It used to call it
    inside the per-attachment loop, so a three-file turn paid the cost three
    more times ON the loop. It is now resolved for every distinct suffix in ONE
    `asyncio.to_thread` hop before the loop, and `_may_change` is a dict lookup.

    Asserted structurally: no `change_verbs_wanted(` call may appear in
    `_prepare_attachments` outside the offloaded helper.
    """
    tree = ast.parse(
        (_REPO / "src/iron_jarvis/daemon/chat_turn.py").read_text(encoding="utf-8")
    )
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_prepare_attachments"
    )
    # The helper that IS allowed to call it, and which the to_thread hop wraps.
    helper = next(
        (n for n in ast.walk(fn)
         if isinstance(n, ast.FunctionDef) and n.name == "_resolve_changes"),
        None,
    )
    assert helper is not None, (
        "_prepare_attachments must resolve change verbs through a helper that "
        "asyncio.to_thread can hop"
    )
    hopped = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and ast.unparse(n).startswith("asyncio.to_thread(_resolve_changes")
    ]
    assert hopped, "_resolve_changes must be reached via asyncio.to_thread"

    inside_helper = {id(n) for n in ast.walk(helper)}
    stray = [
        ast.unparse(n) for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and id(n) not in inside_helper
        and ast.unparse(n).startswith("change_verbs_wanted(")
    ]
    assert not stray, (
        f"change_verbs_wanted is called on the event loop in "
        f"_prepare_attachments: {stray}"
    )


def test_the_agent_lane_arms_off_the_event_loop():
    """THE THIRD CALLER. Both chat lanes were offloaded and the module comment
    then claimed the scorer was off the loop — but `agents/runtime.arm_for_task`
    runs the SAME scorer, and `Runner.run` is `async def`, so a task string with
    a long whitespace run parked the loop there instead. Its own docstring said
    "nothing to offload", which is how it stayed invisible.

    Once per RUN rather than per turn, so the frequency is low; the cost when it
    lands is the same ~200 ms of every request in the app.
    """
    tree = ast.parse(
        (_REPO / "src/iron_jarvis/agents/runtime.py").read_text(encoding="utf-8")
    )
    run = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "run"
    )
    calls = [
        ast.unparse(n) for n in ast.walk(run)
        if isinstance(n, ast.Call) and "arm_for_task" in ast.unparse(n)
    ]
    assert calls, "Runner.run must still arm per task"
    assert any(c.startswith("asyncio.to_thread(arm_for_task") for c in calls), (
        f"arm_for_task runs the CPU-bound scorer on the event loop: {calls}"
    )
    assert not any(c.startswith("arm_for_task(") for c in calls), (
        f"a bare arm_for_task call remains on the loop: {calls}"
    )


def test_the_behavioural_pin_lives_where_the_lane_is_driven():
    """WHERE THE BEHAVIOURAL PROOF ACTUALLY IS, and why it is not here.

    Two earlier versions of this test were vacuous. The first defined its own
    function, handed it to `asyncio.to_thread`, and asserted the thread name was
    not main — it never imported `chat_turn`, and passed with BOTH lanes' hops
    deleted. The second monkeypatched the real `_resolve_armed_tools` but still
    called `asyncio.to_thread` ITSELF rather than driving a lane, so it also
    stayed green when both hops were removed. Both carried docstrings claiming
    to be "the behavioural half". Both were caught by reviewers, not by me.

    The lesson is the one this whole wave keeps relearning: a test that does not
    drive the code under test proves nothing about it, however real the
    machinery inside it looks. Rather than write a third near-miss, the
    behavioural pin lives in the file that already drives BOTH routes through a
    TestClient — `test_attachment_handoff_v1196.py::
    test_the_scorer_is_hopped_off_the_loop_in_both_lanes` — where a spy calls
    `asyncio.get_running_loop()` and records whether it raised. A worker thread
    has no running loop; the event-loop thread does. That distinction is the
    assertion, and it goes red the moment either hop is deleted.

    (`threading.main_thread()` cannot be used for it: TestClient runs the event
    loop on a non-main thread, so a name check would pass in both worlds.)

    What remains in THIS file is the structural half — the AST tests above —
    which is genuinely load-bearing: deleting either hop turns them red.
    """
    # Resolved from __file__, not the cwd: a cwd-relative path makes this test
    # error out the moment pytest is invoked from anywhere but the repo root.
    src = (_REPO / "tests" / "test_attachment_handoff_v1196.py").read_text(
        encoding="utf-8"
    )
    assert "test_the_scorer_is_hopped_off_the_loop_in_both_lanes" in src, (
        "the behavioural pin named in this docstring does not exist — either "
        "restore it or correct this docstring; do not leave the claim dangling"
    )
