"""v1.351.0 (G12) — a pack's secret INSIDE an argument or a URL is masked on
every answer, kept out of config.toml, and still reaches the launched pack.

The v1.343.0 Known line: "an ``mcp-remote`` entry INSIDE a pasted block stays
the stdio pack it spells, so a ``--header`` secret in its args is stored plain
in config.toml and shown raw in the row's ``args``". The same held for
``--api-key=…``, ``--api-key …``, a Directory Postgres connection string and a
``?api_key=`` url, and the env-key rule missed ``GITHUB_PAT``,
``DATABASE_URL`` and ``*_DSN``.

ACCEPTANCE (as a new user would check it): paste an mcp-remote block with an
Authorization header → the Tools row shows the header masked, config.toml
holds no token, and the pack still launches with the real value. The launch
is proven against a REAL child (``fixtures/argv_mcp_server_v13510.py``
reports the argv it was started with), through the real app factory.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.config import persist_config_values
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.secrets import SECRET_KEY_RX, migrate_mcp_env_secrets
from iron_jarvis.mcp.tools import _build_transport, close_tools_clients, mcp_tools

FIXTURE = str(Path(__file__).parent / "fixtures" / "argv_mcp_server_v13510.py")
NO_SUCH = "ij-no-such-mcp-launcher-v13510"
#: A token with no known SHAPE (no ghp_/sk- prefix): only the Authorization
#: rule can see it, so a pass cannot come from the token-shape rule.
TOKEN = "tok-LANE1E-0123456789-NEVERSHOWN"
API_KEY = "plainKEYvalue98765"
DB_PASSWORD = "dbPassw0rd-LANE1E"
URL_KEY = "urlKEY-LANE1E-4242"


def _toml_text(platform) -> str:
    return (Path(platform.config.home) / "config.toml").read_text(encoding="utf-8")


def _entry(platform, name: str) -> dict:
    for s in platform.config.mcp_servers:
        if s.get("name") == name:
            return s
    raise AssertionError(f"no config entry named {name!r}")


def _row(client, name: str) -> dict:
    res = client.get("/mcp/servers")
    assert res.status_code == 200, res.text
    return next(s for s in res.json()["servers"] if s["name"] == name)


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


# --------------------------------------------------------------------------- #
# THE ACCEPTANCE: a pasted mcp-remote block with an Authorization header.
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(120)
def test_a_pasted_mcp_remote_block_is_masked_kept_out_of_config_and_launches_with_the_real_header(client):
    platform = client.app.state.platform
    header = f"Authorization: Bearer {TOKEN}"
    paste = json.dumps(
        {
            "mcpServers": {
                "remote": {
                    "command": sys.executable,
                    "args": [FIXTURE, "-y", "mcp-remote", "https://remote.example/mcp", "--header", header],
                }
            }
        }
    )
    res = client.post("/mcp/import/preview", json={"text": paste})
    assert res.status_code == 200 and TOKEN not in res.text, res.text

    res = client.post(
        "/mcp/import/apply",
        json={"text": paste, "names": ["remote"], "overrides": {}, "auto_approve": False, "replace": False},
    )
    assert res.status_code == 200, res.text
    assert TOKEN not in res.text
    (added,) = res.json()["added"]
    assert added["name"] == "remote" and added["tools_loaded"] == 1, res.text

    # 1. The Tools row shows the header MASKED (and the rest of the line).
    row = _row(client, "remote")
    assert row["args"][-1] == "Authorization: Bearer ***", row["args"]
    assert row["args"][:-1] == [FIXTURE, "-y", "mcp-remote", "https://remote.example/mcp", "--header"]
    assert "arg_secrets" not in row, "vault names never ride the row"
    assert TOKEN not in client.get("/mcp/servers").text

    # 2. config.toml holds no token; the vault holds the real argument.
    text = _toml_text(platform)
    assert TOKEN not in text
    stored = next(s for s in tomllib.loads(text)["mcp_servers"] if s["name"] == "remote")
    assert stored["args"][-1] == "Authorization: Bearer ***"
    assert stored["arg_secrets"] == {"5": "mcp.remote.arg.5"}
    assert platform.secrets.get("mcp.remote.arg.5") == header

    # 3. The pack LAUNCHED with the real value (the live load), and launches
    #    with it again from the stored row (the boot path: mcp_tools over
    #    config.mcp_servers with the vault as resolver).
    live = platform.registry.get("mcp__remote__argv")
    out = asyncio.run(live.execute({}, None))
    assert out.ok, out.error
    assert header in out.output, out.output
    tools = mcp_tools([_entry(platform, "remote")], secret_resolver=platform.secrets.get)
    try:
        again = asyncio.run(tools[0].execute({}, None))
        assert again.ok and header in again.output, again.output
    finally:
        close_tools_clients(tools)

    # 4. Removing the pack removes its argument's vault row.
    assert client.delete("/mcp/servers/remote").status_code == 200
    assert platform.secrets.get("mcp.remote.arg.5") is None


# --------------------------------------------------------------------------- #
# The other shapes: --api-key=…, --api-key …, a connection string, env names.
# --------------------------------------------------------------------------- #
def test_api_key_flags_a_connection_string_and_the_missed_env_names_are_vaulted(client):
    platform = client.app.state.platform
    res = client.post(
        "/mcp/servers",
        json={
            "name": "db",
            "command": NO_SUCH,
            "args": ["--api-key", API_KEY, f"--token={API_KEY}x", f"postgresql://me:{DB_PASSWORD}@db:5432/x", "--keyword", "search"],
            "env": {
                "GITHUB_PAT": "pat-value-LANE1E",
                "DATABASE_URL": f"postgres://u:{DB_PASSWORD}@h/d",
                "SENTRY_DSN": "https://dsn-value-LANE1E@sentry.example/1",
                "PATH": "C:/bin",
                "REGION": "us",
            },
        },
    )
    assert res.status_code == 200, res.text
    body = res.text
    for value in (API_KEY, DB_PASSWORD, "pat-value-LANE1E", "dsn-value-LANE1E"):
        assert value not in body, value
    entry = _entry(platform, "db")
    assert entry["args"] == ["--api-key", "***", "--token=***", "postgresql://me:***@db:5432/x", "--keyword", "search"]
    assert sorted(entry["arg_secrets"]) == ["1", "2", "3"]
    assert platform.secrets.get("mcp.db.arg.1") == API_KEY
    assert platform.secrets.get("mcp.db.arg.3") == f"postgresql://me:{DB_PASSWORD}@db:5432/x"
    assert entry["env"] == {"PATH": "C:/bin", "REGION": "us"}, "PATH is not a PAT"
    assert sorted(entry["env_secrets"]) == ["DATABASE_URL", "GITHUB_PAT", "SENTRY_DSN"]
    text = _toml_text(platform)
    for value in (API_KEY, DB_PASSWORD, "pat-value-LANE1E", "dsn-value-LANE1E"):
        assert value not in text, value
    row = _row(client, "db")
    assert row["args"] == entry["args"]
    assert row["secret_keys"] == ["DATABASE_URL", "GITHUB_PAT", "SENTRY_DSN"]
    assert row["env_keys"] == ["PATH", "REGION"]
    # The launch puts every real value back (resolve_launcher is the only
    # thing that would fail here: the command does not exist).
    real = _build_transport(dict(entry, command=sys.executable), platform.secrets.get)
    assert real.args == ["--api-key", API_KEY, f"--token={API_KEY}x", f"postgresql://me:{DB_PASSWORD}@db:5432/x", "--keyword", "search"]


@pytest.mark.parametrize("key", ["GITHUB_PAT", "PAT", "pat_token", "DATABASE_URL", "SENTRY_DSN", "DB_CONNECTION_STRING"])
def test_the_key_rule_now_names_the_secrets_it_missed(key):
    assert SECRET_KEY_RX.search(key), key


@pytest.mark.parametrize("key", ["PATH", "PYTHONPATH", "PATTERN", "COMPAT_MODE", "PORT", "NODE_ENV", "LOG_LEVEL"])
def test_the_key_rule_still_leaves_plain_settings_alone(key):
    assert not SECRET_KEY_RX.search(key), key


def test_an_http_pack_url_secret_is_vaulted_and_put_back_at_connect(client):
    platform = client.app.state.platform
    real_url = f"http://127.0.0.1:{_closed_port()}/mcp?api_key={URL_KEY}&team=a"
    res = client.post("/mcp/servers", json={"name": "hosted", "transport": "http", "url": real_url})
    assert res.status_code == 200, res.text
    assert URL_KEY not in res.text
    entry = _entry(platform, "hosted")
    assert entry["url"].endswith("/mcp?api_key=***&team=a")
    assert entry["url_secret"] == "mcp.hosted.url"
    assert platform.secrets.get("mcp.hosted.url") == real_url
    assert URL_KEY not in _toml_text(platform)
    row = _row(client, "hosted")
    assert row["url"] == entry["url"] and "url_secret" not in row
    assert _build_transport(entry, platform.secrets.get).url == real_url
    # A vault miss keeps the masked url (fails honestly, never guesses).
    assert _build_transport(entry, lambda _n: None).url == entry["url"]


# --------------------------------------------------------------------------- #
# A row from before the migration is masked anyway; the migration moves it.
# --------------------------------------------------------------------------- #
def _plant(platform, servers):
    platform.config.mcp_servers = servers
    persist_config_values(platform.config.home, {"mcp_servers": servers})


def test_a_row_stored_before_the_migration_is_shown_masked(client):
    platform = client.app.state.platform
    _plant(platform, [{"name": "old", "command": NO_SUCH, "args": ["--header", f"Authorization: Bearer {TOKEN}"]}])
    res = client.get("/mcp/servers")
    assert res.status_code == 200 and TOKEN not in res.text, res.text
    assert _row(client, "old")["args"] == ["--header", "Authorization: Bearer ***"]


def test_the_boot_migration_moves_argument_and_url_secrets_once_and_logs_names_only(client, caplog):
    platform = client.app.state.platform
    url = f"https://hosted.example/mcp?api_key={URL_KEY}"
    _plant(
        platform,
        [
            {"name": "old", "command": "npx", "args": ["-y", "mcp-remote", "https://x/mcp", "--header", f"Authorization: Bearer {TOKEN}"], "env": {"GITHUB_PAT": "pat-LANE1E"}},
            {"name": "web", "transport": "http", "url": url},
            {"name": "plain", "command": "npx", "args": ["-y", "pkg"]},
        ],
    )
    with caplog.at_level(logging.INFO, logger="iron_jarvis"):
        first = migrate_mcp_env_secrets(platform)
    assert first == {"moved": ["mcp.old.GITHUB_PAT", "mcp.old.arg.4", "mcp.web.url"], "skipped": []}
    old, web, plain = platform.config.mcp_servers
    assert old["args"][-1] == "Authorization: Bearer ***" and old["arg_secrets"] == {"4": "mcp.old.arg.4"}
    assert old["env_secrets"] == {"GITHUB_PAT": "mcp.old.GITHUB_PAT"} and old["env"] == {}
    assert web["url"] == "https://hosted.example/mcp?api_key=***" and web["url_secret"] == "mcp.web.url"
    assert plain == {"name": "plain", "command": "npx", "args": ["-y", "pkg"]}, "a clean entry is untouched"
    assert platform.secrets.get("mcp.old.arg.4") == f"Authorization: Bearer {TOKEN}"
    assert platform.secrets.get("mcp.web.url") == url
    text = _toml_text(platform)
    assert TOKEN not in text and URL_KEY not in text and "pat-LANE1E" not in text
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "mcp.old.arg.4" in joined
    assert TOKEN not in joined and URL_KEY not in joined

    # Idempotent: nothing moves twice and the file is not rewritten.
    before = json.dumps(platform.config.mcp_servers, sort_keys=True)
    mtime = (Path(platform.config.home) / "config.toml").stat().st_mtime_ns
    assert migrate_mcp_env_secrets(platform) == {"moved": [], "skipped": []}
    assert json.dumps(platform.config.mcp_servers, sort_keys=True) == before
    assert (Path(platform.config.home) / "config.toml").stat().st_mtime_ns == mtime


def test_a_refused_vault_leaves_the_whole_entry_as_it_was(client, monkeypatch):
    platform = client.app.state.platform
    entry = {"name": "old", "command": "npx", "args": ["--header", f"Authorization: Bearer {TOKEN}"]}
    _plant(platform, [dict(entry)])

    def _refuse(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(platform.secrets, "set", _refuse)
    out = migrate_mcp_env_secrets(platform)
    assert out == {"moved": [], "skipped": ["old.arg.1: OSError"]}
    assert platform.config.mcp_servers == [entry], "untouched means untouched, not half-moved"


# --------------------------------------------------------------------------- #
# An EDIT that sends the masked text back keeps the real value.
# --------------------------------------------------------------------------- #
def _add_remote(client) -> None:
    res = client.post(
        "/mcp/servers",
        json={"name": "remote", "command": NO_SUCH, "args": ["-y", "mcp-remote", "https://x/mcp", "--header", f"Authorization: Bearer {TOKEN}"]},
    )
    assert res.status_code == 200, res.text


def test_an_edit_that_sends_the_shown_args_back_changes_nothing_about_them(client):
    platform = client.app.state.platform
    _add_remote(client)
    before = dict(_entry(platform, "remote"))
    shown = _row(client, "remote")["args"]
    # The editor's one-line form splits on whitespace before sending it back.
    split = " ".join(shown).split()
    for args in (shown, split):
        res = client.patch("/mcp/servers/remote", json={"env": {"PORT": "1"}, "args": args})
        assert res.status_code == 200, res.text
        assert TOKEN not in res.text
        entry = _entry(platform, "remote")
        assert entry["args"] == before["args"] and entry["arg_secrets"] == before["arg_secrets"]
        assert platform.secrets.get("mcp.remote.arg.4") == f"Authorization: Bearer {TOKEN}"
    assert TOKEN not in _toml_text(platform)


def test_an_edit_that_changes_other_args_puts_the_real_value_back_and_vaults_it(client):
    platform = client.app.state.platform
    _add_remote(client)
    shown = _row(client, "remote")["args"]
    res = client.patch("/mcp/servers/remote", json={"args": ["--debug", *shown]})
    assert res.status_code == 200, res.text
    entry = _entry(platform, "remote")
    assert entry["args"] == ["--debug", "-y", "mcp-remote", "https://x/mcp", "--header", "Authorization: Bearer ***"]
    assert entry["arg_secrets"] == {"5": "mcp.remote.arg.5"}
    assert platform.secrets.get("mcp.remote.arg.5") == f"Authorization: Bearer {TOKEN}"
    assert platform.secrets.get("mcp.remote.arg.4") is None, "the moved argument's old row is gone"
    # Split on whitespace AND changed: the token put back for `***` is still a
    # secret even though, alone, it no longer looks like one.
    split = ["--verbose", *" ".join(_row(client, "remote")["args"]).split()]
    res = client.patch("/mcp/servers/remote", json={"args": split})
    assert res.status_code == 200, res.text
    entry = _entry(platform, "remote")
    assert TOKEN not in json.dumps(entry) and TOKEN not in _toml_text(platform)
    i = entry["args"].index("***")
    assert platform.secrets.get(entry["arg_secrets"][str(i)]) == TOKEN


def test_an_edit_with_a_stray_mask_is_refused_and_changes_nothing(client):
    platform = client.app.state.platform
    _add_remote(client)
    before = json.dumps(_entry(platform, "remote"), sort_keys=True)
    res = client.patch("/mcp/servers/remote", json={"args": ["--header", "X-Other: ***"]})
    assert res.status_code == 422, res.text
    assert "***" in res.json()["detail"] and "argument 2" in res.json()["detail"]
    assert json.dumps(_entry(platform, "remote"), sort_keys=True) == before
    res = client.patch("/mcp/servers/remote", json={"args": ["-y", "pkg"]})
    assert res.status_code == 200, res.text
    assert "arg_secrets" not in _entry(platform, "remote")
    assert platform.secrets.get("mcp.remote.arg.4") is None, "a removed secret argument leaves the vault"


# --------------------------------------------------------------------------- #
# The Directory's Postgres connection string, connected from chat.
# --------------------------------------------------------------------------- #
def test_app_connect_keeps_a_connection_string_password_in_the_vault(client, monkeypatch):
    from iron_jarvis.settings import records

    platform = client.app.state.platform
    launched: list[dict] = []
    monkeypatch.setattr(records, "load_app", lambda p, cfg: launched.append(cfg) or 0)
    tool = records.AppConnectTool(platform)
    conn = f"postgresql://me:{DB_PASSWORD}@db.example:5432/books"
    out = asyncio.run(tool.execute({"app": "postgres", "values": {"connection": conn}}, None))
    assert out.ok, out.error
    entry = _entry(platform, "postgres")
    assert entry["args"][-1] == "postgresql://me:***@db.example:5432/books"
    assert entry["arg_secrets"] == {"2": "mcp.postgres.arg.2"}
    assert platform.secrets.get("mcp.postgres.arg.2") == conn
    assert DB_PASSWORD not in _toml_text(platform)
    assert DB_PASSWORD not in json.dumps(out.data, default=str) and DB_PASSWORD not in out.output
    # What the pack launches with is the real string.
    assert launched and _build_transport(dict(launched[0], command=sys.executable), platform.secrets.get).args[-1] == conn


# --------------------------------------------------------------------------- #
# Review (v1.351.0): two hidden values that SHOW the same text.
# --------------------------------------------------------------------------- #
def test_an_edit_keeps_each_of_two_hidden_values_that_show_the_same_text(client):
    """``--api-key *** --token ***`` (and a bearer header beside them): the
    first cut kept ONE value per shown text, so adding any argument stored the
    token as the API key too — the pack then failed at launch with a key it
    was never given. Each hidden value goes back to its own argument, also
    when the edit moves or removes the ones around it."""
    platform = client.app.state.platform
    first, second = "AAA-first-LANE1E-review", "BBB-second-LANE1E-review"
    header = f"Authorization: Bearer {TOKEN}"
    res = client.post(
        "/mcp/servers",
        json={"name": "two", "command": NO_SUCH, "args": ["--api-key", first, "--token", second, "--header", header]},
    )
    assert res.status_code == 200, res.text

    def real_args() -> list[str]:
        entry = _entry(platform, "two")
        return _build_transport(dict(entry, command=sys.executable), platform.secrets.get).args

    # Add an argument (the editor sends the shown line back plus the change).
    shown = _row(client, "two")["args"]
    assert shown == ["--api-key", "***", "--token", "***", "--header", "Authorization: Bearer ***"]
    res = client.patch("/mcp/servers/two", json={"args": [*shown, "--verbose"]})
    assert res.status_code == 200, res.text
    assert real_args() == ["--api-key", first, "--token", second, "--header", header, "--verbose"]

    # The same, split on whitespace by the one-line editor.
    split = ["--debug", *" ".join(_row(client, "two")["args"]).split()]
    res = client.patch("/mcp/servers/two", json={"args": split})
    assert res.status_code == 200, res.text
    got = real_args()
    assert got[:5] == ["--debug", "--api-key", first, "--token", second], got
    assert TOKEN in got and first not in got[5:] and second not in got[5:], got

    # Remove the FIRST pair: the token stays the token.
    res = client.patch("/mcp/servers/two", json={"args": ["--token", "***"]})
    assert res.status_code == 200, res.text
    assert real_args() == ["--token", second]
    text = _toml_text(platform)
    for value in (first, second, TOKEN):
        assert value not in text, value


def test_a_split_header_before_a_bare_hidden_key_keeps_each_value_in_its_place(client):
    """Fix round 2: the first review's fix still took ANY whole ``***``
    argument ahead of the token a split header was cut from. With the header
    FIRST (``--header "Authorization: Bearer ***" --api-key ***``), the
    one-line editor's split line put the API key into the header and the
    header's token into ``--api-key`` — both still vaulted, both wrong, and
    the pack then launched with neither credential where it belongs."""
    platform = client.app.state.platform
    header = f"Authorization: Bearer {TOKEN}"
    res = client.post(
        "/mcp/servers",
        json={"name": "hdr", "command": NO_SUCH, "args": ["--header", header, "--api-key", API_KEY]},
    )
    assert res.status_code == 200, res.text

    def real_args() -> list[str]:
        entry = _entry(platform, "hdr")
        return _build_transport(dict(entry, command=sys.executable), platform.secrets.get).args

    shown = _row(client, "hdr")["args"]
    assert shown == ["--header", "Authorization: Bearer ***", "--api-key", "***"]
    # The one-line editor: the shown line split on whitespace, plus a change.
    res = client.patch("/mcp/servers/hdr", json={"args": [*" ".join(shown).split(), "--verbose"]})
    assert res.status_code == 200, res.text
    assert real_args() == ["--header", "Authorization:", "Bearer", TOKEN, "--api-key", API_KEY, "--verbose"]

    # Moved around (key pair first, header split): each value follows the
    # word it came after.
    res = client.patch(
        "/mcp/servers/hdr", json={"args": ["--api-key", "***", "--header", "Authorization:", "Bearer", "***"]}
    )
    assert res.status_code == 200, res.text
    assert real_args() == ["--api-key", API_KEY, "--header", "Authorization:", "Bearer", TOKEN]
    text = _toml_text(platform)
    assert TOKEN not in text and API_KEY not in text
