"""v1.426.0 (Wave 6, E3) REVIEW — the conversation a terminal moved to is
read from what Claude Code WRITES when it starts one, never from "a newer
file in the same folder", and from the home the terminal's Claude really uses.

THE FINDINGS (each pin red on the builder's first cut):

* "Open in Code view" took ANY conversation written in the terminal's folder
  after the recorded one as the terminal's own. The user's own Claude Code,
  running OUTSIDE Iron Jarvis in the same folder (the Claude desktop app, a
  VS Code panel — the owner works this way every day), writes such files all
  the time: with no ``/clear`` in the terminal at all, its conversation was
  handed over as the terminal's (and the Code view could then resume it while
  that Claude was still writing it: a second writer), and a running terminal
  "held" it, refusing to open it with a sentence that blamed the terminal.
  MEASURED on this PC's own Claude Code transcripts (2.1.280, 2.1.296): a
  conversation a ``/clear`` started BEGINS with a meta caveat record and then
  the ``<command-name>/clear</command-name>`` record, both carrying the NEW
  session's id; an ordinary conversation never does. Only such a conversation
  is the terminal's (``service.started_by_clear``).
* A terminal on "this PC's login" (``source: "default"``) REMOVES an
  inherited ``CLAUDE_CONFIG_DIR`` (``terminals/pane_accounts``), so its
  Claude writes ``~/.claude``; the hand-over read the daemon's own
  ``CLAUDE_CONFIG_DIR`` instead (``home_of``) — another account's home — and
  handed over a conversation from there. The pane's home is
  ``terminals/continue_on.claude_home`` (the one rule the restart Resume and
  Continue on the next account already use).
"""

from __future__ import annotations

import json
import os
import time

from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from tests._build_app_helpers_v1409 import client
from tests.test_build_app_review_v1409 import (  # noqa: F401 — fixtures by name
    EXITED,
    RUNNING,
    _Pane,
    _panes,
    _write_claude,
    app,
    work,
)

OLD = "1a1a1a1a-1111-4222-8333-444444444444"
CLEARED = "1b1b1b1b-1111-4222-8333-444444444444"
OUTSIDE = "1c1c1c1c-1111-4222-8333-444444444444"

#: Claude Code's own records at the head of a conversation a /clear started
#: (the boilerplate copied from a real 2.1.296 transcript; no user text).
CAVEAT = (
    "<local-command-caveat>The command below was run directly in Claude Code, not sent to you as a "
    "request, and its output goes straight to the user. It's recorded here as context for later "
    "messages.</local-command-caveat>"
)
CLEAR_RECORD = (
    "<command-name>/clear</command-name>\n            <command-message>clear</command-message>\n"
    "            <command-args></command-args>"
)


def write_cleared(home, folder, sid: str, text: str = "after clear"):
    """A conversation as Claude Code writes it after `/clear`."""
    path = home / "projects" / "C--demo" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    base = {"sessionId": sid, "cwd": str(folder), "timestamp": "2026-10-29T10:00:00Z"}
    recs = [
        {"type": "mode", "sessionId": sid, "mode": "default"},
        {"type": "file-history-snapshot", "messageId": "m0", "snapshot": {}},
        {**base, "type": "user", "isMeta": True, "parentUuid": None,
         "message": {"role": "user", "content": CAVEAT}},
        {**base, "type": "user", "parentUuid": "u0", "message": {"role": "user", "content": CLEAR_RECORD}},
        {**base, "type": "system", "subtype": "local_command", "parentUuid": "u1"},
        {**base, "type": "user", "parentUuid": "u2", "message": {"role": "user", "content": text}},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    return path


def _aged(path, seconds_ago: float) -> None:
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


def _home(tmp_path, monkeypatch):
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


def test_only_a_conversation_a_clear_started_reads_as_one(tmp_path):
    cleared = write_cleared(tmp_path, tmp_path, CLEARED)
    plain = _write_claude(tmp_path, tmp_path, OUTSIDE, "fix the build")
    assert S.started_by_clear(cleared) is True
    assert S.started_by_clear(plain) is False
    # A conversation whose first words name another command is not a /clear.
    other = write_cleared(tmp_path, tmp_path, OLD)
    other.write_text(other.read_text(encoding="utf-8").replace("/clear", "/model"), encoding="utf-8")
    assert S.started_by_clear(other) is False
    assert S.started_by_clear(tmp_path / "nope.jsonl") is False


async def test_the_users_own_claude_outside_iron_jarvis_is_never_handed_over(app, work, tmp_path, monkeypatch):
    """No /clear in the terminal: the newer conversation in the folder is the
    user's OWN Claude Code (outside Iron Jarvis), never the terminal's."""
    home = _home(tmp_path, monkeypatch)
    _aged(_write_claude(home, work, OLD, "the terminal's question"), 120)
    _aged(_write_claude(home, work, OUTSIDE, "asked in the Claude app"), 10)
    pane = _Pane(work, EXITED, sid=OLD, pane_id="p")
    _panes(app, monkeypatch, {"p": pane})
    async with client(app) as c:
        r = await c.post("/build/app/from-terminal/p")
    assert r.status_code == 201, r.text
    assert r.json()["harness_session_id"] == OLD, "the user's own outside conversation was handed over"
    assert pane.claude_session_id == OLD


async def test_a_running_terminal_never_holds_the_users_own_outside_conversation(
        app, work, tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _aged(_write_claude(home, work, OLD), 120)
    _aged(_write_claude(home, work, OUTSIDE, "asked in the Claude app"), 10)
    _panes(app, monkeypatch, {"p": _Pane(work, RUNNING, sid=OLD, pane_id="p")})
    held = S.terminal_holds(app.state.platform, OUTSIDE, harness="claude-code", rows=[])
    assert held is False, "a terminal was said to hold a conversation it never wrote"


async def test_the_terminals_clear_is_found_beside_the_users_outside_conversation(
        app, work, tmp_path, monkeypatch):
    """The terminal's /clear made one conversation AND the user's own Claude
    wrote another in the folder: the /clear one is the terminal's."""
    home = _home(tmp_path, monkeypatch)
    _aged(_write_claude(home, work, OLD), 120)
    _aged(write_cleared(home, work, CLEARED), 30)
    _aged(_write_claude(home, work, OUTSIDE, "asked in the Claude app"), 5)
    pane = _Pane(work, EXITED, sid=OLD, pane_id="p")
    _panes(app, monkeypatch, {"p": pane})
    async with client(app) as c:
        r = await c.post("/build/app/from-terminal/p")
    assert r.status_code == 201, r.text
    assert r.json()["harness_session_id"] == CLEARED


async def test_a_this_pc_login_terminal_reads_its_own_home_never_the_daemons(app, work, tmp_path, monkeypatch):
    """The terminal is on this PC's login: its Claude writes ~/.claude (the
    inherited CLAUDE_CONFIG_DIR was removed). A /clear conversation in the
    daemon's CLAUDE_CONFIG_DIR (ANOTHER account's home) is never its."""
    user = tmp_path / "user"
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    other = tmp_path / "other-account"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(other))
    mine = user / ".claude"
    _aged(_write_claude(mine, work, OLD, "the terminal's question"), 60)
    _aged(write_cleared(other, work, CLEARED, "another account's conversation"), 10)
    pane = _Pane(work, EXITED, sid=OLD, pane_id="p",
                 accounts={"anthropic": {"id": None, "title": "This PC", "source": "default"}})
    _panes(app, monkeypatch, {"p": pane})
    async with client(app) as c:
        r = await c.post("/build/app/from-terminal/p")
    assert r.status_code == 201, r.text
    assert r.json()["harness_session_id"] == OLD, "a conversation of ANOTHER account's home was handed over"
    assert S.claude_pane_home(pane) == str(mine)


async def test_a_code_view_rows_own_cleared_conversation_is_still_never_the_panes(
        app, work, tmp_path, monkeypatch):
    """Even a conversation that BEGINS with /clear is a Code-view row's when a
    row holds it (the ``taken`` rule, now alone in deciding)."""
    home = _home(tmp_path, monkeypatch)
    _aged(_write_claude(home, work, OLD), 120)
    _aged(write_cleared(home, work, CLEARED), 10)
    store.create(app.state.platform.engine, harness="claude-code", folder=str(work), harness_session_id=CLEARED)
    pane = _Pane(work, EXITED, sid=OLD, pane_id="p")
    _panes(app, monkeypatch, {"p": pane})
    async with client(app) as c:
        r = await c.post("/build/app/from-terminal/p")
    assert r.status_code == 201 and r.json()["harness_session_id"] == OLD, r.text
