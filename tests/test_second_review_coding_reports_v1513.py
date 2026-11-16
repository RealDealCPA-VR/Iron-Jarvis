"""Second review, group 13 part D (v1.513.0): three places where a coding
session reported more than was true.

* D8 — the ESLint checker said "no problems in app.ts" for a file ESLint
  IGNORED (its own settings left it out): an ignored file is now said NOT
  CHECKED, a run where every file was ignored is "excluded" (never "no
  problems", never listed under ``ran``), a mixed run says both.
* D11 — an attempt cut at the step limit read "Finished" on the attempts card:
  its end frame's ``step_limit`` is folded and worded.
* S5 — a reply's own failure marks (cut off, an error, the step limit, the
  session's cap, failing tests at the finish check) never reached the next
  turn's model or the summarizer: one bracketed line in Iron Jarvis's own
  fixed words now rides the reply, and a failed reply with no words keeps a
  stand-in instead of vanishing.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from iron_jarvis.daemon.durable_turns import INTERRUPTED_DETAIL
from iron_jarvis.harness import attempts as A
from iron_jarvis.harness import checkers as C
from iron_jarvis.harness import compact as K
from iron_jarvis.harness import sessions as S

# --------------------------------------------------------------------------- #
# D8: ESLint's ignored files
# --------------------------------------------------------------------------- #

_ESLINT = C.Checker(name="eslint", program="eslint", where="path", config="eslint.config.js",
                    suffixes=C.JS_SUFFIXES)
_IGNORED = "File ignored because no matching configuration was supplied."


def _eslint_json(folder, entries: dict[str, list[dict]]) -> str:
    return json.dumps([{"filePath": str(folder / name), "messages": msgs} for name, msgs in entries.items()])


async def _eslint_take(tmp_path, files: list[str], output: str) -> tuple[str, C.ProjectCheckers]:
    pc = C.ProjectCheckers(tmp_path, [_ESLINT], "shell")
    cid = C.CALL_ID_PREFIX + "1"
    pc._pending[cid] = C._Pending(checker=_ESLINT, files=list(files), ran_files=list(files))
    call = SimpleNamespace(id=cid, name="shell", arguments={})
    result = SimpleNamespace(ok=True, data={"returncode": 0}, output=output, error="")
    text = await pc.take(call, result)
    return text, pc


def _ignored_msg() -> dict:
    return {"fatal": False, "severity": 1, "message": _IGNORED, "line": 0, "column": 0}


async def test_a_file_eslint_ignored_is_said_not_checked_and_the_run_is_excluded(tmp_path):
    out = _eslint_json(tmp_path, {"app.ts": [_ignored_msg()]})
    text, pc = await _eslint_take(tmp_path, ["app.ts"], out)
    assert "no problems" not in text, text
    assert text == (
        "Check (eslint): eslint does not check app.ts (the project's own eslint settings leave it out), "
        "so it was not checked."
    ), text
    assert pc.runs[-1]["verdict"] == "excluded"
    assert pc.runs[-1]["files"] == [], "a file eslint ignored is never listed as checked"
    rec = pc.record()
    assert rec["ran"] == [], rec
    assert [s["checker"] for s in rec["skipped"]] == ["eslint"]


async def test_a_mixed_eslint_run_says_both_clean_and_not_checked(tmp_path):
    out = _eslint_json(tmp_path, {"a.js": [], "app.ts": [_ignored_msg()]})
    text, pc = await _eslint_take(tmp_path, ["a.js", "app.ts"], out)
    assert text.startswith("Check (eslint): no problems in a.js "), text
    assert "app.ts" in text and "not checked" in text, text
    assert "no problems in a.js, app.ts" not in text
    assert pc.runs[-1]["verdict"] == "clean" and pc.runs[-1]["files"] == ["a.js"]
    assert pc.record()["ran"] == ["eslint"]


async def test_a_mixed_eslint_run_with_problems_names_the_ignored_file_too(tmp_path):
    bad = {"line": 3, "column": 7, "severity": 2, "message": "'x' is never used.", "ruleId": "no-unused-vars"}
    out = _eslint_json(tmp_path, {"a.js": [bad], "app.ts": [_ignored_msg()]})
    text, _pc = await _eslint_take(tmp_path, ["a.js", "app.ts"], out)
    head = text.splitlines()[0]
    assert head.startswith("Check (eslint): 1 problem in a.js "), head
    assert "app.ts" in head and "not checked" in head, head


async def test_a_clean_checked_eslint_file_still_says_no_problems(tmp_path):
    out = _eslint_json(tmp_path, {"a.js": []})
    text, pc = await _eslint_take(tmp_path, ["a.js"], out)
    assert text == "Check (eslint): no problems in a.js.", text
    assert pc.record()["ran"] == ["eslint"]


def test_the_ignored_message_is_still_not_a_finding(tmp_path):
    out = _eslint_json(tmp_path, {"app.ts": [_ignored_msg()]})
    lines, others, understood = C.findings(_ESLINT, out, ["app.ts"], tmp_path)
    assert lines == [] and others == 0 and understood


# --------------------------------------------------------------------------- #
# D11: an attempt cut at the step limit
# --------------------------------------------------------------------------- #


def _attempt() -> A.Attempt:
    return A.Attempt(id="try1", target=A.Target(label="Model one", provider="ollama", model="local-model"),
                     pin=None, git={}, folder="", sid="build:x~try1", turn_id="t.try1")


def test_an_attempt_cut_at_the_step_limit_says_so_and_not_finished():
    a = _attempt()
    a.fold("done", json.dumps({"reply": "Halfway there.", "step_limit": {"limit": 60}}))
    a.settle()
    assert a.words != A.WHY_WORDS["finished"]
    assert a.why != "finished", "the card offers Keep on a 'finished' attempt as if its work were whole"
    assert "step limit" in a.words and "60" in a.words, a.words
    frame = a.frame()
    assert frame["words"] == a.words and frame["why"] == a.why


def test_the_card_line_for_the_model_says_the_step_limit_too():
    a = _attempt()
    a.fold("done", json.dumps({"reply": "Halfway there.", "step_limit": {"limit": 60}}))
    a.settle()
    item = {**a.frame(), "label": "Model one"}
    words = A.summary_words({"items": [item]})
    assert "step limit" in words and "Finished" not in words, words
    assert "Press Keep on the one you want" not in words


def test_an_attempt_that_finished_still_reads_finished():
    a = _attempt()
    a.fold("done", json.dumps({"reply": "Done."}))
    a.settle()
    assert (a.state, a.why, a.words) == ("done", "finished", "Finished")


# --------------------------------------------------------------------------- #
# S5: a reply's failure marks reach the next model and the summarizer
# --------------------------------------------------------------------------- #

Q1 = {"role": "user", "content": "update the three callers", "turnId": "t1"}
Q2 = {"role": "user", "content": "did it work?", "turnId": "t2"}


def _reply(**extra) -> dict:
    return {"role": "assistant", "content": "I'll now update the three callers", "turnId": "t1", **extra}


def _marks(text: str) -> int:
    return text.count("[" + S.REPLY_END_LEAD)


def test_a_cut_off_reply_carries_one_bracket_line():
    for line in (_reply(interrupted=True),
                 _reply(interrupted=True, error=INTERRUPTED_DETAIL)):
        out = S.history_of([Q1, line, Q2])
        assert [m["role"] for m in out] == ["user", "assistant", "user"]
        reply = out[1]["content"]
        assert reply.startswith("I'll now update the three callers")
        assert _marks(reply) == 1 and S.REPLY_CUT_OFF in reply, reply
        assert S.REPLY_ERROR not in reply


def test_an_errored_reply_carries_the_error_mark_never_the_error_text():
    line = _reply(error="Provider says: secret-ish failure text in /home/me/app.py", interrupted=True,
                  errorTitle="Provider says")
    reply = S.history_of([Q1, line, Q2])[1]["content"]
    assert _marks(reply) == 1 and S.REPLY_ERROR in reply, reply
    assert "secret-ish" not in reply and "app.py" not in reply and "Provider says" not in reply


def test_step_limit_cap_and_failed_finish_each_carry_their_mark():
    cases = {
        S.REPLY_STEP_LIMIT: _reply(stepLimit={"limit": 60, "safety": False}),
        S.REPLY_TIME_CAP: _reply(capMet={"kind": "time", "limit": 30}),
        S.REPLY_COST_CAP: _reply(capMet={"kind": "cost", "limit": 2.5}),
        S.REPLY_TESTS_FAILED: _reply(finish={"tests": "failed", "command": "pytest"}),
        S.REPLY_ACCOUNT_LIMIT: _reply(error="The account is at its limit.", accountLimit={"kind": "rate"}),
    }
    for words, line in cases.items():
        reply = S.history_of([Q1, line, Q2])[1]["content"]
        assert _marks(reply) == 1 and words in reply, (words, reply)


def test_a_healthy_reply_gets_nothing():
    for line in (_reply(), _reply(finish={"tests": "passed", "command": "pytest"})):
        out = S.history_of([Q1, line, Q2])
        assert out[1]["content"] == "I'll now update the three callers"
    for m in S.history_of([Q1, _reply(), Q2], last_work=True):
        assert S.REPLY_END_LEAD not in m["content"]


def test_a_failed_reply_with_no_words_keeps_its_place():
    for line in ({"role": "assistant", "content": "", "turnId": "t1", "error": "The model is down."},
                 {"role": "assistant", "content": "", "turnId": "t1", "interrupted": True,
                  "error": INTERRUPTED_DETAIL}):
        out = S.history_of([Q1, line, Q2])
        assert [m["role"] for m in out] == ["user", "assistant", "user"], out
        assert out[1]["content"].startswith("[" + S.REPLY_END_LEAD), out[1]
        assert "\n" not in out[1]["content"], "a one-line stand-in"
    # A reply with no words and NO failure mark is left out, as before.
    out = S.history_of([Q1, {"role": "assistant", "content": "", "turnId": "t1"}, Q2])
    assert [m["role"] for m in out] == ["user", "user"]


def test_the_newest_replys_failed_check_is_not_said_twice_with_the_last_work_note():
    line = _reply(
        finish={"tests": "failed", "command": "python -m pytest"},
        steps=[{"name": "edit_file", "ok": True, "target": "cart.py"},
               {"name": "shell", "ok": False, "check": "finish",
                "detail": {"input": json.dumps({"command": "python -m pytest"})}}],
    )
    out = S.history_of([Q1, line, Q2], last_work=True)
    reply = out[1]["content"]
    assert S.LAST_WORK_LEAD in reply and "(failed) (Iron Jarvis's own check)" in reply, reply
    assert S.REPLY_TESTS_FAILED not in reply, "the note already says the check failed"
    # Without the note (an older reply, or the plain reader) the mark says it.
    assert S.REPLY_TESTS_FAILED in S.history_of([Q1, line, Q2])[1]["content"]


def test_the_mark_is_counted_by_the_history_budget():
    """The mark is in the history's CONTENT before any planner reads it (the
    lane plans what ``history_for_turn`` returns), so the next prompt's cost
    includes it — measured here the planner's way."""
    from iron_jarvis.context.budget import estimate_tokens

    healthy = S.history_of([Q1, _reply(), Q2])
    cut = S.history_of([Q1, _reply(interrupted=True), Q2])
    assert sum(estimate_tokens(m["content"]) for m in cut) > sum(estimate_tokens(m["content"]) for m in healthy)
    tokens_cut, _ = K.replaced([Q1, _reply(interrupted=True), Q2], 3, None)
    tokens_ok, _ = K.replaced([Q1, _reply(), Q2], 3, None)
    assert tokens_cut > tokens_ok


def test_the_summarizer_reads_the_same_marks():
    lines = [Q1, _reply(stepLimit={"limit": 60, "safety": False}), Q2,
             {"role": "assistant", "content": "", "turnId": "t2", "error": "The model is down."},
             {"role": "user", "content": "try again", "turnId": "t3"},
             {"role": "assistant", "content": "All good now.", "turnId": "t3"}]
    for with_steps in (True, False):
        pairs = K.pairs_of(lines, with_steps=with_steps)
        assert [r for r, _ in pairs] == ["user", "assistant", "user", "assistant", "user", "assistant"], pairs
        assert S.REPLY_STEP_LIMIT in pairs[1][1]
        assert S.REPLY_ERROR in pairs[3][1] and "The model is down" not in pairs[3][1]
        assert S.REPLY_END_LEAD not in pairs[5][1]


def test_a_failed_reply_with_no_words_gets_no_follow_up_suggestions():
    """Lead review of S5: the stand-in that keeps a failed reply's place in
    the history is Iron Jarvis's own note, not an answer. Follow-up
    suggestions read the same history and must still find nothing to
    suggest after it (the source is checked: the door runs a model)."""
    import inspect

    from iron_jarvis.harness import followups, sessions

    src = inspect.getsource(followups.suggest).replace("\r\n", "\n")
    assert "S.REPLY_END_LEAD" in src and "F.NOTHING_REASON" in src
    note = sessions.reply_end_note({"role": "assistant", "content": "", "error": "boom"})
    assert note.startswith("[" + sessions.REPLY_END_LEAD)
