"""v1.414.0 — Share my profile with Pi and OpenCode.

Which file each READS was measured on this PC against the installed program
(no model call — each was pointed at a local stand-in endpoint that recorded
whether a sentinel in the file reached the request):

* Pi 1.3.0: ``<PI_CODING_AGENT_DIR or ~/.pi/agent>/AGENTS.md``; Pi reads the
  FIRST of ``AGENTS.override.md`` / ``AGENTS.md`` / ``CLAUDE.md`` that EXISTS
  (an empty override still wins — its ``core/resource-loader.js``).
* OpenCode 1.18.18: ``<XDG_CONFIG_HOME or ~/.config>/opencode/AGENTS.md``;
  it reads it INSTEAD of ``~/.claude/CLAUDE.md`` (measured), so a missing
  AGENTS.md is never created while that fallback is what OpenCode reads.

The same rules as Claude Code and Codex: off by default, outside the block
byte-identical, drift never overwritten. Real temp homes; the user's own files
are checked untouched.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.profile import share

_REAL_HOME = Path(os.path.expanduser("~"))
_REAL_FILES = [
    _REAL_HOME / ".pi" / "agent" / "AGENTS.md",
    _REAL_HOME / ".pi" / "agent" / "AGENTS.override.md",
    _REAL_HOME / ".config" / "opencode" / "AGENTS.md",
    _REAL_HOME / ".claude" / "CLAUDE.md",
]


def _stat(p: Path):
    try:
        st = p.stat()
        return (st.st_size, st.st_mtime_ns)
    except FileNotFoundError:
        return None


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    before = {p: _stat(p) for p in _REAL_FILES}
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "IRONJARVIS_HOME", "PI_CODING_AGENT_DIR", "XDG_CONFIG_HOME",
              "OPENCODE_DISABLE_CLAUDE_CODE", "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    monkeypatch.setattr(share, "_cli_found", lambda spec: True)
    monkeypatch.setattr(share, "DEBOUNCE_S", 0.05)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    yield
    assert {p: _stat(p) for p in _REAL_FILES} == before, "a test touched the user's real files"


@pytest.fixture
def user(tmp_path) -> Path:
    return tmp_path / "user"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path / "proj"))) as c:
        yield c


def _about(c, text: str) -> None:
    r = c.put("/profile", json={"values": {"about": text}})
    assert r.status_code == 200, r.text


def _on(c, cli: str, on: bool = True) -> dict:
    r = c.put("/profile/share", json={"cli": cli, "on": on})
    assert r.status_code == 200, r.text
    return next(x for x in r.json()["clis"] if x["cli"] == cli)


def _inside(data: bytes) -> str:
    s = data.index(share.START)
    e = data.index(share.END) + len(share.END)
    return data[s:e].decode("utf-8")


def _outside(data: bytes) -> bytes:
    s = data.index(share.START)
    e = data.index(share.END) + len(share.END)
    e += 2 if data[e:e + 2] == b"\r\n" else 1 if data[e:e + 1] == b"\n" else 0
    return data[:s] + data[e:]


def test_both_are_listed_and_off_by_default(client):
    clis = {x["cli"]: x for x in client.get("/profile/share").json()["clis"]}
    assert {"claude-code", "codex", "pi", "opencode"} <= set(clis)
    assert clis["pi"]["on"] is False and clis["opencode"]["on"] is False
    assert clis["pi"]["file_name"] == "AGENTS.md" and clis["opencode"]["file_name"] == "AGENTS.md"
    assert client.app.state.platform.config.profile_share_pi is False
    assert client.app.state.platform.config.profile_share_opencode is False


def test_pi_writes_its_global_agents_md_and_keeps_the_rest_byte_identical(client, user):
    _about(client, "Dana Reyes, CPA.")
    agent = user / ".pi" / "agent"
    agent.mkdir(parents=True)
    mine = b"# My Pi rules\r\nUse tabs.\r\n"
    (agent / "AGENTS.md").write_bytes(mine)
    row = _on(client, "pi")
    assert row["on"] is True
    data = (agent / "AGENTS.md").read_bytes()
    assert "Dana Reyes" in _inside(data)
    assert _outside(data) == mine, "Pi's file outside the block must be byte-identical"
    _on(client, "pi", False)
    assert (agent / "AGENTS.md").read_bytes() == mine


def test_pi_reads_an_empty_override_first_so_the_block_goes_there(client, user):
    _about(client, "Dana Reyes, CPA.")
    agent = user / ".pi" / "agent"
    agent.mkdir(parents=True)
    (agent / "AGENTS.override.md").write_bytes(b"")
    (agent / "AGENTS.md").write_bytes(b"other rules\n")
    _on(client, "pi")
    assert "Dana Reyes" in _inside((agent / "AGENTS.override.md").read_bytes())
    assert (agent / "AGENTS.md").read_bytes() == b"other rules\n"


def test_pi_respects_its_config_dir_variable(client, tmp_path, monkeypatch):
    _about(client, "Dana Reyes, CPA.")
    custom = tmp_path / "pi-home"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(custom))
    _on(client, "pi")
    assert "Dana Reyes" in _inside((custom / "AGENTS.md").read_bytes())


def test_opencode_writes_its_xdg_agents_md(client, user):
    _about(client, "Dana Reyes, CPA.")
    row = _on(client, "opencode")
    path = user / ".config" / "opencode" / "AGENTS.md"
    assert "Dana Reyes" in _inside(path.read_bytes())
    assert row["files"][0]["path"] == str(path) and row["files"][0]["error"] is None
    _on(client, "opencode", False)
    assert not path.exists(), "a file we created that holds nothing else is removed"


def test_opencode_respects_xdg_config_home(client, tmp_path, monkeypatch):
    _about(client, "Dana Reyes, CPA.")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _on(client, "opencode")
    assert "Dana Reyes" in _inside((tmp_path / "xdg" / "opencode" / "AGENTS.md").read_bytes())


def test_opencode_never_creates_agents_md_while_it_reads_the_claude_file(client, user, monkeypatch):
    """OpenCode reads ~/.claude/CLAUDE.md while it has no AGENTS.md; creating
    one would silently stop it reading that file."""
    _about(client, "Dana Reyes, CPA.")
    (user / ".claude").mkdir()
    (user / ".claude" / "CLAUDE.md").write_bytes(b"my claude rules\n")
    row = _on(client, "opencode")
    path = user / ".config" / "opencode" / "AGENTS.md"
    assert not path.exists()
    assert row["files"][0]["error"] == share.OPENCODE_FALLBACK_ERROR
    assert (user / ".claude" / "CLAUDE.md").read_bytes() == b"my claude rules\n"
    # OpenCode's own switch turns that fallback off: then the file is made.
    monkeypatch.setenv("OPENCODE_DISABLE_CLAUDE_CODE_PROMPT", "1")
    client.app.state.platform.profile_share.sync("opencode")
    assert "Dana Reyes" in _inside(path.read_bytes())


def test_opencode_writes_an_agents_md_that_exists_whatever_the_fallback(client, user):
    _about(client, "Dana Reyes, CPA.")
    (user / ".claude").mkdir()
    (user / ".claude" / "CLAUDE.md").write_bytes(b"my claude rules\n")
    path = user / ".config" / "opencode" / "AGENTS.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"opencode rules\n")
    _on(client, "opencode")
    data = path.read_bytes()
    assert "Dana Reyes" in _inside(data) and _outside(data) == b"opencode rules\n"


def test_the_settings_writer_knows_every_share_switch():
    from iron_jarvis.settings import writer

    assert set(writer._SHARE_KEYS.values()) == set(share.CLIS)
    assert {share.CLIS[c].config_key for c in share.CLIS} == set(writer._SHARE_KEYS)
