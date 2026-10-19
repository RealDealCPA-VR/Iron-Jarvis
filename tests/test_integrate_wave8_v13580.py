"""v1.358.0 integrate — the wave-8 seams, driven through the real app.

Wave 8 closed the re-audit's regressions in five lanes. Each lane pinned its
own half; these pins hold the JOINS, and the integrator's follow-ups:

(a) ONE VERDICT PER RUN (8A + integrate). A scheduled workflow parked on the
    user reads "Needs you" on its row and the home line counts it — and once
    the person answers and the run finishes, the row says how it ended
    (``scheduling/parked_runs`` + the ``workflow.completed`` handler), so
    the Schedules row never contradicts the workflow's own run page. A later
    fire keeps its own say. The answered row is written to
    ``fixtures/wave8-seams-v13580.json`` for the dashboard half.
(c) CHAT PICKS THE RIGHT TOOLS (8B), both lanes lock-step: a research
    question about a connected app reaches the web AND the app's 24 armed
    tools are the ones the message asks about — the same set in both lanes.
(d) THE DIRECTORY AND CHAT NEVER CONTRADICT (8D + integrate): a Microsoft
    365 that has not signed in reads "Sign in to Microsoft to finish" on its
    card AND in the chat's apps section; signed in, both say connected. One
    Pixio key sentence everywhere (the media provider, the creative tools,
    the 401, the portrait door) names Settings › Connections.
(e) A FRESH INSTALL STAYS ON THIS PC (8E + integrate): ``lan_access`` is off,
    the door's sentence names the setting, and ``serve --host 0.0.0.0``
    (the setting on) still finds a running copy through loopback.

Regenerate the fixture with ``IJ_REGEN_WAVE8_SEAMS=1``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
import typer
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.connectors import app_names
from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.scheduling import run_outcome as ro
from iron_jarvis.scheduling.models import ScheduledTaskRecord
from iron_jarvis.scheduling.parked_runs import FILE_NAME, ParkedRuns

# The lanes' own harnesses (fixtures are registered by import).
from tests.test_chat_picks_tools_v13580 import _connect_github, _scripted, _send
from tests.test_connections_honest_v13580 import (  # noqa: F401 — pytest fixtures
    CARD_ID,
    _connect_ms365,
    _fresh,
    _platform,
    _row,
    ms365,
    signin_file,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "wave8-seams-v13580.json"
REGEN = os.environ.get("IJ_REGEN_WAVE8_SEAMS") == "1"


def _hold(key: str, real) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    if REGEN:
        data[key] = real
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        return
    assert key in data, f"run IJ_REGEN_WAVE8_SEAMS=1 once: the fixture has no {key!r}"
    assert real == data[key], (
        f"the daemon no longer sends what {FIXTURE.name}[{key!r}] says — regenerate it "
        "if the change is intended, and update the dashboard half"
    )


# =========================================================================== #
# (a) one verdict per run: a parked scheduled workflow settles when answered
# =========================================================================== #
def _schedule_row(client: TestClient, name: str) -> dict:
    return next(r for r in client.get("/schedules").json()["schedules"] if r["name"] == name)


def _park(client: TestClient, schedule: str = "fire-gated") -> str:
    """A saved workflow whose only step asks the user, fired by its schedule
    through Run now; returns the parked run's id."""
    r = client.post(
        "/workflows",
        json={"name": "gated", "steps": [{"name": "Approve", "kind": "ask", "message": "Send it?"}]},
    )
    assert r.status_code < 300, r.text
    r = client.post(
        "/schedules",
        json={"name": schedule, "cron": "0 8 * * *", "kind": "workflow", "payload": {"workflow": "gated"}},
    )
    assert r.status_code == 200, r.text
    body = client.post(f"/schedules/{schedule}/run").json()
    assert body["last_status"] == "needs_you", body
    runs = client.get("/workflows/runs", params={"status": "waiting"}).json()["runs"]
    assert len(runs) == 1, runs
    return runs[0]["id"]


def _answer_and_wait(client: TestClient, run_id: str, schedule: str, want: str) -> dict:
    r = client.post(f"/workflows/runs/{run_id}/answer", json={"answer": "yes"})
    assert r.status_code < 300, r.text
    deadline = time.monotonic() + 30
    row = _schedule_row(client, schedule)
    while row["last_status"] != want and time.monotonic() < deadline:
        time.sleep(0.1)
        row = _schedule_row(client, schedule)
    return row


def test_a_parked_schedule_settles_its_row_when_the_person_answers(tmp_path):
    """THE SEAM (8A follow-up 1): before the integrator, the row kept "Needs
    you" — and the home line "1 scheduled task needs a look" — after the
    person answered and the run finished, until the next fire. MUTATION:
    drop ``event_bus.add_handler(_settle_parked_workflow)`` -> the row never
    leaves ``needs_you`` (red)."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        run_id = _park(client)
        assert client.get("/ui/status-line").json()["short_schedules"] == 1
        link_before = _schedule_row(client, "fire-gated")["last_session_id"]

        row = _answer_and_wait(client, run_id, "fire-gated", "ok")
        run = client.get(f"/workflows/runs/{run_id}").json()
        assert run["status"] == "completed", run
        assert row["last_status"] == "ok", row
        assert row["last_detail"] == ro.workflow_answered("completed", "gated")[1]
        assert row["last_session_id"] == link_before, "the schedule's link is kept"
        assert client.get("/ui/status-line").json()["short_schedules"] == 0
        _hold("parked_then_answered", {k: row[k] for k in ("name", "last_status", "last_detail")})


def test_a_later_fire_keeps_its_own_say(tmp_path):
    """A row that no longer holds the parked verdict (a later fire wrote its
    own) is never restamped by the old run finishing."""
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        run_id = _park(client)
        platform = client.app.state.platform
        with session_scope(platform.engine) as db:
            rec = db.exec(select(ScheduledTaskRecord).where(ScheduledTaskRecord.name == "fire-gated")).one()
            rec.last_status, rec.last_detail = "error", "The run failed: a later fire."
            db.add(rec)
            db.commit()
        r = client.post(f"/workflows/runs/{run_id}/answer", json={"answer": "yes"})
        assert r.status_code < 300, r.text
        deadline = time.monotonic() + 30
        while client.get(f"/workflows/runs/{run_id}").json()["status"] != "completed":
            assert time.monotonic() < deadline, "the answered run never finished"
            time.sleep(0.1)
        time.sleep(0.3)  # the handler runs right after the run's own event
        row = _schedule_row(client, "fire-gated")
        assert (row["last_status"], row["last_detail"]) == ("error", "The run failed: a later fire.")


def test_the_parked_map_is_per_schedule_and_survives_a_restart(tmp_path):
    first = ParkedRuns(tmp_path)
    first.park("nightly", "wfrun_a", "gated")
    first.park("nightly", "wfrun_b", "gated")  # a newer park replaces the older
    again = ParkedRuns(tmp_path)  # a restart reads the file
    assert again.settle("wfrun_a") is None
    assert again.settle("wfrun_b") == ("nightly", "gated")
    assert again.settle("wfrun_b") is None, "settled once"
    again.park("weekly", "wfrun_c", "gated")
    again.clear("weekly")
    assert ParkedRuns(tmp_path).settle("wfrun_c") is None
    assert (tmp_path / FILE_NAME).exists()


def test_the_answered_words_and_the_asked_reason_read_as_sentences():
    assert ro.workflow_answered("completed", "gated") == (
        ro.STATUS_OK, "Your workflow “gated” finished after your answer.")
    status, detail = ro.workflow_answered("failed", "gated")
    assert status == ro.STATUS_ERROR and detail.startswith("The run failed: your workflow “gated”")
    assert ro.workflow_answered("cancelled", "gated")[0] == ro.STATUS_CANCELLED
    assert ro.workflow_answered("waiting", "gated") is None
    # 8A review follow-up 3: never "...in time. the run ended failed".
    assert ro.failed_reason("", "failed", asked=True) == ro.ASK_EXPIRED_REASON
    # 8A review follow-up 2: the row never shows the engine's internal id.
    assert "goal_7f" not in ro.goal_skipped_detail("unknown goal 'goal_7f3a'")


# =========================================================================== #
# (c) chat arms the right app tools AND still reaches the web, both lanes
# =========================================================================== #
RESEARCH = "look up the github api docs on how to create a pull request"


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_research_about_a_connected_app_reaches_the_web_and_the_right_tools(tmp_path, monkeypatch, lane):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    _connect_github(platform)  # GitHub's own 44 tools: more than the 24 slots
    seen = _scripted(platform, monkeypatch)
    _send(client, lane, RESEARCH, connectors=["github"])
    tools = seen["tools"][0]
    assert "web_search" in tools and "web_fetch" in tools, tools  # 8B (1)
    github = [t for t in tools if t.startswith("mcp__github__")]
    assert len(github) == chat_turn._MAX_CONNECTOR_TOOLS
    assert "mcp__github__create_pull_request" in github, github  # 8B (2): the message's tool


def test_both_lanes_arm_the_same_tools_for_the_same_message(tmp_path, monkeypatch):
    armed = {}
    for lane in ("post", "stream"):
        client = TestClient(create_app(str(tmp_path / lane)))
        platform = client.app.state.platform
        _connect_github(platform)
        seen = _scripted(platform, monkeypatch)
        _send(client, lane, RESEARCH, connectors=["github"])
        armed[lane] = sorted(
            t for t in seen["tools"][0] if t.startswith("mcp__github__") or t in ("web_search", "web_fetch")
        )
    # THE SEAM (integrate): the stream lane's apps-by-name arming counted
    # only its own additions against the cap, so GitHub turned on AND named
    # read 44 tools there and 24 in the POST lane. MUTATION: drop the per-app
    # room in ``named_app_ask_tools`` -> 44 != 24 (red).
    assert armed["post"] == armed["stream"], "the two chat lanes are lock-step"
    assert len([t for t in armed["stream"] if t.startswith("mcp__github__")]) == chat_turn._MAX_CONNECTOR_TOOLS


def test_a_refused_connected_app_tool_points_at_the_menu_it_lives_in():
    """The integrator's rename: the chat's Connections switches sit in the
    Tools menu (the wrench chip), never a "+" menu — the footnote, like the
    built-in one, names the Tools menu."""
    out = chat_turn.refusal_footnotes([("mcp__github__create_issue", "not armed")])
    assert "the chat's Tools menu › Connections" in out and "+ ›" not in out
    built_in = chat_turn.refusal_footnotes([("write_file", "not armed")])
    assert "the chat's Tools menu" in built_in


# =========================================================================== #
# (d) the Directory card and the chat's apps section say one thing
# =========================================================================== #
def _apps_row(c: TestClient) -> dict:
    p = _platform(c)
    live = app_names.live_tools(p, [CARD_ID])
    (row,) = app_names.app_states(p, [CARD_ID], live)
    return row


def test_microsoft_365_reads_sign_in_to_finish_on_its_card_and_in_chat(ms365, signin_file):  # noqa: F811 — pytest fixtures
    """THE SEAM (8D review 3): the card said "Sign in to Microsoft to finish"
    while the chat's apps section told the model it was connected with 24
    tools. MUTATION: ``_signin_unfinished`` returns "" -> chat reads
    "connected" (red)."""
    _connect_ms365(ms365)
    card = _row(ms365, CARD_ID)
    assert card["status"] == "signin"
    row = _apps_row(ms365)
    assert row["state"] == "signin" and row["note"] == card["detail"]
    section = app_names.apps_section([row])
    assert "not signed in yet" in section and card["detail"].rstrip(".") in section
    assert "connected (" not in section
    assert row["id"] not in app_names.connected_ids([row]), "a search is not handed to it"

    # Signed in (its own check, through the card's Test): both say connected.
    signin_file.write_text("dana@contoso.com", encoding="utf-8")
    assert ms365.post(f"/connectors/{CARD_ID}/test").json()["ok"] is True
    assert _row(ms365, CARD_ID)["status"] == "connected"
    row = _apps_row(ms365)
    assert row["state"] == "connected", row
    assert "connected (24 tools)" in app_names.apps_section([row])


def test_one_pixio_key_sentence_names_the_place_everywhere():
    from iron_jarvis.daemon.routes import agents as routes_agents
    from iron_jarvis.media import pixio, pixio_http
    from iron_jarvis.providers.failure_words import ACCOUNTS_PLACE

    assert pixio_http.KEY_PLACE == ACCOUNTS_PLACE
    rejected = pixio.map_error(401, {"error": "bad key"})
    assert rejected is not None and rejected.words == pixio_http.REJECTED_KEY_ERROR
    assert pixio_http.http_error(401, {}) == pixio_http.REJECTED_KEY_ERROR  # the pixio_* tools
    for text in (pixio_http.MISSING_KEY_ERROR, pixio_http.REJECTED_KEY_ERROR, routes_agents._NO_IMAGE_MODEL):
        assert ACCOUNTS_PLACE in text, text
        assert "PIXIO_API_KEY" not in text and "secret" not in text, text


# =========================================================================== #
# (e) a fresh install stays on this PC; the door points at the setting
# =========================================================================== #
def test_a_fresh_install_keeps_the_service_on_this_pc_and_says_how_to_open_it(tmp_path):
    from iron_jarvis.daemon.routes.agents import LAN_ACCESS_KEY, reachability
    from iron_jarvis.settings import schema

    platform = create_app(str(tmp_path)).state.platform
    assert getattr(platform.config, LAN_ACCESS_KEY) is False
    label = schema.get(LAN_ACCESS_KEY).label
    out = reachability("http://192.168.1.20:8787/agents/task-door/task", who="the other computer",
                       instead="paste its address here")
    assert out["host_allowed"] is False
    assert label in out["note"] and "--host" not in out["note"]
    # The Help page's phone recipe names the same setting (integrate).
    help_src = " ".join((ROOT / "dashboard" / "app" / "help" / "page.tsx").read_text(encoding="utf-8").split())
    assert label in help_src


@pytest.mark.parametrize("host", ["0.0.0.0", "::", ""])
def test_serve_on_every_interface_still_finds_a_running_copy_on_loopback(monkeypatch, host):
    """8E's REQUIRED note: with the setting on the desktop starts ``serve
    --host 0.0.0.0``; ``connect_ex(("0.0.0.0", p))`` misses a listener on
    Windows, so a still-running copy was not adopted (exit 75) and the new
    one died on the home lock. MUTATION: probe ``host`` -> red."""
    from iron_jarvis.daemon import cli

    asked: list[tuple[str, int]] = []

    def _in_use(h, p):
        asked.append((h, p))
        return h == "127.0.0.1"

    monkeypatch.setattr(cli, "_port_in_use", _in_use)
    monkeypatch.setattr(cli, "_is_ironjarvis_daemon", lambda h, p: asked.append((h, p)) or h == "127.0.0.1")
    with pytest.raises(typer.Exit) as exc:
        cli.serve(host=host, port=8787, root=".", git_native=False)
    assert exc.value.exit_code == 75
    assert asked == [("127.0.0.1", 8787), ("127.0.0.1", 8787)]


def test_serve_on_a_named_address_probes_that_address(monkeypatch):
    from iron_jarvis.daemon import cli

    asked: list[str] = []
    monkeypatch.setattr(cli, "_port_in_use", lambda h, p: asked.append(h) or True)
    monkeypatch.setattr(cli, "_is_ironjarvis_daemon", lambda h, p: asked.append(h) or True)
    with pytest.raises(typer.Exit):
        cli.serve(host="192.168.1.20", port=8787, root=".", git_native=False)
    assert asked == ["192.168.1.20", "192.168.1.20"]


# =========================================================================== #
# (8B follow-ups) every CLI's timeout names its provider; no doubled prefix
# =========================================================================== #
def test_a_codex_timeout_reads_as_a_timeout_naming_codex():
    """8B review 4: only the Claude transport STAMPED its timeout. A Codex /
    Grok / OpenCode timeout was recognised only through its cause chain (the
    ``TimeoutExpired`` underneath); a wrapper that re-raises without ``from``
    loses that and read "Codex couldn't finish this answer". The stamp rides
    the error itself — the pin cuts the chain to prove it. MUTATION: drop
    the ``kind`` stamp in subprocess_cli -> the generic title (red)."""
    import asyncio
    import subprocess

    from iron_jarvis.providers.adapters.base import LLMMessage
    from iron_jarvis.providers.adapters.subprocess_cli import SubprocessCliAdapter
    from iron_jarvis.providers.failure_words import friendly_error

    def _hangs(argv, stdin):
        raise subprocess.TimeoutExpired(argv, 300)

    adapter = SubprocessCliAdapter(
        "codex-cli", "codex", lambda prompt, model: ["codex", "exec", prompt], lambda out: out,
        runner=_hangs, which=lambda b: "C:/bin/codex.exe",
    )
    with pytest.raises(Exception) as exc:
        asyncio.run(adapter.complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=[]))
    err = exc.value
    assert isinstance(err.__cause__, subprocess.TimeoutExpired)  # the chain says it too
    err.__cause__ = err.__context__ = None  # ... but a wrapper may drop it
    err.__suppress_context__ = True
    words = friendly_error(err, provider="codex-cli")
    assert words["title"] == "Codex didn't answer in time", words
    assert words["technical"].count("codex-cli:") == 1


def test_the_rate_limited_sentence_says_the_provider_once():
    from iron_jarvis.providers.adapters.base import ProviderError
    from iron_jarvis.providers.router import _named_failure

    assert _named_failure("claude-cli", ProviderError("claude-cli: CLI timed out")) == "claude-cli: CLI timed out"
    assert _named_failure("openai", RuntimeError("429 slow down")) == "openai: 429 slow down"
