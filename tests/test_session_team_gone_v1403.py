"""v1.403.0 integrate — the team's two open points from the review, closed.

1. A session whose team has NO worker left that can write code (each removed
   from the model team, or no longer holding Writing code) used to run solo
   with no record and no note — the lead edited files without "saying so",
   against the user's rule. Now the turn runs unlocked AND its record's
   ``lead_did`` says why, and the model is told to say so (both lanes: one
   shared builder, ``chat_turn._build_harness``; one record helper,
   ``chat_turn.team_record``).
2. A member that hands work out itself (a coordinator) is refused as a worker
   at PATCH/POST: the router never gives it a piece, so it would unlock the
   lead on its first piece.
"""

from __future__ import annotations

import pytest

from iron_jarvis.harness import team as T
from tests.test_session_team_v1403 import (  # noqa: F401 — `world` is a fixture
    body,
    lead_calls,
    members,
    post,
    stream,
    world,
)


async def test_a_team_with_no_worker_left_runs_unlocked_and_says_so(world):
    members(world.p)
    world.p.agents_registry.remove("qwen")  # the only worker is gone
    world.main = ["I did it myself."]
    out = await stream(world.p, body(world.ws, "set X to 2"))
    (first, *_rest) = lead_calls(world)
    # Unlocked: the coding set is there (nobody could take a piece).
    assert {"write_file", "edit_file"} & set(first["tools"])
    # The model is told to say so.
    assert "none of its workers can write code right now (qwen)" in first["system"]
    # The record SAYS so, last on the done frame.
    done = out["done"]
    assert list(done)[-1] == "team"
    assert done["team"]["workers"] == [] and done["team"]["pieces"] == 0
    (did,) = done["team"]["lead_did"]
    assert did["reason"].startswith("None of this session's workers can write code now (qwen")


async def test_the_post_lane_says_so_too(world):
    members(world.p)
    world.p.agents_registry.remove("qwen")
    world.main = ["Done."]
    out = await post(world.p, body(world.ws, "set X to 2"))
    (did,) = out["team"]["lead_did"]
    assert "None of this session's workers can write code now" in did["reason"]


async def test_a_solo_session_still_has_no_team_record(world):
    members(world.p)
    world.main = ["Read."]
    out = await stream(world.p, body(world.ws, "what does app.py do?"), team=None)
    assert "team" not in out["done"]


def test_a_coordinator_cannot_be_a_worker(world):
    p = world.p
    p.agents_registry.register("boss", "You coordinate.", ["delegate", "read_file"], provider="ollama",
                               model="qwen3:8b", roles=["code"])
    with pytest.raises(T.TeamRefused, match="hands work out to other agents itself"):
        T.validate(p, {"workers": ["boss"], "reviewer": ""})
