"""Leap 06-a: rehearse a schedule or a rule, and see the list.

Done when: rehearsing a schedule that renames files leaves every file
untouched and returns the exact list of renames, read from the ledger.

Driven through the REAL daemon (``create_app``): the schedule's own door
(``platform.rehearse_scheduled`` → ``_dispatch_scheduled``), the real
orchestrator, runtime, registry and ledger. Only the model is a double (a
scripted stream). Also pinned here:

* the sentence equals the rows (counted by code from the ledger);
* a rehearsal's run rings no bell and sends no notice;
* a rule's rehearsal leaves the rule's row alone (no fire counted);
* a rule that starts a workflow is refused in words, nothing ran;
* Keep it stamps ``rehearsed_at`` on the subject; Stop stops it.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import select

from iron_jarvis.comm.notifier import bell_pings, is_rehearsal_run
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session as SessionRow
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.tools import rehearsal_runs as RR

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(120)]

FILES = {"2025_W2_smith.pdf": b"%PDF smith", "2025_W2_jones.pdf": b"%PDF jones", "notes.txt": b"keep me"}
RENAMES = [
    ("2025_W2_smith.pdf", "Smith - W2 2025.pdf"),
    ("2025_W2_jones.pdf", "Jones - W2 2025.pdf"),
]


def _scripted(steps: list[list[ToolCall]]):
    state = {"n": 0}

    async def fake_stream(*, system=None, messages=None, tools=None, **kw):
        i = state["n"]
        state["n"] += 1
        if i < len(steps):
            resp = LLMResponse(text="", tool_calls=list(steps[i]))
        else:
            resp = LLMResponse(text="Renamed the files.")
            yield {"type": "text", "text": "Renamed the files."}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    return fake_stream


@pytest.fixture
def daemon(tmp_path):
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    events: list[dict[str, Any]] = []
    real = p.event_bus.publish

    async def spy(type, payload=None, session_id=None, **kw):
        events.append({"type": str(getattr(type, "value", type)), "payload": payload or {},
                       "session_id": session_id})
        return await real(type, payload, session_id=session_id, **kw)

    p.event_bus.publish = spy
    sent: list[Any] = []
    p.notifier.notify = lambda *a, **k: sent.append((a, k))  # type: ignore[method-assign]
    folder = tmp_path / "inbox"
    folder.mkdir()
    for name, data in FILES.items():
        (folder / name).write_bytes(data)
    return p, events, sent, folder


async def _settled(p, rid: str, seconds: float = 60.0) -> dict[str, Any]:
    # Polls a condition (never asserts a duration).
    deadline = time.monotonic() + seconds
    while True:
        view = await asyncio.to_thread(RR.report, p, rid)
        if view["status"] != "running" or time.monotonic() > deadline:
            return view
        await asyncio.sleep(0.05)


def _snapshot(folder) -> dict[str, bytes]:
    return {f.name: f.read_bytes() for f in sorted(folder.iterdir())}


async def test_a_schedule_that_renames_files_changes_nothing_and_lists_each_rename(daemon):
    p, events, sent, folder = daemon
    p.scheduler.add_task(
        "Sort the W2s", "0 8 * * *", kind="task",
        payload={"task": "Rename the W2 PDFs to 'Name - W2 2025.pdf'.",
                 "workspace_root": str(folder), "notify": False},
    )
    p.router.stream = _scripted([[
        ToolCall(id=f"c{i}", name="rename_file", arguments={"path": src, "new_path": dst})
        for i, (src, dst) in enumerate(RENAMES)
    ]])
    before = _snapshot(folder)

    started = await RR.start(p, "schedule", "Sort the W2s")
    assert started["status"] == "running"
    view = await _settled(p, started["id"])

    assert view["status"] == "ready", view
    assert _snapshot(folder) == before, "a rehearsal changed the folder"
    renames = [w["words"] for w in view["would"] if w["kind"] == "move"]
    assert renames == [f"rename {s} to {d}" for s, d in RENAMES]
    assert view["sentence"] == "This would rename 2 files. Nothing was changed."
    assert view["counts"] == {"move": 2}
    # The sentence is counted from the ledger's rows, not from the reply.
    sid = [e for e in events if e["type"] == "session.completed"][-1]["session_id"]
    with session_scope(p.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.session_id == sid)))
        outcome = db.get(SessionRow, sid).outcome
    assert sorted(r.tool for r in rows if r.rehearsed and r.would == "run" and r.tool == "rename_file") == [
        "rename_file", "rename_file"
    ]
    assert outcome == "rehearsed"


async def test_a_rehearsal_rings_no_bell_and_sends_no_notice(daemon):
    p, events, sent, folder = daemon
    p.scheduler.add_task(
        "Nightly tidy", "0 2 * * *", kind="task",
        payload={"task": "Tidy the inbox folder.", "workspace_root": str(folder)},
    )
    p.router.stream = _scripted([[ToolCall(id="w", name="write_file",
                                           arguments={"path": "summary.txt", "content": "x"})]])
    started = await RR.start(p, "schedule", "Nightly tidy")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready"
    done = [e for e in events if e["type"] == "session.completed"]
    assert done and all(is_rehearsal_run(e["payload"]) for e in done)
    assert not any(bell_pings(e) for e in done), "a rehearsal rang the bell"
    await asyncio.sleep(0)
    assert sent == [], "a rehearsal sent a notice"
    assert not (folder / "summary.txt").exists()
    # The schedule's own row is not a fire.
    row = p.scheduler.get("Nightly tidy")
    assert (row.last_status, row.last_session_id) == ("", "")


async def test_keep_it_stamps_the_schedule(daemon):
    p, events, sent, folder = daemon
    p.scheduler.add_task("Keepme", "0 9 * * *", kind="task",
                         payload={"task": "Say hello.", "workspace_root": str(folder), "notify": False})
    p.router.stream = _scripted([])
    started = await RR.start(p, "schedule", "Keepme")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready" and view["sentence"].startswith("This would change nothing.")
    assert p.scheduler.get("Keepme").rehearsed_at is None
    kept = await RR.keep(p, started["id"])
    assert kept["kept_at"]
    assert p.scheduler.get("Keepme").rehearsed_at is not None


async def test_a_rule_rehearses_on_a_sample_and_its_row_is_not_a_fire(daemon):
    """A rule's run is a supervisor: the write it tries itself is refused for
    real (its roster), and listed as refused, not as something it would do."""
    p, events, sent, folder = daemon
    rule = p.reflex.add(name="Client mail", source="webhook", match="client-mail", action="session",
                        target="", task_template="File the client's message: {text}", enabled=True)
    p.router.stream = _scripted([[ToolCall(id="w", name="write_file",
                                           arguments={"path": "client-note.txt", "content": "filed"})]])
    started = await RR.start(p, "rule", "client mail")
    view = await _settled(p, started["id"])
    assert view["status"] == "ready", view
    assert view["sample"] == "a made-up signal"
    assert [(w["words"], w["would"]) for w in view["refused"]] == [("write client-note.txt", "refuse")]
    assert view["sentence"] == "This would change nothing. Nothing was changed."
    again = p.reflex.get(rule.id)
    assert again.fire_count == 0 and again.last_result in (None, "")
    assert not [e for e in events if e["type"] == "reflex.fired"]


async def test_a_rule_that_starts_a_workflow_is_refused_in_words(daemon):
    p, events, sent, folder = daemon
    p.reflex.add(name="Run triage", source="webhook", match="triage", action="workflow",
                 target="Triage", task_template="", enabled=True)
    view = await RR.start(p, "rule", "Run triage")
    assert view["status"] == "failed"
    assert "cannot be rehearsed yet" in view["error"]
    assert not [e for e in events if e["type"] == "session.created"]


async def test_an_unknown_name_lists_the_ones_there_are(daemon):
    p, *_ = daemon
    p.scheduler.add_task("Morning digest", "0 7 * * *", kind="task", payload={"task": "Digest."})
    with pytest.raises(RR.RehearsalRefused) as err:
        await RR.start(p, "schedule", "evening digest")
    assert err.value.status == 404 and "Morning digest" in err.value.words


# --------------------------------------------------------------------------- #
# The desktop tray (desktop/main.js under node) says nothing for a rehearsal
# --------------------------------------------------------------------------- #

MAIN = Path(__file__).resolve().parents[1] / "desktop" / "main.js"


def _tray(tmp_path, events: list[dict]) -> list:
    src = MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = src.index("const RUN_OUTCOME_AUDIT_TYPES")
    tail = src.index("function runOutcomePath", start)
    end = src.index("\n}\n", tail) + 3
    script = tmp_path / "leap06a_tray.js"
    script.write_text(
        src[start:end]
        + "\nconst input = JSON.parse(process.argv[2]);\n"
        + "process.stdout.write(JSON.stringify({ out: input.events.map((e) => runOutcomeNotice(e)) }) + '\\n');\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["node", str(script), json.dumps({"events": events})],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])["out"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_tray_says_nothing_for_a_rehearsal(tmp_path):
    def ev(i: str, **extra) -> dict:
        return {
            "id": f"e{i}", "type": "session.completed", "ts": "2026-11-20T10:00:00+00:00",
            "session_id": f"s{i}",
            "payload": {"status": "failed", "summary": "x", "origin": "schedule:nightly",
                        "project_id": None, "outcome": None, "chat_thread_id": None, **extra},
        }

    quiet, finished, control = _tray(tmp_path, [
        ev("1", rehearsal=True),
        ev("2", status="completed", outcome="rehearsed", rehearsal=True),
        ev("3"),
    ])
    assert quiet is None and finished is None
    assert control is not None, "control: the same failed schedule run is said from the tray"
