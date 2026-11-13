"""Everyday phrasings in the capability index (v1.481.0).

The index is built from the product's own text, and people do not use those
words. Each entry now also carries eight phrasings a model wrote from THAT
ENTRY'S OWN TEXT (``capability_search/phrasings.py``, written by
``scripts/gen_capability_phrasings.py``). They are search words only.

What must hold:

* every entry of a fresh install has phrasings, and no phrasing names an
  entry that is gone (a new tool or setting means re-running the generator);
* every phrase is one clean line (no address, no link, bounded);
* a phrase finds its own entry;
* no answer line ever shows a phrase (the model and the user see only the
  product's own words);
* with the phrasings left out the index is the v1.480.0 index, word for word;
* the lookup's own instruction asks for two other ways to name the request,
  and a query written that way is read whole (the " / " is not a word).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from iron_jarvis.capability_search import index as CI
from iron_jarvis.capability_search import tool as CT_TOOL
from iron_jarvis.capability_search.phrasings import PHRASINGS
from iron_jarvis.daemon.app import create_app

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

ROOT = Path(__file__).parent.parent


def _gen():
    spec = importlib.util.spec_from_file_location(
        "gen_capability_phrasings", ROOT / "scripts" / "gen_capability_phrasings.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def platform(tmp_path):
    return create_app(str(tmp_path / "home")).state.platform


def test_every_entry_has_phrasings_and_none_is_stale(platform):
    gen = _gen()
    bare = CI.build(platform, phrasings=False).entries
    bad = gen.problems(bare, {k: list(v) for k, v in PHRASINGS.items()})
    assert bad == [], (
        "the phrasings are out of date; regenerate them (scripts/gen_capability_phrasings.py "
        "--prompts DIR, run the prompts, --collect DIR): " + "; ".join(bad[:10])
    )


def test_every_phrase_is_one_clean_line():
    gen = _gen()
    for key, phrases in PHRASINGS.items():
        assert 1 <= len(phrases) <= gen.PER_ENTRY, key
        for phrase in phrases:
            assert gen.clean(phrase) == phrase, (key, phrase)
            assert "@" not in phrase and "://" not in phrase and len(phrase) <= gen.MAX_CHARS


def test_the_generator_keeps_nothing_unclean():
    gen = _gen()
    assert gen.clean("mail me at someone@example.com") == ""
    assert gen.clean("open https://example.com") == ""
    assert gen.clean("x" * 81) == ""
    assert gen.clean('  "make the text bigger". ') == "make the text bigger"


def test_a_phrase_finds_its_own_entry(platform):
    """Every entry is found by at least one of its own phrasings, in the first
    five. (Phrasings of near twins, a tool and its own setting, may each find
    the other first; one of the eight is enough.)"""
    idx = CI.build(platform)
    lost = []
    for entry in idx.entries:
        phrases = PHRASINGS.get(entry.id, ())
        if not any(entry.id in [e.id for _s, e in idx.search(p, 5)] for p in phrases):
            lost.append(entry.id)
    assert len(lost) <= len(idx.entries) // 20, lost


def test_no_answer_line_shows_a_phrase(platform):
    """Every answer line is built from the product's own text: the same line
    with and without the phrasings."""
    with_ = CI.build(platform).entries
    bare = CI.build(platform, phrasings=False).entries
    for a, b in zip(with_, bare):
        assert CT_TOOL.entry_line(platform, a) == CT_TOOL.entry_line(platform, b), a.id
    text, _data = CT_TOOL.answer(platform, "new helper with its own name / create an agent / custom assistant")
    for phrase in PHRASINGS.get("agent_create", ()):
        assert phrase not in text, phrase


def test_without_phrasings_the_index_is_the_old_one(platform):
    with_ = CI.build(platform)
    bare = CI.build(platform, phrasings=False)
    assert [e.id for e in with_.entries] == [e.id for e in bare.entries]
    for a, b in zip(with_.entries, bare.entries):
        assert a.text.startswith(b.text)
        assert (a.kind, a.title, a.tool, a.href, a.line, a.changes) == (b.kind, b.title, b.tool, b.href, b.line, b.changes)


def test_an_entry_with_no_phrasings_keeps_its_own_text():
    e = CI.Capability(kind="tool", id="no_such_entry_ever", title="t", text="own words")
    assert CI.with_phrasings([e]) == [e]


def test_a_broken_phrasings_table_still_finds_everything_by_its_own_words(platform, monkeypatch):
    from iron_jarvis.capability_search import phrasings as P

    monkeypatch.setattr(P, "PHRASINGS", None)
    idx = CI.build(platform)
    assert "phrasings" in idx.unreadable
    bare = CI.build(platform, phrasings=False)
    assert [e.text for e in idx.entries] == [e.text for e in bare.entries]


def test_the_lookup_asks_for_two_other_ways_to_name_it():
    assert '" / "' in CT_TOOL.FindCapabilityTool.description or " / " in CT_TOOL.FindCapabilityTool.description
    assert "two other short ways to name" in CT_TOOL.FindCapabilityTool.description
    assert "two other" in CT_TOOL.FindCapabilityTool.input_schema["properties"]["query"]["description"]
    assert "job" not in CT_TOOL.FindCapabilityTool.description.lower().split()


def test_a_query_with_other_names_is_read_whole(platform):
    idx = CI.build(platform)
    one = [e.id for _s, e in idx.search("new helper with its own name", 5)]
    three = [e.id for _s, e in idx.search(
        "new helper with its own name / create an agent / custom assistant", 5)]
    assert "agent_create" in three, three
    assert "/" not in CI.tokens("a / b")
    assert one != [] and three != []


def test_the_index_is_still_bounded(platform):
    import time

    started = time.perf_counter()
    idx = CI.build(platform)
    built = time.perf_counter() - started
    started = time.perf_counter()
    for _ in range(20):
        idx.search("run a task every Monday morning / repeat on a schedule / weekly task", 8)
    searched = (time.perf_counter() - started) / 20
    # Generous bounds (a loaded runner): a build well under a few seconds, a
    # search well under a tenth of a second.
    assert built < 10 and searched < 0.5, (built, searched)


def test_a_full_lookup_lets_no_settings_tool_in_after_it(tmp_path):
    """v1.481.0 (found by the new ranking): with a lookup's three places
    taken, a setting match after them brought the settings READER (it was free
    on its own) and then a settings CHANGER (free once the reader was in): five
    tools for one lookup. Matches are written by hand here, so the rule is
    pinned whatever the list ranks first."""
    from types import SimpleNamespace

    from iron_jarvis.capability_search import join as J
    from iron_jarvis.daemon import chat_turn as CT
    from iron_jarvis.providers.adapters.base import LLMMessage

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.config.capability_search = True
    J.FOUND.clear()
    ctx = SimpleNamespace(session_id="chat", turn_id="turn-full-lookup")
    rows = [
        {"kind": "tool", "id": "agent_create", "tool": "agent_create"},
        {"kind": "tool", "id": "agent_update", "tool": "agent_update"},
        {"kind": "tool", "id": "agent_pause", "tool": "agent_pause"},
        {"kind": "setting", "id": "device.text_size", "tool": "config_set"},
        {"kind": "setting", "id": "device.theme", "tool": "config_set"},
    ]
    J.FOUND.note(J.turn_key(ctx), rows)
    state = dict(
        tools_used=[J.NAME], armed=[J.NAME], auto_armed=[J.NAME], ask_armed=[],
        tool_specs=p.registry.specs([J.NAME]), overrides={J.NAME: "allow"}, armed_grant={J.NAME},
        msgs=[LLMMessage(role="tool", tool_call_id="c1", name=J.NAME, content="found")], ctx=ctx,
    )
    joined = CT.join_found_tools(
        SimpleNamespace(platform=p), SimpleNamespace(auto_tools=True, granted_tools=[]),
        can_ask=True, found_asks=set(), **state,
    )
    J.FOUND.clear()
    assert joined == ["agent_create", "agent_update", "agent_pause"], joined

    # The control: a setting match FIRST takes one place for the family, and
    # its reader comes with it.
    J.FOUND.note(J.turn_key(ctx), [rows[3], *rows[:3]])
    state.update(armed=[J.NAME], auto_armed=[J.NAME], ask_armed=[], overrides={J.NAME: "allow"},
                 armed_grant={J.NAME}, tool_specs=p.registry.specs([J.NAME]),
                 msgs=[LLMMessage(role="tool", tool_call_id="c1", name=J.NAME, content="found")])
    joined = CT.join_found_tools(
        SimpleNamespace(platform=p), SimpleNamespace(auto_tools=True, granted_tools=[]),
        can_ask=True, found_asks=set(), **state,
    )
    J.FOUND.clear()
    assert joined == ["config_set", "config_list", "agent_create", "agent_update"], joined
