"""Second review, group 15 part A (v1.515.0): the account manager's secrets
and the account a coding tool really runs on.

* A3 — the account manager prints its access token on start into
  ``<home>/logs/iron-proxy.log``. Iron Jarvis masks it IN PLACE (same length,
  every other byte kept) at each start, an earlier start's line too.
* A10 — the usage check's child gets a terminal's scrubbed environment: the
  install bearer (``IRONJARVIS_TOKEN``) never reaches the vendor's program.
* A8 — every account of a provider turned off is said in words, never a
  silent run on this PC's own sign-in.
* A6 — Claude Code saying it runs on an API key (``apiKeySource`` in its
  ``init`` frame) is said once by name, and the usage line no longer claims
  "at list price" on a subscription.
* A13 — a ``proxy.json`` naming a host that is not this PC is never sent the
  token.

No real account manager and no real coding tool is started: the manager is
the in-process ``FakeIronProxy``; a "start" fakes ``Popen``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.iron_proxy import accounts
from iron_jarvis.iron_proxy import service as svc_mod
from iron_jarvis.iron_proxy.client import IronProxyError
from iron_jarvis.iron_proxy.service import IronProxyService

FIXTURES = Path(__file__).parent / "fixtures"
sys.path.insert(0, str(FIXTURES))
from fake_iron_proxy_v1301 import FakeIronProxy  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Never the user's real ~/.iron-proxy, a real bundle or a real node."""
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    yield


def _cfg(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return SimpleNamespace(home=home, iron_proxy_enabled=True)


# --------------------------------------------------------------------------- #
# A3: the token is masked in the log, in place
# --------------------------------------------------------------------------- #
OLD = "old-run-token-value"
NEW = "this-run-token-value"


def _log_bytes(old: str = OLD, new: str = NEW) -> bytes:
    return (
        "\n--- 2026-11-15 09:00:00 Iron Jarvis starting Iron-Proxy\n"
        "Iron-Proxy listening on http://127.0.0.1:50001\n"
        "  OpenAI-compatible:    http://127.0.0.1:50001/v1/chat/completions\n"
        f"  Control API token:    {old}\r\n"
        "  Descriptor:           C:\\data\\proxy.json\n"
        "some later line from the manager\n"
        "\n--- 2026-11-16 09:00:00 Iron Jarvis starting Iron-Proxy\n"
        "Iron-Proxy listening on http://127.0.0.1:50002\n"
        f"  Control API token:    {new}\n"
        "  Descriptor:           C:\\data\\proxy.json\n"
        f"a line that quotes it again: {new} (and stops)\n"
    ).encode("utf-8")


def test_a3_both_token_lines_are_masked_in_place_and_nothing_else_moves(tmp_path):
    log = tmp_path / "iron-proxy.log"
    before = _log_bytes()
    log.write_bytes(before)
    svc_mod.mask_token_in_log(log, NEW)
    after = log.read_bytes()
    assert len(after) == len(before)
    assert OLD.encode() not in after and NEW.encode() not in after
    expected = before.replace(OLD.encode(), b"*" * len(OLD)).replace(NEW.encode(), b"*" * len(NEW))
    assert after == expected  # every other byte intact (the CRLF line included)


def test_a3_a_token_line_is_masked_even_when_the_token_is_not_known(tmp_path):
    log = tmp_path / "iron-proxy.log"
    before = _log_bytes()
    log.write_bytes(before)
    svc_mod.mask_token_in_log(log, None)
    after = log.read_bytes()
    assert len(after) == len(before)
    assert OLD.encode() not in after
    # the token line itself is masked; a stray copy needs the token to be known
    assert f"Control API token:    {'*' * len(NEW)}".encode() in after


def test_a3_a_missing_or_unopenable_log_never_raises(tmp_path, monkeypatch):
    svc_mod.mask_token_in_log(tmp_path / "nope.log", NEW)
    folder = tmp_path / "a-folder.log"
    folder.mkdir()
    svc_mod.mask_token_in_log(folder, NEW)

    def locked(*a, **k):
        raise PermissionError(13, "The process cannot access the file")

    log = tmp_path / "iron-proxy.log"
    log.write_bytes(_log_bytes())
    monkeypatch.setattr(svc_mod, "open", locked, raising=False)
    svc_mod.mask_token_in_log(log, NEW)  # skipped, tried again at the next start
    assert log.read_bytes() == _log_bytes()


class _FakeChild:
    """What ``subprocess.Popen`` hands back, for a 'child' that is this test
    process (the in-process fake manager writes this pid into proxy.json)."""

    def __init__(self) -> None:
        self.pid = os.getpid()
        self.returncode = None

    def poll(self):
        return None


@pytest.mark.timeout(60)
def test_a3_a_start_masks_this_starts_line_and_an_earlier_one(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle.mjs"
    bundle.write_text("// not run", encoding="utf-8")
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(bundle))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_NODE", sys.executable)
    s = IronProxyService(_cfg(tmp_path), register=False)
    log = s.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(f"  Control API token:    {OLD}\n".encode())
    fakes: list[FakeIronProxy] = []

    def popen(argv, *, stdout, **kw):
        fake = FakeIronProxy(tmp_path / "ipd").start()
        fakes.append(fake)
        # what the real bundle prints right after it writes proxy.json
        stdout.write(f"Iron-Proxy listening on {fake.url}\n  Control API token:    {fake.token}\n".encode())
        stdout.flush()
        return _FakeChild()

    monkeypatch.setattr(svc_mod.subprocess, "Popen", popen)
    try:
        st = s.start()
        assert st["running"] is True and st["owned"] is True, st
        token = fakes[0].token
        text = log.read_bytes()
        assert token.encode() not in text and OLD.encode() not in text
        assert f"Control API token:    {'*' * len(token)}".encode() in text
    finally:
        # never let stop() kill "the child" (this test process)
        s._proc, s._owned, s._pid = None, False, None
        s.stop()
        for f in fakes:
            f.stop()


def test_a3_a_start_that_reuses_a_running_manager_masks_the_log_too(tmp_path):
    """An earlier run's child printed its token; this start finds it running."""
    fake = FakeIronProxy(tmp_path / "ipd").start()
    try:
        s = IronProxyService(_cfg(tmp_path), register=False)
        log = s.log_path()
        log.parent.mkdir(parents=True, exist_ok=True)
        rotated = log.with_suffix(".log.1")
        log.write_bytes(f"x\n  Control API token:    {fake.token}\ny\n".encode())
        rotated.write_bytes(f"  Control API token:    {OLD}\n".encode())
        st = s.start()
        assert st["running"] is True and st["owned"] is False
        assert fake.token.encode() not in log.read_bytes()
        assert log.read_bytes() == f"x\n  Control API token:    {'*' * len(fake.token)}\ny\n".encode()
        assert OLD.encode() not in rotated.read_bytes()
        s.stop()
    finally:
        fake.stop()


def test_a3_status_never_carries_the_token(tmp_path):
    fake = FakeIronProxy(tmp_path / "ipd").start()
    try:
        s = IronProxyService(_cfg(tmp_path), register=False)
        st = s.start()
        assert st["running"] is True
        assert fake.token not in json.dumps(st)
        s.stop()
    finally:
        fake.stop()


# --------------------------------------------------------------------------- #
# A13: proxy.json must name THIS PC
# --------------------------------------------------------------------------- #
def test_a13_a_descriptor_naming_another_host_is_not_a_running_manager(tmp_path, caplog, monkeypatch):
    fake = FakeIronProxy(tmp_path / "ipd").start()
    try:
        s = IronProxyService(_cfg(tmp_path), register=False)
        assert s.locate() is not None  # the loopback one is used
        # Whatever answers at the other address would be handed the token:
        # here every address "answers", so only the address check stands.
        asked: list[str] = []
        monkeypatch.setattr(svc_mod, "_health",
                            lambda url, timeout=2.0: asked.append(url) or {"ok": True, "features": ["executor-v1"]})
        desc = tmp_path / "ipd" / "proxy.json"
        for url in ("http://example.invalid:8791", "http://192.168.1.20:8791", "http://10.0.0.5"):
            desc.write_text(json.dumps({"url": url, "token": fake.token, "pid": os.getpid()}))
            with caplog.at_level(logging.INFO):
                assert s.locate() is None, url
            assert fake.token not in caplog.text
        assert asked == []  # never even probed
        assert "not this PC" in caplog.text
        for url in ("http://localhost:1", "http://[::1]:1", "http://127.0.0.2:1"):
            assert svc_mod._loopback_url(url), url
        for url in ("http://127.0.0.1.example.invalid", "file:///x", "http://0.0.0.0:1", ""):
            assert not svc_mod._loopback_url(url), url
    finally:
        fake.stop()


# --------------------------------------------------------------------------- #
# A10: the usage check's child never gets the install token
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("vendor", ["claude", "codex"])
def test_a10_the_usage_checks_child_gets_no_install_token(monkeypatch, vendor):
    from iron_jarvis.iron_proxy import usage_read as U

    monkeypatch.setenv("IRONJARVIS_TOKEN", "test-token-value")
    monkeypatch.setenv("IRONJARVIS_MCP_TOKEN", "test-token-value")
    monkeypatch.setenv("IJ_A10_KEEP", "kept")
    lease = accounts.Lease("p1", "Work", "anthropic" if vendor == "claude" else "openai",
                           env_set={"CLAUDE_CONFIG_DIR" if vendor == "claude" else "CODEX_HOME": "home-dir"},
                           ij_provider="claude-cli" if vendor == "claude" else "codex-cli")
    env = U._env(vendor, lease)
    names = {k.upper() for k in env}
    assert "IRONJARVIS_TOKEN" not in names and "IRONJARVIS_MCP_TOKEN" not in names
    assert "test-token-value" not in env.values()
    assert env.get("IJ_A10_KEEP") == "kept"  # the rest of the environment stays
    assert ("home-dir" in env.values())  # the lease is still applied on top


# --------------------------------------------------------------------------- #
# A8: every account turned off is said, never a silent default login
# --------------------------------------------------------------------------- #
class _NoProfile:
    def pick(self, provider, lane="cli", **kw):
        raise IronProxyError("NO_PROFILE", "No enabled profile.", status=404)


@pytest.fixture
def _no_profile(monkeypatch):
    previous = accounts.set_client_source(lambda: _NoProfile())
    monkeypatch.setattr(accounts, "_NOTED", set())
    yield
    accounts.set_client_source(previous)


def _snap(*profiles):
    return (list(profiles), {})


def test_a8_every_account_turned_off_is_refused_in_words(monkeypatch, _no_profile):
    monkeypatch.setattr(accounts, "_cached_snapshot", lambda: _snap(
        {"id": "a", "provider": "anthropic", "lane": "cli", "enabled": False, "title": "Work"},
        {"id": "b", "provider": "anthropic", "lane": "cli", "enabled": False, "title": "Home"},
        {"id": "c", "provider": "openai", "lane": "cli", "enabled": True, "title": "Other"},
    ))
    with pytest.raises(Exception) as e:
        accounts.lease("claude-cli")
    assert getattr(e.value, "no_failover", False) is True
    assert str(e.value) == (
        "claude-cli: Every Claude account is turned off on the Several accounts card in "
        "Settings › Connections. "
        "Turn one on, or remove them to use this PC's own sign-in."
    )


def test_a8_no_account_at_all_or_no_snapshot_runs_as_before(monkeypatch, _no_profile):
    # no account for this provider (another provider's off account is not it)
    monkeypatch.setattr(accounts, "_cached_snapshot", lambda: _snap(
        {"id": "c", "provider": "openai", "lane": "cli", "enabled": False, "title": "Other"},
    ))
    assert accounts.lease("claude-cli") is None
    # an account manager that has not been read yet: as before
    monkeypatch.setattr(accounts, "_cached_snapshot", lambda: None)
    assert accounts.lease("claude-cli") is None


# --------------------------------------------------------------------------- #
# A6: Claude Code on an API key says so, and is not "at list price"
# --------------------------------------------------------------------------- #
def _claude_driver(got: list):
    from iron_jarvis.hosted import claude as C

    return C.ClaudeDriver(write=None, emit=got.append, ask=lambda a: None,
                          on_session_id=lambda s: None, mode="ask", cwd="")


def _init(source: str | None) -> dict:
    frame = {"type": "system", "subtype": "init", "cwd": "C:\\work\\demo",
             "session_id": "s-1", "model": "claude-opus-5-5", "permissionMode": "default"}
    if source is not None:
        frame["apiKeySource"] = source
    return frame


_RESULT = {"type": "result", "subtype": "success", "result": "ok", "total_cost_usd": 0.42,
           "usage": {"input_tokens": 10, "output_tokens": 5}}

KEY_NOTICE = ("Claude Code is using an API key from this PC's environment (ANTHROPIC_API_KEY), "
              "not the signed-in account, so this session is billed per use.")


def test_a6_an_api_key_is_said_once_by_name_and_is_not_list_price(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-token-value")
    got: list = []
    d = _claude_driver(got)
    d.on_message(_init("ANTHROPIC_API_KEY"))
    d.on_message(_init("ANTHROPIC_API_KEY"))  # Claude Code sends init again per message
    d.on_message(dict(_RESULT))
    notices = [e["text"] for e in got if e.get("type") == "notice"]
    assert notices.count(KEY_NOTICE) == 1, notices
    assert all("test-token-value" not in json.dumps(e) for e in got)
    (usage,) = [e for e in got if e.get("type") == "usage"]
    assert usage["list_price"] is False
    assert usage["cost_usd"] == 0.42 and usage["cost_reported_by"] == "Claude Code"


@pytest.mark.parametrize("source", ["none", None])
def test_a6_the_signed_in_account_stays_at_list_price(source):
    got: list = []
    d = _claude_driver(got)
    d.on_message(_init(source))
    d.on_message(dict(_RESULT))
    assert not [e for e in got if e.get("type") == "notice" and "API key" in e.get("text", "")]
    (usage,) = [e for e in got if e.get("type") == "usage"]
    assert usage["list_price"] is True and "cost_reported_by" not in usage


def test_a6_a_helper_or_console_key_is_said_too():
    for source, words in (("apiKeyHelper", "its API key helper (apiKeyHelper)"),
                          ("/login managed key", "the API key saved by its own sign-in")):
        got: list = []
        d = _claude_driver(got)
        d.on_message(_init(source))
        d.on_message(dict(_RESULT))
        notices = [e["text"] for e in got if e.get("type") == "notice"]
        assert any(words in n and "billed per use" in n for n in notices), notices
        (usage,) = [e for e in got if e.get("type") == "usage"]
        assert usage["list_price"] is False


def test_chats_own_claude_code_child_never_gets_the_daemons_credentials(monkeypatch):
    """Lead review of A10: the same environment guard every Claude Code child
    of the daemon starts from (chat's own child, the model-list read)."""
    from iron_jarvis.providers.claude_models import child_env

    monkeypatch.setenv("IRONJARVIS_TOKEN", "test-token-value")
    monkeypatch.setenv("IRONJARVIS_MCP_TOKEN", "test-token-value")
    env = child_env()
    assert "IRONJARVIS_TOKEN" not in env and "IRONJARVIS_MCP_TOKEN" not in env
    assert not any("test-token-value" == v for v in env.values())
    # a caller's own base is guarded the same way, case-blind
    env = child_env({"ironjarvis_token": "test-token-value", "PATH": "x"})
    assert list(env) and "ironjarvis_token" not in env
