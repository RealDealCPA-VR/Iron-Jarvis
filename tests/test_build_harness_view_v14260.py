"""v1.426.0 (Wave 6, E1) — what the harness does is VISIBLE.

THE AUDIT: "The harness's own instructions are invisible; an invalid folder
setup is ignored silently (the Handbook promises a note)". Both chat lanes put
``route.harness = {family, dialect, fingerprint, layers, nudged[, note]}`` on
the route (v1.366.0), but nothing on screen read it, and a voided
``.ironjarvis/harness/`` layer's note reached only the "/" menu.

Pinned here, through the REAL app factory (the model is the only double):

* a coding session's saved reply line carries ``route.harness`` — the dialect,
  the layers and the voided layer's NOTE (``harness/sessions.LINE_KEYS``
  keeps ``route`` whole) — which the Session tab reads;
* ``GET /build/sessions/{id}/harness`` answers the instructions the turn
  gives the model, put together THE SAME WAY (its text is exactly the
  component tree the turn's own system prompt carried), with the note, the
  layers, plan mode and the folder's own files; masked before it is cut,
  never stored, 404 for an unknown session;
* the dashboard's dialect words name each dialect as its table does.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build_harness as BH
from iron_jarvis.harness import components as C
from iron_jarvis.harness import sessions as S
from iron_jarvis.harness.dialects import DIALECTS
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    Script,
    asgi,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    wire,
)

ROOT = Path(__file__).resolve().parent.parent
SECRET = "sk-ant-api03-" + "Z" * 40


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _harness_dir(folder: Path) -> Path:
    h = folder.joinpath(*C.HARNESS_PARTS)
    h.mkdir(parents=True, exist_ok=True)
    return h


async def _turn(app, sid: str, text: str, turn_id: str) -> list[tuple[str, dict]]:
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id})
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] in ("done", "error"), frames[-3:]
    return frames


def _tree_in(system: str) -> str:
    """The component tree inside a turn's system prompt: from "# IJ harness"
    to the end of the tree (the tree is followed by the turn's own extras)."""
    start = system.index("# IJ harness")
    return system[start:]


# --------------------------------------------------------------------------- #
# the reply line carries route.harness, with the voided layer's note
# --------------------------------------------------------------------------- #
async def test_a_voided_folder_layer_is_said_on_the_reply_line_and_in_the_view(app, work):
    _harness_dir(work).joinpath("notes.txt").write_text("use tabs\n", encoding="utf-8")  # not a harness file
    sid = new_session(app, work)["id"]
    script = wire(app, Script(["All good."]))
    await _turn(app, sid, "hello", "h-1")
    reply = [m for m in messages_of(app, sid) if m["role"] == "assistant"][-1]
    harness = reply["route"]["harness"]
    assert harness["dialect"] == "claude" and harness["layers"] == ["default", "family:claude"]
    assert harness["note"].startswith("This folder's coding-session instructions") and "notes.txt" in harness["note"]
    # The view says the same, and its text is the tree the turn sent.
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}/harness")
    assert status == 200, view
    assert view["note"] == harness["note"] and view["layers"] == harness["layers"]
    assert view["dialect"] == "claude" and view["dialect_label"] == "Claude Code" and view["family"] == "claude"
    assert view["fingerprint"] == harness["fingerprint"], "the view is not the tree the turn used"
    sent = script.calls[0]["system"]
    assert view["text"] and view["text"] in sent, "the view's text is not what the model was given"
    assert view["text"].startswith("# IJ harness") and "## Guardrails" in view["text"]
    assert view["mode"] == "act" and view["cut"] is False and view["chars"] == len(view["text"])


async def test_a_valid_folder_layer_is_in_the_view_masked_and_plan_mode_is_said(app, work):
    hdir = _harness_dir(work)
    hdir.joinpath("system_prompt.md").write_text(
        f"Answer in short sentences. The staging key is {SECRET}.\n", encoding="utf-8",
    )
    (work / "AGENTS.md").write_text("Run the tests before you say done.\n", encoding="utf-8")
    sid = new_session(app, work)["id"]
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}/harness")
    assert status == 200, view
    assert "project" in view["layers"] and view["note"] == ""
    assert "Answer in short sentences." in view["text"]
    assert SECRET not in view["text"], "a key in the folder's files reached the view unmasked"
    assert "AGENTS.md" in view["rule_files"] and "AGENTS.md" in view["text"]
    # Plan mode is the turn's MODE component.
    status, _ = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"mode": "plan"})
    assert status == 200
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}/harness")
    assert view["mode"] == "plan" and "## Mode\nPlan only" in view["text"]


async def test_the_view_is_the_same_tree_the_turn_sends_with_the_folders_own_layer(app, work):
    hdir = _harness_dir(work)
    hdir.joinpath("guardrails.md").write_text("Never touch the migrations folder.\n", encoding="utf-8")
    sid = new_session(app, work)["id"]
    script = wire(app, Script([[ToolCall(id="c1", name="Read", arguments={"file_path": "app.py"})], "Read it."]))
    await _turn(app, sid, "read app.py", "h-2")
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}/harness")
    assert status == 200
    sent = script.calls[0]["system"]
    assert view["text"] in sent and "Never touch the migrations folder." in view["text"]
    reply = [m for m in messages_of(app, sid) if m["role"] == "assistant"][-1]
    assert reply["route"]["harness"]["fingerprint"] == view["fingerprint"]


async def test_the_folders_kept_lessons_are_in_the_view_as_in_the_turn(app, work):
    from datetime import datetime, timezone

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.harness.library import folder_key
    from iron_jarvis.harness.library_models import ACCEPTED, KIND_LESSON, HarnessLibraryRecord

    with session_scope(app.state.platform.engine) as db:
        db.add(HarnessLibraryRecord(
            folder_key=folder_key(str(work)), folder=str(work), kind=KIND_LESSON, status=ACCEPTED,
            text="The tests live in tests/unit, not tests/.", decided_at=datetime.now(timezone.utc),
        ))
        db.commit()
    sid = new_session(app, work)["id"]
    script = wire(app, Script(["Noted."]))
    await _turn(app, sid, "hi", "h-3")
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}/harness")
    assert status == 200 and "library" in view["layers"]
    assert "The tests live in tests/unit, not tests/." in view["text"]
    assert view["text"] in script.calls[0]["system"]


async def test_the_view_is_bounded_and_an_unknown_session_is_404(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    monkeypatch.setattr(BH, "SHOW_CAP", 200)
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}/harness")
    assert status == 200 and view["cut"] is True and len(view["text"]) == 200 == view["chars"]
    status, _ = await asgi(app, "GET", "/build/sessions/nope/harness")
    assert status == 404


async def test_the_view_is_never_stored(app, work):
    sid = new_session(app, work)["id"]
    before = S.get_row(app.state.platform.engine, sid)
    status, _ = await asgi(app, "GET", f"/build/sessions/{sid}/harness")
    assert status == 200
    after = S.get_row(app.state.platform.engine, sid)
    assert (before.setup_json, before.messages_json, before.updated_at) == (
        after.setup_json, after.messages_json, after.updated_at,
    )


async def test_a_failure_is_a_sentence_never_a_500(app, work, monkeypatch):
    from iron_jarvis.harness import mode as M

    sid = new_session(app, work)["id"]

    def boom(**kw):
        raise RuntimeError("no registry")

    monkeypatch.setattr(M, "build", boom)
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}/harness")
    assert status == 200 and view["error"] == BH.SHOW_FAILED and view["text"] == ""


def test_the_dashboards_dialect_words_follow_the_dialect_table():
    src = (ROOT / "dashboard" / "lib" / "sessionRail.ts").read_text(encoding="utf-8").replace("\r\n", "\n")
    m = re.search(r"export const HARNESS_DIALECT_LABELS[^{]*\{(.*?)\n\};", src, re.S)
    assert m, "HARNESS_DIALECT_LABELS not found"
    table = dict(re.findall(r'(\w+): "([^"]+)"', m.group(1)))
    assert table == {d.id: d.label for d in DIALECTS.values()}
