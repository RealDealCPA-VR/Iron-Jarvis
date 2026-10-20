"""v1.360.0 integrate — Wave 10 "the final re-audit's regressions closed",
joined end to end.

Each lane proved its own half; these pins hold the SEAMS between them, through
the real app factory, the real chat lanes, the real notifier and the real
desktop/main.js functions under node. What the daemon really sends is written
to ONE fixture the dashboard's seam test feeds through the real bell and the
real model words: ``dashboard/__tests__/fixtures/wave10-seams-v13600.json``
(regenerate with ``IJ_REGEN_WAVE10_SEAMS=1`` after an intended change).

(a) ONE NOTICE PER OUTCOME, end to end (lane 10A + integrate). A failed
    scheduled task and a failed scheduled WORKFLOW each give "This PC" nothing
    while the bell rings them (one row each, one ping each — the dashboard
    half), the phone one line, and — with the app in the tray — main.js reads
    the daemon's real GET /audit answer and raises exactly ONE OS notice per
    outcome, in the bell's words. The ask class (integrate, lane 10A note g):
    an agent's ask and a parked workflow's question no longer toast "This PC"
    beside the bell's "N pending" ping. A goal iteration whose session ended
    needing the user reads "Needs you" on its row (note a). The tray notice
    is never a bare title (note f).
(b) CHAT MATCHES WHAT WAS MEANT, and the door is the Directory's (lane 10B):
    on a fresh install "check my inbox" offers ONE door, at the very address
    the dashboard's /marketplace (the Directory) lands on; a research sentence
    offers none.
(c) ONE NAME FOR THE USER'S MODEL SERVER (lane 10B): the reply's
    ``route.label`` is the machine's name, and the real GET /models rows carry
    the same name — the dashboard half proves the reply, the run page and the
    menu all read "DGX Spark" from exactly these rows (and "your model server"
    for an unnamed twin).
(d) ONE VOCABULARY (lane 10C + integrate): the words lane 10C parked for the
    integrator now read in the canon's words through the real routes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.comm.channels import DesktopChannel, MockChannel
from iron_jarvis.comm.notifier import bell_pings, is_workflow_step
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentState
from iron_jarvis.daemon import doors
from iron_jarvis.daemon.app import create_app

# The lanes' own harnesses (one helper per thing, never a copy).
from tests.test_chat_matches_meant_v13600 import (  # noqa: F401 — fixtures
    _clean,
    _name_the_machine,
    _post_turn,
    _stream_turn,
    _wire_server,
)
from tests.test_one_notice_v13600 import _lift

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "wave10-seams-v13600.json"
BELL = ROOT / "dashboard" / "components" / "NotificationBell.tsx"
RELOCATED = ROOT / "dashboard" / "lib" / "relocated.ts"
MAIN = ROOT / "desktop" / "main.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")

NO_DETAIL = "Open the dashboard for details."


@pytest.fixture
def desktop_sink(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(DesktopChannel, "sink", lambda title, message: sent.append(message))
    return sent


def _fake_run(state: AgentState, result: str):
    async def run(session, agent_def, *a, **kw):
        return SimpleNamespace(
            state=state,
            provider=session.provider,
            model=session.model,
            result=result,
            input_tokens=0,
            output_tokens=0,
        )

    return run


def _phone(app) -> MockChannel:
    for ch in app.state.platform.notifier._channels.values():
        if isinstance(ch, MockChannel):
            return ch
    raise AssertionError("no mock channel")


def _capture(platform, types: tuple[str, ...]) -> list[dict]:
    seen: list[dict] = []

    def handler(ev) -> None:
        if ev.type in types:
            seen.append({"type": ev.type, "session_id": ev.session_id, "payload": dict(ev.payload or {})})

    platform.event_bus.add_handler(handler)
    return seen


def _audit(client) -> list[dict]:
    """The daemon's answer at the path desktop/main.js reads (runOutcomePath)."""
    r = client.get("/audit", params={"type": "session.completed,workflow.completed", "since": "", "limit": "50"})
    assert r.status_code == 200, r.text
    return r.json()["entries"]


# --------------------------------------------------------------------------- #
# The fixture: real answers with their volatile ids and times made stable
# --------------------------------------------------------------------------- #

_ID_RX = re.compile(r"^(session|wfrun|evt|wf|run)_[0-9a-f]{8,}$")


def _stable(obj, names: dict[str, str]):
    """Replace generated ids and timestamps with stable names, keeping every
    relation (a workflow's ``sessions`` names its step by the same token)."""
    if isinstance(obj, dict):
        return {k: _stable(v, names) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_stable(v, names) for v in obj]
    if isinstance(obj, str) and _ID_RX.match(obj):
        if obj not in names:
            names[obj] = f"{obj.split('_', 1)[0]}_{len(names) + 1:02d}"
        return names[obj]
    return obj


def _with_ts(entries: list[dict]) -> list[dict]:
    """Audit entries newest first, with distinct stable times (main.js reads
    them oldest first and compares each to the window-seen time)."""
    out = []
    n = len(entries)
    for i, e in enumerate(entries):
        out.append({**e, "ts": f"2026-10-20T00:01:{(n - i):02d}"})
    return out


def _check_or_write(key: str, value) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    if os.environ.get("IJ_REGEN_WAVE10_SEAMS") == "1":
        data.setdefault(
            "_about",
            "What the REAL daemon sends for the wave-10 seams (tests/test_integrate_wave10_v13600.py "
            "writes it with IJ_REGEN_WAVE10_SEAMS=1; dashboard/__tests__/wave10-seams-v13600.test.tsx "
            "feeds it through the real bell and the real model words). Ids and times are made stable.",
        )
        data[key] = value
        FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    assert key in data, f"the fixture has no {key!r}: regenerate with IJ_REGEN_WAVE10_SEAMS=1"
    assert data[key] == value, f"the daemon's {key!r} changed: if on purpose, regenerate with IJ_REGEN_WAVE10_SEAMS=1"


# --------------------------------------------------------------------------- #
# (a) one notice per outcome, end to end
# --------------------------------------------------------------------------- #


def _fire_failed_workflow(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    platform = app.state.platform
    monkeypatch.setattr(platform.orchestrator.runtime, "run", _fake_run(AgentState.FAILED, "The report folder is missing."))
    seen = _capture(platform, (EventType.SESSION_COMPLETED, EventType.WORKFLOW_COMPLETED))
    r = client.post(
        "/workflows",
        json={"name": "Weekly report", "steps": [{"name": "Draft", "agent": "builder", "task": "draft the report"}]},
    )
    assert r.status_code < 300, r.text
    r = client.post(
        "/schedules",
        json={"name": "weekly", "cron": "0 8 * * 1", "kind": "workflow", "payload": {"workflow": "Weekly report"}},
    )
    assert r.status_code == 200, r.text
    phone = _phone(app)
    before = len(phone.sent)
    body = client.post("/schedules/weekly/run").json()
    return SimpleNamespace(body=body, events=seen, phone=phone.sent[before:], audit=_audit(client))


def test_a_failed_scheduled_workflow_is_one_notice_on_this_pc_and_one_line_on_the_phone(
    tmp_path, monkeypatch, desktop_sink
):
    out = _fire_failed_workflow(tmp_path, monkeypatch)
    assert out.body["last_status"] == "error", out.body
    # This PC: NOTHING from the daemon — the bell (window open) or main.js
    # (tray) is the one notice.
    assert desktop_sink == [], desktop_sink
    # The phone: the schedule's one line, never the generic run alerts too.
    assert len(out.phone) == 1 and out.phone[0].startswith("Schedule “weekly”: FAILED — "), out.phone
    # The bell's half: the step is a workflow step and the failed workflow
    # rings — the dashboard folds the step's row into the workflow's.
    [step] = [e for e in out.events if e["type"] == EventType.SESSION_COMPLETED]
    [wf] = [e for e in out.events if e["type"] == EventType.WORKFLOW_COMPLETED]
    assert is_workflow_step(step["payload"]) and bell_pings(step)
    assert bell_pings(wf) and step["session_id"] in wf["payload"]["sessions"]
    names: dict[str, str] = {}
    _check_or_write(
        "failed_workflow",
        {"events": _stable([wf, step], names), "audit": _with_ts(_stable(out.audit, names))},
    )


def _fire_failed_task(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    platform = app.state.platform
    monkeypatch.setattr(platform.orchestrator.runtime, "run", _fake_run(AgentState.FAILED, "Stopped at the inbox it could not open."))
    seen = _capture(platform, (EventType.SESSION_COMPLETED,))
    client.post(
        "/schedules",
        json={"name": "morning-brief", "cron": "0 7 * * *", "kind": "task", "payload": {"task": "Write the morning brief."}},
    )
    phone = _phone(app)
    before = len(phone.sent)
    body = client.post("/schedules/morning-brief/run").json()
    return SimpleNamespace(body=body, events=seen, phone=phone.sent[before:], audit=_audit(client))


def test_a_failed_scheduled_task_is_one_notice_too_and_its_words_reach_the_fixture(
    tmp_path, monkeypatch, desktop_sink
):
    out = _fire_failed_task(tmp_path, monkeypatch)
    assert out.body["last_status"] == "error", out.body
    assert desktop_sink == [], desktop_sink
    assert len(out.phone) == 1, out.phone
    [ev] = out.events
    assert bell_pings(ev)
    names: dict[str, str] = {}
    _check_or_write("failed_task", {"events": _stable([ev], names), "audit": _with_ts(_stable(out.audit, names))})


_PLAN = r"""
const input = JSON.parse(require("fs").readFileSync(process.argv[2], "utf-8"));
const out = {};
for (const [name, entries] of Object.entries(input.cases)) {
  const closed = planRunOutcomeNotifications({}, entries, false, 0);
  const open = planRunOutcomeNotifications({}, entries, true, 0);
  // A second read of the same answer must never say it again.
  const again = planRunOutcomeNotifications(closed.state, entries, false, 0);
  out[name] = { closed: closed.notify, open: open.notify, again: again.notify };
}
out.noSummary = runOutcomeNotice({
  id: "evt_x", type: "session.completed", session_id: "session_x", ts: "2026-10-20T00:00:01",
  payload: { status: "failed", origin: "remote:desk", outcome: null },
});
process.stdout.write(JSON.stringify(out) + "\n");
"""


def _node(tmp_path: Path, cases: dict) -> dict:
    data = tmp_path / "in.json"
    data.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    script = tmp_path / "plan.js"
    script.write_text(_lift() + "\n" + _PLAN, encoding="utf-8")
    proc = subprocess.run(["node", str(script), str(data)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@requires_node
def test_in_the_tray_main_js_says_each_failed_outcome_once_from_the_real_audit(tmp_path, monkeypatch):
    """The app in the tray: main.js reads the daemon's REAL GET /audit answer
    (a failed scheduled workflow = its step + its workflow; a failed
    scheduled task) and raises exactly ONE OS notice per outcome, in the
    bell's words — none while a window is open, none twice."""
    wf = _fire_failed_workflow(tmp_path / "wf", monkeypatch)
    task = _fire_failed_task(tmp_path / "task", monkeypatch)
    out = _node(tmp_path, {"workflow": wf.audit, "task": task.audit})
    assert [n["title"] for n in out["workflow"]["closed"]] == ["Your workflow Weekly report could not finish"], out
    assert out["workflow"]["closed"][0]["href"] == "/workflows"
    assert [n["title"] for n in out["task"]["closed"]] == ["Your schedule morning-brief could not finish"], out
    # The run's own sentence, as the daemon sent it (the bell's row body).
    assert out["task"]["closed"][0]["body"] == task.events[0]["payload"]["summary"], out
    for name in ("workflow", "task"):
        assert out[name]["open"] == [], f"{name}: the bell had it while a window was open"
        assert out[name]["again"] == [], f"{name}: said twice"
    # (integrate, lane 10A note f) never a bare title.
    assert out["noSummary"]["body"] == NO_DETAIL


def test_the_tray_notice_falls_back_to_the_bells_own_ping_words():
    """One fallback sentence, two places: the bell's ping and main.js."""
    bell = BELL.read_text(encoding="utf-8").replace("\r\n", "\n")
    main = MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert f'latest.body || "{NO_DETAIL}"' in bell
    assert f'const RUN_OUTCOME_NO_DETAIL = "{NO_DETAIL}";' in main
    assert "body: runOutcomeClip(p.summary) || RUN_OUTCOME_NO_DETAIL," in main


def test_a_parked_scheduled_workflow_is_one_notice_on_this_pc(tmp_path, desktop_sink):
    """A needs-you SCHEDULED workflow (parked on its question) while a window
    is open: the bell's waiting row ("N pending") is the one desktop notice.
    The schedule's own "needs you" line and the engine's park message both
    leave "This PC" out (lane 10A + integrate note g); the phone still gets
    the answerable question."""
    app = create_app(str(tmp_path / "home"))
    client = TestClient(app)
    r = client.post("/workflows", json={"name": "gated", "steps": [{"name": "Approve", "kind": "ask", "message": "Send it?"}]})
    assert r.status_code < 300, r.text
    r = client.post(
        "/schedules",
        json={"name": "fire-gated", "cron": "0 8 * * *", "kind": "workflow", "payload": {"workflow": "gated"}},
    )
    assert r.status_code == 200, r.text
    phone = _phone(app)
    before = len(phone.sent)
    body = client.post("/schedules/fire-gated/run").json()
    assert body["last_status"] == "needs_you", body
    assert desktop_sink == [], desktop_sink
    sent = phone.sent[before:]
    assert any(m.startswith("Workflow “gated” needs you: Send it?") for m in sent), sent
    # The bell's half: the run is in the list the bell polls.
    runs = client.get("/workflows/runs", params={"status": "waiting", "slim": "true", "limit": "200"}).json()
    waiting = [r for r in runs.get("runs", []) if r.get("status") == "waiting"]
    assert len(waiting) == 1, runs
    names: dict[str, str] = {}
    keep = ("id", "workflow_name", "status", "waiting_json")  # what the bell's parseWaitingRun reads
    assert waiting[0].get("workflow_name") == "gated", waiting[0]
    _check_or_write("parked_workflow", {"runs": _stable([{k: w.get(k) for k in keep} for w in waiting], names)})


def test_a_notify_step_still_reaches_this_pc(tmp_path, desktop_sink):
    """The other half of note g: a workflow's own NOTIFY step is the user's
    message — nothing else says it — so it keeps every destination."""
    app = create_app(str(tmp_path / "home"))
    engine = getattr(app.state.platform, "workflow_engine", None)
    if engine is None:
        from iron_jarvis.workflows.engine import WorkflowEngine

        engine = WorkflowEngine(app.state.platform)
    ok, _detail = engine._deliver("Weekly report sent.")
    assert ok and desktop_sink == ["Weekly report sent."], desktop_sink
    # ... and the park path is the only one that leaves This PC out.
    ok, _detail = engine._deliver("Workflow “gated” needs you: Send it?", skip_desktop=True)
    assert desktop_sink == ["Weekly report sent."], desktop_sink


def test_an_agent_ask_is_one_notice_on_this_pc(tmp_path, desktop_sink):
    """An agent's ask: the bell's "N pending" ping (window open) and main.js's
    ask watcher (tray) say it on this PC; "This PC" adds nothing. The phone
    gets its one answerable line, as before."""
    app = create_app(str(tmp_path / "home"))
    notifier = app.state.platform.notifier
    phone = _phone(app)
    before = len(phone.sent)
    notifier.on_event({"type": EventType.APPROVAL_REQUESTED, "session_id": "session_a1", "payload": {"tool": "shell"}})
    assert desktop_sink == [], desktop_sink
    assert len(phone.sent[before:]) == 1 and "shell" in phone.sent[-1], phone.sent


def test_the_bells_count_ping_is_the_main_windows_only():
    """The "N pending" ping skips a popped-out window like the activity ping
    does: one notice per ask on this PC, never once per window."""
    bell = BELL.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "if (count > prev && !inPopout()) {" in bell


# --------------------------------------------------------------------------- #
# (b) the door chat offers is the Directory's one address
# --------------------------------------------------------------------------- #


def _marketplace_target() -> str:
    ts = RELOCATED.read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r'"/marketplace":\s*\{\s*base:\s*"([^"]+)",\s*extra:\s*\{\s*section:\s*"([^"]+)"\s*\}\s*\}', ts)
    assert m, "lib/relocated.ts no longer maps /marketplace"
    return f"{m.group(1)}?section={m.group(2)}"


def test_the_connect_door_opens_where_the_directory_lives():
    assert doors._APPS_FIX_HREF == _marketplace_target()


@pytest.fixture()
def fresh(tmp_path):
    return create_app(str(tmp_path / "fresh"))


@pytest.mark.parametrize("lane", ["stream", "post"])
def test_own_mail_offers_one_door_to_the_directory_and_research_offers_none(fresh, lane):
    turn = _stream_turn if lane == "stream" else _post_turn
    platform = fresh.state.platform
    out, _ = turn(platform, "check my inbox")
    assert [d["href"] for d in out["doors"]] == [_marketplace_target()], out["doors"]
    assert "Gmail" in out["doors"][0]["label"] and "Microsoft 365" in out["doors"][0]["label"]
    for text in ("explain how postgres indexes work", "stripe the table rows", "how do I connect my printer to wifi"):
        out, model = turn(platform, text)
        assert out["doors"] == [], (text, out["doors"])
    # The Guide is not armed for a how-to about something else (stream lane
    # arms; the POST lane's armed set is read off the same selector).
    if lane == "stream":
        assert not {"guide_search", "app_search", "app_status"} & set(model.tools[0] or [])


# --------------------------------------------------------------------------- #
# (c) one name for the user's model server: the reply's and the catalog's
# --------------------------------------------------------------------------- #


def _machine(tmp_path, monkeypatch, label: str) -> dict:
    from tests.test_chat_matches_meant_v13600 import _done_frame

    with TestClient(create_app(str(tmp_path / label.replace(" ", "-")))) as c:
        platform = c.app.state.platform
        _name_the_machine(platform, label)
        _wire_server(c, monkeypatch)
        body = {"messages": [{"role": "user", "content": "hi"}], "provider": "custom", "model": "fleet"}
        flat = c.post("/chat", json=body).json()["route"]
        streamed = _done_frame(c.post("/chat/stream", json=body).text)["route"]
        rows = [
            m
            for m in c.get("/models").json().get("models", [])
            if m.get("provider") in ("custom", "fleet-custom")
        ]
    assert flat == streamed or flat["label"] == streamed["label"]
    keep = ("provider", "model", "label")
    return {"route": {k: flat.get(k) for k in keep if k in flat}, "models": rows}


def test_the_reply_and_the_catalog_carry_the_same_machine_name(tmp_path, monkeypatch):
    named = _machine(tmp_path, monkeypatch, "DGX Spark")
    assert named["route"]["label"] == "DGX Spark"
    assert any(r.get("name") == "DGX Spark" for r in named["models"]), named["models"]
    unnamed = _machine(tmp_path, monkeypatch, "Custom endpoint")
    # The registry's default label is not a name: the reply carries none.
    assert not unnamed["route"].get("label"), unnamed["route"]
    _check_or_write("machine", {"named": named, "unnamed": unnamed})


# --------------------------------------------------------------------------- #
# (d) the words lane 10C parked now read in the canon's words
# --------------------------------------------------------------------------- #


def test_an_extension_refusal_says_extension(tmp_path):
    with TestClient(create_app(str(tmp_path / "h"))) as c:
        for body, words in (
            ({"name": "web", "transport": "http"}, "an extension reached at a web address needs a url"),
            ({"name": "s", "transport": "stdio"}, "an extension that runs on this PC needs a command"),
        ):
            r = c.post("/mcp/servers", json=body)
            assert r.status_code == 422, r.text
            detail = json.dumps(r.json())
            assert words in detail and "pack" not in detail, detail


def test_a_terminal_tool_refusal_says_terminal(tmp_path):
    import asyncio

    from iron_jarvis.tools.pane_tools import PaneReadTool

    app = create_app(str(tmp_path / "h"))
    tool = PaneReadTool(app.state.platform.terminals)
    res = asyncio.run(tool.execute({"pane": "nope"}, SimpleNamespace(workspace=str(tmp_path), session_id="s")))
    assert res.ok is False and "Build terminal" in res.error and "pane named" not in res.error, res.error


def test_plug_in_is_retired_in_the_list_every_scan_reads():
    """(integrate, lane 10C note C) VOCABULARY.md retired "plug-in" in
    v1.216.0, but the scans' list had no row for it, so none of the three
    could see one. The daemon's answer scan now catches it (and the
    dashboard's and main.js's read the same list)."""
    from tests.test_vocabulary_beyond_dashboard_v13580 import RETIRED, _hits
    from tests.test_vocabulary_daemon_answers_v13600 import answer_strings

    words = dict(RETIRED)
    assert "plug-in" in words, [w for w, _ in RETIRED]
    planted = 'def f():\n    raise ValueError("the plug-in did not start")\n'
    found = {w for _line, text in answer_strings(planted) for w in _hits(text)}
    assert found == {"plug-in"}, found
