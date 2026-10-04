"""An account that signs in is SEEN to be signed in; an old Iron-Proxy an Iron
Jarvis started is replaced (v1.303.3, two LIVE bugs + the review).

1. The user added a Claude account, pressed Sign in, logged in in the Build
   pane — the login landed in the account's home — and the card said "Needs
   sign-in" forever: Iron-Proxy set the state ``unauthenticated`` at creation
   and nothing asked it to look again. Now:
   * ``IronProxyClient.refresh`` (``POST /iron/refresh {id}``);
   * ``GET /iron-proxy`` re-checks an account ONLY while a sign-in is in
     progress for it (its sign-in pane open, or Sign in pressed in the last
     3 minutes) — throttled to once per 10 s per account, off the loop, no
     wait (the answer lands in the next read). Iron-Proxy's re-check reads the
     LOCAL credentials file, so re-checking every signed-out account flipped
     one a real 401 had just signed out back to ready (the review's 401 loop);
   * the sign-in pane watches for Claude Code's OWN success shape and has
     Iron-Proxy re-check at once (first sighting forced, then throttled,
     capped per pane); ``signed_in: true`` on its rows once confirmed;
   * ``POST /iron-proxy/accounts/{id}/check`` re-checks on demand.
   Never on availability or a list route.
2. The daemon REUSED an Iron-Proxy an earlier Iron Jarvis had started from an
   older bundle (no "pick-profile"). Now a located proxy that lacks a feature
   the bundle has is stopped and replaced — ONLY when its command line runs an
   Iron Jarvis bundle (THIS repo's, or an install's); never a pid twice, never
   the same (bundle, version) twice, at most twice in 10 minutes (two Iron
   Jarvis copies sharing one data dir), and never when the kill did not take.

Driven through the REAL app factory and the fake Iron-Proxy; process command
lines and kills are faked (nothing real is ever killed).
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.iron_proxy import service as svc_mod
from iron_jarvis.iron_proxy.client import IronProxyClient
from iron_jarvis.iron_proxy.service import OUTDATED, IronProxyService, is_iron_jarvis_bundle
from iron_jarvis.terminals import signin_watch
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.signin_watch import login_succeeded

FIXTURE = Path(__file__).parent / "fixtures" / "fake_iron_proxy_v1301.py"
sys.path.insert(0, str(FIXTURE.parent))
from fake_iron_proxy_v1301 import FEATURES_PICK_PROFILE, FakeIronProxy  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", FakeBackend)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    yield


@pytest.fixture
def fake(tmp_path):
    f = FakeIronProxy(tmp_path / "ipd", features=list(FEATURES_PICK_PROFILE)).start()
    yield f
    f.stop()


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    with TestClient(create_app(str(root))) as c:
        yield c


def _enable(c) -> None:
    r = c.post("/iron-proxy/enable")
    assert r.status_code == 200 and r.json()["status"]["running"] is True, r.text


def _signed_out_account(c, fake, title="TSI") -> str:
    """An account as Iron-Proxy creates one: state ``unauthenticated``, its
    login not in place yet."""
    acct = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": title}).json()
    fake.states[acct["id"]]["status"] = "unauthenticated"
    fake.login_ok[acct["id"]] = False
    return acct["id"]


def _refreshes(fake) -> list:
    return [body for m, path, body in fake.calls if path == "/iron/refresh"]


def _card(c, pid: str, light: bool = True) -> dict:
    accounts = c.get("/iron-proxy?discover=0" if light else "/iron-proxy").json()["accounts"]
    return next(a for a in accounts if a["id"] == pid)


def _until(fn, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    got = fn()
    while not got and time.monotonic() < deadline:
        time.sleep(0.05)
        got = fn()
    return got


# --------------------------------------------------------------------------- #
# 1. the client
# --------------------------------------------------------------------------- #
def test_client_refresh_posts_the_id_and_answers_the_states(fake):
    """MUTATION: send no id -> every account re-checked -> red."""
    a = fake.add_profile("anthropic", "A")
    b = fake.add_profile("anthropic", "B")
    fake.login_ok[a["id"]] = False
    c = IronProxyClient(fake.url, fake.token)
    out = c.refresh(a["id"])
    assert out == [{"profileId": a["id"], "status": "unauthenticated", "served": 0}]
    assert _refreshes(fake) == [{"id": a["id"]}]
    everyone = c.refresh()
    assert {s["profileId"] for s in everyone} == {a["id"], b["id"]}
    assert _refreshes(fake)[-1] == {}


# --------------------------------------------------------------------------- #
# 2. the card read re-checks ONLY a sign-in in progress
# --------------------------------------------------------------------------- #
def test_a_401_signalled_account_stays_signed_out_across_card_reads(client, fake):
    """The review's 401 loop: a real request was refused (revoked token), the
    local credentials still say "logged in", and a re-check would flip it back
    to ready within 10 s. Nobody is signing in: it is never re-checked.
    MUTATION: re-check every signed-out account -> red."""
    c = client
    _enable(c)
    a = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "Work"}).json()
    fake.signal(a["id"], {"status": 401})  # a real request: AUTH_REQUIRED
    for _ in range(3):
        assert _card(c, a["id"])["state"]["status"] == "unauthenticated"
        assert _card(c, a["id"], light=False)["state"]["status"] == "unauthenticated"
    assert _refreshes(fake) == []
    assert c.app.state.platform.iron_proxy.has_usable_account("claude-cli") is False


@pytest.mark.parametrize("light", [True, False])
def test_with_a_sign_in_in_progress_the_login_is_seen(client, fake, monkeypatch, light):
    """The LIVE bug: Sign in pressed, the login landed, the card stuck on
    "Needs sign-in". With the sign-in in progress the card's reads re-check it
    (by id) and the next read shows ``ready``. MUTATION: no re-check in the
    view -> red. MUTATION: re-check without an id -> red."""
    monkeypatch.setattr(svc_mod, "REFRESH_EVERY_S", 0.0)
    c = client
    _enable(c)
    pid = _signed_out_account(c, fake)
    assert c.post(f"/iron-proxy/accounts/{pid}/signin").status_code == 200
    assert _card(c, pid, light)["state"]["status"] == "unauthenticated"
    fake.login_ok[pid] = True  # the user logs in in the sign-in pane
    assert _until(lambda: _card(c, pid, light)["state"]["status"] == "ready")
    assert _refreshes(fake) and all(body == {"id": pid} for body in _refreshes(fake))
    svc = c.app.state.platform.iron_proxy
    assert _until(lambda: svc.cached_accounts()[1][pid]["status"] == "ready")
    assert svc.has_usable_account("claude-cli") is True


def test_a_sign_in_press_counts_for_three_minutes(client, fake, monkeypatch):
    """The pane closed but Sign in was pressed recently: still re-checked;
    past the window, not. MUTATION: drop the press record -> red."""
    monkeypatch.setattr(svc_mod, "REFRESH_EVERY_S", 0.0)
    c = client
    _enable(c)
    pid = _signed_out_account(c, fake)
    pane = c.post(f"/iron-proxy/accounts/{pid}/signin").json()["terminal_id"]
    assert c.delete(f"/terminals/{pane}").status_code in (200, 204)
    _card(c, pid)
    assert _until(lambda: len(_refreshes(fake)) == 1)
    monkeypatch.setattr(svc_mod, "SIGNIN_WINDOW_S", 0.0)
    _card(c, pid)
    time.sleep(0.3)
    assert len(_refreshes(fake)) == 1


def test_an_open_sign_in_pane_counts_even_after_the_window(client, fake, monkeypatch):
    """MUTATION: ignore the open panes -> red."""
    monkeypatch.setattr(svc_mod, "REFRESH_EVERY_S", 0.0)
    monkeypatch.setattr(svc_mod, "SIGNIN_WINDOW_S", 0.0)
    c = client
    _enable(c)
    pid = _signed_out_account(c, fake)
    c.post(f"/iron-proxy/accounts/{pid}/signin")
    _card(c, pid)
    assert _until(lambda: len(_refreshes(fake)) == 1)


def test_the_re_check_is_throttled_per_account(client, fake):
    """At most once per 10 s per account. MUTATION: no throttle -> red."""
    c = client
    _enable(c)
    pid = _signed_out_account(c, fake)
    c.post(f"/iron-proxy/accounts/{pid}/signin")
    for _ in range(4):
        _card(c, pid)
        time.sleep(0.05)
    time.sleep(0.2)
    assert _refreshes(fake) == [{"id": pid}]


def test_a_ready_account_is_never_re_checked(client, fake):
    """MUTATION: re-check any account being signed in, whatever its state -> red."""
    c = client
    _enable(c)
    a = c.post("/iron-proxy/accounts", json={"provider": "anthropic", "title": "Ready"}).json()
    c.post(f"/iron-proxy/accounts/{a['id']}/signin")
    _card(c, a["id"])
    time.sleep(0.3)
    assert _refreshes(fake) == []


def test_the_card_read_never_waits_and_a_slow_check_lands_later(client, fake):
    """The card polls every second during a sign-in: the view does not wait
    for the check (review 8); the answer lands in the snapshot and the next
    read. MUTATION: wait in the view -> red. MUTATION: drop the merge -> red."""
    c = client
    _enable(c)
    pid = _signed_out_account(c, fake)
    c.post(f"/iron-proxy/accounts/{pid}/signin")
    fake.login_ok[pid] = True
    fake.refresh_delay = 0.8
    started = time.monotonic()
    assert _card(c, pid)["state"]["status"] == "unauthenticated"
    assert time.monotonic() - started < 0.7  # did not wait for the slow check
    svc = c.app.state.platform.iron_proxy
    assert _until(lambda: svc.cached_accounts()[1][pid]["status"] == "ready")
    assert _card(c, pid)["state"]["status"] == "ready"
    assert _refreshes(fake) == [{"id": pid}]


def test_the_view_re_checks_off_the_event_loop(client, fake, monkeypatch):
    """Review 3: the re-check (and the pane scan) run on a worker thread.
    MUTATION: call it on the loop -> red."""
    c = client
    _enable(c)
    _signed_out_account(c, fake)
    svc = c.app.state.platform.iron_proxy
    seen: list[str] = []

    def probe(*_a, **_k):
        try:
            asyncio.get_running_loop()
            seen.append("loop")
        except RuntimeError:
            seen.append("thread")
        return {}

    monkeypatch.setattr(svc, "recheck_signed_out", probe)
    c.get("/iron-proxy?discover=0")
    assert seen == ["thread"]


def test_availability_and_list_routes_never_re_check(client, fake):
    """MUTATION: re-check from a list route -> red."""
    c = client
    _enable(c)
    pid = _signed_out_account(c, fake)
    c.post(f"/iron-proxy/accounts/{pid}/signin")
    before = len(_refreshes(fake))
    c.get("/terminals")
    c.get("/terminals/activity")
    c.get("/health")
    c.get("/models")
    assert len(_refreshes(fake)) == before


# --------------------------------------------------------------------------- #
# 4. "Check again"
# --------------------------------------------------------------------------- #
def test_check_again_re_checks_now_and_answers_the_row(client, fake):
    """Any account, any time — the user asked. MUTATION: honour the throttle
    on the manual check -> red."""
    c = client
    _enable(c)
    pid = _signed_out_account(c, fake)
    assert c.post(f"/iron-proxy/accounts/{pid}/check").json()["state"]["status"] == "unauthenticated"
    fake.login_ok[pid] = True
    r = c.post(f"/iron-proxy/accounts/{pid}/check")
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["id"] == pid and row["title"] == "TSI" and row["state"]["status"] == "ready"
    assert len(_refreshes(fake)) == 2
    assert c.app.state.platform.iron_proxy.has_usable_account("claude-cli") is True


def test_check_again_on_an_unknown_account_is_404(client, fake):
    c = client
    _enable(c)
    assert c.post("/iron-proxy/accounts/prof_nope/check").status_code == 404


def test_check_again_with_iron_proxy_off_is_409(client):
    assert client.post("/iron-proxy/accounts/prof_x/check").status_code == 409


# --------------------------------------------------------------------------- #
# 3. the sign-in pane notices its own login
# --------------------------------------------------------------------------- #
#: The shapes claude 2.1.289 prints (read from its binary — see signin_watch).
AUTH_LOGIN_DONE = "Login successful."
SLASH_LOGIN_DONE = "Login successful. Press Enter to continue…"


@pytest.mark.parametrize(
    "tail,expected",
    [
        (f"Opening browser to sign in…\n{AUTH_LOGIN_DONE}\nPS C:\\x> ", True),
        (f"Logged in as vr@example.test\n{SLASH_LOGIN_DONE}", True),
        (f"│ Logged in as vr@example.test │\n│ {SLASH_LOGIN_DONE} │", True),
        (SLASH_LOGIN_DONE, False),  # the pair, not half of it
        ("Login successful", False),  # Claude's line has the period
        (f"  ⎿  {AUTH_LOGIN_DONE}", False),  # a tool result
        (f"> {AUTH_LOGIN_DONE}", False),  # the user typed it
        (f"│ > {AUTH_LOGIN_DONE} │", False),  # inside the input box
        (f"❯ {AUTH_LOGIN_DONE}", False),
        (f"PS C:\\x> echo Login successful.\n{AUTH_LOGIN_DONE}\nPS C:\\x> ", False),  # echo
        ("Authentication successful. Connected to github.", False),  # MCP auth
        ("Login failed: invalid code", False),
    ],
)
def test_the_login_success_shape_is_claude_codes_own(tail, expected):
    """MUTATION: accept a ⎿/> row -> red. MUTATION: no echo guard -> red."""
    assert login_succeeded(tail, "anthropic") is expected


def test_other_providers_are_not_watched_by_words():
    assert login_succeeded(AUTH_LOGIN_DONE, "openai") is False


def _activity(c, pane_id: str) -> dict:
    return {p["id"]: p for p in c.get("/terminals/activity").json()["panes"]}[pane_id]


def _poll(c, pane: str, n: int = 6, gap: float = 0.05) -> dict:
    row = {}
    for _ in range(n):
        row = _activity(c, pane)
        time.sleep(gap)
    return row


def test_the_sign_in_pane_sees_the_login_and_iron_proxy_confirms_it(client, fake, monkeypatch):
    """First sighting: an immediate (forced) re-check. "Not yet" -> the next
    one waits for the throttle (review 2: it fired on EVERY new output before).
    Then ``signed_in: true`` once Iron-Proxy says ready, and the snapshot is
    read fresh. MUTATION: no watch -> red. MUTATION: force every sighting ->
    red. MUTATION: signed_in from the words alone -> red."""
    monkeypatch.setattr(svc_mod, "SIGNIN_WINDOW_S", 0.0)  # isolate the pane's own checks
    c = client
    _enable(c)
    pid = _signed_out_account(c, fake)
    pane = c.post(f"/iron-proxy/accounts/{pid}/signin").json()["terminal_id"]
    row = _activity(c, pane)
    assert row["signin_for"] == pid and "signed_in" not in row
    sess = c.app.state.platform.terminals.get(pane)
    sess._ingest(b"Opening browser to sign in...\r\n")
    _poll(c, pane)
    assert _refreshes(fake) == []  # nothing to re-check yet

    sess._ingest(f"{AUTH_LOGIN_DONE}\r\n".encode())  # the words, the login not yet in place
    assert _until(lambda: _activity(c, pane) is not None and len(_refreshes(fake)) == 1)
    _poll(c, pane)  # the forced check comes back "not yet"
    for i in range(5):  # more output keeps arriving: no forced re-check per output
        sess._ingest(f"line {i}\r\n".encode())
        _activity(c, pane)
        time.sleep(0.05)
    assert len(_refreshes(fake)) == 1 and "signed_in" not in _activity(c, pane)

    fake.login_ok[pid] = True
    svc = c.app.state.platform.iron_proxy
    svc._accounts_cache = None  # e.g. just restarted: the confirmed sign-in reads a fresh one
    svc._usable = {}
    monkeypatch.setattr(svc_mod, "REFRESH_EVERY_S", 0.0)  # the throttle has passed
    row = _until(lambda: _activity(c, pane).get("signed_in") and _activity(c, pane))
    assert row and row["signed_in"] is True
    assert len(_refreshes(fake)) == 2 and _refreshes(fake)[-1] == {"id": pid}
    assert svc.cached_accounts()[1][pid]["status"] == "ready"
    assert svc.has_usable_account("claude-cli") is True
    full = {p["id"]: p for p in c.get("/terminals").json()["terminals"]}[pane]
    assert full["signin_for"] == pid and full["signed_in"] is True


def test_a_sign_in_pane_asks_a_bounded_number_of_times(client, fake, monkeypatch):
    """Review 2: a login that never takes must not re-check forever.
    MUTATION: no cap -> red."""
    monkeypatch.setattr(svc_mod, "SIGNIN_WINDOW_S", 0.0)
    monkeypatch.setattr(svc_mod, "REFRESH_EVERY_S", 0.0)
    c = client
    _enable(c)
    pid = _signed_out_account(c, fake)
    pane = c.post(f"/iron-proxy/accounts/{pid}/signin").json()["terminal_id"]
    c.app.state.platform.terminals.get(pane)._ingest(f"{AUTH_LOGIN_DONE}\r\n".encode())
    _poll(c, pane, n=40, gap=0.02)
    _poll(c, pane, n=10, gap=0.05)
    assert len(_refreshes(fake)) == signin_watch.MAX_CHECKS


def test_an_ordinary_pane_has_no_sign_in_keys(client):
    pane = client.post("/terminals", json={}).json()["id"]
    row = _activity(client, pane)
    assert "signin_for" not in row and "signed_in" not in row


# --------------------------------------------------------------------------- #
# 5. an outdated Iron-Proxy that an Iron Jarvis started is replaced
# --------------------------------------------------------------------------- #
REPO_BUNDLE = str(svc_mod._repo_bundle_dir() / "iron-proxy.mjs")


@pytest.fixture
def install(tmp_path):
    """An Iron Jarvis install: ``Iron Jarvis.exe`` beside ``resources/``."""
    root = tmp_path / "Programs" / "Iron Jarvis"
    (root / "resources" / "iron-proxy").mkdir(parents=True)
    (root / "Iron Jarvis.exe").write_bytes(b"MZ")
    return root


def test_only_an_iron_jarvis_bundle_command_line_is_ours(tmp_path, install):
    """Review 6: the bundle is the FIRST script argument after the runtime and
    lives in THIS repo's vendor folder or an install's resources folder.
    MUTATION: match any iron-proxy.mjs -> red. MUTATION: skip the exe check
    -> red."""
    packaged = str(install / "resources" / "iron-proxy" / "iron-proxy.mjs")
    no_exe = tmp_path / "Fake" / "resources" / "iron-proxy"
    no_exe.mkdir(parents=True)
    ours = [
        ["node", REPO_BUNDLE, "serve", "--port", "0"],
        ["node", "--no-warnings", REPO_BUNDLE, "serve"],
        [str(install / "Iron Jarvis.exe"), packaged, "serve"],
    ]
    not_ours = [
        [r"C:\Program Files\Iron-Proxy\Iron-Proxy.exe"],  # the tray app
        ["node", r"C:\src\Iron-Proxy\packages\cli\dist-bundle\iron-proxy.mjs", "serve"],
        ["node", r"C:\other\Iron-Jarvis\desktop\vendor\iron-proxy\iron-proxy.mjs", "serve"],
        ["node", str(no_exe / "iron-proxy.mjs"), "serve"],  # no Iron Jarvis.exe there
        ["node", r"C:\tools\wrap.js", REPO_BUNDLE, "serve"],  # not the FIRST script
        ["iron-proxy", "serve"],
        [],
    ]
    for cmd in ours:
        assert is_iron_jarvis_bundle(cmd) is True, cmd
    for cmd in not_ours:
        assert is_iron_jarvis_bundle(cmd) is False, cmd


def _cfg(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return SimpleNamespace(home=home, iron_proxy_enabled=True)


@pytest.fixture
def bundle_with_pick_profile(tmp_path, monkeypatch):
    """The BUNDLED Iron-Proxy advertises pick-profile (read from its text)."""
    bundle = tmp_path / "bundle" / "iron-proxy.mjs"
    bundle.parent.mkdir()
    bundle.write_text('const features = ["executor-v1", "pick-profile"];\n', encoding="utf-8")
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(bundle))
    return bundle


class _Procs:
    """Fake processes: the stale proxies' pids read as ``cmdline``; a kill is
    recorded and (unless ``survive``) makes the pid read as dead."""

    def __init__(self, monkeypatch, cmdline: list[str], *, survive: bool = False) -> None:
        self.cmdline = cmdline
        self.killed: list[int] = []
        self.stale: set[int] = set()
        self.survive = survive
        monkeypatch.setattr(svc_mod, "_cmdline", lambda pid: list(cmdline) if pid in self.stale else [])
        monkeypatch.setattr(svc_mod, "_kill_pids", lambda pids: self.killed.extend(int(p) for p in pids))
        monkeypatch.setattr(
            svc_mod, "_pid_alive", lambda pid: self.survive or int(pid) not in self.killed
        )

    def stale_proxy(self, tmp_path, pid: int, version: str = "0.0.9") -> FakeIronProxy:
        self.stale.add(pid)
        return FakeIronProxy(tmp_path / "ipd", features=["executor-v1"],
                             health_version=version).start(pid=pid)


def _fake_spawn(tmp_path, started: list):
    """Starting the current bundle = a fresh fake Iron-Proxy WITH pick-profile."""

    def spawn(self) -> None:
        new = FakeIronProxy(tmp_path / "ipd", features=list(FEATURES_PICK_PROFILE)).start(
            pid=800000 + len(started)
        )
        started.append(new)
        self._note_health(svc_mod._health(new.url))
        self._adopt(new.url, new.token, 800000 + len(started) - 1, owned=True)

    return spawn


@pytest.fixture
def started(tmp_path, monkeypatch):
    out: list = []
    monkeypatch.setattr(IronProxyService, "_spawn", _fake_spawn(tmp_path, out))
    yield out
    for f in out:
        f.stop()


@pytest.mark.parametrize("entry", ["start", "check"])
@pytest.mark.parametrize("where", ["repo", "install"])
def test_an_old_proxy_an_iron_jarvis_started_is_replaced(
    tmp_path, monkeypatch, bundle_with_pick_profile, started, install, entry, where
):
    """The LIVE bug: an old bundle's proxy (no pick-profile, no owned record)
    was reused. It is ours by its command line: stopped and the current
    bundle started. MUTATION: reuse it -> red."""
    bundle = REPO_BUNDLE if where == "repo" else str(install / "resources" / "iron-proxy" / "iron-proxy.mjs")
    procs = _Procs(monkeypatch, ["node", bundle, "serve", "--port", "0"])
    old = procs.stale_proxy(tmp_path, 700001)
    try:
        svc = IronProxyService(_cfg(tmp_path), register=False)
        getattr(svc, entry)()
        st = svc.status()
        assert procs.killed == [700001] and len(started) == 1
        assert st["running"] is True and st["error"] is None and st["owned"] is True
        assert svc.has_feature("pick-profile")
        svc.check()  # never twice: the new one is current
        assert procs.killed == [700001] and len(started) == 1
    finally:
        old.stop()


def test_a_proxy_that_does_not_die_is_left_and_said_once(
    tmp_path, monkeypatch, bundle_with_pick_profile, started
):
    """Review 5: the kill did not take — keep its descriptor, start nothing,
    say so; review 3: never try the same pid twice. MUTATION: no death check
    -> red. MUTATION: drop the once-per-pid guard -> red."""
    procs = _Procs(monkeypatch, ["node", REPO_BUNDLE, "serve"], survive=True)
    old = procs.stale_proxy(tmp_path, 700002)
    try:
        svc = IronProxyService(_cfg(tmp_path), register=False)
        svc.start()
        st = svc.status()
        assert procs.killed == [700002] and started == []
        assert st["running"] is True
        assert st["error"].startswith("Iron Jarvis could not stop the older Iron-Proxy (pid 700002)")
        assert (tmp_path / "ipd" / "proxy.json").is_file()
        for _ in range(3):
            svc.check()
        assert procs.killed == [700002] and started == []
        assert "pid 700002" in svc.status()["error"]
    finally:
        old.stop()


def test_two_iron_jarvis_copies_never_fight_forever(
    tmp_path, monkeypatch, bundle_with_pick_profile, started, install
):
    """Review 4: the other Iron Jarvis restarts ITS older proxy after we
    replaced it. The same (bundle, version) is not replaced twice, and after
    two replacements in 10 minutes we stop — said, naming the other copy.
    MUTATION: no per-(bundle, version) guard -> red. MUTATION: no window
    limit -> red."""
    other = str(install / "resources" / "iron-proxy" / "iron-proxy.mjs")
    procs = _Procs(monkeypatch, [str(install / "Iron Jarvis.exe"), other, "serve"])
    svc = IronProxyService(_cfg(tmp_path), register=False)
    fakes = []
    try:
        fakes.append(procs.stale_proxy(tmp_path, 700010, version="0.1.0"))
        svc.start()
        assert procs.killed == [700010] and len(started) == 1

        def other_restarts(pid: int, version: str):
            # The other copy takes the data dir back: whatever ran there is
            # gone, and its own older proxy answers now.
            for f in [*started, *fakes]:
                f.stop()
            fakes.append(procs.stale_proxy(tmp_path, pid, version=version))
            svc.check()

        other_restarts(700011, "0.1.0")  # the SAME bundle and version again
        assert procs.killed == [700010] and len(started) == 1
        err = svc.status()["error"]
        assert err.startswith("Another Iron Jarvis on this PC (") and str(install) in err

        other_restarts(700012, "0.1.1")  # a newer old one: replaced (2nd in the window)
        assert procs.killed == [700010, 700012] and len(started) == 2
        other_restarts(700013, "0.1.2")  # a third within 10 minutes: refused
        assert procs.killed == [700010, 700012] and len(started) == 2
        assert svc.status()["error"].startswith("Another Iron Jarvis on this PC (")
    finally:
        for f in fakes:
            f.stop()


@pytest.mark.parametrize(
    "cmdline",
    [
        [r"C:\Program Files\Iron-Proxy\Iron-Proxy.exe"],
        ["node", r"C:\src\Iron-Proxy\packages\cli\dist-bundle\iron-proxy.mjs", "serve"],
        [],
    ],
)
def test_an_old_proxy_someone_else_runs_is_never_stopped_and_says_so(
    tmp_path, monkeypatch, bundle_with_pick_profile, started, cmdline
):
    """MUTATION: replace any outdated proxy -> red. MUTATION: say nothing -> red."""
    procs = _Procs(monkeypatch, cmdline)
    old = procs.stale_proxy(tmp_path, 700020)
    try:
        svc = IronProxyService(_cfg(tmp_path), register=False)
        svc.start()
        svc.check()
        st = svc.status()
        assert procs.killed == [] and started == []
        assert st["running"] is True and st["owned"] is False
        if cmdline:
            # v1.303.3 final review: name the process, or "close it" is unactionable.
            from iron_jarvis.iron_proxy.service import OUTDATED_NAMED

            what = cmdline[1] if len(cmdline) > 1 else cmdline[0]
            assert st["error"] == OUTDATED_NAMED.format(pid=700020, what=what)
            assert "older than this Iron Jarvis needs" in st["error"]  # the card's banner keys on this
        else:
            assert st["error"] == OUTDATED
    finally:
        old.stop()


def test_a_current_proxy_is_never_replaced(tmp_path, monkeypatch, bundle_with_pick_profile, started, fake):
    """MUTATION: replace whenever the command line is ours -> red."""
    procs = _Procs(monkeypatch, ["node", REPO_BUNDLE, "serve"])
    procs.stale.add(os.getpid())  # the fake's pid reads as an Iron Jarvis bundle
    svc = IronProxyService(_cfg(tmp_path), register=False)
    svc.start()
    svc.check()
    assert procs.killed == [] and started == [] and svc.status()["error"] is None


def test_the_bundle_features_are_read_from_the_bundle(tmp_path, monkeypatch):
    bundle = tmp_path / "iron-proxy.mjs"
    bundle.write_text('x = ["executor-v1"]', encoding="utf-8")
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(bundle))
    svc = IronProxyService(_cfg(tmp_path), register=False)
    assert svc._bundle_features == frozenset({"executor-v1"})
    time.sleep(0.02)
    bundle.write_text('x = ["executor-v1", "pick-profile"] // newer', encoding="utf-8")
    svc._refresh_bundle_facts()
    assert svc._bundle_features == frozenset({"executor-v1", "pick-profile"})


def test_a_refresh_from_two_threads_is_one_call(tmp_path, fake):
    """An in-flight re-check is shared. MUTATION: no sharing -> two calls."""
    fake.refresh_delay = 0.3
    a = fake.add_profile("anthropic", "A")
    svc = IronProxyService(_cfg(tmp_path), register=False)
    svc.start()
    futs: list = []
    threads = [threading.Thread(target=lambda: futs.append(svc.request_refresh(a["id"], force=True)))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert futs[0] is futs[1]
    futs[0].result(5)
    assert len(_refreshes(fake)) == 1


def test_stop_shuts_the_re_check_pool_down(tmp_path, fake):
    """Review 7: queued re-checks are cancelled when Iron-Proxy is turned off
    or the daemon stops. MUTATION: no shutdown -> red."""
    fake.refresh_delay = 0.5
    ids = [fake.add_profile("anthropic", f"A{i}")["id"] for i in range(6)]
    svc = IronProxyService(_cfg(tmp_path), register=False)
    svc.start()
    futs = [svc.request_refresh(i, force=True) for i in ids]
    svc.stop()
    assert svc._refresh_pool is None
    assert any(f.cancelled() for f in futs)


def test_our_own_child_is_never_judged_stale(tmp_path, monkeypatch):
    """The child THIS daemon started IS the current bundle, even when its
    health lists fewer features than the bundle text (the fake bundle names
    pick-profile; its child serves executor-v1 only). MUTATION: judge our own
    child too -> red."""
    import psutil

    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_NODE", sys.executable)
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(FIXTURE))
    svc = IronProxyService(_cfg(tmp_path), register=False)
    try:
        st = svc.start()
        assert st["running"] is True and st["owned"] is True, st
        assert "pick-profile" in svc._bundle_features and not svc.has_feature("pick-profile")
        assert svc.check() is True
        assert svc.status()["error"] is None
    finally:
        svc.stop()
        needle = str(tmp_path / "ipd")
        for proc in psutil.process_iter(["cmdline"]):
            try:
                if needle in " ".join(proc.info.get("cmdline") or []):
                    proc.kill()
            except (psutil.Error, OSError):
                pass


def test_a_confirmed_sign_in_pane_no_longer_counts_as_signing_in():
    """v1.303.3 final review: an open sign-in pane whose login was confirmed
    must not keep its account on the re-check list (a later 401 would be
    re-armed for as long as the pane stays open)."""
    from types import SimpleNamespace

    from iron_jarvis.daemon.routes import iron_proxy as ipx_routes

    signing = SimpleNamespace(signin_for="prof_a", alive=True, signed_in=False)
    done = SimpleNamespace(signin_for="prof_b", alive=True, signed_in=True)
    closed = SimpleNamespace(signin_for="prof_c", alive=False, signed_in=False)
    d = SimpleNamespace(platform=SimpleNamespace(terminals=SimpleNamespace(
        _sessions={"1": signing, "2": done, "3": closed})))
    assert ipx_routes._signin_panes(d) == {"prof_a"}
