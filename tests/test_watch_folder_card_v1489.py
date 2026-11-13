"""A watcher asks WHICH folder on a card (v1.489.0).

Seen live on the practice set: "keep an eye on the folder called bookings on
my desktop and tell me if anything in it changes" ended on words (a question,
or "I can't read that folder") with no call, in the released version and in
the new one. Which folder to watch is a detail only the user has, so
`sentinel_add` now names it as one (`Tool.user_details`): left out, or given
as a bare name, the user is asked for the full location on a question card
before the approval card.
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.app import create_app
from tests.test_tool_details_v1489 import _app, _finished, _run

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

ASK = "set up a folder watcher (a sentinel) for my bookings folder"


def _names(p):
    return sorted(s.name for s in p.sentinels.list())


def test_only_a_folder_that_is_not_a_full_location_is_asked_for(tmp_path):
    tool = create_app(str(tmp_path / "ij")).state.platform.registry.get("sentinel_add")
    assert list(tool.missing_details({"name": "bookings"})) == ["path"]
    assert list(tool.missing_details({"name": "bookings", "path": "  "})) == ["path"]
    assert list(tool.missing_details({"name": "bookings", "path": "bookings"})) == ["path"], "a bare name is not a place"
    assert list(tool.missing_details({"name": "bookings", "path": "Desktop/bookings"})) == ["path"]
    # A full location (a glob too) is taken as given; a network spelling is
    # left to the tool's own refusal.
    for path in (str(tmp_path), str(tmp_path / "*.pdf"), r"\\server\share\bookings", "//server/share/x"):
        assert tool.missing_details({"name": "bookings", "path": path}) == {}, path
    spec = tool.spec()
    assert spec["input_schema"]["required"] == ["name"]
    assert "WITHOUT it" in spec["description"]
    assert tool.input_schema["required"] == ["name", "path"], "the tool's own schema is untouched"


@pytest.mark.asyncio
async def test_the_folder_is_asked_on_a_card_and_the_approval_card_shows_it(tmp_path):
    folder = tmp_path / "bookings"
    folder.mkdir()
    app = _app(tmp_path, [("sentinel_add", {"name": "bookings", "task": "Tell me what changed in bookings"})])
    p = app.state.platform
    before = _names(p)
    frames, order = await _run(app, ASK, answer={"path": str(folder)})
    assert [k for k, _ in order] == ["question", "approval"], order
    q = order[0][1]
    assert q["pack"] == "Iron Jarvis" and [f["name"] for f in q["fields"]] == ["path"]
    assert "which folder to watch" in q["message"]
    card = order[1][1]
    assert card["tool"] == "sentinel_add" and card["args"]["path"] == str(folder)
    done = _finished(frames, "sentinel_add")
    assert len(done) == 1 and done[0]["ok"] is True
    assert _names(p) == sorted(before + ["bookings"])


@pytest.mark.asyncio
async def test_decline_makes_no_watcher(tmp_path):
    app = _app(tmp_path, [("sentinel_add", {"name": "bookings", "path": "bookings"})])
    p = app.state.platform
    before = _names(p)
    frames, order = await _run(app, ASK, answer=None)
    assert [k for k, _ in order] == ["question"]
    assert _names(p) == before
    assert _finished(frames, "sentinel_add")[0]["ok"] is False


@pytest.mark.asyncio
async def test_a_full_location_shows_no_question(tmp_path):
    folder = tmp_path / "bookings"
    folder.mkdir()
    app = _app(tmp_path, [("sentinel_add", {"name": "bookings", "path": str(folder)})])
    _, order = await _run(app, ASK, answer={"path": "never used"})
    assert [k for k, _ in order] == ["approval"]
