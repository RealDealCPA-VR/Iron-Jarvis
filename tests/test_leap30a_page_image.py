"""Idea 30-a: the page a figure was read from, drawn with its line boxed.

``GET /documents/page-image`` renders the page (pypdfium2), finds the
recorded line among pdfplumber's word boxes and boxes it; a line it cannot
find is never guessed: the page comes back with the line printed above it
(``X-Line-Found: 0``). The read gate comes first.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from iron_jarvis.daemon.app import create_app
from iron_jarvis.documents.page_image import MAX_WIDTH_PX, NoSuchPage, find_line, render_page
from tests.test_leap30a_marks import make_pdf

LINE = "Total wages 48,210.00 for the year"


@pytest.fixture()
def client(tmp_path):
    c = TestClient(create_app(str(tmp_path / "home")))
    yield c
    c.close()


def _orange(img: Image.Image) -> int:
    """How many pixels are the box's edge colour."""
    rgb = img.convert("RGB")
    small = rgb.resize((rgb.width // 2, rgb.height // 2))
    return sum(1 for r, g, b in small.getdata() if r > 200 and 60 < g < 120 and b < 40)


def test_the_recorded_line_is_found_on_its_page(tmp_path):
    pdf = make_pdf(tmp_path / "s.pdf")
    hit = find_line(pdf, 7, LINE, "48,210.00")
    assert hit is not None
    line_box, fig_box = hit
    assert fig_box is not None and line_box[0] <= fig_box[0] and fig_box[2] <= line_box[2]
    # The same line is not on page 6, and a figure the page lacks is not found.
    assert find_line(pdf, 6, LINE, "48,210.00") is None
    assert find_line(pdf, 7, "Total wages 99,999.00", "99,999.00") is None
    # The figure is on the page but the recorded line reads otherwise: not
    # boxed by guess.
    assert find_line(pdf, 7, "Bonus line 48,210.00 paid elsewhere later", "48,210.00") is None
    with pytest.raises(NoSuchPage):
        find_line(pdf, 12, LINE, "48,210.00")


def test_the_page_is_drawn_with_the_line_boxed(tmp_path, client):
    pdf = make_pdf(tmp_path / "s.pdf")
    r = client.get("/documents/page-image", params={"path": str(pdf), "page": 7, "find": LINE,
                                                    "fig": "48,210.00"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.headers["x-line-found"] == "1"
    img = Image.open(io.BytesIO(r.content))
    assert img.width <= MAX_WIDTH_PX
    plain, _ = render_page(pdf, 7)
    clean = Image.open(io.BytesIO(plain))
    assert img.size == clean.size  # nothing above the page
    assert _orange(img) > 50 and _orange(clean) == 0


def test_a_line_not_on_the_page_is_printed_above_it_never_guessed(tmp_path, client):
    pdf = make_pdf(tmp_path / "s.pdf")
    r = client.get("/documents/page-image", params={"path": str(pdf), "page": 6, "find": LINE,
                                                    "fig": "48,210.00"})
    assert r.status_code == 200, r.text
    assert r.headers["x-line-found"] == "0"
    img = Image.open(io.BytesIO(r.content))
    clean = Image.open(io.BytesIO(render_page(pdf, 6)[0]))
    assert img.width == clean.width and img.height > clean.height
    assert _orange(img) == 0  # no box anywhere


def test_the_page_is_asked_for_by_its_record_so_no_line_rides_the_address(tmp_path, client):
    from tests.test_leap30a_marks import _ctx, _invoke
    from iron_jarvis.documents.sources import SourceTurn

    platform = client.app.state.platform
    pdf = make_pdf(tmp_path / "s.pdf")
    turn = SourceTurn(engine=platform.engine)
    assert _invoke(platform, "read_document", {"path": str(pdf)}, _ctx(tmp_path, platform, turn)).ok
    mid = [m for m in turn.snapshot()[0] if m["key"] == "48210"][0]["id"]
    r = client.get("/documents/page-image", params={"mark": mid})
    assert r.status_code == 200, r.text
    assert r.headers["x-line-found"] == "1"
    assert client.get("/documents/page-image", params={"mark": "src_0000000000000000"}).status_code == 404
    assert client.get("/documents/page-image", params={"mark": "../x"}).status_code == 400
    # A sheet cell's record is no PDF page.
    book = tmp_path / "b.csv"
    book.write_text("a,b\nx,1500.25\n", encoding="utf-8")
    t2 = SourceTurn(engine=platform.engine)
    _invoke(platform, "read_document", {"path": str(book)}, _ctx(tmp_path, platform, t2))
    cell = t2.snapshot()[0][0]["id"]
    assert client.get("/documents/page-image", params={"mark": cell}).status_code == 422


def test_the_read_gate_and_the_wrong_file(tmp_path, client):
    pdf = make_pdf(tmp_path / "s.pdf")
    assert client.get("/documents/page-image", params={"path": str(pdf), "page": 12}).status_code == 404
    note = tmp_path / "n.txt"
    note.write_text("hello", encoding="utf-8")
    assert client.get("/documents/page-image", params={"path": str(note), "page": 1}).status_code == 415
    assert client.get("/documents/page-image", params={"path": "s.pdf", "page": 1}).status_code == 400
    home = Path(client.app.state.platform.config.home)
    secret = home / "secrets" / "x.pdf"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(pdf.read_bytes())
    assert client.get("/documents/page-image", params={"path": str(secret), "page": 1}).status_code == 403


def test_a_sheet_preview_can_start_at_the_row_of_a_cell(tmp_path, client):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for r in range(1, 301):
        ws.cell(row=r, column=1, value=f"row {r}")
        ws.cell(row=r, column=2, value=r * 10)
    book = tmp_path / "long.xlsx"
    wb.save(str(book))
    r = client.get("/documents/preview", params={"path": str(book), "start": 250})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["first_row"] == 250 and body["rows"][0] == ["row 250", "2500"]
    # Without it, the preview is as it always was.
    plain = client.get("/documents/preview", params={"path": str(book)}).json()
    assert plain["first_row"] == 1 and plain["rows"][0] == ["row 1", "10"]
