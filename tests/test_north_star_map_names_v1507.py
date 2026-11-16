"""North-star fix 8.2 (v1.507.0): one name per page, on the Home status map too.

The status map (``daemon/routes/system_map.py``) wrote its own labels and
links, so one page had two names: the map called ``/sessions`` "Sessions"
while the sidebar and search call it "Agent runs" (VOCABULARY.md retires
"Sessions" as the page's name), the chat star read "Chats" beside the page
"Chat", the models star "Models" on the Connections page, and the Directory
pointed at an address the page table does not use. Now every star takes its
name and link from the ONE page table (``daemon/pages.py``, v1.506.0) by the
row's id, so they cannot drift, and the map has stars for Improvements and
Clients.

Real app and route.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon import pages
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import system_map as sm

ROWS = {(p.name, p.href) for p in pages.PAGES}


def _nodes(client) -> list[dict]:
    got = client.get("/ui/system-map").json()
    return [n for area in got["areas"] for n in area["nodes"]]


def test_every_map_star_has_the_name_and_link_of_a_page_table_row(tmp_path):
    nodes = _nodes(TestClient(create_app(str(tmp_path / "d"))))
    assert len(nodes) >= 27
    wrong = [(n["key"], n["label"], n["href"]) for n in nodes if (n["label"], n["href"]) not in ROWS]
    assert not wrong, wrong


def test_agent_runs_is_agent_runs_and_improvements_and_clients_have_stars(tmp_path):
    client = TestClient(create_app(str(tmp_path / "d")))
    by_key = {n["key"]: n for n in _nodes(client)}
    assert (by_key["sessions"]["label"], by_key["sessions"]["href"]) == ("Agent runs", "/sessions")
    assert all(n["label"] != "Sessions" for n in by_key.values())
    assert (by_key["improvements"]["label"], by_key["improvements"]["href"]) == ("Improvements", "/improvements")
    assert (by_key["clients"]["label"], by_key["clients"]["href"]) == ("Clients", "/clients")
    knowledge = next(a for a in client.get("/ui/system-map").json()["areas"] if a["key"] == "knowledge")
    assert {"improvements", "clients"} <= {n["key"] for n in knowledge["nodes"]}


def test_a_star_that_cannot_be_read_still_has_its_page_rows_name(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path / "d")))

    def broken(*a, **k):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(sm, "_count", broken)
    nodes = _nodes(client)
    assert nodes, "the fallback stars are still drawn"
    wrong = [(n["key"], n["label"], n["href"]) for n in nodes if (n["label"], n["href"]) not in ROWS]
    assert not wrong, wrong
    assert {"sessions", "improvements", "clients"} <= {n["key"] for n in nodes}


def test_a_star_is_named_by_a_page_row_id_never_by_its_own_words():
    assert sm._node("sessions", "page_agent_runs")["label"] == "Agent runs"
    assert sm._node("clients", "page_clients")["href"] == "/clients"
