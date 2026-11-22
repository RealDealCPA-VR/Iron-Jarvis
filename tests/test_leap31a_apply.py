"""Idea 31-a: a scanned pile becomes named documents — CONFIRM and UNDO.

Real files in a temp folder, the real registry, the real undo journal and
the real ``POST /undo`` path (through the card's own route):

* Confirm writes the eight documents into their folders; each file is
  re-opened and holds exactly its pages; the counts add up to the scan's;
  the scan's bytes are unchanged and it stays where it was (D-31.1); every
  path is reported absolute;
* the facts of each new file are kept (``sourcemark.kept``) with their page
  in the NEW file;
* ONE Undo removes all eight files and the folders the plan made, and leaves
  only the scan; a file edited since refuses the whole Undo;
* the user's edits on the card (a page moved, a rename, another folder) are
  what is written; an edit that drops a page, or a folder outside the chat's
  folder, writes nothing;
* the gate is the registry's: without the press (no grant) the ask refuses
  and nothing is written; a tool set to Never run stays refused even with the
  press; a plan made in this very turn is refused (the user has not seen it);
* all or nothing: a failure on the fifth document leaves no file and no
  folder behind; an existing file of the same name is never written over.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation, UndoJournal
from iron_jarvis.documents import source_store
from iron_jarvis.documents import stack as S
from iron_jarvis.tools.base import ToolContext
from tests.test_leap31a_boundaries import BLANK_PAGE, Planner, make_stack, model_plan

pytestmark = pytest.mark.timeout(300)


@pytest.fixture()
def desk(tmp_path, monkeypatch):
    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path / "home")))
    p = client.app.state.platform
    ws = tmp_path / "work"
    ws.mkdir()
    scan = make_stack(ws / "scan.pdf")
    planner = Planner(model_plan(forget=(), double=False))
    monkeypatch.setattr(p.router, "complete", planner.complete)
    yield p, client, ws, scan
    client.close()


def _ctx(p, ws: Path, turn_id: str) -> ToolContext:
    from iron_jarvis.documents.sources import SourceTurn

    return ToolContext(
        workspace=ws, session_id="chat", agent_run_id="chat", config=p.config,
        event_bus=p.event_bus, engine=p.engine, thread_id="thr_sort",
        source_turn=SourceTurn(thread_id="thr_sort", turn_id=turn_id, engine=p.engine),
    )


def _plan(p, ws: Path, scan: Path, turn_id: str = "turn-plan") -> dict:
    r = asyncio.run(p.registry.invoke(
        "stack_plan", {"path": str(scan)}, _ctx(p, ws, turn_id), p.permissions, None,
        session_allow={"stack_plan"}, allowed_names={"stack_plan"},
    ))
    assert r.ok, r.error
    return r.data["stack_plan"]


def _files(ws: Path) -> list[str]:
    return sorted(x.relative_to(ws).as_posix() for x in ws.rglob("*"))


def _pages(path: str) -> int:
    from pypdf import PdfReader

    return len(PdfReader(path).pages)


def _page_text(path: str, n: int) -> str:
    from pypdf import PdfReader

    return PdfReader(path).pages[n - 1].extract_text() or ""


def test_confirm_writes_eight_named_documents_and_one_undo_leaves_only_the_scan(desk):
    p, client, ws, scan = desk
    before = S.file_sha256(scan)
    card = _plan(p, ws, scan)
    assert len(card["documents"]) == 8 and not any(d["unsure"] for d in card["documents"])

    res = client.post(f"/documents/stack/{card['plan_id']}/apply", json={})
    assert res.status_code == 200, res.text
    out = res.json()
    files = out["files"]
    assert len(files) == 8
    assert all(Path(f["path"]).is_absolute() and Path(f["path"]).is_file() for f in files)
    assert all(f["path"] in out["output"] for f in files), "every path is reported, absolute"
    assert sum(_pages(f["path"]) for f in files) == 60
    want = {d["name"]: len(d["pages"]) for d in card["documents"]}
    assert {f["name"]: _pages(f["path"]) for f in files} == want
    first = next(f for f in files if f["name"] == "2026-03 Bank statement.pdf")
    assert Path(first["path"]).parent.name == "Bank statements"
    assert "First Harbor Bank" in _page_text(first["path"], 1)
    third = next(f for f in files if "Insurance renewal" in f["name"])
    assert _pages(third["path"]) == 9, "the blank page stayed with the document before it"
    # The scan is untouched and where it was.
    assert scan.is_file() and S.file_sha256(scan) == before
    assert out["card"]["applied"] is True

    # One ledger row, one journal row of kind files_delete over all eight.
    with session_scope(p.engine) as db:
        rows = [r for r in db.exec(select(ToolInvocation)).all() if r.tool == "stack_apply"]
        assert len(rows) == 1 and rows[0].ok
        journal = db.get(UndoJournal, rows[0].id)
        assert journal is not None and journal.kind == "files_delete"
        meta = json.loads(journal.pre_inline)
    assert len(meta["paths"]) == 8 and set(meta["sha256"]) == set(meta["paths"])
    assert out["action_id"] == rows[0].id

    # The facts of each new file are kept, with their page in the NEW file.
    with session_scope(p.engine) as db:
        kept = [source_store.as_dict(r) for r in db.exec(select(source_store.SourceMark))
                if r.kept and r.path.endswith("Electricity bill.pdf")]
    bill = {m["shown"]: m["locator"]["page"] for m in kept}
    assert bill.get("1,141.50") == 1 and bill.get("1,146.50") == 6

    # ONE Undo, through the card: only the scan is left.
    res = client.post(f"/documents/stack/{card['plan_id']}/undo")
    assert res.status_code == 200, res.text
    assert _files(ws) == ["scan.pdf"]
    assert S.file_sha256(scan) == before
    assert res.json()["card"]["applied"] is False
    with session_scope(p.engine) as db:
        assert not [r for r in db.exec(select(source_store.SourceMark)) if r.kept]
    # The plan can be written again after its Undo.
    again = client.post(f"/documents/stack/{card['plan_id']}/apply", json={})
    assert again.status_code == 200, again.text
    assert len(again.json()["files"]) == 8


def test_the_users_edits_on_the_card_are_what_is_written(desk):
    p, client, ws, scan = desk
    card = _plan(p, ws, scan)
    docs = [dict(d) for d in card["documents"]]
    docs[0]["pages"] = [n for n in docs[0]["pages"] if n != 8]
    docs[1]["pages"] = sorted(docs[1]["pages"] + [8])
    docs[1]["name"] = "Power bill March"
    docs[1]["folder"] = "Utilities/2026"
    res = client.post(f"/documents/stack/{card['plan_id']}/apply", json={"documents": docs})
    assert res.status_code == 200, res.text
    files = {f["name"]: f for f in res.json()["files"]}
    power = files["Power bill March.pdf"]
    assert Path(power["path"]).parent == (ws / "Utilities" / "2026").resolve()
    assert _pages(power["path"]) == 7 and "First Harbor Bank" in _page_text(power["path"], 1)
    assert _pages(files["2026-03 Bank statement.pdf"]["path"]) == 7


@pytest.mark.parametrize("change,words", [
    (lambda docs: docs[0].update(pages=docs[0]["pages"][:-1]), "in no document"),
    (lambda docs: docs[1].update(pages=docs[1]["pages"] + [1]), "in two documents"),
    (lambda docs: docs[0].update(folder="../../outside"), "outside this chat's folder"),
    (lambda docs: docs[0].update(folder="C:\\Windows"), "outside this chat's folder"),
])
def test_an_edit_that_would_lose_a_page_or_leave_the_folder_writes_nothing(desk, change, words):
    p, client, ws, scan = desk
    card = _plan(p, ws, scan)
    docs = [dict(d) for d in card["documents"]]
    change(docs)
    res = client.post(f"/documents/stack/{card['plan_id']}/apply", json={"documents": docs})
    assert res.status_code == 409 and words in res.json()["detail"]
    assert _files(ws) == ["scan.pdf"]


def test_without_the_press_the_registry_refuses_and_writes_nothing(desk):
    p, _client, ws, scan = desk
    card = _plan(p, ws, scan)
    r = asyncio.run(p.registry.invoke(
        "stack_apply", {"plan_id": card["plan_id"]}, _ctx(p, ws, "turn-later"), p.permissions, None,
        allowed_names={"stack_apply"},
    ))
    assert not r.ok
    assert _files(ws) == ["scan.pdf"]


def test_never_run_stays_refused_even_with_the_press(desk, monkeypatch):
    p, client, ws, scan = desk
    card = _plan(p, ws, scan)
    monkeypatch.setitem(p.permissions._base, "stack_apply", "deny")  # the user's "Never run"
    res = client.post(f"/documents/stack/{card['plan_id']}/apply", json={})
    assert res.status_code == 409
    assert _files(ws) == ["scan.pdf"]


def test_a_plan_made_in_this_turn_is_not_written_in_it(desk):
    p, _client, ws, scan = desk
    card = _plan(p, ws, scan, turn_id="turn-same")
    r = asyncio.run(p.registry.invoke(
        "stack_apply", {"plan_id": card["plan_id"]}, _ctx(p, ws, "turn-same"), p.permissions, None,
        session_allow={"stack_apply"}, allowed_names={"stack_apply"},
    ))
    assert not r.ok and "has not seen it yet" in (r.error or "")
    assert _files(ws) == ["scan.pdf"]


def test_a_plan_is_written_once(desk):
    p, client, ws, scan = desk
    card = _plan(p, ws, scan)
    assert client.post(f"/documents/stack/{card['plan_id']}/apply", json={}).status_code == 200
    twice = client.post(f"/documents/stack/{card['plan_id']}/apply", json={})
    assert twice.status_code == 409 and "already written" in twice.json()["detail"]
    assert len([x for x in ws.rglob("*.pdf")]) == 9


def test_the_cards_undo_works_after_the_plan_was_swept(desk):
    """The Undo is of the recorded call, not of the kept plan: a plan removed
    after PLAN_DAYS days (or by hand) still undoes from its card."""
    p, client, ws, scan = desk
    card = _plan(p, ws, scan)
    assert client.post(f"/documents/stack/{card['plan_id']}/apply", json={}).status_code == 200
    (S.plans_dir(p.config.home) / f"{card['plan_id']}.json").unlink()
    res = client.post(f"/documents/stack/{card['plan_id']}/undo")
    assert res.status_code == 200, res.text
    assert _files(ws) == ["scan.pdf"]
    assert res.json()["card"] is None


def test_a_file_edited_since_refuses_the_whole_undo(desk):
    p, client, ws, scan = desk
    card = _plan(p, ws, scan)
    files = client.post(f"/documents/stack/{card['plan_id']}/apply", json={}).json()["files"]
    Path(files[3]["path"]).write_bytes(b"%PDF-1.4 changed by the user")
    res = client.post(f"/documents/stack/{card['plan_id']}/undo")
    assert res.status_code == 409
    assert all(Path(f["path"]).is_file() for f in files), "nothing removed when one file changed"


def test_a_changed_scan_is_not_written_from_an_old_plan(desk):
    p, client, ws, scan = desk
    card = _plan(p, ws, scan)
    with open(scan, "ab") as fh:
        fh.write(b"\n% appended")
    res = client.post(f"/documents/stack/{card['plan_id']}/apply", json={})
    assert res.status_code == 409 and "changed since the plan" in res.json()["detail"]
    assert _files(ws) == ["scan.pdf"]


def test_all_or_nothing_and_never_over_an_existing_file(desk, monkeypatch):
    from iron_jarvis.documents import pdf_pages

    p, client, ws, scan = desk
    card = _plan(p, ws, scan)
    real = pdf_pages.arrange
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 5:
            raise OSError("disk full")
        return real(*a, **kw)

    monkeypatch.setattr(pdf_pages, "arrange", flaky)
    res = client.post(f"/documents/stack/{card['plan_id']}/apply", json={})
    assert res.status_code == 409 and "Nothing was kept" in res.json()["detail"]
    assert _files(ws) == ["scan.pdf"], "no file and no folder left behind"
    monkeypatch.setattr(pdf_pages, "arrange", real)

    # A file already holding a planned name is kept as it is.
    (ws / "Bank statements").mkdir()
    mine = ws / "Bank statements" / "2026-03 Bank statement.pdf"
    mine.write_bytes(b"my own file")
    res = client.post(f"/documents/stack/{card['plan_id']}/apply", json={})
    assert res.status_code == 200, res.text
    assert mine.read_bytes() == b"my own file"
    assert (ws / "Bank statements" / "2026-03 Bank statement (2).pdf").is_file()
    assert client.post(f"/documents/stack/{card['plan_id']}/undo").status_code == 200
    assert mine.read_bytes() == b"my own file" and (ws / "Bank statements").is_dir()
    assert sorted(x.name for x in ws.rglob("*.pdf")) == ["2026-03 Bank statement.pdf", "scan.pdf"]
    assert BLANK_PAGE == 23
