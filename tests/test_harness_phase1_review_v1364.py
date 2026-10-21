"""v1.364.0 (IJ harness Phase 1) — the REVIEW's pins. Each one is a defect
the review found in the doer's commit, red without its fix.

1. THE CHAT SAYS WHAT THE NEW EDITORS CHANGED. A reply's "N files changed"
   line and the files rail read the turn's ``documents`` (v1.329.0 F4), which
   both chat lanes fill through ``chat_turn._reports_document``. It knew
   ``edit_file`` only, so a turn that changed files with ``multi_edit``,
   ``notebook_edit`` or ``apply_patch`` listed nothing (an apply_patch that
   only CHANGED files listed nothing at all; one that created a file listed
   that file alone, through ``created_paths``). Now
   ``chat_turn.reported_document_paths`` answers for every editor, and every
   file a patch created or changed — never one it deleted. Lock-step: both
   lanes.
2. THE HONESTY NOTE NEVER ACCUSES A NEW EDITOR. ``_FILE_CHANGING_TOOLS``
   (v1.351.0 G06) did not know them, so a reply saying "I created notes.md"
   after apply_patch had created it drew "no document-writing tool ran — so
   that file does not exist".
3. "ASK BEFORE FILE EDITS" CARDS THEM. The editors sit at edit_file's tier
   (allow); ``STRICT_ASK_TOOLS`` is what the ``always_ask`` posture cards on
   top of the ask tier, and it did not name them — a patch changed files
   uncarded in the posture whose promise is the opposite. The card says what
   they do (toolWords == mission.ASK_WORDS == desktop TOOL_WORDS).
4. ALL OR NOTHING INCLUDES THE FILE IN FLIGHT. ``apply_patch``'s rollback
   put back the files it had FINISHED writing, not the one whose write
   failed part way — ``write_bytes`` truncates on open, so a full disk left
   that file cut short under "what it had written was put back".
5. THE PATCH UNDO TOUCHES NOTHING UNTIL IT HAS EVERY SAVED COPY. A missing
   blob raised half way, after earlier files had already been put back.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import mission
from iron_jarvis.core.config import Config
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    _FILE_CHANGING_TOOLS,
    STRICT_ASK_TOOLS,
    _claimed_write_note,
    _reports_document,
    reported_document_paths,
)
from iron_jarvis.harness.tools import ApplyPatchTool
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.undo import patch_entries, undo_dir

from tests.test_approval_modes_v1188 import _body as _mode_body
from tests.test_approval_modes_v1188 import _calls_stream
from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream

EDITORS = ("multi_edit", "apply_patch", "notebook_edit")


def _patch(*lines: str) -> str:
    return "\n".join(["*** Begin Patch", *lines, "*** End Patch"])


# --------------------------------------------------------------------------- #
# 1. documents — the helper, then both lanes end to end.
# --------------------------------------------------------------------------- #


def test_every_editor_is_reported_and_a_patch_lists_what_it_made_and_changed():
    """MUTATION: drop multi_edit/notebook_edit from _FILE_EDIT_TOOLS, or
    apply_patch from _MULTI_FILE_EDIT_TOOLS -> red."""
    for name in (*EDITORS, "edit_file"):
        assert _reports_document(name) is True, name
    assert reported_document_paths("multi_edit", {"path": "a.py", "abs_path": "x"}) == ["a.py"]
    assert reported_document_paths("notebook_edit", {"path": "n.ipynb"}) == ["n.ipynb"]
    data = {
        "files": ["new.md", "a.py", "gone.txt"],
        "created": ["new.md"],
        "changed": ["a.py"],
        "deleted": ["gone.txt"],
    }
    # A deleted file is not a document (it cannot be previewed); POST
    # /chat/changes still answers for it from the journal.
    assert reported_document_paths("apply_patch", data) == ["new.md", "a.py"]
    assert reported_document_paths("read_file", {"path": "a.py"}) == []
    assert reported_document_paths("apply_patch", None) == []


class _CallingAdapter(LLMAdapter):
    """A connected real provider that calls ONE tool once, then answers."""

    def __init__(self, name: str, args: dict, provider: str = "acme", model: str = "acme-1"):
        self.provider = provider
        self.model = model
        self._name = name
        self._args = args
        self.rounds = 0

    async def complete(self, *, system, messages, tools):
        self.rounds += 1
        if self.rounds == 1:
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(id="call-1", name=self._name, arguments=dict(self._args))],
                usage={},
            )
        return LLMResponse(text="Done.", tool_calls=[], usage={})


def _wire(client: TestClient, adapter: LLMAdapter) -> None:
    platform = client.app.state.platform
    platform.config.default_provider = adapter.provider
    real_get = platform.providers.get
    platform.providers.available = lambda name: name in (adapter.provider, "mock")
    platform.providers.get = lambda p, m=None: adapter if p == adapter.provider else real_get(p, m)


def _done_frame(raw: str) -> dict:
    for block in raw.split("\n\n"):
        ev, data = None, None
        for line in block.strip().splitlines():
            if line.startswith("event: "):
                ev = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if ev == "done":
            return data or {}
    raise AssertionError(f"no done frame in stream: {raw[:600]}")


def _folder(root: Path) -> Path:
    ws = root / "project"
    ws.mkdir(parents=True)
    (ws / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    (ws / "gone.txt").write_text("bye\n", encoding="utf-8")
    nb = {
        "cells": [{"cell_type": "code", "id": "c1", "metadata": {}, "source": ["print(1)\n"],
                   "execution_count": None, "outputs": []}],
        "metadata": {}, "nbformat": 4, "nbformat_minor": 5,
    }
    (ws / "n.ipynb").write_text(json.dumps(nb, indent=1), encoding="utf-8")
    return ws


_CALLS = {
    "apply_patch": (
        {"patch": _patch(
            "*** Add File: new.md", "+# new",
            "*** Update File: a.py", "@@", "-y = 2", "+y = 3",
            "*** Delete File: gone.txt",
        )},
        ["new.md", "a.py"],
    ),
    "multi_edit": (
        {"path": "a.py", "edits": [{"old": "x = 1", "new": "x = 10"}, {"old": "y = 2", "new": "y = 20"}]},
        ["a.py"],
    ),
    "notebook_edit": (
        {"path": "n.ipynb", "cell_index": 0, "new_source": "print(2)\n"},
        ["n.ipynb"],
    ),
}


def _lane_body(ws: Path, tool: str) -> dict:
    return {
        "messages": [{"role": "user", "content": "make the change"}],
        "tools": [tool],
        "workspace_dir": str(ws),
        "approval_mode": "yolo",
    }


@pytest.mark.parametrize("tool", EDITORS)
def test_post_chat_lists_what_the_editor_changed(tmp_path, tool):
    """MUTATION: put the old `data.path`-only read back in chat_turn's tool
    loop -> the patch's changed file (and a.py) go missing -> red."""
    ws = _folder(tmp_path / "files")
    args, rels = _CALLS[tool]
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        _wire(client, _CallingAdapter(tool, args))
        r = client.post("/chat", json=_lane_body(ws, tool))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tools_used"] == [tool], body
    assert body["documents"] == [str((ws / rel).resolve()) for rel in rels], body["documents"]


@pytest.mark.parametrize("tool", EDITORS)
def test_stream_lane_lists_what_the_editor_changed(tmp_path, tool):
    """MUTATION: put the old `data.path`-only read back in routes/chat.py's
    stream loop -> red (the lanes are lock-step)."""
    ws = _folder(tmp_path / "files")
    args, rels = _CALLS[tool]
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        _wire(client, _CallingAdapter(tool, args))
        r = client.post("/chat/stream", json=_lane_body(ws, tool))
    assert r.status_code == 200, r.text
    done = _done_frame(r.text)
    assert done["tools_used"] == [tool], done
    assert done["documents"] == [str((ws / rel).resolve()) for rel in rels], done["documents"]


# --------------------------------------------------------------------------- #
# 2. the honesty note
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("tool", EDITORS)
def test_a_new_editor_is_never_accused_of_writing_nothing(tool):
    """MUTATION: drop the three from _FILE_CHANGING_TOOLS -> the note says
    "that file does not exist" about the file the patch created -> red."""
    assert tool in _FILE_CHANGING_TOOLS
    assert _claimed_write_note("I created notes.md with the summary.", [tool]) == ""
    # anti-vacuity: with nothing that writes, the same claim is noted
    assert "does not exist" in _claimed_write_note("I created notes.md with the summary.", ["read_file"])


# --------------------------------------------------------------------------- #
# 3. "Ask before file edits" cards them, in the card's words
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", EDITORS)
async def test_always_ask_cards_each_new_editor(tmp_path, tool):
    """MUTATION: drop the three from STRICT_ASK_TOOLS -> no card, the call
    runs -> red."""
    assert tool in STRICT_ASK_TOOLS
    app = create_app(str(tmp_path))
    args, _rels = _CALLS[tool]
    app.state.platform.router.stream = _calls_stream([(tool, args)])
    seen: list[str] = []

    async def deny(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
        assert status == 200

    frames = await _drive_stream(app, _mode_body("make the change", "always_ask", tools=[tool]), deny)
    assert seen == [tool], "the always_ask posture let a file editor run uncarded"
    done = next(d for ev, d in frames if ev == "done")
    assert tool not in (done.get("tools_used") or [])


def test_the_card_says_what_each_editor_does():
    """The words ride the activity log too (mission.ASK_WORDS is pinned equal
    to lib/toolWords and desktop TOOL_WORDS by the v13520/v13580 pins)."""
    assert mission.ASK_WORDS["multi_edit"] == "change a file"
    assert mission.ASK_WORDS["apply_patch"] == "change files with a patch"
    assert mission.ASK_WORDS["notebook_edit"] == "change a notebook"


# --------------------------------------------------------------------------- #
# 4. all or nothing includes the file whose write failed part way
# --------------------------------------------------------------------------- #


def _ctx(ws: Path) -> ToolContext:
    cfg = Config(home=Path(tempfile.mkdtemp()), project_root=ws)
    return ToolContext(workspace=ws, session_id="t", agent_run_id="t", config=cfg, event_bus=None, engine=None)


async def test_a_write_that_fails_part_way_is_put_back_too(tmp_path, monkeypatch):
    """A full disk: b.txt's write opens (truncating it), writes two bytes,
    then fails. MUTATION: append to `done` AFTER the write again -> b.txt is
    left as b'B1' -> red."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.txt").write_bytes(b"a1\na2\n")
    (ws / "b.txt").write_bytes(b"b1\nb2\n")
    (ws / "c.txt").write_bytes(b"c1\n")
    before = {p.name: p.read_bytes() for p in ws.iterdir()}
    real = Path.write_bytes

    def disk_full(self, data):
        if self.name == "b.txt" and data != before["b.txt"]:
            with open(self, "wb") as fh:
                fh.write(data[:2])
            raise OSError(28, "No space left on device")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", disk_full)
    res = await ApplyPatchTool().execute(
        {"patch": _patch(
            "*** Update File: a.txt", "@@", "-a1", "+A1",
            "*** Update File: b.txt", "@@", "-b1", "+B1",
            "*** Add File: new/d.txt", "+d",
            "*** Delete File: c.txt",
        )},
        _ctx(ws),
    )
    monkeypatch.setattr(Path, "write_bytes", real)
    assert res.ok is False and "put back" in (res.error or "")
    assert {p.name: p.read_bytes() for p in ws.iterdir() if p.is_file()} == before
    assert not (ws / "new" / "d.txt").exists()


# --------------------------------------------------------------------------- #
# 5. the patch undo loads every saved copy before it touches a file
# --------------------------------------------------------------------------- #


async def test_a_missing_saved_copy_undoes_nothing(tmp_path):
    """MUTATION: load the before-image inside the restore loop again -> a.txt
    is put back, then the missing blob raises -> red."""
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = _ctx(ws)
    (ws / "a.txt").write_bytes(b"a1\na2\n")
    (ws / "big.txt").write_bytes(("x" * 100 + "\n").encode() * 200)  # over the inline cap
    patch = _patch(
        "*** Update File: a.txt", "@@", "-a1", "+A1",
        "*** Update File: big.txt", "@@", "-" + "x" * 100, "+" + "y" * 100,
    )
    tool = ApplyPatchTool()
    undo = await tool.capture_undo({"patch": patch}, ctx)
    assert (await tool.execute({"patch": patch}, ctx)).ok
    after = {p.name: p.read_bytes() for p in ws.iterdir()}
    refs = [e["ref"] for e in patch_entries(json.loads(undo["pre_inline"])) if e.get("ref")]
    assert refs, "big.txt's before-image must be a blob for this test to mean anything"
    for ref in refs:
        (undo_dir(ctx.config.home) / ref).unlink()
    res = await tool.revert(undo, ctx)
    assert res.ok is False and "nothing was undone" in (res.error or "")
    assert {p.name: p.read_bytes() for p in ws.iterdir()} == after, "a file was put back half way"


# --------------------------------------------------------------------------- #
# 6. the per-session cap holds when the starts arrive together
# --------------------------------------------------------------------------- #


async def test_the_cap_holds_when_one_step_starts_programs_together(tmp_path):
    """An agent step runs its tool calls together (``asyncio.gather``). The
    check-then-spawn let seven starts in one step all see an empty table:
    seven ran under a cap of four. MUTATION: check ``TABLE.running`` instead
    of taking a slot with ``TABLE.reserve`` -> 7 run -> red."""
    import sys
    from types import SimpleNamespace

    from iron_jarvis.harness import process as P

    sleeper = f'"{sys.executable}" -u -c "import time;print(1,flush=True);time.sleep(60)"'
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s-race", agent_run_id="r",
        config=SimpleNamespace(home=tmp_path, sandbox={"modify_env": "allow"}),
        event_bus=None, engine=None,
    )
    try:
        results = await asyncio.gather(*(
            P.ProcessStartTool().execute({"command": sleeper, "wait_ms": 0}, ctx) for _ in range(7)
        ))
        assert sum(r.ok for r in results) == P.MAX_RUNNING_PER_SESSION
        refused = [r for r in results if not r.ok]
        assert refused and all("stop one with process_stop first" in r.error for r in refused)
        assert len(P.TABLE.running("s-race")) == P.MAX_RUNNING_PER_SESSION
        assert P.TABLE._starting == {}, "every slot is handed back"
    finally:
        await asyncio.to_thread(P.end_session, "s-race")
