"""North-star plan 6.1: ONE PAGE TABLE (v1.506.0).

The feature lookup knew no pages but 17 page-only steps and 8 "not offered"
lines, and its own rule then told the model "say plainly that you found no
feature". ``daemon/pages.py`` is one table of every dashboard page (and the
Settings sections that are places of their own): its on-screen name, its
address and what can be done there. This file pins:

* the SCAN: every ``dashboard/app/*/page.tsx`` has a row, every row's address
  is a page that exists today, and a row's name is the one the sidebar uses;
* the lookup has one entry per row and answers it as a page, never as
  "nothing found";
* a page row the lookup RETURNED this turn becomes a door under the reply, in
  BOTH lanes' real replies (a scripted model that calls ``find_capability``),
  with the same cap and one door per address; a page row nobody looked up,
  and a lookup on the phone, give none.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from iron_jarvis.capability_search import index as CI
from iron_jarvis.capability_search import join as J
from iron_jarvis.capability_search import tool as CT_TOOL
from iron_jarvis.daemon import doors as D
from iron_jarvis.daemon import pages as PG
from iron_jarvis.daemon.app import create_app
from tests.test_script_power_lanes_v1473 import Model, _body, _call, _post, _stream

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

ROOT = Path(__file__).resolve().parent.parent
DASH = ROOT / "dashboard"
NAME = CT_TOOL.NAME


# --------------------------------------------------------------------------- #
# the scan
# --------------------------------------------------------------------------- #
def _app_routes() -> set[str]:
    return {p.name for p in (DASH / "app").iterdir() if (p / "page.tsx").exists()}


def _settings_sections() -> set[str]:
    src = (DASH / "components" / "settings" / "SettingsHome.tsx").read_text(encoding="utf-8")
    return set(re.findall(r'^\s+id: "([a-z]+-[a-z-]+)",$', src, re.M))


def _resolves(href: str) -> bool:
    u = urlparse(href)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    seg = u.path.strip("/").split("/")[0]
    if seg not in _app_routes():
        return False
    if u.path == "/settings" and q:
        return set(q) == {"section"} and q["section"] in _settings_sections()
    return not q


def test_every_dashboard_page_has_a_row_and_every_row_leads_to_a_real_page():
    routes = _app_routes()
    assert len(routes) >= 40, sorted(routes)
    covered = {r for page in PG.PAGES for r in page.routes}
    assert routes - covered == set(), f"pages with no row: {sorted(routes - covered)}"
    assert covered - routes == set(), f"rows for pages that do not exist: {sorted(covered - routes)}"
    for page in PG.PAGES:
        assert _resolves(page.href), (page.id, page.href)
        assert page.name.strip() and page.what.strip(), page.id
        assert page.what.rstrip().endswith("."), page.id
    # Anti-vacuity: a broken address is caught.
    assert not _resolves("/nowhere") and not _resolves("/settings?section=system-nothing")
    ids = [p.id for p in PG.PAGES]
    assert len(ids) == len(set(ids)) and all(i.startswith("page_") for i in ids)
    hrefs = [p.href for p in PG.PAGES]
    assert len(hrefs) == len(set(hrefs)), "one row per address"


def _nav_labels() -> dict[str, str]:
    out: dict[str, str] = {}
    for name in ("nav.ts", "surfaces.ts"):
        src = (DASH / "lib" / name).read_text(encoding="utf-8")
        for href, label in re.findall(r'href: "(/[a-z-]*)",\s*(?://[^\n]*\n\s*)*label: "([^"]+)"', src):
            out.setdefault(href, label)
    return out


def test_a_row_is_named_the_way_the_sidebar_names_its_page():
    labels = _nav_labels()
    assert labels.get("/reflex") == "Rules" and labels.get("/sessions") == "Agent runs", labels
    checked = 0
    for page in PG.PAGES:
        if page.href in labels:
            assert page.name == labels[page.href], (page.id, page.name, labels[page.href])
            checked += 1
    assert checked >= 30, checked
    src = (DASH / "components" / "settings" / "SettingsHome.tsx").read_text(encoding="utf-8")
    for page in PG.PAGES:
        if page.in_settings and page.href.startswith("/settings?section="):
            section = page.href.split("=", 1)[1]
            if section == "connections-apps":
                continue  # named like the page it holds: the Directory
            block = src.split(f'id: "{section}",', 1)[1][:400]
            assert f'label: "{page.name}"' in block, (page.id, section)


# --------------------------------------------------------------------------- #
# the lookup
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def plat(tmp_path_factory):
    return create_app(str(tmp_path_factory.mktemp("pages") / "home")).state.platform


def test_the_lookup_has_one_entry_per_page_row(plat):
    entries = [e for e in CI.build(plat, phrasings=False).entries if getattr(e, "place", False)]
    assert {e.id for e in entries} == {p.id for p in PG.PAGES}
    for e in entries:
        page = PG.BY_ID[e.id]
        assert e.kind == "page" and e.href == page.href and e.title == page.name and e.line == page.what


def test_a_page_row_is_answered_as_a_page_not_as_nothing_found(plat):
    text, data = CT_TOOL.answer(plat, "rules page when a message arrives run something automatically", 8)
    rows = [r for r in data["matches"] if r.get("place")]
    assert rows, text
    assert any(r["id"] == "page_rules" for r in rows), [r["id"] for r in data["matches"]]
    assert 'The page "Rules" (/reflex): ' in text, text
    assert CT_TOOL.NOTHING_FOUND not in text


# --------------------------------------------------------------------------- #
# through the real lanes
# --------------------------------------------------------------------------- #
@pytest.fixture()
def desk(tmp_path):
    app = create_app(str(tmp_path / "home"))
    ws = tmp_path / "work"
    ws.mkdir()
    app.state.platform.config.capability_search = True
    J.FOUND.clear()
    yield app, ws
    J.FOUND.clear()


QUERY = "improvements page waiting suggestions accept or decline"


def _first_place(plat) -> dict:
    _text, data = CT_TOOL.answer(plat, QUERY, 5)
    top = [r for r in data["matches"][:3] if r.get("place")]
    assert top, data["matches"]
    return PG.door_for(top[0]["id"])


def _body_plain(ws, text):
    body = _body(ws, text)
    body.pop("workspace_dir", None)
    return body


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_page_the_lookup_returned_becomes_a_door_in_both_lanes(desk, lane):
    app, ws = desk
    want = _first_place(app.state.platform)
    assert want == {"href": "/improvements", "label": "Open Improvements"}
    model = Model([_call(1, NAME, query=QUERY), "Those wait on the Improvements page."])
    body = _body_plain(ws, "where do I answer what you suggested")
    if lane == "stream":
        _f, _cards, done = await _stream(app, body, model)
    else:
        done = await _post(app, body, model)
    assert want in done["doors"], done["doors"]


@pytest.mark.asyncio
@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_no_lookup_no_page_door(desk, lane):
    app, ws = desk
    model = Model(["Those wait on the Improvements page."])
    body = _body_plain(ws, "where do I answer what you suggested")
    done = (await _stream(app, body, model))[2] if lane == "stream" else await _post(app, body, model)
    assert {"href": "/improvements", "label": "Open Improvements"} not in done["doors"], done["doors"]


@pytest.mark.asyncio
async def test_a_lookup_door_never_crowds_out_or_repeats_a_door(desk):
    app, ws = desk
    plat = app.state.platform
    key = "chat\x1fturn-x"
    J.FOUND.note_pages(key, [{"kind": "page", "id": "page_improvements", "place": True}])
    from types import SimpleNamespace

    from iron_jarvis.daemon import chat_turn as CT

    ctx = SimpleNamespace(session_id="chat", turn_id="turn-x")
    body = SimpleNamespace(messages=[{"role": "user", "content": "hi"}], pane_id="")
    full = [{"href": f"/x{i}", "label": str(i)} for i in range(D.MAX_DOORS)]
    assert CT.with_page_door(full, body, platform=plat, ctx=ctx) == full
    same = [{"href": "/improvements", "label": "See the suggestion"}]
    assert CT.with_page_door(same, body, platform=plat, ctx=ctx) == same
    assert CT.with_page_door([], body, platform=plat, ctx=ctx) == [
        {"href": "/improvements", "label": "Open Improvements"}
    ]
    # Only for a turn that looked it up.
    other = SimpleNamespace(session_id="chat", turn_id="turn-y")
    assert CT.with_page_door([], body, platform=plat, ctx=other) == []


@pytest.mark.asyncio
async def test_the_phone_gets_no_lookup_door(desk):
    app, ws = desk
    from iron_jarvis.providers.failure_words import phone_surface

    model = Model([_call(1, NAME, query=QUERY), "On the Improvements page."])
    with phone_surface():
        done = await _post(app, _body_plain(ws, "where do I answer what you suggested"), model)
    assert done["doors"] == [], done["doors"]
