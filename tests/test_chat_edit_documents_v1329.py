"""A chat turn that only EDITED files still says what it changed (v1.329.0,
calm chat wave 4, F4).

The reply's "N files changed" line (v1.328.0, W3-2) is drawn only when the
turn's ``documents`` lists a path (``dashboard/lib/turnChanges.turnWindow``
returns null on an empty list, so nothing is even asked). ``edit_file``
returned only an output line, no ``data.path``, and was in neither
``_DOC_WRITING_TOOLS`` nor ``created_paths``, so a turn that edited existing
files and wrote nothing new listed no documents and showed no line, although
``POST /chat/changes`` had the journal rows to answer it.

Now ``edit_file`` names its file in ``data`` (``path`` as asked, ``abs_path``
resolved; the model's output text is unchanged) and BOTH chat lanes list it
through ``chat_turn._reports_document`` (lock-step). It stays out of
``_DOC_WRITING_TOOLS`` (the office round budget) and out of ``created_paths``
(which means CREATED to the registry and the workflow engine).

Every lane test drives the REAL app factory with a scripted adapter that
calls the REAL ``edit_file`` through the real registry.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    _DOC_WRITING_TOOLS,
    _FILE_EDIT_TOOLS,
    _is_office_turn,
    _reports_document,
)
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import EditFileTool

ORIGINAL = "alpha\nfee: 100\nomega\n"
EDITED = "alpha\nfee: 250\nomega\n"


class _EditingAdapter(LLMAdapter):
    """A connected real provider that calls ``edit_file`` once, then answers."""

    def __init__(self, args: dict, provider: str = "acme", model: str = "acme-1"):
        self.provider = provider
        self.model = model
        self._args = args
        self.rounds = 0

    async def complete(self, *, system, messages, tools):
        self.rounds += 1
        if self.rounds == 1:
            return LLMResponse(
                text="",
                tool_calls=[ToolCall(id="call-edit", name="edit_file", arguments=dict(self._args))],
                usage={},
            )
        return LLMResponse(text="Changed the fee.", tool_calls=[], usage={})


def _wire(client: TestClient, adapter: LLMAdapter):
    platform = client.app.state.platform
    platform.config.default_provider = adapter.provider
    real_get = platform.providers.get
    platform.providers.available = lambda name: name in (adapter.provider, "mock")
    platform.providers.get = lambda p, m=None: adapter if p == adapter.provider else real_get(p, m)
    return platform


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


def _folder(tmp_path: Path) -> tuple[Path, Path]:
    ws = tmp_path / "project"
    ws.mkdir(parents=True)
    target = ws / "letter.txt"
    target.write_text(ORIGINAL, encoding="utf-8")
    return ws, target


def _body(ws: Path) -> dict:
    return {
        "messages": [{"role": "user", "content": "change the fee to 250"}],
        "tools": ["edit_file"],
        "workspace_dir": str(ws),
        # The stream lane cards an ask-tier tool; this test is about what the
        # turn REPORTS, so the posture that runs it without a card is used.
        "approval_mode": "yolo",
    }


_EDIT = {"path": "letter.txt", "old": "fee: 100", "new": "fee: 250"}


# --------------------------------------------------------------------------- #
# The tool itself.
# --------------------------------------------------------------------------- #
async def test_edit_file_names_its_file_and_keeps_its_output(tmp_path):
    ws, target = _folder(tmp_path)
    ctx = ToolContext(
        workspace=ws, session_id="t", agent_run_id="t", config=None, event_bus=None, engine=None
    )
    res = await EditFileTool().execute(dict(_EDIT), ctx)
    assert res.ok is True, res.error
    assert res.output == "edited letter.txt"  # what the model sees is unchanged
    assert res.data == {"path": "letter.txt", "abs_path": str(target.resolve())}
    assert Path(res.data["abs_path"]).is_absolute()
    # CREATED means CREATED: an edit of an existing file is not a creation.
    assert res.created_paths is None
    assert target.read_text(encoding="utf-8") == EDITED


async def test_a_failed_edit_names_nothing(tmp_path):
    ws, _target = _folder(tmp_path)
    ctx = ToolContext(
        workspace=ws, session_id="t", agent_run_id="t", config=None, event_bus=None, engine=None
    )
    res = await EditFileTool().execute({"path": "letter.txt", "old": "nope", "new": "x"}, ctx)
    assert res.ok is False
    assert res.data is None


def test_edit_file_is_reported_but_is_not_an_office_turn():
    assert "edit_file" in _FILE_EDIT_TOOLS
    assert _reports_document("edit_file") is True
    for name in _DOC_WRITING_TOOLS:
        assert _reports_document(name) is True  # the document tools still count
    assert _reports_document("read_file") is False
    assert _reports_document("rename_file") is False
    # The office round budget is unchanged: a code edit is not office work.
    assert "edit_file" not in _DOC_WRITING_TOOLS
    assert _is_office_turn(["edit_file"]) is False


# --------------------------------------------------------------------------- #
# Both lanes list the edited file in `documents`.
# --------------------------------------------------------------------------- #
def test_post_chat_lists_the_edited_file(tmp_path):
    ws, target = _folder(tmp_path / "files")
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        _wire(client, _EditingAdapter(_EDIT))
        r = client.post("/chat", json=_body(ws))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tools_used"] == ["edit_file"], body
    assert body["documents"] == [str(target.resolve())], body["documents"]
    assert target.read_text(encoding="utf-8") == EDITED


def test_stream_lane_lists_the_edited_file(tmp_path):
    ws, target = _folder(tmp_path / "files")
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        _wire(client, _EditingAdapter(_EDIT))
        r = client.post("/chat/stream", json=_body(ws))
    assert r.status_code == 200, r.text
    done = _done_frame(r.text)
    assert done["tools_used"] == ["edit_file"], done
    assert done["documents"] == [str(target.resolve())], done["documents"]
    assert target.read_text(encoding="utf-8") == EDITED


def test_a_failed_edit_lists_nothing_in_either_lane(tmp_path):
    bad = {"path": "letter.txt", "old": "not in the file", "new": "x"}
    ws, target = _folder(tmp_path / "files")
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        _wire(client, _EditingAdapter(bad))
        flat = client.post("/chat", json=_body(ws))
    assert flat.status_code == 200, flat.text
    assert flat.json()["documents"] == [], flat.json()["documents"]
    with TestClient(create_app(str(tmp_path / "home2"))) as client:
        _wire(client, _EditingAdapter(bad))
        streamed = client.post("/chat/stream", json=_body(ws))
    assert _done_frame(streamed.text)["documents"] == []
    assert target.read_text(encoding="utf-8") == ORIGINAL


# --------------------------------------------------------------------------- #
# End to end: the listed path is the one POST /chat/changes answers about, so
# the reply's line reads "1 file changed +1 -1".
# --------------------------------------------------------------------------- #
def test_the_listed_path_gets_the_changed_files_answer(tmp_path):
    ws, target = _folder(tmp_path / "files")
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        _wire(client, _EditingAdapter(_EDIT))
        since = datetime.now(timezone.utc) - timedelta(seconds=1)
        done = _done_frame(client.post("/chat/stream", json=_body(ws)).text)
        until = datetime.now(timezone.utc) + timedelta(seconds=1)
        paths = done["documents"]
        assert paths, "an edit-only turn listed no documents, so the line never asks"
        r = client.post(
            "/chat/changes",
            json={"since": since.isoformat(), "until": until.isoformat(), "paths": paths},
        )
    assert r.status_code == 200, r.text
    answer = r.json()
    assert answer["files"] == 1, answer
    change = answer["changes"][0]
    assert change["status"] == "modified"
    assert Path(change["path"]) == target.resolve()
    assert (change["added"], change["removed"]) == (1, 1)
    assert "+fee: 250" in change["diff"] and "-fee: 100" in change["diff"]
