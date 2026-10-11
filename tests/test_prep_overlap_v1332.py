"""The turn gets ready AT THE SAME TIME (v1.332.0, speed S2).

After the project and the model are known, seven preparation steps depend
only on the body and the project: the attachments, the page the user asked
about, the apps' resources, the earlier chats pointed to, the "/" skill's
playbook, the bound folder and the arming pass. Both chat lanes ran them one
after another. They now START together (``chat_turn._PrepGroup``) and are
AWAITED at their old places, so:

* the steps are in flight at once (counted by a rendezvous, never timed);
* the system prompt is byte-identical to the serial order, even when the
  steps FINISH in reverse order;
* a Stop during preparation cancels every step still running;
* one failing step fails the turn exactly as before, and the steps after it
  are cancelled (the serial turn would never have started them);
* the local-only scope is visible inside a step, in its task and its thread.

Mutations (each went red, then restored green): start lazily at the take
(serial again) -> the overlap test; append the page before the attachments
-> the byte-identical test; drop the group's close -> the Stop and the
failure tests; drop the cancel-the-later-steps callback -> the "at once"
test; create the step tasks in an empty context -> the local-only test.

Review round (v1.332.0), each red then restored: drop the POST lane's early
unknown-skill 404 -> the refused-turn test; make the group's close a no-op, or
drop the stream lane's close -> the dropped-connection tests.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.daemon.chat_turn as chat_turn
import iron_jarvis.daemon.routes.chat as chat_routes
import iron_jarvis.skills.framework as skills_framework
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.providers import egress
from tests.test_wave3_chat_lanes_v1311 import _parse_sse, _post_turn, _scripted_router

LANES = ("post", "stream")

#: The serial order the lanes have always awaited the steps in.
ORDER = ("attachments", "page", "resources", "refs", "skill", "workspace", "arming")


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


#: The four ASYNC fakes park in this pool, not in the loop's default
#: executor: that one has min(32, cpus + 4) workers, i.e. SIX on a 2-vCPU
#: runner, and seven steps parked there at once would starve it (the
#: rendezvous would time out on a small machine with nothing wrong). The
#: three steps the lanes themselves hand to ``asyncio.to_thread`` (skill,
#: folder, arming) still use the default executor, as in production.
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="s2-rendezvous")


async def _parked(hop, name: str, value):
    return await asyncio.get_running_loop().run_in_executor(_POOL, hop, name, value)


class _Rendezvous:
    """Every step checks in (on a worker thread) and waits until ALL seven
    have arrived; then they leave in REVERSE order (the last step finishes
    first). Serial code breaks it: the first step waits for siblings that
    cannot arrive, the wait times out, and from then on nobody waits. The
    timeout only ever ends a wait; nothing is asserted about time."""

    def __init__(self, names=ORDER, timeout: float = 10.0) -> None:
        self.names = list(names)
        self.timeout = timeout
        self.cv = threading.Condition()
        self.arrived: set[str] = set()
        self.left: list[str] = []
        self.inflight = 0
        self.peak = 0
        self.broken = False

    def hop(self, name: str, value):
        with self.cv:
            self.arrived.add(name)
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
            self.cv.notify_all()
            if not self.broken:
                if not self.cv.wait_for(lambda: self.arrived >= set(self.names),
                                        timeout=self.timeout):
                    self.broken = True
                    self.cv.notify_all()
            if not self.broken:
                later = set(self.names[self.names.index(name) + 1:])
                self.cv.wait_for(lambda: later <= set(self.left), timeout=self.timeout)
            self.left.append(name)
            self.inflight -= 1
            self.cv.notify_all()
        return value


_SKILL = SimpleNamespace(name="prep-skill", instructions="Follow the steps.")


def _body(ws: Path, **extra) -> dict:
    return {
        "messages": [{"role": "user", "content": "what is in the ledger?"}],
        "page_context": {"title": "Ledger", "path": "/ledger", "text": "a page"},
        "resources": [{"pack": "files", "uri": "file://ledger.txt"}],
        "thread_refs": ["earlier-chat"],
        "skill": "prep-skill",
        "workspace_dir": str(ws),
        **extra,
    }


def _install_steps(platform, monkeypatch, *, hop, ws: Path, record=None):
    """Replace each of the seven steps with a fake that calls ``hop`` (on a
    worker thread) and returns a marker. ``record(name)`` runs first, in the
    step's own task or thread."""
    record = record or (lambda name: None)
    real_arming = chat_turn._resolve_armed_tools

    async def attachments(d, body, **kw):
        record("attachments")
        return [], await _parked(hop, "attachments", "\n\nATT-1")

    async def page(d, body):
        record("page")
        return await _parked(hop, "page", "\n\nPAGE-1")

    async def resources(d, body):
        record("resources")
        return await _parked(hop, "resources", ("\n\nRES-1", []))

    async def refs(d, body, **kw):
        record("refs")
        return await _parked(hop, "refs", ("\n\nREFS-1", []))

    def playbook(skill, **kw):
        record("skill")
        return hop("skill", "SKILL-1")

    def workspace(default_ws, workspace_dir, root):
        record("workspace")
        return hop("workspace", (ws, False))

    def arming(d, body, cap=None):
        record("arming")
        hop("arming", None)
        return real_arming(d, body, cap)

    for mod in (chat_turn, chat_routes):
        monkeypatch.setattr(mod, "_prepare_attachments", attachments)
        monkeypatch.setattr(mod, "_page_context_section", page)
        monkeypatch.setattr(mod, "_resolve_tool_workspace", workspace)
        monkeypatch.setattr(mod, "_resolve_armed_tools", arming)
    monkeypatch.setattr(chat_turn, "_read_turn_resources", resources)
    monkeypatch.setattr(chat_routes, "read_turn_resources", resources)
    monkeypatch.setattr(chat_turn, "_read_thread_refs", refs)
    monkeypatch.setattr(chat_routes, "read_thread_refs", refs)
    monkeypatch.setattr(skills_framework, "guarded_instructions", playbook)
    real_get = platform.skills.get
    monkeypatch.setattr(
        platform.skills, "get",
        lambda name: _SKILL if name == "prep-skill" else real_get(name),
    )


def _turn(tmp_path, monkeypatch, lane: str):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    ws = tmp_path / "bound"
    ws.mkdir()
    probe = _Rendezvous()
    _install_steps(platform, monkeypatch, hop=probe.hop, ws=ws)
    seen = _scripted_router(platform, monkeypatch, [], "ok.")
    _post_turn(client, lane, _body(ws))
    assert seen["systems"], "the router was never reached"
    return probe, seen["systems"][0], ws


@pytest.mark.parametrize("lane", LANES)
def test_the_prep_steps_run_at_the_same_time(tmp_path, monkeypatch, lane):
    probe, _system, _ws = _turn(tmp_path, monkeypatch, lane)
    assert probe.arrived == set(ORDER), f"{lane}: every step must still run"
    assert not probe.broken and probe.peak == len(ORDER), (
        f"{lane}: the prep steps ran {probe.peak} at a time; expected all "
        f"{len(ORDER)} in flight together"
    )
    assert probe.left == list(reversed(ORDER)), "the steps finished out of order on purpose"


@pytest.mark.parametrize("lane", LANES)
def test_the_system_prompt_is_byte_identical_to_the_serial_order(tmp_path, monkeypatch, lane):
    """The steps FINISHED in reverse order, yet each section sits where the
    serial turn put it, with the same joins."""
    _probe, system, ws = _turn(tmp_path, monkeypatch, lane)
    expected = (
        "\n\n# Attachments (provided by the user this turn)" + "\n\nATT-1"
        + "\n\nPAGE-1"
        + "\n\n# Resources from the user's apps (attached this turn)" + "\n\nRES-1"
        + "\n\nREFS-1"
        + "\n\n# Skill invoked by the user: prep-skill\n"
        "FOLLOW this playbook for this request.\n" + "SKILL-1"
    )
    assert expected in system, f"{lane}: prep sections out of order:\n{system[-900:]!r}"
    ws_block = chat_turn._workspace_grounding_block(str(ws), (ws, False))
    assert ws_block and ws_block in system
    assert system.index(expected) < system.index(ws_block), (
        "the bound folder's block moved ahead of the skill section"
    )


def test_both_lanes_send_the_same_prompt(tmp_path, monkeypatch):
    systems = {}
    for lane in LANES:
        _p, system, _ws = _turn(tmp_path / lane, monkeypatch, lane)
        systems[lane] = system.replace(str(tmp_path / lane), "<HOME>")
    assert systems["post"] == systems["stream"]


# --------------------------------------------------------------------------- #
# Stop, failure, and the local-only scope
# --------------------------------------------------------------------------- #
class _Held:
    """Async steps that park until released, recording a cancellation."""

    def __init__(self, names) -> None:
        self.started = {n: asyncio.Event() for n in names}
        self.release = asyncio.Event()
        self.cancelled: set[str] = set()

    def step(self, name, value):
        async def run(*a, **k):
            self.started[name].set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.add(name)
                raise
            return value

        return run


class _CountingRouter:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, **kw):
        self.calls += 1
        yield {"type": "text", "text": "never"}

    async def complete(self, **kw):
        self.calls += 1
        raise AssertionError("the model must not be called")


async def _drain(agen, out: list) -> None:
    async for frame in agen:
        out.append(frame)


async def test_a_stop_during_prep_cancels_every_step_still_running(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    held = _Held(("attachments", "page", "resources", "refs"))
    for mod in (chat_turn, chat_routes):
        monkeypatch.setattr(mod, "_prepare_attachments", held.step("attachments", ([], "")))
        monkeypatch.setattr(mod, "_page_context_section", held.step("page", ""))
    monkeypatch.setattr(chat_routes, "read_turn_resources", held.step("resources", ("", [])))
    monkeypatch.setattr(chat_routes, "read_thread_refs", held.step("refs", ("", [])))
    router = _CountingRouter()
    monkeypatch.setattr(platform.router, "stream", router.stream)
    monkeypatch.setattr(platform.router, "complete", router.complete)
    body = ChatBody(**{
        "messages": [{"role": "user", "content": "hello"}],
        "page_context": {"title": "T", "path": "/t", "text": "x"},
        "resources": [{"pack": "files", "uri": "file://a"}],
        "thread_refs": ["earlier-chat"],
        "turn_id": "s2-stop",
    })
    frames: list = []
    gen = await chat_routes.stream_chat_turn(platform, {}, body)
    consumer = asyncio.create_task(_drain(gen, frames))
    try:
        for ev in held.started.values():
            await asyncio.wait_for(ev.wait(), 10)  # all four in flight at once
        assert TURNS.stop("s2-stop")
        await asyncio.wait_for(consumer, 10)
        assert held.cancelled == {"attachments", "page", "resources", "refs"}, (
            f"steps left running after Stop: {set(held.started) - held.cancelled}"
        )
        assert router.calls == 0
        assert not any("event: done" in f for f in frames)
    finally:
        held.release.set()
        if not consumer.done():
            consumer.cancel()


def _failing_steps(monkeypatch, held: _Held, boom: Exception):
    async def broken(*a, **k):
        raise boom

    async def quick_attachments(*a, **k):
        return [], ""

    async def quick_page(*a, **k):
        return ""

    for mod in (chat_turn, chat_routes):
        monkeypatch.setattr(mod, "_prepare_attachments", quick_attachments)
        monkeypatch.setattr(mod, "_page_context_section", quick_page)
    monkeypatch.setattr(chat_turn, "_read_turn_resources", broken)
    monkeypatch.setattr(chat_routes, "read_turn_resources", broken)
    monkeypatch.setattr(chat_turn, "_read_thread_refs", held.step("refs", ("", [])))
    monkeypatch.setattr(chat_routes, "read_thread_refs", held.step("refs", ("", [])))


_FAIL_BODY = {
    "messages": [{"role": "user", "content": "hello"}],
    "page_context": {"title": "T", "path": "/t", "text": "x"},
    "resources": [{"pack": "files", "uri": "file://a"}],
    "thread_refs": ["earlier-chat"],
}


async def test_one_failing_step_fails_the_post_turn_as_before_and_cancels_the_rest(
    tmp_path, monkeypatch
):
    platform = create_app(str(tmp_path)).state.platform
    held = _Held(("refs",))
    _failing_steps(monkeypatch, held, RuntimeError("the apps did not answer"))
    router = _CountingRouter()
    monkeypatch.setattr(platform.router, "complete", router.complete)
    try:
        with pytest.raises(RuntimeError, match="the apps did not answer"):
            await chat_turn.run_chat_turn(platform, {}, ChatBody(**_FAIL_BODY))
        assert held.started["refs"].is_set(), "the later step should have started"
        assert held.cancelled == {"refs"}, "a step after the failure was left running"
        assert router.calls == 0
    finally:
        held.release.set()


async def test_one_failing_step_is_one_plain_error_frame_on_the_stream(tmp_path, monkeypatch):
    """The same frame a preparation fault has said since v1.312.0, and the
    step after the failed one does not outlive the turn (checked while the
    loop is still running: a loop torn down at the end of a request would
    cancel it anyway and prove nothing)."""
    platform = create_app(str(tmp_path)).state.platform
    cancelled: set[str] = set()

    async def refs(*a, **k):
        try:
            await asyncio.Event().wait()  # never answers on its own
        except asyncio.CancelledError:
            cancelled.add("refs")
            raise

    async def broken(*a, **k):
        raise RuntimeError("the apps did not answer")

    async def quick_attachments(*a, **k):
        return [], ""

    async def quick_page(*a, **k):
        return ""

    monkeypatch.setattr(chat_routes, "_prepare_attachments", quick_attachments)
    monkeypatch.setattr(chat_routes, "_page_context_section", quick_page)
    monkeypatch.setattr(chat_routes, "read_turn_resources", broken)
    monkeypatch.setattr(chat_routes, "read_thread_refs", refs)
    router = _CountingRouter()
    monkeypatch.setattr(platform.router, "stream", router.stream)
    out: list = []
    await _drain(await chat_routes.stream_chat_turn(platform, {}, ChatBody(**_FAIL_BODY)), out)
    frames = _parse_sse("".join(out))
    assert [ev for ev, _ in frames] == ["phase", "error"], frames
    detail = frames[-1][1]["detail"]
    assert "the apps did not answer" in detail
    assert "could not get ready to answer" in detail
    assert cancelled == {"refs"}, "a step after the failure was left running"
    assert router.calls == 0


async def test_a_failed_step_cancels_the_later_steps_at_once(tmp_path, monkeypatch):
    """The steps after a failed one are cancelled the moment it fails, not
    only when the turn unwinds: the attachments step (awaited first) waits
    to SEE the later step cancelled before it returns."""
    platform = create_app(str(tmp_path)).state.platform
    refs_cancelled = asyncio.Event()
    seen = {"cancelled_before_attachments_returned": False}

    async def refs(*a, **k):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            refs_cancelled.set()
            raise

    async def broken(*a, **k):
        raise RuntimeError("the apps did not answer")

    async def attachments(*a, **k):
        try:
            await asyncio.wait_for(refs_cancelled.wait(), 5)
            seen["cancelled_before_attachments_returned"] = True
        except asyncio.TimeoutError:
            pass
        return [], ""

    async def quick_page(*a, **k):
        return ""

    monkeypatch.setattr(chat_turn, "_prepare_attachments", attachments)
    monkeypatch.setattr(chat_turn, "_page_context_section", quick_page)
    monkeypatch.setattr(chat_turn, "_read_turn_resources", broken)
    monkeypatch.setattr(chat_turn, "_read_thread_refs", refs)
    monkeypatch.setattr(platform.router, "complete", _CountingRouter().complete)
    with pytest.raises(RuntimeError, match="the apps did not answer"):
        await chat_turn.run_chat_turn(platform, {}, ChatBody(**_FAIL_BODY))
    assert seen["cancelled_before_attachments_returned"], (
        "the step after the failed one kept running until the turn unwound"
    )


@pytest.mark.parametrize("lane", LANES)
def test_the_local_only_scope_is_visible_inside_a_parallel_step(tmp_path, monkeypatch, lane):
    """Each step runs in its own task (and the threaded ones in a worker
    thread): the turn's local-only scope must reach all of them."""
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    ws = tmp_path / "bound"
    ws.mkdir()
    scopes: dict[str, bool] = {}
    lock = threading.Lock()

    def record(name):
        with lock:
            scopes[name] = egress.current_scope().on

    _install_steps(platform, monkeypatch, hop=lambda name, value: value, ws=ws, record=record)
    _scripted_router(platform, monkeypatch, [], "ok.")
    _post_turn(client, lane, _body(ws, local_only=True))
    assert set(scopes) == set(ORDER), scopes
    off = sorted(name for name, on in scopes.items() if not on)
    assert off == [], f"{lane}: the local-only scope did not reach: {off}"


async def test_a_step_nobody_awaited_never_logs_an_unretrieved_exception():
    """The group reads every finished step's exception, so a failure in a
    step the turn never reached (it stopped or failed first) is not printed
    as "Task exception was never retrieved" at garbage collection."""
    group = chat_turn._PrepGroup()

    async def boom():
        raise RuntimeError("never awaited")

    group.start("late", boom())
    await asyncio.wait([group.take("late")])
    await group.close()
    task = group.take("late")
    assert task.done() and not task.cancelled()
    assert task._log_traceback is False  # noqa: SLF001 — what asyncio checks at GC


# --------------------------------------------------------------------------- #
# Review round (v1.332.0): an unknown skill starts nothing; a dropped
# connection leaves nothing running
# --------------------------------------------------------------------------- #
async def test_an_unknown_skill_is_refused_before_any_prep_step_starts(tmp_path, monkeypatch):
    """POST lane: the 404 for an unknown "/" skill lands BEFORE the group
    starts, so the bound folder is not made, the apps are not read and no
    attachment is OCR'd for a turn that never runs (the stream lane's
    ``_eager_checks`` refuses it before the response opens)."""
    from fastapi import HTTPException

    platform = create_app(str(tmp_path)).state.platform
    started: list[str] = []

    async def attachments(*a, **k):
        started.append("attachments")
        return [], ""

    async def resources(*a, **k):
        started.append("resources")
        return "", []

    def workspace(*a, **k):
        started.append("workspace")
        return (tmp_path, False)

    def arming(*a, **k):
        started.append("arming")
        return [], []

    monkeypatch.setattr(chat_turn, "_prepare_attachments", attachments)
    monkeypatch.setattr(chat_turn, "_read_turn_resources", resources)
    monkeypatch.setattr(chat_turn, "_resolve_tool_workspace", workspace)
    monkeypatch.setattr(chat_turn, "_resolve_armed_tools", arming)
    monkeypatch.setattr(platform.router, "complete", _CountingRouter().complete)
    body = ChatBody(**{
        "messages": [{"role": "user", "content": "hello"}],
        "resources": [{"pack": "files", "uri": "file://a"}],
        "attachments": ["a.pdf"],
        "skill": "no-such-skill-anywhere",
        "workspace_dir": str(tmp_path / "bound"),
    })
    with pytest.raises(HTTPException) as err:
        await chat_turn.run_chat_turn(platform, {}, body)
    assert err.value.status_code == 404
    assert "no such skill" in str(err.value.detail)
    await asyncio.sleep(0)  # one tick: a step started as a task would have run by now
    assert started == [], f"prep steps started for a refused turn: {started}"


async def test_the_stream_lane_refuses_an_unknown_skill_before_any_prep(tmp_path, monkeypatch):
    """Anti-drift: the stream lane's refusal is still the eager status code,
    raised before the generator exists (so no step can start)."""
    from fastapi import HTTPException

    platform = create_app(str(tmp_path)).state.platform
    started: list[str] = []

    async def attachments(*a, **k):
        started.append("attachments")
        return [], ""

    monkeypatch.setattr(chat_routes, "_prepare_attachments", attachments)
    body = ChatBody(**{
        "messages": [{"role": "user", "content": "hello"}],
        "skill": "no-such-skill-anywhere",
    })
    with pytest.raises(HTTPException) as err:
        await chat_routes.stream_chat_turn(platform, {}, body)
    assert err.value.status_code == 404
    assert started == []


@pytest.mark.parametrize("how", ["cancel", "aclose"])
async def test_a_dropped_connection_during_prep_leaves_no_step_running(
    tmp_path, monkeypatch, how
):
    """A client that goes away mid-preparation: Starlette CANCELS the task
    iterating the stream (``cancel``), or the consumer closes the generator
    at a frame (``aclose``). Either way every step still running is
    cancelled before the stream is gone, with no ``turn_id`` (no Stop
    handle) to help."""
    app = create_app(str(tmp_path))
    platform = app.state.platform
    held = _Held(("attachments", "page", "resources", "refs"))
    monkeypatch.setattr(chat_routes, "_prepare_attachments", held.step("attachments", ([], "")))
    monkeypatch.setattr(chat_routes, "_page_context_section", held.step("page", ""))
    monkeypatch.setattr(chat_routes, "read_turn_resources", held.step("resources", ("", [])))
    monkeypatch.setattr(chat_routes, "read_thread_refs", held.step("refs", ("", [])))
    router = _CountingRouter()
    monkeypatch.setattr(platform.router, "stream", router.stream)
    body = ChatBody(**{
        "messages": [{"role": "user", "content": "hello"}],
        "attachments": ["a.pdf"],
        "page_context": {"title": "T", "path": "/t", "text": "x"},
        "resources": [{"pack": "files", "uri": "file://a"}],
        "thread_refs": ["earlier-chat"],
    })
    gen = await chat_routes.stream_chat_turn(platform, {}, body)
    try:
        if how == "cancel":
            frames: list = []
            consumer = asyncio.ensure_future(_drain(gen, frames))
            for ev in held.started.values():
                await asyncio.wait_for(ev.wait(), 10)
            consumer.cancel()
            with pytest.raises(asyncio.CancelledError):
                await consumer
        else:
            # "reading_files" is said AFTER the steps start (attachments ride).
            while True:
                chunk = await gen.__anext__()
                if "reading_files" in chunk:
                    break
            for ev in held.started.values():
                await asyncio.wait_for(ev.wait(), 10)
            await gen.aclose()
        assert held.cancelled == {"attachments", "page", "resources", "refs"}, (
            f"{how}: steps left running: {set(held.started) - held.cancelled}"
        )
        assert router.calls == 0
    finally:
        held.release.set()
