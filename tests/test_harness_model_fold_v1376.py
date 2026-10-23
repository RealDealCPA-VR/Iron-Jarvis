"""v1.376.0 review — FOLDING IS ROUTING: every row the IJ harness's model menu
offers is a pair the daemon accepts and SERVES, on the provider the menu
names, on the account the menu names.

The harness menu (dashboard lib/harnessModelMenu) lists a keyless Anthropic's
rows under Claude Code and a keyless OpenAI's under Codex, so a pick of one of
them stores ``claude-cli::<id>`` / ``codex-cli::<id>`` where the user used to
store ``anthropic::<id>`` / ``openai::<id>``. The real catalog lists some ids
ONLY under the keyless API (``claude-opus-4-8``, ``claude-sonnet-4-6``,
``claude-fable-5``; ``gpt-4o``, ``gpt-4o-mini``, ``gpt-5.5``): each of those
becomes a CLI pick the catalog never listed under the CLI.

One contract, both sides (``dashboard/__tests__/fixtures/harness-model-fold-v1376.json``):

* this file writes what the REAL daemon answers — ``GET /models`` (keyless
  Anthropic + OpenAI served by the signed-in CLIs, the Claude picker read from
  a scripted handshake), ``GET /build/accounts`` (the fake Iron-Proxy's three
  Claude and two Codex accounts) and the session views;
* ``ij-harness-model-fold-v1376.test.tsx`` renders the real session screen on
  those answers, presses EVERY pickable row of the menu and records the exact
  PATCH body each press sends (``picks`` / ``picks_off``);
* this file replays every recorded body through the REAL ``PATCH
  /build/sessions/{id}``, then runs a real build turn (``POST …/turn``, the
  real router; only the two subscription adapters are the v1368 doubles) and
  asserts the turn ran on the provider, model and account the menu said.

Plus the session stored BEFORE the change as ``anthropic::claude-opus-4-8`` /
``openai::gpt-4o``: it runs on the same adapter and model as its folded pick.

Regenerate (after an intended change): ``IJ_REGEN_HARNESS_FOLD=1 uv run pytest
tests/test_harness_model_fold_v1376.py -k regen``, then
``IJ_REGEN_HARNESS_FOLD=1 npx vitest run __tests__/ij-harness-model-fold-v1376.test.tsx``
in dashboard/.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers import claude_models as cm
from iron_jarvis.providers.manager import ProviderManager
from tests.test_build_accounts_v1368 import Behaviour, enable, isolate, make_fake, picks, wire_doubles
from tests.test_build_sessions_v1367 import asgi, frames_of, open_turn, setup_of

FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "harness-model-fold-v1376.json"
REGEN = os.environ.get("IJ_REGEN_HARNESS_FOLD") == "1"

#: The account's Claude picker (claude 2.1.288 shape; test_claude_catalog_rows_v1300).
LIVE_ROWS = [
    {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)",
     "description": "Opus 5.5 · Best for everyday, complex tasks"},
    {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus",
     "description": "Opus 5.5 · Best for everyday, complex tasks"},
    {"value": "claude-fable-5-1[1m]", "resolvedModel": "claude-fable-5-1", "displayName": "Fable",
     "description": "Fable 5.1 · Most capable for your hardest and longest-running tasks"},
    {"value": "sonnet", "resolvedModel": "claude-sonnet-5-5", "displayName": "Sonnet",
     "description": "Sonnet 5.5 · Efficient for routine tasks"},
    {"value": "haiku", "resolvedModel": "claude-haiku-4-5-20251001", "displayName": "Haiku",
     "description": "Haiku 4.5 · Fastest for quick answers"},
]
ACCOUNT = {"email": "user@example.test", "subscriptionType": "Claude Max"}

#: The session the menu is drawn for: on the Work account, Opus 5.5.
ON_WORK = {"provider": "claude-cli", "model": "claude-opus-5-5", "account": "c-work"}
#: …and with Iron-Proxy off: no account at all.
PLAIN = {"provider": "claude-cli", "model": "claude-opus-5-5"}

#: The CLI a provider's turn is served by (the doubles stand in for both).
SERVED_BY = {"claude-cli": "claude-cli", "anthropic": "claude-cli", "codex-cli": "codex-cli", "openai": "codex-cli"}


def _handshake(timeout) -> str:  # noqa: ARG001 — the chokepoint's signature
    return json.dumps({
        "type": "control_response",
        "response": {"subtype": "success", "request_id": "ij-picker",
                     "response": {"models": LIVE_ROWS, "account": ACCOUNT, "commands": []}},
    }) + "\n"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """No key for anthropic or openai; claude and codex installed (each keyless
    API is SERVED by its CLI); the Claude picker from a scripted handshake;
    our own Iron-Proxy data dir."""
    gen = isolate(tmp_path, monkeypatch)
    next(gen)
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: b in ("claude", "codex")))
    # v1.376.1: grok-cli is not found through `_cli_binary_present` but on
    # disk (~/.grok via GROK_HOME, `cli_detect.grok_session`) and through its
    # own binary lookup — the fixture was written on a PC with the Grok CLI
    # signed in, so it carried two grok-cli rows the CI runner (no Grok) never
    # has, and v1.376.0's gates went red. No Grok here, whatever the PC has.
    from iron_jarvis.providers import cli_detect

    no_grok = tmp_path / "no-grok-home"
    no_grok.mkdir()
    monkeypatch.setenv("GROK_HOME", str(no_grok))
    monkeypatch.setattr(cli_detect, "_grok_binary", lambda: None)
    cm.reset()
    cm.set_home(None)
    monkeypatch.setattr(cm, "_run_handshake", _handshake)
    assert cm.discover(force=True)["error"] is None
    yield
    deadline = time.monotonic() + 5
    while cm._refreshing and time.monotonic() < deadline:
        time.sleep(0.01)
    cm.reset()
    cm.set_home(None)
    for _ in gen:
        pass


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
    yield f
    f.stop()


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


def _read_fixture() -> dict:
    if not FIXTURE.exists():
        return {}
    return json.loads(FIXTURE.read_text(encoding="utf-8").replace("\r\n", "\n"))


#: The fake Iron-Proxy's resets are "now + N": a fixed time keeps the fixture steady.
STEADY_RESET = "2030-01-01T16:12:00Z"


def _steady(value):
    if isinstance(value, dict):
        return {k: (STEADY_RESET if k == "resets_at" and v else _steady(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_steady(v) for v in value]
    return value


def _session_view_for_screen(view: dict) -> dict:
    """The parts of a real session view the screen reads, with the run's own
    folder, id and time replaced (the fixture is checked in)."""
    out = dict(view)
    out.update({"id": "s1", "folder": "C:\\work\\shop", "folder_name": "shop", "session_id": "build:s1",
                "updated_at": "2026-10-23T10:00:00", "title": "Add a cart", "messages": []})
    for k in ("branch", "checkout", "worktree"):
        out.pop(k, None)
    return out


async def _new_session(app, work, setup: dict) -> str:
    status, view = await asgi(app, "POST", "/build/sessions", {"folder": str(work), **setup})
    assert status == 201, view
    return view["id"]


async def _run(app, sid: str, turn_id: str) -> dict:
    status, raw = await open_turn(app, sid, {"text": "hi", "turn_id": turn_id})
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] == "done", frames[-3:]
    return dict(frames)["done"]


# --------------------------------------------------------------------------- #
# the daemon's half of the fixture
# --------------------------------------------------------------------------- #


async def _daemon_answers(tmp_path, work, fake) -> dict:
    off = create_app(str(tmp_path / "home-off"))
    _, models = await asgi(off, "GET", "/models")
    _, accounts_off = await asgi(off, "GET", "/build/accounts")
    sid = await _new_session(off, work, PLAIN)
    _, plain = await asgi(off, "GET", f"/build/sessions/{sid}")

    on = create_app(str(tmp_path / "home-on"))
    await enable(on)
    _, models_on = await asgi(on, "GET", "/models")
    assert models_on == models, "Iron-Proxy on or off, /models lists the same rows"
    _, accounts = await asgi(on, "GET", "/build/accounts")
    sid = await _new_session(on, work, ON_WORK)
    _, on_work = await asgi(on, "GET", f"/build/sessions/{sid}")
    return _steady({
        "models": models["models"],
        "accounts": accounts,
        "accounts_off": accounts_off,
        "session_on_work": _session_view_for_screen(on_work),
        "session_plain": _session_view_for_screen(plain),
    })


async def test_regen_or_check_the_daemons_answers(tmp_path, work, fake):
    now = await _daemon_answers(tmp_path, work, fake)
    if REGEN:
        old = _read_fixture()
        out = {**now, "picks": old.get("picks", []), "picks_off": old.get("picks_off", [])}
        FIXTURE.write_bytes((json.dumps(out, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    before = _read_fixture()
    for key in ("models", "accounts", "accounts_off"):
        assert now[key] == before[key], f"{key}: the daemon's answer moved; regenerate the fixture (docstring)"
    for key in ("session_on_work", "session_plain"):
        for part in ("setup", "account", "account_vendor"):
            assert now[key].get(part) == before[key].get(part), (key, part)


# --------------------------------------------------------------------------- #
# the folded rows really are in the catalog only under the keyless API
# --------------------------------------------------------------------------- #


def test_the_fixture_holds_ids_only_the_keyless_apis_list():
    models = _read_fixture()["models"]
    by = {p: {m["model"] for m in models if m["provider"] == p} for p in SERVED_BY}
    only_api = (by["anthropic"] - by["claude-cli"]) | (by["openai"] - by["codex-cli"])
    assert {"claude-opus-4-8", "claude-sonnet-4-6", "claude-fable-5", "gpt-4o"} <= only_api
    assert all(m["inherited_from"] in ("claude-cli", "codex-cli") for m in models if m["provider"] in ("anthropic", "openai"))


# --------------------------------------------------------------------------- #
# every press of the menu, replayed through the real route and a real turn
# --------------------------------------------------------------------------- #


def _picks(key: str) -> list[dict]:
    got = _read_fixture().get(key) or []
    assert got, f"no {key} in the fixture: run the dashboard half (docstring)"
    return got


async def test_every_pick_under_an_account_is_accepted_and_served_on_that_account(tmp_path, work, fake, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour())
    pairs = _picks("picks")
    folded = [p for p in pairs if p["model"] in ("claude-opus-4-8", "claude-sonnet-4-6", "claude-fable-5", "gpt-4o")]
    assert folded, "the menu offered none of the ids only a keyless API lists"
    seen = set()
    for n, p in enumerate(pairs):
        sid = await _new_session(app, work, ON_WORK)
        status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", p["body"])
        assert status == 200, (p, view)
        setup = setup_of(app, sid)
        want_account = p["body"].get("account") or "c-work"
        provider = p["body"].get("provider") or ("codex-cli" if want_account.startswith("x-") else "claude-cli")
        assert setup.get("provider") == provider and setup.get("model") == p["model"], (p, setup)
        assert setup.get("account") == want_account, (p, setup)
        beh.calls.clear()
        beh.leases.clear()
        fake.queries.clear()
        done = await _run(app, sid, f"fold-{n}")
        # Served by the CLI the menu named, with the model the menu named, on
        # the account the menu named — and nothing else was asked.
        assert [c["provider"] for c in beh.calls] == [SERVED_BY[provider]], (p, beh.calls)
        assert beh.calls[0]["model"] == p["model"], (p, beh.calls)
        assert beh.leases == [(SERVED_BY[provider], want_account)], (p, beh.leases)
        assert {q.get("profileId") for q in picks(fake)} == {want_account}, (p, picks(fake))
        assert done["route"]["provider"] in (provider, SERVED_BY[provider]), (p, done["route"])
        assert done["route"]["account"]["id"] == want_account, (p, done["route"])
        seen.add((provider, p["model"], want_account))
    # The menu offered every model of both CLIs on every account that can serve.
    assert {a for _, _, a in seen} == {"c-work", "c-spare", "x-plus", "x-team"}


async def test_every_pick_with_iron_proxy_off_is_accepted_and_served_on_this_pcs_login(tmp_path, work, monkeypatch):
    app = create_app(str(tmp_path / "home"))
    beh = wire_doubles(app, monkeypatch, Behaviour())
    pairs = [p for p in _picks("picks_off") if p["body"].get("provider") in SERVED_BY]
    assert any(p["model"] == "claude-opus-4-8" for p in pairs)
    for n, p in enumerate(pairs):
        assert set(p["body"]) == {"provider", "model"}, p
        sid = await _new_session(app, work, PLAIN)
        status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", p["body"])
        assert status == 200, (p, view)
        setup = setup_of(app, sid)
        assert (setup.get("provider"), setup.get("model"), setup.get("account")) == (
            p["body"]["provider"], p["model"], None), (p, setup)
        beh.calls.clear()
        beh.leases.clear()
        done = await _run(app, sid, f"plain-{n}")
        provider = p["body"]["provider"]
        assert [c["provider"] for c in beh.calls] == [SERVED_BY[provider]], (p, beh.calls)
        assert beh.calls[0]["model"] == p["model"], (p, beh.calls)
        assert beh.leases == [(SERVED_BY[provider], None)], (p, beh.leases)
        assert done["route"]["reason"] == "explicit", (p, done["route"])


@pytest.mark.parametrize("stored, folded", [
    ({"provider": "anthropic", "model": "claude-opus-4-8"}, {"provider": "claude-cli", "model": "claude-opus-4-8"}),
    ({"provider": "openai", "model": "gpt-4o"}, {"provider": "codex-cli", "model": "gpt-4o"}),
])
async def test_a_pair_stored_before_the_change_runs_exactly_as_its_folded_pick(tmp_path, work, monkeypatch, stored, folded):
    """A session made before the change stores the keyless API's name; the
    menu now marks it under the CLI. Both run the SAME adapter with the SAME
    model (manager.get resolves a keyless API to its CLI), so the fold changes
    the words, never where a turn goes."""
    app = create_app(str(tmp_path / "home"))
    beh = wire_doubles(app, monkeypatch, Behaviour())
    served = []
    for n, setup in enumerate((stored, folded)):
        sid = await _new_session(app, work, setup)
        assert {k: setup_of(app, sid).get(k) for k in ("provider", "model")} == setup
        beh.calls.clear()
        await _run(app, sid, f"stored-{n}")
        served.append([(c["provider"], c["model"]) for c in beh.calls])
    assert served[0] == served[1] == [(SERVED_BY[stored["provider"]], stored["model"])]


async def test_an_account_at_its_limit_picked_from_the_menu_shows_the_limit_card_never_another_account(
    tmp_path, work, fake, monkeypatch,
):
    """The menu lets the user pick an account AT its limit (the chip's list
    does too). The pick is the menu's own body — the account and a model in
    ONE request — and the next turn meets the limit card with Iron-Proxy's
    real reset: nothing runs, nothing is leased from another account, no
    other provider is asked (no silent failover)."""
    from iron_jarvis.iron_proxy import accounts as acc
    from tests.test_build_accounts_v1368 import iso

    app = create_app(str(tmp_path / "home"))
    await enable(app)
    beh = wire_doubles(app, monkeypatch, Behaviour(["never sent"]))
    until = iso(5400)
    fake.states["c-spare"].update({"status": "parked", "parkedUntil": until,
                                   "parkedReason": {"kind": "quota-exhausted"}})
    # The list is the CACHED snapshot (the watch loop refreshes it); read it now.
    await asyncio.to_thread(app.state.platform.iron_proxy.refresh_accounts)
    _, listing = await asgi(app, "GET", "/build/accounts")
    spare = next(a for a in listing["claude"] if a["id"] == "c-spare")
    assert spare["status"] == "limit", spare  # the menu draws "Limit reached · resets …", pickable
    sid = await _new_session(app, work, ON_WORK)
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"account": "c-spare", "model": "claude-opus-4-8"})
    assert status == 200, view
    assert {k: setup_of(app, sid).get(k) for k in ("provider", "model", "account")} == {
        "provider": "claude-cli", "model": "claude-opus-4-8", "account": "c-spare"}
    fake.queries.clear()
    status, raw = await open_turn(app, sid, {"text": "hi", "turn_id": "fold-limit"})
    assert status == 200, raw
    frames = frames_of(raw)
    kinds = [e for e, _ in frames]
    assert kinds[-2:] == ["account_limit", "error"], kinds[-4:]
    card = dict(frames)["account_limit"]
    assert card["account"] == "c-spare" and card["resets_at"] == acc.iso_utc(until) and card["resumable"] is True
    # The router asked Claude Code alone; its pinned lease was refused, so no
    # call ran and no other provider was tried.
    assert beh.attempts == ["claude-cli"] and beh.calls == [] and beh.leases == [], beh.attempts
    assert [q.get("profileId") for q in picks(fake)] == ["c-spare"], "no other account was asked"
