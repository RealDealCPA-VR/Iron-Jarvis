"""v1.434.0 (Wave 2 of the chat-first plan, group F): EVERY CHAT IN THE APP
SAYS WHICH SCREEN IT RUNS ON.

Settings kept per device (theme, approvals in chat, read aloud, persona,
Auto tools — the ``device.*`` rows of ``settings/schema``) need the chat turn
to carry the app window's device id, or the settings tool answers "open this
chat in the app" — to a user who is already in it. Only the chat page sent
one. Now a coding session's turn (``POST /build/sessions/{id}/turn``) takes
``device_id`` and hands it to the stream lane through the turn's
``ChatBody``, exactly where the chat page's id travels; an older dashboard
(no id) gets today's answer, byte for byte.

The browser SIDEBAR is the add-on, not a screen of the app: it has no device
id, and the settings tool's refusal there names the place the setting lives.

Driven through the REAL app factory, the real build turn route and the real
stream lane; only the model is a scripted double.
"""

from __future__ import annotations

import asyncio

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.providers.failure_words import reset_surface, set_sidebar_surface
from iron_jarvis.tools.base import ToolContext
from tests.test_build_sessions_v1367 import Script, frames_of, new_session, open_turn, wire

DEVICE = "dev_phone0123456"
OTHER = "dev_desk00123456"
SET_READ_ALOUD = [ToolCall(id="c1", name="config_set", arguments={"key": "device.read_aloud", "value": True})]
PER_DEVICE_REFUSAL = "Read replies aloud is set per device; open this chat in the app to change it."
SIDEBAR_REFUSAL = (
    "Read replies aloud belongs to one screen of the Iron Jarvis app; change it there "
    "(Settings, or a chat in the app)."
)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _arm_config_set(monkeypatch) -> None:
    """A coding session's turn arms the CODING set (``harness/mode``); the
    settings tools join it by the sentence only once the chat lanes' arming
    says so (another lane's change). Arm ``config_set`` here so the pin is
    about where the setting LANDS, whichever way it came to be armed (a name
    already in the set is not added twice)."""
    from iron_jarvis.harness import mode as harness_mode

    if "config_set" not in harness_mode.CODING_TOOLS:
        monkeypatch.setattr(harness_mode, "CODING_TOOLS", (*harness_mode.CODING_TOOLS, "config_set"))


def _spy_lane(monkeypatch) -> list:
    seen: list = []
    real = build_routes.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append(body)
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(build_routes, "stream_chat_turn", spy)
    return seen


async def _turn(app, sid: str, body: dict) -> list[tuple[str, dict]]:
    status, raw = await open_turn(app, sid, body)
    assert status == 200, raw
    frames = frames_of(raw)
    assert frames and frames[-1][0] in ("done", "error"), frames[-3:]
    return frames


def _config_set_result(frames) -> dict:
    return next(
        d for e, d in frames
        if e == "tool_call" and d.get("name") == "config_set" and d.get("status") == "finished"
    )


# --------------------------------------------------------------------------- #
# a coding session's turn carries the device id
# --------------------------------------------------------------------------- #


async def test_a_coding_session_turn_changes_a_device_setting_for_that_screen(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    _arm_config_set(monkeypatch)
    seen = _spy_lane(monkeypatch)
    wire(app, Script([SET_READ_ALOUD, "Read aloud is on."]))
    frames = await _turn(app, sid, {"text": "turn on read aloud", "turn_id": "dv-1", "device_id": DEVICE})
    # The id rode the turn's ChatBody — the field both chat lanes read.
    assert seen and seen[0].device_id == DEVICE
    result = _config_set_result(frames)
    assert result["ok"] is True, result
    w = app.state.platform.config_writer
    # Stored under THAT screen's id, and no other.
    assert w.current("device.read_aloud", device_id=DEVICE) is True
    assert w.current("device.read_aloud", device_id=OTHER) in (None, False)


async def test_a_coding_session_turn_with_no_device_id_is_answered_as_today(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    _arm_config_set(monkeypatch)
    seen = _spy_lane(monkeypatch)
    wire(app, Script([SET_READ_ALOUD, "I could not change it."]))
    frames = await _turn(app, sid, {"text": "turn on read aloud", "turn_id": "dv-2"})
    # An older dashboard: the ChatBody carries no id at all (None, as before).
    assert seen and seen[0].device_id is None
    result = _config_set_result(frames)
    assert result["ok"] is False
    assert PER_DEVICE_REFUSAL in (result.get("output") or result.get("error") or ""), result
    w = app.state.platform.config_writer
    assert w.current("device.read_aloud", device_id=DEVICE) in (None, False)


async def test_continue_from_where_it_stopped_carries_the_device_id(app, work, monkeypatch):
    """The other person-pressed door of the turn route ("Continue from where
    it stopped") hands the id to the same start."""
    sid = new_session(app, work)["id"]
    got: list[dict] = []

    async def spy(d, thread_id, text, turn_id, **kw):
        got.append(kw)
        raise build_routes.ResumeGone()

    monkeypatch.setattr(build_routes, "start_build_turn", spy)
    status, _raw = await open_turn(app, sid, {
        "text": "", "turn_id": "dv-3", "continue_turn": "bt_gone", "device_id": DEVICE,
    })
    assert status == 409
    assert got and got[0]["device_id"] == DEVICE and got[0]["resume_failed"] is True
    got.clear()
    status, _raw = await open_turn(app, sid, {"text": "", "turn_id": "dv-4", "continue_turn": "bt_gone"})
    assert status == 409 and got and got[0]["device_id"] == ""


def test_an_attempts_body_carries_the_plans_device_id():
    """"Try on several models": each attempt's ChatBody carries the screen's
    id from the person's message (``Plan.device_id``); none = None."""
    from types import SimpleNamespace

    from iron_jarvis.harness import attempts as A

    plan = SimpleNamespace(
        setup={"provider": "ollama"}, history=[], task="fix it", project_id="", thread_id="t1",
        local_only=False, device_id=DEVICE,
    )
    a = SimpleNamespace(target=SimpleNamespace(provider="ollama", model="glm"), folder="C:/x", turn_id="t.try1")
    assert A._body(plan, a).device_id == DEVICE
    plan.device_id = ""
    assert A._body(plan, a).device_id is None
    # The dataclass's own default: none.
    assert A.Plan.__dataclass_fields__["device_id"].default == ""


@pytest.fixture()
def attempts_world(tmp_path, monkeypatch):
    """The v1.397.0 attempts harness: a scratch repository, the fake
    Iron-Proxy, scripted models (nothing reaches a real service)."""
    from tests.test_build_accounts_v1368 import isolate, make_fake
    from tests.test_build_git_v1375 import no_real_gh

    no_real_gh(monkeypatch)
    gen = isolate(tmp_path, monkeypatch)
    next(gen)
    fake = make_fake(tmp_path)
    try:
        yield fake
    finally:
        fake.stop()
        try:
            next(gen)
        except StopIteration:
            pass


@pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git is not installed")
async def test_try_on_several_models_hands_each_attempt_the_screens_id(attempts_world, app, tmp_path, monkeypatch):
    from iron_jarvis.harness import attempts as A
    from tests.test_build_accounts_v1368 import enable
    from tests.test_build_attempts_v1397 import LOCAL, World, attempts_turn, make_calc_repo, wire_models
    from tests.test_build_attempts_v1397 import new_session as new_attempts_session

    await enable(app)
    wire_models(app, monkeypatch, World({"ollama": ["Nothing to change."]}))
    real_body = A._body
    bodies: list = []

    def spy(plan, a):
        out = real_body(plan, a)
        bodies.append(out)
        return out

    monkeypatch.setattr(A, "_body", spy)
    repo = make_calc_repo(tmp_path / "proj")
    sid = (await new_attempts_session(app, repo))["id"]
    await attempts_turn(app, sid, "look at calc.py", "tr-1", device_id=DEVICE,
                        try_on=[{"provider": LOCAL[0], "model": LOCAL[1]}])
    assert bodies and all(b.device_id == DEVICE for b in bodies)
    bodies.clear()
    await attempts_turn(app, sid, "look again", "tr-2", try_on=[{"provider": LOCAL[0], "model": LOCAL[1]}])
    assert bodies and all(b.device_id is None for b in bodies)


# --------------------------------------------------------------------------- #
# the browser sidebar: no device id, and the refusal names the place
# --------------------------------------------------------------------------- #


def _ctx(app, tmp_path, device_id: str = "") -> ToolContext:
    p = app.state.platform
    return ToolContext(
        workspace=tmp_path, session_id="chat", agent_run_id="chat", device_id=device_id,
        config=p.config, event_bus=p.event_bus, engine=p.engine,
    )


@pytest.mark.parametrize(("tool", "key", "value", "label"), [
    ("config_set", "device.read_aloud", True, "Read replies aloud"),
    ("config_set", "device.theme", "mark8", "Theme"),
    # Don't ask is ask-floor: config_change_protected, the same base class.
    ("config_change_protected", "device.approval_mode", "yolo", "Approvals in chat"),
])
def test_the_sidebar_refusal_names_the_place_the_setting_lives(app, tmp_path, tool, key, value, label):
    reg = app.state.platform.registry

    async def run() -> object:
        token = set_sidebar_surface()
        try:
            return await reg.get(tool).execute({"key": key, "value": value}, _ctx(app, tmp_path))
        finally:
            reset_surface(token)

    res = asyncio.run(run())
    assert res.ok is False
    assert res.error == SIDEBAR_REFUSAL.replace("Read replies aloud", label)
    assert "open this chat in the app" not in res.error
    # Nothing was written for any device.
    assert app.state.platform.config_writer.devices.get_all(DEVICE) == {}


def test_outside_the_sidebar_a_missing_device_id_keeps_todays_words(app, tmp_path):
    reg = app.state.platform.registry
    res = asyncio.run(reg.get("config_set").execute({"key": "device.read_aloud", "value": True}, _ctx(app, tmp_path)))
    assert res.ok is False and res.error == PER_DEVICE_REFUSAL


def test_with_a_device_id_the_tool_writes_under_it(app, tmp_path):
    reg = app.state.platform.registry
    res = asyncio.run(reg.get("config_set").execute(
        {"key": "device.read_aloud", "value": True}, _ctx(app, tmp_path, DEVICE),
    ))
    assert res.ok is True, res.error
    assert app.state.platform.config_writer.current("device.read_aloud", device_id=DEVICE) is True


@pytest.mark.parametrize("bad", ["bad id!", "ab", "x" * 65, "../etc", "dev ice"])
def test_a_device_id_the_app_never_makes_gets_a_plain_refusal(app, tmp_path, bad):
    # v1.434.0 integration: an API caller's bad id used to reach the store and
    # come back as its raw ValueError text.
    from iron_jarvis.settings.tools import BAD_DEVICE_REFUSAL

    reg = app.state.platform.registry
    res = asyncio.run(reg.get("config_set").execute(
        {"key": "device.read_aloud", "value": True}, _ctx(app, tmp_path, bad),
    ))
    assert res.ok is False
    assert res.error == BAD_DEVICE_REFUSAL.format(label="Read replies aloud")
    assert "device id" not in res.error and "ValueError" not in res.error
    assert app.state.platform.config_writer.current("device.read_aloud", device_id=DEVICE) in (None, False)
