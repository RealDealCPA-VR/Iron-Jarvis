"""North-star plan 6.3 and 6.6: PAIRING, BACKUP, RESTORE, UPDATES AND THE
SMALL ACTIONS HAVE A WAY IN FROM CHAT (v1.506.0).

* Pairing with another computer was a "not offered" line (no door) whose
  words never matched "pair": it is a page step now, with a door to Settings ›
  Connections › Your other computers. ``another_computer`` stays for opening
  these screens on another computer, which is not offered.
* "Back up now" is a tool (``backup_now``, allow: it adds an archive and says
  where); RESTORE is never a tool (it replaces the database): a step with a
  door to the Maintenance card. The backup line now names restore.
* Installing or holding an update: a step with a door to the Updates page.
* The small actions with no tool (6.6): removing a key (never a tool: a key's
  value is never an argument and its removal can't be undone), forgetting a
  folder, deleting a project, templates and skill bundles each a step with a
  door; deleting, archiving or renaming a chat a true "not offered" line (the
  place is the list the user is looking at).

Every door is proven in both lanes' real replies.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iron_jarvis.daemon import page_doors as P
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.settings import setup_words as W
from iron_jarvis.settings.records import record_tools_for
from tests.test_north_star_pages_v1506 import _resolves
from tests.test_script_power_lanes_v1473 import Model, _body, _call, _post, _rows, _stream

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

BY_ID = {s.id: s for s in P.STEPS}
NEW_STEPS = ("pair_computer", "restore_backup", "updates", "remove_key", "forget_folder", "delete_project",
             "templates")


def _step(text):
    s = P.page_step(text)
    return s.id if s else None


# --------------------------------------------------------------------------- #
# the words
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,step", [
    ("pair this computer with my office PC", "pair_computer"),
    ("how do I link my two computers so they can share work?", "pair_computer"),
    ("where do I type the pairing code?", "pair_computer"),
    ("hand tasks to my office PC when this one is busy", "pair_computer"),
    ("restore Iron Jarvis from yesterday's backup", "restore_backup"),
    ("roll back to an earlier backup please", "restore_backup"),
    ("install the update", "updates"),
    ("hold the update until Friday", "updates"),
    ("is there a new version of Iron Jarvis?", "updates"),
    ("delete my OpenAI API key", "remove_key"),
    ("remove the GitHub token", "remove_key"),
    ("forget the Smith folder, I don't want it in your work record", "forget_folder"),
    ("delete the Hartley project", "delete_project"),
    ("make a new template", "templates"),
    ("load the starter skill bundle", "templates"),
])
def test_the_words_find_the_step(text, step):
    assert _step(text) == step, (text, _step(text))


@pytest.mark.parametrize("text", [
    "pair my headphones with my phone",
    "restore my files from the OneDrive backup",
    "install the Windows update on my laptop",
    "remove the password from this PDF",
    "I always forget my password",
    "make a template for an engagement letter",
    "delete the project files from the shared drive",
    "don't update the spreadsheet yet",
    "what is my API key for Stripe used for?",
])
def test_everyday_sentences_find_no_new_step(text):
    assert _step(text) not in NEW_STEPS, (text, _step(text))


def test_backup_now_is_armed_only_for_iron_jarviss_own_backup_now():
    assert "backup_now" in record_tools_for("back up Iron Jarvis now")
    assert "backup_now" in record_tools_for("take a backup now before I change the settings")
    for text in ("back up my client files now", "how often does it back up my settings?",
                 "schedule a weekly backup of my settings to my OneDrive folder"):
        assert "backup_now" not in record_tools_for(text), text


def test_deleting_a_chat_is_a_true_not_offered_line():
    got = [n.id for n in P.not_offered("delete this chat")]
    assert got == ["chat_items"], got
    assert not P.not_offered("delete this paragraph from the chat transcript I pasted")
    # The old pairing line stays for opening the screens elsewhere.
    assert [n.id for n in P.not_offered("open Jarvis from my laptop")] == ["another_computer"]


def test_every_new_step_is_true_and_leads_to_a_real_place():
    for sid in NEW_STEPS:
        step = BY_ID[sid]
        assert _resolves(step.href), (sid, step.href)
        assert "plainly" in step.line and ("cannot" in step.line or "does not" in step.line), sid
    backup = next(n.line for n in P.NOT_OFFERED if n.id == "jarvis_backup")
    assert "backup_now" in backup and "Restore from backup" in backup


def test_restore_is_never_a_tool(tmp_path):
    names = set(create_app(str(tmp_path / "home")).state.platform.registry.names())
    assert "backup_now" in names
    assert not [n for n in names if "restore" in n and n != "record_restore"], sorted(n for n in names if "restore" in n)


def test_the_new_patterns_are_linear():
    """Pathological input stays fast (each pattern's gaps are bounded)."""
    import time

    nasty = ("pair " + "my other " * 400 + "computer ") * 3 + ("restore " + "from the " * 300) + "x" * 1500
    t0 = time.perf_counter()
    for rx in W.ALL_PATTERNS:
        rx.search(nasty[: P.SCAN_CHARS])
    assert time.perf_counter() - t0 < 2.0


# --------------------------------------------------------------------------- #
# through the real lanes
# --------------------------------------------------------------------------- #
@pytest.fixture()
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    yield app, ws


def _plain(ws, text, **extra):
    body = _body(ws, text, **extra)
    body.pop("workspace_dir", None)
    return body


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["stream", "post"])
@pytest.mark.parametrize("text,sid", [
    ("pair this computer with my office PC", "pair_computer"),
    ("restore Iron Jarvis from yesterday's backup", "restore_backup"),
    ("install the update", "updates"),
    ("delete my OpenAI API key", "remove_key"),
])
async def test_the_step_door_rides_both_lanes(desk, lane, text, sid):
    app, ws = desk
    model = Model(["That is done on its page."])
    body = _plain(ws, text)
    done = (await _stream(app, body, model))[2] if lane == "stream" else await _post(app, body, model)
    assert BY_ID[sid].door() in done["doors"], done["doors"]
    assert BY_ID[sid].line in model.systems[0]


@pytest.mark.asyncio
async def test_back_up_now_from_chat_writes_a_backup_and_says_where(desk):
    app, ws = desk
    model = Model([_call(1, "backup_now"), "Backed up."])
    _f, cards, done = await _stream(app, _plain(ws, "back up Iron Jarvis now"), model)
    assert "backup_now" in model.offered[0] and cards == [], "allow tier: no card"
    said = model.tool_texts[1][-1]
    rows = [r for r in _rows(app, "backup_now") if r.ok]
    assert rows, said
    path = Path(said.split("Backed up Iron Jarvis to ", 1)[1].split(".tar.gz", 1)[0] + ".tar.gz")
    assert path.is_absolute() and path.exists(), said
    door = {"href": "/settings?section=system-maintenance", "label": "Open Maintenance in Settings"}
    assert door in done["doors"], done["doors"]
    # The POST lane carries the same door (an explicit pick there: the phone
    # does not arm a tool that writes).
    model = Model([_call(1, "backup_now"), "Backed up."])
    res = await _post(app, _plain(ws, "back up Iron Jarvis now", tools=["backup_now"]), model)
    assert door in res["doors"], res["doors"]
