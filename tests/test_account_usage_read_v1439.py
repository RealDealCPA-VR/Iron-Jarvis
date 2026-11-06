"""v1.439.0: "Check usage now" — one account's usage read from its own CLI.

Pinned here (``iron_proxy/usage_read.py``), with a FAKE claude / codex
(``tests/fixtures/fake_usage_cli_v1439.py``, the measured shapes) and the fake
Iron-Proxy (``tests/fixtures/fake_iron_proxy_v1301.py``, ``plan-usage`` on):

* a Claude read sends ``initialize`` + ``get_usage`` and Iron-Proxy is told
  the windows as the ``anthropic-ratelimit-unified-*`` headers in a
  ``status: 200`` signal — stored, nothing parked, no request counted; the
  snapshot is refreshed;
* a Codex read sends ``initialize``, ``initialized``, ``account/rateLimits/
  read`` and Iron-Proxy gets its ``rateLimits``;
* the child runs AS the account (its home variable; none for an adopted
  default home), with no API key, in a private EMPTY folder; a hang is
  bounded and the TREE killed;
* refusals (unknown, Grok, signed out, turned off) start nothing;
* one read per account per 20 s, a second press returns the last answer,
  two presses at once share ONE read;
* the in-use loop reads ONLY accounts a reply runs on, and nothing at all
  when nothing is in use.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from iron_jarvis.iron_proxy import usage_read as U
from iron_jarvis.iron_proxy.client import IronProxyClient

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_CLI = FIXTURES / "fake_usage_cli_v1439.py"
sys.path.insert(0, str(FIXTURES))
from fake_iron_proxy_v1301 import FEATURES_PHASE5, FakeIronProxy  # noqa: E402


class _Svc:
    """The service's surface the reader uses, over a real client to the fake."""

    enabled = True

    def __init__(self, fake: FakeIronProxy) -> None:
        self.fake = fake
        self.c = IronProxyClient(fake.url, fake.token)
        self.refreshed = 0
        self.cache = None
        self.refresh_accounts()
        self.refreshed = 0

    def running(self) -> bool:
        return True

    def has_feature(self, name: str) -> bool:
        return name in (self.fake.features or [])

    def client(self):
        return self.c

    def refresh_accounts(self) -> None:
        self.refreshed += 1
        self.cache = (self.c.profiles(), self.c.states())

    def cached_accounts(self):
        return self.cache


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    U.reset()
    log = tmp_path / "cli-log.jsonl"
    monkeypatch.setenv("FAKE_USAGE_LOG", str(log))
    monkeypatch.delenv("FAKE_USAGE_MODE", raising=False)
    monkeypatch.setattr(U, "_cli_argv", lambda vendor: [sys.executable, str(FAKE_CLI)])
    yield
    U.reset()


@pytest.fixture
def fake(tmp_path):
    f = FakeIronProxy(tmp_path / "ipd", features=list(FEATURES_PHASE5)).start(write_descriptor=False)
    yield f
    f.stop()


def _starts(tmp_path) -> list[dict]:
    log = tmp_path / "cli-log.jsonl"
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def _signals(fake) -> list:
    return [c for c in fake.calls if c[0] == "POST" and c[1].endswith("/signal")]


# --------------------------------------------------------------------------- #
# the measured answers, read defensively
# --------------------------------------------------------------------------- #
def test_claude_answer_becomes_iron_proxys_own_headers():
    got = U.claude_headers({"rate_limits": {
        "five_hour": {"utilization": 42, "resets_at": "2100-01-01T00:00:00Z"},
        "seven_day": {"utilization": 63.5},
    }})
    assert got == {
        "anthropic-ratelimit-unified-5h-utilization": "0.4200",
        "anthropic-ratelimit-unified-5h-reset": "4102444800",
        "anthropic-ratelimit-unified-7d-utilization": "0.6350",
    }


@pytest.mark.parametrize("answer", [
    None, "x", {}, {"rate_limits": "?"}, {"rate_limits": {"five_hour": "?"}},
    {"rate_limits": {"five_hour": {"utilization": "42"}}},
    {"rate_limits": {"five_hour": {"utilization": True}}},
    {"rate_limits": {"five_hour": {"utilization": 900}}},
    {"rate_limits": {"five_hour": {"utilization": -1}}},
    {"rate_limits": {"weekly": {"utilization": 3}}},
])
def test_an_unknown_claude_shape_is_not_known_never_a_number(answer):
    assert U.claude_headers(answer) is None


def test_codex_answer_becomes_iron_proxys_rate_limits():
    got = U.codex_rate_limits({"rateLimits": {
        "primary": {"usedPercent": 37, "windowDurationMins": 300, "resetsAt": 4102444800},
        "secondary": {"usedPercent": "x"}, "planType": "plus",
    }})
    assert got["planType"] == "plus"
    assert got["primary"] == {"usedPercent": 37, "windowDurationMins": 300, "resetsAt": 4102444800}
    assert "secondary" not in got and isinstance(got["observedAt"], int)
    for bad in (None, {}, {"rateLimits": None}, {"rateLimits": {"planType": "plus"}}):
        assert U.codex_rate_limits(bad) is None


# --------------------------------------------------------------------------- #
# one read, end to end with the fakes
# --------------------------------------------------------------------------- #
def test_a_claude_read_is_kept_by_iron_proxy_without_parking_or_counting(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-a-real-key")
    acct = fake.add_profile("anthropic", "Work")
    svc = _Svc(fake)
    read = U.read_usage(svc, acct["id"])
    assert read["ok"] is True and read["throttled"] is False and read["at"].endswith("Z"), read
    # Iron-Proxy was told with a 200: stored, not parked, nothing served.
    sig = _signals(fake)
    assert len(sig) == 1 and sig[0][2]["status"] == 200
    assert sig[0][2]["headers"]["anthropic-ratelimit-unified-5h-utilization"] == "0.4200"
    st = fake.states[acct["id"]]
    assert st["status"] == "ready" and st.get("served", 0) == 0
    assert {w["kind"]: w["usedPercent"] for w in st["windows"]} == {"5h": 42, "7d": 64}
    assert not [c for c in fake.calls if c[1].endswith("/finished")]
    # ... and every reader sees it: the snapshot was refreshed.
    assert svc.refreshed == 1
    from iron_jarvis.harness import build_accounts as BA

    row = BA.find(svc, acct["id"])
    assert row["status"] == "usage" and row["usage"]["percent"] == 64
    # The child: the account's home, no API key, a private EMPTY folder.
    (start,) = _starts(tmp_path)
    assert start["kind"] == "claude"
    assert start["env"]["CLAUDE_CONFIG_DIR"] == acct["cli"]["home"]
    assert start["env"]["ANTHROPIC_API_KEY"] is None
    assert start["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert start["cwd_items"] == [] and "ij-usage-read-" in start["cwd"]
    assert Path(start["cwd"]) != Path.cwd()
    subtypes = [f["request"]["subtype"] for f in start["frames"]]
    assert subtypes == ["initialize", "get_usage"]
    assert start["frames"][1]["request"]["skip_behaviors"] is True
    for flag in ("-p", "--setting-sources", "--strict-mcp-config"):
        assert flag in start["argv"]
    assert U.last_read(acct["id"])["ok"] is True


def test_an_adopted_default_home_sets_no_home_variable(fake, tmp_path, monkeypatch):
    home = tmp_path / "userhome"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "inherited"))
    acct = fake.add_profile("anthropic", "This PC", home=str(home / ".claude"), adopted=True)
    assert U.read_usage(_Svc(fake), acct["id"])["ok"] is True
    (start,) = _starts(tmp_path)
    assert start["env"]["CLAUDE_CONFIG_DIR"] is None


def test_a_codex_read_tells_iron_proxy_the_rate_limits(fake, tmp_path):
    acct = fake.add_profile("openai", "Codex")
    svc = _Svc(fake)
    assert U.read_usage(svc, acct["id"])["ok"] is True
    (start,) = _starts(tmp_path)
    assert start["kind"] == "codex" and start["env"]["CODEX_HOME"] == acct["cli"]["home"]
    assert [f.get("method") for f in start["frames"]] == [
        "initialize", "initialized", "account/rateLimits/read"]
    (sig,) = _signals(fake)
    assert sig[2]["status"] == 200 and sig[2]["rateLimits"]["primary"]["usedPercent"] == 37
    st = fake.states[acct["id"]]
    assert st["status"] == "ready" and st["plan"] == "plus"
    assert {w["kind"] for w in st["windows"]} == {"primary", "secondary"}


@pytest.mark.parametrize("mode,words", [
    ("error", "Claude Code could not say: Not logged in."),
    ("shape", "Claude Code did not return its usage this time. Try again in a minute."),
    ("exit", "Claude Code stopped before it answered."),
])
def test_a_read_that_fails_says_why_and_tells_iron_proxy_nothing(fake, monkeypatch, mode, words):
    monkeypatch.setenv("FAKE_USAGE_MODE", mode)
    acct = fake.add_profile("anthropic", "Work")
    svc = _Svc(fake)
    read = U.read_usage(svc, acct["id"])
    assert read["ok"] is False and read["words"] == words
    assert _signals(fake) == [] and svc.refreshed == 0
    assert U.last_read(acct["id"]) == {k: read[k] for k in ("at", "ok", "words")}


def test_a_hang_is_bounded_and_its_whole_tree_killed(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_USAGE_MODE", "hang")
    monkeypatch.setattr(U, "READ_TIMEOUT_S", 4.0)
    acct = fake.add_profile("openai", "Codex")
    t0 = time.monotonic()
    read = U.read_usage(_Svc(fake), acct["id"])
    assert time.monotonic() - t0 < 30
    assert read["ok"] is False and read["words"] == "Codex did not answer in 4 seconds."
    (start,) = _starts(tmp_path)
    child = start["grandchild"]
    deadline = time.monotonic() + 10
    while psutil.pid_exists(child) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not psutil.pid_exists(child) or psutil.Process(child).status() == psutil.STATUS_ZOMBIE


def test_an_iron_proxy_without_plan_usage_is_not_told(fake):
    fake.features = ["executor-v1"]
    acct = fake.add_profile("anthropic", "Work")
    read = U.read_usage(_Svc(fake), acct["id"])
    assert read["ok"] is False and read["words"] == U.PROXY_UPDATE
    assert _signals(fake) == []


def test_a_missing_cli_is_said(fake, monkeypatch):
    monkeypatch.setattr(U, "_cli_argv", lambda vendor: None)
    acct = fake.add_profile("openai", "Codex")
    assert U.read_usage(_Svc(fake), acct["id"])["words"] == "Codex is not installed on this PC."


# --------------------------------------------------------------------------- #
# refusals start nothing
# --------------------------------------------------------------------------- #
def test_refusals_start_nothing(fake, tmp_path):
    grok = fake.add_profile("xai", "Grok")
    out = fake.add_profile("anthropic", "Out")
    fake.states[out["id"]]["status"] = "unauthenticated"
    off = fake.add_profile("openai", "Off", enabled=False)
    parked_signin = fake.add_profile("openai", "Parked")
    fake.states[parked_signin["id"]].update(
        {"status": "parked", "parkedReason": {"kind": "auth-expired"}})
    svc = _Svc(fake)
    cases = [("prof_nope", "unknown"), (grok["id"], "not_supported"), (out["id"], "sign_in"),
             (off["id"], "off"), (parked_signin["id"], "sign_in")]
    for pid, code in cases:
        with pytest.raises(U.Refused) as got:
            U.read_usage(svc, pid)
        assert got.value.code == code, pid
    off_svc = _Svc(fake)
    off_svc.enabled = False
    with pytest.raises(U.Refused) as got:
        U.read_usage(off_svc, out["id"])
    assert got.value.code == "proxy_off"
    assert _starts(tmp_path) == []


def test_a_limited_account_can_still_be_read(fake):
    acct = fake.add_profile("anthropic", "Work")
    fake.states[acct["id"]].update({"status": "parked", "parkedUntil": "2100-01-01T00:00:00Z",
                                    "parkedReason": {"kind": "quota-exhausted"}})
    svc = _Svc(fake)
    from iron_jarvis.harness import build_accounts as BA

    assert U.can_refresh(BA.find(svc, acct["id"])) is True
    assert U.read_usage(svc, acct["id"])["ok"] is True


# --------------------------------------------------------------------------- #
# the throttle
# --------------------------------------------------------------------------- #
def test_a_second_press_inside_twenty_seconds_returns_the_last_answer(fake, tmp_path, monkeypatch):
    acct = fake.add_profile("anthropic", "Work")
    svc = _Svc(fake)
    first = U.read_usage(svc, acct["id"])
    again = U.read_usage(svc, acct["id"])
    assert again["throttled"] is True and again["at"] == first["at"] and again["ok"] is True
    assert len(_starts(tmp_path)) == 1
    # Past the throttle, a press reads again.
    U._READ_MONO[acct["id"]] -= U.THROTTLE_S + 1
    assert U.read_usage(svc, acct["id"])["throttled"] is False
    assert len(_starts(tmp_path)) == 2


def test_two_presses_at_once_share_one_read(fake, tmp_path):
    acct = fake.add_profile("anthropic", "Work")
    svc = _Svc(fake)
    answers: list[dict] = []
    threads = [threading.Thread(target=lambda: answers.append(U.read_usage(svc, acct["id"])))
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert len(answers) == 2 and sorted(a["throttled"] for a in answers) == [False, True]
    assert len(_starts(tmp_path)) == 1


# --------------------------------------------------------------------------- #
# the in-use loop
# --------------------------------------------------------------------------- #
def _live(account: str, running: bool) -> SimpleNamespace:
    return SimpleNamespace(account=account, turn=SimpleNamespace(running=running) if running is not None else None)


def test_nothing_in_use_means_no_read_at_all(fake, tmp_path, monkeypatch):
    a = fake.add_profile("anthropic", "A")
    svc = _Svc(fake)
    spawned: list = []
    from iron_jarvis.core import procs

    real_spawn = procs.spawn
    monkeypatch.setattr(procs, "spawn", lambda *x, **k: spawned.append(x) or real_spawn(*x, **k))
    platform = SimpleNamespace(
        iron_proxy=svc, hosted=SimpleNamespace(live={"s1": _live(a["id"], False), "s2": _live(a["id"], None)}),
        turn_logs=None, engine=None)
    assert U.poll_in_use(platform) == []
    assert spawned == [] and _starts(tmp_path) == []
    # ...even an account whose last read is ancient.
    U._remember(a["id"], True, "old")
    U._READ_MONO[a["id"]] -= 10_000
    assert U.poll_in_use(platform) == [] and spawned == []


def test_an_account_a_build_code_reply_runs_on_is_reread_every_three_minutes(fake, tmp_path):
    a = fake.add_profile("anthropic", "A")
    b = fake.add_profile("openai", "B")
    svc = _Svc(fake)
    platform = SimpleNamespace(
        iron_proxy=svc,
        hosted=SimpleNamespace(live={"s1": _live(a["id"], True), "s2": _live(b["id"], False)}),
        turn_logs=None, engine=None)
    assert U.poll_in_use(platform) == [a["id"]]
    assert U.poll_in_use(platform) == []  # read under three minutes ago
    U._READ_MONO[a["id"]] -= U.IN_USE_EVERY_S + 1
    assert U.poll_in_use(platform) == [a["id"]]
    assert [s["kind"] for s in _starts(tmp_path)] == ["claude", "claude"]


def test_iron_proxy_off_or_not_running_reads_nothing(fake, tmp_path):
    a = fake.add_profile("anthropic", "A")
    svc = _Svc(fake)
    svc.running = lambda: False  # type: ignore[method-assign]
    platform = SimpleNamespace(iron_proxy=svc, hosted=SimpleNamespace(live={"s": _live(a["id"], True)}),
                               turn_logs=None, engine=None)
    assert U.poll_in_use(platform) is None and _starts(tmp_path) == []


def test_a_pass_while_iron_proxy_is_off_drops_the_row_instead_of_saying_ok(monkeypatch):
    import asyncio

    # Off is not failing and not a healthy loop either (the v1.433.0 rule):
    # the loop calls on_off, never on_tick(True).
    ticks: list = []
    offs: list = []
    monkeypatch.setattr(U, "poll_in_use", lambda platform: None)

    async def run() -> None:
        task = asyncio.create_task(U.usage_loop("P", lambda ok, exc: ticks.append(ok),
                                                interval=0.01, on_off=lambda: offs.append(1)))
        while len(offs) < 2:
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(run())
    assert ticks == [] and len(offs) >= 2


def test_a_chat_code_turn_reads_its_sessions_account(fake, tmp_path):
    from iron_jarvis.core.db import open_db
    from iron_jarvis.harness import sessions as S

    engine = open_db(tmp_path / "db.sqlite")
    work = tmp_path / "work"
    work.mkdir()
    a = fake.add_profile("anthropic", "A")
    row = S.create(engine, str(work), "", {"account": a["id"], "account_name": "A"})
    other = S.create(engine, str(work), "", {})
    logs = SimpleNamespace(running=lambda: [
        SimpleNamespace(turn_id="t1", thread_id=row.id, surface="build", started_at=None,
                        open_asks=lambda p: []),
        SimpleNamespace(turn_id="t2", thread_id=other.id, surface="build", started_at=None,
                        open_asks=lambda p: []),
        SimpleNamespace(turn_id="t3", thread_id="chat_x", surface="chat", started_at=None,
                        open_asks=lambda p: []),
    ])
    platform = SimpleNamespace(iron_proxy=_Svc(fake), hosted=None, turn_logs=logs, engine=engine)
    assert U.in_use_accounts(platform) == [a["id"]]
    assert U.poll_in_use(platform) == [a["id"]]


def test_the_loop_reports_each_pass(monkeypatch):
    import asyncio

    ticks: list = []
    calls: list = []

    def poll(platform):
        calls.append(platform)
        if len(calls) == 2:
            raise RuntimeError("boom")
        return []

    monkeypatch.setattr(U, "poll_in_use", poll)

    async def run() -> None:
        task = asyncio.create_task(U.usage_loop("P", lambda ok, exc: ticks.append(ok), interval=0.01))
        while len(ticks) < 3:
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(run())
    assert ticks[:3] == [True, False, True] and calls[0] == "P"


# --------------------------------------------------------------------------- #
# review (v1.439.0): an account whose home Iron-Proxy does not say
# --------------------------------------------------------------------------- #
def test_an_account_with_no_home_is_refused_and_never_read_as_this_pcs_login(fake, tmp_path, monkeypatch):
    """Every other lease caller refuses an account with no ``cli.home``
    (``accounts.lease``: "an answer with no account home"). A read with the
    home variable set to "" would run the CLI on whatever login this PC has
    and store THAT usage under this account."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "this-pc"))
    acct = fake.add_profile("anthropic", "Homeless")
    fake.profiles[acct["id"]]["cli"] = {"home": ""}
    svc = _Svc(fake)
    with pytest.raises(U.Refused) as got:
        U.read_usage(svc, acct["id"])
    assert got.value.code == "no_home" and got.value.words == U.NO_HOME_WORDS
    assert _starts(tmp_path) == [] and _signals(fake) == []


def test_as_of_is_the_last_read_that_worked_never_a_failed_one(fake, monkeypatch):
    """``usage_read_at`` says when the NUMBER shown was read. A read that
    failed read no number: a page drawn after it must not say "as of just
    now" beside an older number."""
    from iron_jarvis.harness import build_accounts as BA

    acct = fake.add_profile("anthropic", "Work")
    svc = _Svc(fake)
    monkeypatch.setenv("FAKE_USAGE_MODE", "error")
    failed = U.read_usage(svc, acct["id"])
    assert failed["ok"] is False
    assert U.decorate(BA.find(svc, acct["id"]))["usage_read_at"] is None
    assert U.last_ok_at(acct["id"]) is None
    monkeypatch.setenv("FAKE_USAGE_MODE", "ok")
    good = U.read_usage(svc, acct["id"], force=True)
    assert good["ok"] is True
    assert U.decorate(BA.find(svc, acct["id"]))["usage_read_at"] == good["at"]
    monkeypatch.setenv("FAKE_USAGE_MODE", "error")
    U._remember(acct["id"], False, "later failure")
    U._READS[acct["id"]]["at"] = "2999-01-01T00:00:00Z"
    assert U.decorate(BA.find(svc, acct["id"]))["usage_read_at"] == good["at"]


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_after_its_answer_the_cli_is_let_exit_by_itself(fake, tmp_path, monkeypatch, provider):
    """A Claude Code that is killed while it saves ``.claude.json`` can leave
    the user's own file cut short (an adopted account's home IS the user's
    ``~/.claude``). After the answer, stdin closes and the CLI gets a moment
    to exit on its own (the picker handshake's way); only one that lingers
    past the grace is killed."""
    eof = tmp_path / "eof.txt"
    monkeypatch.setenv("FAKE_USAGE_EOF", str(eof))
    acct = fake.add_profile(provider, "Work")
    assert U.read_usage(_Svc(fake), acct["id"])["ok"] is True
    assert eof.exists() and eof.read_text(encoding="utf-8").strip() == "exited by itself"


def test_a_cli_that_lingers_after_its_answer_is_still_killed(fake, tmp_path, monkeypatch):
    eof = tmp_path / "eof.txt"
    monkeypatch.setenv("FAKE_USAGE_EOF", str(eof))
    monkeypatch.setattr(U, "EXIT_GRACE_S", 0.05)
    acct = fake.add_profile("anthropic", "Work")
    t0 = time.monotonic()
    assert U.read_usage(_Svc(fake), acct["id"])["ok"] is True
    assert time.monotonic() - t0 < 10
    time.sleep(0.6)
    assert not eof.exists()
