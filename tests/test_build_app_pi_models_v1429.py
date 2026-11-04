"""v1.429.0 — Pi in Build › Code offers what Pi in a terminal offers: the
model list Pi's own ``/model`` shows (grouped by provider), the thinking
levels EACH model takes, Pi's own ``/`` commands, and its extensions,
templates and skills.

WHY it was bare (measured, read-only, on the user's live install): the model
list was kept only from a Pi process the Code view had started, and that file
(``hosted/pi_models.json``) did not exist — no Pi session had run — so the
menu held "Pi's default" alone and the ``/`` menu nothing; and Pi's own
commands typed in the box (``/compact``, ``/model``…) reached the MODEL as
plain text (measured with a stand-in model server).

Every Pi here is the fake harness replaying RECORDED pi 1.3.0 answers
(``tests/fixtures/build_app_v1429/pi_catalog.json``: a scratch config, paths
masked; ``build_app_v1411/pi_read.json``). Nothing here runs a real Pi.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import pi as P
from iron_jarvis.hosted import pi_catalog as PC
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted import words as W
from tests._build_app_helpers_v1409 import FAKE, client, frames, new_session, sent, starts, stop_all, until

FIX = Path(__file__).parent / "fixtures"
CATALOG = FIX / "build_app_v1429" / "pi_catalog.json"
PI_READ = FIX / "build_app_v1411" / "pi_read.json"
#: THE CONTRACT: Pi's row as the real daemon lists it after one background
#: read of the recorded catalog; the dashboard decodes the same file
#: (``dashboard/__tests__/build-app-pi-models-v1429.test.tsx``). Regenerate
#: after an intended change with ``IJ_REGEN_BUILD_APP_PI_MODELS=1``.
GOLDEN = Path(__file__).parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-app-pi-models-v1429.json"


def _steps(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["steps"]


def _scenario(tmp_path: Path, steps: list[dict], name: str = "scenario") -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"harness": "pi", "steps": steps}), encoding="utf-8")
    return path


def _fake_pi(monkeypatch, scenario: Path, log: Path) -> None:
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_SCENARIO", str(scenario))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))


def _recorded(kind: str, path: Path = CATALOG) -> dict:
    return next(s["out"] for s in _steps(path) if isinstance(s.get("out"), dict) and s["out"].get("command") == kind)


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def fresh(monkeypatch, tmp_path):
    """No list kept in memory, Pi's own files in a scratch folder."""
    from iron_jarvis.hosted import claude as CL

    monkeypatch.setattr(P, "LAST_MODELS", [])
    monkeypatch.setattr(CL, "LAST_COMMANDS", {})
    agent = tmp_path / "pi-agent"
    agent.mkdir()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))
    return agent


# --------------------------------------------------------------------------- #
# The thinking levels each model takes — Pi's own rule
# --------------------------------------------------------------------------- #
def test_levels_follow_pis_own_rule_on_the_recorded_models():
    models = {m["id"]: m for m in _recorded("get_available_models")["data"]["models"]}
    # Measured live: Pi's own get_available_thinking_levels said exactly these.
    assert P.levels_of(models["think-big"]) == ["off", "minimal", "low", "medium", "high", "xhigh"]
    assert P.levels_of(models["think-small"]) == ["low", "medium", "high"]
    assert P.levels_of(models["plain"]) == ["off"]
    codex = {m["id"]: m for m in _recorded("get_available_models", PI_READ)["data"]["models"]}
    assert P.levels_of(codex["gpt-6-luna"]) == ["off", "minimal", "low", "medium", "high", "xhigh", "max"]
    assert P.levels_of(codex["gpt-6-astra"]) == ["minimal", "low", "medium", "high", "xhigh", "max"]
    # A record that does not say whether it reasons: not known.
    assert P.levels_of({"id": "x", "provider": "p"}) is None
    assert P.levels_of({"reasoning": True}) == ["off", "minimal", "low", "medium", "high"]


def test_rows_are_grouped_by_provider_named_as_pi_names_them_with_their_levels():
    rows = P.model_rows(_recorded("get_available_models")["data"]["models"])
    assert rows == [
        {"id": "standin-a/think-big", "label": "Think Big", "group": "standin-a",
         "efforts": ["off", "minimal", "low", "medium", "high", "xhigh"]},
        {"id": "standin-a/plain", "label": "Plain Model", "group": "standin-a", "efforts": [], "pictures": False},
        {"id": "standin-b/think-small", "label": "Think Small", "group": "standin-b",
         "efforts": ["low", "medium", "high"], "pictures": False},
    ]
    # Pi's picker sorts by provider and keeps its own order inside one.
    mixed = P.model_rows([{"provider": "zed", "id": "z1"}, {"provider": "acme", "id": "b"},
                          {"provider": "zed", "id": "z0"}, {"provider": "acme", "id": "a"}])
    assert [r["id"] for r in mixed] == ["acme/b", "acme/a", "zed/z1", "zed/z0"]
    many = P.model_rows([{"provider": "openrouter", "id": f"m{i}"} for i in range(1200)])
    assert len(many) == P.MAX_MODELS == 1000


def test_the_kept_catalog_reads_back_the_same(tmp_path, monkeypatch, fresh):
    path = tmp_path / "pi_models.json"
    rows = P.model_rows(_recorded("get_available_models")["data"]["models"])
    P.save_catalog(path, models=rows, commands=P.command_rows(_recorded("get_commands")["data"]["commands"]))
    P.save_catalog(path, default={"model": "standin-a/think-big", "label": "Think Big", "thinking": "low",
                                  "efforts": ["off", "low"]})
    assert P.cached_models(path) == rows, "a second write keeps what the first wrote"
    assert P.cached_default(path)["label"] == "Think Big"
    assert [c["source"] for c in P.cached_commands(path)] == ["extension", "extension", "prompt", "skill"]


# --------------------------------------------------------------------------- #
# Before any Pi session ran: one short background read
# --------------------------------------------------------------------------- #
async def test_a_fresh_install_lists_pis_models_and_commands_from_one_background_read(
        tmp_path, monkeypatch, fresh):
    log = tmp_path / "fake.log"
    _fake_pi(monkeypatch, CATALOG, log)
    monkeypatch.setattr(PC, "AUTO_PROBE", True)
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        first = {r["id"]: r for r in (await c.get("/build/app/harnesses")).json()["harnesses"]}["pi"]
        assert first.get("models_reading") is True, "the page is told the list is coming"
        await until(lambda: not PC.reading(svc._pi_catalog))
        pi = {r["id"]: r for r in (await c.get("/build/app/harnesses")).json()["harnesses"]}["pi"]
    assert "models_reading" not in pi and "models_note" not in pi
    assert pi["models"][0] == {"id": "", "label": "Pi's default (Think Big)", "default_name": "Think Big",
                               "efforts": ["off", "minimal", "low", "medium", "high", "xhigh"]}
    assert [(m["id"], m["group"]) for m in pi["models"][1:]] == [
        ("standin-a/think-big", "standin-a"), ("standin-a/plain", "standin-a"), ("standin-b/think-small", "standin-b")]
    names = {c["name"]: c for c in pi["commands"]}
    assert names["hello"]["source"] == "extension"
    assert names["review"]["source"] == "prompt"
    assert names["skill:hello-skill"]["source"] == "skill"
    assert names["compact"]["source"] == "pi" and names["compact"]["argument_hint"] == "[instructions]"
    assert names["settings"]["note"] == P.TERMINAL_ONLY["settings"]
    # The reader: Pi's own flags for a quiet read, in an empty folder of the app's own.
    [start] = starts(log)
    # No session file, no network, none of the user's MCP servers (literal:
    # a test reading the constant back would prove nothing).
    assert start["argv"] == ["--mode", "rpc", "--no-session", "--offline", "--no-mcp"]
    assert Path(start["cwd"]) == svc.home / "hosted" / "pi-probe"
    assert "--session-id" not in start["argv"]
    assert [x["type"] for x in sent(log)] == ["get_state", "get_commands", "get_available_models"]
    kept = json.loads(svc._pi_catalog.read_text(encoding="utf-8"))
    assert kept["fetched_at"] and kept["watch"] == PC.watch_signature()
    doc = {"_about": ("Pi's row in GET /build/app/harnesses, written by the REAL daemon after one background "
                      "read of tests/fixtures/build_app_v1429/pi_catalog.json (recorded pi 1.3.0). "
                      "Regenerate with IJ_REGEN_BUILD_APP_PI_MODELS=1."),
           "pi": pi}
    if os.environ.get("IJ_REGEN_BUILD_APP_PI_MODELS") == "1":
        GOLDEN.write_bytes((json.dumps(doc, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    assert json.loads(GOLDEN.read_text(encoding="utf-8")) == doc, "regenerate with IJ_REGEN_BUILD_APP_PI_MODELS=1"


async def test_the_list_is_read_once_until_pis_own_files_change(tmp_path, monkeypatch, fresh):
    log = tmp_path / "fake.log"
    _fake_pi(monkeypatch, CATALOG, log)
    monkeypatch.setattr(PC, "AUTO_PROBE", True)
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        await c.get("/build/app/harnesses")
        await until(lambda: not PC.reading(svc._pi_catalog))
        await c.get("/build/app/harnesses")
        await asyncio.sleep(0.2)
        assert len(starts(log)) == 1, "a fresh list is not read again"
        # A /login (auth.json) or a new models.json: read again.
        (fresh / "models.json").write_text('{"providers": {}}', encoding="utf-8")
        await c.get("/build/app/harnesses")
        await until(lambda: len(starts(log)) == 2 and not PC.reading(svc._pi_catalog))


def test_an_old_read_is_stale_and_a_fresh_one_is_not():
    now = 10_000.0
    assert PC.is_stale({}, now=now)
    assert not PC.is_stale({"fetched_at": now - 5, "watch": [["a", 1, 2]]}, now=now, watch=[["a", 1, 2]])
    assert PC.is_stale({"fetched_at": now - PC.CATALOG_TTL_S - 1}, now=now)
    assert PC.is_stale({"fetched_at": now - 5, "watch": [["a", 1, 2]]}, now=now, watch=[["a", 1, 3]])


async def test_no_read_starts_when_the_reader_is_off(tmp_path, monkeypatch, fresh):
    log = tmp_path / "fake.log"
    _fake_pi(monkeypatch, CATALOG, log)
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        pi = {r["id"]: r for r in (await c.get("/build/app/harnesses")).json()["harnesses"]}["pi"]
    assert "models_reading" not in pi and pi["models"] == [{"id": "", "label": "Pi's default"}]
    assert not log.exists() or starts(log) == []


async def test_pi_with_no_model_at_all_says_why(tmp_path, monkeypatch, fresh):
    steps = _steps(CATALOG)
    for s in steps:
        if isinstance(s.get("out"), dict) and s["out"].get("command") == "get_available_models":
            s["out"] = {**s["out"], "data": {"models": []}}
        if isinstance(s.get("out"), dict) and s["out"].get("command") == "get_state":
            s["out"] = {**s["out"], "data": {**s["out"]["data"], "model": None}}
    _fake_pi(monkeypatch, _scenario(tmp_path, steps), tmp_path / "fake.log")
    monkeypatch.setattr(PC, "AUTO_PROBE", True)
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        await c.get("/build/app/harnesses")
        await until(lambda: not PC.reading(svc._pi_catalog))
        pi = {r["id"]: r for r in (await c.get("/build/app/harnesses")).json()["harnesses"]}["pi"]
    assert pi["models_note"] == W.PI_NO_MODELS
    assert pi["models"] == [{"id": "", "label": "Pi's default"}]


def test_a_reader_that_does_not_answer_keeps_what_was_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "LAST_MODELS", [])
    path = tmp_path / "home" / "hosted" / "pi_models.json"
    P.save_catalog(path, models=[{"id": "a/b", "label": "B", "group": "a"}])
    gone = tmp_path / "gone.py"
    gone.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    assert PC.read_now(path, tmp_path / "home", [sys.executable, str(gone)]) is False
    assert P.cached_models(path) == [{"id": "a/b", "label": "B", "group": "a"}]


# --------------------------------------------------------------------------- #
# A session's own list: the same, and it is kept for the next look
# --------------------------------------------------------------------------- #
async def test_a_sessions_own_list_keeps_pis_default_and_the_users_commands(tmp_path, monkeypatch, work, fresh):
    log = tmp_path / "fake.log"
    _fake_pi(monkeypatch, PI_READ, log)
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "hi", "turn_id": "t"}), 30)
        pi = {r["id"]: r for r in (await c.get("/build/app/harnesses")).json()["harnesses"]}["pi"]
    await stop_all(app)
    # The recording's default (started with no model named): GPT-6 Luna.
    assert pi["models"][0]["default_name"] == "GPT-6 Luna"
    assert pi["models"][0]["efforts"] == ["off", "minimal", "low", "medium", "high", "xhigh", "max"]
    assert {m["group"] for m in pi["models"][1:]} == {"openai-codex", "local-models"}
    kept = json.loads(svc._pi_catalog.read_text(encoding="utf-8"))
    assert kept["default"]["model"] == "openai-codex/gpt-6-luna"
    assert {c["name"] for c in kept["commands"]} >= {"plan", "implement"}


async def test_a_reopened_conversation_does_not_name_its_last_model_as_pis_default(
        tmp_path, monkeypatch, work, fresh):
    log = tmp_path / "fake.log"
    _fake_pi(monkeypatch, PI_READ, log)
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        store.update(app.state.platform.engine, s["id"], harness_session_id="46a90daa-be98-49b6-9f0a-e43db9c81108")
        await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "hi", "turn_id": "t"}), 30)
    await stop_all(app)
    assert P.LAST_DEFAULT == {}, "a resumed conversation's model is ITS last one, not Pi's default"


# --------------------------------------------------------------------------- #
# Pi's own `/` commands, typed in the box
# --------------------------------------------------------------------------- #
def test_a_typed_command_is_pis_own_unless_one_of_the_users_has_that_name():
    assert P.typed_builtin("/compact keep the plan", []) == ("compact", "keep the plan")
    assert P.typed_builtin("  /MODEL  openai/x ", []) == ("model", "openai/x")
    assert P.typed_builtin("/settings", []) == ("settings", "")
    assert P.typed_builtin("/review api", [{"name": "review", "source": "prompt"}]) is None
    assert P.typed_builtin("/name x", [{"name": "name", "source": "extension"}]) is None
    assert P.typed_builtin("please /compact", []) is None
    assert P.typed_builtin("/compactly", []) is None
    assert P.typed_builtin("/compact\nfocus on tests", []) == ("compact", "focus on tests")


@pytest.mark.parametrize("typed", ["/settings", "/new", "/resume", "/tree", "/share", "/login", "/quit"])
async def test_a_command_only_pis_terminal_has_is_refused_in_words_and_never_sent(
        tmp_path, monkeypatch, work, fresh, typed):
    log = tmp_path / "fake.log"
    _fake_pi(monkeypatch, PI_READ, log)
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        r = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": typed, "turn_id": "t"})
    assert r.status_code == 400 and r.json()["detail"] == P.TERMINAL_ONLY[typed[1:]]
    assert not log.exists() or starts(log) == [], "Pi was never started for it"


async def _after_one_turn(tmp_path, monkeypatch, work, steps=None, folder=None, **env):
    log = tmp_path / "fake.log"
    _fake_pi(monkeypatch, _scenario(tmp_path, steps) if steps is not None else PI_READ, log)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    app = create_app(str(tmp_path / "home"))
    c = client(app)
    await c.__aenter__()
    s = await new_session(c, folder or work, harness="pi")
    await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "hi", "turn_id": "t0"}), 30)
    return app, c, s, log


async def _typed(c, sid: str, text: str, n: int) -> list[dict]:
    r = await asyncio.wait_for(c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": f"t{n}"}), 30)
    assert r.status_code == 200, r.text
    return frames(r.text)


async def test_compact_session_name_and_export_go_through_pis_own_commands(tmp_path, monkeypatch, work, fresh):
    stats = {"userMessages": 2, "assistantMessages": 2, "toolCalls": 0, "tokens": {"total": 34}, "cost": 0,
             "contextUsage": {"tokens": 17, "contextWindow": 128000, "percent": 0.0133}}
    app, c, s, log = await _after_one_turn(tmp_path, monkeypatch, work, IJ_FAKE_PI_STATS=json.dumps(stats))
    try:
        evs = await _typed(c, s["id"], "/compact keep the plan", 1)
        assert [e["text"] for e in evs if e["type"] == "notice"] == [
            "Pi summarized the conversation so far to make room."]
        assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"
        evs = await _typed(c, s["id"], "/session", 2)
        said = [e["text"] for e in evs if e["type"] == "notice"]
        assert said == ["This session: 2 messages from you, 2 from Pi, 0 tool calls, 34 tokens, "
                        "0% of the 128,000-token context used."]
        evs = await _typed(c, s["id"], "/name Release work", 3)
        assert any(e.get("text") == 'Pi named this session "Release work".' for e in evs)
        await _typed(c, s["id"], "/export", 4)
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
    finally:
        await c.__aexit__(None, None, None)
        await stop_all(app)
    got = [x for x in sent(log) if x.get("type") != "get_state" or x.get("id", "").startswith("ij-get_state")]
    kinds = [x["type"] for x in got]
    assert {"compact", "get_session_stats", "set_session_name", "export_html"} <= set(kinds)
    assert next(x for x in got if x["type"] == "compact")["customInstructions"] == "keep the plan"
    assert next(x for x in got if x["type"] == "set_session_name")["name"] == "Release work"
    prompts = [x.get("message") for x in got if x["type"] == "prompt"]
    assert prompts == ["hi"], "none of Pi's own commands reached the model as words"
    assert row["title"] == "Release work"


async def test_pis_refusal_is_said_in_its_own_words(tmp_path, monkeypatch, work, fresh):
    steps = _steps(PI_READ) + [
        {"await": "command", "command": "compact"},
        {"out": {"id": "x", "type": "response", "command": "compact", "success": False,
                 "error": "Nothing to compact (session too small)"}, "reply_to_command": "compact"},
    ]
    app, c, s, log = await _after_one_turn(tmp_path, monkeypatch, work, steps)
    try:
        evs = await _typed(c, s["id"], "/compact", 1)
    finally:
        await c.__aexit__(None, None, None)
        await stop_all(app)
    err = next(e for e in evs if e["type"] == "error")
    assert err["title"] == "Pi said: Nothing to compact (session too small)"
    assert evs[-1]["stop"] == "failed"


async def test_model_and_thinking_typed_change_the_session_like_the_chips(tmp_path, monkeypatch, work, fresh):
    app, c, s, log = await _after_one_turn(tmp_path, monkeypatch, work)
    try:
        evs = await _typed(c, s["id"], "/model gpt-6-astra", 1)
        assert [e["text"] for e in evs if e["type"] == "notice"] == ["Pi now uses GPT-6 Astra (openai-codex)."]
        evs = await _typed(c, s["id"], "/thinking HIGH", 2)
        assert [e["text"] for e in evs if e["type"] == "notice"] == ["Pi's thinking level is now high."]
        bad = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "/thinking off", "turn_id": "t3"})
        nope = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "/model nope", "turn_id": "t4"})
        bare = await c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "/model", "turn_id": "t5"})
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
    finally:
        await c.__aexit__(None, None, None)
        await stop_all(app)
    assert row["model"] == "openai-codex/gpt-6-astra" and row["effort"] == "high"
    # gpt-6-astra takes no "off" (its map says null): refused with its levels.
    assert bad.status_code == 400 and "use minimal, low, medium, high, xhigh, max." in bad.json()["detail"]
    assert nope.status_code == 400 and nope.json()["detail"] == W.PI_NO_SUCH_MODEL.format(name="nope")
    assert bare.status_code == 400 and bare.json()["detail"] == P.MODEL_NEEDS_NAME
    got = sent(log)
    assert {"type": "set_model", "provider": "openai-codex", "modelId": "gpt-6-astra"} in [
        {k: v for k, v in x.items() if k != "id"} for x in got]
    assert any(x["type"] == "set_thinking_level" and x["level"] == "high" for x in got)
    assert [x.get("message") for x in got if x["type"] == "prompt"] == ["hi"]


async def test_reload_starts_pi_again_before_the_next_message(tmp_path, monkeypatch, work, fresh):
    app, c, s, log = await _after_one_turn(tmp_path, monkeypatch, work)
    try:
        evs = await _typed(c, s["id"], "/reload", 1)
        assert any("again before your next message" in e.get("text", "") for e in evs)
        assert len(starts(log)) == 1, "/reload alone starts nothing"
        await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn", json={"text": "go", "turn_id": "t2"}), 30)
    finally:
        await c.__aexit__(None, None, None)
        await stop_all(app)
    assert len(starts(log)) == 2, "the next message started Pi again (it reads them fresh)"


async def test_a_first_message_that_is_a_command_does_not_title_the_session(tmp_path, monkeypatch, work, fresh):
    _fake_pi(monkeypatch, PI_READ, tmp_path / "fake.log")
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
        await asyncio.wait_for(c.post(f"/build/app/sessions/{s['id']}/turn",
                                      json={"text": "/reload", "turn_id": "t"}), 30)
        row = (await c.get(f"/build/app/sessions/{s['id']}")).json()["session"]
    await stop_all(app)
    assert not (row["title"] or "").startswith("/reload")


async def test_the_slash_menu_lists_pis_own_commands_first_then_the_users(tmp_path, monkeypatch, work, fresh):
    app, c, s, log = await _after_one_turn(tmp_path, monkeypatch, work)
    try:
        cmds = (await c.get(f"/build/app/sessions/{s['id']}/commands")).json()["commands"]
    finally:
        await c.__aexit__(None, None, None)
        await stop_all(app)
    own = [x["name"] for x in cmds if x.get("source") == "pi" and not x.get("note")]
    assert own == list(P.BUILTINS)
    assert cmds[0]["name"] == "model" and cmds[0]["argument_hint"] == "<provider/model>"
    theirs = {x["name"]: x["source"] for x in cmds if x.get("source") in ("extension", "prompt", "skill")}
    assert theirs["plan"] == "extension" and theirs["implement"] == "prompt"
    notes = {x["name"]: x["note"] for x in cmds if x.get("note")}
    assert set(notes) == set(P.TERMINAL_ONLY)


async def test_a_note_while_pi_works_never_carries_one_of_its_commands(tmp_path, monkeypatch, work):
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        s = await new_session(c, work, harness="pi")
    live = svc._live(store.get(app.state.platform.engine, s["id"]))
    turn = S.HostedTurn("t", s["id"])
    live.turn = turn
    with pytest.raises(S.Refused) as got:
        await svc.steer(s["id"], "/compact")
    assert got.value.sentence == W.PI_COMMAND_NOT_A_NOTE
    turn.end("done")


def test_a_projects_own_commands_are_never_kept_for_every_folder():
    listed = [
        {"name": "mine", "source": "prompt", "sourceInfo": {"scope": "user"}},
        {"name": "theirs", "source": "prompt", "sourceInfo": {"scope": "project"}},
    ]
    assert [c["name"] for c in P.command_rows(listed)] == ["mine", "theirs"]
    assert [c["name"] for c in P.command_rows(listed, user_only=True)] == ["mine"]


async def test_the_list_is_read_again_after_pis_own_catalog_refresh(tmp_path, monkeypatch, work, fresh):
    monkeypatch.setattr(P, "REREAD_MODELS_S", 0.2)
    app, c, s, log = await _after_one_turn(tmp_path, monkeypatch, work)
    try:
        await until(lambda: [x["type"] for x in sent(log)].count("get_available_models") == 2)
    finally:
        await c.__aexit__(None, None, None)
        await stop_all(app)


async def test_stop_during_a_compact_ends_the_turn_and_says_pi_may_finish(tmp_path, monkeypatch, work, fresh):
    steps = _steps(PI_READ) + [{"await": "command", "command": "compact"}, {"sleep": 30}]
    app, c, s, log = await _after_one_turn(tmp_path, monkeypatch, work, steps)
    svc = S.service_for(app.state.platform)
    try:
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{s['id']}/turn",
                                            json={"text": "/compact", "turn_id": "t1"}))
        await until(lambda: "compact" in [x["type"] for x in sent(log)])
        r = await c.post(f"/build/app/sessions/{s['id']}/interrupt")
        assert r.status_code == 200, r.text
        evs = frames((await asyncio.wait_for(task, 10)).text)
    finally:
        await c.__aexit__(None, None, None)
        await stop_all(app)
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "interrupted"
    assert any("may still finish summarizing" in e.get("text", "") for e in evs)
    assert svc.turn("t1") is None or not svc.turn("t1").running


async def test_export_names_the_file_pi_wrote_in_the_sessions_folder(tmp_path, monkeypatch, work, fresh):
    steps = _steps(PI_READ) + [
        {"await": "command", "command": "export_html"},
        {"out": {"id": "x", "type": "response", "command": "export_html", "success": True,
                 "data": {"path": "pi-session-2026-11-04.html"}}, "reply_to_command": "export_html"},
    ]
    # A folder written with forward slashes (as the live check's was): the
    # path said is one Windows path, never a mix of both slashes.
    app, c, s, log = await _after_one_turn(tmp_path, monkeypatch, work, steps, folder=str(work).replace("\\", "/"))
    try:
        evs = await _typed(c, s["id"], "/export", 1)
    finally:
        await c.__aexit__(None, None, None)
        await stop_all(app)
    wrote = os.path.normpath(os.path.join(str(work), "pi-session-2026-11-04.html"))
    assert [e["text"] for e in evs if e["type"] == "notice"] == [f"Pi saved the conversation as {wrote}."]
