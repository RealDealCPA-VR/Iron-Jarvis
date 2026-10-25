"""Readiness 12A (G22/G24) — every registered built-in tool reads in words on
the chat's work line, the approval card, the desktop's ask toast and the run
log, from ONE source.

THE GAP (docs/PRODUCT-READINESS-RESULTS.md 0d.4/0d.6, carried through 0e/0f):
the run log already worded every registered built-in (v1.359.0,
``test_every_registered_builtin_is_worded_in_the_run_log``), but

* the approval card (``lib/toolWords``) and the desktop toast
  (``desktop/main.js`` TOOL_WORDS) knew ~50 by hand: "The assistant wants to
  run assign_work", "use pack restart";
* the chat's work line (``lib/stepWords``) knew ~40 by kind: "Ran
  guide_search", "Ran history_search", counted "ran 1 tool";
* the activity log's ask line and the chat's refusal footnote used
  ``mission.ask_words``: "needed your OK to use read file".

NOW ``agents/mission.card_words`` is the ONE rule (the card's own table, the
browser tools, the run log's lines in the present) and
``step_line_words`` the work line's (done / running);
``scripts/gen_tool_words.py`` writes the dashboard's
``lib/builtinToolWords.ts`` and the desktop's block FROM them. This file
builds the REAL registry and fails on any built-in with no words, on a
generated table that is stale, on a card table the Python twin does not hold
entry for entry, and on a toast that says anything else; it writes the
contract fixture ``dashboard/__tests__/fixtures/tool-words-everywhere-v1387.json``
(``IJ_REGEN_TOOL_WORDS_EVERYWHERE=1``) that
``dashboard/__tests__/tool-words-everywhere-v1387.test.tsx`` feeds through the
real ApprovalCard, the real work line and the real run log words.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

from iron_jarvis.agents import mission
from iron_jarvis.daemon.chat_turn import builtin_tool_words, refusal_footnotes
from iron_jarvis.platform import build_platform
from tests.test_desktop_reliability_v1249 import _asks, requires_node

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "tool-words-everywhere-v1387.json"
TOOL_WORDS_TS = ROOT / "dashboard" / "lib" / "toolWords.ts"


def _gen():
    spec = importlib.util.spec_from_file_location("gen_tool_words", ROOT / "scripts" / "gen_tool_words.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def builtins(tmp_path_factory) -> list[str]:
    platform = build_platform(str(tmp_path_factory.mktemp("tool-words-registry")))
    names = sorted(n for n in platform.registry._tools if not n.startswith(("mcp__", "custom:")))
    assert len(names) >= 150, "anti-vacuity: the real registry"
    return names


def _worded(name: str, text: str) -> None:
    spaced = name.replace("_", " ")
    assert text and "_" not in text, (name, text)
    assert text != f"use {spaced}" and not text.startswith(f"run {name}"), (name, text)


def test_every_built_in_has_card_words(builtins):
    """RED on the old code: ~110 built-ins answered "use <its spaced id>"."""
    for name in builtins:
        _worded(name, mission.card_words(name))
        # The ask line and the refusal line say the same (one rule).
        assert mission.ask_words(name) == mission.do_words(name) == mission.card_words(name), name


def test_every_built_in_the_line_does_not_word_by_kind_has_line_words(builtins):
    """The chat's work line: a built-in neither worded by kind (``_STEP_KIND``)
    nor a browser tool reads the run log's own line, done and running."""
    for name in builtins:
        if name in mission._STEP_KIND or name in mission._BROWSER_TOOLS:
            continue
        words = mission.step_line_words(name)
        assert words is not None, name
        done, doing = words
        _worded(name, done)
        _worded(name, doing)
        assert done[0].isupper() and doing[0].isupper(), words
        assert doing.split(" ", 1)[0].endswith("ing"), words
    for did, _noun in mission._RUN_TOOL_WORDS.values():
        assert did.split(" ", 1)[0] in mission._ING, did


def _ts_words() -> dict[str, str]:
    src = TOOL_WORDS_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    block = src[src.index("const WORDS: Record<string, string> = {") :]
    block = block[: block.index("\n};")]
    return dict(re.findall(r'^\s+([a-z_]+):\s*"([^"]+)",', block, re.M))


def test_the_cards_table_and_its_python_twin_are_one_table():
    """ASK_WORDS held only part of the card's table, so the activity log and
    the refusal line said "change a workflow" beside a card that said "change
    a saved workflow". Now entry for entry, both ways."""
    ts = _ts_words()
    assert len(ts) >= 40, "anti-vacuity: read the card's table"
    assert ts == mission.ASK_WORDS


def test_the_generated_tables_are_current(builtins):
    gen = _gen()
    card, step = gen.tables(builtins)
    assert card == {n: mission.card_words(n) for n in sorted(set(builtins) | set(mission.ASK_WORDS))}
    ts = gen.render_ts(card, step)
    assert gen.TS_FILE.read_text(encoding="utf-8") == ts, "run: uv run python scripts/gen_tool_words.py"
    js_src = gen.JS_FILE.read_text(encoding="utf-8")
    assert gen.with_js_block(js_src, gen.render_js_block(card)) == js_src, (
        "run: uv run python scripts/gen_tool_words.py"
    )


@requires_node
def test_the_desktop_toast_says_the_cards_words_for_every_built_in(builtins, tmp_path):
    """RED on the old code: "use pack restart", "use assign work" …"""
    approvals = [{"id": f"a{i}", "tool": n, "count": 1} for i, n in enumerate(builtins)]
    out = _asks({"steps": [{"approvals": approvals, "now": 0}]}, tmp_path)["out"]
    bodies = [n["body"] for n in out[0]["notify"]]
    assert len(bodies) == len(builtins)
    for name, body in zip(builtins, bodies):
        assert body == f"{mission.card_words(name)} — click to open the run.", (name, body)


def test_the_refusal_footnote_and_the_ask_line_use_the_card_words():
    """The chat's footnote read "Jarvis couldn't use assign work"."""
    assert builtin_tool_words("assign_work") == "queue a task for a teammate"
    assert refusal_footnotes([("pack_restart", "permission denied")]) == (
        "\n\n_Note: Jarvis couldn't restart an extension (permission denied)._"
    )
    assert mission.ask_words("read_file") == "read a file"
    assert mission.ask_words("browser_click") == "act in a browser tab"
    # A custom tool keeps the only name it has; an app's tool its catalog name.
    assert mission.card_words("rename_real_file") == "use rename real file"
    assert mission.card_words("mcp__gmail__search_messages").startswith("use Gmail")


def test_the_new_words_pass_the_retired_word_scan(builtins):
    from tests.test_vocabulary_beyond_dashboard_v13580 import _hits

    for name in builtins:
        lines = [mission.card_words(name), *(mission.step_line_words(name) or ())]
        for line in lines:
            assert _hits(line) == [], (name, line)


def test_the_contract_fixture_is_the_real_registrys_words(builtins):
    data = {
        "about": (
            "Readiness 12A (G22/G24): every built-in the REAL registry holds, with the card's words "
            "(agents/mission.card_words) and, for the ones the work line does not word by kind, its "
            "line words (step_line_words). Written by tests/test_tool_words_everywhere_v1387.py "
            "(IJ_REGEN_TOOL_WORDS_EVERYWHERE=1); read by dashboard/__tests__/"
            "tool-words-everywhere-v1387.test.tsx through the real card and work line."
        ),
        "builtins": builtins,
        "card": {n: mission.card_words(n) for n in builtins},
        "line": {n: list(w) for n in builtins if (w := mission.step_line_words(n)) is not None},
        "kind": {n: mission._STEP_KIND.get(n, "browser" if n in mission._BROWSER_TOOLS else "")
                 for n in builtins},
    }
    if os.environ.get("IJ_REGEN_TOOL_WORDS_EVERYWHERE") == "1":
        FIXTURE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(json.dumps(data)), (
        "regenerate with IJ_REGEN_TOOL_WORDS_EVERYWHERE=1 (and scripts/gen_tool_words.py) after an intended change"
    )
