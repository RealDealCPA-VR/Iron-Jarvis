"""Idea 31-a: a scanned pile becomes named documents — the PLAN.

A built 60-page fixture of eight documents (each its own letterhead, date,
sender and "Page k of n" line, one blank separator page) is planned:

* the pages are read on this PC (text layer here; Windows' OCR is the
  seam ``local_ocr.read_pages``), and the code finds every document start;
* ONE model call over a bounded digest, the page text marked as data;
* the safety step, whatever the model says: every page in exactly one
  document, a page the model forgot lands in "Not sure (pages 41-42).pdf", a
  blank page stays with the document before it and is flagged, a page two
  documents claim goes to the first, names and folders are cleaned;
* a page this PC could not read is NEVER sent to a model by the plan
  (D-31.2): it is counted, marked NOT READ for the planner, and offered on
  the card ("Read 2 more pages with Claude?"); only the press sends it, to
  the model the card named and no other;
* the demo model never plans a real file; a model that does not answer is
  said;
* chat arms ``stack_plan`` for "sort this" with a PDF, in BOTH lanes, and the
  card rides the reply's ``config_cards``; "sort these rows by date" arms
  nothing.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.documents import local_ocr
from iron_jarvis.documents import stack as S
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.autoselect import wants_stack
from iron_jarvis.tools.base import ToolContext

pytestmark = pytest.mark.timeout(300)

#: (letterhead, title, date line, sender, pages) — 60 pages in all; the third
#: document ends with a blank separator page (page 23).
DOCS = [
    ("First Harbor Bank", "Bank statement", "March 31, 2026", "First Harbor Bank", 8),
    ("City Power and Light", "Electricity bill", "March 5, 2026", "City Power and Light", 6),
    ("Greenfield Insurance Company", "Insurance renewal", "February 12, 2026", "Greenfield Insurance", 9),
    ("Northside Medical Group", "Medical invoice", "January 20, 2026", "Northside Medical Group", 7),
    ("Coastal Water District", "Water bill", "April 2, 2026", "Coastal Water District", 5),
    ("Summit Brokerage Services", "Brokerage statement", "March 31, 2026", "Summit Brokerage", 10),
    ("Riverside Property Management", "Lease renewal letter", "May 1, 2026", "Riverside Property", 8),
    ("State Revenue Department", "Tax notice", "June 15, 2026", "State Revenue Department", 7),
]
STARTS = [1, 9, 15, 24, 31, 36, 46, 54]
BLANK_PAGE = 23


def make_stack(path: Path) -> Path:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    page = 0
    for d, (head, title, when, sender, count) in enumerate(DOCS):
        for k in range(1, count + 1):
            page += 1
            pdf.add_page()
            if d == 2 and k == count:
                continue  # the blank separator page (page 23)
            shown = count - 1 if d == 2 else count
            pdf.cell(0, 8, head, new_x="LMARGIN", new_y="NEXT")
            pdf.cell(0, 8, f"{title} dated {when}", new_x="LMARGIN", new_y="NEXT")
            pdf.cell(0, 8, f"From: {sender}", new_x="LMARGIN", new_y="NEXT")
            pdf.cell(0, 8, f"Amount due 1,{d}4{k}.50 for account 88{d}{k}", new_x="LMARGIN", new_y="NEXT")
            for j in range(4):
                pdf.cell(0, 6, f"line {j} of the {title.lower()} with its usual words", new_x="LMARGIN", new_y="NEXT")
            pdf.cell(0, 8, f"Page {k} of {shown}", new_x="LMARGIN", new_y="NEXT")
    assert page == 60
    pdf.output(str(path))
    return path


def model_plan(*, forget=(41, 42), blank_elsewhere=True, double=True, extra=None) -> dict:
    """What a model might answer: eight documents, two pages forgotten, the
    blank page put at the start of the NEXT document, and (``double``) one
    page claimed twice."""
    out = []
    starts = STARTS + [61]
    folders = ["Bank statements", "Bills", "Insurance", "Medical", "Bills", "Investments", "Housing", "Taxes"]
    for i, (_h, title, _w, sender, _c) in enumerate(DOCS):
        pages = [n for n in range(starts[i], starts[i + 1]) if n not in forget]
        if blank_elsewhere and i == 2:
            pages = [n for n in pages if n != BLANK_PAGE]
        if blank_elsewhere and i == 3:
            pages = [BLANK_PAGE] + pages
        out.append({"pages": pages, "title": title, "date": "2026-03-31" if i == 0 else "2026-03",
                    "sender": sender, "kind": title.lower(), "folder": folders[i]})
    if double:
        out[1]["pages"] = out[1]["pages"] + [9]  # page 9 is doc 2's first ...
        out[0]["pages"] = out[0]["pages"] + [9]  # ... and doc 1 claims it too, first: doc 1 keeps it
    if extra:
        out.append(extra)
    return {"documents": out}


class Planner:
    """A scripted router: answers the planning call, records every call."""

    def __init__(self, answer, provider: str = "anthropic") -> None:
        self.answer = answer
        self.provider = provider
        self.calls: list[dict] = []
        self.manager = None

    async def complete(self, *, system="", messages=None, tools=None, **kw):
        self.calls.append({"system": system, "messages": messages, "kw": kw})
        text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return RouteResult(LLMResponse(text=text), self.provider, "planner-model")


@pytest.fixture()
def scan(tmp_path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    return make_stack(work / "scan.pdf")


# --------------------------------------------------------------------------- #
# reading and the code's signals
# --------------------------------------------------------------------------- #
def test_every_page_is_read_on_this_pc_and_the_blank_one_is_known(scan):
    pages = S.read_pages(scan)
    assert len(pages) == 60
    assert [pg.number for pg in pages if pg.how == S.HOW_BLANK] == [BLANK_PAGE]
    assert all(pg.how == S.HOW_TEXT for pg in pages if pg.number != BLANK_PAGE)
    assert "First Harbor Bank" in pages[0].text


def test_the_code_finds_every_document_start(scan):
    pages = S.read_pages(scan)
    sig = S.boundary_signals(pages)
    starts = [i + 1 for i, s in enumerate(sig) if s["starts"]]
    assert starts == STARTS
    assert sig[0]["page_of"] == [1, 8]
    assert "new letterhead" in sig[8]["reasons"] and "page 1 of 6" in sig[8]["reasons"]
    assert sig[BLANK_PAGE - 1]["blank"] is True
    assert "after a blank page" in sig[BLANK_PAGE]["reasons"]
    assert sig[0]["date"] == "2026-03-31"


def test_the_digest_is_bounded_and_labels_every_page(scan):
    pages = S.read_pages(scan)
    text = S.digest(pages, S.boundary_signals(pages))
    assert len(text) <= S.DIGEST_TOTAL_CHARS + 200
    assert "[page 1] signals:" in text and f"[page {BLANK_PAGE}] BLANK" in text and "[page 60]" in text
    # A long scan is clipped per page and in total, never sent whole.
    long_pages = [S.Page(number=i, text="word " * 2000) for i in range(1, 301)]
    clipped = S.digest(long_pages, S.boundary_signals(long_pages))
    assert len(clipped) <= S.DIGEST_TOTAL_CHARS + 200


# --------------------------------------------------------------------------- #
# the safety step
# --------------------------------------------------------------------------- #
def test_a_forgotten_page_lands_in_not_sure_and_no_page_is_lost(scan):
    pages = S.read_pages(scan)
    docs = S.safe_plan(S.parse_plan(json.dumps(model_plan())), pages)
    every = sorted(n for d in docs for n in d["pages"])
    assert every == list(range(1, 61)), "every page in exactly one document"
    unsure = [d for d in docs if d["unsure"]]
    assert [d["pages"] for d in unsure] == [[41, 42]]
    assert unsure[0]["name"] == "Not sure (pages 41-42).pdf"
    assert unsure[0]["folder"] == ""
    # The blank page stays with the document BEFORE it, flagged.
    third = next(d for d in docs if 15 in d["pages"])
    assert BLANK_PAGE in third["pages"] and third["blank"] == [BLANK_PAGE]
    fourth = next(d for d in docs if 24 in d["pages"])
    assert BLANK_PAGE not in fourth["pages"]
    # A page two documents claim goes to the first that claimed it.
    assert 9 in docs[0]["pages"]
    names = [d["name"] for d in docs]
    assert names[0] == "2026-03 Bank statement.pdf"
    assert docs[0]["folder"] == "Bank statements"
    assert len(docs) == 9


def test_every_left_out_run_is_its_own_not_sure_document(scan):
    pages = S.read_pages(scan)
    raw = model_plan(forget=(5, 41, 42, 60))
    docs = S.safe_plan(S.parse_plan(json.dumps(raw)), pages)
    unsure = [(d["pages"], d["name"]) for d in docs if d["unsure"]]
    assert unsure == [([5], "Not sure (page 5).pdf"), ([41, 42], "Not sure (pages 41-42).pdf"),
                      ([60], "Not sure (page 60).pdf")]
    assert sorted(n for d in docs for n in d["pages"]) == list(range(1, 61))


def test_a_model_that_names_nothing_still_loses_no_page(scan):
    pages = S.read_pages(scan)
    docs = S.safe_plan(S.parse_plan('{"documents": []}'), pages)
    # Nothing placed: one "Not sure" covers every page, the blank one flagged.
    assert [d["pages"] for d in docs] == [list(range(1, 61))]
    assert docs[0]["unsure"] and docs[0]["blank"] == [BLANK_PAGE]
    assert docs[0]["name"] == "Not sure (pages 1-60).pdf"


@pytest.mark.parametrize("raw,clean", [
    ("../../Windows", ""), ("C:\\Temp", ""), ("\\\\server\\share", ""), ("/etc", ""),
    ("Bank statements", "Bank statements"), ("Bills/2026", "Bills/2026"),
    ("a/b/c/d/e", "a/b/c"), ("CON", ""), ("Bills:2026?", "Bills 2026"),
])
def test_a_folder_stays_inside_the_chats_folder(raw, clean):
    assert S.clean_folder(raw) == clean


def test_names_are_cleaned_and_unique(scan):
    pages = S.read_pages(scan)
    raw = model_plan(forget=())
    for i in (1, 4):  # two documents the model named alike, in the same folder
        raw["documents"][i].update(title="Electricity bill", date="2026-03", folder="Bills")
    raw["documents"][2]["title"] = 'Renewal <for> "you": NUL?'
    raw["documents"][3]["title"] = "CON"
    docs = S.safe_plan(S.parse_plan(json.dumps(raw)), pages)
    names = [(d["folder"], d["name"]) for d in docs]
    assert all(not any(ch in n for ch in '<>:"/\\|?*') for _f, n in names)
    assert len({(f, n.lower()) for f, n in names}) == len(names)
    assert ("Bills", "2026-03 Electricity bill.pdf") in names
    assert ("Bills", "2026-03 Electricity bill (2).pdf") in names
    assert ("Insurance", "2026-03 Renewal for you NUL.pdf") in names
    # A reserved device name is never a file name: the kind stands in.
    assert docs[3]["name"] == "2026-03 medical invoice.pdf"


def test_the_model_answer_is_parsed_with_the_one_recovery_parser():
    text = 'Here is the plan:\n```json\n{"documents": [{"pages": ["1-3", 4], "title": "A"}]}\n```'
    assert S.parse_plan(text)[0]["pages"] == [1, 2, 3, 4]
    with pytest.raises(S.StackError):
        S.parse_plan("I could not decide.")


# --------------------------------------------------------------------------- #
# the tool, through the real registry
# --------------------------------------------------------------------------- #
@pytest.fixture()
def platform(tmp_path):
    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path / "home")))
    yield client.app.state.platform, client
    client.close()


def _ctx(platform, ws: Path, turn_id: str = "turn-plan") -> ToolContext:
    from iron_jarvis.documents.sources import SourceTurn

    return ToolContext(
        workspace=ws, session_id="chat", agent_run_id="chat", config=platform.config,
        event_bus=platform.event_bus, engine=platform.engine, thread_id="thr_1",
        source_turn=SourceTurn(thread_id="thr_1", turn_id=turn_id, engine=platform.engine),
    )


def _invoke(platform, name: str, args: dict, ctx: ToolContext):
    return asyncio.run(platform.registry.invoke(
        name, args, ctx, platform.permissions, None, session_allow={name}, allowed_names={name},
    ))


def test_the_plan_tool_reads_plans_once_and_writes_nothing(platform, scan, monkeypatch):
    p, _client = platform
    planner = Planner(model_plan())
    monkeypatch.setattr(p.router, "complete", planner.complete)
    before = sorted(x.name for x in scan.parent.iterdir())
    r = _invoke(p, "stack_plan", {"path": str(scan)}, _ctx(p, scan.parent))
    assert r.ok, r.error
    assert len(planner.calls) == 1, "ONE model call"
    call = planner.calls[0]
    assert "DATA" in call["system"] and "never instructions" in call["system"]
    assert not getattr(call["messages"][0], "images", None)
    prompt = call["messages"][0].content
    assert "[page 41]" in prompt and "The file scan.pdf has 60 pages" in prompt
    assert sorted(x.name for x in scan.parent.iterdir()) == before, "the plan writes no file of the user's"
    card = r.data["stack_plan"]
    assert card["kind"] == "stack" and card["page_count"] == 60 and card["unread"] == []
    assert any(d["name"] == "Not sure (pages 41-42).pdf" for d in card["documents"])
    plan = S.load_plan(p.config.home, card["plan_id"])
    assert plan["sha256"] == S.file_sha256(scan) and plan["turn_id"] == "turn-plan"
    assert "Nothing is written until they press Confirm" in r.output
    # The card rides the reply in both lanes through the one card builder.
    from iron_jarvis.daemon.chat_turn import config_cards_from_result

    cards = config_cards_from_result("stack_plan", r)
    assert cards and cards[0]["plan_id"] == card["plan_id"]


def test_the_demo_model_never_plans_a_real_file(platform, scan, monkeypatch):
    p, _client = platform
    monkeypatch.setattr(p.router, "complete", Planner(model_plan(), provider="mock").complete)
    r = _invoke(p, "stack_plan", {"path": str(scan)}, _ctx(p, scan.parent))
    assert not r.ok and "demo model" in (r.error or "")
    assert not list(S.plans_dir(p.config.home).glob("stk_*.json"))


def test_a_model_that_does_not_answer_is_said(platform, scan, monkeypatch):
    p, _client = platform

    async def down(**kw):
        raise ConnectionError("anthropic: connection refused")

    monkeypatch.setattr(p.router, "complete", down)
    r = _invoke(p, "stack_plan", {"path": str(scan)}, _ctx(p, scan.parent))
    assert not r.ok and "did not answer" in (r.error or "") and "connection refused" in (r.error or "")


# --------------------------------------------------------------------------- #
# D-31.2: pages this PC could not read are offered, never sent first
# --------------------------------------------------------------------------- #
def make_scanned(path: Path, pages: int = 6) -> Path:
    """Pages that are pictures only (no text layer), each a different grey
    pattern so none of them is blank."""
    from fpdf import FPDF
    from PIL import Image, ImageDraw

    pdf = FPDF()
    for n in range(1, pages + 1):
        img = Image.new("RGB", (600, 800), "white")
        draw = ImageDraw.Draw(img)
        for y in range(60, 760, 24):
            draw.rectangle([50, y, 550, y + 10], fill="black")
        pic = path.parent / f"_p{n}.png"
        img.save(pic)
        pdf.add_page()
        pdf.image(str(pic), x=0, y=0, w=210)
        pic.unlink()
    pdf.output(str(path))
    return path


def _fake_local(read: dict[int, str]):
    def fake(blobs, numbers, config=None):
        return {n: local_ocr.OcrResult(text=read[n], confident=True, number_heavy=False, seconds=0.1)
                for n in numbers if n in read}
    return fake


def test_unread_pages_are_offered_and_never_sent_by_the_plan(platform, tmp_path, monkeypatch):
    p, client = platform
    ws = tmp_path / "work2"
    ws.mkdir()
    scan = make_scanned(ws / "pile.pdf")
    monkeypatch.setattr(local_ocr, "read_pages", _fake_local({
        1: "Acme Bank statement Page 1 of 2", 2: "Acme Bank statement Page 2 of 2",
        3: "Power bill Page 1 of 2", 4: "Power bill Page 2 of 2",
    }))
    planner = Planner({"documents": [{"pages": [1, 2], "title": "Bank statement", "folder": "Bank"},
                                     {"pages": [3, 4], "title": "Power bill", "folder": "Bills"}]})
    monkeypatch.setattr(p.router, "complete", planner.complete)
    monkeypatch.setattr(S, "vision_choice", lambda router, config: {"provider": "anthropic", "model": None,
                                                                     "name": "Claude"})
    sent: list = []

    async def transcribe(blobs, router, *, route_kwargs, label_pages, page_numbers=None):
        sent.append((list(page_numbers or []), dict(route_kwargs)))
        return [f"[page {n}]\nAcme Bank statement continued, page {n}" for n in page_numbers], ""

    from iron_jarvis.documents import ocr as _ocr

    monkeypatch.setattr(_ocr, "_transcribe", transcribe)
    r = _invoke(p, "stack_plan", {"path": str(scan)}, _ctx(p, ws))
    assert r.ok, r.error
    card = r.data["stack_plan"]
    assert card["unread"] == [5, 6] and card["read_here"] == 4
    assert card["offer"] == {"provider": "anthropic", "model": None, "name": "Claude"}
    assert sent == [], "no page went to a model before the press"
    assert len(planner.calls) == 1 and not getattr(planner.calls[0]["messages"][0], "images", None)
    assert "[page 5] NOT READ" in planner.calls[0]["messages"][0].content
    assert "nothing has been sent" in r.output
    # Unread pages the planner left out are in "Not sure", never dropped.
    assert any(d["unsure"] and d["pages"] == [5, 6] for d in card["documents"])

    # The press for ANOTHER model than the one now offered sends nothing.
    monkeypatch.setattr(S, "vision_choice", lambda router, config: {"provider": "openai", "model": None,
                                                                     "name": "ChatGPT"})
    res = client.post(f"/documents/stack/{card['plan_id']}/read-more", json={"provider": "anthropic"})
    assert res.status_code == 200 and res.json()["changed"] is True and sent == []
    assert res.json()["card"]["offer"]["name"] == "ChatGPT"

    # The press for the model the card names sends exactly the unread pages, to it.
    monkeypatch.setattr(S, "vision_choice", lambda router, config: {"provider": "anthropic", "model": None,
                                                                     "name": "Claude"})
    planner.answer = {"documents": [{"pages": [1, 2, 5, 6], "title": "Bank statement", "folder": "Bank"},
                                    {"pages": [3, 4], "title": "Power bill", "folder": "Bills"}]}
    res = client.post(f"/documents/stack/{card['plan_id']}/read-more", json={"provider": "anthropic"})
    assert res.status_code == 200, res.text
    assert sent == [([5, 6], {"provider": "anthropic", "model": None})]
    after = res.json()["card"]
    assert after["unread"] == [] and after["read_by_model"] == 2 and after["offer"] is None
    assert not any(d["unsure"] for d in after["documents"])
    assert len(planner.calls) == 2, "one more planning call with what was read"


def test_no_page_read_means_no_model_is_asked(platform, tmp_path, monkeypatch):
    p, _client = platform
    ws = tmp_path / "work3"
    ws.mkdir()
    scan = make_scanned(ws / "pile.pdf", pages=3)
    monkeypatch.setattr(local_ocr, "read_pages", _fake_local({}))
    planner = Planner({"documents": []})
    monkeypatch.setattr(p.router, "complete", planner.complete)
    monkeypatch.setattr(S, "vision_choice", lambda router, config: None)
    r = _invoke(p, "stack_plan", {"path": str(scan)}, _ctx(p, ws))
    assert r.ok, r.error
    assert planner.calls == []
    card = r.data["stack_plan"]
    assert card["unread"] == [1, 2, 3] and card["offer"] is None
    assert [d["pages"] for d in card["documents"]] == [[1, 2, 3]] and card["documents"][0]["unsure"]


# --------------------------------------------------------------------------- #
# chat: the words, and both lanes
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text,has_pdf,want", [
    ("sort this", True, True),
    ("Can you sort out these scans for me?", True, True),
    ("split this into separate documents", True, True),
    ("break it up into separate files please", True, True),
    ("separate the documents in scan.pdf", False, True),
    ("sort this", False, False),
    ("sort these rows by date", True, False),
    ("sort the invoices by amount", True, False),
    ("don't sort this, just read it", True, False),
    ("what does this say?", True, False),
])
def test_the_words_that_ask_to_sort_a_scan(text, has_pdf, want):
    assert wants_stack(text, has_pdf) is want


def test_the_sorting_patterns_are_linear():
    import time

    big = ("sort " + "x " * 3000) + "by"
    t0 = time.perf_counter()
    wants_stack(big, True)
    wants_stack("split " + "it " * 1500 + "into", True)
    assert time.perf_counter() - t0 < 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["post", "stream"])
async def test_sort_this_arms_the_plan_and_its_card_rides_the_reply_in_both_lanes(tmp_path, lane, monkeypatch):
    from tests.test_script_power_lanes_v1473 import Model, _body, _call, _post, _stream
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    scan = make_stack(ws / "scan.pdf")
    planner = Planner(model_plan())
    model = Model([_call(1, "stack_plan", path=str(scan)), "The plan is on the card below."])

    # Every model call goes through `complete` or `stream`: the planning call
    # (its own system words) is answered by the planner, the turn's by the
    # scripted model.
    async def complete(**kw):
        if kw.get("system") == S.PLAN_SYSTEM:
            return await planner.complete(**kw)
        return await model.complete(**kw)

    app.state.platform.router.complete = complete
    body = _body(ws, "sort this", attachments=[str(scan)])
    if lane == "post":
        from tests.test_chat_approvals_v1187 import _asgi_post

        status, raw = await _asgi_post(app, "/chat", body)
        assert status == 200, raw
        done = json.loads(raw)
    else:
        _frames, _cards, done = await _stream(app, body, model)
    assert "stack_plan" in model.offered[0]
    assert "stack_apply" not in model.offered[0], "the model never writes the plan; the card's Confirm does"
    cards = [c for c in done.get("config_cards") or [] if c.get("kind") == "stack"]
    assert len(cards) == 1 and cards[0]["page_count"] == 60
    assert len(planner.calls) == 1
    # Nothing was written into the chat's folder by the turn.
    assert sorted(x.name for x in ws.iterdir()) == ["scan.pdf"]


@pytest.mark.asyncio
async def test_a_scanned_attachment_in_a_sort_turn_goes_to_no_vision_model(tmp_path, monkeypatch):
    """D-31.2 at the attachment step: the scan's pages are not transcribed by
    a model before the plan's card has said how many this PC could not read."""
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.daemon.chat_turn import STACK_HELD_NOTE, _prepare_attachments
    from iron_jarvis.daemon.schemas import ChatBody
    from iron_jarvis.documents import ocr as _ocr

    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    scan = make_scanned(ws / "pile.pdf", pages=2)
    sent: list = []

    async def ocr_document(*a, **kw):
        sent.append(a)
        return "[page 1]\nread by a model", "scanned PDF — text recovered via OCR (1 of 2 page(s) transcribed)"

    monkeypatch.setattr(_ocr, "ocr_document", ocr_document)

    class D:
        platform = app.state.platform

    def body(text: str) -> ChatBody:
        return ChatBody(messages=[{"role": "user", "content": text}], auto_tools=True,
                        attachments=[str(scan)], workspace_dir=str(ws))

    _images, block = await _prepare_attachments(D, body("sort this"), inline_budget=20000, rag_budget=8000, rag_k=4)
    assert sent == [] and STACK_HELD_NOTE in block
    # Any other turn reads the scan exactly as before.
    _images, block = await _prepare_attachments(D, body("what does this say?"), inline_budget=20000,
                                                rag_budget=8000, rag_k=4)
    assert len(sent) == 1 and STACK_HELD_NOTE not in block
