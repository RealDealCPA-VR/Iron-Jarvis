"""v1.413.0 review — OpenCode in Build's Code view: the defects the
independent review found, each pinned red-first.

THE PERMISSION MODEL below is OpenCode 1.18.18's own, read from the installed
program (``opencode.exe``'s bundled source, the Permission and Agent modules
and the config loader), so the tests judge the ENVIRONMENT Iron Jarvis builds
by what OpenCode would really decide, not by the env's shape:

* the config's ``permission`` objects are merged in load order, and the
  environment's ``OPENCODE_PERMISSION`` is merged LAST
  (``Q.permission = r(Q.permission, JSON.parse(OPENCODE_PERMISSION))``); a
  merged key that already exists keeps its PLACE (a JS object), a new key is
  appended;
* the legacy ``tools`` map becomes rules placed BEFORE the permission object
  (``Q.permission = r(J, Q.permission)``; write/edit/patch -> ``edit``);
* every rule is ``{permission, pattern, action}`` in key order
  (``fromConfig``), and ``evaluate`` takes the LAST rule whose permission and
  pattern both match (``findLast``; a wildcard ``*``/``?``, case-insensitive);
* each agent's ruleset is ``defaults + the agent's own + the config's``: the
  config's (and so the environment's) rules come AFTER the plan agent's own
  ``edit`` deny.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import opencode as O
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, starts, stop_all

FIX_09 = Path(__file__).parent / "fixtures" / "build_app_v1409"
FIX_11 = Path(__file__).parent / "fixtures" / "build_app_v1411"
RANK = {"allow": 0, "ask": 1, "deny": 2}


# --------------------------------------------------------------------------- #
# OpenCode 1.18.18's permission evaluation, as its own source does it
# --------------------------------------------------------------------------- #
def _wild(name: str, pattern: str) -> bool:
    text = name.replace("\\", "/")
    rx = re.sub(r"[.+^${}()|\[\]\\]", lambda m: "\\" + m.group(0), pattern.replace("\\", "/"))
    rx = rx.replace("*", ".*").replace("?", ".")
    if rx.endswith(" .*"):
        rx = rx[:-3] + "( .*)?"
    return re.match("^" + rx + "$", text, re.S | re.I) is not None


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _rules(perm: dict) -> list[tuple[str, str, str]]:
    out = []
    for key, value in perm.items():
        if isinstance(value, str):
            out.append((key, "*", value))
        else:
            out += [(key, pat, act) for pat, act in value.items()]
    return out


DEFAULTS = {"*": "allow", "doom_loop": "ask", "external_directory": {"*": "ask"}, "question": "deny",
            "plan_enter": "deny", "plan_exit": "deny",
            "read": {"*": "allow", "*.env": "ask", "*.env.*": "ask", "*.env.example": "allow"}}
AGENTS = {
    "build": {"question": "allow", "plan_enter": "allow"},
    "plan": {"question": "allow", "plan_exit": "allow", "task": {"general": "deny"},
             "edit": {"*": "deny", ".opencode/plans/*.md": "allow"}},
}


def config_permission(user: dict, tools: dict | None, env: dict | None) -> dict:
    perm = _merge({}, user)
    if env is not None:
        perm = _merge(perm, env)
    if tools:
        legacy = {}
        for name, on in tools.items():
            legacy["edit" if name in ("write", "edit", "patch") else name] = "allow" if on else "deny"
        perm = _merge(legacy, perm)
    return perm


def verdict(agent: str, perm: dict, tool: str, pattern: str) -> str:
    rules = _rules(DEFAULTS) + _rules(AGENTS[agent]) + _rules(perm)
    hit = None
    for p, pat, act in rules:
        if _wild(tool, p) and _wild(pattern, pat):
            hit = act
    return hit or "ask"


def env_for(tmp_path: Path, monkeypatch, mode: str, user: dict, tools: dict | None = None) -> dict | None:
    """Write the user's config where Iron Jarvis reads it, and return the
    OPENCODE_PERMISSION Iron Jarvis would give that OpenCode process."""
    conf = tmp_path / f"conf-{mode}-{abs(hash(json.dumps([user, tools], sort_keys=True)))}"
    conf.mkdir()
    body: dict = {"permission": user}
    if tools is not None:
        body["tools"] = tools
    (conf / "opencode.json").write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setenv("IRONJARVIS_OPENCODE_CONFIG_DIR", str(conf))
    folder = conf / "work"
    folder.mkdir()
    env, _note = O.child_env({}, mode, str(folder))
    raw = env.get("OPENCODE_PERMISSION")
    return json.loads(raw) if raw else None


USER_CONFIGS = [
    {},
    {"bash": "deny"},
    {"bash": {"rm *": "deny", "*": "allow"}},
    {"edit": "allow"},
    {"edit": "ask", "webfetch": "deny"},
    {"read": {"*": "allow", "secrets/*": "deny"}},
    {"github_*": "deny"},
]
TOOLS = ["bash", "edit", "webfetch", "read", "github_create_issue", "external_directory", "task"]
PATTERNS = ["notes.txt", "rm -rf build", "secrets/x.txt", "app.env"]


# --------------------------------------------------------------------------- #
# 1. Plan mode really does not edit
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("user", USER_CONFIGS)
def test_plan_mode_denies_every_edit_whatever_the_users_rules(tmp_path, monkeypatch, user):
    """OpenCode puts the config's rules (and so the environment's) AFTER the
    plan agent's own ``edit`` deny: a ``"*": "ask"`` from Iron Jarvis turned
    Plan's refusal into a card, and an Allow on it changed the file."""
    env = env_for(tmp_path, monkeypatch, "plan", user)
    perm = config_permission(user, None, env)
    for pattern in ("src/app.py", ".opencode/plans/plan.md", "notes.txt"):
        assert verdict("plan", perm, "edit", pattern) == "deny", (user, pattern, env)


def test_leaving_plan_needs_a_new_process_and_entering_it_too():
    """Plan's edit refusal is the PROCESS's environment: switching to or from
    Plan (same "ask" posture) must restart before the next message, or Ask
    first would keep refusing every edit (and Plan would keep allowing them)."""
    for old, new in (("plan", "ask"), ("ask", "plan"), ("plan", "edits")):
        wrote: list = []

        async def write(obj, wrote=wrote):
            wrote.append(obj)

        drv = O.OpenCodeDriver(write=write, emit=lambda e: None, ask=lambda a: None,
                               on_session_id=lambda s: None, mode=old)
        drv.session_id = "ses_aaaaaaaaaaaaaaaaaaaaaa"
        got = asyncio.run(drv.set_mode(new))
        assert drv.restart_needed is True and got == "next", (old, new)
        assert wrote == [], "no agent switch on the old process's rules (the new one sets it)"
    drv = O.OpenCodeDriver(write=None, emit=lambda e: None, ask=lambda a: None,
                           on_session_id=lambda s: None, mode="ask")
    assert asyncio.run(drv.set_mode("ask")) == "now" and drv.restart_needed is False


def test_a_mode_that_cannot_use_star_says_what_runs_unasked(tmp_path, monkeypatch):
    """When a rule of the user's keeps Iron Jarvis's ``"*"`` out, tools of the
    user's MCP servers and plugins run on OpenCode's own default (no card):
    said in plain words, never left silent."""
    from iron_jarvis.hosted import words as W

    conf = tmp_path / "c"
    conf.mkdir()
    monkeypatch.setenv("IRONJARVIS_OPENCODE_CONFIG_DIR", str(conf))
    (conf / "opencode.json").write_text('{"permission": {"bash": "deny"}}', encoding="utf-8")
    _env, note = O.child_env({}, "ask", str(tmp_path))
    assert note == W.OPENCODE_RULES_KEPT.format(tools="bash") and "MCP" in note and "without asking" in note
    (conf / "opencode.json").write_text('{"permission": {"edit": "ask", "bash": "allow"}}', encoding="utf-8")
    env, note = O.child_env({}, "plan", str(tmp_path))
    assert "*" not in json.loads(env["OPENCODE_PERMISSION"])
    assert note == W.OPENCODE_RULES_KEPT.format(tools="bash")
    (conf / "opencode.json").write_text('{"permission": {"edit": "ask"}}', encoding="utf-8")
    env, note = O.child_env({}, "plan", str(tmp_path))
    assert "*" not in json.loads(env["OPENCODE_PERMISSION"]) and note == W.OPENCODE_UNNAMED_UNASKED


# --------------------------------------------------------------------------- #
# 2. A rule of the user's own is never widened — judged by OpenCode's own rules
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", ["ask", "edits", "plan", "full"])
@pytest.mark.parametrize("user", USER_CONFIGS)
def test_no_rule_of_the_users_is_ever_widened(tmp_path, monkeypatch, mode, user):
    env = env_for(tmp_path, monkeypatch, mode, user)
    with_env = config_permission(user, None, env)
    without = config_permission(user, None, None)
    for agent in ("build", "plan"):
        for tool in TOOLS:
            for pattern in PATTERNS:
                if not any(_wild(tool, k) and (isinstance(v, str) or any(_wild(pattern, p) for p in v))
                           for k, v in user.items()):
                    continue  # no rule of the user's covers it
                a = verdict(agent, with_env, tool, pattern)
                b = verdict(agent, without, tool, pattern)
                assert RANK[a] >= RANK[b], (mode, user, agent, tool, pattern, env)


@pytest.mark.parametrize("mode", ["ask", "edits", "plan"])
def test_the_legacy_tools_switch_is_a_rule_of_the_users_too(tmp_path, monkeypatch, mode):
    """``"tools": {"bash": false}`` (OpenCode's older way to switch a tool off)
    becomes a ``bash`` deny placed BEFORE the permission object — Iron Jarvis's
    ``"*"`` after it turned the off switch into a card."""
    env = env_for(tmp_path, monkeypatch, mode, {}, tools={"bash": False, "write": False})
    perm = config_permission({}, {"bash": False, "write": False}, env)
    assert verdict("build", perm, "bash", "ls") == "deny", env
    assert verdict("build", perm, "edit", "notes.txt") == "deny", env


@pytest.mark.parametrize("mode", ["ask", "edits", "plan"])
def test_a_dot_env_file_still_asks_before_it_is_read(tmp_path, monkeypatch, mode):
    """OpenCode's own default asks before it reads a ``.env`` file (keys,
    passwords). Iron Jarvis's ``"read": "allow"`` landed after that default and
    read every ``.env`` without a word in every mode but Full access."""
    env = env_for(tmp_path, monkeypatch, mode, {})
    perm = config_permission({}, None, env)
    for agent in ("build", "plan"):
        assert RANK[verdict(agent, perm, "read", "C:/work/.env")] >= RANK["ask"], (mode, env)
        assert RANK[verdict(agent, perm, "read", "C:/work/prod.env.local")] >= RANK["ask"], (mode, env)
        assert verdict(agent, perm, "read", "C:/work/app.py") == "allow", (mode, env)


def test_the_managed_and_home_config_files_are_read(tmp_path, monkeypatch):
    """OpenCode also merges ``%ProgramData%\\opencode`` (an admin's policy),
    ``~/.opencode`` and the global ``config.json``: a deny there must keep
    Iron Jarvis's ``"*"`` out."""
    managed = tmp_path / "managed"
    managed.mkdir()
    (managed / "opencode.json").write_text('{"permission": {"bash": "deny"}}', encoding="utf-8")
    monkeypatch.setenv("OPENCODE_TEST_MANAGED_CONFIG_DIR", str(managed))
    monkeypatch.delenv("IRONJARVIS_OPENCODE_CONFIG_DIR", raising=False)
    home = tmp_path / "home"
    (home / ".opencode").mkdir(parents=True)
    (home / ".config" / "opencode").mkdir(parents=True)
    (home / ".config" / "opencode" / "config.json").write_text('{"permission": {"webfetch": "deny"}}',
                                                              encoding="utf-8")
    (home / ".opencode" / "opencode.json").write_text('{"permission": {"task": "deny"}}', encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    folder = tmp_path / "elsewhere" / "w"
    folder.mkdir(parents=True)
    rules = O.user_rules(str(folder), {})
    assert isinstance(rules, dict)
    assert rules.get("bash") == "deny" and rules.get("webfetch") == "deny" and rules.get("task") == "deny", rules


# --------------------------------------------------------------------------- #
# 4. OpenCode's own server stays on this PC, whatever its config says
# --------------------------------------------------------------------------- #
def test_opencodes_internal_server_is_always_on_loopback():
    """``opencode acp`` runs an HTTP server inside the process. With no flag,
    the user's global ``server.hostname`` / ``server.mdns`` / ``server.port``
    decide (measured: ``"hostname": "0.0.0.0"`` listened on every interface,
    unauthenticated). Explicit flags win over the config."""
    argv = O.OpenCodeDriver(write=None, emit=lambda e: None, ask=lambda a: None,
                            on_session_id=lambda s: None, mode="ask").argv(session_id="", resume=False)
    assert argv[0] == "acp"
    flags = argv[1:]
    assert flags[flags.index("--hostname") + 1] == "127.0.0.1"
    assert flags[flags.index("--port") + 1] == "0"
    assert "--mdns=false" in flags
    assert "--pure" not in flags


# --------------------------------------------------------------------------- #
# 2b. The shared restart before the next message: Claude Code and Pi too
# --------------------------------------------------------------------------- #
@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


def _fake(monkeypatch, fixture: Path, log: Path) -> None:
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(fixture))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))


async def test_a_claude_code_effort_change_restarts_and_the_next_message_runs(tmp_path, work, monkeypatch):
    """The restart before the next message must not end that very message's
    turn: before v1.413.0 it ended "interrupted" and the message was never
    sent."""
    log = tmp_path / "fake.log"
    _fake(monkeypatch, FIX_09 / "claude_resume.json", log)
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        s = await new_session(c, work)
        first = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                      json={"text": "one", "turn_id": "t1"}), 30)).text)
        assert first[-1]["stop"] == "completed", first
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"effort": "high"})
        assert r.status_code == 200, r.text
        svc = S.service_for(app.state.platform)
        assert svc.live[s["id"]].restart is True
        old = svc.live[s["id"]].proc
        evs = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                    json={"text": "two", "turn_id": "t2"}), 30)).text)
        assert old.stopped and old.proc.poll() is not None, "the old process is gone"
    await stop_all(app)
    assert evs[-1]["stop"] == "completed", evs
    assert not [e for e in evs if e["type"] == "notice" and "stopped" in str(e.get("text", "")).lower()], evs
    rows = starts(log)
    assert len(rows) == 2 and "--effort" in rows[1]["argv"]
    assert rows[1]["argv"][rows[1]["argv"].index("--effort") + 1] == "high"
    users = [m for m in sent(log) if m.get("type") == "user"]
    assert len(users) == 2, "each message went to a process once"
    pids = [r["pid"] for r in rows]
    assert pids[0] != pids[1]


async def test_a_pi_model_back_to_its_default_restarts_and_the_next_message_runs(tmp_path, work, monkeypatch):
    log = tmp_path / "fake.log"
    _fake(monkeypatch, FIX_11 / "pi_read.json", log)
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        s = await new_session(c, work, harness="pi", model="lab/fleet")
        first = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                      json={"text": "one", "turn_id": "t1"}), 30)).text)
        assert first[-1]["stop"] == "completed", first
        r = await c.patch(f"/build/app/sessions/{s['id']}", json={"model": ""})
        assert r.status_code == 200, r.text
        svc = S.service_for(app.state.platform)
        assert svc.live[s["id"]].restart is True
        evs = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                    json={"text": "two", "turn_id": "t2"}), 30)).text)
    await stop_all(app)
    assert evs[-1]["stop"] == "completed", evs
    rows = starts(log)
    assert len(rows) == 2
    assert "--model" not in rows[1]["argv"], "back to Pi's own default"


async def test_a_new_process_that_cannot_start_is_said_in_words(tmp_path, work, monkeypatch):
    log = tmp_path / "fake.log"
    _fake(monkeypatch, FIX_09 / "claude_resume.json", log)
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        s = await new_session(c, work)
        await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "one", "turn_id": "t1"}), 30)
        await c.patch(f"/build/app/sessions/{s['id']}", json={"effort": "high"})
        monkeypatch.setattr(S, "binary_for", lambda harness: None)  # uninstalled meanwhile
        evs = frames((await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                                    json={"text": "two", "turn_id": "t2"}), 30)).text)
        svc = S.service_for(app.state.platform)
        assert svc.live[s["id"]].proc is None, "the old process is not left running"
    await stop_all(app)
    err = next(e for e in evs if e["type"] == "error")
    assert "Claude Code" in err["title"] and err["fix"]
    assert evs[-1]["stop"] == "failed"
    assert len([m for m in sent(log) if m.get("type") == "user"]) == 1, "nothing went to the old process"
    _ = store  # (the row store is the real one)
