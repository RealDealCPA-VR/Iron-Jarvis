"""v1.441.0: the review's fixes to "Check usage now" (v1.439.0).

Pinned here, with the v1.439.0 fakes (``tests/fixtures/fake_usage_cli_v1439.py``,
``tests/fixtures/fake_iron_proxy_v1301.py``):

* U1/X2: an Iron-Proxy too old to keep the number (no ``plan-usage``), or one
  that is not answering, is a REFUSAL decided before the CLI starts; the rows
  say ``can_refresh: false`` for it;
* U2: a Build › Code reply parked on a card or a question is not "in use";
* U3: a Build TERMINAL started on an Iron-Proxy account and WORKING is in use
  (its CLI picks the provider); Chat › Code is not read (its adapters report);
* U4: a usage read never inherits an API key or Codex's own sign-in variables;
* U7/X1: Claude Code's measured "no plan" answer says so in words (never "try
  again"), and the in-use loop backs off after answers with no numbers;
* X5: a parent Claude Code session's account variables never reach a child.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from iron_jarvis.iron_proxy import usage_read as U
from iron_jarvis.iron_proxy.client import IronProxyClient

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_CLI = FIXTURES / "fake_usage_cli_v1439.py"
sys.path.insert(0, str(FIXTURES))
from fake_iron_proxy_v1301 import FEATURES_PHASE5, FakeIronProxy  # noqa: E402


class _Svc:
    enabled = True

    def __init__(self, fake: FakeIronProxy, answering: bool = True) -> None:
        self.fake = fake
        self.c = IronProxyClient(fake.url, fake.token)
        self.answering = answering
        self.cache = (self.c.profiles(), self.c.states())

    def running(self) -> bool:
        return True

    def has_feature(self, name: str) -> bool:
        return name in (self.fake.features or [])

    def client(self):
        return self.c if self.answering else None

    def refresh_accounts(self) -> None:
        self.cache = (self.c.profiles(), self.c.states())

    def cached_accounts(self):
        return self.cache


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    U.reset()
    monkeypatch.setenv("FAKE_USAGE_LOG", str(tmp_path / "cli-log.jsonl"))
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


# --------------------------------------------------------------------------- #
# U1 / X2: refused before the CLI starts
# --------------------------------------------------------------------------- #
def test_an_iron_proxy_without_plan_usage_is_refused_before_any_cli_starts(fake, tmp_path):
    from iron_jarvis.harness import build_accounts as BA

    fake.features = ["executor-v1", "pick-profile"]
    acct = fake.add_profile("anthropic", "Work")
    svc = _Svc(fake)
    with pytest.raises(U.Refused) as got:
        U.read_usage(svc, acct["id"])
    assert got.value.code == "proxy_update" and got.value.words == U.PROXY_UPDATE
    assert _starts(tmp_path) == []
    row = BA.find(svc, acct["id"])
    assert U.can_refresh(row) is True  # the row alone cannot tell ...
    assert U.can_refresh(row, svc) is False  # ... the service can
    assert U.decorate(dict(row), svc)["can_refresh"] is False
    assert all(r["can_refresh"] is False for r in BA.accounts_view(svc)["claude"])


def test_an_iron_proxy_that_is_not_answering_is_refused_before_any_cli_starts(fake, tmp_path):
    acct = fake.add_profile("openai", "Codex")
    svc = _Svc(fake, answering=False)
    with pytest.raises(U.Refused) as got:
        U.read_usage(svc, acct["id"])
    assert got.value.code == "proxy_not_answering"
    assert _starts(tmp_path) == []


def test_the_in_use_loop_starts_nothing_on_an_outdated_iron_proxy(fake, tmp_path):
    fake.features = ["executor-v1"]
    acct = fake.add_profile("anthropic", "Work")
    platform = SimpleNamespace(
        iron_proxy=_Svc(fake),
        hosted=SimpleNamespace(live={"s": SimpleNamespace(account=acct["id"], asks={},
                                                           turn=SimpleNamespace(running=True))}),
        terminals=None, turn_logs=None, engine=None)
    assert U.poll_in_use(platform) == []
    assert _starts(tmp_path) == []


def test_the_route_answers_a_409_in_words_for_an_outdated_iron_proxy(fake, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.routes import account_usage

    fake.features = ["executor-v1"]
    acct = fake.add_profile("anthropic", "Work")
    app = FastAPI()
    account_usage.register(app, SimpleNamespace(platform=SimpleNamespace(iron_proxy=_Svc(fake))))
    r = TestClient(app).post(f"/build/accounts/{acct['id']}/usage/refresh")
    assert r.status_code == 409 and r.json()["detail"] == U.PROXY_UPDATE
    assert _starts(tmp_path) == []


# --------------------------------------------------------------------------- #
# U2 / U3: what counts as in use
# --------------------------------------------------------------------------- #
class _Pane:
    def __init__(self, accounts, state, cli=None, alive=True):
        self.accounts = accounts
        self.alive = alive
        self.last_output_at = time.monotonic()  # printed just now (review)
        self._act = SimpleNamespace(state=SimpleNamespace(value=state), cli=cli)

    def activity(self):
        return self._act


def test_a_reply_parked_on_a_card_or_question_is_not_in_use():
    def live(acct, asks):
        return SimpleNamespace(account=acct, asks=asks, turn=SimpleNamespace(running=True))

    platform = SimpleNamespace(
        hosted=SimpleNamespace(live={"a": live("acct_card", {"ask1": object()}),
                                     "b": live("acct_work", {})}),
        terminals=None, turn_logs=None, engine=None)
    assert U.in_use_accounts(platform) == ["acct_work"]


def test_a_working_build_terminal_on_an_account_is_in_use():
    panes = [
        _Pane({"anthropic": {"id": "acct_claude"}, "openai": {"id": "acct_codex"}}, "working", cli="claude"),
        _Pane({"openai": {"id": "acct_idle"}}, "idle", cli="codex"),
        _Pane({"openai": {"id": "acct_blocked"}}, "blocked", cli="codex"),
        _Pane({"anthropic": {"id": None}}, "working", cli="claude"),  # this PC's login
        _Pane({"openai": {"id": "acct_dead"}}, "working", cli="codex", alive=False),
        _Pane({"openai": {"id": "acct_unknown_cli"}}, "working", cli=None),
        _Pane(None, "working", cli="claude"),
    ]
    platform = SimpleNamespace(hosted=None, terminals=SimpleNamespace(sessions=lambda: panes),
                               turn_logs=None, engine=None)
    # Review (moved on purpose): a pane whose CLI is not known counts nothing.
    assert U.in_use_accounts(platform) == ["acct_claude"]


def test_a_real_terminal_manager_is_read_through_its_public_list(tmp_path):
    from iron_jarvis.terminals.manager import TerminalManager

    m = TerminalManager()
    pane = SimpleNamespace(accounts={"anthropic": {"id": "acct_t"}}, alive=True,
                           last_output_at=time.monotonic(),
                           activity=lambda: SimpleNamespace(state=SimpleNamespace(value="working"), cli="claude"))
    m._sessions["p1"] = pane  # type: ignore[assignment]
    assert m.sessions() == [pane]
    platform = SimpleNamespace(hosted=None, terminals=m, turn_logs=None, engine=None)
    assert U.in_use_accounts(platform) == ["acct_t"]


def test_a_chat_code_turn_is_not_read_its_adapters_report_after_every_step(tmp_path):
    """Chat › Code's claude-cli and Codex adapters hand Iron-Proxy the vendor's
    own windows after every model step (``tests/test_build_account_usage_v1368.py``
    pins both). A usage read beside them only started a second CLI."""
    from iron_jarvis.core.db import open_db
    from iron_jarvis.harness import sessions as S

    engine = open_db(tmp_path / "db.sqlite")
    work = tmp_path / "work"
    work.mkdir()
    row = S.create(engine, str(work), "", {"account": "acct_chat", "account_name": "A"})
    logs = SimpleNamespace(running=lambda: [
        SimpleNamespace(turn_id="t1", thread_id=row.id, surface="build", started_at=None,
                        open_asks=lambda p: []),
    ])
    platform = SimpleNamespace(hosted=None, terminals=None, turn_logs=logs, engine=engine)
    assert U.in_use_accounts(platform) == []


# --------------------------------------------------------------------------- #
# U4 / X5: what a usage read's child inherits
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_a_read_never_inherits_an_api_key_or_a_parent_sessions_account(fake, tmp_path, monkeypatch, provider):
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "GEMINI_API_KEY",
                 "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "not-a-real-value")
    monkeypatch.setenv("CLAUDE_CODE_ACCOUNT_UUID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setenv("CLAUDE_CODE_USER_EMAIL", "parent@example.com")
    monkeypatch.setenv("CLAUDE_CODE_ORGANIZATION_UUID", "22222222-2222-4222-8222-222222222222")
    acct = fake.add_profile(provider, "Work")
    assert U.read_usage(_Svc(fake), acct["id"])["ok"] is True
    (start,) = _starts(tmp_path)
    dropped = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "GEMINI_API_KEY",
               "ANTHROPIC_API_KEY", "CLAUDE_CODE_ACCOUNT_UUID", "CLAUDE_CODE_USER_EMAIL",
               "CLAUDE_CODE_ORGANIZATION_UUID")
    leaked = {k: v for k, v in start["env"].items() if k in dropped and v is not None}
    assert leaked == {}, leaked


def test_the_claude_child_env_drops_a_parent_sessions_account(monkeypatch):
    from iron_jarvis.core.claude_markers import CLAUDE_SESSION_MARKERS
    from iron_jarvis.providers.claude_models import child_env

    for name in ("CLAUDE_CODE_ACCOUNT_UUID", "CLAUDE_CODE_USER_EMAIL", "CLAUDE_CODE_ORGANIZATION_UUID"):
        assert name in CLAUDE_SESSION_MARKERS
        monkeypatch.setenv(name, "parent")
    env = {k.upper() for k in child_env()}
    assert not {"CLAUDE_CODE_ACCOUNT_UUID", "CLAUDE_CODE_USER_EMAIL", "CLAUDE_CODE_ORGANIZATION_UUID"} & env


# --------------------------------------------------------------------------- #
# U7 / X1: an answer with no numbers
# --------------------------------------------------------------------------- #
def test_a_login_with_no_plan_says_so_and_never_try_again(fake, monkeypatch):
    monkeypatch.setenv("FAKE_USAGE_MODE", "noplan")
    acct = fake.add_profile("anthropic", "Work")
    read = U.read_usage(_Svc(fake), acct["id"])
    assert read["ok"] is False and read["words"] == U.NO_PLAN_WORDS
    assert "try again" not in read["words"].lower()


def test_an_unknown_shape_still_says_try_again(fake, monkeypatch):
    monkeypatch.setenv("FAKE_USAGE_MODE", "shape")
    acct = fake.add_profile("anthropic", "Work")
    assert "Try again" in U.read_usage(_Svc(fake), acct["id"])["words"]


def _in_use_platform(fake, acct_id):
    return SimpleNamespace(
        iron_proxy=_Svc(fake),
        hosted=SimpleNamespace(live={"s": SimpleNamespace(account=acct_id, asks={},
                                                           turn=SimpleNamespace(running=True))}),
        terminals=None, turn_logs=None, engine=None)


def test_the_loop_backs_off_after_answers_with_no_numbers(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_USAGE_MODE", "shape")
    acct = fake.add_profile("anthropic", "Work")
    platform = _in_use_platform(fake, acct["id"])
    assert U.poll_in_use(platform) == [acct["id"]]
    # One empty answer: the next read waits twice as long.
    U._READ_MONO[acct["id"]] -= U.IN_USE_EVERY_S + 1
    assert U.poll_in_use(platform) == []
    U._READ_MONO[acct["id"]] -= U.IN_USE_EVERY_S
    assert U.poll_in_use(platform) == [acct["id"]]
    # Two in a row: four times as long.
    U._READ_MONO[acct["id"]] -= 2 * U.IN_USE_EVERY_S + 1
    assert U.poll_in_use(platform) == []
    # A read that works resets it.
    monkeypatch.setenv("FAKE_USAGE_MODE", "ok")
    U._READ_MONO[acct["id"]] -= 4 * U.IN_USE_EVERY_S
    assert U.poll_in_use(platform) == [acct["id"]]
    U._READ_MONO[acct["id"]] -= U.IN_USE_EVERY_S + 1
    assert U.poll_in_use(platform) == [acct["id"]]
    assert len(_starts(tmp_path)) == 4


def test_a_login_with_no_plan_is_reread_only_after_the_longest_wait(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_USAGE_MODE", "noplan")
    acct = fake.add_profile("anthropic", "Work")
    platform = _in_use_platform(fake, acct["id"])
    assert U.poll_in_use(platform) == [acct["id"]]
    U._READ_MONO[acct["id"]] -= U.EMPTY_BACKOFF_MAX_S - 1
    assert U.poll_in_use(platform) == []
    U._READ_MONO[acct["id"]] -= 2
    assert U.poll_in_use(platform) == [acct["id"]]
    # ... and a press is not held back by the loop's back-off.
    U._READ_MONO[acct["id"]] -= U.THROTTLE_S + 1
    assert U.read_usage(platform.iron_proxy, acct["id"])["throttled"] is False
    assert len(_starts(tmp_path)) == 3
