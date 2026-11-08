"""v1.455.0 (readiness plan 0j.5, Wave E): one word for each thing.

The dashboard's scanner (dashboard/__tests__/vocabulary.test.ts) reads what the
DASHBOARD says. These pins read what the DAEMON says on the same surfaces:

* the account notes the coding screens show (Chat › Code's account menu, a
  Build terminal's account chip and its refusals, Continue on the next account,
  Build › Code) say "your accounts" / "the Several accounts card" / "the account
  manager", never "Iron-Proxy" (VOCABULARY.md row 29). This is the daemon's
  twin of the dashboard scanner's scoped `onSurfaces("Iron-Proxy", …)` row: it
  reads only these four files, because Iron-Proxy stays the program's own name
  on its Connections card and in its own errors (``iron_proxy/*``);
* an MCP server is an "extension" (VOCABULARY.md row 22), never a "tool
  server", in chat's add door, the run words and Build › Code's notes;
* the behaviour the words ride on (a value's display name, a refusal's
  sentence, the run notices' subject) is driven through the real code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_vocabulary_beyond_dashboard_v13580 import py_user_strings

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iron_jarvis"

#: The daemon files whose sentences a coding screen shows (canon row 29).
CODING_ACCOUNT_FILES = (
    SRC / "harness" / "build_accounts.py",
    SRC / "terminals" / "pane_accounts.py",
    SRC / "terminals" / "continue_on.py",
    SRC / "hosted" / "words.py",
    # The 409s and refusals the same screens show (Choose account, Continue on
    # another account, a Build terminal's Launch on an account).
    SRC / "daemon" / "routes" / "build.py",
    SRC / "daemon" / "routes" / "terminals.py",
    SRC / "hosted" / "service.py",
    SRC / "hosted" / "codex_move.py",
)
IRON_PROXY = re.compile(r"\biron[- ]?proxy\b", re.I)

#: The files whose sentences name an MCP server to a person.
EXTENSION_FILES = (
    SRC / "settings" / "records_tools.py",
    SRC / "agents" / "mission.py",
    SRC / "daemon" / "change_claims.py",
    SRC / "hosted" / "tool_setup.py",
    SRC / "settings" / "schema.py",
)
TOOL_SERVER = re.compile(r"\btool[- ]servers?\b", re.I)


def _hits(paths, rx):
    out = []
    for p in paths:
        for line, text in py_user_strings(p.read_text(encoding="utf-8")):
            if rx.search(text):
                out.append(f"{p.relative_to(ROOT).as_posix()}:{line} {text[:110]!r}")
    return out


def test_the_coding_screens_account_notes_never_name_iron_proxy():
    assert not _hits(CODING_ACCOUNT_FILES, IRON_PROXY)


def test_the_reader_reaches_the_account_notes_it_exists_for():
    """Anti-vacuity: the reader really reads these files' sentences."""
    texts = [t for p in CODING_ACCOUNT_FILES for _l, t in py_user_strings(p.read_text(encoding="utf-8"))]
    assert any("Several accounts is off" in t for t in texts), texts[:20]
    assert any("account manager" in t for t in texts)
    planted = 'NOTE = "Iron-Proxy is off; turn it on in Connections."\n'
    assert [t for _l, t in py_user_strings(planted) if IRON_PROXY.search(t)]


def test_an_extension_is_never_a_tool_server_in_what_a_person_reads():
    assert not _hits(EXTENSION_FILES, TOOL_SERVER)


def test_the_build_account_notes_say_coding_session_and_your_accounts():
    from iron_jarvis.harness import build_accounts as BA

    for note in (BA.OFF_NOTE, BA.NOT_READ_NOTE, BA.UPDATE_NOTE, BA.GROUP_OFF, BA.GROUP_NOT_READ):
        assert not IRON_PROXY.search(note), note
        assert "Build session" not in note, note
    assert "Several accounts" in BA.OFF_NOTE and "coding sessions" in BA.OFF_NOTE


def test_a_pane_that_cannot_reach_the_account_manager_says_so_in_the_cards_words():
    from iron_jarvis.terminals import pane_accounts as PA

    class _Dropped(Exception):
        code = "UNREACHABLE"

    said = PA._unavailable_sentence(_Dropped())
    assert said == PA.NOT_ANSWERING_NOW and not IRON_PROXY.search(said)
    assert "Several accounts" in said and "this PC's own login" in said


def test_a_plan_default_model_reads_as_words_on_the_setting_card():
    from iron_jarvis.settings import schema, tools

    row = next(s for s in schema.SETTINGS if s.key == "default_model")
    assert tools.value_label(row, "subscription") == tools.PLAN_DEFAULT_MODEL_WORDS
    assert tools.value_label(row, "claude-opus-4-1") is None  # a real id keeps the card's own words


def test_the_run_notices_call_a_rule_a_rule():
    from iron_jarvis.comm import notifier

    subject = notifier._UNATTENDED_SUBJECT["reflex"]
    assert subject("invoice-drop") == "Your rule invoice-drop"
    assert subject("") == "A rule"


def test_the_users_own_model_server_has_one_name():
    from iron_jarvis.providers.failure_words import plain_name

    assert plain_name("custom") == "your model server"


def test_reading_scanned_pages_turned_off_names_no_setting_row_that_does_not_exist():
    from iron_jarvis.documents import attachment_rag, ocr

    for note in (ocr._DISABLED_NOTE, attachment_rag.OCR_DISABLED_NOTE):
        assert "ocr_enabled" not in note, note
        assert "reading scanned pages is turned off" in note


@pytest.mark.parametrize("frozen", [True, False])
def test_a_missing_part_is_fixed_by_a_reinstall_in_the_packaged_app(monkeypatch, frozen):
    import importlib
    import sys

    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")
    if frozen:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    else:
        monkeypatch.delattr(sys, "frozen", raising=False)
    fix = doctor._reinstall_fix("its reader for scanned pages", "Reinstall dependencies (`uv sync`).")
    if frozen:
        assert "uv sync" not in fix and "Reinstall it from the Updates page or the installer" in fix
    else:
        assert "uv sync" in fix


def test_the_renamed_settings_keep_their_old_names_as_search_words():
    """Renaming a label widens the chat settings trigger (it reads labels AND
    aliases); the old label stays an alias so a person's old words still find it."""
    from iron_jarvis.settings import schema

    rows = {s.key: s for s in schema.SETTINGS}
    for key, old in (
        ("autonomy_level", "autonomy ceiling"),
        ("autonomy_dry_run", "dry-run mode"),
        ("sentinels_enabled", "folder watchers (sentinels)"),
        ("build_round_budget", "steps per build session reply"),
        ("build_reflect", "learn from build sessions"),
        ("comm_trust", "runs started from inbound messages"),
        ("fleet_sampling_enabled", "fleet telemetry"),
        ("custom_base_url", "custom endpoint url"),
    ):
        assert old in [a.lower() for a in rows[key].aliases], key
        assert rows[key].label.lower() != old, key
