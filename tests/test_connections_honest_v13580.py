"""v1.358.0 lane 8D — the Directory and Connections never claim more than is true.

Each gap, as a new user meets it, pinned through the REAL app factory, the
real catalog, the real add door and the real routes:

(1) the Directory's "Built in, nothing to connect: Files & documents, Web, …"
    line and the cards below it agree: a card that is a SEPARATE version of
    something built in names it (``built_in``) and says how it differs, in the
    built-in line's own words (the dashboard folds those cards apart —
    dashboard/__tests__/connections-honest-v13580.test.tsx);
(3) a saved Microsoft 365 reads ``status: "signin"`` ("Sign in to Microsoft
    to finish") until ITS OWN sign-in check says signed in — the pack's
    process is a stand-in (``tests/fixtures/ms365_signin_server_v13580.py``)
    answering ``verify-login`` the way Softeria's server does; the check is
    cached, never waited on by the listing, bounded, and a successful call
    counts when the check cannot answer;
(4) a key saved on an API-key card (Pixio) is CHECKED on save with the
    registry's own Test (a stand-in prober — the live one calls Pixio), and a
    refused key says so on save and on the card, never green;
(6) LM Studio / llama.cpp answering on this PC are seen by a bounded,
    cached, never-blocking read of their model list (two real local HTTP
    servers on free ports) and offered in one press where Ollama is
    (``GET /onboarding`` usable rows, ``POST /onboarding/use-model``, the
    Connections page's rows).
"""

from __future__ import annotations

import asyncio
import http.server
import json
import re
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors import catalog, service, signin_seen
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import client as mcp_client
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.providers import local_servers_seen as lss

ROOT = Path(__file__).resolve().parents[1]
SIGNIN_SERVER = ROOT / "tests" / "fixtures" / "ms365_signin_server_v13580.py"
PLAIN_SERVER = ROOT / "tests" / "fixtures" / "ms365_mcp_server_v13560.py"
CARD_ID = "microsoft_365"


def _src(rel: str) -> str:
    """A source file as the CI runner reads it (CRLF normalised, v1.232.1)."""
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def _platform(c: TestClient):
    return c.app.state.d.platform


def _row(c: TestClient, cid: str) -> dict:
    return next(r for r in c.get("/connectors").json()["connectors"] if r["id"] == cid)


@pytest.fixture(autouse=True)
def _fresh():
    """Load records, live clients and the two caches are process-wide."""
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    signin_seen.forget()
    lss.forget()
    with service._KEY_LOCK:
        service._KEY_CHECKS.clear()
    yield
    for store in (mcp_tools_mod._LOAD_STATUS, mcp_tools_mod._ANNOUNCED, mcp_tools_mod._LOAD_CFGS):
        store.clear()
    mcp_tools_mod.forget_live_client(CARD_ID)
    signin_seen.forget()
    lss.forget()
    with service._KEY_LOCK:
        service._KEY_CHECKS.clear()


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    monkeypatch.delenv("ENABLED_TOOLS", raising=False)
    monkeypatch.delenv(catalog.MS365_TENANT_ENV, raising=False)
    monkeypatch.delenv("PIXIO_API_KEY", raising=False)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    proj = tmp_path / "proj"
    proj.mkdir(exist_ok=True)
    return create_app(str(proj))


@pytest.fixture
def client(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        yield c


# =========================================================================== #
# (1) One truth about what is built in
# =========================================================================== #
def test_every_separate_version_names_what_the_built_in_line_says():
    """The three cards that repeat a built-in tool say which one, in the
    built-in line's own words, and how they differ; nothing else claims to.
    MUTATION: drop ``built_in`` from the filesystem card -> red."""
    extras = {c.id: c.built_in for c in catalog.CATALOG if c.built_in}
    assert extras == {"filesystem": "Files & documents", "fetch": "Web", "memory": "Memory"}
    for c in catalog.CATALOG:
        if c.built_in:
            assert c.built_in in catalog.BUILT_IN_TOOLS, c.id
            assert "already" in c.built_in_note and "separate" in c.built_in_note, c.id
        else:
            assert c.built_in_note == "", c.id


def test_the_directorys_built_in_line_is_the_catalogs_list():
    """DirectoryPage's "Built in, nothing to connect" words ARE
    ``catalog.BUILT_IN_TOOLS`` — a card's ``built_in`` can never name a thing
    that line does not say."""
    page = _src("dashboard/components/settings/pages/DirectoryPage.tsx")
    m = re.search(r"const BUILT_IN = \[([^\]]*)\]", page)
    assert m, "BUILT_IN not found on the Directory page"
    assert tuple(re.findall(r'"([^"]+)"', m.group(1))) == catalog.BUILT_IN_TOOLS


def test_the_gallery_rows_carry_the_built_in_fields(client):
    """The daemon's rows say it (the dashboard folds by it); a real app's row
    says nothing. The names stay the Tools page's names (v1.357.0 one
    catalog)."""
    rows = {r["id"]: r for r in client.get("/connectors").json()["connectors"]}
    assert rows["filesystem"]["built_in"] == "Files & documents"
    assert rows["filesystem"]["name"] == "Files & folders"
    assert rows["fetch"]["built_in"] == "Web" and rows["fetch"]["name"] == "Fetch web pages"
    assert rows["memory"]["built_in"] == "Memory"
    for cid in ("filesystem", "fetch", "memory"):
        assert rows[cid]["built_in_note"].startswith("Iron Jarvis already ")
    assert rows["github"]["built_in"] == "" and rows["github"]["built_in_note"] == ""


# =========================================================================== #
# (3) Microsoft 365 is green only once it is signed in
# =========================================================================== #
def _use_server(monkeypatch, script: Path, signin_file: Path | None = None) -> None:
    """The pack's program is ``script`` (only the program is swapped — the
    command, arguments and settings are the card's)."""
    real = mcp_client.StdioTransport

    def _transport(command, args=None, env=None, cwd=None, request_timeout=None):
        env2 = dict(env or {})
        if signin_file is not None:
            env2["MS365_FIXTURE_SIGNIN"] = str(signin_file)
        return real(sys.executable, [str(script)], env=env2, cwd=cwd, request_timeout=request_timeout)

    monkeypatch.setattr(mcp_tools_mod, "StdioTransport", _transport)
    monkeypatch.setattr(mcp_tools_mod, "resolve_launcher", lambda command: command)


@pytest.fixture
def signin_file(tmp_path) -> Path:
    return tmp_path / "ms365-signed-in.txt"


@pytest.fixture
def ms365(tmp_path, monkeypatch, signin_file):
    _use_server(monkeypatch, SIGNIN_SERVER, signin_file)
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        try:
            yield c
        finally:
            mcp_tools_mod.unload_pack_tools(_platform(c).registry, CARD_ID)


def _connect_ms365(c: TestClient, answer: str = "common"):
    r = c.post(f"/connectors/{CARD_ID}/connect", json={"values": {catalog.MS365_TENANT_ENV: answer}})
    assert r.status_code == 200, r.text
    assert r.json()["tools_loaded"] == 24
    return r


def test_microsoft_365_reads_sign_in_to_finish_until_its_own_check_says_signed_in(ms365, signin_file):
    """THE ACCEPTANCE: after Connect its 24 tools load, but nobody signed in
    to Microsoft — the card is not green and says "Sign in to Microsoft to
    finish" beside the existing Sign in press. Test asks the app's own check
    and says the same. Once the sign-in lands (the server's token file), Test
    and the card say signed in, and who. MUTATION: drop the
    ``_signin_state`` call in ``_status_for`` -> the first row reads
    "connected" (red)."""
    _connect_ms365(ms365)
    row = _row(ms365, CARD_ID)
    assert row["connected"] is True and row["tools_loaded"] == 24
    assert row["status"] == "signin" and row["signed_in"] is False
    assert row["detail"] == "Sign in to Microsoft to finish."
    assert row["signin"]["label"] == "Sign in to Microsoft"

    test = ms365.post(f"/connectors/{CARD_ID}/test").json()
    assert test["ok"] is False
    assert test["error"] == "Sign in to Microsoft to finish."
    assert test["fix"] == "Press Sign in to Microsoft on this card and finish in the window it opens."

    # The user signs in in the Build terminal; the running app sees it.
    signin_file.write_text("dana@contoso.com", encoding="utf-8")
    test = ms365.post(f"/connectors/{CARD_ID}/test").json()
    assert test["ok"] is True and test["account"] == "dana@contoso.com"
    row = _row(ms365, CARD_ID)
    assert row["status"] == "connected" and row["signed_in"] is True
    assert row["account"] == "dana@contoso.com"


def test_the_card_turns_by_itself_once_signed_in(ms365, signin_file):
    """No press needed: the listing's cached check is refreshed behind it,
    so a sign-in finished elsewhere turns the card on a later read (the
    Directory polls every 8 s). Waits for the thing asserted."""
    _connect_ms365(ms365)
    signin_file.write_text("dana@contoso.com", encoding="utf-8")
    deadline = time.monotonic() + 30
    row = _row(ms365, CARD_ID)
    while row["status"] != "connected" and time.monotonic() < deadline:
        time.sleep(0.2)
        # A verdict "not signed in" from before the write is served for its
        # short TTL; age it so the next read asks again.
        with signin_seen._lock:
            for k, (at, v, ident) in list(signin_seen._answers.items()):
                if not v.signed_in:
                    signin_seen._answers[k] = (at - signin_seen.TTL_NOT_SIGNED_IN_S, v, ident)
        row = _row(ms365, CARD_ID)
    assert row["status"] == "connected" and row["account"] == "dana@contoso.com"


def test_the_listing_never_waits_on_the_apps_check(ms365, monkeypatch):
    """The check runs behind the read: a check that hangs (an app stuck on
    the network) leaves the listing answering at once, saying "signin" —
    never blocking GET /connectors. The proof is ORDER, not a clock: the
    listing answers while the check is still held."""
    _connect_ms365(ms365)
    signin_seen.forget()
    held = threading.Event()
    entered = threading.Event()

    def _hung(platform, server, timeout_s):
        entered.set()
        held.wait(30)
        return signin_seen.Verdict(True, "late@example.com", "check")

    monkeypatch.setattr(signin_seen, "_call_check", _hung)
    try:
        row = _row(ms365, CARD_ID)
        assert row["status"] == "signin"
        assert entered.wait(10), "the background check never started"
        assert not held.is_set()
        # Still held: a second read neither waits nor starts a second check.
        assert _row(ms365, CARD_ID)["status"] == "signin"
    finally:
        held.set()


def test_one_check_is_bounded_and_never_doubles_up():
    """A check is bounded (``timeout_s``) and, while the app's own call is
    still out, a second check never sends another call into the same app."""
    calls: list[int] = []
    gate = threading.Event()

    class _Client:
        async def call_tool(self, name, args):
            calls.append(1)
            await asyncio.get_running_loop().run_in_executor(None, gate.wait, 30)
            return {"content": [{"type": "text", "text": json.dumps({"success": True})}]}

    tool = SimpleNamespace(client=_Client(), remote_name="verify-login")
    platform = SimpleNamespace(
        registry=SimpleNamespace(get=lambda name: tool, mcp_names=lambda server: []),
        engine=None,
    )
    try:
        v = signin_seen.check_now(platform, "app", "id", timeout_s=0.3)
        assert v.signed_in is False and v.how == "none"
        v2 = signin_seen.check_now(platform, "app", "id", timeout_s=0.3)
        assert v2.signed_in is False
        assert calls == [1], "a second call went into an app whose first is still out"
    finally:
        gate.set()


@pytest.mark.parametrize(
    "result, expected",
    [
        ({"content": [{"type": "text", "text": '{"success": false, "message": "Login failed: No valid token found"}'}]},
         signin_seen.Verdict(False, "", "check")),
        ({"content": [{"type": "text", "text": json.dumps({"success": True, "message": "Login successful",
                                                          "userData": {"displayName": "Dana", "userPrincipalName": "d@x.com"}})}]},
         signin_seen.Verdict(True, "d@x.com", "check")),
        ({"content": [{"type": "text", "text": "verify-login: ok"}]}, None),
        ({"content": [{"type": "text", "text": '{"success": true}'}], "isError": True}, None),
        ("nonsense", None),
    ],
)
def test_the_apps_own_answer_is_read_the_way_its_server_writes_it(result, expected):
    assert signin_seen.parse_check(result) == expected


def test_a_successful_call_counts_when_the_check_cannot_answer(tmp_path, monkeypatch):
    """"Or the first successful call": the v1.356.0 stand-in's verify-login
    answers in no shape the check reads, so nothing says signed in — until a
    call of one of the app's own tools succeeds (the ledger), which proves
    it. A sign-in tool's success proves nothing."""
    from sqlmodel import Session as DbSession

    from iron_jarvis.core.models import ToolInvocation

    _use_server(monkeypatch, PLAIN_SERVER)
    with TestClient(_app(tmp_path, monkeypatch)) as c:
        try:
            _connect_ms365(c)
            p = _platform(c)
            cfg = service._server_cfg(p, CARD_ID)
            ident = service.signin_identity(cfg)
            assert signin_seen.check_now(p, CARD_ID, ident).signed_in is False

            def _ledger(tool: str) -> None:
                with DbSession(p.engine) as db:
                    db.add(ToolInvocation(session_id="chat", agent_run_id="chat", tool=tool, ok=True,
                                          created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
                    db.commit()

            _ledger(f"mcp__{CARD_ID}__login")
            assert signin_seen.check_now(p, CARD_ID, ident).signed_in is False
            _ledger(f"mcp__{CARD_ID}__get-calendar-view")
            v = signin_seen.check_now(p, CARD_ID, ident)
            assert v.signed_in is True and v.how == "call"
            assert _row(c, CARD_ID)["status"] == "connected"
        finally:
            mcp_tools_mod.unload_pack_tools(_platform(c).registry, CARD_ID)


def test_removing_the_app_forgets_its_sign_in(ms365, signin_file):
    """A removed app must not keep its old answer for the next connect."""
    signin_file.write_text("dana@contoso.com", encoding="utf-8")
    _connect_ms365(ms365)
    assert ms365.post(f"/connectors/{CARD_ID}/test").json()["ok"] is True
    assert signin_seen._answers.get(CARD_ID) is not None
    assert ms365.delete(f"/connectors/{CARD_ID}").status_code == 200
    assert signin_seen._answers.get(CARD_ID) is None


# =========================================================================== #
# (4) An API key is checked on save
# =========================================================================== #
REFUSED = (
    "pixio rejected the credential (401) — the key/token is invalid, revoked, "
    "or expired; reconnect it"
)


def test_a_refused_key_says_so_on_save_and_the_card_is_never_green(client):
    """THE ACCEPTANCE: a mistyped Pixio key is tested on save (the registry's
    own Test — the Connections page's check) OFF the event loop, the answer
    says it was refused, and the card reads "failed" with those words until
    the key changes. MUTATION: drop the check in ``_connect_api_key`` ->
    no ``test`` (red); drop the key_check branch in ``_status_for`` -> the
    row reads "connected" (red)."""
    p = _platform(client)
    seen_on_loop: list[bool] = []

    def _refuse(provider, cred):
        try:
            asyncio.get_running_loop()
            seen_on_loop.append(True)
        except RuntimeError:
            seen_on_loop.append(False)
        return False, REFUSED

    p.connections._prober = _refuse
    r = client.post("/connectors/pixio/connect", json={"values": {"key": "pxio_typo"}})
    assert r.status_code == 200, r.text
    assert r.json()["test"] == {
        "ok": False,
        "detail": "Pixio didn't accept this key. Remove it and connect again with a working key.",
    }
    assert seen_on_loop == [False], "the key check ran on the event loop"

    row = _row(client, "pixio")
    assert row["connected"] is True and row["status"] == "failed"
    assert row["detail"] == "Pixio didn't accept this key. Remove it and connect again with a working key."
    assert row["technical"] == REFUSED

    # A NEW key (the Connections page) is not judged by the old verdict ...
    p.connections._prober = lambda provider, cred: (True, "pixio reachable — credential accepted")
    assert client.post("/connections/pixio/key", json={"key": "pxio_good"}).status_code == 200
    assert _row(client, "pixio")["status"] == "connected"
    # ... and the Connections page's Test is the same check the card shows.
    p.connections._prober = lambda provider, cred: (False, REFUSED)
    assert client.post("/connections/pixio/test").json()["ok"] is False
    assert _row(client, "pixio")["status"] == "failed"
    p.connections._prober = lambda provider, cred: (True, "pixio reachable — credential accepted")
    assert client.post("/connectors/pixio/test").json()["ok"] is True
    row = _row(client, "pixio")
    assert row["status"] == "connected" and row["checked"] is True


def test_a_key_that_could_not_be_checked_says_that_not_refused(client):
    p = _platform(client)
    p.connections._prober = lambda provider, cred: (False, "could not reach pixio: ConnectError: boom")
    r = client.post("/connectors/pixio/connect", json={"values": {"key": "pxio_x"}})
    assert r.json()["test"]["detail"] == "Couldn't reach Pixio to check the key. Press Test to try again."
    assert _row(client, "pixio")["status"] == "failed"


def test_removing_a_key_forgets_its_check(client):
    p = _platform(client)
    p.connections._prober = lambda provider, cred: (False, REFUSED)
    client.post("/connectors/pixio/connect", json={"values": {"key": "pxio_typo"}})
    assert client.delete("/connectors/pixio").status_code == 200
    assert "pixio" not in service._KEY_CHECKS
    assert _row(client, "pixio")["connected"] is False


# =========================================================================== #
# (6) LM Studio / llama.cpp on this PC
# =========================================================================== #
class _ModelServer:
    """A real OpenAI-compatible model list on 127.0.0.1:<free port>/v1."""

    def __init__(self, rows: list[dict], hold: threading.Event | None = None) -> None:
        self.rows = rows
        self.hold = hold
        owner = self

        class _H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if owner.hold is not None:
                    owner.hold.wait(30)
                if self.path.rstrip("/") != "/v1/models":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps({"object": "list", "data": owner.rows}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                pass

        class _S(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.httpd = _S(("127.0.0.1", 0), _H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


LMSTUDIO_ROWS = [
    {"id": "text-embedding-nomic-embed-text-v1.5", "object": "model", "owned_by": "organization_owner"},
    {"id": "qwen2.5-7b-instruct", "object": "model", "owned_by": "organization_owner"},
]


@pytest.fixture
def servers(monkeypatch):
    """An LM Studio answering, and a server on the llama.cpp port that is NOT
    llama.cpp (it does not say ``owned_by: llamacpp``)."""
    lm = _ModelServer(LMSTUDIO_ROWS)
    other = _ModelServer([{"id": "some-model", "object": "model", "owned_by": "someone"}])
    monkeypatch.setattr(lss, "PROBE_FORCED_OFF", False)
    monkeypatch.setattr(
        lss,
        "SERVERS",
        (
            lss.LocalServer("lmstudio", "LM Studio", "LM Studio (on this PC)", lm.url),
            lss.LocalServer("llamacpp", "llama.cpp", "llama.cpp (on this PC)", other.url, owned_by="llamacpp"),
        ),
    )
    try:
        yield SimpleNamespace(lm=lm, other=other)
    finally:
        lm.stop()
        other.stop()


def test_the_probe_sees_lm_studio_and_never_names_another_server_llama_cpp(servers, monkeypatch):
    seen = lss.probe_now()
    assert seen["lmstudio"].models == ("qwen2.5-7b-instruct",), "an embedding model is not a chat model"
    assert seen["llamacpp"].models == (), "a server that is not llama.cpp was called llama.cpp"
    llama = _ModelServer([{"id": "model.gguf", "object": "model", "owned_by": "llamacpp"}])
    try:
        monkeypatch.setattr(
            lss,
            "SERVERS",
            (lss.SERVERS[0], lss.LocalServer("llamacpp", "llama.cpp", "llama.cpp (on this PC)", llama.url, owned_by="llamacpp")),
        )
        assert lss.probe_now(("llamacpp",))["llamacpp"].models == ("model.gguf",)
    finally:
        llama.stop()


def test_seen_never_waits_on_a_port(monkeypatch):
    """``seen`` returns the last answer at once and refreshes behind it: the
    proof is ORDER (it answered while the server still held its reply)."""
    hold = threading.Event()
    slow = _ModelServer(LMSTUDIO_ROWS, hold=hold)
    monkeypatch.setattr(lss, "PROBE_FORCED_OFF", False)
    monkeypatch.setattr(lss, "SERVERS", (lss.LocalServer("lmstudio", "LM Studio", "LM Studio (on this PC)", slow.url),))
    monkeypatch.setattr(lss, "PROBE_TIMEOUT_S", 10.0)
    try:
        first = lss.seen()
        assert first["lmstudio"].answering is False
        assert not hold.is_set()
        hold.set()
        deadline = time.monotonic() + 15
        while not lss.seen()["lmstudio"].answering and time.monotonic() < deadline:
            time.sleep(0.05)
        assert lss.seen()["lmstudio"].models == ("qwen2.5-7b-instruct",)
    finally:
        hold.set()
        slow.stop()


def test_onboarding_offers_lm_studio_in_one_press_and_the_press_connects_and_chooses_it(client, servers):
    """THE ACCEPTANCE: where a detected Ollama is offered (GET /onboarding's
    usable rows, the Overview's one-press card), a running LM Studio is too;
    the ONE press saves its address into the model-server slot (through the
    settings writer) and chooses it. MUTATION: drop the local-server branch
    in the use-model door -> 409 "I don't know a model called" (red)."""
    lss.probe_now()
    usable = client.get("/onboarding").json()["model"]["usable"]
    assert {"provider": "lmstudio", "label": "LM Studio (on this PC)", "local": True} in usable
    assert all(u["provider"] != "llamacpp" for u in usable)

    r = client.post("/onboarding/use-model", json={"provider": "lmstudio"})
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] == {"provider": "custom", "model": "qwen2.5-7b-instruct"}
    cfg = _platform(client).config
    assert cfg.custom_base_url == servers.lm.url and cfg.custom_model == "qwen2.5-7b-instruct"
    assert cfg.default_provider == "custom"
    # Saved now: no second one-press offer for it.
    assert all(u["provider"] != "lmstudio" for u in client.get("/onboarding").json()["model"]["usable"])


def test_the_press_never_replaces_a_saved_model_server(client, servers):
    lss.probe_now()
    assert client.put("/settings", json={"values": {"custom_base_url": "http://192.168.1.50:8000/v1"}}).status_code == 200
    assert all(u["provider"] != "lmstudio" for u in client.get("/onboarding").json()["model"]["usable"])
    r = client.post("/onboarding/use-model", json={"provider": "lmstudio"})
    assert r.status_code == 409
    assert "already set to http://192.168.1.50:8000/v1" in r.json()["detail"]
    cfg = _platform(client).config
    assert cfg.custom_base_url == "http://192.168.1.50:8000/v1" and cfg.default_provider == "mock"


def test_a_press_for_a_server_that_stopped_says_so_and_saves_nothing(client, servers):
    lss.probe_now()
    servers.lm.stop()
    r = client.post("/onboarding/use-model", json={"provider": "lmstudio"})
    assert r.status_code == 409
    assert r.json()["detail"].startswith("LM Studio isn't answering on this PC")
    assert not (_platform(client).config.custom_base_url or "")


def test_the_connections_rows_and_their_press(client, servers):
    """The Accounts card's rows read the cache; its press connects (saves the
    address) and never chooses — the Ollama row's rule."""
    lss.probe_now()
    rows = {r["key"]: r for r in client.get("/connections/local-servers").json()["servers"]}
    assert rows["lmstudio"]["running"] is True and rows["lmstudio"]["slot"] == "free"
    assert rows["llamacpp"]["running"] is False
    r = client.post("/connections/local-servers/lmstudio/connect")
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "qwen2.5-7b-instruct"
    cfg = _platform(client).config
    assert cfg.custom_base_url == servers.lm.url and cfg.default_provider == "mock"
    rows = {r["key"]: r for r in client.get("/connections/local-servers").json()["servers"]}
    assert rows["lmstudio"]["slot"] == "saved"
    assert client.post("/connections/local-servers/nope/connect").status_code == 404


def test_health_field_offers_only_what_one_press_can_connect(servers):
    lss.probe_now()
    free = SimpleNamespace(custom_base_url="")
    assert lss.health_field(free) == [
        {"key": "lmstudio", "label": "LM Studio (on this PC)", "base_url": servers.lm.url}
    ]
    assert lss.health_field(SimpleNamespace(custom_base_url="http://192.168.1.50:8000/v1")) == []


def test_the_endpoint_forms_chips_name_the_same_addresses():
    """lib/localServerPresets.ts (the endpoint forms' chips) and the probe
    name each server at the same documented address."""
    ts = _src("dashboard/lib/localServerPresets.ts")
    chips = dict(re.findall(r'id: "(\w+)",[^}]*?baseUrl: "([^"]+)"', ts, flags=re.S))
    assert chips == {s.key: s.base_url for s in lss.SERVERS}


# =========================================================================== #
# The dashboard's fixture is the REAL daemon's rows
# =========================================================================== #
CONTRACT = ROOT / "dashboard" / "__tests__" / "fixtures" / "connections-honest-v13580.json"


def test_the_dashboard_fixture_is_the_daemons_rows(ms365, signin_file):
    """``dashboard/__tests__/fixtures/connections-honest-v13580.json`` holds
    what the REAL daemon's GET /connectors sends for: the three separate
    versions of built-in tools and a real app, a Pixio key refused on save,
    and Microsoft 365 before and after its sign-in. The dashboard's pins feed
    them through the real Directory. Regenerate with IJ_REGEN_HONEST_ROWS=1
    after an intended change."""
    import os

    p = _platform(ms365)
    rows = {r["id"]: r for r in ms365.get("/connectors").json()["connectors"]}
    data: dict = {k: rows[k] for k in ("filesystem", "fetch", "memory", "github")}
    p.connections._prober = lambda provider, cred: (False, REFUSED)
    ms365.post("/connectors/pixio/connect", json={"values": {"key": "pxio_typo"}})
    data["pixio_refused"] = _row(ms365, "pixio")
    _connect_ms365(ms365)
    data["ms365_signin"] = _row(ms365, CARD_ID)
    signin_file.write_text("dana@contoso.com", encoding="utf-8")
    assert ms365.post(f"/connectors/{CARD_ID}/test").json()["ok"] is True
    data["ms365_signed_in"] = _row(ms365, CARD_ID)
    assert data["ms365_signin"]["status"] == "signin"
    assert data["ms365_signed_in"]["status"] == "connected"
    if os.environ.get("IJ_REGEN_HONEST_ROWS") == "1":
        CONTRACT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert json.loads(CONTRACT.read_text(encoding="utf-8")) == data
