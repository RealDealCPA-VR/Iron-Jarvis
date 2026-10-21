"""v1.364.0 (IJ harness Phase 1): ``read_file`` reads a numbered WINDOW.

Claude Code's ``Read`` returns ``cat -n`` lines, 2000 by default, with
``offset``/``limit`` for the rest; Jarvis's ``read_file`` returned the whole
file. The window is opt-in: with NONE of ``offset``/``limit``/``numbered`` the
output is byte-identical to before (chat and office work read plain text),
and any one of them turns it on. A window that is not the whole file SAYS
which lines it showed. The streamed read runs off the event loop.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.tools import builtins as B
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.builtins import ReadFileTool


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id="s", agent_run_id="r",
        config=SimpleNamespace(home=ws.parent), event_bus=None, engine=None,
    )


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    return w


async def _read(ws, **args):
    return await ReadFileTool().execute(args, _ctx(ws))


# --------------------------------------------------------------------------- #
# The plain read is untouched
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    [b"alpha\nbeta\n", b"one\r\ntwo\r\n\r\nthree", b"", "café → x\n".encode("utf-8")],
)
async def test_no_window_argument_reads_exactly_as_before(ws, raw):
    """MUTATION: turn the window on by default (``_window_spec`` returning a
    spec for every call) -> the numbered/cut output differs -> red."""
    (ws / "f.txt").write_bytes(raw)
    res = await _read(ws, path="f.txt")
    want = (ws / "f.txt").read_text(encoding="utf-8")
    assert res.ok
    assert res.output == want
    assert res.data == {"bytes": len(want)}


async def test_explicit_nones_are_the_plain_read(ws):
    (ws / "f.txt").write_text("a\nb\n", encoding="utf-8")
    res = await _read(ws, path="f.txt", offset=None, limit=None, numbered=None)
    assert res.output == "a\nb\n"


# --------------------------------------------------------------------------- #
# The window
# --------------------------------------------------------------------------- #


async def test_numbered_is_cat_n_style_and_the_whole_file_has_no_note(ws):
    (ws / "f.py").write_bytes(b"def f():\r\n    return 1\r\n\r\nx = f()\r\n")
    res = await _read(ws, path="f.py", numbered=True)
    assert res.ok
    assert res.output == "     1\tdef f():\n     2\t    return 1\n     3\t\n     4\tx = f()"
    assert res.data["first_line"] == 1 and res.data["last_line"] == 4
    assert res.data["total_lines"] == 4 and res.data["truncated"] is False


async def test_offset_and_limit_report_the_cut(ws):
    """MUTATION: drop the note -> red."""
    (ws / "f.txt").write_text("\n".join(f"line {i}" for i in range(1, 6)) + "\n", encoding="utf-8")
    res = await _read(ws, path="f.txt", offset=2, limit=2)
    assert res.output == "line 2\nline 3\n\n[showing lines 2–3 of 5; use offset to read more]"
    assert res.data["truncated"] is True
    numbered = await _read(ws, path="f.txt", offset="4", limit="10", numbered="true")
    assert numbered.output.startswith("     4\tline 4\n     5\tline 5")
    assert "[showing lines 4–5 of 5; use offset to read more]" in numbered.output


async def test_the_default_window_is_2000_lines(ws):
    """MUTATION: READ_DEFAULT_LIMIT ignored (limit None -> everything) -> red."""
    (ws / "big.txt").write_text("".join(f"{i}\n" for i in range(1, 2501)), encoding="utf-8")
    res = await _read(ws, path="big.txt", numbered=False, offset=1)
    lines = res.output.split("\n")
    assert lines[0] == "1" and lines[1999] == "2000"
    assert "[showing lines 1–2,000 of 2,500; use offset to read more]" in res.output


async def test_offset_past_the_end_and_an_empty_file_say_so(ws):
    (ws / "f.txt").write_text("a\nb\n", encoding="utf-8")
    past = await _read(ws, path="f.txt", offset=9)
    assert past.ok and past.output == "[the file has 2 lines; offset 9 is past the end]"
    (ws / "e.txt").write_bytes(b"")
    empty = await _read(ws, path="e.txt", numbered=True)
    assert empty.ok and empty.output == "[the file is empty]"


async def test_a_long_line_is_cut_with_its_length(ws):
    (ws / "f.txt").write_text("x" * 5000 + "\nshort\n", encoding="utf-8")
    res = await _read(ws, path="f.txt", numbered=True)
    first = res.output.split("\n")[0]
    assert first == "     1\t" + "x" * B.READ_LINE_MAX + "… [line cut: 5,000 characters]"
    assert "     2\tshort" in res.output


async def test_a_huge_window_stops_at_the_character_cap_and_says_so(ws):
    line = "y" * 1500
    (ws / "f.txt").write_text("".join(f"{line}\n" for _ in range(200)), encoding="utf-8")
    res = await _read(ws, path="f.txt", limit=200)
    shown = [ln for ln in res.output.split("\n") if ln.startswith("y")]
    assert 0 < len(shown) < 200
    assert sum(len(s) + 1 for s in shown) <= B.READ_WINDOW_MAX_CHARS
    assert f"of 200 — cut at {B.READ_WINDOW_MAX_CHARS:,} characters; use offset to read more]" in res.output


@pytest.mark.parametrize(
    "args, words",
    [
        ({"offset": "x"}, "`offset` must be a whole number"),
        ({"limit": 0}, "`limit` must be at least 1"),
        ({"offset": -3}, "`offset` must be at least 0"),
        ({"numbered": "maybe"}, "`numbered` must be true or false"),
        ({"limit": True}, "`limit` must be a whole number, not true/false"),
    ],
)
async def test_bad_window_arguments_are_refused_in_words(ws, args, words):
    (ws / "f.txt").write_text("a\n", encoding="utf-8")
    res = await _read(ws, path="f.txt", **args)
    assert res.ok is False and words in res.error


async def test_a_huge_limit_is_clamped_and_the_note_says_what_came_back(ws):
    (ws / "f.txt").write_text("".join(f"{i}\n" for i in range(1, B.READ_MAX_LIMIT + 6)), encoding="utf-8")
    res = await _read(ws, path="f.txt", limit=10**9)
    assert f"[showing lines 1–{B.READ_MAX_LIMIT:,} of {B.READ_MAX_LIMIT + 5:,}" in res.output


async def test_a_file_that_is_not_utf8_is_extracted_then_windowed(ws):
    (ws / "latin.txt").write_bytes(b"caf\xe9\nsecond\nthird\n")
    res = await _read(ws, path="latin.txt", offset=1, limit=1, numbered=True)
    assert res.ok, res.error
    assert res.output.startswith("     1\t")
    assert "use offset to read more]" in res.output


async def test_the_window_is_read_off_the_event_loop(ws, monkeypatch):
    """THREAD IDENTITY, never timing. MUTATION: call ``_window_of_file``
    directly on the loop -> the recorded thread is the loop's -> red."""
    (ws / "f.txt").write_text("a\nb\n", encoding="utf-8")
    seen: list[int] = []
    real = B._window_of_file

    def spy(*a, **kw):
        seen.append(threading.get_ident())
        return real(*a, **kw)

    monkeypatch.setattr(B, "_window_of_file", spy)
    loop_thread = threading.get_ident()
    res = await _read(ws, path="f.txt", limit=1)
    assert res.ok and seen and seen[0] != loop_thread


async def test_a_path_outside_the_workspace_is_refused(ws):
    with pytest.raises(PermissionError):
        await _read(ws, path="../outside.txt", numbered=True)


async def test_the_registry_caches_a_window_apart_from_the_whole_file(platform, ws):
    """The read cache keys on the other arguments: a windowed read is never
    served the cached whole file (or the other way round)."""
    ctx = ToolContext(
        workspace=ws, session_id="s-cache", agent_run_id="",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    (ws / "f.txt").write_text("".join(f"{i}\n" for i in range(1, 11)), encoding="utf-8")
    whole = await platform.registry.invoke("read_file", {"path": "f.txt"}, ctx, platform.permissions)
    window = await platform.registry.invoke(
        "read_file", {"path": "f.txt", "offset": 3, "limit": 2}, ctx, platform.permissions
    )
    assert whole.ok and window.ok
    assert whole.output.startswith("1\n2\n")
    assert window.output.startswith("3\n4\n\n[showing lines 3–4 of 10")
