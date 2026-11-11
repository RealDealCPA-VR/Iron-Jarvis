"""v1.470.0: "create a skill for …" in chat makes a SKILL, not a workflow.

The report: the user asked chat for a tax advisement skill and got a workflow
card. Chat offered the ``workflow_draft`` exit on every turn and never offered
``skill_create``. Pinned here:

* the matcher, both ways (a request to make a skill; never a question, the
  user's own abilities, or another product's "skill");
* the stream lane's ask-arming (``chat_turn.settings_ask_tools``, the very
  function the lane calls) offers ``skill_create`` for it, as an ASK-tier
  tool, so it shows a card; the POST lane's arming never offers it;
* the brief rides only beside the tool, and BOTH lanes append it;
* a skill made in chat is pinned (the curator never archives it) and stays
  marked as written by a model (so it is still scanned before use);
* a low-trust turn may not make one.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.core.trust import LOW_TRUST_DENY
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.platform import build_platform
from iron_jarvis.skills.loader import SKILL_FILE
from iron_jarvis.skills.trigger import wants_skill_create
from iron_jarvis.tools.base import ToolContext

ASKS = [
    "create a skill for tax advisement",
    "Can you create a skill for tax advisement",
    "please make me a tax advisement skill",
    "save this as a skill",
    "turn that into a reusable skill",
    "I need a new skill that reviews leases",
    "write a skill called lease review",
    "make this a skill",
    "build a skill for reviewing K-1s and call it k1-review",
]
DOES_NOT_ASK = [
    "what skills do you have?",
    "how do I make a skill?",
    "what is a skill?",
    "improve my Excel skills",
    "build an Alexa skill",
    "add a new skill to my resume",
    "make a skill check for the rogue",
    "I want to build a strong skill set",
    "use the tax skill on this file",
    "create a workflow for month end",
    "every Friday, pull the ledger and email the report",
    "",
]


@pytest.mark.parametrize("text", ASKS)
def test_a_request_to_make_a_skill_is_read_as_one(text):
    assert wants_skill_create(text)


@pytest.mark.parametrize("text", DOES_NOT_ASK)
def test_everything_else_is_not(text):
    assert not wants_skill_create(text)


def test_the_matcher_is_linear_on_a_long_message():
    """A pathological message, in a child process with a deadline: a matcher
    that backtracked would hang the turn that reads it."""
    code = (
        "from iron_jarvis.skills.trigger import wants_skill_create as w\n"
        "w('create a ' * 60000 + 'skil')\n"
        "w('skill ' * 60000)\n"
        "print('done')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert "done" in out.stdout, out.stderr[-400:]


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    p = build_platform(str(tmp_path_factory.mktemp("skillchat") / "home"))
    yield SimpleNamespace(platform=p)
    if p.repl is not None:
        asyncio.run(p.repl.dispose_all())


def test_the_stream_lane_offers_skill_create_as_an_ask(d):
    ask = CT.settings_ask_tools(d, "create a skill for tax advisement", [], [])
    assert "skill_create" in ask
    # Its own tier is ask: the lane shows the ordinary card for it.
    tool = d.platform.registry.get("skill_create")
    assert d.platform.permissions.mode_for(tool.perm_key()).value == "ask"
    # Not twice, and not for a question.
    assert CT.settings_ask_tools(d, "create a skill for tax advisement", [], ["skill_create"]) == []
    assert "skill_create" not in CT.settings_ask_tools(d, "how do I create a skill?", [], [])
    assert "skill_create" not in CT.settings_ask_tools(d, "summarize this file", [], [])


def test_the_lane_that_cannot_ask_never_arms_it(d):
    body = ChatBody(
        messages=[{"role": "user", "content": "create a skill for tax advisement"}],
        auto_tools=True,
    )
    assert "skill_create" not in CT._resolve_armed_tools(d, body)[0]


def test_the_brief_rides_only_beside_the_tool():
    assert CT.skill_block(["read_file"]) == ""
    assert CT.skill_block(None) == ""
    block = CT.skill_block({"skill_create", "read_file"})
    # v1.473.0 (moved on purpose): the brief also says how a skill is found
    # again by later chats, and that a script that worked can be saved with it.
    assert block == CT.SKILL_BLOCK + CT.SKILL_FINDABLE_LINE
    assert "skill_create" in block and "workflow_draft" in block
    assert "\n" in block and "\\n" not in block


def test_both_lanes_append_the_brief():
    """Lock-step: the POST lane and the stream lane each call skill_block at
    their Tools seam (a brief in one lane only is the bug this app keeps
    paying for)."""
    root = Path(CT.__file__).parent
    post = (root / "chat_turn.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    stream = (root / "routes" / "chat.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "_skill_brief = skill_block(armed)" in post
    assert "_skill_brief = _chat_turn.skill_block({*armed, *ask_armed})" in stream
    for src in (post, stream):
        at = src.index("_skill_brief = ")
        assert 'system += "\\n\\n" + _skill_brief' in src[at: at + 200]


def _ctx(p, tmp_path, session_id):
    return ToolContext(
        workspace=tmp_path, session_id=session_id, agent_run_id=session_id,
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )


def _frontmatter(path: Path) -> str:
    text = (path / SKILL_FILE).read_text(encoding="utf-8")
    return text.split("---")[1]


def test_a_skill_made_in_chat_is_pinned_and_still_marked_model_written(d, tmp_path):
    tool = d.platform.registry.get("skill_create")
    args = {
        "name": "Tax advisement",
        "description": "How to approach a tax question",
        "instructions": "# Tax advisement\n\nAsk the filing status first.",
    }
    r = asyncio.run(tool.execute(dict(args), _ctx(d.platform, tmp_path, "chat")))
    assert r.ok, r.error
    front = _frontmatter(Path(r.data["path"]))
    assert "created_by: agent" in front
    assert "pinned: true" in front.lower()
    assert d.platform.skills.get("Tax advisement") is not None or d.platform.skills.search("tax advisement", 3)

    # An agent run's own skill is not pinned: the curator may archive it.
    r2 = asyncio.run(tool.execute(
        {**args, "name": "Run made skill"}, _ctx(d.platform, tmp_path, "sess_123")
    ))
    assert r2.ok, r2.error
    assert "pinned" not in _frontmatter(Path(r2.data["path"])).lower()


def test_a_low_trust_turn_may_not_make_one():
    assert "skill_create" in LOW_TRUST_DENY
