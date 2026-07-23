"""Wave 1 (v1.89.0): local-LLM document analysis + Excel compute.

* excel_profile / excel_query — ENGINE-computed figures (never model math).
* Attachment RAG — big attachments become question-relevant excerpts with
  location refs, not a head-clip; small ones stay inline verbatim.
* Model-aware budgets — config-pinned context windows scale the inline
  budget; unknown stays at the conservative default.
* /documents/preview + /documents/file + /documents/open — the chat's
  embedded preview panel + native Word/Excel open (launcher monkeypatched).
* /chat reports `documents` (created/edited paths) for the preview panel.
* Network-path acceptance — UNC/tailnet-style paths pass fs policy.

Offline throughout.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from iron_jarvis.daemon.app import create_app
from iron_jarvis.documents.attachment_rag import chunk_text, rag_block, retrieve
from iron_jarvis.documents.tools import document_tools
from iron_jarvis.tools.base import ToolContext


def _ctx(ws: Path) -> ToolContext:
    return ToolContext(
        workspace=ws, session_id="t", agent_run_id="t",
        config=None, event_bus=None, engine=None,
    )


def _tool(name: str):
    return next(t for t in document_tools() if t.name == name)


def _ledger(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Ledger"
    ws.append(["Client", "Amount", "Month"])
    ws.append(["Acme", 1200, "Jan"])
    ws.append(["Birch", 800, "Jan"])
    ws.append(["Acme", 300, "Feb"])
    ws.append(["Cedar", "n/a", "Feb"])  # non-numeric — must be skipped, honestly
    wb.create_sheet("Notes")
    wb.save(str(path))


# --- excel_profile / excel_query ---------------------------------------------


async def test_excel_profile_maps_the_workbook(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _ledger(ws / "book.xlsx")
    res = await _tool("excel_profile").execute({"path": "book.xlsx"}, _ctx(ws))
    assert res.ok, res.error
    sheets = res.data["sheets"]
    assert [s["sheet"] for s in sheets] == ["Ledger", "Notes"]
    assert sheets[0]["headers"][:3] == ["Client", "Amount", "Month"]
    assert "Ledger" in res.output and "headers: Client, Amount, Month" in res.output


async def test_excel_query_sum_is_engine_computed(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _ledger(ws / "book.xlsx")
    res = await _tool("excel_query").execute(
        {"path": "book.xlsx", "op": "sum", "column": "Amount"}, _ctx(ws)
    )
    assert res.ok, res.error
    assert res.data["value"] == 2300.0  # 1200 + 800 + 300; "n/a" skipped
    assert "1 non-numeric skipped" in res.output
    # By Excel LETTER too.
    res = await _tool("excel_query").execute(
        {"path": "book.xlsx", "op": "max", "column": "B"}, _ctx(ws)
    )
    assert res.ok and res.data["value"] == 1200.0


async def test_excel_query_where_group_and_filter(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _ledger(ws / "book.xlsx")
    q = _tool("excel_query")
    res = await q.execute(
        {"path": "book.xlsx", "op": "sum", "column": "Amount",
         "where": [{"column": "Client", "op": "eq", "value": "Acme"}]}, _ctx(ws)
    )
    assert res.ok and res.data["value"] == 1500.0
    res = await q.execute(
        {"path": "book.xlsx", "op": "group", "group_by": "Client",
         "column": "Amount", "agg": "sum"}, _ctx(ws)
    )
    assert res.ok
    groups = {g["group"]: g["sum"] for g in res.data["groups"]}
    assert groups == {"Acme": 1500.0, "Birch": 800.0}
    res = await q.execute(
        {"path": "book.xlsx", "op": "filter",
         "where": [{"column": "Month", "op": "eq", "value": "Feb"}]}, _ctx(ws)
    )
    assert res.ok and res.data["matches"] == 2


async def test_excel_query_bad_column_names_the_headers(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _ledger(ws / "book.xlsx")
    res = await _tool("excel_query").execute(
        {"path": "book.xlsx", "op": "sum", "column": "Revenue"}, _ctx(ws)
    )
    assert not res.ok
    assert "Client, Amount, Month" in res.error  # actionable, not a bare KeyError


# --- attachment RAG -----------------------------------------------------------


def test_chunking_tracks_pdf_page_refs():
    text = "\n".join(f"[page {n}]\n" + (f"page {n} body " * 40) for n in range(1, 9))
    chunks = chunk_text(text)
    assert len(chunks) >= 3
    assert chunks[0].ref == "p.1"  # the page the chunk STARTS on
    assert chunks[-1].ref.startswith("p.")
    plain = chunk_text("word " * 3000)
    assert plain[0].ref == "part 1" and plain[1].ref == "part 2"


def test_retrieve_prefers_the_relevant_chunk():
    filler = "quarterly narrative discussion of general business matters. "
    text = (filler * 120) + " the wire transfer code is 9317 " + (filler * 120)
    chunks = chunk_text(text)
    top = retrieve(None, chunks, "what is the wire transfer code?", k=2)
    assert any("9317" in c.text for c in top)  # lexical path (no embedder)


def test_rag_block_is_honest_about_coverage():
    text = "alpha section. " * 2000 + "the vault code is 4242. " + "omega. " * 2000
    block = rag_block("big.pdf", text, "what is the vault code?", None)
    assert "big.pdf" in block and "4242" in block
    assert "NOT the whole document" in block
    assert "read_document" in block  # tells the model how to reach the rest


def test_chat_large_attachment_uses_retrieval_not_headclip(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    uploads = tmp_path / ".ironjarvis" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    filler = "routine narrative paragraph about engagements and filings. "
    (uploads / "big.txt").write_text(
        (filler * 300) + " the vault code is 4242. " + (filler * 300),
        encoding="utf-8",
    )
    captured: dict = {}
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy(p, m=None):
        a = real_get(p, m)
        rc = a.complete

        async def c(*, system, messages, tools):
            captured["system"] = system
            return await rc(system=system, messages=messages, tools=tools)

        a.complete = c
        return a

    monkeypatch.setattr(platform.providers, "get", spy)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "what is the vault code?"}],
        "attachments": ["big.txt"],
    })
    assert r.status_code == 200
    system = captured["system"]
    assert "4242" in system  # the RELEVANT middle chunk was retrieved
    assert "NOT the whole document" in system  # honest retrieval header
    # Small attachments stay verbatim inline (no retrieval header).
    (uploads / "small.txt").write_text("just a note: deadline is March 16")
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "deadline?"}],
        "attachments": ["small.txt"],
    })
    assert r.status_code == 200
    assert "deadline is March 16" in captured["system"]
    assert "NOT the whole document" not in captured["system"]


# --- model-aware budgets ------------------------------------------------------


def test_context_pin_scales_the_inline_budget(tmp_path):
    from iron_jarvis.daemon.routes.chat import _attachment_budgets

    client = TestClient(create_app(str(tmp_path)))
    d = client.app.state  # the deps object exposes platform via state
    class _D:  # minimal deps shim for the helper
        platform = client.app.state.platform
    inline, rag, k = _attachment_budgets(_D, "custom", "fleet")
    assert (inline, rag, k) == (6000, 2400, 6)  # unknown window → defaults
    _D.platform.config.model_context_windows = {"custom::fleet": 131072}
    inline2, rag2, k2 = _attachment_budgets(_D, "custom", "fleet")
    assert inline2 == 60_000 and rag2 == 20_000 and k2 == 10
    _D.platform.config.model_context_windows = {"fleet": 8192}
    inline3, rag3, k3 = _attachment_budgets(_D, "custom", "fleet")
    assert inline3 == max(6000, int(8192 * 4 * 0.30)) and k3 == 6


def test_model_context_windows_settable_via_settings(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.put("/settings", json={
        "values": {"model_context_windows": {"custom::fleet": 131072}}
    })
    assert r.status_code == 200, r.text
    got = client.get("/settings").json()["settings"]
    assert got["model_context_windows"] == {"custom::fleet": 131072}


# --- preview / file / open ----------------------------------------------------


def test_preview_sheet_and_text_and_gating(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    book = tmp_path / "book.xlsx"
    _ledger(book)
    out = client.get("/documents/preview", params={"path": str(book)}).json()
    assert out["kind"] == "sheet" and out["sheets"] == ["Ledger", "Notes"]
    assert out["rows"][0] == ["Client", "Amount", "Month"]
    out2 = client.get(
        "/documents/preview", params={"path": str(book), "sheet": "Notes"}
    ).json()
    assert out2["sheet"] == "Notes"
    note = tmp_path / "note.md"
    note.write_text("# Hello\nbody", encoding="utf-8")
    out3 = client.get("/documents/preview", params={"path": str(note)}).json()
    assert out3["kind"] == "markdown" and "# Hello" in out3["content"]
    # Gating: relative → 400, missing → 404, protected → 403.
    assert client.get("/documents/preview", params={"path": "rel.txt"}).status_code == 400
    assert client.get(
        "/documents/preview", params={"path": str(tmp_path / "ghost.txt")}
    ).status_code == 404
    key = tmp_path / ".ironjarvis" / "secrets" / ".secrets.key"
    if key.is_file():
        assert client.get(
            "/documents/preview", params={"path": str(key)}
        ).status_code == 403


def test_document_file_serves_bytes(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    f = tmp_path / "note.txt"
    f.write_text("raw bytes here", encoding="utf-8")
    r = client.get("/documents/file", params={"path": str(f)})
    assert r.status_code == 200 and b"raw bytes here" in r.content


def test_document_open_launches_native_app(tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import documents as docroutes

    launched: list[str] = []
    monkeypatch.setattr(docroutes, "_open_native", lambda p: launched.append(p))
    client = TestClient(create_app(str(tmp_path)))
    book = tmp_path / "book.xlsx"
    _ledger(book)
    out = client.post("/documents/open", json={"path": str(book)}).json()
    assert out["ok"] is True and out["app"] == "Excel"
    assert launched == [str(book)]
    doc = tmp_path / "memo.docx"
    doc.write_bytes(b"PK\x03\x04fake")
    out = client.post("/documents/open", json={"path": str(doc)}).json()
    assert out["app"] == "Word"
    assert client.post(
        "/documents/open", json={"path": str(tmp_path / "ghost.docx")}
    ).status_code == 404


# --- chat reports generated documents ----------------------------------------


def test_chat_reports_created_documents_for_preview(tmp_path, monkeypatch):
    from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    calls = {"n": 0}

    class _ToolCallingAdapter:
        provider = "anthropic"
        model = "claude-opus-4-8"

        async def complete(self, *, system, messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return LLMResponse(text="", tool_calls=[
                    ToolCall(
                        id="c1", name="write_document",
                        arguments={"path": "report.md", "content": "# Report"},
                    )
                ])
            return LLMResponse(text="Wrote the report.")

    monkeypatch.setattr(
        platform.providers, "get", lambda p, m=None: _ToolCallingAdapter()
    )
    ws = tmp_path / "proj"
    ws.mkdir()
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "write a report"}],
        "tools": ["write_document"],
        "workspace_dir": str(ws),
    })
    assert r.status_code == 200
    docs = r.json()["documents"]
    assert len(docs) == 1 and docs[0].endswith("report.md")
    assert Path(docs[0]).is_file()


# --- network/tailnet paths pass policy ----------------------------------------


def test_unc_and_network_style_paths_pass_fs_policy():
    from iron_jarvis.core.fs_policy import fs_path_allowed, is_protected_path

    for p in (
        r"\\\\tailserver\\share\\clients\\2026\\ledger.xlsx",
        r"\\tailserver\share\clients\ledger.xlsx",
        "//tailserver/share/clients/ledger.xlsx",
    ):
        assert fs_path_allowed(p), p  # default policy: allowed
        assert not is_protected_path(p), p  # and nowhere near the vault
