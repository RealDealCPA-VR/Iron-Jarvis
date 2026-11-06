"""v1.441.0 (lane g4) — Share my profile with Grok.

Which file Grok reads was MEASURED on grok 1.0.70 with ``grok inspect
--json`` (no sign-in, no model call) on a scratch ``GROK_HOME`` and a
scratch ``USERPROFILE``: every ``Agents.md`` / ``Claude.md`` / ``AGENT.md``
(and ``AGENTS.md``, the same file on Windows) in the Grok home is listed as a
"global" instruction file, and so is every ``rules/*.md`` there. Iron Jarvis's
block goes into ``<GROK_HOME or ~/.grok>/AGENTS.md``. The v1.306.0 rules hold:
off by default, the outside of the block byte-identical, a hand edit never
overwritten, a linked file left alone; the user's real ``~/.grok`` untouched.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.profile import share
from iron_jarvis.settings import schema as SCH
from iron_jarvis.settings import writer as WR

_REAL = Path(os.path.expanduser("~")) / ".grok" / "AGENTS.md"


def _stat(p: Path):
    try:
        st = p.stat()
        return (st.st_size, st.st_mtime_ns)
    except FileNotFoundError:
        return None


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    before = _stat(_REAL)
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "IRONJARVIS_HOME", "PI_CODING_AGENT_DIR", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("GROK_HOME", raising=False)
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    monkeypatch.setattr(share, "_cli_found", lambda spec: True)
    monkeypatch.setattr(share, "DEBOUNCE_S", 0.05)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    yield
    assert _stat(_REAL) == before, "a test touched the user's real Grok file"


@pytest.fixture
def user(tmp_path) -> Path:
    return tmp_path / "user"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "proj"))) as c:
        yield c


def _about(c, text: str) -> None:
    assert c.put("/profile", json={"values": {"about": text}}).status_code == 200


def _on(c, on: bool = True) -> dict:
    r = c.put("/profile/share", json={"cli": "grok", "on": on})
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["clis"] if x["cli"] == "grok")


def _outside(data: bytes) -> bytes:
    s = data.index(share.START)
    e = data.index(share.END) + len(share.END)
    e += 2 if data[e:e + 2] == b"\r\n" else 1 if data[e:e + 1] == b"\n" else 0
    return data[:s] + data[e:]


def test_grok_is_listed_off_by_default_with_its_setting_row():
    spec = share.CLIS["grok"]
    assert (spec.home_var, spec.default_dir, spec.files, spec.proxy_provider) == ("GROK_HOME", ".grok",
                                                                                    ("AGENTS.md",), "xai")
    row = next(r for r in SCH.SETTINGS if r.key == "profile_share_grok")
    assert row.put is False and row.tier == "ask"
    assert WR._SHARE_KEYS["profile_share_grok"] == "grok" and "profile_share_grok" in WR._HOOK_KEYS


def test_the_view_lists_grok(client):
    clis = {x["cli"]: x for x in client.get("/profile/share").json()["clis"]}
    assert clis["grok"]["on"] is False and clis["grok"]["file_name"] == "AGENTS.md"
    assert clis["grok"]["label"] == "Grok" and clis["grok"]["vendor"] == "xAI"
    assert client.app.state.platform.config.profile_share_grok is False


def test_grok_writes_its_home_agents_md_and_keeps_the_rest_byte_identical(client, user):
    _about(client, "Dana Reyes, CPA.")
    home = user / ".grok"
    home.mkdir()
    mine = b"# My Grok rules\r\nNo emoji.\r\n"
    (home / "AGENTS.md").write_bytes(mine)
    row = _on(client)
    data = (home / "AGENTS.md").read_bytes()
    assert b"Dana Reyes, CPA." in data and _outside(data) == mine
    assert row["files"][0]["path"].endswith(os.path.join(".grok", "AGENTS.md"))
    _on(client, False)
    assert (home / "AGENTS.md").read_bytes() == mine


def test_grok_home_follows_GROK_HOME_and_a_new_file_goes_when_turned_off(client, tmp_path, monkeypatch):
    gh = tmp_path / "grokhome"
    monkeypatch.setenv("GROK_HOME", str(gh))
    _about(client, "Dana Reyes.")
    _on(client)
    assert b"Dana Reyes." in (gh / "AGENTS.md").read_bytes()
    _on(client, False)
    assert not (gh / "AGENTS.md").exists(), "a file Iron Jarvis created and left empty is removed"


def test_a_hand_edit_is_never_overwritten(client, user):
    _about(client, "Dana Reyes.")
    _on(client)
    f = user / ".grok" / "AGENTS.md"
    edited = f.read_bytes().replace(b"Dana Reyes.", b"Dana, in my words.")
    f.write_bytes(edited)
    _about(client, "Somebody else.")
    row = _on(client)
    assert f.read_bytes() == edited
    assert row["files"][0]["drift"] == "edited"


def test_a_linked_file_is_left_alone(client, user, tmp_path):
    _about(client, "Dana Reyes.")
    target = tmp_path / "dotfiles-agents.md"
    target.write_bytes(b"# shared\n")
    home = user / ".grok"
    home.mkdir()
    os.link(target, home / "AGENTS.md")  # a hard link: replacing it would cut it
    row = _on(client)
    assert target.read_bytes() == b"# shared\n"
    assert row["files"][0]["error"]
