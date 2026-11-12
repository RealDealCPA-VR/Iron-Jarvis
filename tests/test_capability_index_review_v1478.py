"""INDEPENDENT REVIEW of the capability search plan, Phase 1 (v1.478.0).

Written by the reviewer, who did not build ``src/iron_jarvis/capability_search/``
(named ``capabilities/`` when it was reviewed).
Two parts:

* DEFECTS: one test per defect. Each FAILS on the code as reviewed and says,
  in its docstring, what is wrong and why it matters.
* GUARDS: guarantees the builder's own file states and never drives. They
  PASS on the code as reviewed; they are here so a later change cannot break
  the guarantee quietly.

Nothing here reads or tunes to ``fixtures/capability_heldout_v1478``.
"""

from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

import pytest

from iron_jarvis.capability_search import index as CI
from iron_jarvis.capability_search import tool as CT_TOOL
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import schema

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]


@pytest.fixture(scope="module")
def shared_app(tmp_path_factory):
    return create_app(str(tmp_path_factory.mktemp("capreview") / "home"))


@pytest.fixture(scope="module")
def platform(shared_app):
    """Read-only use only: a test that changes anything builds its own app."""
    return shared_app.state.platform


def _line_for(text: str, needle: str) -> str:
    return next((ln for ln in text.splitlines() if needle in ln), "")


# =========================================================================== #
# DEFECTS (each fails on the reviewed code)
# =========================================================================== #
def test_a_users_own_command_tool_is_never_indexed(tmp_path):
    """DEFECT 1 (real harm: the stated safety rule is not kept).

    ``index.py``, ``tool.py``, the Handbook and CLAUDE.md all say a user's
    own command tool is NOT in the index, because its words are someone
    else's. The index leaves out names that hold ":" (``custom:…``). But
    ``custom:<name>`` is only the PERMISSION key: both creation doors
    (``tool_create`` and ``POST /tools/custom``) register the tool under its
    plain name (``tools/dynamic.CommandTool.name = record.name``,
    ``registry.register(..., custom=True)``). So every command tool a user
    or an agent ever made IS indexed, its agent-written description decides
    its rank, and the unfenced answer presents it under "What Iron Jarvis
    has". Phase 2 would then join it into the reply. The builder's own test
    registers a tool named ``custom:pay``, a name the product never makes.
    """
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    create = p.registry.get("tool_create")
    ctx = SimpleNamespace(session_id="review")
    made = asyncio.run(create.execute({
        "name": "wire_the_money_now",
        "description": "zzmarker export the quarterly payroll report for the accountant",
        "command": [sys.executable, "-c", "pass"],
    }, ctx))
    assert made.ok, made.error
    assert "wire_the_money_now" in p.registry.custom_names()  # the real door made it

    ids = {e.id for e in CI.index_for(p).entries}
    text, data = CT_TOOL.answer(p, "export the quarterly payroll report zzmarker", 8)
    assert "wire_the_money_now" not in ids, "a user-made command tool is in the index"
    assert "wire_the_money_now" not in text and "wire the money now" not in text, text
    assert not any(m["id"] == "wire_the_money_now" for m in data["matches"])


@pytest.mark.asyncio
async def test_looking_things_up_never_makes_another_tool_join_the_reply(tmp_path):
    """DEFECT 2 (real harm, small: "nothing joins" is not true).

    Phase 1's promise is that the model "holds exactly the tools it held
    before" a lookup. ``chat_turn.offer_script_tool`` (v1.473.0) adds
    ``tool_script`` to a turn, ARMED AND GRANTED, once the model has made
    four successful calls of one armed read-only tool. ``find_capability``
    is such a tool, so four lookups in one reply make the script tool join.
    The builder's lane test makes ONE lookup and so never sees it. The plan's
    own bound ("2 lookups per reply") is not in the code either.
    """
    from tests.test_script_power_lanes_v1473 import Model, _body, _call, _stream

    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    app.state.platform.config.capability_search = True
    asks = ["make a new agent", "text size", "connect an app", "run something every Monday"]
    model = Model([*[_call(n, CT_TOOL.NAME, query=q) for n, q in enumerate(asks, 1)], "Here is what I found."])
    await _stream(app, _body(ws, "hello there"), model)
    assert CT_TOOL.NAME in model.offered[0]
    assert len(model.offered) == 5, model.offered
    joined = sorted(set(model.offered[-1]) - set(model.offered[0]))
    assert joined == [], f"after four lookups the reply gained: {joined}"


def test_one_thing_and_several_of_it_are_the_same_search(platform):
    """DEFECT 3 (plain search bug, not tuning).

    ``_stem`` strips ONE suffix, trying "ing", "ed", "es", "s" in that
    order. So it is not stable ("settings" -> "setting", but "setting" ->
    "sett") and it cuts the "e" off a plural but not off the word itself
    ("files" -> "fil", "file" -> "file"; "changes"/"changed" -> "chang",
    "change" -> "change"). A model that writes "settings" finds NO setting
    row in the first eight answers, while "setting" finds five; "rename
    files" and "rename a file" are different searches. English words that
    end in "e" (file, schedule, change, rename, update, page, rule, note,
    save, create, delete, pause) are most of this product's vocabulary.
    """
    pairs = [("setting", "settings"), ("file", "files"), ("schedule", "schedules"),
             ("change", "changes"), ("page", "pages"), ("rule", "rules"), ("update", "updates")]
    apart = [(a, b, CI.tokens(a), CI.tokens(b)) for a, b in pairs if CI.tokens(a) != CI.tokens(b)]
    one = [e.id for e in CI.search(platform, "setting", 8)]
    many = [e.id for e in CI.search(platform, "settings", 8)]
    assert not apart and one == many, {"stemmed apart": apart, "setting": one, "settings": many}


@pytest.mark.parametrize("query", ["what can Iron Jarvis do", "use tools", "call"])
def test_a_request_made_of_common_words_is_not_told_nothing_matches(platform, query):
    """DEFECT 4 (wrong words).

    The stop list holds "what", "can", "do", "use", "tool(s)", "call",
    "iron", "jarvis", "user"… A query made only of those has no search
    words left, and the answer is the same as for a true miss: "Nothing in
    Iron Jarvis matches that … say plainly that you found no feature for
    it". For the tool whose card reads "look up what Iron Jarvis can do",
    the query "what can Iron Jarvis do" is told nothing in Iron Jarvis
    matches. The model then tells the user a feature does not exist, which
    is the answer this tool was built to stop. The honest answer is that
    the query had no words to search with.
    """
    text, data = CT_TOOL.answer(platform, query, 5)
    assert data["matches"] == []
    assert "Nothing in Iron Jarvis matches" not in text, text


def test_a_search_that_failed_is_not_said_as_nothing_matches(platform, monkeypatch):
    """DEFECT 5 (wrong words; rare).

    ``index.search`` turns EVERY fault into ``[]`` ("a fault is no match"),
    and ``build`` drops a whole part (all tools, say) on any fault and the
    result is then kept for as long as the tool list does not change.
    ``answer`` cannot tell either from a true miss and tells the model to
    "say plainly that you found no feature for it". A fault must be said as
    a fault ("the lookup could not be read"), never as "Iron Jarvis has
    nothing for this": the user is told a feature does not exist.
    """
    def boom(self, query, limit=5):
        raise RuntimeError("index unreadable")

    monkeypatch.setattr(CI.CapabilityIndex, "search", boom)
    text, _data = CT_TOOL.answer(platform, "make a new agent", 5)
    assert "Nothing in Iron Jarvis matches" not in text, text


def test_a_setting_with_protected_values_says_so(platform):
    """DEFECT 6 (wrong words).

    A settings row's tier is per VALUE (``SettingDef.tier_for``): fifteen
    rows have ``floor_when`` values that only ``config_change_protected``
    may set (the update channel to Fresh, approvals to Don't ask, Start with
    Windows on…). ``config_list`` already tells the model both the tool and
    the ``protected_values``. The lookup line names only the row's base
    tool: "Setting "Update channel" …: changed with tool config_set", with
    no word that Fresh needs the protected tool and always asks. In Phase 3
    the plan joins "by its own tier, the right change tool" from this same
    field. The builder's test ``test_a_setting_names_the_tool_of_its_own_tier``
    compares the entry with the very table it was built from.
    """
    floored = [r for r in schema.SETTINGS if not r.pattern and r.floor_when and r.tier != "ask-floor"]
    assert floored, "no row has protected values any more: drop this test"
    silent = []
    for row in floored:
        line, _row = CT_TOOL.entry_line(platform, next(
            e for e in CI.index_for(platform).entries if e.kind == "setting" and e.id == row.key
        ))
        if "config_change_protected" not in line:
            silent.append((row.key, line))
    assert not silent, silent[:3]


def test_a_setting_line_says_when_its_change_tool_is_set_to_never_run(tmp_path):
    """DEFECT 7 (wrong words).

    A tool line reads the permission table ("The user has set it to Never
    run."). A setting line reads the same table only for "ask". With
    ``config_set`` set to Never run, the lookup still answers "changed with
    tool config_set", and HOW_TO_USE tells the model to say Iron Jarvis can
    do it. The user set that tool to never run.
    """
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.permissions._base["config_set"] = "deny"
    text, data = CT_TOOL.answer(p, "text size", 5)
    line = _line_for(text, "device.text_size")
    assert line, text
    assert next(m for m in data["matches"] if m["id"] == "device.text_size")["mode"] == "deny"
    assert "Never run" in line, line


def test_a_page_line_never_points_at_a_press_under_this_reply(platform):
    """DEFECT 8 (wrong words: a sentence that is untrue in this answer).

    The install steps' sentence was written for the chat's page door: "the
    "Install Claude Code for me" press … (on the chat page, also under this
    reply)". That press is drawn only when ``with_page_door`` matched the
    USER's words. A lookup joins nothing, so a reply that learned of the
    step from ``find_capability`` has no press under it, and the model
    tells the user to press a button that is not there (the v1.455.0 review
    fixed exactly this for the prompt block).
    """
    text, data = CT_TOOL.answer(platform, "install Claude Code", 8)
    assert any(m["id"] == "install_claude_code" for m in data["matches"]), data
    assert "under this reply" not in text, _line_for(text, "under this reply")


def test_a_program_already_installed_gets_no_install_step(platform, monkeypatch):
    """DEFECT 9 (wrong words).

    ``page_doors.prompt_block`` and ``with_page_door`` drop the install step
    for a program that is already on this PC (``step_not_needed``, read from
    the remembered answer ``known_cli_presence``), because the press it
    names is drawn only while the program is missing. The index holds every
    step always, so with Claude Code installed the lookup still answers
    "Iron Jarvis can install Claude Code on this PC: the "Install Claude
    Code for me" press beside Claude Code in Settings › Connections", a
    press that is not on that page.
    """
    from iron_jarvis.providers import manager

    monkeypatch.setattr(manager, "known_cli_presence", lambda binary: True)
    text, data = CT_TOOL.answer(platform, "install Claude Code", 8)
    assert not any(m["id"] == "install_claude_code" for m in data["matches"]), text


def test_a_limit_no_number_can_hold_is_not_a_crash():
    """DEFECT 11 (nit).

    ``execute`` guards ``int(limit)`` against TypeError and ValueError only.
    JSON ``1e999`` reads as infinity in Python, and ``int(inf)`` raises
    OverflowError, which leaves ``execute`` as a raw exception although
    ``answer`` promises "never raises". Every other odd limit falls back to
    5 or is clamped.
    """
    tool = CT_TOOL.FindCapabilityTool(None)
    got = asyncio.run(tool.execute({"query": "text size", "limit": float("inf")}, None))
    assert got.ok and 1 <= len(got.data["matches"]) <= CI.MAX_RESULTS


@pytest.mark.parametrize("message", [
    "let Jarvis look up the rates and update what we can do for the client",
    "stop, and let Jarvis look this up before we do anything",
    "Change the plan: let Jarvis look up what the client can do under the new rule",
])
def test_with_the_setting_off_an_everyday_sentence_arms_no_settings_writer(platform, message):
    """DEFECT 10 (real harm, small: "with it off nothing in chat is different"
    is not true).

    The settings trigger is built from every schema row's label
    (``settings/trigger._index``). The new row's label, "Let Jarvis look up
    what it can do", is four everyday words in order: let, Jarvis, look, up.
    So with ``capability_search`` OFF (the default), a message that says
    "let Jarvis look up …" next to any change word now arms ``config_list``,
    ``config_set`` and ``config_secret``, where v1.477.2 armed none of them.
    Arming is granting: ``config_set`` then changes an allow-tier setting
    with no card. CLAUDE.md's own rule (v1.434.0): "a row with everyday words
    needs a negative row in the table"; the arming table was not touched and
    the builder's three negatives avoid this shape.
    """
    from iron_jarvis.daemon import chat_turn as CT
    from iron_jarvis.daemon.schemas import ChatBody
    from iron_jarvis.settings.trigger import settings_ask

    assert platform.config.capability_search is False
    body = ChatBody(messages=[{"role": "user", "content": message}], auto_tools=True)
    armed = list(CT._resolve_armed_tools(SimpleNamespace(platform=platform), body)[0])
    assert settings_ask(message).keys in ((), ("capability_search",)), "another row matched: pick another sentence"
    assert not {"config_set", "config_secret", "config_list"} & set(armed), armed


# =========================================================================== #
# GUARDS the builder's file states and never drives (each passes today)
# =========================================================================== #
@pytest.mark.asyncio
async def test_a_found_tool_the_reply_does_not_hold_is_refused_when_called(tmp_path):
    """GUARD (test gap): Phase 1 answers only. The builder's lane test checks
    the OFFERED list; nothing drives the model doing what a web page would
    tell it to: call the changing tool the lookup just named. Both lanes
    must refuse it through the registry's own gate, and nothing is written.
    """
    from tests.test_script_power_lanes_v1473 import Model, _body, _call, _post, _rows, _stream

    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    app.state.platform.config.capability_search = True
    for lane in ("stream", "post"):
        target = ws / f"planted-{lane}.txt"
        model = Model([
            _call(1, CT_TOOL.NAME, query="write a file"),
            _call(2, "write_file", path=str(target), content="planted"),
            "Done.",
        ])
        body = _body(ws, "hello there", workspace_dir="")
        if lane == "stream":
            _frames, cards, _done = await _stream(app, body, model)
            assert cards == [], cards
        else:
            await _post(app, body, model)
        assert "write_file" not in model.offered[0], lane
        assert "tool write_file" in model.tool_texts[1][-1], "the lookup did name it"
        assert not target.exists(), f"{lane}: a tool found by lookup ran"
        assert "write_file" not in model.offered[-1], lane
    assert not [r for r in _rows(app, "write_file") if r.ok]


def test_a_very_long_query_is_answered_quickly(platform):
    """GUARD: a megabyte of text is cut to QUERY_CHARS; the answer and its
    data never carry more than that, and it does not take seconds."""
    CI.index_for(platform)  # built before the clock starts
    huge = "make a new agent " + "schedule backup theme " * 50_000
    started = time.perf_counter()
    text, data = CT_TOOL.answer(platform, huge, 5)
    took = time.perf_counter() - started
    assert len(data["query"]) <= CI.QUERY_CHARS and len(text) < 20_000
    assert took < 2.0, took
    for odd in ("???", "!!! ... ---", "\u00e9\u00e8 \u4f60\u597d \u043f\u0440\u0438\u0432\u0435\u0442", "\x00\x01"):
        assert CT_TOOL.answer(platform, odd, 5)[1]["matches"] == []
    for limit in (0, -3, 10**9, True):
        assert 1 <= len(CI.search(platform, "setting", limit)) <= CI.MAX_RESULTS


def test_the_tier_table_is_the_settings_tools_own(platform):
    """GUARD (the builder's tier test is circular): ``SETTING_TOOL`` must be
    the table the settings tools themselves refuse by."""
    from iron_jarvis.settings.tools import TOOL_FOR_TIER

    assert CI.SETTING_TOOL == TOOL_FOR_TIER


def test_an_answer_never_carries_a_settings_value_or_a_description(tmp_path):
    """GUARD: the answer holds labels, keys, names and page sentences; never
    a setting's VALUE and never a tool's own description."""
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.config.obsidian_vault = "C:/ZZSECRETVAULT/notes"
    p.config.backup_mirror_dir = "D:/ZZSECRETMIRROR"
    for query in ("obsidian vault notes folder", "backup copy folder", "memory base"):
        text, data = CT_TOOL.answer(p, query, 8)
        assert "ZZSECRET" not in text and "ZZSECRET" not in str(data), query
