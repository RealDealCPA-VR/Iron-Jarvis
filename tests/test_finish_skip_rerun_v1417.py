"""v1.417.0 (C1.3c, C1.7, C2.4) — the finish check, quieter and honest.

* THE SKIP RULE (decision D1): when the model's own test command ran AFTER the
  reply's last change and exited 0, the check does not run it again — the
  record says ``tests: "passed"`` with ``reused: true`` and the note says so.
  Any change after it (an edit, a command that may write) brings the re-run
  back; a read-only command does not.
* WHO ASKS AND WHY (C1.3b/C1.7): every ``approval`` frame carries ``asker``
  ("model" | "iron_jarvis") and one ``why`` line.
* THE FINISH ROUND IS TAGGED (C2.4): every round / token / thinking /
  tool_call / reset frame the lane emits during the check's round carries
  ``check: "finish"``, and no other frame does.

Driven through the REAL stream and POST lanes (registry, permission engine,
cards, ledger, the shell tool — a test command really runs); only the model
is scripted.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.core import auto_approve as A
from iron_jarvis.harness import finish as F
from tests.test_finish_lanes_v1386 import (  # noqa: F401 — `env` is a fixture
    TEST_CMD,
    _body,
    _finish_card,
    _finish_rows,
    _lane,
    _shell,
    _stream,
    _write,
    env,
)

ANSWER = "1. met: X is 2.\nNothing needed changing."


# --------------------------------------------------------------------------- #
# the skip rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_test_that_passed_after_the_last_edit_is_not_run_again(env, lane, monkeypatch):
    platform, ws, wire = env
    model = wire([_write("app.py", "X = 2\n"), _shell(TEST_CMD), "Set X to 2.", ANSWER])
    done, frames = await _lane(lane, platform, _body(ws), monkeypatch)
    assert _finish_rows(platform) == [], "the check ran the tests again"
    assert done["finish"]["tests"] == "passed"
    assert done["finish"]["reused"] is True
    assert done["finish"]["command"] == TEST_CMD
    assert list(done["finish"])[-1] == "reused"  # (no review: it rides last)
    (note,) = model.notes()
    assert f"`{TEST_CMD}`" in note and "so Iron Jarvis did not run it again." in note
    # The reply line keeps it.
    assert F.saved_record(done["finish"])["reused"] is True


@pytest.mark.parametrize("posture", ["approve_for_me", "always_ask"])
async def test_under_ask_first_the_skip_means_no_finish_card(env, posture):
    platform, ws, wire = env
    seen: list[dict] = []
    wire([_write("app.py", "X = 2\n"), _shell(TEST_CMD), "Done.", "1. met."])
    out = await _stream(platform, _body(ws, approval_mode=posture),
                        answer=lambda f: (seen.append(f), "once")[1])
    assert [f for f in seen if _finish_card(f)] == []
    assert len(seen) == (2 if posture == "always_ask" else 1)  # the model's own cards only
    assert out["done"]["finish"]["tests"] == "passed" and out["done"]["finish"]["reused"] is True


@pytest.mark.parametrize(
    "after",
    [
        _write("other.py", "Y = 1\n", "w2"),
        _shell('python -c "open(\'side.txt\', \'w\').write(\'x\')"', "s2"),
    ],
    ids=["an-edit", "a-command-that-may-write"],
)
async def test_a_change_after_the_test_brings_the_rerun_back(env, after):
    platform, ws, wire = env
    wire([_write("app.py", "X = 2\n"), _shell(TEST_CMD), after, "Done.", ANSWER])
    out = await _stream(platform, _body(ws), answer=lambda f: "once")
    rows = _finish_rows(platform)
    assert [json.loads(r.args_json)["command"] for r in rows] == [TEST_CMD]
    assert out["done"]["finish"]["tests"] == "passed"
    assert "reused" not in out["done"]["finish"]


async def test_a_read_only_command_after_the_test_keeps_the_skip(env):
    platform, ws, wire = env
    wire([_write("app.py", "X = 2\n"), _shell(TEST_CMD), _shell("dir", "s2"), "Done.", ANSWER])
    out = await _stream(platform, _body(ws), answer=lambda f: "once")
    assert _finish_rows(platform) == []
    assert out["done"]["finish"]["reused"] is True


async def test_a_test_that_failed_after_the_edit_is_run_again(env):
    platform, ws, wire = env
    wire([_write("app.py", "X = 11\n"), _shell(TEST_CMD), "Done.", ANSWER])
    out = await _stream(platform, _body(ws), answer=lambda f: "once")
    assert [r.tool for r in _finish_rows(platform)] == ["shell"]
    assert out["done"]["finish"]["tests"] == "failed"
    assert "reused" not in out["done"]["finish"]


async def test_a_run_whose_exit_status_is_not_its_tests_is_run_again(env):
    """``pytest || echo x`` exits 0 when the tests fail: never reused."""
    platform, ws, wire = env
    wire([_write("app.py", "X = 2\n"), _shell(f"{TEST_CMD} || echo done"), "Done.", ANSWER])
    out = await _stream(platform, _body(ws), answer=lambda f: "once")
    assert [json.loads(r.args_json)["command"] for r in _finish_rows(platform)] == [TEST_CMD]
    assert "reused" not in out["done"]["finish"]


def test_saved_record_keeps_reused_only_on_a_pass():
    base = {"tests": "passed", "command": "pytest", "removed": 0, "kept": 0, "points": 0, "unmet": 0, "after": ""}
    assert F.saved_record({**base, "reused": True})["reused"] is True
    assert "reused" not in F.saved_record(base)
    assert "reused" not in F.saved_record({**base, "tests": "failed", "reused": True})
    assert "reused" in F.RECORD_KEYS and F.RECORD_KEYS[-1] == "review"


# --------------------------------------------------------------------------- #
# who asks, and why
# --------------------------------------------------------------------------- #


async def test_the_finish_card_names_iron_jarvis_and_says_why(env):
    platform, ws, wire = env
    seen: list[dict] = []
    # The test BEFORE the edit: the check re-runs it, on a card.
    wire([_shell(TEST_CMD), _write("app.py", "X = 2\n"), "Done.", ANSWER])
    await _stream(platform, _body(ws, approval_mode="approve_for_me"),
                  answer=lambda f: (seen.append(f), "once")[1])
    mine = [f for f in seen if _finish_card(f)]
    theirs = [f for f in seen if not _finish_card(f)]
    assert len(mine) == 1 and len(theirs) == 1
    assert mine[0]["asker"] == "iron_jarvis" and mine[0]["why"] == A.FINISH_WHY
    assert theirs[0]["asker"] == "model" and theirs[0]["why"] == A.NOT_GRANTED_WHY


async def test_strict_mode_says_so_on_the_card(env):
    platform, ws, wire = env
    seen: list[dict] = []
    wire([_shell("echo hi"), "Done."])
    await _stream(platform, _body(ws, text="say hi", approval_mode="always_ask"),
                  answer=lambda f: (seen.append(f), "deny")[1])
    assert seen and seen[0]["asker"] == "model" and seen[0]["why"] == A.STRICT_WHY


def test_card_why_order_and_words():
    assert A.card_why() == A.NOT_GRANTED_WHY
    assert A.card_why(own=A.FINISH_WHY, forced="local_only") == A.FINISH_WHY
    assert A.card_why(plan=True, strict=True) == A.PLAN_WHY
    assert A.card_why(spend_words="Push to origin", once_reason="git") == "Push to origin"
    assert A.card_why(once_reason="git") == A.ONCE_REASON_WHY["git"]
    assert A.card_why(forced="local_only") == A.ASK_RULES["local_only"]
    assert A.card_why(rule="edits_only") == A.ASK_RULES["edits_only"]
    assert A.card_why(rule=A.JUDGE_ASKED_RULE) == A.JUDGE_ASKED_WORDS
    assert A.card_why(strict=True) == A.STRICT_WHY
    assert A.checker_why("ruff") == "Iron Jarvis runs the project's own checker ruff"
    assert len(A.card_why(spend_words="x" * 900)) == A.MAX_WHY_CHARS


async def test_accept_edits_says_which_rule_kept_the_card(env):
    platform, ws, wire = env
    seen: list[dict] = []
    wire([_shell("npm run build"), "Done."])
    await _stream(platform, _body(ws, text="build it", approval_mode="accept_edits"),
                  answer=lambda f: (seen.append(f), "deny")[1])
    assert seen and seen[0]["why"] == A.ASK_RULES["edits_only"]


# --------------------------------------------------------------------------- #
# the finish round is tagged
# --------------------------------------------------------------------------- #

TAGGED_KINDS = ("round", "token", "thinking", "tool_call", "reset")


async def test_the_finish_rounds_frames_and_only_those_carry_check_finish(env):
    platform, ws, wire = env
    # The test BEFORE the edit: the check's round runs it again; then the
    # model answers the note.
    wire([_shell(TEST_CMD), _write("app.py", "X = 2\n"), "Set X to 2.", ANSWER])
    out = await _stream(platform, _body(ws), answer=lambda f: "once")
    frames = [(e, d) for e, d in out["frames"] if e in TAGGED_KINDS]
    first = next(i for i, (e, d) in enumerate(frames) if d.get("check") == "finish")
    tagged = [(e, d) for e, d in frames if d.get("check") == "finish"]
    before, during = frames[:first], frames[first:]
    # Before the check: the model's own work — nothing tagged.
    assert all("check" not in d for _e, d in before)
    assert any(e == "token" and d["text"] == "Set X to 2." for e, d in before)
    # From the check on: its test step, the separator, the answer round's
    # `round` frame and the answer's tokens — all tagged, nothing else left.
    assert all(d.get("check") == "finish" for _e, d in during), during
    kinds = {e for e, _d in tagged}
    assert {"tool_call", "token", "round"} <= kinds
    ids = {d.get("id") for e, d in tagged if e == "tool_call"}
    assert ids and all(str(i).startswith(F.CALL_ID_PREFIX) for i in ids)
    answer_text = "".join(d["text"] for e, d in tagged if e == "token")
    assert "1. met:" in answer_text


async def test_a_reply_with_no_finish_round_tags_nothing(env):
    platform, ws, wire = env
    wire([_shell("echo hi"), "Done."])
    out = await _stream(platform, _body(ws, text="say hi"), answer=lambda f: "once")
    assert all("check" not in d for e, d in out["frames"] if e in TAGGED_KINDS)


async def test_the_models_work_after_its_answer_is_not_tagged(env):
    """The answer round's own fixes (a later round) are the model's work."""
    platform, ws, wire = env
    (ws / "app.py").write_bytes(b"X = 0\n")

    def answer_then_fix(model):
        return ("1. not met: the tests failed.", [_write("app.py", "X = 2\n", "w9")[0]])

    wire([_shell(TEST_CMD), _write("app.py", "X = 11\n"), "Done.", answer_then_fix, "Fixed X."])
    out = await _stream(platform, _body(ws), answer=lambda f: "once")
    frames = [(e, d) for e, d in out["frames"] if e in TAGGED_KINDS]
    fix = [d for e, d in frames if e == "tool_call" and d.get("id") == "w9"]
    assert fix and all("check" not in d for d in fix)
    last_tokens = [d for e, d in frames if e == "token" and d["text"] == "Fixed X."]
    assert last_tokens and "check" not in last_tokens[0]
