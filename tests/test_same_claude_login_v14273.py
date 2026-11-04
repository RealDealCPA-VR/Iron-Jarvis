"""v1.427.3 (a USER BUG) — accounts signed in to ONE Claude account.

The user's live Build › Code session (2026-11-03): three Iron-Proxy Claude
accounts were signed in to the same Claude account. Claude Code answered every
one with the same eleven claude.ai connectors and the same usage windows (5-hour
100%, 7-day 43%, the same resets), so Continue on another account moved twice
onto the same limit, told Iron-Proxy each time that the account it left was
limited, and then refused: every other account was parked. Pinned here, with
throwaway account homes (never a real one):

* ``iron_proxy.claude_identity``: the signed-in account is ``.claude.json``'s
  ``oauthAccount`` pair, inside the home, or ``~/.claude.json`` for this PC's
  own login; unknown is never "the same"; a network path is never read; the
  cache follows the file;
* the picker never moves to an account with this one's login, says why, and
  never tells Iron-Proxy anything when it refuses; Iron-Proxy's own pick (the
  Build terminal path) is overridden when it hands back such an account;
* a refusal with nothing free offers the parked accounts whose login is not
  this one's (``code: none_free``, ``choices``), never a signed-out one;
* "Try it anyway" (``force``) ends Iron-Proxy's park first and moves; never
  for a signed-out account or one with this login;
* the Iron-Proxy card's view says which accounts share one login.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.iron_proxy import claude_identity as CI
from iron_jarvis.terminals import continue_on as CO
from tests._build_app_helpers_v1409 import client

ORG_A = "11111111-1111-4111-8111-111111111111"
ACC_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ORG_B = "22222222-2222-4222-8222-222222222222"
ACC_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
SID = "6b1c2f0e-5d4a-4c3b-9a8e-7f6d5c4b3a21"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    CI.forget()
    # ~ is a throwaway folder: this PC's own login is never the real one.
    fake_home = tmp_path / "userhome"
    fake_home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    yield
    CI.forget()


def _sign_in(home: Path, org: str, acc: str, extra: dict | None = None) -> None:
    home.mkdir(parents=True, exist_ok=True)
    data = {"oauthAccount": {"organizationUuid": org, "accountUuid": acc, "emailAddress": "someone@example.com"}}
    data.update(extra or {})
    (home / ".claude.json").write_text(json.dumps(data), encoding="utf-8")


class _Client:
    def __init__(self, profiles, states):
        self._p, self._s = profiles, states
        self.signals: list = []
        self.unparked: list = []
        self.picked = None

    def profiles(self):
        return self._p

    def states(self):
        return self._s

    def signal(self, pid, text=""):
        self.signals.append((pid, text))
        return {"parked": True}

    def unpark(self, pid):
        self.unparked.append(pid)
        self._s = {**self._s, pid: {"status": "ready"}}
        return {"ok": True}

    def pick(self, provider, lane="cli"):
        return self.picked


class _Svc:
    enabled = True

    def __init__(self, c):
        self.client = c

    def lease_client(self, timeout_s=None):
        return self.client

    def note_accounts(self, profiles, states):
        pass


def _three(tmp_path):
    """Work (on the session), Clone (Work's own login), Other (its own)."""
    h1, h2, h3 = tmp_path / "acc1", tmp_path / "acc2", tmp_path / "acc3"
    _sign_in(h1, ORG_A, ACC_A)
    _sign_in(h2, ORG_A, ACC_A)
    _sign_in(h3, ORG_B, ACC_B)
    profiles = [
        {"id": "p1", "provider": "anthropic", "lane": "cli", "title": "Work", "order": 0, "cli": {"home": str(h1)}},
        {"id": "p2", "provider": "anthropic", "lane": "cli", "title": "Clone", "order": 1, "cli": {"home": str(h2)}},
        {"id": "p3", "provider": "anthropic", "lane": "cli", "title": "Other", "order": 2, "cli": {"home": str(h3)}},
    ]
    return h1, h2, h3, profiles


# --------------------------------------------------------------------------- #
# who an account is signed in as
# --------------------------------------------------------------------------- #
def test_the_login_is_the_oauth_pair_and_unknown_is_never_the_same(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    _sign_in(a, ORG_A, ACC_A.upper())  # Claude Code's case never matters
    _sign_in(b, ORG_A, ACC_A)
    _sign_in(c, ORG_B, ACC_B)
    assert CI.identity_of(str(a)) == (ORG_A, ACC_A)
    assert CI.same_login(str(a), str(b)) is True
    assert CI.same_login(str(a), str(c)) is False
    # The same organisation, another account (a Team seat): its own limit.
    d = tmp_path / "d"
    _sign_in(d, ORG_A, ACC_B)
    assert CI.same_login(str(a), str(d)) is False
    # No file, no pair, half a pair, junk, an empty file: unknown.
    empty = tmp_path / "empty"
    empty.mkdir()
    half = tmp_path / "half"
    half.mkdir()
    (half / ".claude.json").write_text(json.dumps({"oauthAccount": {"organizationUuid": ORG_A}}), encoding="utf-8")
    junk = tmp_path / "junk"
    junk.mkdir()
    (junk / ".claude.json").write_text("{not json", encoding="utf-8")
    zero = tmp_path / "zero"
    zero.mkdir()
    (zero / ".claude.json").write_text("", encoding="utf-8")
    for home in (empty, half, junk, zero, tmp_path / "missing"):
        assert CI.identity_of(str(home)) is None
        assert CI.same_login(str(home), str(home)) is False  # two unknowns are not the same
    assert CI.same_login("", str(a)) is False


def test_this_pcs_own_login_is_read_from_the_users_claude_json(tmp_path):
    """The adopted ~/.claude sets no variable, so Claude Code reads ~/.claude.json
    — never ~/.claude/.claude.json."""
    user = Path(tmp_path / "userhome")
    (user / ".claude").mkdir()
    (user / ".claude.json").write_text(json.dumps(
        {"oauthAccount": {"organizationUuid": ORG_B, "accountUuid": ACC_B}}), encoding="utf-8")
    # A stray file INSIDE ~/.claude is not what Claude Code reads.
    (user / ".claude" / ".claude.json").write_text(json.dumps(
        {"oauthAccount": {"organizationUuid": ORG_A, "accountUuid": ACC_A}}), encoding="utf-8")
    assert CI.config_file(str(user / ".claude")) == user / ".claude.json"
    assert CI.identity_of(str(user / ".claude")) == (ORG_B, ACC_B)


def test_a_network_path_is_never_looked_up(monkeypatch):
    calls = []
    real = Path.stat

    def spy(self, *a, **k):
        calls.append(str(self))
        if "host" in str(self):
            raise OSError("a network path must never be looked up")  # never really sent
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "stat", spy)
    for home in (r"\\host\share\acc", "//host/share/acc"):
        assert CI.config_file(home) is None
        assert CI.identity_of(home) is None
    assert not any("host" in c for c in calls)


def test_a_new_sign_in_is_read_again(tmp_path):
    home = tmp_path / "acc"
    _sign_in(home, ORG_A, ACC_A)
    assert CI.identity_of(str(home)) == (ORG_A, ACC_A)
    _sign_in(home, ORG_B, ACC_B, extra={"pad": "x" * 40})  # a different size: the cache follows
    assert CI.identity_of(str(home)) == (ORG_B, ACC_B)


def test_shared_groups_names_only_the_sets_of_two_or_more(tmp_path):
    h1, h2, h3, _ = _three(tmp_path)
    unknown = tmp_path / "unknown"
    unknown.mkdir()
    groups = CI.shared_groups([("p1", str(h1)), ("p2", str(h2)), ("p3", str(h3)), ("p4", str(unknown))])
    assert groups == [["p1", "p2"]]
    assert CI.shared_groups([("p1", str(h1)), ("p3", str(h3))]) == []


# --------------------------------------------------------------------------- #
# the picker
# --------------------------------------------------------------------------- #
def test_the_picker_skips_an_account_with_this_ones_login(tmp_path):
    h1, h2, h3, profiles = _three(tmp_path)
    ready = {"p1": {"status": "ready"}, "p2": {"status": "ready"}, "p3": {"status": "ready"}}
    lease = CO._own_choice(profiles, ready, "p1", str(h1))
    assert lease is not None and lease.profile_id == "p3"
    # Without the home, the current account's own profile names it.
    assert CO._own_choice(profiles, ready, "p1").profile_id == "p3"
    # Only the clone is free: nothing is.
    assert CO._own_choice(profiles, {**ready, "p3": {"status": "unauthenticated"}}, "p1", str(h1)) is None


def test_a_refusal_says_the_login_is_shared_and_offers_only_real_choices(tmp_path):
    h1, h2, h3, profiles = _three(tmp_path)
    states = {
        "p1": {"status": "parked", "parkedUntil": "2099-01-01T03:30:00Z", "parkedReason": {"kind": "quota-exhausted"}},
        "p2": {"status": "ready"},
        "p3": {"status": "parked", "parkedUntil": "2099-01-01T03:30:00Z", "parkedReason": {"kind": "quota-exhausted"}},
    }
    refused = CO._none_free(profiles, states, "p1", str(h1))
    assert '"Clone" is signed in to the same Claude account as "Work"' in refused.sentence
    assert CO.SAME_LOGIN_FIX in refused.sentence
    assert [c["id"] for c in refused.choices] == ["p3"]
    assert refused.choices[0]["title"] == "Other" and "usage limit" in refused.choices[0]["why"]
    # A signed-out account is never offered to try: it needs its sign-in first.
    signed_out = {**states, "p3": {"status": "unauthenticated"}}
    assert CO._none_free(profiles, signed_out, "p1", str(h1)).choices == []


def test_the_build_terminal_path_overrides_iron_proxys_pick_of_a_clone(tmp_path):
    from iron_jarvis.iron_proxy import accounts as _acc
    from iron_jarvis.terminals import pane_accounts as PA

    h1, h2, h3, profiles = _three(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    conv = h1 / "projects" / "C--work" / f"{SID}.jsonl"
    conv.parent.mkdir(parents=True)
    conv.write_text(json.dumps({"type": "user", "sessionId": SID, "cwd": str(work),
                                "timestamp": "2026-11-03T10:00:00Z",
                                "message": {"role": "user", "content": "hi"}}) + "\n", encoding="utf-8")
    fake = _Client(profiles, {k: {"status": "ready"} for k in ("p1", "p2", "p3")})
    fake.picked = {"profile": {"id": "p2", "title": "Clone", "provider": "anthropic"},
                   "env": {"set": {"CLAUDE_CONFIG_DIR": str(h2)}, "unset": []}}
    # Control: the answer's shape is the one the real pick reads.
    assert _acc._lease_of(PA.SPECS["anthropic"], fake.picked).profile_id == "p2"
    session = SimpleNamespace(
        accounts={"anthropic": {"id": "p1", "title": "Work", "source": "iron-proxy", "home": str(h1)}},
        cwd=str(work), claude_session_id=SID,
        limit_info=lambda: None, _limit=None,
    )
    plan = CO.prepare(_Svc(fake), session, trash_root=tmp_path / "trash")
    # The pane opens on Other, never on the clone Iron-Proxy handed back.
    assert plan.lease.profile_id == "p3"
    assert not (h2 / "projects").exists()


# --------------------------------------------------------------------------- #
# the Code view's Continue on another account
# --------------------------------------------------------------------------- #
@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "jarvis"))


def _row(app, work: Path, home: Path):
    rec = {"id": "p1", "title": "Work", "source": "iron-proxy", "home": str(home), "unset": []}
    conv = home / "projects" / "C--work" / f"{SID}.jsonl"
    conv.parent.mkdir(parents=True, exist_ok=True)
    conv.write_text(json.dumps({"type": "user", "sessionId": SID, "cwd": str(work),
                                "timestamp": "2026-11-03T10:00:00Z",
                                "message": {"role": "user", "content": "hi"}}) + "\n", encoding="utf-8")
    row = store.create(app.state.platform.engine, harness="claude-code", folder=str(work), account="p1",
                       account_json=json.dumps(rec), harness_session_id=SID)
    from iron_jarvis.terminals.limit_state import PaneLimit

    S.service_for(app.state.platform)._live(row).limit = PaneLimit(line="You've hit your 5-hour limit")
    return row


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


async def test_continue_never_moves_onto_this_accounts_own_login(app, work, tmp_path, monkeypatch):
    h1, h2, h3, profiles = _three(tmp_path)
    states = {"p1": {"status": "ready"}, "p2": {"status": "ready"},
              "p3": {"status": "parked", "parkedUntil": "2099-01-01T03:30:00Z",
                     "parkedReason": {"kind": "quota-exhausted"}}}
    fake = _Client(profiles, states)
    monkeypatch.setattr(app.state.platform, "iron_proxy", _Svc(fake), raising=False)
    row = _row(app, work, h1)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={})
        assert r.status_code == 409, r.text
        body = r.json()
        assert '"Clone" is signed in to the same Claude account as "Work"' in body["detail"]
        assert body["code"] == "none_free" and [x["id"] for x in body["choices"]] == ["p3"]
        # Asking for the clone by name is refused the same way.
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={"account": "p2"})
        assert r.status_code == 409 and r.json()["code"] == "same_login"
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={"account": "p2", "force": True})
        assert r.status_code == 409 and r.json()["code"] == "same_login"
    # Nothing was told, nothing ended, nothing copied, the session stayed put.
    assert fake.signals == [] and fake.unparked == []
    assert not (h2 / "projects").exists() and not (h3 / "projects").exists()
    assert store.get(app.state.platform.engine, row.id).account == "p1"


async def test_try_it_anyway_ends_the_park_then_moves(app, work, tmp_path, monkeypatch):
    h1, h2, h3, profiles = _three(tmp_path)
    states = {"p1": {"status": "ready"}, "p2": {"status": "ready"},
              "p3": {"status": "parked", "parkedUntil": "2099-01-01T03:30:00Z",
                     "parkedReason": {"kind": "quota-exhausted"}}}
    fake = _Client(profiles, states)
    monkeypatch.setattr(app.state.platform, "iron_proxy", _Svc(fake), raising=False)
    row = _row(app, work, h1)
    async with client(app) as c:
        # Without force a parked account is refused (as before) and nothing ends.
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={"account": "p3"})
        assert r.status_code == 409 and "cannot take over right now" in r.json()["detail"]
        assert fake.unparked == []
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={"account": "p3", "force": True})
        assert r.status_code == 200, r.text
        assert r.json()["account"] == "p3"
        # force with no account is a plain 400.
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={"force": True})
        assert r.status_code == 400
    assert fake.unparked == ["p3"]
    assert [pid for pid, _ in fake.signals] == ["p1"]
    assert (h3 / "projects" / "C--work" / f"{SID}.jsonl").is_file()
    assert store.get(app.state.platform.engine, row.id).account == "p3"


async def test_try_it_anyway_never_skips_a_sign_in(app, work, tmp_path, monkeypatch):
    h1, h2, h3, profiles = _three(tmp_path)
    states = {"p1": {"status": "ready"}, "p2": {"status": "ready"}, "p3": {"status": "unauthenticated"}}
    fake = _Client(profiles, states)
    monkeypatch.setattr(app.state.platform, "iron_proxy", _Svc(fake), raising=False)
    row = _row(app, work, h1)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={"account": "p3", "force": True})
    assert r.status_code == 409 and "needs to sign in again" in r.json()["detail"]
    assert fake.unparked == [] and fake.signals == []
    assert store.get(app.state.platform.engine, row.id).account == "p1"


async def test_two_real_accounts_still_move_as_before(app, work, tmp_path, monkeypatch):
    """Control: a free account with its own login is picked, exactly as before."""
    h1, h2, h3, profiles = _three(tmp_path)
    states = {"p1": {"status": "ready"}, "p2": {"status": "ready"}, "p3": {"status": "ready"}}
    fake = _Client(profiles, states)
    monkeypatch.setattr(app.state.platform, "iron_proxy", _Svc(fake), raising=False)
    row = _row(app, work, h1)
    async with client(app) as c:
        r = await c.post(f"/build/app/sessions/{row.id}/continue-on", json={})
    assert r.status_code == 200, r.text
    assert r.json()["account"] == "p3"


# --------------------------------------------------------------------------- #
# the Iron-Proxy card
# --------------------------------------------------------------------------- #
async def test_the_card_says_which_accounts_share_one_login(app, tmp_path, monkeypatch):
    h1, h2, h3, profiles = _three(tmp_path)

    class CardClient(_Client):
        def usage(self):
            return []

        def discover(self, *_a):
            return []

    fake = CardClient(profiles, {k: {"status": "ready"} for k in ("p1", "p2", "p3")})

    class CardSvc:
        enabled = True
        config = None

        def __init__(self, c):
            self._c = c

        def lease_client(self, timeout_s=None):
            return self._c

        def note_accounts(self, profiles, states):
            pass

        def status(self):
            return {"enabled": True, "running": True}

        def check(self):
            return True

        def client(self):  # type: ignore[override]
            return fake

        def recheck_signed_out(self, *a, **k):
            return {}

    svc = CardSvc(fake)
    monkeypatch.setattr(app.state.platform, "iron_proxy", svc, raising=False)
    async with client(app) as c:
        r = await c.get("/iron-proxy?discover=0")
    assert r.status_code == 200, r.text
    assert r.json()["shared_logins"] == [["p1", "p2"]]
    body = json.dumps(r.json())
    assert "someone@example.com" not in body  # only the two ids were read

    # Every account its own login: no key at all (byte-identical answer).
    _sign_in(h2, ORG_B, "cccccccc-cccc-4ccc-8ccc-cccccccccccc", extra={"pad": "y" * 50})
    async with client(app) as c:
        r = await c.get("/iron-proxy?discover=0")
    assert "shared_logins" not in r.json()
