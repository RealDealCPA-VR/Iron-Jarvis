"""v1.455.0 — readiness Wave H: connections, the rest (docs/PRODUCT-READINESS-RESULTS.md
0j.5 items 27-32 and the owner's decisions on 33 and 35).

Each test here is RED on the code before this wave:

* 27 — Help has a guide for the sign-in app a Drive / Gmail / OneDrive /
  Dropbox card needs, with a section per vendor the cards link to.
* 29 — a folder-first user's own repo config (``.mcp.json``, ``.cursor/``…)
  is read for the LAST WORKING FOLDER when no project is active.
* 30 — Codex's native install folder is on the CLI search list.
* 31 — web services take a key in a header or as a basic sign-in, refuse in
  plain words, and the remote reachability note names Windows Firewall.
* 32 — the Guide's main reading is the Handbook's overview and newest
  releases; the night-shift windows and retention fold under More options.

Nothing here reads the user's real configs: the import tests move HOME,
USERPROFILE and APPDATA to a scratch folder (the v13540 fixture's rule).
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.daemon import page_doors
from iron_jarvis.daemon.routes import helpdocs

REPO = Path(__file__).resolve().parents[1]
DASH = REPO / "dashboard"


def _src(p: Path) -> str:
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# 27 — the own-app guide
# --------------------------------------------------------------------------- #


def test_the_own_app_guide_is_served_bundled_and_a_help_guide():
    from iron_jarvis.guide.corpus import BUNDLED_DOCS

    assert "own-apps" in helpdocs._DOCS
    assert helpdocs._DOCS["own-apps"][1] == "OWN-APPS.md"
    assert "own-apps" in page_doors.HELP_GUIDES
    assert ("own-apps", "docs/OWN-APPS.md", "Your own sign-in apps") in BUNDLED_DOCS
    app = FastAPI()
    helpdocs.register(app, None)
    body = TestClient(app).get("/helpdocs/own-apps").json()
    assert body["markdown"].startswith("# Your own sign-in apps")


def test_every_card_section_the_dashboard_links_is_a_real_heading_of_the_guide():
    """lib/directoryLinks.OWN_APP_SECTIONS names a heading per card; each must
    be a heading of docs/OWN-APPS.md by the Help page's own slug rule."""
    ts = _src(DASH / "lib" / "directoryLinks.ts")
    block = ts[ts.index("export const OWN_APP_SECTIONS"):]
    block = block[: block.index("};")]
    sections = dict(re.findall(r"(\w+): \"([a-z0-9-]+)\"", block))
    assert set(sections) == {"google_drive", "gmail", "onedrive", "dropbox"}
    guide = _src(REPO / "docs" / "OWN-APPS.md")
    headings = {page_doors.heading_slug(h) for h in re.findall(r"^#{1,3} (.+)$", guide, flags=re.M)}
    for card, section in sections.items():
        assert section in headings, (card, section)
    assert 'export const OWN_APPS_GUIDE = "own-apps";' in ts


def test_the_guide_says_the_steps_a_first_timer_stalls_on():
    """The review's stall points: the consent screen, a test user, turning the
    API on, the client type, the redirect address, and (Microsoft) the
    account type the common sign-in page needs."""
    g = _src(REPO / "docs" / "OWN-APPS.md")
    for words in ("Test users", "Google Drive API", "Gmail API", "Desktop app", "Google Auth platform",
                  "Mobile and desktop", "personal Microsoft accounts", "Redirect URIs",
                  "files.content.read", "gcp-oauth.keys.json", "http://localhost:8787/oauth/"):
        assert words in g, words


def test_the_gmail_page_step_points_at_the_guide_never_a_docs_link():
    line = next(s for s in page_doors.STEPS if s.id == "gmail_setup").line
    assert "Step-by-step setup" in line and "Your own sign-in apps" in line
    assert "Docs link" not in line


# --------------------------------------------------------------------------- #
# 29 — the last working folder's own config
# --------------------------------------------------------------------------- #


@pytest.fixture
def homes(tmp_path, monkeypatch):
    user = tmp_path / "user"
    appdata = tmp_path / "appdata"
    user.mkdir()
    appdata.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("APPDATA", str(appdata))
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    return {"user": user, "appdata": appdata}


@pytest.fixture
def client(tmp_path, homes):
    from iron_jarvis.daemon.app import create_app

    return TestClient(create_app(str(tmp_path / "ij")))


CURSOR_PROJECT = '{"mcpServers": {"repo-only-v1455": {"command": "npx", "args": ["-y", "repo-only-v1455"]}}}'


def _coding_session(client, folder: Path) -> None:
    from iron_jarvis.harness import sessions as S

    S.create(client.app.state.platform.engine, str(folder), "Fix the API", {"provider": "mock", "model": "mock"})


def _rows(client) -> dict[str, dict]:
    res = client.get("/mcp/import/sources")
    assert res.status_code == 200, res.text
    return {r["source"]: r for r in res.json()["sources"]}


def test_a_folder_first_users_repo_config_is_read_from_their_last_coding_session(client, tmp_path):
    repo = tmp_path / "ClosePilot"
    (repo / ".cursor").mkdir(parents=True)
    (repo / ".cursor" / "mcp.json").write_text(CURSOR_PROJECT, encoding="utf-8")
    # No project, no coding session: the project source is not listed at all.
    assert "cursor-project" not in _rows(client)
    _coding_session(client, repo)
    row = _rows(client)["cursor-project"]
    assert row["origin"] == str(repo / ".cursor" / "mcp.json")
    assert row["label"] == "Cursor (folder ClosePilot)"
    assert [s["name"] for s in row["servers"]] == ["repo-only-v1455"]


def test_an_active_project_still_wins_and_keeps_its_words(client, tmp_path):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import Project

    repo = tmp_path / "repo"
    proj = tmp_path / "proj"
    for root in (repo, proj):
        (root / ".cursor").mkdir(parents=True)
        (root / ".cursor" / "mcp.json").write_text(CURSOR_PROJECT, encoding="utf-8")
    _coding_session(client, repo)
    platform = client.app.state.platform
    with session_scope(platform.engine) as db:
        db.add(Project(id="p-v1455", name="P", root=str(proj), status="active"))
        db.commit()
    platform.config.active_project_id = "p-v1455"
    row = _rows(client)["cursor-project"]
    assert row["origin"] == str(proj / ".cursor" / "mcp.json")
    assert row["label"] == "Cursor (this project)"


def test_a_network_folder_is_refused_by_its_text_never_looked_at(client, monkeypatch):
    from iron_jarvis.mcp import import_sources

    _coding_session(client, Path(r"\\fileserver\clients\repo"))
    looked: list[str] = []
    real_exists = Path.exists

    def spy(self, *a, **k):  # a stat of a share is an SMB connection
        if str(self).startswith(("\\\\", "//")):
            looked.append(str(self))
        return real_exists(self, *a, **k)

    monkeypatch.setattr(Path, "exists", spy)
    assert import_sources.last_working_folder(client.app.state.platform) == ""
    rows = _rows(client)
    assert "cursor-project" not in rows
    assert looked == []


# --------------------------------------------------------------------------- #
# 30 — Codex's native install folder
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(os.name != "nt", reason="Windows install layout")
def test_codex_from_its_native_installer_is_found(tmp_path, monkeypatch):
    """OpenAI's install.ps1 (releases.openai.com/codex, read 2026-11-08) puts
    codex.exe in %LOCALAPPDATA%\\Programs\\OpenAI\\Codex\\bin and adds it to the
    USER PATH, which a daemon started before the install never sees."""
    import iron_jarvis.terminals.ai_clis as m

    local = tmp_path / "LocalAppData"
    bin_dir = local / "Programs" / "OpenAI" / "Codex" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "codex.exe").write_text("stub")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "nothing"))
    monkeypatch.delenv("CODEX_INSTALL_DIR", raising=False)
    monkeypatch.setattr(m.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(m.shutil, "which", lambda exe: None)
    assert m._find("codex") == str(bin_dir / "codex.exe")


@pytest.mark.skipif(os.name != "nt", reason="Windows install layout")
def test_codex_install_dir_override_is_honoured_and_a_share_is_never_looked_at(tmp_path, monkeypatch):
    import iron_jarvis.terminals.ai_clis as m

    custom = tmp_path / "MyTools"
    custom.mkdir()
    (custom / "codex.exe").write_text("stub")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "ad"))
    monkeypatch.setattr(m.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(m.shutil, "which", lambda exe: None)
    monkeypatch.setenv("CODEX_INSTALL_DIR", str(custom))
    assert m._find("codex") == str(custom / "codex.exe")
    monkeypatch.setenv("CODEX_INSTALL_DIR", r"\\host\share\codex")
    looked: list[str] = []
    real_is_dir = m.Path.is_dir

    def spy(self):  # a stat of a share is an SMB connection (the v1.379.0 rule)
        if str(self).startswith(("\\\\", "//")):
            looked.append(str(self))
        return real_is_dir(self)

    monkeypatch.setattr(m.Path, "is_dir", spy)
    m._extra_bin_dirs()
    assert looked == []


# --------------------------------------------------------------------------- #
# 31 — web services: header and basic, plain refusals; the firewall note
# --------------------------------------------------------------------------- #


def _hookup(config: dict, key: str = "k-" + "v1455-secret"):
    from iron_jarvis.integrations.builtin import RestApiIntegration

    seen: dict = {}

    def fake_get(url, headers=None):
        seen["headers"] = dict(headers or {})
        return {"ok": True, "status_code": 200}

    cfg = {"base_url": "https://api.example.com", "auth_secret": "s", "http_get": fake_get, **config}
    return RestApiIntegration(cfg, lambda name: key), seen


def test_the_key_goes_in_the_header_the_service_names():
    integ, seen = _hookup({"auth_kind": "header", "auth_header": "X-API-Key"})
    assert integ.test_connection()["ok"] is True
    assert seen["headers"] == {"X-API-Key": "k-v1455-secret"}


def test_a_basic_sign_in_sends_the_username_and_the_key_as_its_password():
    integ, seen = _hookup({"auth_kind": "basic", "auth_user": "firm"})
    integ.test_connection()
    expected = base64.b64encode(b"firm:k-v1455-secret").decode("ascii")
    assert seen["headers"] == {"Authorization": f"Basic {expected}"}


def test_bearer_is_still_the_default_byte_for_byte():
    integ, seen = _hookup({})
    integ.test_connection()
    assert seen["headers"] == {"Authorization": "Bearer k-v1455-secret"}


def test_an_echoed_basic_sign_in_is_hidden_too():
    from iron_jarvis.integrations.builtin import auth_headers, scrub_values

    headers, hidden = auth_headers({"auth_kind": "basic", "auth_user": "firm"}, "k-v1455-secret")
    encoded = headers["Authorization"].split()[1]
    text = scrub_values(f"server said: {encoded} and k-v1455-secret", hidden)
    assert encoded not in text and "k-v1455-secret" not in text


@pytest.mark.parametrize("header", ["Host", "Content-Length", "bad header", "X-Key\nEvil", ""])
def test_a_header_that_cannot_carry_a_key_is_refused_in_plain_words(header):
    from iron_jarvis.integrations.builtin import auth_problem

    words = auth_problem("header", header)
    assert words and "integration" not in words.lower()


def test_the_page_door_stores_the_way_and_refuses_before_writing_the_key(tmp_path, homes):
    from iron_jarvis.daemon.app import create_app

    c = TestClient(create_app(str(tmp_path / "ij")))
    p = c.app.state.platform
    bad = c.post("/integrations", json={
        "name": "Bad", "base_url": "https://bad.example.com", "auth_token": "k-" + "v1455",
        "auth_kind": "header", "auth_header": "Host",
    })
    assert bad.status_code == 400 and "can’t carry a key" in bad.json()["detail"]
    assert p.secrets.get("integration_bad_token") in (None, "")
    ok = c.post("/integrations", json={
        "name": "Pricing", "base_url": "https://pricing.example.com", "auth_token": "k-" + "v1455",
        "auth_kind": "header", "auth_header": "X-API-Key",
    })
    assert ok.status_code == 200, ok.text
    cfg = p.integrations.stored_config("pricing")
    assert cfg["auth_kind"] == "header" and cfg["auth_header"] == "X-API-Key"
    assert "k-v1455" not in str(cfg)  # the key is only in the vault


def test_the_chat_door_takes_the_way_and_never_the_key(tmp_path, homes):
    import asyncio

    from iron_jarvis.daemon.app import create_app
    from types import SimpleNamespace

    from iron_jarvis.settings.records_tools import WebServiceAddTool

    c = TestClient(create_app(str(tmp_path / "ij")))
    p = c.app.state.platform
    tool = WebServiceAddTool(p)
    args = {"name": "Ledger API", "address": "https://ledger.example.com", "needs_key": True,
            "key_in": "basic", "username": "firm"}
    ctx = SimpleNamespace(session_id="chat", workspace=str(tmp_path))
    res = asyncio.run(tool.execute(args, ctx))
    assert res.ok, res.error
    cfg = p.integrations.stored_config("ledger_api")
    assert cfg["auth_kind"] == "basic" and cfg["auth_user"] == "firm"
    assert "the password of a sign-in as firm" in res.output
    assert res.data["secret_requests"][0]["label"] == "Ledger API's password"
    refused = asyncio.run(tool.execute(
        {**args, "name": "Other", "address": "https://other.example.com", "key_in": "header", "header": ""}, ctx
    ))
    assert not refused.ok and "Name the header" in refused.error


def test_the_add_form_offers_exactly_the_daemons_ways():
    from iron_jarvis.integrations.builtin import AUTH_KINDS

    ts = _src(DASH / "components" / "connections" / "RestHookups.tsx")
    block = ts[ts.index("export const KEY_IN_CHOICES"):]
    block = block[: block.index("];")]
    assert tuple(re.findall(r'value: "(\w+)"', block)) == AUTH_KINDS


def test_the_reachability_note_names_the_windows_firewall_prompt():
    from iron_jarvis.daemon.routes import agents

    for lan in (False, True):
        note = agents.reachability("http://192.168.1.20:8787/x", who="a remote", instead="x",
                                   lan_access=lan)["note"]
        assert agents.FIREWALL_NOTE in note
        assert "Allow an app through firewall" in note
    ts = _src(DASH / "lib" / "anotherJarvis.ts")
    assert f'export const FIREWALL_PLACE = "{agents.FIREWALL_PLACE}";' in ts


def test_the_model_servers_page_links_the_one_add_form():
    page = _src(DASH / "app" / "fleet" / "page.tsx")
    assert "function AddNodeForm" not in page and "<AddNodeForm" not in page
    assert 'export const ADD_MODEL_SERVER_HREF = "/connections?focus=endpoints";' in page
    assert "<AddModelServerLink />" in page
    conn = _src(DASH / "components" / "settings" / "pages" / "ConnectionsPage.tsx")
    assert "/detect`, {})" in conn  # the one form re-detects once a key is on the server


# --------------------------------------------------------------------------- #
# 32 — the Guide's main reading; Settings folds the expert rows
# --------------------------------------------------------------------------- #


def test_old_release_sections_weigh_less_than_current_ones():
    from iron_jarvis.guide import corpus

    idx = corpus.GuideIndex()
    hb = [s for s in idx.docs if s.doc == "handbook"]
    old = [s for s in hb if s.prior < 1.0]
    assert old, "the Handbook's old release sections are marked"
    assert all(s.prior == corpus.OLD_RELEASE_PRIOR for s in old)
    # The overview and the marker chapter itself stay full weight.
    for s in hb:
        if corpus._chapter(s.heading).split(" (")[0] in ("What Iron Jarvis is", corpus.HISTORY_STARTS_AFTER):
            assert s.prior == 1.0, s.heading
    # The newest release chapter is current.
    newest = max(
        (s for s in hb if corpus._VERSION_IN_HEADING.search(s.heading)),
        key=lambda s: tuple(int(g) for g in corpus._VERSION_IN_HEADING.search(s.heading).groups()),
    )
    assert newest.prior == 1.0
    assert corpus.HISTORY_STARTS_AFTER in _src(REPO / "docs" / "HANDBOOK.md")


def test_a_search_scores_a_section_by_its_prior():
    from iron_jarvis.guide import corpus

    idx = corpus.GuideIndex()
    secs = [corpus.Section("handbook", "The Handbook", "H › Old one (v1.200.0)", "zebra quilt answer"),
            corpus.Section("handbook", "The Handbook", "H › New one (v1.454.0)", "zebra quilt answer")]
    secs[0].prior = corpus.OLD_RELEASE_PRIOR
    idx.docs = secs
    idx.sections = lambda: secs  # type: ignore[method-assign]
    top = idx.search("zebra quilt", k=2)
    assert top[0][1].heading.endswith("(v1.454.0)")
    assert top[1][0] == pytest.approx(top[0][0] * corpus.OLD_RELEASE_PRIOR)


@pytest.mark.parametrize("key", [
    "night_window_start", "night_window_end", "night_max_at_once", "night_task_minutes",
    "night_card_wait_minutes", "curator_min_age_days", "curator_stale_after_days",
    "curator_interval_hours", "event_retention_days",
])
def test_the_expert_rows_fold_under_more_options(key):
    from iron_jarvis.settings import schema

    assert schema.BY_KEY[key].advanced is True


# --------------------------------------------------------------------------- #
# 35 — the one connections front door is mounted and reachable by its address
# --------------------------------------------------------------------------- #


def test_the_landing_sits_atop_settings_connections_and_its_address_keeps_the_old_alias():
    home = _src(DASH / "components" / "settings" / "SettingsHome.tsx")
    assert '{!q && active === "connections" && <ConnectionsHome onGo={goTo} />}' in home
    # /connections?tab=… opens the GROUP (the landing), never the accounts
    # section the alias names; the alias itself is unchanged.
    rule = 'if (section.startsWith("connections") && /^(discover|connected|import)$/.test(p.get("tab") || "")) {'
    assert rule in home
    after = home[home.index(rule) + len(rule):]
    assert after.lstrip().startswith('section = "connections";'), after[:120]
    lib = _src(DASH / "lib" / "connectionsHome.ts")
    assert 'export const CONNECTIONS_HOME_HREF = "/connections?tab=discover";' in lib
    relocated = _src(DASH / "lib" / "relocated.ts")
    assert '"/connections": { base: "/settings", extra: { section: "connections-accounts" } },' in relocated
    # The other places link back to it.
    for page, test_id in (
        ("components/settings/pages/ToolsPage.tsx", "tools-all-connections"),
        ("components/settings/pages/NotificationsPage.tsx", "notifications-all-connections"),
        ("app/fleet/page.tsx", "fleet-all-connections"),
    ):
        assert f'<AllConnectionsLink testId="{test_id}" />' in _src(DASH / page), page
    assert 'href: "/connections?tab=discover",' in _src(DASH / "components" / "settings" / "pages" / "DirectoryPage.tsx")
