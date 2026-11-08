"""v1.455.0 — the independent review of readiness Wave H. Each test was RED on
the builder's commit (1430f0c8):

* 32 — a Handbook chapter whose own title holds " › " ("Archiving a Build ›
  Code session … (v1.453.0)") was read as the chapter "Archiving a Build",
  which names no version, so a CURRENT release section was weighted as old.
* 27 — the OneDrive steps said a personal Microsoft account can register the
  app by itself; Microsoft's page needs a directory (an Azure account, whose
  Default Directory is free) — a personal account alone is refused.
* 29 — the last working folder of a LOCAL ONLY coding session reached a
  chat's `app_import` answer (the folder name and its servers) in a turn
  that leaves this network.
* 31 — the page door saved a key pasted into the Username or Header box in
  plain text (the integrations table).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[1]


def _src(p: Path) -> str:
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# 32 — a chapter title holding " › " keeps its own version
# --------------------------------------------------------------------------- #


def test_a_chapter_title_with_a_breadcrumb_arrow_keeps_its_version(monkeypatch):
    from iron_jarvis.guide import corpus

    text = "\n".join([
        "# The Handbook",
        "## Troubleshooting in one minute",
        "Fix it.",
        "## Archiving a Build › Code session keeps a summary (v9.2.0)",
        "Current words about archiving.",
        "### More on it",
        "Still current.",
        "## Something old (v1.0.0)",
        "Old words.",
    ])
    secs = corpus.split_markdown("handbook", "The Handbook", text)
    monkeypatch.setattr(corpus, "CURRENT_RELEASES", 1)
    corpus.mark_release_history(secs)
    by = {s.heading: s.prior for s in secs}
    assert by["The Handbook › Archiving a Build › Code session keeps a summary (v9.2.0)"] == 1.0
    assert by["The Handbook › Archiving a Build › Code session keeps a summary (v9.2.0) › More on it"] == 1.0
    assert by["The Handbook › Something old (v1.0.0)"] == corpus.OLD_RELEASE_PRIOR


def test_no_current_release_section_of_the_real_handbook_is_weighted_as_old():
    from iron_jarvis.guide import corpus

    text = _src(REPO / "docs" / "HANDBOOK.md")
    chapters = re.findall(r"^## (.+)$", text, flags=re.M)
    start = chapters.index(corpus.HISTORY_STARTS_AFTER) + 1
    vers = []
    for c in chapters[start:]:
        m = corpus._VERSION_IN_HEADING.search(c)
        if m:
            vers.append((tuple(int(g) for g in m.groups()), c))
    current = {v for v, _c in sorted(vers, reverse=True)[: corpus.CURRENT_RELEASES]}
    current_titles = {c for v, c in vers if v in current}
    assert any(" › " in t for t in current_titles), "the case this pins is still in the Handbook"
    secs = corpus.split_markdown("handbook", "The Handbook", text)
    corpus.mark_release_history(secs)
    checked = 0
    for s in secs:
        if s.chapter in current_titles:
            assert s.prior == 1.0, s.heading
            checked += 1
    assert {s.chapter for s in secs} >= current_titles
    assert checked >= len(current_titles)


# --------------------------------------------------------------------------- #
# 27 — Microsoft's account rule, Dropbox's page
# --------------------------------------------------------------------------- #


def _section(doc: str, heading: str) -> str:
    start = doc.index(f"## {heading}\n")
    rest = doc[start + 3:]
    end = rest.find("\n## ")
    return rest if end < 0 else rest[:end]


def test_the_onedrive_steps_never_say_a_personal_account_registers_alone():
    doc = _src(REPO / "docs" / "OWN-APPS.md")
    one = _section(doc, "OneDrive")
    assert "A personal Microsoft account can do this too" not in one
    # Microsoft's prerequisite: an Azure account (its Default Directory).
    assert "Azure account" in one and "Default Directory" in one


def test_the_dropbox_link_is_the_page_dropbox_serves_now():
    doc = _src(REPO / "docs" / "OWN-APPS.md")
    drop = _section(doc, "Dropbox")
    assert "developers.dropbox.com/oauth-guide" not in drop  # answers 301 now
    assert "https://docs.dropboxapi.com/dropbox-api/docs/oauth" in drop


# --------------------------------------------------------------------------- #
# 29 — a local-only folder never reaches a turn that leaves this network
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


CURSOR = '{"mcpServers": {"client-books-v1455r": {"command": "npx", "args": ["-y", "client-books"]}}}'


def _repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    (repo / ".cursor").mkdir(parents=True)
    (repo / ".cursor" / "mcp.json").write_text(CURSOR, encoding="utf-8")
    return repo


def test_a_local_only_sessions_folder_is_withheld_from_a_cloud_chats_import_list(client, tmp_path):
    from iron_jarvis.harness import sessions as S
    from iron_jarvis.mcp import import_sources
    from iron_jarvis.providers import egress
    from iron_jarvis.settings.records_apps import list_other_apps

    platform = client.app.state.platform
    repo = _repo(tmp_path, "Smith Family Trust")
    S.create(platform.engine, str(repo), "Books", {"provider": "mock", "model": "mock", "local_only": True})
    # The dashboard's own import screen (nothing leaves the PC) still reads it.
    assert import_sources.last_working_folder(platform) == str(repo)
    # A chat turn that leaves this network is never told about it.
    words = list_other_apps(platform)
    assert "Smith Family Trust" not in words and "client-books-v1455r" not in words
    # A Local only turn (stays on this network) may.
    with egress.local_only_scope(True):
        assert "client-books-v1455r" in list_other_apps(platform)


def test_a_cloud_sessions_folder_still_reaches_the_chats_import_list(client, tmp_path):
    from iron_jarvis.harness import sessions as S
    from iron_jarvis.settings.records_apps import list_other_apps

    platform = client.app.state.platform
    repo = _repo(tmp_path, "ClosePilot")
    S.create(platform.engine, str(repo), "Fix", {"provider": "mock", "model": "mock"})
    assert "client-books-v1455r" in list_other_apps(platform)


# --------------------------------------------------------------------------- #
# 31 — a key pasted into the wrong box is never stored in plain text
# --------------------------------------------------------------------------- #


KEY = "sk-live-0123456789abcdefREVIEW"


@pytest.mark.parametrize("field,kind", [("auth_user", "basic"), ("auth_header", "header")])
def test_a_key_pasted_as_the_username_or_header_is_refused(client, field, kind):
    body = {"name": "Acme", "base_url": "https://api.example.com", "auth_token": KEY, "auth_kind": kind}
    body["auth_user" if kind == "basic" else "auth_header"] = "someone"
    body[field] = KEY
    res = client.post("/integrations", json=body)
    assert res.status_code == 400, res.text
    assert KEY not in res.text
    platform = client.app.state.platform
    assert platform.integrations.get_spec("acme") is None
    assert platform.secrets.get("integration_acme_token") in (None, "")


def test_a_plain_word_key_pasted_as_the_header_is_refused_by_its_value(client):
    """A key with no tell-tale shape is still the key: the box equals it."""
    plain = "correcthorsebattery42"
    res = client.post("/integrations", json={
        "name": "Eps", "base_url": "https://api.example.com", "auth_token": plain,
        "auth_kind": "header", "auth_header": plain,
    })
    assert res.status_code == 400, res.text
    assert plain not in res.text


def test_a_username_shaped_like_a_secret_is_refused_without_a_key_match(client):
    shaped = "ghp_" + "0123456789abcdefghijklmnopqrstuvwxyzAB"
    res = client.post("/integrations", json={
        "name": "Beta", "base_url": "https://api.example.com", "auth_token": "plainpassword1",
        "auth_kind": "basic", "auth_user": shaped,
    })
    assert res.status_code == 400, res.text
    assert shaped not in res.text


def test_a_plain_username_and_header_still_save(client):
    res = client.post("/integrations", json={
        "name": "Gamma", "base_url": "https://api.example.com", "auth_token": "plainpassword1",
        "auth_kind": "basic", "auth_user": "vr@firm.example",
    })
    assert res.status_code == 200, res.text
    res = client.post("/integrations", json={
        "name": "Delta", "base_url": "https://api.example.com", "auth_token": "plainpassword1",
        "auth_kind": "header", "auth_header": "X-API-Key",
    })
    assert res.status_code == 200, res.text


@pytest.mark.parametrize("share", [r"\\fileserver\clients\repo", "//fileserver/clients/repo", r"\\?\UNC\fs\c"])
def test_a_share_spelled_folder_is_refused_by_its_text(client, monkeypatch, share):
    """The builder's share pin survived removing the spelling check; here the
    newest coding-session row really holds a share spelling, and the folder
    is refused by its text."""
    from iron_jarvis.mcp import import_sources
    from iron_jarvis.projects import coding_sessions

    monkeypatch.setattr(
        coding_sessions, "read_rows",
        lambda engine, scan=None: ([], [{"folder": share, "updated": None, "local_only": False}]),
    )
    looked: list[str] = []
    real_exists = Path.exists

    def spy(self, *a, **k):
        looked.append(str(self))
        return real_exists(self, *a, **k)

    monkeypatch.setattr(Path, "exists", spy)
    assert import_sources.last_working_folder(client.app.state.platform) == ""
    assert import_sources.working_root(client.app.state.platform) == ("", "")
    assert not any(p.startswith(("\\\\", "//")) for p in looked)
