"""v1.424.0 (wave 4, D6): an ACCOUNT can be chosen everywhere, Grok too.

The audit (gap D6): only a Chat › Code session could be put on one Iron-Proxy
account. A plain chat, a custom agent that is not a team member, a schedule
and a night task leased Iron-Proxy's FIRST FREE account; and a Grok session
could not be pinned at all (``build_accounts.VENDORS`` knew Claude Code and
Codex only).

Now every door may name an account, checked by ONE rule
(``iron_proxy.choice``: listed by Iron-Proxy, turned on, of the vendor the
work's model runs on — another vendor's account, Iron-Proxy off, an account
gone are refused in words before anything runs) and pinned through the REAL
lease path: every ``/iron/pick`` the work makes is ``pinned=1&profileId=<the
chosen account>`` — that account or a plain refusal, never the next one.

Driven through the REAL app factory, the real routes, the real router and the
real Iron-Proxy service/client against the fake Iron-Proxy
(``tests/fixtures/fake_iron_proxy_v1301.py``). Only the subscription adapters
are doubles, and they make the SAME ``iron_proxy.accounts`` calls the real
ones do (``tests/test_build_accounts_v1368.AccountDouble``).
"""

from __future__ import annotations

import asyncio
import json

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import build_accounts as BA
from iron_jarvis.iron_proxy import choice
from iron_jarvis.iron_proxy import pin as P
from tests.test_build_accounts_v1368 import (
    AccountDouble,
    Behaviour,
    enable,
    isolate,
    make_fake,
    picks,
)
from tests.test_build_sessions_v1367 import asgi, frames_of, open_turn, setup_of, until
from tests.test_chat_turn_stop_v1241 import _scope

CLAUDE = {"provider": "claude-cli", "model": "claude-sonnet-4-5"}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    yield from isolate(tmp_path, monkeypatch)


@pytest.fixture
def fake(tmp_path):
    f = make_fake(tmp_path)
    # Two Grok accounts (Iron-Proxy's ``xai`` CLI lane).
    f.add_profile("xai", "Grok Work", id="g-work", home=str(tmp_path / "homes" / "g-work"))
    f.add_profile("xai", "Grok Home", id="g-home", home=str(tmp_path / "homes" / "g-home"))
    yield f
    f.stop()


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


def wire(app, monkeypatch, beh: Behaviour) -> Behaviour:
    """claude-cli, codex-cli and grok-cli on the REAL router are the doubles."""
    mgr = app.state.platform.providers
    for name in ("claude-cli", "codex-cli", "grok-cli"):
        mgr.register(name, lambda model=None, n=name: AccountDouble(n, model, beh))
    real = mgr.available
    monkeypatch.setattr(mgr, "available",
                        lambda name: True if name in ("claude-cli", "codex-cli", "grok-cli") else real(name))
    return beh


def only_pinned(fake, account: str) -> None:
    got = picks(fake)
    assert got, "no pick was made"
    for p in got:
        assert p.get("profileId") == account and p.get("pinned") == "1", got


def unpinned(fake) -> None:
    got = picks(fake)
    assert got, "no pick was made"
    assert all("profileId" not in p for p in got), got


async def post_stream(app, path: str, body: dict, timeout: float = 30.0) -> tuple[int, str]:
    """POST an SSE route in-process and read it to the end."""
    raw = json.dumps(body).encode()
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    status, got = 0, []

    async def send(msg):
        nonlocal status
        if msg["type"] == "http.response.start":
            status = msg["status"]
        elif msg["type"] == "http.response.body":
            got.append(msg.get("body", b"").decode("utf-8", "replace"))

    await asyncio.wait_for(app(_scope(path, raw), receive, send), timeout=timeout)
    return status, "".join(got)


def chat_body(**extra) -> dict:
    return {"messages": [{"role": "user", "content": "hi"}], **CLAUDE, **extra}


# --------------------------------------------------------------------------- #
# the one rule
# --------------------------------------------------------------------------- #


async def test_the_rule_admits_an_account_of_the_works_vendor_and_says_every_no(app, fake, monkeypatch):
    await enable(app)
    wire(app, monkeypatch, Behaviour())
    p = app.state.platform
    row, why = choice.check(p, "c-spare", "claude-cli")
    assert row["id"] == "c-spare" and why == ""
    pin, why = choice.pin_for(p, "g-work", "grok-cli")
    assert pin.profile_id == "g-work" and pin.ij_provider == "grok-cli" and pin.title == "Grok Work"
    # Another vendor's account is refused — never "fixed" by moving the model.
    _row, why = choice.check(p, "x-plus", "claude-cli", who="chat")
    assert "“Codex Plus” is a Codex account" in why and "Claude Code" in why
    # Not a subscription model.
    _row, why = choice.check(p, "c-spare", "ollama", "llama3")
    assert "only applies to work on Claude Code, Codex or Grok" in why
    # Gone, turned off.
    assert choice.check(p, "nope", "claude-cli")[1] == choice.UNKNOWN_WORDS
    fake.profiles["c-spare"]["enabled"] = False
    await asyncio.to_thread(p.iron_proxy.refresh_accounts)
    assert "is turned off" in choice.check(p, "c-spare", "claude-cli")[1]
    # Nothing named: nothing to say.
    assert choice.check(p, "", "claude-cli") == (None, "")


async def test_iron_proxy_off_refuses_a_chosen_account_in_words(app):
    assert choice.check(app.state.platform, "c-work", "claude-cli")[1] == choice.OFF_WORDS


# --------------------------------------------------------------------------- #
# a plain chat
# --------------------------------------------------------------------------- #


async def test_a_plain_chat_on_a_chosen_account_leases_only_that_account(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour(["Hello from Spare."]))
    fake.queries.clear()
    status, raw = await post_stream(app, "/chat/stream", chat_body(account="c-spare"))
    assert status == 200, raw
    done = dict(frames_of(raw))["done"]
    assert done["route"]["account"] == {"id": "c-spare", "name": "Spare"}
    assert beh.leases and all(lease == ("claude-cli", "c-spare") for lease in beh.leases)
    only_pinned(fake, "c-spare")
    assert P.current() is None, "the pin leaked out of the turn"


async def test_the_post_lane_pins_the_same_account(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    fake.queries.clear()
    status, out = await asgi(app, "POST", "/chat", chat_body(account="c-spare"))
    assert status == 200, out
    assert out["route"]["account"] == {"id": "c-spare", "name": "Spare"}
    assert beh.leases == [("claude-cli", "c-spare")]
    only_pinned(fake, "c-spare")


async def test_a_plain_chat_with_no_account_leases_as_before(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    fake.queries.clear()
    status, raw = await post_stream(app, "/chat/stream", chat_body())
    assert status == 200, raw
    done = dict(frames_of(raw))["done"]
    assert "account" not in done["route"]
    assert beh.leases == [("claude-cli", "c-work")]  # Iron-Proxy's first free account
    unpinned(fake)


async def test_another_vendors_account_is_a_409_before_anything_runs(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    fake.queries.clear()
    status, raw = await post_stream(app, "/chat/stream", chat_body(account="x-plus"))
    assert status == 409
    assert "“Codex Plus” is a Codex account" in json.loads(raw)["detail"]
    status, out = await asgi(app, "POST", "/chat", chat_body(account="g-work"))
    assert status == 409 and "is a Grok account" in out["detail"]
    assert beh.leases == [] and beh.attempts == [] and picks(fake) == []


async def test_a_chosen_account_with_iron_proxy_off_is_refused_never_run_on_this_login(app, monkeypatch):
    beh = wire(app, monkeypatch, Behaviour())
    status, raw = await post_stream(app, "/chat/stream", chat_body(account="c-work"))
    assert status == 409 and json.loads(raw)["detail"] == choice.OFF_WORDS
    assert beh.attempts == []


# --------------------------------------------------------------------------- #
# a custom agent (not a team member)
# --------------------------------------------------------------------------- #


async def _agent(app, name: str, **extra) -> None:
    status, body = await asgi(app, "POST", "/agents", {
        "name": name, "system_prompt": "You help.", "tools": ["read_file"], **CLAUDE, **extra,
    })
    assert status in (200, 201), body


async def test_a_custom_agents_own_account_pins_every_run(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    await _agent(app, "scout", account="c-spare")
    fake.queries.clear()
    status, view = await asgi(app, "POST", "/agents/scout/spawn", {"task": "Say hi.", "wait": True})
    assert status == 200, view
    assert beh.leases and all(lease == ("claude-cli", "c-spare") for lease in beh.leases), beh.leases
    only_pinned(fake, "c-spare")


async def test_a_custom_agent_with_no_account_leases_as_before(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    await _agent(app, "scout")
    fake.queries.clear()
    status, view = await asgi(app, "POST", "/agents/scout/spawn", {"task": "Say hi.", "wait": True})
    assert status == 200, view
    assert beh.leases and beh.leases[0] == ("claude-cli", "c-work")
    unpinned(fake)


async def test_an_agents_account_of_another_vendor_is_refused_when_saved(app, fake, monkeypatch):
    await enable(app)
    wire(app, monkeypatch, Behaviour())
    status, body = await asgi(app, "POST", "/agents", {
        "name": "scout", "system_prompt": "You help.", "tools": [], **CLAUDE, "account": "g-work",
    })
    assert status == 422 and "is a Grok account" in json.dumps(body)


# --------------------------------------------------------------------------- #
# a schedule
# --------------------------------------------------------------------------- #


async def _schedule(app, name: str, payload: dict) -> tuple[int, dict]:
    return await asgi(app, "POST", "/schedules", {
        "name": name, "cron": "0 9 * * *", "kind": "task", "payload": payload,
    })


async def test_a_schedules_account_pins_its_fire(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    status, body = await _schedule(app, "rounds", {"task": "Say hi.", **CLAUDE, "account": "c-spare"})
    assert status == 200, body
    fake.queries.clear()
    status, ran = await asgi(app, "POST", "/schedules/rounds/run", {})
    assert status == 200 and ran["last_status"] == "ok", ran
    assert beh.leases and all(lease == ("claude-cli", "c-spare") for lease in beh.leases)
    only_pinned(fake, "c-spare")
    assert P.current() is None


async def test_a_schedules_account_is_checked_when_saved_and_when_it_fires(app, fake, monkeypatch):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    status, body = await _schedule(app, "wrong", {"task": "Hi.", **CLAUDE, "account": "x-plus"})
    assert status == 409 and "is a Codex account" in body["detail"]
    status, body = await _schedule(app, "later", {"task": "Hi.", **CLAUDE, "account": "c-spare"})
    assert status == 200
    # Turned off since: the fire is refused in words, no session, no lease.
    fake.profiles["c-spare"]["enabled"] = False
    await asyncio.to_thread(app.state.platform.iron_proxy.refresh_accounts)
    fake.queries.clear()
    status, ran = await asgi(app, "POST", "/schedules/later/run", {})
    assert ran["last_status"] == "error" and "turned off" in ran["last_detail"], ran
    assert ran["last_session_id"] == "" and beh.attempts == [] and picks(fake) == []


# --------------------------------------------------------------------------- #
# Grok in Chat › Code
# --------------------------------------------------------------------------- #


async def test_grok_accounts_are_listed_and_a_grok_session_is_pinned(app, fake, monkeypatch, tmp_path):
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour(["Hello from Grok."]))
    status, view = await asgi(app, "GET", "/build/accounts")
    assert [r["id"] for r in view["grok"]] == ["g-work", "g-home"]
    assert view["grok"][0]["vendor_label"] == "Grok" and view["grok"][0]["default"] is True
    work = tmp_path / "work"
    work.mkdir()
    status, made = await asgi(app, "POST", "/build/sessions", {
        "folder": str(work), "provider": "grok-cli", "model": "grok-build-0.1", "account": "g-home",
    })
    assert status == 201, made
    sid = made["id"]
    assert setup_of(app, sid)["account"] == "g-home"
    fake.queries.clear()
    status, raw = await open_turn(app, sid, {"text": "hi", "turn_id": "grok-1"})
    assert status == 200, raw
    done = dict(frames_of(raw))["done"]
    assert done["route"]["account"]["id"] == "g-home"
    assert beh.leases and all(lease == ("grok-cli", "g-home") for lease in beh.leases)
    only_pinned(fake, "g-home")


async def test_choosing_a_grok_account_moves_a_session_to_grok(app, fake, monkeypatch, tmp_path):
    await enable(app)
    wire(app, monkeypatch, Behaviour())
    work = tmp_path / "work"
    work.mkdir()
    status, made = await asgi(app, "POST", "/build/sessions", {"folder": str(work), **CLAUDE})
    sid = made["id"]
    status, view = await asgi(app, "PATCH", f"/build/sessions/{sid}", {"account": "g-work"})
    assert status == 200, view
    setup = setup_of(app, sid)
    assert setup["account"] == "g-work" and setup["provider"] == "grok-cli"
    assert BA.vendor_of(app.state.platform, "grok-cli") == "grok"


# --------------------------------------------------------------------------- #
# a night task
# --------------------------------------------------------------------------- #


async def test_a_night_tasks_account_is_kept_checked_and_pins_its_session(app, fake, monkeypatch, tmp_path):
    from iron_jarvis.daemon.routes import night as night_routes
    from iron_jarvis.harness import night as N
    from tests.test_build_git_v1375 import make_repo, no_real_gh
    from tests.test_night_shift_run_v1398 import _local

    no_real_gh(monkeypatch)
    await enable(app)
    beh = wire(app, monkeypatch, Behaviour())
    repo = make_repo(tmp_path / "proj")
    # (A night task's model must be in the model menu: Claude Code's own default.)
    plan = {"provider": "claude-cli", "model": "subscription"}
    status, task = await asgi(app, "POST", "/build/night", {
        "folder": str(repo), "words": "NIGHT-1: say hi", **plan, "account": "c-spare",
    })
    assert status == 201, task
    assert task["account"] == "c-spare"
    status, wrong = await asgi(app, "POST", "/build/night", {
        "folder": str(repo), "words": "NIGHT-2", **plan, "account": "x-team",
    })
    assert status == 409 and "is a Codex account" in wrong["detail"]

    monkeypatch.setattr(N, "CLOCK", lambda: _local(23, 30))
    fake.queries.clear()
    started = await night_routes.start_task(app.state.d, N.get_task(app.state.platform.engine, task["id"]))
    assert started is True
    row = N.get_task(app.state.platform.engine, task["id"])
    assert setup_of(app, row["thread_id"])["account"] == "c-spare"
    assert await until(lambda: beh.leases, 30), "the night turn never leased"
    assert all(lease == ("claude-cli", "c-spare") for lease in beh.leases), beh.leases
    only_pinned(fake, "c-spare")


def test_an_older_night_task_table_gains_the_account_column_and_reads_as_before(tmp_path):
    """``NightTaskRecord.account`` is ADDITIVE: boot (``open_db`` in a clean
    interpreter — the late-model list registers the table) adds it to an
    older table; an old row reads ``account: ""`` (its vendor's Default, as
    it ran before)."""
    import sqlite3
    import subprocess
    import sys

    home = tmp_path / ".ironjarvis"
    home.mkdir()
    db = home / "ironjarvis.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE nighttaskrecord (id VARCHAR PRIMARY KEY, folder VARCHAR, words VARCHAR, "
        "provider VARCHAR, model VARCHAR, team BOOLEAN, try_on_json VARCHAR, position INTEGER, "
        "state VARCHAR, thread_id VARCHAR, turn_id VARCHAR, branch VARCHAR, notes_json VARCHAR, "
        "main_before_json VARCHAR, ran_s FLOAT, night VARCHAR, commit_sha VARCHAR, "
        "morning_json VARCHAR, created_at DATETIME, updated_at DATETIME, started_at DATETIME, "
        "ended_at DATETIME)"
    )
    con.execute(
        "INSERT INTO nighttaskrecord VALUES ('night_old', 'C:/w', 'old task', '', '', 0, '', 1, "
        "'queued', '', '', '', '', '', 0, '', '', '', '2026-10-01 00:00:00', '2026-10-01 00:00:00', "
        "NULL, NULL)"
    )
    con.commit()
    con.close()
    script = "\n".join((
        "import json, sqlite3",
        "from iron_jarvis.core.db import open_db",
        "from iron_jarvis.harness import night as N",
        f"engine = open_db(r{str(db)!r})",
        f"con = sqlite3.connect(r{str(db)!r})",
        "cols = [r[1] for r in con.execute('PRAGMA table_info(nighttaskrecord)')]",
        "con.close()",
        "task = N.get_task(engine, 'night_old')",
        "print(json.dumps({'cols': cols, 'account': task['account'], 'words': task['words']}))",
    ))
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    got = json.loads([ln for ln in out.stdout.splitlines() if ln.startswith("{")][-1])
    assert "account" in got["cols"]
    assert got["account"] == "" and got["words"] == "old task"
