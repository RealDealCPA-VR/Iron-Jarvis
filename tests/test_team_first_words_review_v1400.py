"""v1.400.0 review of TEAM-FIRST: what the planner is told, and the switch
from chat.

* A planner whose model stays on the user's network is not told to save ITS
  usage (it costs nothing per use, and its team may be the same model).
* A member whose name holds a space keeps its roles in the instruction.
* Turning the switch ON from chat cards in the DEFAULT posture
  (approve-for-me) too, and a decline leaves it off.
"""

from __future__ import annotations

from pathlib import Path

from iron_jarvis.agents import model_team as MT


async def _planner_call(tmp_path: Path, monkeypatch, provider: str, model: str, *, team_first: bool = True,
                        plan_mode: bool = False) -> dict:
    """What a coding turn's planner is shown first (``system``, ``specs``)."""
    from iron_jarvis.daemon.chat_stream import stream_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
    from iron_jarvis.platform import build_platform
    from tests.test_team_first_prompt_v1400 import _Model, _register_team

    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "home"))
    monkeypatch.setattr(platform.providers, "inherited_from", lambda name: None)
    # The user's own model server on this PC (a loopback address).
    platform.config.custom_base_url = "http://127.0.0.1:9/v1"
    platform.config.custom_model = "glm-5.3-flash"
    platform.providers.configure_local(ollama_base_url=None, custom_base_url="http://127.0.0.1:9/v1",
                                       custom_model="glm-5.3-flash")
    _register_team(platform)
    platform.config.team_first = team_first
    seen = _Model()
    monkeypatch.setattr(platform.router, "stream", seen.stream)
    monkeypatch.setattr(platform.router, "complete", seen.complete)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    body = ChatBody(messages=[ChatMessageBody(role="user", content="fix the bug in a.py")], provider=provider,
                    model=model, approval_mode="approve_for_me", workspace_dir=str(ws))
    kw = {"session_id": "build:tfreview", "plan_mode": True} if plan_mode else {}
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body, harness="claude", **kw)
    async for _chunk in gen:
        pass
    return seen.calls[0]


async def _planner_system(tmp_path: Path, monkeypatch, provider: str, model: str) -> str:
    return (await _planner_call(tmp_path, monkeypatch, provider, model))["system"]


async def test_a_planner_on_a_local_model_is_not_told_to_save_its_usage(tmp_path, monkeypatch):
    """"The user wants to save this model's usage" is the reason for a planner
    on a plan or paid per use. A planner on the user's own model server costs
    nothing per use, and its team may be the SAME model (the user's glm leads
    a team whose glm reads and writes): told to save its usage, it moves work
    onto the members that DO cost — the reverse of the setting's reason. It is
    told the user wants the team to take its share instead; the rest of the
    instruction is the same."""
    local = await _planner_system(tmp_path / "local", monkeypatch, "custom", "glm-5.3-flash")
    cloud = await _planner_system(tmp_path / "cloud", monkeypatch, "anthropic", "claude-sonnet-4-5")
    assert MT.TEAM_FIRST_LEAD not in local
    assert MT.TEAM_FIRST_LOCAL_LEAD in local
    assert MT.TEAM_FIRST_LOCAL_LEAD == "Work as a team: the user wants the team to take its share of this work."
    assert MT.TEAM_FIRST_LEAD in cloud and MT.TEAM_FIRST_LOCAL_LEAD not in cloud
    # Only the reason differs.

    def para(text: str, lead: str) -> str:
        return lead + text.split(lead, 1)[1].split("\n\n", 1)[0]

    assert para(local, MT.TEAM_FIRST_LOCAL_LEAD).replace(MT.TEAM_FIRST_LOCAL_LEAD, MT.TEAM_FIRST_LEAD) == \
        para(cloud, MT.TEAM_FIRST_LEAD)
    assert para(local, MT.TEAM_FIRST_LOCAL_LEAD).startswith(
        MT.TEAM_FIRST_LOCAL_LEAD + " Hand pieces to the team by role: reading and summarizing files (role:read), ")


def test_a_member_whose_name_holds_a_space_keeps_its_roles(tmp_path, monkeypatch):
    """A member's name may hold a space ("My Reviewer": the registry and POST
    /agents take it). Its line was cut at the first space, so its roles were
    lost — and a team of such members got NO instruction and NO tool
    sentence with team-first on, the team shown as if the setting were off."""
    from iron_jarvis.platform import build_platform

    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "home"))
    monkeypatch.setattr(platform.providers, "inherited_from", lambda name: None)
    reg = platform.agents_registry
    reg.register("My Reviewer", "x", [], description="reviews a change", provider="codex-cli",
                 model="gpt-6-astra", roles=["review"])
    text, roles = MT.team_section(platform, team_first=True)
    assert "- custom:My Reviewer " in text
    assert roles == ("review",)
    assert text.endswith(MT.team_first_words(["review"]))
    # Beside a member whose name is the first word of it, each keeps its own.
    reg.register("My", "x", [], description="reads", provider="mock", model="glm-5.3-flash", roles=["read"])
    _text, roles = MT.team_section(platform, team_first=True)
    assert roles == ("read", "review")


def test_the_words_take_the_planners_place():
    assert MT.team_first_words(["read"]).startswith(MT.TEAM_FIRST_LEAD + " Hand pieces")
    assert MT.team_first_words(["read"], planner_local=True).startswith(MT.TEAM_FIRST_LOCAL_LEAD + " Hand pieces")
    assert MT.team_first_words([], planner_local=True) == ""


async def test_chat_turning_it_on_cards_in_approve_for_me_and_a_decline_leaves_it_off(tmp_path):
    from iron_jarvis.daemon.app import create_app
    from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body, _calls_stream

    app = create_app(str(tmp_path))
    cfg = app.state.platform.config
    app.state.platform.router.stream = _calls_stream(
        [("config_change_protected", {"key": "team_first", "value": "on"})])
    seen = []

    async def decline(data):
        seen.append((data["tool"], data.get("can_always")))
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "deny"})
        assert status == 200

    await _drive_stream(app, _body("switch on team first", "approve_for_me"), decline)
    # "on" is read as True — and True is the floor value: a card, never "Always".
    assert seen == [("config_change_protected", False)]
    assert cfg.team_first is False



async def test_a_plan_mode_turn_is_exactly_as_it_was_with_the_setting_on(tmp_path, monkeypatch):
    """Plan mode cannot start a helper (a helper can change files), so it is
    shown no team — and team-first changes nothing in it, words or tools."""
    on = await _planner_call(tmp_path / "on", monkeypatch, "anthropic", "claude-sonnet-4-5", plan_mode=True)
    off = await _planner_call(tmp_path / "off", monkeypatch, "anthropic", "claude-sonnet-4-5", team_first=False,
                              plan_mode=True)
    assert "# Your model team" not in on["system"]
    assert on["system"].replace(str(tmp_path / "on"), "<T>") == off["system"].replace(str(tmp_path / "off"), "<T>")
    assert on["specs"] == off["specs"]
    assert not any(str(s.get("description") or "").startswith("Your model team") for s in on["specs"])
