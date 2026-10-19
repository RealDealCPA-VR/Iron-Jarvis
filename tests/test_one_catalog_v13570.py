"""v1.357.0 (G82) — one catalog file for every pack.

THE GAP (product-readiness audit, section O): two pack catalogs —
``connectors/catalog.py`` (the Directory's cards) and a table in
``daemon/app.py`` (``_MCP_CATALOG``, the Tools page's ``GET /mcp/catalog``) —
named the same packs with different names and copy, and their drift once
shipped a package npm never had (v1.350.1).

ACCEPTANCE: the Tools catalog and the Directory show the same packs with the
same names from one file.

Every pin reads the REAL answers of the REAL app factory (``GET /mcp/catalog``
and ``GET /connectors``) and the real catalog module; the dashboard's own
copies of pack names are read from its source (CRLF normalised).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors import catalog
from iron_jarvis.connectors import service
from iron_jarvis.connectors.app_names import apps_named
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import agents as agents_routes

ROOT = Path(__file__).resolve().parents[1]


def _src(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture
def client(tmp_path, monkeypatch):
    # The launcher probe answers "not found" so the rows are deterministic.
    monkeypatch.setattr(agents_routes, "launcher_ready", lambda cmd: False)
    return TestClient(create_app(str(tmp_path / "home")))


def _tools_rows(client) -> list[dict]:
    r = client.get("/mcp/catalog")
    assert r.status_code == 200, r.text
    return r.json()["catalog"]


def _directory_rows(client) -> dict[str, dict]:
    r = client.get("/connectors")
    assert r.status_code == 200, r.text
    return {row.get("catalog_id") or row["id"]: row for row in r.json()["connectors"]}


# --------------------------------------------------------------------------- #
# ACCEPTANCE: the same packs, the same names, from one file.
# --------------------------------------------------------------------------- #
def test_a_pack_on_both_surfaces_has_one_name_and_one_set_of_words(client):
    tools = _tools_rows(client)
    directory = _directory_rows(client)
    by_tools_id = {c.tools_id: c for c in catalog.CATALOG if c.tools_id}
    shared = [row for row in tools if row["id"] in by_tools_id]
    # Anti-vacuity: the six packs both pages have offered since v1.316.0.
    assert {r["id"] for r in shared} == {
        "github", "filesystem", "fetch", "memory", "sequentialthinking", "box",
    }
    for row in shared:
        card = directory[by_tools_id[row["id"]].id]
        assert row["name"] == card["name"], (row["id"], row["name"], card["name"])
        # The Tools card's words ARE the Directory card's sentence.
        assert row["description"].startswith(card["unlocks"]), row["id"]
    # The names a new user reads on both pages (the plain ones).
    names = {r["id"]: r["name"] for r in tools}
    assert names["filesystem"] == "Files & folders" and names["fetch"] == "Fetch web pages"
    assert names["memory"] == "Knowledge graph" and names["sequentialthinking"] == "Step-by-step reasoning"
    assert names["box"] == "Box" and names["github"] == "GitHub"


def test_the_tools_catalog_is_built_from_the_one_catalog_file(client):
    rows = _tools_rows(client)
    assert [r["id"] for r in rows] == list(catalog.TOOLS_ORDER)
    expected = {r["id"]: r for r in catalog.mcp_catalog_rows()}
    for row in rows:
        got = {k: v for k, v in row.items() if k != "runtime_ready"}
        assert got == expected[row["id"]], row["id"]
        assert type(row["runtime_ready"]) is bool
    # The route reads the ONE list app.py hands it; no second table exists.
    assert client.app.state.d._MCP_CATALOG == catalog.mcp_catalog_rows()


def test_app_py_holds_no_pack_table():
    src = _src("src/iron_jarvis/daemon/app.py")
    assert "_MCP_CATALOG = _mcp_catalog_rows()" in src
    for needle in ('"@modelcontextprotocol/', '"mcp-server-', '"@playwright/', '"category": "reference"'):
        assert needle not in src, needle


def test_every_install_field_the_tools_page_reads_is_kept(client):
    """The Tools page's Add posts these fields as they are: each row keeps
    its id, command, arguments, keys and (GitHub) its server address."""
    rows = {r["id"]: r for r in _tools_rows(client)}
    assert rows["github"]["transport"] == "http" and rows["github"]["url"] == catalog.GITHUB_MCP_URL
    assert rows["github"]["auth_field"] == "GITHUB_PERSONAL_ACCESS_TOKEN"
    assert rows["github"]["env_keys"] == ["GITHUB_PERSONAL_ACCESS_TOKEN"] and rows["github"]["needs"] == ""
    assert rows["box"]["env_keys"] == ["BOX_CLIENT_ID", "BOX_CLIENT_SECRET"]
    assert rows["git"]["args"] == ["mcp-server-git", "--repository", "<repo-path>"]
    assert rows["sequentialthinking"]["args"] == ["-y", "@modelcontextprotocol/server-sequential-thinking"]
    assert rows["playwright"]["args"] == ["-y", "@playwright/mcp@latest"]
    for r in rows.values():
        assert {"id", "name", "description", "command", "args", "category", "needs"} <= set(r)
        assert r["category"] in ("reference", "integration"), r["id"]
        assert r["needs"] in ("Node", "Python (uv)", ""), r["id"]
        if r["command"] == "npx":
            assert r["needs"] == "Node"
        if r["command"] == "uvx":
            assert r["needs"] == "Python (uv)"
        assert "env_keys" not in r or r["env_keys"], "an empty key list is not sent"


# --------------------------------------------------------------------------- #
# What only one page shows, and why.
# --------------------------------------------------------------------------- #
def test_tools_only_rows_never_become_directory_cards_or_chat_apps():
    only = {c.id for c in catalog.TOOLS_ONLY}
    assert only == {"git", "everything", "playwright"}
    assert not only & {c.id for c in catalog.CATALOG}
    assert all(catalog.get_connector(i) is None for i in only)
    assert apps_named("connect my git repositories and the playwright browser") == []


def test_the_directory_only_packs_are_the_ones_the_tools_form_cannot_set_up():
    """Pinned so a change is deliberate: Gmail needs a Google developer setup
    outside the app and Microsoft 365 an account choice and a sign-in, which
    the Tools page's add form has neither of; the other app cards are reached
    from the Tools page's "Add a tool" door."""
    directory_only = {
        c.id for c in catalog.CATALOG if c.connect_via == "mcp" and not c.tools_id
    }
    assert directory_only == {
        "postgres", "sentry", "slack", "gmail", "microsoft_365",
        "notion", "google_maps", "brave_search", "stripe",
    }
    for cid in ("gmail", "microsoft_365"):
        card = catalog.get_connector(cid)
        assert card.needs or card.signin_args, cid


def test_every_tools_row_is_ordered_exactly_once():
    ids = [c.tools_id for c in [*catalog.CATALOG, *catalog.TOOLS_ONLY] if c.tools_id]
    assert len(ids) == len(set(ids))
    assert sorted(ids) == sorted(catalog.TOOLS_ORDER)
    assert len(catalog.TOOLS_ORDER) == len(set(catalog.TOOLS_ORDER))


# --------------------------------------------------------------------------- #
# Everything that names the same pack reads it from the one file.
# --------------------------------------------------------------------------- #
def test_the_install_name_aliases_come_from_the_catalog():
    assert service.PACK_ALIASES == {"sequential_thinking": ("sequentialthinking",)}


def _ts_set(rel: str, const: str) -> "set[str] | None":
    src = _src(rel)
    m = re.search(rf"const {const}[^=]*=\s*new Set\(\[(.*?)\]\)", src, re.S)
    return set(re.findall(r'"([a-z_]+)"', m.group(1))) if m else None


def test_the_twin_field_names_the_doors_that_add_the_same_pack(client):
    """A Tools row's ``directory_twin`` is the Directory card that saves the
    SAME pack under the SAME name (Sequential Thinking's two doors use two
    names, so it has none)."""
    rows = _tools_rows(client)
    tools_twins = {r["id"] for r in rows if r["directory_twin"]}
    assert all(r["directory_twin"] in ("", r["id"]) for r in rows)
    assert tools_twins == {catalog.tools_twin(c) for c in catalog.CATALOG} - {""}
    assert tools_twins == {"github", "filesystem", "fetch", "memory", "box"}
    # The dashboard's two hand-kept twin lists agree with the one file while
    # they exist (the integrator may replace ToolsPage's with the field).
    for rel, const in (
        ("dashboard/components/settings/pages/ToolsPage.tsx", "DIRECTORY_TWINS"),
        ("dashboard/components/settings/pages/DirectoryPage.tsx", "TOOLS_TWINS"),
    ):
        listed = _ts_set(rel, const)
        if listed is not None:
            assert listed == tools_twins, (rel, listed)


def test_the_dashboards_own_pack_words_match_the_catalog(client):
    """``components/tools/meta.ts`` names three packs on the built-in tools'
    cards ("Use Files & folders instead") and lists every pack's reach
    (PACK_CAPS — a row it does not know claims "Nothing on your machine")."""
    meta = _src("dashboard/components/tools/meta.ts")
    names = {r["id"]: r["name"] for r in _tools_rows(client)}
    for pack_id, shown in re.findall(r'id:\s*"([a-z_]+)",\s*name:\s*"([^"]+)"', meta):
        assert names[pack_id] == shown, (pack_id, shown)
    caps = meta[meta.index("const PACK_CAPS"):]
    caps = caps[: caps.index("};")]
    assert set(re.findall(r"^\s*([a-z_]+):", caps, re.M)) == set(names)


@pytest.mark.parametrize(
    "text,cid",
    [
        ("set up sequential thinking", "sequential_thinking"),
        ("connect step-by-step reasoning", "sequential_thinking"),
        ("connect the filesystem", "filesystem"),
        ("connect files & folders", "filesystem"),
        ("connect web fetch", "fetch"),
        ("connect fetch web pages", "fetch"),
        ("set up the knowledge graph memory", "memory"),
        ("connect the knowledge graph", "memory"),
    ],
)
def test_chat_still_knows_each_renamed_app_by_its_old_and_new_name(text, cid):
    assert cid in apps_named(text)


def test_a_renamed_generic_app_still_needs_a_connect_word():
    assert apps_named("sort these files & folders by date") == []
    assert apps_named("think it through step-by-step reasoning out loud") == []
