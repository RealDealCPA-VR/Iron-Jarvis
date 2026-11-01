"""v1.420.0 — the system prompt's ORDER and what a coding turn is sent.

C3.7, PROMPT ORDER FOR THE CACHE. The query-dependent sections (the brain's
retrieved notes, the project knowledge retrieved for this question and the
project's recent activity, the memory fabric's snippets, the connector
memory, the apps the message names, the attachments, page, resources,
earlier chats and the "/" skill) used to sit BEFORE the stable harness block
and the tool list, so Anthropic's prompt cache missed the whole system prompt
(and the history after it) on the first call of every message. Now the
stable head comes first and the query tail LAST, in BOTH lanes; the planner
and the compaction still price all of it.

C3.8, a CODING turn is not sent the chat-only blocks: the email-draft fence,
the chart fence, the browser section and the saved workflows. A plain chat
turn is byte-identical (``tests/test_harness_off_golden_v1366.py``).

The cache pin is driven twice: through the REAL build route (two consecutive
messages of one Build session — the bytes before the first query-dependent
section are identical), and those very prompts through the REAL claude-cli
adapter with the fake CLI, reading back the system-prompt file the CLI was
handed (what its relay then sends Anthropic as ``system``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.platform import build_platform
from tests._fakes.harness_lane import body, no_finish_check, post, stream, wire
from tests.test_build_sessions_v1367 import Script, frames_of, new_session, open_turn
from tests.test_build_sessions_v1367 import wire as wire_build

LANES = ("stream", "post")
BROWSER = "\n\n# Your browser\nBROWSER-SECTION-MARK"
WORKFLOWS = "\n\n# Saved workflows\nWORKFLOWS-MARK"
FAB = "\n\nFABRIC-FOR:"


class _Fabric:
    """A memory fabric whose snippets depend on the question (query-dependent
    by construction)."""

    def ground(self, query, **kw):
        return f"{FAB}{query}"

    def __getattr__(self, item):
        raise AttributeError(item)


def _chat_only_marks(monkeypatch) -> None:
    for mod in (CT, chat_routes):
        monkeypatch.setattr(mod, "_browser_section", lambda *a, **k: BROWSER, raising=False)
    monkeypatch.setattr(CT, "_saved_workflows_block", lambda *a, **k: WORKFLOWS)


@pytest.fixture()
def plat(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("A = 1\n", encoding="utf-8")
    no_finish_check(ws)
    return platform, ws


async def _systems(platform, ws, monkeypatch, lane: str, *, harness: bool, text: str = "what is A?") -> str:
    model = wire(platform, monkeypatch, ["A is 1."])
    b = body(ws, text)
    if harness:
        await (stream(platform, b) if lane == "stream" else post(platform, b))
    else:
        from iron_jarvis.daemon.chat_stream import stream_chat_turn
        from iron_jarvis.daemon.chat_turn import run_chat_turn
        from iron_jarvis.personas.builtins import BUILTIN_PERSONAS

        if lane == "stream":
            gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, b)
            async for _chunk in gen:
                pass
        else:
            await run_chat_turn(platform, BUILTIN_PERSONAS, b)
    assert model.calls, "the router was never reached"
    return model.calls[0]["system"]


# --------------------------------------------------------------------------- #
# C3.8: what a coding turn is (not) sent
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", LANES)
async def test_a_coding_turn_drops_the_chat_only_blocks(plat, monkeypatch, lane):
    platform, ws = plat
    _chat_only_marks(monkeypatch)
    system = await _systems(platform, ws, monkeypatch, lane, harness=True)
    assert "# IJ harness" in system or "IJ harness" in system
    assert CT.DRAFT_BLOCK not in system, f"{lane}: the email-draft fence rode a coding turn"
    assert CT.CHART_BLOCK not in system, f"{lane}: the chart fence rode a coding turn"
    assert "BROWSER-SECTION-MARK" not in system, f"{lane}: the browser section rode a coding turn"
    assert "WORKFLOWS-MARK" not in system, f"{lane}: the saved workflows rode a coding turn"


@pytest.mark.parametrize("lane", LANES)
async def test_a_plain_chat_turn_keeps_every_block(plat, monkeypatch, lane):
    """Anti-vacuity: the same marks DO reach a plain chat turn."""
    platform, ws = plat
    _chat_only_marks(monkeypatch)
    system = await _systems(platform, ws, monkeypatch, lane, harness=False)
    assert CT.DRAFT_BLOCK in system and CT.CHART_BLOCK in system
    assert "BROWSER-SECTION-MARK" in system and "WORKFLOWS-MARK" in system


# --------------------------------------------------------------------------- #
# C3.7: stable head first, query tail last
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", LANES)
async def test_the_query_dependent_sections_come_after_the_stable_head(plat, monkeypatch, lane):
    platform, ws = plat
    monkeypatch.setattr(platform, "fabric", _Fabric())
    system = await _systems(platform, ws, monkeypatch, lane, harness=True, text="what is A in a.py?")
    fab = system.index(FAB)
    harness_at = system.index("IJ harness")
    assert harness_at < fab, f"{lane}: the stable harness block must come before the fabric"
    assert "# Environment" in system[:fab]
    assert system.endswith(f"{FAB}what is A in a.py?"), f"{lane}: the query tail ends the prompt"


async def test_both_lanes_send_the_same_order(plat, monkeypatch, tmp_path):
    platform, ws = plat
    monkeypatch.setattr(platform, "fabric", _Fabric())
    got = {lane: await _systems(platform, ws, monkeypatch, lane, harness=True) for lane in LANES}
    # (The POST lane arms no ask-tier tool, so the harness tree's tool line
    # differs; the head before it and the query tail are the same bytes.)
    heads = {k: v[: v.index("IJ harness")] for k, v in got.items()}
    tails = {k: v[v.index(FAB):] for k, v in got.items()}
    assert heads["stream"] == heads["post"] and tails["stream"] == tails["post"]


def test_split_query_tail_never_says_a_section_twice():
    assert CT.split_query_tail("HEADTAIL\n\nSUMMARY", "HEAD", "TAIL") == ("HEAD\n\nSUMMARY", "TAIL")
    assert CT.split_query_tail("HEADTAIL", "HEAD", "TAIL") == ("HEAD", "TAIL")
    assert CT.split_query_tail("HEAD", "HEAD", "") == ("HEAD", "")
    # Not the shape it expects: everything is the head, nothing is added again.
    assert CT.split_query_tail("OTHER", "HEAD", "TAIL") == ("OTHER", "")


# --------------------------------------------------------------------------- #
# C3.7 through the build route and the claude-cli adapter's fake CLI
# --------------------------------------------------------------------------- #


async def _two_messages(tmp_path, monkeypatch) -> list[str]:
    app = create_app(str(tmp_path / "home"))
    monkeypatch.setattr(app.state.platform, "fabric", _Fabric())
    work = tmp_path / "work"
    work.mkdir()
    (work / "app.py").write_text("x = 1\n", encoding="utf-8")
    sid = new_session(app, work)["id"]
    script = wire_build(app, Script(["First answer.", "Second answer."]))
    for i, text in enumerate(("what is x in app.py?", "and why is it one?")):
        status, raw = await open_turn(app, sid, {"text": text, "turn_id": f"po-{i}"})
        assert status == 200, raw
        assert frames_of(raw)[-1][0] == "done"
    firsts = [c["system"] for c in script.calls]
    assert len(firsts) == 2, "one model call per message"
    return firsts


def _stable_head(system: str) -> str:
    return system[: system.index(FAB)]


async def test_two_messages_of_a_session_share_every_byte_before_the_query_tail(tmp_path, monkeypatch):
    one, two = await _two_messages(tmp_path, monkeypatch)
    assert one != two, "the prompts differ (the query tail)"
    head = _stable_head(one)
    assert head == _stable_head(two), "the stable head must be byte-identical across messages"
    assert len(head) > 2000 and "IJ harness" in head, "the head carries the harness tree"


async def test_the_cli_is_handed_the_same_stable_head(tmp_path, monkeypatch):
    """The two build-route prompts through the REAL claude-cli adapter and the
    fake CLI: the system-prompt file the CLI reads (and its relay sends
    Anthropic) keeps the same bytes before the query tail."""
    from tests.test_claude_native_v1300 import Fake, _clean_env, _user  # noqa: F401

    for key in [k for k in list(__import__("os").environ) if k.startswith("ANTHROPIC_")]:
        monkeypatch.delenv(key, raising=False)
    from iron_jarvis.providers import cli_auth
    from iron_jarvis.providers.adapters.claude_native import transport
    from iron_jarvis.providers.cli_auth import CliAuthProbe

    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", CliAuthProbe(which=lambda b: None))
    monkeypatch.setattr(transport, "DEFAULT_PROBE", cli_auth.DEFAULT_PROBE)
    one, two = await _two_messages(tmp_path / "route", monkeypatch)
    handed = []
    for i, system in enumerate((one, two)):
        folder = tmp_path / f"cli{i}"
        folder.mkdir()
        fake = Fake(folder, monkeypatch)
        await fake.adapter().complete(system=system, messages=[_user("q")], tools=[])
        handed.append(fake.record["system"])
    assert FAB in handed[0] and FAB in handed[1]
    assert _stable_head(handed[0]) == _stable_head(handed[1])
    assert _stable_head(handed[0]).endswith(_stable_head(one)[-500:])


def test_the_harness_off_golden_has_no_query_dependent_section():
    """Why ``harness_off_golden_v1366.json`` did not move for C3.7: none of its
    scenarios carries a query-dependent section (no project, fabric hit,
    attachment or skill), so the reorder changes no byte of it — and C3.8
    changes only harness turns."""
    golden = json.loads((Path(__file__).parent / "fixtures" / "harness_off_golden_v1366.json")
                        .read_text(encoding="utf-8"))
    text = json.dumps(golden)
    for mark in ("# Attachments (provided", "Project knowledge (reference)", "# Skill invoked by the user"):
        assert mark not in text
