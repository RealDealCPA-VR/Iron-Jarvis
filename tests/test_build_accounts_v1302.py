"""Build panes on Iron-Proxy accounts (v1.302.0), driven through the REAL app
factory (``create_app`` + its lifespan + the real terminals manager) against the
fake Iron-Proxy (``tests/fixtures/fake_iron_proxy_v1301.py``), with a recording
fake PTY backend.

A vendor CLI's account is its home variable AT PROCESS START, so every claim
here is asserted on the environment the BACKEND RECEIVED (CLAUDE.md v1.217.0:
a dict the shell never saw proves nothing):

* an explicit account -> that account's ``CLAUDE_CONFIG_DIR`` (and Iron-Proxy's
  unset list) in the shell; ``"default"`` -> an inherited home REMOVED;
* no key + Iron-Proxy on -> its first free account, recorded; no key +
  Iron-Proxy off -> the env byte-identical to v1.301.0 and no ``accounts``;
* parked / needs sign-in / missing / turned off / too-old Iron-Proxy -> 409
  with one sentence and NO pane; Iron-Proxy not answering -> this PC's login +
  a note (absent) or 409 (explicit);
* pane rows (GET /terminals AND the 2.5 s /terminals/activity poll) carry each
  account's live state from the CACHED snapshot — Iron-Proxy is never called;
* snapshot -> restore re-applies the recorded home with NO Iron-Proxy call
  (boot never waits on it) and a post-boot check updates the chips; a snapshot
  without homes is re-resolved by id within a bounded budget;
* ``POST /terminals/launch`` and ``POST /iron-proxy/accounts/{id}/open``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.iron_proxy.client import IronProxyClient
from iron_jarvis.terminals.backend import FakeBackend
from iron_jarvis.terminals.pane_accounts import STARTING_NOTE

FIXTURE = Path(__file__).parent / "fixtures" / "fake_iron_proxy_v1301.py"
sys.path.insert(0, str(FIXTURE.parent))
from fake_iron_proxy_v1301 import FEATURES_PICK_PROFILE, FakeIronProxy  # noqa: E402

OFF = "Iron-Proxy is off; turn it on in Connections first."
DAEMON_ONLY = ("IRONJARVIS_TOKEN", "IRONJARVIS_MCP_TOKEN")


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
class _RecordingBackend(FakeBackend):
    """Keeps the env each shell was STARTED with and everything typed into it."""

    instances: list["_RecordingBackend"] = []
    lock = threading.Lock()

    def __init__(self) -> None:
        super().__init__()
        self.env: dict | None = None
        self.cwd: str | None = None
        self.written: list[str] = []
        with _RecordingBackend.lock:
            _RecordingBackend.instances.append(self)

    def start(self, argv, cwd, env, cols, rows) -> None:  # type: ignore[override]
        self.env = dict(env) if env is not None else None
        self.cwd = cwd
        super().start(argv, cwd, env, cols, rows)

    def write(self, data) -> None:  # type: ignore[override]
        self.written.append(data if isinstance(data, str) else data.decode())
        super().write(data)


def _backend_of(pane_id: str) -> _RecordingBackend:
    found = [
        b for b in _RecordingBackend.instances
        if b.env is not None and b.env.get("IRONJARVIS_PANE_ID") == pane_id
    ]
    assert found, f"no shell was started for pane {pane_id}"
    return found[-1]


def _home_keys(env: dict, var: str) -> list[str]:
    return [k for k in env if k.upper() == var]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    for k in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
              "GOOGLE_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    _RecordingBackend.instances = []
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", _RecordingBackend)

    # The lifespan's Iron-Proxy watch loop would refresh the account snapshot
    # in the background; these tests control every refresh themselves.
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
def fake(tmp_path):
    f = FakeIronProxy(tmp_path / "ipd", features=list(FEATURES_PICK_PROFILE)).start()
    yield f
    f.stop()


@pytest.fixture
def client(proj):
    with TestClient(create_app(str(proj))) as c:
        yield c


def _enable(c) -> dict:
    r = c.post("/iron-proxy/enable")
    assert r.status_code == 200, r.text
    assert r.json()["status"]["running"] is True, r.text
    return r.json()


def _panes(c) -> dict:
    return {p["id"]: p for p in c.get("/terminals").json()["terminals"]}


def _park(fake, pid: str, *, kind: str = "quota-exhausted", hours: float = 1.0) -> str:
    until = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    fake.states[pid].update(
        {"status": "parked", "parkedUntil": until, "parkedReason": {"kind": kind}}
    )
    return until


def _picks(fake) -> list[dict]:
    return [q for path, q in fake.queries if path == "/iron/pick"]


def _refresh(c) -> None:
    """Accounts the test added to the fake directly: refresh the snapshot the
    way every account route does (``/iron-proxy`` reads profiles + states)."""
    assert c.get("/iron-proxy?discover=0").status_code == 200


# --------------------------------------------------------------------------- #
# The account reaches the shell
# --------------------------------------------------------------------------- #
def test_an_explicit_account_is_the_shells_home(client, fake, monkeypatch):
    """MUTATION: drop ``accounts.apply(base)`` in ``_with_pane_env`` -> the shell
    keeps the inherited home and the API key -> red."""
    c = client
    _enable(c)
    work = fake.add_profile("anthropic", "Work Max")
    personal = fake.add_profile("anthropic", "Personal")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(Path("C:/inherited/claude")))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-must-not-reach-a-subscription-pane")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-the-users-own-openai")

    r = c.post("/terminals", json={"accounts": {"anthropic": personal["id"]}})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["accounts"]["anthropic"] == {
        "id": personal["id"], "title": "Personal", "source": "iron-proxy", "state": "ready",
    }
    assert "openai" not in row["accounts"] and "xai" not in row["accounts"]  # NO_PROFILE

    env = _backend_of(row["id"]).env
    assert _home_keys(env, "CLAUDE_CONFIG_DIR") == ["CLAUDE_CONFIG_DIR"]
    assert env["CLAUDE_CONFIG_DIR"] == personal["cli"]["home"] != work["cli"]["home"]
    assert "ANTHROPIC_API_KEY" not in env  # its OWN provider's key only
    assert env["OPENAI_API_KEY"] == "sk-the-users-own-openai"  # never another's (F2)
    assert env.get("PATH") == os.environ.get("PATH")  # the base is kept
    assert env["IRONJARVIS_PANE_ID"] == row["id"] and env["IRONJARVIS_BUILD"] == "1"
    # The pinned pick asked for THAT account.
    assert any(q.get("profileId") == [personal["id"]] for q in _picks(fake))


def test_default_removes_an_inherited_home_even_with_iron_proxy_off(client, monkeypatch):
    """MUTATION: skip the home-var removal in ``PaneAccounts.apply`` -> the
    inherited ``CLAUDE_CONFIG_DIR`` survives -> red."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(Path("C:/inherited/claude")))
    r = client.post("/terminals", json={"accounts": {"anthropic": "default"}})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["accounts"] == {
        "anthropic": {"id": None, "title": "this PC's login", "source": "default",
                      "state": "default"},
    }
    env = _backend_of(row["id"]).env
    assert _home_keys(env, "CLAUDE_CONFIG_DIR") == []


def test_no_key_with_iron_proxy_on_takes_the_first_free_account_from_the_cache(
    client, fake, monkeypatch
):
    """A plain "+" pane NEVER waits on Iron-Proxy (review F4): the first free
    account comes from the cached snapshot with ZERO calls, and the pane strips
    only that provider's own keys (F2). MUTATION: ``absent = []`` -> nothing
    recorded -> red. MUTATION: skip parked accounts no more -> "First" -> red.
    MUTATION: unset Iron-Proxy's whole list in a pane -> OPENAI_API_KEY gone ->
    red."""
    c = client
    _enable(c)
    first = fake.add_profile("anthropic", "First")
    second = fake.add_profile("anthropic", "Second")
    _park(fake, first["id"])
    _refresh(c)
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(key, f"users-own-{key}")
    calls_before = len(fake.calls)
    r = c.post("/terminals", json={})
    assert r.status_code == 200, r.text
    assert len(fake.calls) == calls_before, fake.calls[calls_before:]  # zero calls
    row = r.json()
    assert row["accounts"] == {
        "anthropic": {"id": second["id"], "title": "Second", "source": "iron-proxy",
                      "state": "ready"},
    }
    env = _backend_of(row["id"]).env
    assert env["CLAUDE_CONFIG_DIR"] == second["cli"]["home"]
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["OPENAI_API_KEY"] == "users-own-OPENAI_API_KEY"
    assert env["GOOGLE_API_KEY"] == "users-own-GOOGLE_API_KEY"


def test_no_key_with_every_account_parked_says_so_from_the_cache(client, fake):
    c = client
    _enable(c)
    only = fake.add_profile("anthropic", "Only")
    _park(fake, only["id"], kind="rate-limit")
    _refresh(c)
    calls_before = len(fake.calls)
    row = c.post("/terminals", json={}).json()
    assert len(fake.calls) == calls_before
    rec = row["accounts"]["anthropic"]
    assert rec["source"] == "default" and rec["id"] is None
    assert rec["note"].startswith("The only Claude account in Iron-Proxy is at its request limit")
    assert rec["note"].endswith("— this pane uses this PC's login.")


def test_no_key_with_iron_proxy_off_is_byte_identical_to_v1301(client, monkeypatch):
    """The whole env the shell got equals v1.301.0's (``os.environ`` minus the
    daemon's credentials plus the pane identity), and the row and the snapshot
    carry no ``accounts``. MUTATION: record ``this PC's login`` for absent keys
    while off -> red."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(Path("C:/inherited/claude")))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-the-users-own")
    r = client.post("/terminals", json={})
    assert r.status_code == 200, r.text
    row = r.json()
    assert "accounts" not in row
    expected = {k: v for k, v in os.environ.items() if k not in DAEMON_ONLY}
    expected.update({
        "IRONJARVIS_BUILD": "1",
        "IRONJARVIS_PANE_ID": row["id"],
        "IRONJARVIS_PANE_CWD": row["cwd"],
    })
    assert _backend_of(row["id"]).env == expected
    activity = client.get("/terminals/activity").json()["panes"]
    assert all("accounts" not in p for p in activity)
    state_path = client.app.state.platform.terminals.state_path
    entries = json.loads(state_path.read_text(encoding="utf-8"))["terminals"]
    assert entries and all("accounts" not in e for e in entries)


# --------------------------------------------------------------------------- #
# Refusals: one sentence, no pane
# --------------------------------------------------------------------------- #
def _assert_no_new_pane(c, before: int, shells_before: int) -> None:
    assert len(_panes(c)) == before
    assert len(_RecordingBackend.instances) == shells_before


@pytest.mark.parametrize(
    "case,words",
    [
        ("usage", 'The Claude account "Work Max" is at its usage limit until'),
        ("rate", 'The Claude account "Work Max" is at its request limit until'),
        ("signin", 'The Claude account "Work Max" needs to sign in again — sign it in on '
                   "the Connections page (Iron-Proxy card)"),
        ("disabled", 'The Claude account "Work Max" is turned off on the Iron-Proxy card'),
        ("missing", 'The Claude account "prof_gone" no longer exists in Iron-Proxy'),
    ],
)
@pytest.mark.parametrize("lenient", [False, True])
def test_an_unusable_chosen_account_is_409_and_no_pane(client, fake, case, words, lenient):
    """Never silently a different account (a free one exists). ``lenient``: an
    Iron-Proxy whose pinned pick LENDS a parked / signed-out account anyway —
    the fresh-read pre-check still refuses. MUTATION: drop the pre-check's
    parked branch -> the lenient ``usage``/``rate`` cases go red (the strict
    ones are then refused by Iron-Proxy's own 429, pinned below)."""
    c = client
    _enable(c)
    work = fake.add_profile("anthropic", "Work Max")
    fake.add_profile("anthropic", "Spare")  # free: must NOT be used instead
    fake.pinned_lenient = lenient
    pid = work["id"]
    if case == "usage":
        _park(fake, pid, kind="quota-exhausted")
    elif case == "rate":
        _park(fake, pid, kind="rate-limit")
    elif case == "signin":
        fake.states[pid]["status"] = "unauthenticated"
    elif case == "disabled":
        fake.profiles[pid]["enabled"] = False
    else:
        pid = "prof_gone"
    before, shells = len(_panes(c)), len(_RecordingBackend.instances)
    r = c.post("/terminals", json={"accounts": {"anthropic": pid}})
    assert r.status_code == 409, r.text
    assert words in r.json()["detail"]
    _assert_no_new_pane(c, before, shells)


def test_iron_proxys_own_refusal_is_worded_by_kind(client, fake, monkeypatch):
    """Iron-Proxy (2d4f645) answers a parked pinned account 429 QUOTA_EXCEEDED
    with ``{profileId, title, provider, resetAt, kind}`` — even when our fresh
    read still said ready (it parked in between). MUTATION: drop the
    QUOTA_EXCEEDED mapping in ``_explicit_error`` -> the generic sentence -> red."""
    c = client
    _enable(c)
    work = fake.add_profile("anthropic", "Work Max")
    real_states = fake.handle

    def park_after_the_read(method, path, headers, body):
        if path.startswith("/iron/pick"):
            _park(fake, work["id"], kind="rate-limit")
        return real_states(method, path, headers, body)

    monkeypatch.setattr(fake, "handle", park_after_the_read)
    r = c.post("/terminals", json={"accounts": {"anthropic": work["id"]}})
    assert r.status_code == 409, r.text
    assert 'The Claude account "Work Max" is at its request limit until' in r.json()["detail"]


def test_a_wrong_provider_or_an_api_key_account_is_400(client, fake):
    c = client
    _enable(c)
    gpt = fake.add_profile("openai", "Work GPT")
    key = fake.add_profile("anthropic", "Key", "api-key")
    r = c.post("/terminals", json={"accounts": {"anthropic": gpt["id"]}})
    assert r.status_code == 400 and "not a Claude one" in r.json()["detail"]
    r = c.post("/terminals", json={"accounts": {"anthropic": key["id"]}})
    assert r.status_code == 400 and "API-key account" in r.json()["detail"]
    r = c.post("/terminals", json={"accounts": {"google": "x"}})
    assert r.status_code == 400 and "no account for" in r.json()["detail"]


def test_a_chosen_account_with_iron_proxy_off_is_409(client):
    before, shells = len(_panes(client)), len(_RecordingBackend.instances)
    r = client.post("/terminals", json={"accounts": {"anthropic": "prof_0001"}})
    assert r.status_code == 409 and r.json()["detail"] == OFF
    _assert_no_new_pane(client, before, shells)


def test_an_iron_proxy_without_pick_profile_refuses_a_chosen_account(client, fake):
    """An older Iron-Proxy IGNORES ``profileId`` and lends the next ready
    account, so an explicit id is never sent to it. Absent keys still work
    (plain pick). MUTATION: drop the ``has_feature`` check -> the id is sent
    (and the answer-id guard says "different account", not "too old") -> red."""
    c = client
    fake.features = ["executor-v1"]
    _enable(c)
    first = fake.add_profile("anthropic", "First")
    second = fake.add_profile("anthropic", "Second")
    before, shells = len(_panes(c)), len(_RecordingBackend.instances)
    r = c.post("/terminals", json={"accounts": {"anthropic": second["id"]}})
    assert r.status_code == 409, r.text
    assert "too old to open a pane on a chosen account — update Iron-Proxy" in r.json()["detail"]
    _assert_no_new_pane(c, before, shells)
    assert not any("profileId" in q for q in _picks(fake))
    _refresh(c)
    r = c.post("/terminals", json={})
    assert r.status_code == 200, r.text
    assert r.json()["accounts"]["anthropic"]["id"] == first["id"]


def test_an_answer_for_another_account_is_refused(client, fake, monkeypatch):
    """Even with the feature advertised, the answer must BE the chosen account.
    MUTATION: drop the ``lease.profile_id != pid`` guard -> the pane runs as
    "First" -> red."""
    c = client
    _enable(c)
    fake.add_profile("anthropic", "First")
    second = fake.add_profile("anthropic", "Second")
    monkeypatch.setattr(fake, "pick_profile", lambda provider, lane, pid: fake.pick(provider, lane))
    before, shells = len(_panes(c)), len(_RecordingBackend.instances)
    r = c.post("/terminals", json={"accounts": {"anthropic": second["id"]}})
    assert r.status_code == 409, r.text
    assert 'different Claude account than "Second"' in r.json()["detail"]
    _assert_no_new_pane(c, before, shells)


# --------------------------------------------------------------------------- #
# Iron-Proxy on but not answering
# --------------------------------------------------------------------------- #
def test_iron_proxy_not_answering(client, fake, tmp_path):
    """A no-key pane still takes the cached account (a folder needs no live
    Iron-Proxy); an explicit id is 409; once the snapshot is gone (Iron-Proxy
    being restarted) the pane is on this PC's login with the "still starting"
    note, only for a provider Iron-Proxy had accounts for. MUTATION: skip the
    note when there is no snapshot -> the last pane has no ``accounts`` -> red."""
    c = client
    _enable(c)
    work = fake.add_profile("anthropic", "Work Max")
    _refresh(c)
    fake.stop()
    (tmp_path / "ipd" / "proxy.json").unlink()

    r = c.post("/terminals", json={})
    assert r.status_code == 200, r.text
    assert r.json()["accounts"]["anthropic"]["id"] == work["id"]

    before, shells = len(_panes(c)), len(_RecordingBackend.instances)
    r = c.post("/terminals", json={"accounts": {"anthropic": work["id"]}})
    assert r.status_code == 409, r.text
    assert "Iron-Proxy is on but not answering" in r.json()["detail"]
    _assert_no_new_pane(c, before, shells)

    r = c.post("/terminals", json={})  # the snapshot went with the client
    assert r.status_code == 200, r.text
    assert r.json()["accounts"] == {
        "anthropic": {"id": None, "title": "this PC's login", "source": "default",
                      "note": STARTING_NOTE, "state": "default"},
    }
    assert _home_keys(_backend_of(r.json()["id"]).env, "CLAUDE_CONFIG_DIR") == []


def test_a_plain_pane_while_iron_proxy_is_still_starting(client):
    """Enabled, never read (nothing bundled here): no wait, this PC's login,
    the note for every provider."""
    c = client
    r = c.post("/iron-proxy/enable")
    assert r.status_code == 200 and r.json()["status"]["running"] is False
    row = c.post("/terminals", json={}).json()
    assert set(row["accounts"]) == {"anthropic", "openai", "xai"}
    assert all(a["note"] == STARTING_NOTE and a["source"] == "default"
               for a in row["accounts"].values())


# --------------------------------------------------------------------------- #
# Rows read the CACHE
# --------------------------------------------------------------------------- #
_LIST_ROUTES = {"list_terminals", "terminal_activity", "with_live_state", "pane_row"}


@pytest.fixture
def no_calls_from_list_routes(monkeypatch):
    """Every Iron-Proxy HTTP call made while a list route is on the stack."""
    offenders: list[str] = []
    real = IronProxyClient._call

    def spy(self, method, path, **kw):
        names = {f.function for f in inspect.stack()}
        if names & _LIST_ROUTES:
            offenders.append(f"{method} {path}")
        return real(self, method, path, **kw)

    monkeypatch.setattr(IronProxyClient, "_call", spy)
    return offenders


def test_rows_carry_live_state_from_the_cache_and_never_call_iron_proxy(
    client, fake, no_calls_from_list_routes
):
    """MUTATION: make ``with_live_state`` refresh the snapshot itself -> the
    stale-cache assertion and the no-call assertion go red."""
    c = client
    _enable(c)
    work = fake.add_profile("anthropic", "Work Max")
    pane = c.post("/terminals", json={"accounts": {"anthropic": work["id"]}}).json()

    def chips():
        listed = _panes(c)[pane["id"]]["accounts"]["anthropic"]
        polled = next(p for p in c.get("/terminals/activity").json()["panes"]
                      if p["id"] == pane["id"])["accounts"]["anthropic"]
        assert listed == polled  # the 2.5 s poll carries the same chip
        return listed

    assert chips()["state"] == "ready"
    until = _park(fake, work["id"], kind="rate-limit")
    assert chips()["state"] == "ready"  # the cache, not a call
    assert c.get("/iron-proxy").status_code == 200  # refreshes the snapshot
    assert chips() == {"id": work["id"], "title": "Work Max", "source": "iron-proxy",
                       "state": "parked", "until": until, "reason": "rate-limit"}
    fake.states[work["id"]].update({"status": "unauthenticated", "parkedUntil": None,
                                    "parkedReason": None})
    c.get("/iron-proxy")
    assert chips()["state"] == "needs-sign-in"
    fake.profiles[work["id"]]["enabled"] = False
    c.get("/iron-proxy")
    assert chips()["state"] == "missing" and chips()["reason"] == "disabled"
    del fake.profiles[work["id"]]
    c.get("/iron-proxy")
    assert chips()["state"] == "missing" and "reason" not in chips()
    c.post("/iron-proxy/disable")
    assert chips()["state"] == "unknown"  # off: nothing to read, nothing guessed
    assert no_calls_from_list_routes == []


def test_the_list_routes_survive_an_iron_proxy_that_raises(client, fake, monkeypatch):
    c = client
    _enable(c)
    work = fake.add_profile("anthropic", "Work Max")
    pane = c.post("/terminals", json={"accounts": {"anthropic": work["id"]}}).json()
    svc = c.app.state.platform.iron_proxy

    def boom(*_a, **_k):
        raise AssertionError("a list route called Iron-Proxy")

    for name in ("client", "lease_client", "refresh_accounts", "check", "start"):
        monkeypatch.setattr(svc, name, boom)
    assert _panes(c)[pane["id"]]["accounts"]["anthropic"]["state"] == "ready"
    assert c.get("/terminals/activity").status_code == 200


# --------------------------------------------------------------------------- #
# Snapshot -> restore: boot NEVER waits on Iron-Proxy
# --------------------------------------------------------------------------- #
_BOOT_FRAMES = {"rehydrate", "restore", "_restore_accounts"}


@pytest.fixture
def boot_calls(monkeypatch):
    """Every Iron-Proxy touch (HTTP call, health probe, client/lease lookup)
    made while a rehydrate/restore is on the stack."""
    from iron_jarvis.iron_proxy import service as svc_mod

    seen: list[str] = []

    def on_boot_path() -> bool:
        return bool({f.function for f in inspect.stack()} & _BOOT_FRAMES)

    real_call = IronProxyClient._call

    def spy_call(self, method, path, **kw):
        if on_boot_path():
            seen.append(f"{method} {path}")
        return real_call(self, method, path, **kw)

    real_health = svc_mod._health

    def spy_health(url, *a, **k):
        if on_boot_path():
            seen.append("health")
        return real_health(url, *a, **k)

    for name in ("lease_client", "client", "locate", "start", "check"):
        real = getattr(svc_mod.IronProxyService, name)

        def spy(self, *a, _real=real, _name=name, **k):
            if on_boot_path():
                seen.append(_name)
            return _real(self, *a, **k)

        monkeypatch.setattr(svc_mod.IronProxyService, name, spy)
    monkeypatch.setattr(IronProxyClient, "_call", spy_call)
    monkeypatch.setattr(svc_mod, "_health", spy_health)
    return seen


def _wait_for(fn, timeout: float = 15.0):
    import time

    end = time.monotonic() + timeout
    while True:
        got = fn()
        if got or time.monotonic() > end:
            return got
        time.sleep(0.05)


def test_restore_reapplies_the_recorded_home_with_no_iron_proxy_call(
    proj, fake, boot_calls, monkeypatch
):
    """A restored pane comes back on the SAME account folder (its conversations
    live there — ``claude --continue`` must find them) WITHOUT the boot asking
    Iron-Proxy anything; the post-boot check (a background thread) then updates
    the chips from the cache: parked -> parked, gone -> a note that the pane
    still runs on that folder. MUTATION: treat a recorded home as legacy ->
    the boot makes Iron-Proxy calls -> red. MUTATION: never start the post-boot
    check -> the chips stay ``unknown`` -> red. MUTATION: drop the still-runs
    note -> red. MUTATION: the snapshot drops ``accounts`` -> red."""
    with TestClient(create_app(str(proj))) as c1:
        _enable(c1)
        work = fake.add_profile("anthropic", "Work Max")
        personal = fake.add_profile("anthropic", "Personal")
        old = fake.add_profile("anthropic", "Old")
        on_a = c1.post("/terminals", json={"accounts": {"anthropic": work["id"]}}).json()
        on_b = c1.post("/terminals", json={"accounts": {"anthropic": personal["id"]}}).json()
        on_c = c1.post("/terminals", json={"accounts": {"anthropic": old["id"]}}).json()
        on_pc = c1.post("/terminals", json={"accounts": {"anthropic": "default"}}).json()
        session_a = c1.app.state.platform.terminals.get(on_a["id"])
        assert "home" not in session_a.info()["accounts"]["anthropic"]  # rows never carry it
        state_path = c1.app.state.platform.terminals.state_path
    entries = {e["id"]: e for e in json.loads(state_path.read_text("utf-8"))["terminals"]}
    rec_a = entries[on_a["id"]]["accounts"]["anthropic"]
    assert rec_a["id"] == work["id"] and rec_a["source"] == "iron-proxy"
    assert rec_a["home"] == work["cli"]["home"] and "ANTHROPIC_API_KEY" in rec_a["unset"]
    assert entries[on_pc["id"]]["accounts"]["anthropic"] == {
        "id": None, "title": "this PC's login", "source": "default",
    }

    until = _park(fake, personal["id"], kind="rate-limit")
    del fake.profiles[old["id"]]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-must-not-reach-a-restored-pane")
    _RecordingBackend.instances = []
    with TestClient(create_app(str(proj))) as c2:
        assert boot_calls == []  # the rehydrate never touched Iron-Proxy
        for pane, prof in ((on_a, work), (on_b, personal), (on_c, old)):
            env = _backend_of(pane["id"]).env
            assert env["CLAUDE_CONFIG_DIR"] == prof["cli"]["home"]
            assert "ANTHROPIC_API_KEY" not in env  # the recorded unset rules
        assert _home_keys(_backend_of(on_pc["id"]).env, "CLAUDE_CONFIG_DIR") == []

        def settled():
            panes = _panes(c2)
            if panes[on_c["id"]]["accounts"]["anthropic"]["state"] != "missing":
                return None
            return panes

        panes = _wait_for(settled)
        assert panes, "the post-boot check never refreshed the chips"
        assert panes[on_a["id"]]["accounts"]["anthropic"] == {
            "id": work["id"], "title": "Work Max", "source": "iron-proxy", "state": "ready",
        }
        assert panes[on_b["id"]]["accounts"]["anthropic"] == {
            "id": personal["id"], "title": "Personal", "source": "iron-proxy",
            "state": "parked", "until": until, "reason": "rate-limit",
        }
        assert panes[on_c["id"]]["accounts"]["anthropic"] == {
            "id": old["id"], "title": "Old", "source": "iron-proxy", "state": "missing",
            "note": 'This pane still runs on "Old"\'s folder; Iron-Proxy no longer lists it.',
        }
        assert panes[on_pc["id"]]["accounts"]["anthropic"]["state"] == "default"
        assert boot_calls == []


def test_restore_with_iron_proxy_off_keeps_the_folder_and_says_so(proj, fake, boot_calls):
    with TestClient(create_app(str(proj))) as c1:
        _enable(c1)
        work = fake.add_profile("anthropic", "Work Max")
        pane = c1.post("/terminals", json={"accounts": {"anthropic": work["id"]}}).json()
        c1.post("/iron-proxy/disable")
    _RecordingBackend.instances = []
    with TestClient(create_app(str(proj))) as c2:
        rec = _panes(c2)[pane["id"]]["accounts"]["anthropic"]
        assert rec == {
            "id": work["id"], "title": "Work Max", "source": "iron-proxy", "state": "unknown",
            "note": 'Iron-Proxy is off; this pane still runs on "Work Max"\'s folder.',
        }
        assert _backend_of(pane["id"]).env["CLAUDE_CONFIG_DIR"] == work["cli"]["home"]
    assert boot_calls == []


def _strip_homes(state_path: Path) -> None:
    """Rewrite a snapshot as one written before homes were recorded."""
    doc = json.loads(state_path.read_text("utf-8"))
    for entry in doc["terminals"]:
        for rec in (entry.get("accounts") or {}).values():
            rec.pop("home", None)
            rec.pop("unset", None)
    state_path.write_text(json.dumps(doc), encoding="utf-8")


def test_a_snapshot_without_homes_is_re_resolved_by_id(proj, fake):
    """The fallback for a snapshot written before homes were recorded: by id,
    never another account; an account that cannot be used comes back on this
    PC's login with a note."""
    with TestClient(create_app(str(proj))) as c1:
        _enable(c1)
        work = fake.add_profile("anthropic", "Work Max")
        personal = fake.add_profile("anthropic", "Personal")
        on_a = c1.post("/terminals", json={"accounts": {"anthropic": work["id"]}}).json()
        on_b = c1.post("/terminals", json={"accounts": {"anthropic": personal["id"]}}).json()
        state_path = c1.app.state.platform.terminals.state_path
    _strip_homes(state_path)
    _park(fake, personal["id"])
    _RecordingBackend.instances = []
    with TestClient(create_app(str(proj))) as c2:
        panes = _panes(c2)
        assert panes[on_a["id"]]["accounts"]["anthropic"]["id"] == work["id"]
        assert _backend_of(on_a["id"]).env["CLAUDE_CONFIG_DIR"] == work["cli"]["home"]
        assert any(q.get("profileId") == [work["id"]] for q in _picks(fake))
        rec_b = panes[on_b["id"]]["accounts"]["anthropic"]
        assert rec_b["id"] is None and rec_b["source"] == "default"
        assert rec_b["note"].startswith('The Claude account "Personal" is at its usage limit')
        assert rec_b["note"].endswith("so this pane came back on this PC's login.")
        assert _home_keys(_backend_of(on_b["id"]).env, "CLAUDE_CONFIG_DIR") == []


def _snapshot(path: Path, panes: list[dict]) -> None:
    path.write_text(json.dumps({"terminals": panes}), encoding="utf-8")


def _entry(i: int, accounts: dict | None) -> dict:
    out = {"id": f"term_{i:04d}", "shell": "shell", "argv": ["shell"], "cwd": str(Path.home()),
           "cols": 80, "rows": 24}
    if accounts is not None:
        out["accounts"] = accounts
    return out


def test_rehydrate_time_does_not_grow_with_recorded_homes(tmp_path):
    """Measured as a RATIO to the same rehydrate without accounts (CLAUDE.md /
    memory: never an absolute wall-clock bound): a resolver that would take
    3 s and a post-boot check that takes 3 s are both never on the boot path.
    MUTATION: run the post-boot check inline -> red. MUTATION: treat recorded
    homes as legacy -> the resolver is called -> red."""
    import time

    from iron_jarvis.terminals import TerminalManager

    resolver_calls: list[dict] = []
    verified = threading.Event()

    def slow_resolver(request, **_k):
        resolver_calls.append(dict(request))
        time.sleep(3.0)

    def slow_verifier():
        time.sleep(3.0)
        verified.set()

    rec = {"anthropic": {"id": "prof_1", "title": "Work Max", "source": "iron-proxy",
                         "home": str(tmp_path / "home1"), "unset": ["ANTHROPIC_API_KEY"]}}

    def boot(name: str, accounts: dict | None) -> tuple[float, TerminalManager]:
        sp = tmp_path / f"{name}.json"
        _snapshot(sp, [_entry(i, accounts) for i in range(3)])
        m = TerminalManager(state_path=sp)
        m.account_resolver = slow_resolver
        m.account_verifier = slow_verifier
        t0 = time.perf_counter()
        assert m.rehydrate(backend=FakeBackend()) == 3
        return time.perf_counter() - t0, m

    plain, _ = boot("plain", None)
    with_homes, m = boot("homes", rec)
    assert resolver_calls == []
    assert with_homes < plain * 3 + 1.0, (with_homes, plain)
    assert m.get("term_0000").accounts["anthropic"]["home"] == str(tmp_path / "home1")
    assert verified.wait(10), "the post-boot check never ran"


def test_a_legacy_snapshot_is_bounded_to_the_restore_budget(tmp_path):
    """A snapshot without homes may ask Iron-Proxy, but ALL panes together get
    ``RESTORE_ACCOUNTS_BUDGET_S``; past it each comes back on this PC's login
    with a note. MUTATION: join the worker without a timeout -> red."""
    import time

    from iron_jarvis.terminals import TerminalManager
    from iron_jarvis.terminals.manager import RESTORE_ACCOUNTS_BUDGET_S

    def hung_resolver(request, **_k):
        time.sleep(30.0)

    rec = {"anthropic": {"id": "prof_1", "title": "Work Max", "source": "iron-proxy"}}
    sp = tmp_path / "legacy.json"
    _snapshot(sp, [_entry(i, rec) for i in range(3)])
    m = TerminalManager(state_path=sp)
    m.account_resolver = hung_resolver
    t0 = time.perf_counter()
    assert m.rehydrate(backend=FakeBackend()) == 3
    elapsed = time.perf_counter() - t0
    # Three hung panes cost ONE budget, not three (nor 3 x 30 s).
    assert elapsed < 2 * RESTORE_ACCOUNTS_BUDGET_S + 1.0, elapsed
    for i in range(3):
        acc = m.get(f"term_{i:04d}").accounts["anthropic"]
        assert acc["id"] is None and acc["source"] == "default"
        assert acc["note"].startswith("Iron-Proxy did not answer in time when this pane came back")
        assert '"Work Max"' in acc["note"]


# --------------------------------------------------------------------------- #
# POST /terminals/launch
# --------------------------------------------------------------------------- #
def test_launch_opens_a_new_pane_on_the_account_and_starts_the_cli(client, fake, tmp_path):
    """MUTATION: drop the ``\\r`` -> nothing starts -> red. MUTATION: drop
    ``cwd or near.cwd`` -> the pane opens in the home folder -> red."""
    c = client
    _enable(c)
    work = fake.add_profile("anthropic", "Work Max")
    folder = tmp_path / "repo"
    folder.mkdir()
    near = c.post("/terminals", json={"cwd": str(folder), "accounts": {"anthropic": "default"}})
    near = near.json()

    r = c.post("/terminals/launch",
               json={"cli": "claude", "account": work["id"], "near": near["id"]})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["id"] != near["id"]  # a NEW pane; the near one is untouched
    assert row["name"] == "Claude Code · Work Max"
    assert row["agent_cli"] == "claude"
    assert row["cwd"] == str(folder)
    assert row["accounts"]["anthropic"]["id"] == work["id"]
    assert set(row) >= {"id", "cwd", "alive", "state", "capabilities", "accounts"}  # full row
    be = _backend_of(row["id"])
    assert be.env["CLAUDE_CONFIG_DIR"] == work["cli"]["home"]
    assert be.env["IRONJARVIS_PANE_CLI"] == "claude"
    assert be.env["IRONJARVIS_PANE_NAME"] == "Claude Code · Work Max"
    assert be.cwd == str(folder)
    # typed AND Enter (the click is the consent); v1.303.0: with the
    # conversation id this app mints and records on the pane.
    sid = row["claude_session_id"]
    assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", sid)
    assert be.written == [f"claude --session-id {sid}\r"]
    assert _backend_of(near["id"]).written == []

    again = c.post("/terminals/launch", json={"cli": "claude", "account": work["id"]}).json()
    assert again["name"] == "Claude Code · Work Max (2)"
    pc = c.post("/terminals/launch", json={"cli": "claude", "account": "default"}).json()
    assert pc["name"] == "Claude Code · this PC's login"
    assert _home_keys(_backend_of(pc["id"]).env, "CLAUDE_CONFIG_DIR") == []


def test_launch_refusals(client, fake):
    c = client
    _enable(c)
    work = fake.add_profile("anthropic", "Work Max")
    _park(fake, work["id"])
    before, shells = len(_panes(c)), len(_RecordingBackend.instances)
    r = c.post("/terminals/launch", json={"cli": "claude", "account": work["id"]})
    assert r.status_code == 409 and "usage limit" in r.json()["detail"]
    r = c.post("/terminals/launch", json={"cli": "not-a-cli"})
    assert r.status_code == 400 and "not a CLI on the Launch menu" in r.json()["detail"]
    r = c.post("/terminals/launch", json={"cli": "opencode", "account": work["id"]})
    assert r.status_code == 400 and "does not run on an Iron-Proxy account" in r.json()["detail"]
    r = c.post("/terminals/launch", json={"cli": "claude", "near": "term_nope"})
    assert r.status_code == 404
    _assert_no_new_pane(c, before, shells)


def test_launch_with_iron_proxy_off_just_starts_the_cli(client):
    r = client.post("/terminals/launch", json={"cli": "codex"})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["name"] == "Codex" and "accounts" not in row
    assert _backend_of(row["id"]).written == ["codex\r"]


def test_the_resolution_stays_off_the_event_loop(client):
    """Both resolving routes are sync ``def`` (Starlette's threadpool): the
    resolution is loopback HTTP. MUTATION: make either ``async`` -> red."""
    routes = {(r.path, tuple(sorted(r.methods or ()))): r.endpoint
              for r in client.app.routes if getattr(r, "methods", None)}
    for path in ("/terminals", "/terminals/launch"):
        assert not inspect.iscoroutinefunction(routes[(path, ("POST",))]), path


# --------------------------------------------------------------------------- #
# POST /iron-proxy/accounts/{id}/open
# --------------------------------------------------------------------------- #
def test_open_in_build_launches_the_accounts_cli(client, fake):
    c = client
    _enable(c)
    gpt = fake.add_profile("openai", "Work GPT")
    r = c.post(f"/iron-proxy/accounts/{gpt['id']}/open")  # no body: the provider's CLI
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"terminal_id", "name"} and out["name"] == "Codex · Work GPT"
    pane = _panes(c)[out["terminal_id"]]
    assert pane["agent_cli"] == "codex"
    assert pane["accounts"]["openai"]["id"] == gpt["id"]
    be = _backend_of(out["terminal_id"])
    assert be.env["CODEX_HOME"] == gpt["cli"]["home"] and be.written == ["codex\r"]

    r = c.post(f"/iron-proxy/accounts/{gpt['id']}/open", json={"cli": "claude"})
    assert r.status_code == 400
    key = fake.add_profile("anthropic", "Key", "api-key")
    assert c.post(f"/iron-proxy/accounts/{key['id']}/open").status_code == 400
    assert c.post("/iron-proxy/accounts/prof_nope/open").status_code == 404


def test_open_in_build_with_iron_proxy_off_is_409(client):
    r = client.post("/iron-proxy/accounts/prof_0001/open")
    assert r.status_code == 409 and r.json()["detail"] == OFF


# --------------------------------------------------------------------------- #
# Review F1: an ADOPTED default home sets no home variable
# --------------------------------------------------------------------------- #
@pytest.fixture
def user_home(tmp_path, monkeypatch):
    home = tmp_path / "user"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    return home


def test_an_adopted_default_home_sets_no_home_var_in_a_pane(client, fake, user_home, monkeypatch):
    """With CLAUDE_CONFIG_DIR set, Claude Code reads ``$CLAUDE_CONFIG_DIR/.claude.json``
    instead of the user's ``~/.claude.json`` (MCP servers, projects, trust): an
    account whose home IS ``~/.claude`` runs with the variable UNSET (an
    inherited one removed), still recorded as that account. An isolated home
    is still set. Explicit, no-key (cache) and restore all agree. MUTATION:
    ``is_default_home`` always False -> CLAUDE_CONFIG_DIR set -> red."""
    c = client
    _enable(c)
    mine = fake.add_profile("anthropic", "This PC", home=str(user_home / ".Claude"), adopted=True)
    work = fake.add_profile("anthropic", "Work Max")
    _refresh(c)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_home / "somewhere-else"))

    explicit = c.post("/terminals", json={"accounts": {"anthropic": mine["id"]}}).json()
    assert explicit["accounts"]["anthropic"] == {
        "id": mine["id"], "title": "This PC", "source": "iron-proxy", "state": "ready",
    }
    assert _home_keys(_backend_of(explicit["id"]).env, "CLAUDE_CONFIG_DIR") == []

    plain = c.post("/terminals", json={}).json()  # first free = the adopted one
    assert plain["accounts"]["anthropic"]["id"] == mine["id"]
    assert _home_keys(_backend_of(plain["id"]).env, "CLAUDE_CONFIG_DIR") == []

    isolated = c.post("/terminals", json={"accounts": {"anthropic": work["id"]}}).json()
    assert _backend_of(isolated["id"]).env["CLAUDE_CONFIG_DIR"] == work["cli"]["home"]


def test_an_adopted_default_home_sets_no_home_var_in_the_native_claude_child(
    tmp_path, user_home, monkeypatch
):
    """The v1.301.0 chat/agent lease (``accounts.Lease``) follows the same rule:
    the native Claude child gets NO CLAUDE_CONFIG_DIR for an adopted ``~/.claude``
    (an inherited one removed), and the lease still names its folder."""
    import asyncio as _asyncio
    import importlib.util
    import socket

    from iron_jarvis.iron_proxy import accounts
    from iron_jarvis.providers import cli_auth
    from iron_jarvis.providers.adapters.claude_native import transport
    from iron_jarvis.providers.cli_auth import CliAuthProbe

    spec = importlib.util.spec_from_file_location(
        "_ip_accounts_v1301_helpers", Path(__file__).parent / "test_iron_proxy_accounts_v1301.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # The v1301 file's autouse `_clean`, by hand.
    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")] + list(transport.BACKEND_SWITCHES):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(transport, "_STRIP_LOGGED", False)
    probe = CliAuthProbe(which=lambda b: None)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", probe)
    monkeypatch.setattr(transport, "DEFAULT_PROBE", probe)
    monkeypatch.setattr(accounts, "_NOTED", set())
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: (
        real(host, *a, **k) if host in ("127.0.0.1", "localhost", "::1")
        else (_ for _ in ()).throw(socket.gaierror(11001, "test guard"))))

    fake_cli = mod.Claude(tmp_path, monkeypatch, "text")
    client = mod.FakeIronProxy("anthropic", {"A": str(user_home / ".claude")})
    previous = accounts.set_client_source(lambda: client)
    try:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(user_home / "somewhere-else"))
        lease = accounts.lease("claude-cli")
        assert lease.env_set == {} and "CLAUDE_CONFIG_DIR" in lease.env_unset
        assert lease.home == str(user_home / ".claude")
        resp = _asyncio.run(fake_cli.adapter().complete(system="", messages=[mod._user()], tools=[]))
        assert resp.text == "Hello"
        assert "CLAUDE_CONFIG_DIR" not in fake_cli.record["env"]
        assert client.picks == ["A", "A"]  # the lease above + the call's own
    finally:
        accounts.set_client_source(previous)


# --------------------------------------------------------------------------- #
# Review F3: the light read never scans for logins
# --------------------------------------------------------------------------- #
def test_the_light_iron_proxy_read_never_runs_discover(client, fake):
    """``GET /iron-proxy?discover=0`` = status + accounts from profiles + states,
    without ``discover()`` (Iron-Proxy runs every vendor CLI's status command
    for it). MUTATION: ignore the flag -> /iron/discover is called -> red."""
    c = client
    _enable(c)
    fake.add_profile("anthropic", "Work Max")
    n = len(fake.calls)
    body = c.get("/iron-proxy?discover=0").json()
    paths = [path for _m, path, _b in fake.calls[n:]]
    assert "/iron/discover" not in paths and "/iron/profiles" in paths and "/iron/states" in paths
    assert body["discovered"] == [] and body["discover_skipped"] is True
    assert [a["title"] for a in body["accounts"]] == ["Work Max"]
    assert body["status"]["running"] is True
    n = len(fake.calls)
    full = c.get("/iron-proxy").json()
    assert "/iron/discover" in [path for _m, path, _b in fake.calls[n:]]
    assert "discover_skipped" not in full


# --------------------------------------------------------------------------- #
# Review nit: the launch name is picked and taken under one lock
# --------------------------------------------------------------------------- #
def test_two_launches_at_once_never_share_a_name(client, fake):
    """MUTATION: drop the lock -> both panes can be named "Codex" -> red
    (the create is slowed so the race is certain without it)."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    terms = client.app.state.platform.terminals
    real_create = terms.create

    def slow_create(*a, **k):
        time.sleep(0.3)
        return real_create(*a, **k)

    terms.create = slow_create
    try:
        with ThreadPoolExecutor(2) as pool:
            rows = list(pool.map(
                lambda _i: client.post("/terminals/launch", json={"cli": "codex"}).json(), range(2)
            ))
    finally:
        terms.create = real_create
    assert sorted(r["name"] for r in rows) == ["Codex", "Codex (2)"]

