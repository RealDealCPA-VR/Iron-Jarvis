"""IJ harness part 3, Phase 3 — READING SEVERAL FILES IN ONE CALL, and a
tool result a harness turn cuts only where it SAYS so.

The Phase 1 measurement (Jarvis on Claude, 126 runs, read-only from the
bench's own trajectories): 291 of 384 ``read_file`` calls were made in
batches of 2 to 16 files in ONE reply — each file one tool call. Jarvis's own
``read_file`` takes ``paths`` now. A vendor-shaped reader keeps its vendor's
one-file shape: Claude Code 2.1.310's ``Read`` (its binary's own schema),
Codex 0.157.0 (no read tool at all) and Qwen Code @13a4fea5's ``read_file``
take ONE path, so none of them has a multi-read to map; Jarvis's
``read_file`` itself is shown with ``paths`` wherever a harness turn offers
it under its own name (the generic dialect, and Codex's, which passes it
through).

Same measurement: ``frontier-multicurrency-ledger``'s 14,063-character
SPEC.md came back cut at the lanes' fixed 12,000 characters WITH NO WORD
SAID, and the model spent three calls on each of the three repeats probing
the file's tail. A harness turn's tool result is now cut at a quarter of the
model's window (never under the old 12,000, never over 50,000) and the cut is
said. Plain chat keeps its old cut byte for byte.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.harness import dialects as D
from iron_jarvis.harness import mode as M
from iron_jarvis.platform import build_platform
from iron_jarvis.tools import builtins as B
from iron_jarvis.tools.base import ToolContext
from tests._fakes.harness_lane import body, call, ledger, no_finish_check, stream, wire

#: sha256 of read_file's REGISTERED spec (``registry.specs``, canonical JSON)
#: as it was before Phase 3 — plain chat and agent runs are shown exactly this
#: (checked on the pre-Phase-3 tree: the same hash).
SPEC_BEFORE_SHA256 = "c744dc260e21627d98b53ada79db1eb7b26bbda4d8b02ff7e4a5808cf0d91ab6"


@pytest.fixture()
def plat(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.py").write_text("A = 1\nB = 2\n", encoding="utf-8")
    (ws / "b.py").write_text("def f():\n    return 3\n", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "c.md").write_text("# C\n", encoding="utf-8")
    return platform, ws


async def _invoke(platform, ws: Path, args: dict, *, session_id: str = "build:mr-test"):
    ctx = ToolContext(session_id=session_id, agent_run_id="", workspace=ws, config=platform.config,
                      event_bus=platform.event_bus, engine=platform.engine)
    return await platform.registry.invoke("read_file", args, ctx, platform.permissions)


# --------------------------------------------------------------------------- #
# the tool
# --------------------------------------------------------------------------- #


async def test_paths_reads_each_file_as_a_call_naming_it_alone_would(plat):
    platform, ws = plat
    res = await _invoke(platform, ws, {"paths": ["a.py", "sub/c.md", "b.py"]})
    assert res.ok, res.error
    singles = [(await _invoke(platform, ws, {"path": p})).output for p in ("a.py", "sub/c.md", "b.py")]
    assert res.output == "\n\n".join(
        f"==> {p} <==\n{t}" for p, t in zip(("a.py", "sub/c.md", "b.py"), singles)
    )
    assert [f["path"] for f in res.data["files"]] == ["a.py", "sub/c.md", "b.py"]
    # The window arguments apply to every file.
    win = await _invoke(platform, ws, {"paths": ["a.py", "b.py"], "numbered": True})
    one = (await _invoke(platform, ws, {"path": "b.py", "numbered": True})).output
    assert win.output.endswith(f"==> b.py <==\n{one}") and "     1\tA = 1" in win.output


async def test_a_file_that_cannot_be_read_says_why_in_its_own_place(plat):
    platform, ws = plat
    res = await _invoke(platform, ws, {"paths": ["a.py", "missing.py", "sub", "../outside.txt"]})
    assert res.ok
    out = res.output
    assert "==> missing.py <==\n[not read: no such file: missing.py]" in out
    assert "==> sub <==\n[not read: 'sub' is a DIRECTORY" in out
    assert "==> ../outside.txt <==\n[not read: path '../outside.txt' escapes the session workspace]" in out
    assert [f["ok"] for f in res.data["files"]] == [True, False, False, False]
    # Nothing readable at all: a failed call, every reason kept.
    bad = await _invoke(platform, ws, {"paths": ["nope.py", "nope2.py"]})
    assert not bad.ok and "no such file: nope.py" in bad.error and "no such file: nope2.py" in bad.error


async def test_each_file_goes_through_the_read_gate(plat, monkeypatch):
    from iron_jarvis.core import fs_policy

    platform, ws = plat
    target = str((ws / "b.py").resolve())
    real = fs_policy.fs_read_ok
    monkeypatch.setattr(fs_policy, "fs_read_ok",
                        lambda p: (False, "a protected folder") if str(p) == target else real(p))
    res = await _invoke(platform, ws, {"paths": ["a.py", "b.py"]})
    assert "==> b.py <==\n[not read: 'b.py' cannot be read here: a protected folder]" in res.output
    assert "==> a.py <==\nA = 1" in res.output


async def test_the_call_is_bounded_and_says_what_it_left_out_first(plat, monkeypatch):
    platform, ws = plat
    monkeypatch.setattr(B, "READ_MANY_MAX_CHARS", 300)
    (ws / "big1.txt").write_text("x" * 200, encoding="utf-8")
    (ws / "big2.txt").write_text("y" * 200, encoding="utf-8")
    res = await _invoke(platform, ws, {"paths": ["big1.txt", "big2.txt", "a.py"]})
    lead, _, rest = res.output.partition("\n\n")
    # big2.txt did not fit; a.py, after it, still did (each file is judged
    # on its own size) — and what was left out is named FIRST.
    assert lead == ("[read 2 of 3 files — this call's 300-character limit was reached; not read: "
                    "big2.txt — read them in another call]")
    assert rest == "==> big1.txt <==\n" + "x" * 200 + "\n\n==> a.py <==\nA = 1\nB = 2\n"
    assert res.data["not_read"] == ["big2.txt"]
    assert len(rest) <= 300
    # The FIRST file alone over the limit: its start, and where to read on.
    (ws / "huge.txt").write_text("z" * 1000, encoding="utf-8")
    one = await _invoke(platform, ws, {"paths": ["huge.txt"]})
    assert one.output.endswith("[cut at 300 characters — read the rest of this file in a call of its own, with offset and limit]")
    assert len(one.output) <= 300 + 20


async def test_the_number_of_files_is_bounded_and_a_bad_list_is_refused(plat):
    platform, ws = plat
    many = [f"f{i}.py" for i in range(B.READ_MANY_MAX_FILES + 1)]
    res = await _invoke(platform, ws, {"paths": many})
    assert not res.ok and res.error == (
        f"`paths` names {len(many)} files; one call reads at most {B.READ_MANY_MAX_FILES} — "
        "split them over several calls"
    )
    for bad in ([], [1, 2], [""], {"a": 1}):
        r = await _invoke(platform, ws, {"paths": bad})
        assert not r.ok and r.error.startswith("`paths` must be a list of file paths") or "wrong type" in r.error
    # path + paths: path first, each file once.
    both = await _invoke(platform, ws, {"path": "b.py", "paths": ["a.py", "b.py"]})
    assert [f["path"] for f in both.data["files"]] == ["b.py", "a.py"]


async def test_a_call_naming_paths_passes_the_registrys_required_argument_check(plat):
    """FAILS ON THE OLD CODE: "missing required: path"."""
    platform, ws = plat
    res = await _invoke(platform, ws, {"paths": ["a.py"]})
    assert res.ok and res.output == "==> a.py <==\nA = 1\nB = 2\n"
    # Neither: the registry's own words, exactly as before.
    none = await _invoke(platform, ws, {})
    assert not none.ok and none.error == "missing required: path — read_file needs ['path']; got []"


async def test_a_single_path_reads_byte_for_byte_as_before(plat):
    platform, ws = plat
    # (the plain read is Python's text read: universal newlines)
    raw = (ws / "a.py").read_text(encoding="utf-8")
    assert raw == "A = 1\nB = 2\n"
    assert (await _invoke(platform, ws, {"path": "a.py"})).output == raw
    assert (await _invoke(platform, ws, {"path": "a.py", "numbered": True})).output == "     1\tA = 1\n     2\tB = 2"
    gone = await _invoke(platform, ws, {"path": "missing.py"})
    assert not gone.ok and gone.error == "no such file: missing.py"


async def test_a_read_of_several_files_is_never_served_from_the_read_cache(plat):
    platform, ws = plat
    first = await _invoke(platform, ws, {"path": "a.py", "paths": ["b.py"]})
    assert "return 3" in first.output
    (ws / "b.py").write_text("def f():\n    return 4\n", encoding="utf-8")
    second = await _invoke(platform, ws, {"path": "a.py", "paths": ["b.py"]})
    assert "return 4" in second.output and "return 3" not in second.output


# --------------------------------------------------------------------------- #
# who is shown `paths`
# --------------------------------------------------------------------------- #


def _canon(spec: dict) -> str:
    return json.dumps(spec, sort_keys=True)


def test_the_registered_spec_is_unchanged_for_plain_chat_and_agents(plat):
    platform, _ws = plat
    spec = platform.registry.specs(["read_file"])[0]
    assert hashlib.sha256(_canon(spec).encode("utf-8")).hexdigest() == SPEC_BEFORE_SHA256
    assert "paths" not in spec["input_schema"]["properties"]


def _plan(platform, dialect: str):
    return M.build(registry=platform.registry, permissions=platform.permissions,
                   requested=dialect, provider="anthropic", model="claude-sonnet-4-5", stream=True,
                   approval_mode="yolo", today="")


@pytest.mark.parametrize("dialect", ["generic", "gpt"])
def test_jarvis_own_read_file_shows_paths_in_a_harness_turn(plat, dialect):
    platform, _ws = plat
    turn = _plan(platform, dialect)
    spec = next(s for s in turn.specs(platform.registry) if s["name"] == "read_file")
    schema = spec["input_schema"]
    assert schema["properties"]["paths"] == B.READ_MANY_PROPERTY
    assert schema["required"] == [] and "path" in schema["properties"]
    assert spec["description"].endswith(B.READ_MANY_WORDS)


@pytest.mark.parametrize("dialect,name", [("claude", "Read"), ("qwen", "read_file")])
def test_a_vendor_shaped_reader_keeps_its_one_file_shape(plat, dialect, name):
    platform, _ws = plat
    turn = _plan(platform, dialect)
    spec = next(s for s in turn.specs(platform.registry) if s["name"] == name)
    vendor = D.DIALECTS[dialect].lookup(name).spec()
    assert spec["input_schema"] == vendor["input_schema"]
    assert "paths" not in spec["input_schema"]["properties"]
    # and Jarvis's own read_file is not shown beside it under its own name
    assert "read_file" not in [s["name"] for s in turn.specs(platform.registry)] or dialect == "qwen"


# --------------------------------------------------------------------------- #
# through the lane: one call, and a cut a model is told about
# --------------------------------------------------------------------------- #


async def test_a_harness_turn_reads_several_files_in_one_call(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    no_finish_check(ws)
    (ws / "a.py").write_text("A = 1\n", encoding="utf-8")
    (ws / "b.py").write_text("B = 2\n", encoding="utf-8")
    model = wire(platform, monkeypatch, [[call("read_file", "r1", paths=["a.py", "b.py"])], "Done."])
    await stream(platform, body(ws))
    assert model.tool_messages(1) == {"r1": "==> a.py <==\nA = 1\n\n\n==> b.py <==\nB = 2\n"}
    assert [r.tool for r in ledger(platform)] == ["read_file"]


async def test_a_harness_turn_cuts_a_long_result_only_where_it_says_so(tmp_path, monkeypatch):
    """FAILS ON THE OLD CODE: the result was cut at 12,000 characters with no
    word said."""
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    no_finish_check(ws)
    cap = CT.harness_tool_message_chars(None)
    assert cap == 32_000  # a quarter of the planner's default 32k-token window
    (ws / "spec.md").write_text("s" * 20_000, encoding="utf-8")
    (ws / "huge.md").write_text("h" * 70_000, encoding="utf-8")
    model = wire(platform, monkeypatch, [
        [call("read_file", "r1", path="spec.md")], [call("read_file", "r2", path="huge.md")], "Done.",
    ])
    await stream(platform, body(ws))
    assert model.tool_messages(1)["r1"] == "s" * 20_000  # whole: the old lane cut it at 12,000
    cut = model.tool_messages(2)["r2"]
    assert len(cut) == cap
    note = ("\n[… 38,000+ of 70,000 characters cut here — ask for less: a range of the file "
            "(offset / limit), a narrower search, a command that prints less, or fewer files at once …]\n")
    head, _, tail = cut.partition(note)
    # Its start and its end kept (a test run says why it failed at its end).
    assert set(head) == {"h"} and set(tail) == {"h"}
    assert len(head) + len(note) + len(tail) == cap and abs(len(head) - 2 * len(tail)) <= 3


async def test_plain_chat_keeps_its_old_cut_byte_for_byte(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "spec.md").write_text("s" * 20_000, encoding="utf-8")
    model = wire(platform, monkeypatch, [[call("read_file", "r1", path="spec.md")], "Done."])
    await stream(platform, body(ws, tools=["read_file"]), harness=None, session_id="chat")
    assert model.tool_messages(1)["r1"] == "s" * 12_000


def test_the_harness_cap_follows_the_window_within_its_bounds():
    assert CT.harness_tool_message_chars(8_000) == CT.TOOL_MESSAGE_CHARS
    assert CT.harness_tool_message_chars(32_000) == 32_000
    assert CT.harness_tool_message_chars(1_000_000) == CT.HARNESS_TOOL_MESSAGE_MAX
    assert CT.tool_message_text("x" * 20, None) == "x" * 20
    assert CT.tool_message_text("x" * 13_000, None) == "x" * 12_000
