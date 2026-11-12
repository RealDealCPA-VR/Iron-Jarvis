"""Capability search plan, Phase 0: HOW OFTEN DO TODAY'S WORD TABLES MISS?

``docs/CAPABILITY-SEARCH-PLAN-2026-11-12.md``. Which tools a chat reply gets
is decided by matching the user's words against hand-written patterns. This
file measures that on wording nobody wrote a pattern for:

* ``fixtures/capability_heldout_v1478/intents.json``: 47 everyday jobs and,
  for each, what counts as "the right thing was reachable" (a tool, a page
  step, or the true "not offered" line). Written before any score was read.
* ``requests.json``: requests for those jobs, written by a separate model
  (Claude Haiku, run from an empty folder, given only the plain job
  descriptions; it never saw this repository). Every label was then checked
  by hand, and a request that did not ask for its job was dropped.
* ``near_misses.json``: messages that use the same everyday words and ask
  for no change at all. None may be offered a tool that changes something.

Every row goes through the arming BOTH chat lanes call
(``chat_turn._resolve_armed_tools``, ``autoselect.select_ask_tools``,
``chat_turn.settings_ask_tools``) and ``daemon/page_doors``. No model runs.

The set is HELD OUT: never add a pattern because a row here misses. A miss is
the measurement. The floors below only stop the tables getting worse; the
plan's later phases are judged on this same set with a real model.

Print the table:  IJ_REACH_REPORT=out.json uv run pytest -q -s <this file>
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon import page_doors as PD
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.tools.autoselect import select_ask_tools
from iron_jarvis.tools.base import Reversibility

pytestmark = pytest.mark.usefixtures("no_network")

HERE = Path(__file__).parent / "fixtures" / "capability_heldout_v1478"

#: Tools that read and change nothing, although they do not declare
#: themselves read-only (read from each one's own description). Every other
#: tool that is not flagged read-only counts as one that changes something.
READERS = frozenset({
    "app_search", "app_status", "blackboard_read", "browse", "computer_use_status",
    "file_search", "goal_list", "guide_read", "guide_search", "image_info",
    "integration_list", "list_agents", "memory_read", "memory_search", "recall",
    "recall_lessons", "secret_list", "tool_list", "view_image", "web_extract",
    "web_fetch", "web_search", "worklist_next", "worklist_status",
})

#: MEASURED at v1.477.2 (2026-11-12): 50 of 164 requests reached (English 48
#: of 135, other languages 2 of 29; tools 44 of 138, page steps 5 of 14,
#: "not offered" lines 1 of 12), and 7 of 99 near-misses were offered a tool
#: that changes something. The bounds below are deliberately loose: they keep
#: the measurement running and catch a collapse, they are not a quality gate
#: (a matcher fix elsewhere may move a row or two either way).
BASELINE_REACHED = 40
BASELINE_NEAR_MISS_WRITERS = 12


def _load(name: str):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


INTENTS = {i["id"]: i for i in _load("intents.json")}
REQUESTS = _load("requests.json")
NEAR_MISSES = _load("near_misses.json")


@pytest.fixture(scope="module")
def d(tmp_path_factory):
    app = create_app(str(tmp_path_factory.mktemp("reach") / "home"))
    return SimpleNamespace(platform=app.state.platform)


def offered(d, text: str) -> set[str]:
    """What the stream lane offers by the arming both lanes call (the POST
    lane's whole arming plus the stream lane's ask tier)."""
    body = ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True)
    armed = list(CT._resolve_armed_tools(d, body)[0])
    ask = [t for t in select_ask_tools(text) if t not in armed]
    ask += CT.settings_ask_tools(d, text, armed, ask)
    return set(armed) | set(ask)


def writers(d, names: set[str]) -> list[str]:
    out = []
    for name in sorted(names):
        tool = d.platform.registry.get(name)
        rev = getattr(tool, "reversibility", Reversibility.IRREVERSIBLE)
        if name in READERS or rev in (Reversibility.READONLY, Reversibility.READONLY.value):
            continue
        out.append(name)
    return out


def reached(d, row: dict) -> tuple[bool, dict]:
    intent = INTENTS[row["intent"]]
    text = row["text"]
    tools = offered(d, text)
    steps = {s.id for s in PD.page_steps(text)}
    honest = {n.id for n in PD.not_offered(text)}
    ok = bool(
        tools & set(intent["tools"])
        or steps & set(intent["steps"])
        or honest & set(intent["honest"])
    )
    return ok, {"offered": sorted(tools), "steps": sorted(steps), "honest": sorted(honest)}


def measure(d) -> dict:
    rows = []
    for row in REQUESTS:
        ok, seen = reached(d, row)
        rows.append({**row, "reached": ok, **seen})
    near = []
    for row in NEAR_MISSES:
        w = writers(d, offered(d, row["text"]))
        near.append({**row, "writers": w})
    by_lang: dict[str, list[int]] = {}
    for r in rows:
        got = by_lang.setdefault(r["lang"], [0, 0])
        got[0] += int(r["reached"])
        got[1] += 1
    by_kind: dict[str, list[int]] = {}
    for r in rows:
        got = by_kind.setdefault(INTENTS[r["intent"]]["kind"], [0, 0])
        got[0] += int(r["reached"])
        got[1] += 1
    missed_by_intent = Counter(r["intent"] for r in rows if not r["reached"])
    return {
        "requests": len(rows),
        "reached": sum(r["reached"] for r in rows),
        "by_lang": by_lang,
        "by_kind": by_kind,
        "missed_by_intent": dict(missed_by_intent.most_common()),
        "near_misses": len(near),
        "near_misses_offered_a_writer": sum(bool(n["writers"]) for n in near),
        "rows": rows,
        "near": near,
    }


def test_the_set_is_well_formed(d):
    names = set(d.platform.registry.names()) if hasattr(d.platform.registry, "names") else None
    for intent in INTENTS.values():
        assert intent["kind"] in ("tool", "step", "honest"), intent
        assert intent["tools"] or intent["steps"] or intent["honest"], intent
        for tool in intent["tools"]:
            assert d.platform.registry.get(tool) is not None, (intent["id"], tool)
        assert set(intent["steps"]) <= {s.id for s in PD.STEPS}, intent
        assert set(intent["honest"]) <= {n.id for n in PD.NOT_OFFERED}, intent
    assert names is None or READERS <= names, sorted(READERS - names)
    ids = [r["id"] for r in REQUESTS] + [r["id"] for r in NEAR_MISSES]
    assert len(ids) == len(set(ids))
    for row in REQUESTS:
        assert row["intent"] in INTENTS, row
        assert row["lang"] in ("en", "es", "fr", "pt", "de"), row
        assert row["text"].strip(), row
    texts = [r["text"].strip().lower() for r in REQUESTS + NEAR_MISSES]
    assert len(texts) == len(set(texts)), "a message appears twice"
    assert len(REQUESTS) >= 150 and len(NEAR_MISSES) >= 90
    assert sum(r["lang"] != "en" for r in REQUESTS) >= 25


def test_the_reach_of_todays_word_tables(d, capsys):
    got = measure(d)
    path = os.environ.get("IJ_REACH_REPORT", "")
    if path:
        Path(path).write_text(json.dumps(got, indent=1, ensure_ascii=False), encoding="utf-8")
    with capsys.disabled():
        print(f"\nheld-out requests reached: {got['reached']} of {got['requests']}")
        for lang, (ok, n) in sorted(got["by_lang"].items()):
            print(f"  {lang}: {ok} of {n}")
        for kind, (ok, n) in sorted(got["by_kind"].items()):
            print(f"  kind {kind}: {ok} of {n}")
        print(f"near-misses offered a tool that changes something: "
              f"{got['near_misses_offered_a_writer']} of {got['near_misses']}")
    # Floors only: the tables must not get worse on wording they never saw.
    assert got["reached"] >= BASELINE_REACHED, got["missed_by_intent"]
    assert got["near_misses_offered_a_writer"] <= BASELINE_NEAR_MISS_WRITERS, [
        (n["text"], n["writers"]) for n in got["near"] if n["writers"]
    ]
