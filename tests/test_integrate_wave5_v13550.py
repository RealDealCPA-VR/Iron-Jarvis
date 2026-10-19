"""v1.355.0 integrate — Wave 5 joined ("Finish the receipts and the doors").

Seams between the lanes, proven end to end through the REAL app factory
(``create_app``) and the real router/platform — never a stub of the thing
under test:

(a) G29 x G28 — a failed chat turn carries the SAME headline, fix, technical
    text and press on BOTH lanes (the stream's ``error`` frame and the POST
    lane's 502 body). The page reads both (lib/failureLine.postFailureFields
    on the POST lane since this integrate). The real daemon's payloads are
    written to ``dashboard/__tests__/fixtures/wave5-seams-v13550.json`` and the
    dashboard's ``wave5-seams-v13550`` test feeds them through the REAL
    ``useChatStream`` decode and the page's helpers. Regenerate with
    ``IJ_REGEN_WAVE5_SEAMS=1`` after an intended change.
(b) G44 x wave 3/4 — the Directory's Connect and the Tools catalog's Add (the
    exact body ToolsPage posts for the remote GitHub row) store the SAME pack
    row through the one door; the import door then reads that pack as added.
(c) G32 x G28 — a background job nothing answered: the banner event carries
    failure_words' headline + fix for the very refusal raised, which is the
    sentence the job's own summary ends with (one sentence per cause).

And the wave-4 follow-ups this integrate closes:

(1) an imported value that is wholly a placeholder (``${input:x}``,
    ``${SOME_VAR}``) is ASKED for, never stored as literal text; a partly
    templated header (``Bearer ${TOKEN}``) asks for TOKEN and fills it in;
    ``${CLAUDE_PLUGIN_ROOT}`` is still written in;
(2) the import door is honest: a Google Drive SIGN-IN is not "already
    connected" for a Drive server (it says the sign-in instead), and a pack
    added under another spelling is "already added" only when it runs the
    same server (and says under which name);
(5) a Verify that got an ANSWER with an error says so (``answered``), so the
    save note never says "could not reach" a server that answered;

plus the lanes' integrator notes (5B: the Brave key's new vault name reaches
web search, a chat-card Undo on an older pack restores the token the pack now
reads, a pack whose server changed drops its manifest and an older GitHub pack
says so on its card, the 401 fix names Change key; 5C: a retrying loop keeps
the start of its failing run).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors import catalog as conn_catalog
from iron_jarvis.connectors import service as conn_service
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.import_config import fill_placeholders, mark_placeholders, parse_any
from iron_jarvis.providers import failure_words
from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError
from tests.test_failure_words_v13540 import OPENROUTER_401, _failing_router, _parse_sse
from tests.test_refusal_words_v1329 import LABEL, PROVIDER, _platform_with_dead_node

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "wave5-seams-v13550.json"
NO_SUCH = "ij-no-such-mcp-launcher-v13550"
GH_TOKEN = "ghp_INTEGRATE_v13550_NEVERSHOWN"


@pytest.fixture(autouse=True)
def _fresh_records():
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    yield
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()


@pytest.fixture
def homes(tmp_path, monkeypatch):
    """Temp homes so no source scan ever reads the user's real config files."""
    user = tmp_path / "user"
    appdata = tmp_path / "appdata"
    user.mkdir()
    appdata.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("APPDATA", str(appdata))
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "XDG_CONFIG_HOME", "PIXIO_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return {"user": user, "appdata": appdata}


@pytest.fixture
def client(tmp_path, homes):
    return TestClient(create_app(str(tmp_path / "ij")))


def _entry(platform, name: str) -> dict | None:
    return next((s for s in platform.config.mcp_servers or [] if s.get("name") == name), None)


# =========================================================================== #
# (a) one failure, both lanes, one sentence and one press.
# =========================================================================== #
_WORD_KEYS = ("title", "fix", "technical", "fix_kind", "fix_provider")


def _both_lanes(tmp_path, monkeypatch, exc) -> tuple[dict, dict]:
    client = TestClient(create_app(str(tmp_path)))
    _failing_router(client.app.state.platform, monkeypatch, exc)
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    [frame] = [d for ev, d in _parse_sse(r.text) if ev == "error"]
    post = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert post.status_code == 502, post.text
    return frame, post.json()


def _signed_out_exc():
    from iron_jarvis.providers.adapters.claude_native.transport import signed_out_message

    return RuntimeError(signed_out_message("Not logged in · Please run /login", probe=False))


def _seams(tmp_path, monkeypatch) -> dict:
    """The REAL daemon's words, for the dashboard fixture."""
    out: dict = {"_note": "written by tests/test_integrate_wave5_v13550.py (IJ_REGEN_WAVE5_SEAMS=1)"}
    for key, exc in (
        ("signed_out", _signed_out_exc()),
        ("refused_key", ProviderError(OPENROUTER_401, status_code=401)),
    ):
        frame, body = _both_lanes(tmp_path / key, monkeypatch, exc)
        out[key] = {"stream": frame, "post": body}
    return out


def test_a_failed_turn_says_one_sentence_with_one_press_on_both_lanes(tmp_path, monkeypatch):
    seams = _seams(tmp_path, monkeypatch)
    for key in ("signed_out", "refused_key"):
        frame, body = seams[key]["stream"], seams[key]["post"]
        for k in _WORD_KEYS:
            assert frame.get(k) == body.get(k), (key, k, frame.get(k), body.get(k))
        assert frame["title"] and frame["fix"], key
    assert seams["signed_out"]["stream"]["fix_kind"] == "sign_in"
    assert seams["refused_key"]["stream"]["fix_kind"] == "key"


def test_the_dashboard_fixture_is_the_real_daemons_words(tmp_path, monkeypatch):
    seams = _seams(tmp_path / "chat", monkeypatch)
    seams["banner"] = _banner_seam(tmp_path / "banner")
    text = json.dumps(seams, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("IJ_REGEN_WAVE5_SEAMS") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(text, encoding="utf-8", newline="\n")
    on_disk = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert on_disk == json.loads(text), (
        "the daemon's words changed: regenerate dashboard/__tests__/fixtures/"
        "wave5-seams-v13550.json with IJ_REGEN_WAVE5_SEAMS=1 and check the dashboard test"
    )


# =========================================================================== #
# (c) a background job nothing answered: the banner says the run's sentence.
# =========================================================================== #
def _banner_seam(tmp_path) -> dict:
    import asyncio

    async def _run():
        platform, spy = _platform_with_dead_node(tmp_path)
        try:
            # v1.359.0 (wave 9): a background job's model call happens INSIDE
            # its run (``Orchestrator.run_session`` words every failure there
            # for a run: ``failure_words.run_surface``), so the banner and the
            # job's summary both say the run's fix — moved on purpose.
            with failure_words.run_surface():
                await platform.router.complete(
                    system="",
                    messages=[LLMMessage(role="user", content="Say hello.")],
                    tools=[],
                    session_id="sess_wave5",
                )
        except ProviderError as exc:
            raised = exc
        else:  # pragma: no cover — the dead node must refuse
            raise AssertionError("the dead node answered")
        [event] = spy.downgraded()
        return event, raised, platform

    event, raised, platform = asyncio.run(_run())
    return {
        "event": {"type": "provider.downgraded", "session_id": "sess_wave5", "payload": event},
        "run_summary": "The run failed: " + failure_words.plain_reason(raised, manager=platform.providers),
        "_raised_words": failure_words.friendly_error(
            raised, manager=platform.providers, surface=failure_words.SURFACE_RUN
        ),
    }


def test_the_banner_carries_the_runs_own_sentence_for_the_refusal(tmp_path):
    seam = _banner_seam(tmp_path)
    payload = seam["event"]["payload"]
    words = seam["_raised_words"]
    assert payload["used"] == "none"
    assert (payload["title"], payload["fix"]) == (words["title"], words["fix"])
    assert payload["title"] == f"{LABEL} isn't connected right now"
    # The job's own summary ends with exactly that sentence.
    assert seam["run_summary"] == f"The run failed: {payload['title']}. {payload['fix']}"
    # The v1.329.0 pins stand: the id stays for the logs, the label LAST.
    assert payload["requested"] == PROVIDER and list(payload)[-1] == "label"


# =========================================================================== #
# (b) one door: the Directory and the Tools catalog store the same row.
# =========================================================================== #
def test_the_tools_catalog_and_the_directory_store_one_github_row(tmp_path, homes, monkeypatch):
    # No network: the add door's live load is best-effort and is not asked.
    monkeypatch.setattr(mcp_tools_mod, "mcp_tools", lambda *a, **k: [])
    tools = TestClient(create_app(str(tmp_path / "tools")))
    row = next(r for r in tools.get("/mcp/catalog").json()["catalog"] if r["id"] == "github")
    assert (row["transport"], row["url"], row["command"]) == ("http", conn_catalog.GITHUB_MCP_URL, "")
    # Exactly the body ToolsPage.addMcpServer posts for a remote row.
    body = {
        "name": "github",
        "transport": row["transport"],
        "url": row["url"],
        "auth": {"header": "Authorization", "format": "Bearer {value}", "value": GH_TOKEN},
    }
    assert tools.post("/mcp/servers", json=body).status_code == 200
    via_tools = _entry(tools.app.state.platform, "github")

    directory = TestClient(create_app(str(tmp_path / "dir")))
    r = directory.post(
        "/connectors/github/connect", json={"values": {"GITHUB_PERSONAL_ACCESS_TOKEN": GH_TOKEN}}
    )
    assert r.status_code == 200, r.text
    via_directory = _entry(directory.app.state.platform, "github")
    assert via_tools == via_directory, (via_tools, via_directory)
    assert via_tools["auth"]["secret"] == conn_service._secret_name("github", "GITHUB_PERSONAL_ACCESS_TOKEN")
    for c in (tools, directory):
        assert c.app.state.platform.secrets.get("mcp.github.auth.value") == GH_TOKEN
        assert GH_TOKEN not in (Path(c.app.state.platform.config.home) / "config.toml").read_text(
            encoding="utf-8"
        )
    # The import door (wave 4) reads the Directory's pack as added.
    paste = json.dumps({"mcpServers": {"github": {"type": "http", "url": conn_catalog.GITHUB_MCP_URL}}})
    [prow] = directory.post("/mcp/import/preview", json={"text": paste}).json()["servers"]
    assert (prow["exists"], prow["exists_as"]) == (True, "pack")


# =========================================================================== #
# (1) placeholders are asked for, never imported as text.
# =========================================================================== #
VSCODE_WITH_INPUTS = json.dumps(
    {
        "servers": {
            "gh": {
                "command": NO_SUCH,
                "args": ["--root", "${input:root_dir}"],
                "env": {"GITHUB_TOKEN": "${input:github_token}", "MODE": "${MODE:-fast}"},
            },
            "remote": {"type": "http", "url": "https://mcp.example.test/mcp", "headers": {"Authorization": "Bearer ${TOKEN}"}},
        },
        "inputs": [{"id": "github_token", "type": "promptString", "password": True}],
    }
)


def test_a_wholly_placeholder_value_is_asked_for_never_stored(client):
    [gh, remote] = sorted(
        client.post("/mcp/import/preview", json={"text": VSCODE_WITH_INPUTS}).json()["servers"],
        key=lambda r: r["name"],
    )
    assert gh["asks"] == ["GITHUB_TOKEN", "root_dir"]
    assert remote["asks"] == ["TOKEN"]
    res = client.post(
        "/mcp/import/apply",
        json={
            "text": VSCODE_WITH_INPUTS,
            "names": ["gh", "remote"],
            "overrides": {
                "gh": {"GITHUB_TOKEN": "ghp_TYPED_v13550", "root_dir": "C:/work"},
                "remote": {"TOKEN": "tok_TYPED_v13550"},
            },
        },
    ).json()
    assert {a["name"] for a in res["added"]} == {"gh", "remote"}, res
    p = client.app.state.platform
    gh_row = _entry(p, "gh")
    assert gh_row["args"] == ["--root", "C:/work"]
    assert gh_row["env"] == {"MODE": "fast"}  # a ${X:-default} is its default
    assert p.secrets.get(gh_row["env_secrets"]["GITHUB_TOKEN"]) == "ghp_TYPED_v13550"
    remote_row = _entry(p, "remote")
    vault = (remote_row.get("header_secrets") or {}).get("Authorization")
    assert vault and p.secrets.get(vault) == "Bearer tok_TYPED_v13550", remote_row
    text = (Path(p.config.home) / "config.toml").read_text(encoding="utf-8")
    assert "${input:" not in text and "${TOKEN}" not in text
    for name in (gh_row["env_secrets"]["GITHUB_TOKEN"], vault):
        assert "${" not in (p.secrets.get(name) or "")


def test_an_untyped_argument_placeholder_is_a_skip_that_names_it(client):
    res = client.post(
        "/mcp/import/apply", json={"text": VSCODE_WITH_INPUTS, "names": ["gh"], "overrides": {}}
    ).json()
    assert res["added"] == []
    assert res["skipped"] == [{"name": "gh", "why": "type a value for root_dir first"}]
    assert _entry(client.app.state.platform, "gh") is None


def test_an_untyped_env_or_header_placeholder_is_dropped_never_sent_as_text():
    [spec] = parse_any(json.dumps({"mcpServers": {"s": {"command": NO_SUCH, "env": {"API_KEY": "${input:k}"}}}}))
    assert fill_placeholders(spec, {}) == []
    assert "API_KEY" not in spec.env


def test_the_plugin_root_is_still_written_in(tmp_path):
    from iron_jarvis.mcp.import_sources import _with_plugin_root

    [spec] = parse_any(
        json.dumps(
            {
                "mcpServers": {
                    "p": {
                        "command": "node",
                        "args": [
                            "${CLAUDE_PLUGIN_ROOT}/server.js",
                            "--token=${PLUGIN_TOKEN}",
                            "${CLAUDE_PLUGIN_ROOT}/bin/${PLATFORM}",
                        ],
                        "env": {"DATA": "${CLAUDE_PLUGIN_ROOT}/data"},
                    }
                }
            }
        )
    )
    assert spec.asks == ["PLUGIN_TOKEN", "PLATFORM"]  # the plugin root is never a question
    spec = _with_plugin_root(spec, tmp_path / "plug")
    folder = str(tmp_path / "plug")
    assert spec.args[0] == f"{folder}/server.js"
    assert spec.env["DATA"] == f"{folder}/data"
    assert fill_placeholders(spec, {"PLUGIN_TOKEN": "t1", "PLATFORM": "win"}) == []
    assert spec.args[1] == "--token=t1"
    # A template that holds the plugin root AND a question gets both.
    assert spec.args[2] == f"{folder}/bin/win"


def test_a_spec_without_placeholders_keeps_its_preview_shape():
    [spec] = parse_any(json.dumps({"mcpServers": {"plain": {"command": NO_SUCH, "env": {"A": "b"}}}}))
    assert spec.fills == [] and "asks" not in spec.preview_row()
    assert mark_placeholders(spec).env == {"A": "b"}


# =========================================================================== #
# (2) the import door is honest about sign-ins and spellings.
# =========================================================================== #
def test_a_drive_sign_in_is_not_already_connected_for_a_drive_server(client, monkeypatch):
    real = conn_service.list_connectors

    def _with_drive(platform):
        rows = [r for r in real(platform) if r.get("id") != "google_drive"]
        rows.append({"id": "google_drive", "name": "Google Drive", "connect_via": "oauth", "connected": True})
        return rows

    monkeypatch.setattr(conn_service, "list_connectors", _with_drive)
    paste = json.dumps({"mcpServers": {"google-drive": {"command": NO_SUCH}}})
    [row] = client.post("/mcp/import/preview", json={"text": paste}).json()["servers"]
    assert (row["exists"], row["exists_as"]) == (False, "")
    assert row["signed_in_app"] == "Google Drive"


def test_a_spelling_is_already_added_only_when_it_runs_the_same_server(client):
    add = {"name": "brave_search", "command": NO_SUCH, "args": ["-y", "@brave/brave-search-mcp-server"]}
    assert client.post("/mcp/servers", json=add).status_code == 200
    same = json.dumps({"mcpServers": {"brave-search": {"command": NO_SUCH, "args": ["-y", "@brave/brave-search-mcp-server@2"]}}})
    [row] = client.post("/mcp/import/preview", json={"text": same}).json()["servers"]
    assert (row["exists"], row["exists_as"], row["exists_name"]) == (True, "pack", "brave_search")
    other = json.dumps({"mcpServers": {"brave-search": {"command": NO_SUCH, "args": ["-y", "@someone/other-search"]}}})
    [row] = client.post("/mcp/import/preview", json={"text": other}).json()["servers"]
    assert (row["exists"], row["exists_as"]) == (False, "")
    assert "exists_name" not in row
    # The exact name stays "already added", with no other name to say.
    exact = json.dumps({"mcpServers": {"brave_search": {"command": NO_SUCH, "args": ["-y", "@someone/other-search"]}}})
    [row] = client.post("/mcp/import/preview", json={"text": exact}).json()["servers"]
    assert (row["exists"], row["exists_as"]) == (True, "pack") and "exists_name" not in row


# =========================================================================== #
# (5) a Verify that got an answer never reads as "could not reach".
# =========================================================================== #
class _Refusing:
    provider = "fleet-x"

    def __init__(self, exc):
        self._exc = exc

    async def complete(self, **kw):
        raise self._exc


@pytest.mark.parametrize(
    ("exc", "answered"),
    [
        (ProviderError("fleet API error 404: model 'm' not found", status_code=404), True),
        (ProviderError("fleet API error 401: bad key", status_code=401), True),
        (ConnectionError("connect failed"), False),
    ],
    ids=["404-answered", "401-answered", "never-reached"],
)
def test_verify_says_whether_the_server_answered(tmp_path, monkeypatch, exc, answered):
    import httpx

    if not answered:
        exc = httpx.ConnectError("connect failed")
    client = TestClient(create_app(str(tmp_path)))
    node = client.post(
        "/fleet/nodes",
        json={"base_url": "http://127.0.0.1:9/v1", "label": "box", "routable": True, "default_model": "m"},
    ).json()["node"]
    monkeypatch.setattr(client.app.state.platform.providers, "get", lambda *a, **k: _Refusing(exc))
    v = client.post(f"/fleet/nodes/{node['id']}/verify", json={"model": "m"}).json()
    assert v["tool_use"] is None and v["error"]
    assert v["answered"] is answered


# =========================================================================== #
# Lane notes.
# =========================================================================== #
def test_a_brave_key_saved_from_the_directory_reaches_web_search():
    from iron_jarvis.tools import websearch

    assert conn_service._secret_name("brave_search", "BRAVE_API_KEY") in websearch._BRAVE_SECRET_NAMES
    # The pre-v1.355.0 card name and a Tools/import pack's name still count.
    assert "conn_brave_search_brave_api_key" in websearch._BRAVE_SECRET_NAMES
    assert "mcp.brave-search.BRAVE_API_KEY" in websearch._BRAVE_SECRET_NAMES


def test_undoing_a_chat_card_token_on_an_older_pack_puts_its_old_token_back(tmp_path, monkeypatch):
    from iron_jarvis.settings import records

    monkeypatch.setattr(records, "load_app", lambda p, cfg: 0)
    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    p.config.mcp_servers = [
        {"name": "notion", "command": NO_SUCH, "args": [], "env_secrets": {"NOTION_TOKEN": "conn_notion_notion_token"}}
    ]
    p.secrets.set("conn_notion_notion_token", "ntn_OLD_v13550")
    res = client.post("/config/secret", json={"name": "app.notion", "value": "ntn_NEW_v13550"}).json()
    assert res["status"] == "replaced", res  # the pack's old token was the prior
    assert _entry(p, "notion")["env_secrets"] == {"NOTION_TOKEN": "mcp.notion.NOTION_TOKEN"}
    undo = client.post(f"/undo/{res['action_id']}")
    assert undo.status_code == 200, undo.text
    # The pack now reads the new row, and Undo put the OLD token there.
    assert p.secrets.get("mcp.notion.NOTION_TOKEN") == "ntn_OLD_v13550"


def test_a_row_that_now_names_a_different_server_drops_its_manifest(tmp_path):
    from iron_jarvis.mcp.manifest import PackManifest
    from iron_jarvis.mcp.pack_door import install_config

    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    old = {"name": "github", "command": NO_SUCH, "args": ["-y", "@modelcontextprotocol/server-github"], "env": {}}
    install_config(p, old, load=False)
    path = PackManifest(p.config.home).path("github")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    # A changed key or folder is the SAME server: the manifest stays.
    install_config(p, {**old, "env": {"X": "1"}}, load=False)
    assert path.is_file()
    # GitHub's own server is a different server: a first load.
    install_config(p, {"name": "github", "transport": "http", "url": conn_catalog.GITHUB_MCP_URL}, load=False)
    assert not path.is_file()


def test_an_older_github_pack_says_so_and_how_to_switch(client):
    p = client.app.state.platform
    p.config.mcp_servers = [
        {"name": "github", "command": NO_SUCH, "args": ["-y", "@modelcontextprotocol/server-github@2025.1.1"]}
    ]
    row = next(r for r in client.get("/connectors").json()["connectors"] if r["id"] == "github")
    assert row["package_note"] == (
        # v1.356.0 integrate (G74): moved on purpose - the card's button reads "Remove".
        f"{conn_catalog.OLDER_PACKAGE_NOTE} Remove it and connect again to use GitHub's own server."
    )
    p.config.mcp_servers = [{"name": "github", "transport": "http", "url": conn_catalog.GITHUB_MCP_URL}]
    row = next(r for r in client.get("/connectors").json()["connectors"] if r["id"] == "github")
    assert row["package_note"] == ""


def test_a_refused_pack_key_names_the_change_key_press():
    words = mcp_tools_mod.classify_load_failure({"command": "npx"}, "HTTPStatusError: 401 Unauthorized")
    assert words["reason"] == "the pack refused our credentials"
    assert "Change key" in words["fix"] and "Retry" in words["fix"]


def test_a_retrying_loop_keeps_the_start_of_its_failing_run(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        tick = client.app.state.platform.assignment_dispatcher._tick_hook
        health = client.app.state.d.loop_health
        tick("wave5_probe", False, RuntimeError("one"))
        first = dict(health["wave5_probe"])
        assert first["since"] == first["at"]
        # A run that has been failing since long ago (Windows' clock ticks at
        # 15.6 ms, so two live ticks can share a stamp: plant the earlier one).
        start = "2026-01-01T00:00:00+00:00"
        health["wave5_probe"] = {**first, "at": start, "since": start}
        tick("wave5_probe", False, RuntimeError("two"))
        second = health["wave5_probe"]
        assert second["since"] == start, "a retry restarted the failing run's clock"
        assert second["at"] != start and second["last_error"] == "RuntimeError: two"
        # A record from before v1.355.0 (no `since`): its `at` is the start.
        health["wave5_probe"] = {"ok": False, "last_error": "x", "at": start}
        tick("wave5_probe", False, RuntimeError("again"))
        assert health["wave5_probe"]["since"] == start
        tick("wave5_probe", True)
        tick("wave5_probe", False, RuntimeError("three"))
        third = health["wave5_probe"]
        assert third["since"] == third["at"], "a new failing run starts its own clock"
        loops = client.get("/diagnostics").json()["background_loops"]
        assert loops["wave5_probe"]["since"] == third["since"]


def test_the_model_is_told_messages_were_left_out_only_when_some_were(monkeypatch):
    """Lane 5A's note: a refit that dropped nothing (it only shortened this
    turn's tool results) must not tell the model older messages were left
    out — the user's receipt says "No messages were left out"."""
    import iron_jarvis.daemon.chat_turn as ct

    monkeypatch.setattr(ct, "_tokens_of", lambda msgs, system, *a: 10 * len(msgs))
    monkeypatch.setattr(ct, "_fit_turn_transcript", lambda d, msgs, system, *a, **k: (msgs, system))
    msgs = [LLMMessage(role="user", content="q1"), LLMMessage(role="assistant", content="a1"),
            LLMMessage(role="user", content="q2")]
    sent, system, dropped = ct._shrink_to_window(None, msgs, "SYS", "p", "m", head=3, window=100_000)
    assert dropped == 0 and system == "SYS" and ct.OVERFLOW_NOTE not in system
    # When older messages DO go, the model is told.
    monkeypatch.setattr(ct, "_tokens_of", lambda msgs, system, *a: 10_000_000 if len(msgs) > 1 else 10)
    sent, system, dropped = ct._shrink_to_window(None, msgs, "SYS", "p", "m", head=3, window=100_000)
    assert dropped == 2 and ct.OVERFLOW_NOTE in system
