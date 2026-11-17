"""Third north-star fix plan, group 20 (what the screen says is what happens):
the Build terminals' TM4, TM16 and TM5.

Driven through the REAL app factory and the REAL terminals manager, with a
recording stand-in shell (``FakeBackend``) and the recording stand-in for the
account manager (``tests/fixtures/fake_iron_proxy_v1301.py``). No real coding
tool runs: the launch recipes' probes are stubbed (``conftest`` answers "not
installed"; ``installed_claude`` below answers a build with every option).

* TM4 (decision G15): "Continue on X" goes to X, or refuses and names the
  account it would use instead. A request with no account named keeps the
  daemon's own pick.
* TM16: a refused Continue sends the account manager no signal; a failure
  after the signal no longer refuses.
* TM5 (decision G16): a "with Iron Jarvis's tools" launch is made WITH the
  ticked boxes, the recipe is set up and the tool is started; with none
  ticked, or a tool whose recipe cannot be set up on this PC, nothing is
  opened and no token is minted; the catalog says "ready" only where true.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.browser.panetokens import MCP_TOKEN_ENV, MCP_URL_ENV
from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals import recipes
from iron_jarvis.terminals.backend import FakeBackend

FIXTURE = Path(__file__).parent / "fixtures" / "fake_iron_proxy_v1301.py"
sys.path.insert(0, str(FIXTURE.parent))
from fake_iron_proxy_v1301 import FEATURES_PICK_PROFILE, FakeError, FakeIronProxy  # noqa: E402

LIMIT_LINE = "You've hit your session limit · resets 3:45pm (America/New_York)"
FOLDER = "C--Users-someone-projects-a-report"


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
def fake(tmp_path):
    f = FakeIronProxy(tmp_path / "ipd", features=list(FEATURES_PICK_PROFILE)).start()
    yield f
    f.stop()


@pytest.fixture
def client(proj):
    with TestClient(create_app(str(proj))) as c:
        yield c


def _enable(c) -> None:
    r = c.post("/iron-proxy/enable")
    assert r.status_code == 200 and r.json()["status"]["running"] is True, r.text


def _refresh(c) -> None:
    assert c.get("/iron-proxy?discover=0").status_code == 200


def _session(c, pane_id: str):
    return c.app.state.platform.terminals.get(pane_id)


def _print(c, pane_id: str, *lines: str) -> None:
    _session(c, pane_id)._ingest(("\r\n".join(lines) + "\r\n").encode("utf-8"))


def _signals(fake) -> list[tuple[str, dict]]:
    return [(path, body) for _m, path, body in fake.calls if path.endswith("/signal")]


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_session(home: str, sid: str, cwd: Path) -> Path:
    path = Path(home) / "projects" / FOLDER / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    first = {"type": "user", "timestamp": _iso(now - timedelta(minutes=30)),
             "message": {"role": "user", "content": "tidy up the report notes"}}
    last = {"type": "assistant", "timestamp": _iso(now), "cwd": str(cwd), "sessionId": sid,
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Working."}]}}
    path.write_text("\n".join(json.dumps(r) for r in (first, last)) + "\n", encoding="utf-8")
    return path


def _three_accounts(fake):
    """Three Claude accounts in this order: the pane's own, then the one the
    daemon picks by itself, then the one the page will name."""
    work = fake.add_profile("anthropic", "Work Max")
    personal = fake.add_profile("anthropic", "Personal")
    spare = fake.add_profile("anthropic", "Spare")
    return work, personal, spare


def _limited_pane(c, fake, repo, work) -> tuple[dict, str, Path]:
    """A Claude pane on ``work`` (started from Launch, so its conversation id
    is recorded) that has hit its limit, with its conversation on disk."""
    old = c.post("/terminals/launch",
                 json={"cli": "claude", "account": work["id"], "cwd": str(repo)}).json()
    sid = old["claude_session_id"]
    source = _write_session(work["cli"]["home"], sid, repo)
    _print(c, old["id"], "⏺ Working on it.", f"  ⎿  {LIMIT_LINE}", "│ > │")
    return old, sid, source


def _park(fake, pid: str) -> None:
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    fake.states[pid].update(
        {"status": "parked", "parkedUntil": until, "parkedReason": {"kind": "rate-limit"}}
    )


# --------------------------------------------------------------------------- #
# TM4: "Continue on X" goes to X
# --------------------------------------------------------------------------- #
def test_continue_goes_to_the_account_the_page_named(client, fake, repo):
    """Three accounts; the strip names "Spare", the daemon by itself would take
    "Personal" (first free in order). The conversation is copied to, and
    resumed under, Spare. Pins ``continue_on.prepare``'s named-account lease
    (``lease = named``) and the route passing ``body.account`` through.
    MUTATION: ignore the named account -> to == Personal -> red."""
    c = client
    _enable(c)
    work, personal, spare = _three_accounts(fake)
    _refresh(c)
    old, sid, source = _limited_pane(c, fake, repo, work)

    r = c.post(f"/terminals/{old['id']}/continue-on-next", json={"account": spare["id"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["to"] == {"id": spare["id"], "title": "Spare"}
    assert out["resumed"] is True and out["session_id"] == sid
    assert out["note"].startswith('Carried your conversation over to "Spare".')
    pane = out["pane"]
    assert pane["accounts"]["anthropic"]["id"] == spare["id"]
    be = _backend_of(pane["id"])
    assert be.env["CLAUDE_CONFIG_DIR"] == spare["cli"]["home"]
    assert be.written == [f"claude --resume {sid}\r"]
    copied = Path(spare["cli"]["home"]) / "projects" / FOLDER / f"{sid}.jsonl"
    assert copied.read_bytes() == source.read_bytes()
    assert not (Path(personal["cli"]["home"]) / "projects").exists(), (
        "the conversation was copied to an account the user did not agree to"
    )


def test_a_named_account_that_cannot_take_over_is_refused_naming_the_other(client, fake, repo):
    """The named account is parked by now: a 409 sentence that names it AND the
    account that could take over instead (``instead`` carries it for the
    strip's next press). Nothing copied, no new terminal, no signal sent.
    MUTATION: fall back to the daemon's own pick -> 200 on Personal -> red."""
    c = client
    _enable(c)
    work, personal, spare = _three_accounts(fake)
    _park(fake, spare["id"])
    _refresh(c)
    old, _sid, _source = _limited_pane(c, fake, repo, work)
    shells = len(_RecordingBackend.instances)

    r = c.post(f"/terminals/{old['id']}/continue-on-next", json={"account": spare["id"]})
    assert r.status_code == 409, r.text
    body = r.json()
    assert '"Spare"' in body["detail"] and '"Personal"' in body["detail"]
    assert "nothing was copied" in body["detail"]
    assert body["instead"] == {"id": personal["id"], "title": "Personal"}
    assert _signals(fake) == []
    assert len(_RecordingBackend.instances) == shells
    for acct in (personal, spare):
        assert not (Path(acct["cli"]["home"]) / "projects").exists()


def test_the_account_the_terminal_is_already_on_is_refused(client, fake, repo):
    c = client
    _enable(c)
    work, personal, _spare = _three_accounts(fake)
    _refresh(c)
    old, _sid, _source = _limited_pane(c, fake, repo, work)
    r = c.post(f"/terminals/{old['id']}/continue-on-next", json={"account": work["id"]})
    assert r.status_code == 409
    assert r.json()["instead"] == {"id": personal["id"], "title": "Personal"}
    assert _signals(fake) == []


def test_no_account_named_keeps_the_daemons_own_pick(client, fake, repo):
    """An older page sends no body: today's behaviour (the first free account)."""
    c = client
    _enable(c)
    work, personal, _spare = _three_accounts(fake)
    _refresh(c)
    old, _sid, _source = _limited_pane(c, fake, repo, work)
    r = c.post(f"/terminals/{old['id']}/continue-on-next")
    assert r.status_code == 200, r.text
    assert r.json()["to"] == {"id": personal["id"], "title": "Personal"}


# --------------------------------------------------------------------------- #
# TM16: a refused Continue sends no signal
# --------------------------------------------------------------------------- #
def test_a_continue_that_is_refused_has_sent_no_signal(client, fake, repo, monkeypatch):
    """The account manager's pick fails AFTER the limit was signalled. Before
    the fix the press was refused then, with the account already reported as
    limited. Now nothing can refuse after the signal: the account read before
    it (the first free one) takes over. Pins ``continue_on.prepare``'s pick
    failure branch (``lease = None`` -> the fallback, no refusal).
    MUTATION: raise ContinueRefused there again -> 409 with a signal -> red."""
    c = client
    _enable(c)
    work, personal, _spare = _three_accounts(fake)
    _refresh(c)
    old, _sid, _source = _limited_pane(c, fake, repo, work)

    def _broken_pick(provider, lane="cli"):
        raise FakeError("INTERNAL_ERROR", "The account manager hit a problem.", status=500)

    monkeypatch.setattr(fake, "pick", _broken_pick)
    r = c.post(f"/terminals/{old['id']}/continue-on-next")
    sent = _signals(fake)
    assert not (r.status_code != 200 and sent), (
        f"Continue was refused ({r.status_code}: {r.text}) after the account manager "
        "had already been told the account is limited"
    )
    assert r.status_code == 200, r.text
    assert r.json()["to"] == {"id": personal["id"], "title": "Personal"}
    assert len(sent) == 1


# --------------------------------------------------------------------------- #
# TM5: "With Iron Jarvis's tools" opens a terminal set up and starts the tool
# --------------------------------------------------------------------------- #
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


def _tokens(c) -> int:
    return len(c.app.state.platform.terminals.pane_tokens)


def test_a_with_tools_launch_is_set_up_and_starts_the_tool(client, repo, installed_claude):
    """The boxes ticked on the page reach the new terminal at creation; the
    recipe writes its configuration; the tool's start command (with the
    recipe's own options) is typed and Enter pressed. Pins the route
    ``/terminals/launch-with-tools`` (capabilities + recipe at create) and
    ``launch_pane``'s ``argv_extra`` typing. MUTATION: drop the capabilities
    -> no token -> red; drop the typing -> nothing written -> red."""
    c = client
    near = c.post("/terminals", json={"cwd": str(repo)}).json()
    r = c.post("/terminals/launch-with-tools",
               json={"cli": "claude", "near": near["id"], "capabilities": {"memory": True}})
    assert r.status_code == 200, r.text
    out = r.json()
    pane = out["pane"]
    assert pane["cwd"] == str(repo)
    be = _backend_of(pane["id"])
    token = be.env[MCP_TOKEN_ENV]
    assert be.env[MCP_URL_ENV]
    grant = c.app.state.platform.terminals.pane_tokens.resolve(token)
    assert grant is not None and grant.pane_id == pane["id"] and grant.allows("memory")
    assert not grant.allows("browser"), "a box the user did not tick was granted"
    assert (repo / ".mcp.json").is_file()
    sid = pane["claude_session_id"]
    [typed] = be.written
    assert typed.startswith(
        f"claude --session-id {sid} --mcp-config .mcp.json --strict-mcp-config --disallowed-tools "
    ), typed
    assert "WebFetch,WebSearch" in typed and typed.endswith("\r")
    assert out["tools"]["set_up"] is True


def test_with_no_box_ticked_no_terminal_is_opened(client, repo, installed_claude):
    """Ticking a box is the consent; none ticked means the row's promise cannot
    be kept: a plain sentence, no terminal, no token."""
    c = client
    near = c.post("/terminals", json={"cwd": str(repo)}).json()
    shells = len(_RecordingBackend.instances)
    r = c.post("/terminals/launch-with-tools",
               json={"cli": "claude", "near": near["id"], "capabilities": {}})
    assert r.status_code == 409
    assert "Tick Memory or Browser" in r.json()["detail"]
    assert len(_RecordingBackend.instances) == shells
    assert _tokens(c) == 0


def test_a_tool_that_cannot_be_set_up_is_refused_and_no_token_is_minted(client, repo):
    """On this PC (the stubbed probes) Claude Code cannot be set up: the route
    says why in the recipe's own words, opens nothing and mints nothing.
    Pins the manager's dry run BEFORE the mint (``_prepare_recipe``)."""
    c = client
    near = c.post("/terminals", json={"cwd": str(repo)}).json()
    shells = len(_RecordingBackend.instances)
    r = c.post("/terminals/launch-with-tools",
               json={"cli": "claude", "near": near["id"], "capabilities": {"browser": True}})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "Claude Code" in detail and "no terminal was opened" in detail
    assert len(_RecordingBackend.instances) == shells
    assert _tokens(c) == 0


def test_no_token_is_minted_before_the_recipe_answers(client, repo):
    """The older door (``POST /terminals`` with a recipe): a recipe that answers
    "cannot be set up here" leaves the terminal opening as a plain one, with no
    token minted for it. MUTATION: mint before the dry run -> a token -> red."""
    c = client
    r = c.post("/terminals",
               json={"cwd": str(repo), "recipe": "claude", "capabilities": {"browser": True}})
    assert r.status_code == 200, r.text
    be = _backend_of(r.json()["id"])
    assert MCP_TOKEN_ENV not in be.env
    assert _tokens(c) == 0


def test_the_catalog_says_ready_only_where_true(monkeypatch, installed_claude):
    """``recipe_state`` (what the Launch menu reads) carries ``ready``: true for
    a Claude Code that can be set up; false, with the plain reason, for a Pi
    whose adapter still has to be run by hand, and for a tool that cannot be
    set up at all."""
    claude = recipes.recipe_state("claude")
    assert claude["ready"] is True

    monkeypatch.setattr(recipes.PiRecipe, "runtime", lambda self: "/fake/node")
    pi = recipes.recipe_state("pi")
    assert pi["ok"] is True and pi["ready"] is False
    assert any("does not load the Iron Jarvis adapter on its own" in s for s in pi["limitations"])

    monkeypatch.setattr(recipes, "_find", lambda command: None)
    recipes.clear_probe_cache()
    gone = recipes.recipe_state("claude")
    assert gone["ready"] is False and gone["limitations"]
