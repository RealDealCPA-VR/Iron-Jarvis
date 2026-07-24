"""v1.93.2: "create an excel" must arm the CREATOR + honest no-file notes.

Real shipped failure: "create an excel …" auto-armed only the spreadsheet
ANALYZERS (excel_edit refuses without an existing workbook) and never
write_document — the local model had no way to create the file, and nothing
in the reply admitted it. Two fixes under test:

* auto-select: creation intent arms write_document FIRST (excel/workbook/
  worksheet joined the noun group; creator outranks analyzers).
* chat honesty: a turn asked for a file that wrote none carries an explicit
  note (distinct wording for not-armed vs armed-but-unused); questions ABOUT
  creating, and turns that DID write, stay clean.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.autoselect import select_auto_tools


# --- auto-select arms the creator ---------------------------------------------


def test_create_an_excel_arms_write_document_first():
    sel = select_auto_tools("create an excel of my top clients and their fees")
    assert sel[0] == "write_document", sel
    sel2 = select_auto_tools("make me an excel workbook for Q3 billing")
    assert "write_document" in sel2 and sel2[0] == "write_document"
    sel3 = select_auto_tools("please create a word doc summarizing the call")
    assert "write_document" in sel3


def test_analysis_requests_still_prefer_the_analyzers():
    sel = select_auto_tools("what is the total of the Amount column in book.xlsx")
    assert "excel_query" in sel
    assert sel[0] != "write_document"  # no creation intent → analyzers lead


# --- the honesty note ---------------------------------------------------------


def _chat(client, content: str, **extra):
    body = {"messages": [{"role": "user", "content": content}], **extra}
    r = client.post("/chat", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_no_file_written_gets_an_honest_note(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    # Auto-tools OFF and nothing armed: the mock replies with prose only.
    out = _chat(client, "create an excel of my top clients")
    assert "no file was actually created" in out["reply"]
    assert "write_document" in out["reply"]  # the note teaches the fix
    # Armed-but-unused (the local-model failure mode): distinct wording.
    out = _chat(
        client, "create an excel of my top clients",
        tools=["write_document"],
    )
    assert "no file was actually written" in out["reply"]
    assert "without using its document tools" in out["reply"]


def test_note_stays_out_of_clean_turns(tmp_path):
    from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

    client = TestClient(create_app(str(tmp_path)))
    # A question ABOUT creating is advice, not a file request.
    out = _chat(client, "how do I create an excel formula for a running total?")
    assert "no file was actually" not in out["reply"]
    # An unrelated message never notes.
    out = _chat(client, "summarize our conversation so far")
    assert "no file was actually" not in out["reply"]
    # A turn that DID write the file stays clean.
    calls = {"n": 0}

    class _WriterAdapter:
        provider = "anthropic"
        model = "claude-opus-4-8"

        async def complete(self, *, system, messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return LLMResponse(text="", tool_calls=[
                    ToolCall(id="c1", name="write_document",
                             arguments={"path": "clients.xlsx",
                                        "content": [["Client", "Fee"],
                                                    ["Acme", 1200]]})
                ])
            return LLMResponse(text="Created clients.xlsx with your data.")

    client.app.state.platform.providers.get = lambda p, m=None: _WriterAdapter()
    ws = tmp_path / "proj"
    ws.mkdir()
    out = _chat(
        client, "create an excel of my top clients",
        tools=["write_document"], workspace_dir=str(ws),
    )
    assert "no file was actually" not in out["reply"]
    assert (ws / "clients.xlsx").is_file()
    assert out["documents"] and out["documents"][0].endswith("clients.xlsx")
