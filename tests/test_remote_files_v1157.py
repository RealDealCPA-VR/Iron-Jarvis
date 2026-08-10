"""Documents come back from a remote agent (v1.157.0).

Asked for directly: "how do I get back documents from remote agents? it would
be great to be able to see these." Remote agents could only ever return TEXT —
``run()`` pulled a string out of the reply and that was the contract — so a
remote that produced a real .xlsx could describe it and never hand it over.

THE TESTS THAT MATTER are in section (2). This is the first path in the app
where BYTES CHOSEN BY ANOTHER MACHINE get written to the user's disk, so the
filename is treated as hostile input, a URL may only point at the agent's own
host, and sizes and counts are capped before anything is decoded. Every one of
those is a real attack, not a hypothetical: ``../../.ssh/authorized_keys`` is a
path escape, ``http://192.168.1.1/admin`` turns the daemon into an SSRF probe
pointed at the user's LAN, and one 4GB base64 string is a denial of disk.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from iron_jarvis.agents import remote_files as rf


# --------------------------------------------------------------------------- #
# (1) THE SHAPE.
# --------------------------------------------------------------------------- #
def test_files_are_read_off_the_reply():
    entries = rf.parse_files(
        {"result": "done", "files": [{"name": "a.txt", "content_b64": "aGk="}]}
    )
    assert len(entries) == 1


def test_a_reply_without_files_is_not_an_error():
    assert rf.parse_files({"result": "just text"}) == []
    assert rf.parse_files("not a dict") == []
    assert rf.parse_files({"files": "not a list"}) == []


def test_entries_without_content_are_ignored():
    assert rf.parse_files({"files": [{"name": "a.txt"}]}) == []


def test_the_file_count_is_capped():
    many = {"files": [{"name": f"{i}.txt", "content_b64": "aGk="} for i in range(200)]}
    assert len(rf.parse_files(many)) == rf.MAX_FILES


# --------------------------------------------------------------------------- #
# (2) THE TRUST BOUNDARY.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "hostile",
    [
        "../../.ssh/authorized_keys",
        "..\\..\\Windows\\System32\\evil.dll",
        "/etc/passwd",
        "C:\\Windows\\system.ini",
        "....//....//escape.txt",
        "con.txt",
        ".hidden",
        "",
        "   ",
        "///",
    ],
)
def test_a_hostile_filename_becomes_a_plain_basename(hostile):
    safe = rf.safe_filename(hostile)
    assert "/" not in safe and "\\" not in safe
    assert not safe.startswith(".")
    assert ".." not in safe
    assert safe.strip() == safe and safe


def test_a_very_long_name_is_truncated_but_keeps_its_extension():
    safe = rf.safe_filename("a" * 5000 + ".xlsx")
    assert len(safe) <= 120
    assert safe.endswith(".xlsx")


def test_a_url_on_another_host_is_refused():
    """Otherwise a reply turns the daemon into a fetcher for any address it
    names — an SSRF primitive aimed at the user's own network."""
    base = "https://agent.example.com/run"
    assert rf.same_host("https://agent.example.com/files/a.pdf", base) is True
    assert rf.same_host("http://192.168.1.1/admin", base) is False
    assert rf.same_host("https://evil.example.net/a.pdf", base) is False
    assert rf.same_host("file:///etc/passwd", base) is False
    assert rf.same_host("https://agent.example.com:9999/a.pdf", base) is False


def test_an_oversized_file_is_refused_before_it_is_decoded():
    """Decoding a gigabyte to discover it is too big IS the denial of service."""
    huge = "A" * (rf.MAX_FILE_BYTES * 2)
    files, notes = asyncio.run(
        rf.collect([{"name": "big.bin", "content_b64": huge}], base_url="https://x/")
    )
    assert files == []
    assert notes and "larger than" in notes[0]


def test_the_total_size_is_capped():
    one = base64.b64encode(b"x" * (rf.MAX_FILE_BYTES - 1024)).decode()
    entries = [{"name": f"{i}.bin", "content_b64": one} for i in range(10)]
    files, notes = asyncio.run(rf.collect(entries, base_url="https://x/"))
    assert sum(len(b) for _n, b in files) <= rf.MAX_TOTAL_BYTES
    assert any("total size cap" in n for n in notes)


def test_bad_base64_is_reported_not_swallowed():
    files, notes = asyncio.run(
        rf.collect([{"name": "a.txt", "content_b64": "!!!not base64!!!"}], base_url="https://x/")
    )
    assert files == []
    assert notes and "base64" in notes[0]


def test_refusals_are_reported_so_a_missing_file_is_never_silent():
    """"The remote sent 3 and you got 2" is exactly what a user must not have
    to discover later."""
    entries = [
        {"name": "good.txt", "content_b64": base64.b64encode(b"ok").decode()},
        {"name": "bad.txt", "content_b64": "@@@"},
        {"name": "far.txt", "url": "https://elsewhere.example/x"},
    ]
    files, notes = asyncio.run(rf.collect(entries, base_url="https://agent.example/"))
    assert len(files) == 1
    assert len(notes) == 2


def test_a_good_file_survives_all_of_it():
    payload = b"real bytes"
    files, notes = asyncio.run(
        rf.collect(
            [{"name": "report.pdf", "content_b64": base64.b64encode(payload).decode()}],
            base_url="https://agent.example/",
        )
    )
    assert notes == []
    assert files == [("report.pdf", payload)]


def test_a_name_collision_never_overwrites(tmp_path):
    (tmp_path / "a.txt").write_text("original", encoding="utf-8")
    target = rf.unique_path(tmp_path, "a.txt")
    assert target.name != "a.txt"
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "original"


# --------------------------------------------------------------------------- #
# (3) END TO END: a fake remote hands over a file, and it is reachable.
# --------------------------------------------------------------------------- #
def test_a_remote_file_lands_in_the_workspace_and_is_journaled(tmp_path, monkeypatch):
    """The whole point: the file exists, it is reported with an ABSOLUTE path,
    and it reaches the run's result (which is what drives the preview rail)."""
    from iron_jarvis.agents.remote import DelegateRemoteTool, RemoteAgentRegistry
    from iron_jarvis.platform import build_platform
    from iron_jarvis.tools.base import ToolContext

    p = build_platform(str(tmp_path))
    registry = RemoteAgentRegistry(p.engine)
    registry.upsert(name="acme", base_url="https://acme.example/run", kind="http-task")

    payload = b"%PDF-1.4 fake"

    async def fake_run(_self, record, task, resolver, **kw):
        return {
            "ok": True,
            "result": "Here is the report.",
            "detail": "ok",
            "files": [
                {"name": "../../escape.pdf", "content_b64": base64.b64encode(payload).decode()}
            ],
        }

    monkeypatch.setattr(RemoteAgentRegistry, "run", fake_run)

    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s1", agent_run_id="r1",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    result = asyncio.run(
        DelegateRemoteTool(p).execute({"agent": "acme", "task": "make it"}, ctx)
    )

    assert result.ok, result.error
    docs = (result.data or {}).get("documents") or []
    assert len(docs) == 1, f"expected one saved file, got {docs}"

    landed = Path(docs[0])
    assert landed.is_file()
    assert landed.read_bytes() == payload
    # The hostile name was defused AND the file stayed inside the workspace.
    assert landed.name == "escape.pdf"
    assert ws.resolve() in landed.resolve().parents
    # The absolute path is in the text the model relays, not just in `data`.
    assert str(landed) in result.output
    assert result.created_paths == docs


def test_a_remote_file_is_journaled_so_the_run_can_report_it(tmp_path, monkeypatch):
    """Journaling is what makes the file REACHABLE. agents/outcome derives a
    run's created files from the undo journal, and everything downstream — the
    result card, the right-rail preview shipped in v1.155.0 — reads that. A
    file on disk that nothing journaled is a file the user is told about in
    prose and cannot click, which is the bug this release exists to close.
    """
    from sqlmodel import Session, select

    from iron_jarvis.agents.remote import RemoteAgentRegistry
    from iron_jarvis.core.models import UndoJournal
    from iron_jarvis.platform import build_platform
    from iron_jarvis.tools.base import ToolContext

    p = build_platform(str(tmp_path))
    RemoteAgentRegistry(p.engine).upsert(
        name="acme", base_url="https://acme.example/run", kind="http-task"
    )

    async def fake_run(_self, record, task, resolver, **kw):
        return {
            "ok": True,
            "result": "done",
            "detail": "ok",
            "files": [
                {"name": "q3.xlsx", "content_b64": base64.b64encode(b"xlsx").decode()}
            ],
        }

    monkeypatch.setattr(RemoteAgentRegistry, "run", fake_run)
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s1", agent_run_id="r1",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    # Through registry.invoke, NOT the tool's execute(): journaling lives in
    # the registry, so calling execute() directly would test a path the app
    # never takes — which is exactly what the first version of this test did.
    from iron_jarvis.agents.remote import register_remote_agent_tool

    register_remote_agent_tool(p)
    asyncio.run(
        p.registry.invoke(
            "delegate_remote",
            {"agent": "acme", "task": "x"},
            ctx,
            p.permissions,
            session_allow=["delegate_remote"],
        )
    )

    with Session(p.engine) as db:
        rows = db.exec(select(UndoJournal).where(UndoJournal.session_id == "s1")).all()
    assert rows, "the created file was never journaled"
    assert any(r.kind == "file_delete" for r in rows), (
        "a CREATED file journals as file_delete (undo removes it)"
    )
    assert any("q3.xlsx" in (r.pre_inline or "") for r in rows)


def test_the_reply_still_works_when_it_carries_no_files(tmp_path, monkeypatch):
    """An endpoint that never adds `files` must behave exactly as before."""
    from iron_jarvis.agents.remote import DelegateRemoteTool, RemoteAgentRegistry
    from iron_jarvis.platform import build_platform
    from iron_jarvis.tools.base import ToolContext

    p = build_platform(str(tmp_path))
    RemoteAgentRegistry(p.engine).upsert(
        name="acme", base_url="https://acme.example/run", kind="http-task"
    )

    async def fake_run(_self, record, task, resolver, **kw):
        return {"ok": True, "result": "just text", "detail": "ok"}

    monkeypatch.setattr(RemoteAgentRegistry, "run", fake_run)
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s1", agent_run_id="r1",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    result = asyncio.run(
        DelegateRemoteTool(p).execute({"agent": "acme", "task": "hi"}, ctx)
    )
    assert result.ok
    assert result.output == "just text"
    assert not (result.data or {}).get("documents")


# --------------------------------------------------------------------------- #
# (4) The registry seam these files ride on.
# --------------------------------------------------------------------------- #
def test_created_paths_outside_the_workspace_are_not_journaled(tmp_path):
    """The journal describes what happened INSIDE the workspace; a path that
    resolves elsewhere is skipped rather than recorded as undoable, because an
    undo would then delete a file the run does not own."""
    from sqlmodel import Session, select

    from iron_jarvis.core.models import UndoJournal
    from iron_jarvis.platform import build_platform
    from iron_jarvis.tools.base import Tool, ToolContext, ToolResult

    p = build_platform(str(tmp_path))
    stray = tmp_path / "outside.txt"
    stray.write_text("not ours", encoding="utf-8")

    class Stray(Tool):
        name = "stray_writer"
        description = "test"
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, args, ctx):
            return ToolResult(ok=True, output="done", created_paths=[str(stray)])

    p.registry.register(Stray())
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s2", agent_run_id="r2",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    asyncio.run(
        p.registry.invoke("stray_writer", {}, ctx, p.permissions,
                          session_allow=["stray_writer"])
    )
    with Session(p.engine) as db:
        rows = db.exec(select(UndoJournal).where(UndoJournal.session_id == "s2")).all()
    assert rows == [], "a path outside the workspace must not be journaled"
    assert stray.is_file(), "and it certainly must not be touched"


def test_a_denial_decided_upstream_is_still_recorded(tmp_path):
    """`deny_reason` lets a caller that already asked a human route the refusal
    through the SAME record-and-publish path as any other denial. Without it a
    caller has to short-circuit, and the execution ledger — which
    agents/outcome derives a run's account of itself from — silently loses the
    refusal."""
    from sqlmodel import Session, select

    from iron_jarvis.core.models import ToolInvocation
    from iron_jarvis.platform import build_platform
    from iron_jarvis.tools.base import Tool, ToolContext, ToolResult

    p = build_platform(str(tmp_path))
    ran = {"yes": False}

    class Marker(Tool):
        name = "marker"
        description = "test"
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, args, ctx):
            ran["yes"] = True
            return ToolResult(ok=True, output="should not happen")

    p.registry.register(Marker())
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="s3", agent_run_id="r3",
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )
    result = asyncio.run(
        p.registry.invoke("marker", {}, ctx, p.permissions, deny_reason="you said no")
    )
    assert result.ok is False
    assert "you said no" in (result.error or "")
    assert ran["yes"] is False, "a denied tool must not execute"

    with Session(p.engine) as db:
        rows = db.exec(
            select(ToolInvocation).where(ToolInvocation.session_id == "s3")
        ).all()
    assert rows and rows[0].ok is False
    assert "you said no" in (rows[0].output or ""), "the refusal left no ledger trace"
