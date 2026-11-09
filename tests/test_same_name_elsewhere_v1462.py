"""v1.462.0: a "no such file" refusal names where the file really is.

Read off the live ledger (two weeks of real coding work by team workers):
78 ``read_file`` calls ended "no such file", and for 41 of them exactly ONE
file of that name sat elsewhere in the same folder. The model had the name
right and the folders wrong, was told only "no such file", and guessed again.

These pins drive the REAL tools against real files on disk.
"""

from __future__ import annotations

from pathlib import Path

from iron_jarvis.harness.tools import MultiEditTool
from iron_jarvis.tools import builtins as B
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import (
    EditFileTool,
    ReadFileTool,
    missing_file_reason,
    same_name_elsewhere,
    unreadable_reason,
)


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id="s", agent_run_id="r",
        config=None, event_bus=None, engine=None,
    )


def _repo(tmp_path: Path) -> Path:
    """The shape of the real miss: the file lives under packages/, not apps/."""
    for rel in (
        "packages/shared/src/tokens.ts",
        "packages/shared/src/config.ts",
        "apps/web/src/config.ts",
        "apps/jobs/src/config.ts",
        "docs/config.ts",
        "node_modules/somepkg/tokens.ts",
        ".git/tokens.ts",
    ):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("export const x = 1;\n", encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------ the read tool --


async def test_a_wrong_folder_names_the_one_place_the_file_is(tmp_path):
    ws = _repo(tmp_path)
    result = await ReadFileTool().execute({"path": "apps/shared/src/tokens.ts"}, _ctx(ws))
    assert result.ok is False
    assert result.error == (
        "no such file: apps/shared/src/tokens.ts."[:-1]
        + " A file with that name is at packages/shared/src/tokens.ts."
    )


async def test_the_hint_is_a_path_the_next_call_can_read(tmp_path):
    """The point of the hint: the NEXT call succeeds with what it was told."""
    ws = _repo(tmp_path)
    miss = await ReadFileTool().execute({"path": "src/tokens.ts"}, _ctx(ws))
    told = miss.error.split(" is at ", 1)[1].rstrip(".")
    again = await ReadFileTool().execute({"path": told}, _ctx(ws))
    assert again.ok is True and "export const x" in again.output


async def test_several_places_are_listed_best_match_first_and_counted(tmp_path):
    ws = _repo(tmp_path)
    result = await ReadFileTool().execute({"path": "apps/shared/src/config.ts"}, _ctx(ws))
    # packages/shared/src/config.ts shares the most trailing folders with what
    # was asked ("shared/src/config.ts"), so it leads; docs/ shares the least.
    assert result.error == (
        "no such file: apps/shared/src/config.ts Files with that name: "
        "packages/shared/src/config.ts, apps/jobs/src/config.ts, "
        "apps/web/src/config.ts (+1 more)."
    )


async def test_a_file_that_is_nowhere_keeps_the_old_sentence_exactly(tmp_path):
    ws = _repo(tmp_path)
    result = await ReadFileTool().execute({"path": "apps/nope.ts"}, _ctx(ws))
    assert result.error == "no such file: apps/nope.ts"


async def test_dependency_and_hidden_folders_are_never_offered(tmp_path):
    ws = tmp_path
    for rel in ("node_modules/a/only.ts", ".git/only.ts", ".venv/only.ts"):
        f = ws / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")
    result = await ReadFileTool().execute({"path": "src/only.ts"}, _ctx(ws))
    assert result.error == "no such file: src/only.ts"


async def test_a_windows_spelled_path_is_read_the_same_way(tmp_path):
    ws = _repo(tmp_path)
    result = await ReadFileTool().execute({"path": "apps\\shared\\src\\tokens.ts"}, _ctx(ws))
    assert result.error.endswith(" A file with that name is at packages/shared/src/tokens.ts.")


async def test_the_name_is_matched_whatever_its_capitals(tmp_path):
    ws = _repo(tmp_path)
    result = await ReadFileTool().execute({"path": "lib/Tokens.TS"}, _ctx(ws))
    assert "packages/shared/src/tokens.ts" in result.error


# ----------------------------------------------------------- the edit tools --


async def test_edit_file_gets_the_same_hint(tmp_path):
    ws = _repo(tmp_path)
    result = await EditFileTool().execute(
        {"path": "apps/shared/src/tokens.ts", "old": "1", "new": "2"}, _ctx(ws)
    )
    assert result.ok is False
    assert result.error.endswith(" A file with that name is at packages/shared/src/tokens.ts.")
    # ...and nothing was changed anywhere.
    assert (ws / "packages/shared/src/tokens.ts").read_text(encoding="utf-8") == "export const x = 1;\n"


async def test_multi_edit_gets_the_same_hint(tmp_path):
    ws = _repo(tmp_path)
    result = await MultiEditTool().execute(
        {"path": "apps/shared/src/tokens.ts", "edits": [{"old": "1", "new": "2"}]}, _ctx(ws)
    )
    assert result.ok is False
    assert result.error.endswith(" A file with that name is at packages/shared/src/tokens.ts.")


# ------------------------------------------------- the other answers stand ---


async def test_a_folder_still_says_it_is_a_folder_with_no_hint(tmp_path):
    ws = _repo(tmp_path)
    # A FILE called "packages" exists elsewhere: the folder's own answer must
    # still not grow a hint (it is not a missing file).
    (ws / "docs" / "packages").write_text("x", encoding="utf-8")
    result = await ReadFileTool().execute({"path": "packages"}, _ctx(ws))
    assert "DIRECTORY" in result.error
    assert "with that name" not in result.error


def test_without_a_workspace_nothing_is_searched(tmp_path, monkeypatch):
    walked = []
    monkeypatch.setattr(B, "_walk_files", lambda *a, **k: walked.append(a) or ([], ""))
    assert missing_file_reason(tmp_path / "nope.ts", "nope.ts", None) == "no such file: nope.ts"
    assert unreadable_reason(tmp_path / "nope.ts", "nope.ts") == "no such file: nope.ts"
    assert same_name_elsewhere(None, "nope.ts") == ""
    assert walked == []


# ------------------------------------------------------- bounded, harmless ---


def test_the_search_is_bounded_in_files_and_time(tmp_path, monkeypatch):
    seen = {}

    def spy(base, *, limit, deadline_s):
        seen.update(base=base, limit=limit, deadline_s=deadline_s)
        return [], ""

    monkeypatch.setattr(B, "_walk_files", spy)
    assert same_name_elsewhere(tmp_path, "a/b.ts") == ""
    assert seen["base"] == tmp_path
    assert seen["limit"] == B._ELSEWHERE_FILES <= 20_000
    assert seen["deadline_s"] == B._ELSEWHERE_DEADLINE_S <= 2.0


def test_a_walk_that_fails_costs_only_the_hint(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("disk went away")

    monkeypatch.setattr(B, "_walk_files", boom)
    assert missing_file_reason(tmp_path / "nope.ts", "nope.ts", tmp_path) == "no such file: nope.ts"


def test_only_names_inside_the_workspace_are_ever_named(tmp_path):
    """A same-named file NEXT TO the workspace is never disclosed."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    assert same_name_elsewhere(ws, "../secret.txt") == ""
    assert same_name_elsewhere(ws, "secret.txt") == ""


def test_an_empty_or_odd_name_says_nothing(tmp_path):
    _repo(tmp_path)
    for raw in ("", "/", "\\", None):
        assert same_name_elsewhere(tmp_path, raw) == ""
