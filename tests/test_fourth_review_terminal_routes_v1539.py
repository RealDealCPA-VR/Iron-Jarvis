"""Fourth north-star fix plan, group 30, the terminal ROUTES (30C): TM3, TM4,
TM5, C-TM-1, C-TM-2, the close-time removal of a terminal's own files (D9
option B) and the words for a shell that cannot be started (after TM2).

* TM3  - a terminal opened "with Iron Jarvis's tools" says, under it, that
  nothing was written in the user's folder, and names once each file an
  older Iron Jarvis left there (never deleting or editing it).
* TM4  - a restored shell's second start ran inline in the socket's receive
  loop, so the keys typed meanwhile were not read for up to 15 s; and "could
  not be started again" was said at every resize that tried again.
* TM5  - a slow skill library made "ask AI" say the chosen skill does not
  exist (decision D8: refuse in words, 503).
* C-TM-1 - "Open with {usual shell}" on a terminal whose program is gone:
  same id, folder and history, on the user's usual shell.
* C-TM-2 - a "with tools" terminal opens on the account the page names (the
  account of the terminal it was opened from).
* D9 B - Close takes the terminal's own file in Iron Jarvis's folder with it.
* TM2 follow-up - ``POST /terminals`` with a shell that cannot be started
  answers in plain words, never a 500.

Driven through the REAL app factory, routes, socket and terminals manager,
with stand-in shells (``FakeBackend``) and the recording stand-in for the
account manager (``tests/fixtures/fake_iron_proxy_v1301.py``). No real coding
tool runs: the launch recipes' probes are stubbed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.daemon.routes.terminals as routes_mod
from iron_jarvis.browser.panetokens import MCP_TOKEN_ENV
from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals import recipes
from iron_jarvis.terminals.backend import FakeBackend

FIXTURE = Path(__file__).parent / "fixtures" / "fake_iron_proxy_v1301.py"
sys.path.insert(0, str(FIXTURE.parent))
from fake_iron_proxy_v1301 import FEATURES_PICK_PROFILE, FakeIronProxy  # noqa: E402

#: The longest a test waits for a thread or a frame. Only a RED run waits it.
_BOUND_S = 5.0

GONE = "gone-program"

NOTHING_WRITTEN = (
    "Nothing was written in your folder: Claude Code has Iron Jarvis's tools for "
    "this terminal only."
)
SKILL_SLOW = (
    'Your skill library did not answer in time, so nothing was asked. Ask again, '
    'or choose "skill: auto".'
)


def _leftover(path: Path) -> str:
    return (
        f"An older Iron Jarvis left {path} in this folder. Nothing uses it now; "
        "delete it whenever you like."
    )


def _engine_words(text: str) -> list[str]:
    bad = ("WinError", "Errno", "OSError", "PermissionError", "FileNotFound", "Traceback")
    return [w for w in bad if w in text]


def _until(cond, timeout: float = _BOUND_S) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
class _RecordingBackend(FakeBackend):
    instances: list["_RecordingBackend"] = []
    lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None
        self.written: list[str] = []
        with _RecordingBackend.lock:
            _RecordingBackend.instances.append(self)

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        if argv and Path(str(argv[0])).name == GONE:
            raise FileNotFoundError(2, "No such file or directory", str(argv[0]))
        self.env = dict(env) if env is not None else None
        super().start(argv, cwd, env, cols, rows)

    def write(self, data) -> None:  # type: ignore[override]
        self.written.append(data if isinstance(data, str) else data.decode())
        super().write(data)


def _backend_of(pane_id: str) -> _RecordingBackend:
    found = [b for b in _RecordingBackend.instances
             if b.env is not None and b.env.get("IRONJARVIS_PANE_ID") == pane_id]
    assert found, f"no shell was started for pane {pane_id}"
    return found[-1]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    for k in ("CLAUDE_CONFIG_DIR", "ANTHROPIC_API_KEY", MCP_TOKEN_ENV):
        monkeypatch.delenv(k, raising=False)
    _RecordingBackend.instances = []
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _RecordingBackend)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    yield


@pytest.fixture
def proj(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    return root


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "a report"
    root.mkdir()
    return root


@pytest.fixture
def client(proj):
    with TestClient(create_app(str(proj))) as c:
        yield c


@pytest.fixture
def fake(tmp_path):
    f = FakeIronProxy(tmp_path / "ipd", features=list(FEATURES_PICK_PROFILE)).start()
    yield f
    f.stop()


_FAKE_CLAUDE = str(Path(__file__).parent / "fixtures" / "no-such-claude")


@pytest.fixture
def installed_claude(monkeypatch):
    """A Claude Code build that advertises every option (probes stubbed)."""
    monkeypatch.setattr(
        recipes, "_find", lambda command: _FAKE_CLAUDE if command == "claude" else ""
    )

    def probe(argv, *args, **kw):
        if argv[1] == "--version":
            return "1.2.3 (Claude Code)"
        return (
            "Usage: claude [options]\n"
            "  --mcp-config <file>          Load MCP servers from a JSON file\n"
            "  --strict-mcp-config          Only use servers from --mcp-config\n"
            "  --disallowed-tools <names>   Comma-separated tools to disable\n"
        )

    monkeypatch.setattr(recipes, "_run_probe", probe)
    recipes.clear_probe_cache()
    yield
    recipes.clear_probe_cache()


def _manager(c):
    return c.app.state.platform.terminals


def _own_file(c, pane_id: str) -> Path:
    return recipes.terminal_files_dir(_manager(c).terminal_files_home) / f"{pane_id}.json"


def _with_tools(c, near_id: str, **extra):
    return c.post(
        "/terminals/launch-with-tools",
        json={"cli": "claude", "near": near_id, "capabilities": {"memory": True}, **extra},
    )


# --------------------------------------------------------------------------- #
# TM3: the note under a "with tools" terminal
# --------------------------------------------------------------------------- #
def test_tm3_a_with_tools_terminal_says_nothing_was_written_in_the_folder(
    client, repo, installed_claude
):
    """The answer AND the terminal's own row (what the page reads after a
    reload) carry the note; nothing is in the folder, so nothing is named.
    MUTATION: do not keep the note on the session -> the row has no
    ``tools`` -> red."""
    c = client
    near = c.post("/terminals", json={"cwd": str(repo)}).json()
    r = _with_tools(c, near["id"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["tools"]["set_up"] is True
    assert out["tools"]["note"] == NOTHING_WRITTEN
    assert out["tools"]["leftovers"] == []
    assert isinstance(out["tools"]["limitations"], list)
    assert list(repo.iterdir()) == []
    assert out["pane"]["tools"]["note"] == NOTHING_WRITTEN
    rows = {t["id"]: t for t in c.get("/terminals").json()["terminals"]}
    assert rows[out["pane"]["id"]]["tools"] == {
        "note": NOTHING_WRITTEN,
        "leftovers": [],
        "limitations": out["tools"]["limitations"],
    }
    # A plain terminal's row is unchanged: no ``tools`` at all.
    assert "tools" not in rows[near["id"]]


def test_tm3_files_an_older_iron_jarvis_left_are_named_once_and_never_touched(
    client, repo, installed_claude
):
    """A ``.jarvis-codex`` folder and a ``.mcp.json`` holding the old terminal
    entry: each named once, by its absolute path, in the contract's words; the
    files are exactly as they were. MUTATION: drop ``leftovers_in`` -> [] ->
    red."""
    c = client
    codex_home = repo / ".jarvis-codex"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text("# an older terminal's settings\n", encoding="utf-8")
    mcp = repo / ".mcp.json"
    old = {
        "mcpServers": {
            "iron-jarvis": {
                "type": "http",
                "url": "http://127.0.0.1:1/mcp",
                "headers": {"Authorization": "Bearer " + recipes.CONFIG_TOKEN_REFERENCE},
            },
            "a-website-helper": {"command": "helper"},
        }
    }
    mcp.write_text(json.dumps(old, indent=2), encoding="utf-8")
    before = mcp.read_bytes()

    near = c.post("/terminals", json={"cwd": str(repo)}).json()
    r = _with_tools(c, near["id"])
    assert r.status_code == 200, r.text
    tools = r.json()["tools"]
    assert tools["note"] == NOTHING_WRITTEN
    assert sorted(tools["leftovers"]) == sorted([_leftover(codex_home), _leftover(mcp)])
    assert len(set(tools["leftovers"])) == len(tools["leftovers"]) == 2
    assert r.json()["pane"]["tools"]["leftovers"] == tools["leftovers"]
    # Never deleted or edited.
    assert codex_home.is_dir() and (codex_home / "config.toml").is_file()
    assert mcp.read_bytes() == before


def test_tm3_the_note_is_withheld_when_a_file_was_written_in_the_folder(tmp_path):
    """The guard behind the sentence: it is said only when the tool was set
    up AND no file it wrote lies inside the terminal's folder.
    MUTATION: drop the ``_inside`` check -> the note is said -> red."""
    folder = tmp_path / "a website"
    folder.mkdir()
    inside = {"set_up": True, "config_writes": [str(folder / "sub" / "x.json")]}
    outside = {"set_up": True, "config_writes": [str(tmp_path / "own" / "x.json")]}
    assert routes_mod._tools_note("Pi", inside, str(folder))["note"] == ""
    assert routes_mod._tools_note("Pi", outside, str(folder))["note"] == (
        "Nothing was written in your folder: Pi has Iron Jarvis's tools for this terminal only."
    )
    assert routes_mod._tools_note("Pi", {"set_up": False}, str(folder))["note"] == ""
    # The recipe's own limitations ride along unchanged (Pi's hand-typed step).
    step = {"set_up": True, "limitations": ["Run the step in this terminal."]}
    assert routes_mod._tools_note("Pi", step, str(folder))["limitations"] == [
        "Run the step in this terminal."
    ]


# --------------------------------------------------------------------------- #
# D9 B: Close takes the terminal's own file with it
# --------------------------------------------------------------------------- #
def test_closing_a_with_tools_terminal_removes_its_own_file(client, repo, installed_claude):
    """MUTATION: drop ``_remove_own_files`` from ``DELETE /terminals/{id}`` ->
    the file stays -> red."""
    c = client
    near = c.post("/terminals", json={"cwd": str(repo)}).json()
    pane = _with_tools(c, near["id"]).json()["pane"]
    own = _own_file(c, pane["id"])
    assert own.is_file()
    assert c.delete(f"/terminals/{pane['id']}").json()["killed"] is True
    assert not own.exists(), "a closed terminal's own file was left in Iron Jarvis's folder"


def test_closing_a_terminal_that_was_not_reopened_removes_its_own_file(client):
    """"Close" on a terminal that could not be reopened is a close for good
    too. MUTATION: drop the call in ``forget_not_reopened`` -> red."""
    c = client
    m = _manager(c)
    rid = "term_gone1"
    own = _own_file(c, rid)
    own.parent.mkdir(parents=True, exist_ok=True)
    own.write_text("{}", encoding="utf-8")
    other = _own_file(c, "term_other")
    other.write_text("{}", encoding="utf-8")
    with m._lock:
        m._restore_failed[rid] = {"id": rid, "shell": GONE, "argv": [GONE], "cwd": "", "cols": 80, "rows": 24}
    assert c.delete(f"/terminals/not-reopened/{rid}").json() == {"closed": True}
    assert not own.exists()
    assert other.is_file(), "another terminal's file was taken"


# --------------------------------------------------------------------------- #
# C-TM-2: a "with tools" terminal opens on the account the page names
# --------------------------------------------------------------------------- #
def _enable(c) -> None:
    r = c.post("/iron-proxy/enable")
    assert r.status_code == 200 and r.json()["status"]["running"] is True, r.text


def _refresh(c) -> None:
    assert c.get("/iron-proxy?discover=0").status_code == 200


def test_ctm2_with_tools_opens_on_the_named_account(client, fake, repo, installed_claude):
    """Three Claude accounts; the daemon by itself would take the first free
    one. The page names "Spare" (the account of the terminal it was opened
    from): the new terminal is on Spare, its Claude home Spare's.
    MUTATION: drop ``account=body.account`` -> Work Max -> red."""
    c = client
    _enable(c)
    fake.add_profile("anthropic", "Work Max")
    fake.add_profile("anthropic", "Personal")
    spare = fake.add_profile("anthropic", "Spare")
    _refresh(c)
    near = c.post("/terminals", json={"cwd": str(repo)}).json()
    r = _with_tools(c, near["id"], account=spare["id"])
    assert r.status_code == 200, r.text
    pane = r.json()["pane"]
    assert pane["accounts"]["anthropic"]["id"] == spare["id"]
    assert _backend_of(pane["id"]).env["CLAUDE_CONFIG_DIR"] == spare["cli"]["home"]


def test_ctm2_an_account_that_cannot_be_used_opens_nothing(client, fake, repo, installed_claude):
    """A parked account: the existing 409 sentence, no terminal, no key."""
    c = client
    _enable(c)
    fake.add_profile("anthropic", "Work Max")
    spare = fake.add_profile("anthropic", "Spare")
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    fake.states[spare["id"]].update(
        {"status": "parked", "parkedUntil": until, "parkedReason": {"kind": "rate-limit"}}
    )
    _refresh(c)
    near = c.post("/terminals", json={"cwd": str(repo)}).json()
    shells = len(_RecordingBackend.instances)
    r = _with_tools(c, near["id"], account=spare["id"])
    assert r.status_code == 409, r.text
    assert "Spare" in r.json()["detail"]
    assert len(_RecordingBackend.instances) == shells
    assert len(_manager(c).pane_tokens) == 0


# --------------------------------------------------------------------------- #
# C-TM-1: "Open with {usual shell}" on a terminal whose program is gone
# --------------------------------------------------------------------------- #
@pytest.fixture
def gone_client(proj):
    """An app whose boot restore meets a terminal whose program is gone."""
    app = create_app(str(proj))
    m = app.state.platform.terminals
    m._pty_ok = True
    rid = "term_gone1"
    m.state_path.parent.mkdir(parents=True, exist_ok=True)
    m.state_path.write_text(json.dumps({"terminals": [{
        "id": rid, "shell": GONE, "argv": [GONE], "cwd": str(proj), "cols": 80, "rows": 24,
        "name": "notes",
    }]}), encoding="utf-8")
    folder = m.state_path.parent / "terminals-scrollback"
    folder.mkdir(exist_ok=True)
    (folder / f"{rid}.bin").write_bytes(b"history of the notes terminal\r\n")
    with TestClient(app) as c:
        yield c, rid


def test_ctm1_open_with_the_usual_shell_keeps_the_id_folder_and_history(gone_client, proj):
    """The row says which usual shell its press opens; the press reopens the
    terminal under its id, in its folder, with its history, on that shell.
    MUTATION: ignore ``shell`` -> the gone program again -> 409 -> red."""
    from iron_jarvis.terminals.shells import default_shell

    c, rid = gone_client
    rows = c.get("/terminals").json()["restore_failed_panes"]
    assert [r["id"] for r in rows] == [rid]
    assert "no longer on this PC" in rows[0]["reason"]
    usual = default_shell()
    assert rows[0]["usual_shell"] == usual["label"]

    r = c.post(f"/terminals/not-reopened/{rid}/retry", json={"shell": "default"})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["id"] == rid and row["alive"] is True
    assert row["cwd"] == str(proj)
    assert row["shell"] == usual["name"]
    assert Path(str(row["argv"][0])).name == Path(str(usual["argv"][0])).name
    assert "history of the notes terminal" in _manager(c).get(rid).output_tail()
    assert "restore_failed_panes" not in c.get("/terminals").json()


def test_ctm1_try_again_still_tries_its_own_program(gone_client):
    """No body: today's "Try again" (its own program, still gone) -> 409, and
    the row stays listed with its own program's reason."""
    c, rid = gone_client
    r = c.post(f"/terminals/not-reopened/{rid}/retry")
    assert r.status_code == 409
    assert GONE in r.json()["detail"] and not _engine_words(r.json()["detail"])
    rows = c.get("/terminals").json()["restore_failed_panes"]
    assert [x["id"] for x in rows] == [rid] and GONE in rows[0]["reason"]
    assert c.post(f"/terminals/not-reopened/{rid}/retry", json={"shell": "zsh"}).status_code == 400


def test_ctm1_when_the_usual_shell_fails_too_the_row_keeps_its_own_program(gone_client, monkeypatch):
    c, rid = gone_client
    monkeypatch.setattr(
        "iron_jarvis.terminals.shells.default_shell",
        lambda: {"name": "fish", "argv": [f"/nowhere/{GONE}"], "label": "fish"},
    )
    r = c.post(f"/terminals/not-reopened/{rid}/retry", json={"shell": "default"})
    assert r.status_code == 409
    m = _manager(c)
    assert m._restore_failed[rid]["argv"] == [GONE]
    rows = c.get("/terminals").json()["restore_failed_panes"]
    assert rows[0]["reason"].startswith(f"Its program, {GONE}, is no longer on this PC")


# --------------------------------------------------------------------------- #
# After TM2: a shell that cannot be started is said in words
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("exc", "said"),
    [
        (FileNotFoundError(2, "No such file or directory", "/opt/x/fish"),
         "The terminal could not be opened. Its program, fish, is no longer on this PC "
         "(it may have been uninstalled or moved)."),
        (PermissionError(13, "Permission denied", "/opt/x/fish"),
         "The terminal could not be opened. This PC did not let Iron Jarvis start its "
         "program, fish."),
        (OSError(5, "Input/output error"),
         "The terminal could not be opened. Its program, fish, could not be started."),
    ],
)
def test_a_new_terminal_whose_shell_cannot_start_is_refused_in_words(proj, monkeypatch, exc, said):
    """30B's Linux/Mac start now raises like Windows does. MUTATION: drop the
    ``except OSError`` in ``create_terminal`` -> 500 -> red."""

    class _Raises(FakeBackend):
        def start(self, argv, cwd, env, cols, rows):
            raise exc

    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _Raises)
    with TestClient(create_app(str(proj)), raise_server_exceptions=False) as c:
        _manager(c)._pty_ok = True
        r = c.post("/terminals", json={"shell": "/opt/x/fish"})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == said
    assert not _engine_words(r.json()["detail"])


# --------------------------------------------------------------------------- #
# TM4: the second start runs beside the keys, and fails in one line
# --------------------------------------------------------------------------- #
def test_tm4_keys_typed_during_a_slow_second_start_are_read_at_once(client, tmp_path):
    """Through the real socket: a restored terminal, its second start (at the
    page's size) hung inside Windows. The key typed after the resize reaches
    the OLD shell while the start still waits, and keeps it; the resize sent
    meanwhile is applied when the start ends (the last one wins).
    MUTATION: await the restart inline in the receive loop -> the key waits
    behind it -> red."""
    c = client
    m = _manager(c)
    gate, typed, entered = threading.Event(), threading.Event(), threading.Event()
    starts = [0]

    class _Gated(FakeBackend):
        def start(self, argv, cwd, env, cols, rows):
            starts[0] += 1
            if starts[0] > 1:  # the second start: Windows does not answer yet
                entered.set()
                gate.wait(60)
            super().start(argv, cwd, env, cols, rows)

        def write(self, data):
            super().write(data)
            typed.set()

    entry = {
        "id": "term_r1", "shell": "sh", "argv": ["sh"], "cwd": str(tmp_path),
        "cols": 80, "rows": 24,
        "scrollback_b64": base64.b64encode(b"old prompt> ").decode("ascii"),
    }
    s = m.restore(entry, backend=_Gated())
    old = s.backend
    try:
        with c.websocket_connect(f"/terminals/{s.id}/ws") as ws:
            ws.send_text(json.dumps({"type": "resize", "cols": 120, "rows": 30}))
            assert entered.wait(_BOUND_S), "the second start never began"
            ws.send_text(json.dumps({"type": "resize", "cols": 130, "rows": 32}))
            ws.send_text("x")
            assert typed.wait(_BOUND_S), (
                "the key typed during the second start was not read until it ended"
            )
            assert not gate.is_set() and s._restarting
            gate.set()
            assert _until(lambda: not s._restarting)
            assert _until(lambda: (old.cols, old.rows) == (130, 32)), (old.cols, old.rows)
        assert s.backend is old, "the typing should have kept the old shell"
    finally:
        gate.set()
        s.kill()


def test_tm4_a_failed_second_start_is_said_once_per_streak(client, tmp_path, monkeypatch):
    """Two resizes, two failed second starts: the yellow line once.
    MUTATION: remove the once-guard in ``restart_at_size`` -> twice -> red."""
    m = _manager(client)
    starts = [0]

    class _Fails(FakeBackend):
        def start(self, argv, cwd, env, cols, rows):
            starts[0] += 1
            if starts[0] > 1:
                raise RuntimeError("the console host did not start")
            super().start(argv, cwd, env, cols, rows)

    entry = {"id": "term_r2", "shell": "sh", "argv": ["sh"], "cwd": str(tmp_path),
             "cols": 80, "rows": 24}
    s = m.restore(entry, backend=_Fails())
    try:
        assert s.restart_at_size(120, 30) is False
        assert s.restart_wanted() is True
        assert s.restart_at_size(130, 32) is False
        assert starts[0] == 3, "the second resize did not try again"
        assert s.output_tail().count("could not be started again") == 1, s.output_tail()
    finally:
        s.kill()


# --------------------------------------------------------------------------- #
# TM5: a slow skill library is not a missing skill (decision D8)
# --------------------------------------------------------------------------- #
def test_tm5_a_slow_skill_library_answers_503_in_words_and_asks_nothing(client, monkeypatch):
    """MUTATION: ``fallback=None`` -> 404 "no such skill" -> red."""
    c = client
    monkeypatch.setattr(routes_mod, "ASSIST_READ_MAX_S", 0.05)
    release = threading.Event()
    platform = c.app.state.platform
    asked: list = []

    def _slow_get(name):
        release.wait(30)
        return None

    async def _one_shot(*a, **k):
        asked.append(a)
        raise AssertionError("a model was asked")

    monkeypatch.setattr(platform.skills, "get", _slow_get)
    monkeypatch.setattr(c.app.state.d, "_one_shot_complete", _one_shot)
    term = c.post("/terminals", json={}).json()
    try:
        r = c.post(f"/terminals/{term['id']}/ai",
                   json={"prompt": "what happened?", "skill": "report-writer"})
        assert r.status_code == 503, r.text
        assert r.json()["detail"] == SKILL_SLOW
        assert asked == []
    finally:
        release.set()


def test_tm5_a_missing_skill_stays_404(client, monkeypatch):
    c = client
    monkeypatch.setattr(c.app.state.platform.skills, "get", lambda name: None)
    term = c.post("/terminals", json={}).json()
    r = c.post(f"/terminals/{term['id']}/ai",
               json={"prompt": "what happened?", "skill": "report-writer"})
    assert r.status_code == 404
    assert r.json()["detail"] == "no such skill: report-writer"
