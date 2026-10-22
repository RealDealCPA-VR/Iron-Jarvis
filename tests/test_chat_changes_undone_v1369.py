"""v1.369.0 review: AN UNDO THAT WORKED READS AS UNDONE ONLY.

``POST /chat/changes`` measured "changed since" against the write the undo
had just taken back, so every successful Undo of a NEW file read
"new file RESULT.md · changed since · undone" with "The file is no longer
there." — on the Build session's changes AND under every chat reply (one
route, one ``ChangedFiles``). Now:

* a file every write of which was undone is ``undone`` with
  ``changed_since`` False and no note — the undo left it as it was before the
  turn (gone for a created file, the prior bytes for an overwritten one);
* a file changed AGAIN after the undo still says so (measured against the
  state the undo left);
* a file whose newest write was undone while an earlier one stands is not
  ``undone``, and "changed since" is measured against that earlier write.

Driven through the REAL write tool and the REAL undo (``perform_undo``, the
body of ``POST /undo/{id}``).
"""

from __future__ import annotations

import iron_jarvis.workflows.models  # noqa: F401  (register tables before init_db)

import asyncio
from types import SimpleNamespace

from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.routes.undo import perform_undo
from iron_jarvis.platform import build_platform
from tests.test_chat_changes_v1328 import _client, _ctx, _invoke, _post, _since


def _write(platform, ctx, path: str, content: str) -> str:
    """The real write tool; answers its ledger row's id (the undo's key)."""
    def ids() -> set[str]:
        with session_scope(platform.engine) as db:
            return {r.id for r in db.exec(select(ToolInvocation).where(ToolInvocation.tool == "write_file")).all()}

    before = ids()
    _invoke(platform, ctx, "write_file", {"path": path, "content": content})
    (new,) = ids() - before
    return new


def _undo(platform, action_id: str) -> None:
    asyncio.run(perform_undo(SimpleNamespace(platform=platform), action_id))


def test_an_undone_new_file_reads_undone_only(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    since = _since()
    _undo(platform, _write(platform, ctx, "RESULT.md", "hello\n"))
    assert not (ws / "RESULT.md").exists(), "the undo removed the new file"
    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["status"] == "created"
    assert c["undone"] is True
    assert c["changed_since"] is False, c
    assert c["note"] == "", c


def test_an_undone_overwrite_reads_undone_only(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    (ws / "u.txt").write_text("before\n", encoding="utf-8")
    since = _since()
    _undo(platform, _write(platform, ctx, "u.txt", "after\n"))
    assert (ws / "u.txt").read_text(encoding="utf-8") == "before\n"
    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["undone"] is True and c["changed_since"] is False and c["note"] == "", c


def test_a_file_changed_again_after_the_undo_still_says_so(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    since = _since()
    _undo(platform, _write(platform, ctx, "RESULT.md", "hello\n"))
    (ws / "RESULT.md").write_text("someone else wrote this\n", encoding="utf-8")
    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["undone"] is True and c["changed_since"] is True, c
    assert c["note"] != "The file is no longer there.", c


def test_undoing_the_newest_of_two_writes_measures_against_the_one_that_stands(tmp_path):
    platform = build_platform(str(tmp_path))
    ws = tmp_path / "work"
    ctx = _ctx(platform, ws)
    since = _since()
    _write(platform, ctx, "two.txt", "one\n")
    _undo(platform, _write(platform, ctx, "two.txt", "two\n"))
    assert (ws / "two.txt").read_text(encoding="utf-8") == "one\n"
    (c,) = _post(_client(platform), since=since)["changes"]
    assert c["undone"] is False, "an earlier write of the file still stands"
    assert c["changed_since"] is False, c
    assert c["status"] == "created" and c["added"] == 1 and c["removed"] == 0, c
