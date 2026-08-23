"""Build-pane chat grounding (v1.210.0).

THE LIVE BUG: the Build/terminals page mounts a per-pane chat that POSTs
/chat/stream with {workspace_dir: <pane cwd>, auto_tools: true} every turn —
but the daemon consumed workspace_dir ONLY to place the tool workspace, and
only inside the armed branch. ``select_auto_tools`` has no workspace signal,
so "tell me about this code base" armed ZERO tools, the system prompt never
mentioned the folder, and the model answered "I don't have any project or
folder attached to this turn" (live thread chat_06bf0135cc8f,
setup.workspace_dir=C:\\Users\\VR\\Projects\\RPA).

Contract under test:

* FIX A — BOTH lanes append a workspace grounding block whenever
  ``body.workspace_dir`` is non-empty, REGARDLESS of arming: usable folders
  are named absolutely and the deixis ("this codebase", "here") is pinned to
  them; unusable folders get honest "not accessible" wording, never a
  grounding claim tools cannot back. The block lands BEFORE the history
  planner runs (the CLAUDE.md budget rule) — pinned by source ORDER, not by
  character count (the v1.208/v1.209 lesson).
* FIX B — ``_resolve_armed_tools`` seeds a curated READ-ONLY baseline
  (list_files, read_file, file_search, list_folder) when a workspace is bound
  and auto_tools is on: after the sentence + attachment passes, free slots
  only, AUTO_SAFE_TOOLS-gated, envelope-ceiling-respecting, and NEVER a write
  tool (arming is granting; a bound folder is consent to READ).
* FIX C — stream lane only: ``shell`` joins the ask tier (visible in
  tool_specs, approval-carded, never granted) when a workspace is bound and
  auto_tools is on. The non-stream lane keeps the documented headless
  asymmetry: no shell.

Fully offline; the router monkeypatch harness is the same one
tests/test_chat_envelope_v1202.py uses (spies take *args/**kwargs — the repo
lesson: fixed-signature spies TypeError on additive params and read as
"never ran").
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    _MAX_ARMED_TOOLS,
    _resolve_armed_tools,
    _workspace_grounding_block,
)
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS

_SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"

#: The curated read-only baseline FIX B seeds, in its deterministic order.
_BASELINE = ["list_files", "read_file", "file_search", "list_folder"]

#: The exact phrasing shape of the live bug — arms nothing by sentence.
_CODEBASE_ASK = "tell me about this code base"


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


@pytest.fixture
def bound_dir(tmp_path) -> Path:
    """A real, usable folder standing in for the Build pane's cwd."""
    d = tmp_path / "rpa-workspace"
    d.mkdir()
    return d


def _capture_complete(client, monkeypatch, seen: dict):
    async def fake_complete(*args, **kw):
        seen["system"] = str(kw.get("system") or "")
        seen["tools"] = list(kw.get("tools") or [])
        return RouteResult(LLMResponse(text="ok"), "mock", "mock")

    monkeypatch.setattr(
        client.app.state.platform.router, "complete", fake_complete
    )


def _capture_stream(client, monkeypatch, seen: dict):
    async def fake_stream(*args, **kw):
        seen["system"] = str(kw.get("system") or "")
        seen["tools"] = list(kw.get("tools") or [])
        yield {"type": "text", "text": "ok"}
        yield {"type": "final", "response": LLMResponse(text="ok"),
               "provider": "mock", "model": "mock"}

    monkeypatch.setattr(client.app.state.platform.router, "stream", fake_stream)


def _stream_done(client, payload):
    """The done frame — detected the way tests/test_doors_v1199.py does
    (only the done frame carries `escalate`)."""
    with client.stream("POST", "/chat/stream", json=payload) as r:
        assert r.status_code == 200
        done = None
        for line in r.iter_lines():
            if line.startswith("data: "):
                frame = json.loads(line[6:])
                if "escalate" in frame:
                    done = frame
    assert done is not None, "no done frame arrived"
    return done


def _spec_names(specs) -> set[str]:
    return {s.get("name", "") for s in specs}


# --------------------------------------------------------------------------- #
# FIX A — the prompt names the bound folder (both lanes)
# --------------------------------------------------------------------------- #


def test_stream_prompt_names_bound_folder_with_zero_tools_armed(
    client, monkeypatch, bound_dir
):
    """THE regression test for the live bug: a /chat/stream turn bound to a
    folder, with NOTHING armable (auto_tools off, no picks), must still hand
    the provider a system prompt naming the absolute folder and pinning the
    deixis to it."""
    seen: dict = {}
    _capture_stream(client, monkeypatch, seen)
    done = _stream_done(client, {
        "messages": [{"role": "user", "content": _CODEBASE_ASK}],
        "workspace_dir": str(bound_dir),
        "auto_tools": False,
    })
    assert done["reply"] == "ok"
    system = seen["system"]
    assert str(bound_dir) in system
    assert "# Working folder (bound by the user)" in system
    assert '"this codebase"' in system
    # Zero tools armed — the block must not depend on the # Tools section.
    assert done["auto_armed"] == []
    assert "# Tools" not in system


def test_nonstream_prompt_names_bound_folder(client, monkeypatch, bound_dir):
    """Lock-step proof: the POST /chat lane carries the identical block."""
    seen: dict = {}
    _capture_complete(client, monkeypatch, seen)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": _CODEBASE_ASK}],
        "workspace_dir": str(bound_dir),
        "auto_tools": False,
    })
    assert r.status_code == 200
    system = seen["system"]
    assert str(bound_dir) in system
    assert "# Working folder (bound by the user)" in system
    assert '"this codebase"' in system


@pytest.mark.parametrize("lane", ["stream", "complete"])
def test_missing_folder_gets_honest_wording_not_a_grounding_claim(
    client, monkeypatch, tmp_path, lane
):
    """workspace_dir pointing at a folder that does not exist: the prompt
    says so honestly — it NEVER claims grounding in a folder the tools
    cannot reach."""
    gone = tmp_path / "does-not-exist"
    seen: dict = {}
    payload = {
        "messages": [{"role": "user", "content": _CODEBASE_ASK}],
        "workspace_dir": str(gone),
        "auto_tools": False,
    }
    if lane == "stream":
        _capture_stream(client, monkeypatch, seen)
        _stream_done(client, payload)
    else:
        _capture_complete(client, monkeypatch, seen)
        assert client.post("/chat", json=payload).status_code == 200
    system = seen["system"]
    assert str(gone) in system
    assert "not accessible right now" in system
    # The grounding claim must NOT render for an unreachable folder.
    assert "they mean that folder" not in system
    assert "opened from a Build terminal pane" not in system


def test_grounding_block_unit_wording():
    """The helper itself: "" without a binding; honest wording when the
    resolution failed outright (None)."""
    assert _workspace_grounding_block("", None) == ""
    assert _workspace_grounding_block("   ", None) == ""
    block = _workspace_grounding_block("C:\\gone", None)
    assert "C:\\gone" in block and "not accessible" in block
    ok = _workspace_grounding_block("C:\\ws", (Path("C:\\ws"), True))
    assert "C:\\ws" in ok and '"this codebase"' in ok


def test_block_lands_before_the_planner_in_both_lanes():
    """CLAUDE.md rule: system-prompt additions land BEFORE _plan_context or
    their cost is invisible to the budget. Pinned by source ORDER (semantic),
    never by character count (the v1.208/v1.209 CRLF lesson)."""
    for name in ("chat_turn.py", "routes/chat.py"):
        src = (_SRC / name).read_text(encoding="utf-8")
        call = src.index(
            "_workspace_grounding_block(body.workspace_dir, _ws_resolved)"
        )
        planner = src.index("plan = _plan_context(")
        assert call < planner, (
            f"{name}: the workspace grounding block no longer lands before "
            "the history planner — its cost is invisible to the budget"
        )


# --------------------------------------------------------------------------- #
# FIX B — a bound workspace seeds read-only file tools
# --------------------------------------------------------------------------- #


def _fake_d():
    registry = SimpleNamespace(get=lambda name: object())
    return SimpleNamespace(platform=SimpleNamespace(registry=registry))


def _body(tools: list[str], question: str = "hello", *,
          workspace_dir: str = "", auto_tools: bool = True):
    return SimpleNamespace(
        tools=tools,
        skill="",
        auto_tools=auto_tools,
        attachments=None,
        workspace_dir=workspace_dir,
        messages=[SimpleNamespace(role="user", content=question)],
    )


def test_workspace_seeds_the_readonly_baseline():
    """A bound workspace + auto_tools + a sentence that arms nothing → the
    curated baseline arms, in its deterministic order, and every member is in
    AUTO_SAFE_TOOLS (never widened from here)."""
    d = _fake_d()
    res = _resolve_armed_tools(d, _body([], workspace_dir="C:\\ws"))
    armed, auto = res
    assert auto == _BASELINE
    assert armed == _BASELINE
    assert set(_BASELINE) <= AUTO_SAFE_TOOLS
    # READ-ONLY by construction: no write/mutating verb may ever seed here.
    assert set(auto).isdisjoint(
        {"write_file", "write_document", "edit_file", "excel_edit",
         "excel_apply_spec", "convert_document", "redact_pii",
         "pdf_arrange", "pdf_split", "shell"}
    )


def test_no_auto_tools_means_no_baseline():
    """auto_tools off = no consent: the same bound workspace arms NOTHING."""
    d = _fake_d()
    res = _resolve_armed_tools(
        d, _body([], workspace_dir="C:\\ws", auto_tools=False)
    )
    assert res == ([], [])
    assert res.dropped == 0


def test_no_workspace_means_no_baseline():
    """Mutation pin for the gate itself: without a bound folder the pass is
    inert and a plain "hello" still arms nothing."""
    d = _fake_d()
    assert _resolve_armed_tools(d, _body([])) == ([], [])


def test_explicit_picks_filling_the_cap_leave_no_room():
    """Explicit consent keeps precedence: six picks fill the standing cap and
    the baseline adds nothing."""
    d = _fake_d()
    six = ["read_document", "image_info", "web_search", "web_fetch",
           "view_image", "extract_pdf"]
    assert len(six) == _MAX_ARMED_TOOLS
    res = _resolve_armed_tools(d, _body(six, workspace_dir="C:\\ws"))
    armed, auto = res
    assert armed == six
    assert auto == []


def test_envelope_ceiling_caps_the_baseline_and_drop_signal_is_consistent():
    """Under a measured cap of 3 the baseline fills only the free slots, and
    the v1.202.0 drop signal reports exactly the members the ceiling cut —
    the identical fill re-run at the standing ceiling, not a heuristic."""
    d = _fake_d()
    res = _resolve_armed_tools(d, _body([], workspace_dir="C:\\ws"), 3)
    armed, auto = res
    assert armed == _BASELINE[:3]
    assert auto == _BASELINE[:3]
    assert res.ceiling == 3
    # The baseline fill at the standing ceiling holds all 4 — exactly 1 cut.
    assert res.dropped == 1


def test_sentence_pass_keeps_its_slots_ahead_of_the_baseline():
    """The workspace pass runs LAST: typed intent (the web ask) fills first,
    the baseline takes what is left."""
    d = _fake_d()
    res = _resolve_armed_tools(d, _body(
        [], question="search the web for the latest EV tax credit news",
        workspace_dir="C:\\ws",
    ))
    armed, auto = res
    assert "web_search" in auto
    assert auto.index("web_search") < auto.index("list_files")
    assert len(armed) <= _MAX_ARMED_TOOLS


def test_unregistered_baseline_tools_are_skipped():
    """Each baseline name is gated on the registry — a build that did not
    register one simply seeds the rest."""
    registry = SimpleNamespace(
        get=lambda name: None if name in ("list_files", "list_folder") else object()
    )
    d = SimpleNamespace(platform=SimpleNamespace(registry=registry))
    res = _resolve_armed_tools(d, _body([], workspace_dir="C:\\ws"))
    armed, auto = res
    assert auto == ["read_file", "file_search"]


# --------------------------------------------------------------------------- #
# FIX C — stream lane: shell joins the ask tier, never the grant
# --------------------------------------------------------------------------- #


def test_stream_bound_workspace_puts_shell_on_the_ask_tier(
    client, monkeypatch, bound_dir
):
    """workspace_dir + auto_tools on the STREAM lane: shell is VISIBLE in
    tool_specs (the model can propose a command) but never armed/granted —
    it stays out of auto_armed, and the prompt names it APPROVAL-GATED."""
    seen: dict = {}
    _capture_stream(client, monkeypatch, seen)
    done = _stream_done(client, {
        "messages": [{"role": "user", "content": _CODEBASE_ASK}],
        "workspace_dir": str(bound_dir),
        "auto_tools": True,
    })
    names = _spec_names(seen["tools"])
    assert "shell" in names
    # Never granted: not in the armed/auto_armed lists the grant is built from.
    assert "shell" not in done["auto_armed"]
    system = seen["system"]
    assert "APPROVAL-GATED" in system and "shell" in system
    # The FIX B baseline armed too (auto_tools on + bound workspace).
    assert "read_file" in done["auto_armed"]
    # And FIX A grounded the prompt on the same turn.
    assert str(bound_dir) in system


def test_stream_no_workspace_no_shell(client, monkeypatch):
    """Without a bound workspace a plain ask signals no host reach — shell
    must not appear (mutation pin for the FIX C gate)."""
    seen: dict = {}
    _capture_stream(client, monkeypatch, seen)
    _stream_done(client, {
        "messages": [{"role": "user", "content": _CODEBASE_ASK}],
        "auto_tools": True,
    })
    assert "shell" not in _spec_names(seen["tools"])


def test_nonstream_lane_keeps_the_headless_asymmetry(
    client, monkeypatch, bound_dir
):
    """The non-stream lane serves headless callers — nobody is present to
    answer an approval card, so shell must NOT join its specs even with a
    bound workspace."""
    seen: dict = {}
    _capture_complete(client, monkeypatch, seen)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": _CODEBASE_ASK}],
        "workspace_dir": str(bound_dir),
        "auto_tools": True,
    })
    assert r.status_code == 200
    assert "shell" not in _spec_names(seen["tools"])
    # The read-only baseline still arms here (FIX B is lane-shared).
    assert "read_file" in r.json()["auto_armed"]
