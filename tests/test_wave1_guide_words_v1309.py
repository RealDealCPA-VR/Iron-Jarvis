"""v1.309.0 wave 1, track D — the Guide and the canon speak about the Agents
page as it is NOW, and the Guide's own links open the right screen.

Since v1.308.0 bare ``/agents`` is the New-task composer; Your team is
``/agents?view=team`` (``&agent=<name>`` opens one agent, contract 8) and a
mission is ``/agents?mission=<id>`` (``&project=<pid>`` when it has one,
contract 9). The Guide is grounded in VOCABULARY.md and the Handbook
(``guide/corpus.BUNDLED_DOCS``) and answers "where is my …" with
``app_search`` hits carrying a dashboard path — so a stale word or a stale
path there is the Guide confidently sending the user somewhere wrong.

Pinned, each through the REAL seam (the real app factory, the real
registry's ``app_search`` tool, the real Guide index over the bundled docs):

- an agent hit opens THAT agent on Your team, not the composer;
- a mission session opens its mission screen (inside its project); a plain
  session and a mission's teammate still open the session page (controls);
- VOCABULARY.md has a canon row for the objective and no longer defines the
  Agents page as the round table (the round-table row is re-scoped to the
  chat @-mention panel, which still exists);
- the Handbook's "Agents & jobs" says the bell tells you when an objective
  is done (the words the bell row uses);
- the guide package no longer says it is reached via "Talk / Give work".
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session, SessionStatus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.guide import GuideIndex


def _route(href: str) -> tuple[str, dict[str, str]]:
    """An href as (path, {param: value}) — order-independent."""
    parts = urlsplit(href)
    return parts.path, {k: v[0] for k, v in parse_qs(parts.query).items()}


@pytest.fixture()
def app_client(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    client.__enter__()
    yield app, client
    client.__exit__(None, None, None)


async def _app_search(app, query: str, kinds: list[str]):
    tool = app.state.platform.registry.get("app_search")
    assert tool is not None, "app_search is not registered"
    res = await tool.execute({"query": query, "kinds": kinds}, None)
    assert res.ok, res.error
    return res.data["hits"]


def _add_session(app, **fields) -> str:
    row = Session(status=SessionStatus.COMPLETED, **fields)
    with session_scope(app.state.platform.engine) as db:
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


# ------------------------------------------------------------ app_search


@pytest.mark.asyncio
async def test_app_search_agent_hit_opens_that_agent_on_your_team(app_client):
    app, client = app_client
    r = client.post(
        "/agents",
        json={"name": "tax-reader", "system_prompt": "You read tax documents.", "description": "Reads tax documents"},
    )
    assert r.status_code in (200, 201), r.text
    hits = await _app_search(app, "tax reader", ["agent"])
    hit = next(h for h in hits if h["id"] == "tax-reader")
    path, q = _route(hit["open"])
    # Red on v1.308.0: "/agents" — the New-task composer, which shows
    # nothing about tax-reader.
    assert path == "/agents"
    assert q.get("view") == "team"
    assert q.get("agent") == "tax-reader"


@pytest.mark.asyncio
async def test_app_search_mission_opens_its_mission_screen_and_a_plain_run_its_page(app_client):
    app, client = app_client
    pid = client.post("/projects", json={"name": "Acme Tax 2026"}).json()["id"]
    mission = _add_session(
        app, task="Research the zephyr competitors", origin="job:mission", project_id=pid
    )
    loose = _add_session(app, task="Research the zephyr suppliers", origin="job:mission")
    plain = _add_session(app, task="Research the zephyr invoices", origin="chat")
    member = _add_session(app, task="Research the zephyr pricing", origin="job:mission-member")

    hits = {h["id"]: h["open"] for h in await _app_search(app, "zephyr", ["session"])}
    assert {mission, loose, plain, member} <= set(hits), hits

    # Red on v1.308.0: every one of these was "/sessions/<id>" — an
    # Advanced-mode engineering page with no way back to the mission.
    assert _route(hits[mission]) == ("/agents", {"mission": mission, "project": pid})
    assert _route(hits[loose]) == ("/agents", {"mission": loose})
    # Controls: an ordinary run, and a TEAMMATE (contract 1's child origin —
    # it is not a mission of its own), keep the session page.
    assert hits[plain] == f"/sessions/{plain}"
    assert hits[member] != f"/agents?mission={member}"
    assert _route(hits[member])[1].get("mission") != member


# ------------------------------------------------------ the Guide's words


def _doc_text(slug: str, heading_part: str = "") -> str:
    idx = GuideIndex()
    secs = [
        s
        for s in idx.sections()
        if s.doc == slug and not s.live and (not heading_part or heading_part in s.heading)
    ]
    assert secs, f"the Guide indexes no {slug!r} section matching {heading_part!r}"
    return "\n".join(s.text for s in secs)


def test_vocabulary_names_the_objective_and_rescopes_the_round_table():
    text = _doc_text("vocabulary")
    rows = [ln for ln in text.splitlines() if ln.lstrip().startswith("|")]
    # A canon row whose ONE name is the objective, with "mission" retired
    # into a search alias (the URL and the API keep the word; the user
    # meets "objective").
    objective = [r for r in rows if "**objective**" in r.lower()]
    assert objective, "VOCABULARY.md has no canon row for the objective"
    assert any("mission" in r.lower() for r in objective)
    # The round-table row no longer says it IS the Agents page.
    round_rows = [r for r in rows if "round-table" in r.lower()]
    assert round_rows, "the round-table row was deleted — re-scope it, the chat panel still exists"
    for r in round_rows:
        assert "the agents page" not in r.lower(), r
    assert any("@-mention" in r or "chat" in r.lower() for r in round_rows)


def test_handbook_agents_section_says_the_bell_tells_you_an_objective_is_done():
    text = _doc_text("handbook", "Agents & jobs").lower()
    assert "bell" in text
    assert "your objective is done" in text


def test_guide_package_no_longer_points_at_talk_and_give_work():
    import iron_jarvis.guide as guide

    doc = guide.__doc__ or ""
    assert "Talk / Give work" not in doc
    assert "At the round table" not in doc
