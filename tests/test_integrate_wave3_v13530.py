"""v1.353.0 integrate — Wave 3's lanes joined, and the follow-ups closed.

Driven through the REAL app (``create_app``), the real autoselect, registry,
tools and doors; the model is the only double (the 3B harness), except where
the point is the offline DEMO model itself.

SEAMS
* (a) ONE place for the apps a new user connects: the checklist's optional
  ``connect_apps`` step, the empty chat's apps line and the chat's Drive fix
  door all name the Directory (Settings › Connections › Apps & tools) — the
  daemon side here, the rendered side in
  ``dashboard/__tests__/wave3-seams-v13530.test.tsx``.
* (b) the chat's fix doors are the doors the other lanes built: "find my tax
  files in Google Drive" (G41's acceptance wording) now reaches app_connect
  and ends with "Connect Google Drive in Settings"; app_connect for the
  browser ("puppeteer" until G43) answers where the browser is set up and
  leaves the SAME "Pair your browser" door a browser refusal leaves; the
  status read chat offers while browser access is off opens that door too.
* No "connect an app" approval card for a call that changes nothing (an app
  that signs in through the browser, the browser, an API-key app whose
  secure card is its own consent) — an MCP app that connects at once keeps it.
* The offline demo model follows the chat's "no media service" line: a fresh
  install's "make a picture" shows the secure Pixio key card.

FOLLOW-UPS
* The Ollama press with a model already chosen saves nothing and says so.
* The Overview's one-press list offers a detected Ollama like the chat doors.
* ``/health``'s ollama row says whether an address is saved.
* The Directory's category list names "Added by you", the user packs' own
  category.
* ``device.approval_mode`` round-trips through GET/PUT /settings/device with
  exactly the chip's three values (the dashboard reads it now).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors.catalog import LINK_CARDS, get_link_card
from iron_jarvis.connectors.service import USER_CATEGORY
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.doors import _APPS_FIX_HREF, _BROWSER_FIX_HREF, door_for, failure_door_for
from iron_jarvis.daemon.schemas import ChatBody
import importlib

readiness = importlib.import_module("iron_jarvis.onboarding.readiness")  # the package re-exports a function of this name
from iron_jarvis.onboarding.checklist import getting_started
from iron_jarvis.providers import ollama_seen
from iron_jarvis.providers.adapters import mock as mock_adapter
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.settings.records import AppConnectTool, record_tools_for
from iron_jarvis.tools.base import ToolResult
from tests.test_chat_guide_media_doors_v13530 import (  # the 3B harness, reused
    DRIVE_DOOR,
    PAIR_DOOR,
    _body,
    _done,
    _PostModel,
    _stream,
    _StreamModel,
    _clean_env,  # noqa: F401 — autouse: no developer key makes the install look connected
)

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"


def _src(*parts: str) -> str:
    return (DASH.joinpath(*parts)).read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture()
def app(tmp_path, monkeypatch):
    import iron_jarvis.daemon.routes.chat as chat_routes

    app = create_app(str(tmp_path))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    return app


@pytest.fixture(autouse=True)
def _forget_ollama():
    ollama_seen.forget()
    yield
    ollama_seen.forget()


# =========================================================================== #
# (a) one place for the apps — the daemon's side of the seam
# =========================================================================== #


def test_the_apps_step_the_apps_line_and_the_drive_door_name_one_place(app):
    step = next(s for s in getting_started(app.state.platform) if s["key"] == "connect_apps")
    assert step["href"] == "/marketplace"
    assert step["optional"] is True
    # The chat home's line and the Overview row use the step's own words
    # and address (dashboard/components/chat/HomeReadiness, OnboardingWelcome).
    home = _src("components", "chat", "HomeReadiness.tsx")
    assert '<Link href="/marketplace"' in home and "Connect the apps you use" in home
    assert step["title"].startswith("Connect the apps you use")
    welcome = _src("components", "OnboardingWelcome.tsx")
    assert 'connect_apps: { href: "/marketplace", cta: "Open the Directory" }' in welcome
    # /marketplace and the Drive door's address are the SAME Settings section.
    relocated = _src("lib", "relocated.ts")
    assert '"/marketplace": { base: "/settings", extra: { section: "connections-apps" } }' in relocated
    assert _APPS_FIX_HREF == "/settings?section=connections-apps"
    assert DRIVE_DOOR["href"] == _APPS_FIX_HREF


def test_the_browser_door_and_the_directorys_browser_card_open_one_address():
    card = get_link_card("browser_addon")
    assert card is not None and card.href == _BROWSER_FIX_HREF == PAIR_DOOR["href"] == "/computeruse"
    # "puppeteer" (a catalog entry until G43) and "browser" name that card.
    assert get_link_card("puppeteer") is card and get_link_card("Browser") is card
    assert get_link_card("notion") is None
    assert [c.id for c in LINK_CARDS] == ["browser_addon"]


def _settings_row_label(section: str) -> str:
    """The name a Settings row shows (v1.354.0, G67): a row that holds a
    relocated page is named like that page — the nav catalogue's label for the
    old address that redirects to it (lib/settingsRows.settingsRowLabel)."""
    relocated = _src("lib", "relocated.ts")
    routes = {
        sec: route
        for route, sec in re.findall(
            r'"(/[a-z-]+)":\s*\{\s*base:\s*"/settings",\s*extra:\s*\{\s*section:\s*"([a-z-]+)"\s*\}', relocated
        )
    }
    route = routes[section]
    catalogue = _src("lib", "nav.ts") + _src("lib", "surfaces.ts")
    m = re.search(r'href:\s*"' + re.escape(route) + r'",\s*(?://[^\n]*\n\s*)*label:\s*"([^"]+)"', catalogue)
    assert m, f"no nav label for {route}"
    return m.group(1)


def test_the_pixio_refusal_names_a_place_that_exists():
    from iron_jarvis.media.registry import ACCOUNTS_AND_KEYS

    # v1.354.0 (G67, updated on purpose): the Settings row is named like the
    # page it opens ("Connections", the page's heading and its Everything
    # entry) — it no longer carries a third name of its own, so the place the
    # refusal names must END in the name the row actually shows.
    home = _src("components", "settings", "SettingsHome.tsx")
    at = home.index('id: "connections-accounts"')
    assert "label:" not in home[at : home.index("Comp:", at)]
    assert "label: settingsRowLabel(sub.id, sub.label ?? sub.id)" in home
    row = _settings_row_label("connections-accounts")
    assert row == "Connections"
    # v1.354.0 integrate (on purpose): the place is the Settings group whose
    # FIRST row is that page, the SAME words the chat's failure lines use
    # (providers/failure_words.ACCOUNTS_PLACE) — "Settings › Connections ›
    # Connections" said one word twice. It still ends in the row's name.
    from iron_jarvis.providers.failure_words import ACCOUNTS_PLACE

    assert ACCOUNTS_AND_KEYS == ACCOUNTS_PLACE == "Settings › Connections"
    assert ACCOUNTS_AND_KEYS.endswith(f"› {row}")
    first_row = re.search(r'connections:\s*\[\s*\{\s*id:\s*"([a-z-]+)"', home)
    assert first_row and first_row.group(1) == "connections-accounts"
    # …and Pixio is on that page (its "Creative media" group).
    assert re.search(r"pixio:\s*\"creative\"", _src("components", "settings", "pages", "ConnectionsPage.tsx"))


# =========================================================================== #
# (b) the chat's fix doors are the doors the other lanes built
# =========================================================================== #


def test_the_drive_vocabulary_reaches_app_connect_and_questions_still_do_not():
    for s in ("find my tax files in Google Drive", "search my OneDrive for receipts", "pull the deck from Dropbox"):
        assert "app_connect" in record_tools_for(s), s
    assert "app_connect" not in record_tools_for("is my Google Drive connected?")
    assert "app_connect" not in record_tools_for("what is a drive")


def test_find_my_tax_files_in_google_drive_ends_with_the_connect_button(app):
    """G41's acceptance wording: a user asking for Drive files with Drive not
    set up gets "Connect Google Drive in Settings" — and no "connect an app"
    card first (the call changes nothing)."""
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "google_drive"})], []])
    app.state.platform.router.stream = model
    cards: list = []
    frames = _stream(app, _body("find my tax files in Google Drive"), cards=cards)
    assert "app_connect" in model.tools[0]
    assert cards == []
    assert _done(frames)["doors"] == [DRIVE_DOOR]


def test_app_connect_for_the_browser_points_at_the_browser_page_with_the_pair_door(app):
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "puppeteer"})], []])
    app.state.platform.router.stream = model
    cards: list = []
    frames = _stream(app, _body("connect my puppeteer"), cards=cards)
    assert "app_connect" in model.tools[0], "anti-vacuity: the sentence armed app_connect"
    assert cards == []
    finished = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
    assert finished and finished[0]["ok"] is False
    assert "Settings › Connections › Browser" in finished[0]["output"]
    assert "no app called" not in finished[0]["output"]
    assert _done(frames)["doors"] == [PAIR_DOOR]


def test_app_connect_for_the_browser_in_the_post_lane_leaves_the_same_door(app):
    platform = app.state.platform
    platform.router = _PostModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "browser"})], []])
    body = ChatBody(messages=[{"role": "user", "content": "connect my browser"}], tools=["app_connect"])
    res = asyncio.run(run_chat_turn(platform, {}, body))
    assert res["doors"] == [PAIR_DOOR]


def test_the_browser_status_read_opens_the_fix_door_when_the_browser_is_not_set_up():
    def status(**data):
        return ToolResult(ok=True, output="", data=data)

    assert door_for("browser_get_status", status(connected=False, code="BROWSER_NOT_CONNECTED")) == PAIR_DOOR
    assert door_for("browser_get_status", status(connected=False, code="BROWSER_ACCESS_OFF")) == {
        "href": "/computeruse",
        "label": "Turn on browser access",
    }
    # A paired, connected browser with access off: turn access on.
    assert door_for("browser_get_status", status(connected=True, access="off"))["label"] == "Turn on browser access"
    # CONTROL: connected with access on → no door; a code that is the model's
    # to fix → no door.
    assert door_for("browser_get_status", status(connected=True, access="read_only")) is None
    assert door_for("browser_get_status", status(connected=True, access="read_only", code="TAB_NOT_FOUND")) is None
    assert door_for("browser_get_status", ToolResult(ok=True, output="")) is None


def test_a_fresh_install_asking_about_its_browser_gets_the_pair_door(app):
    """Browser access is off by default, so the status read is what chat
    offers — and it answers with the button, through the real tool."""
    model = _StreamModel([[ToolCall(id="s1", name="browser_get_status", arguments={})], []])
    app.state.platform.router.stream = model
    frames = _stream(app, _body("is my browser connected?"))
    assert "browser_get_status" in model.tools[0], "anti-vacuity: the status read was offered"
    assert _done(frames)["doors"] in ([PAIR_DOOR], [{"href": "/computeruse", "label": "Turn on browser access"}])


# =========================================================================== #
# no card for a call that changes nothing — an MCP app keeps its card
# =========================================================================== #


@pytest.mark.parametrize(
    ("app_id", "carded"),
    [("google_drive", False), ("onedrive", False), ("gmail", True), ("pixio", False), ("puppeteer", False), ("nope", False), ("notion", True), ("github", True)],
)
def test_app_connect_cards_only_a_call_that_connects_something(app_id, carded):
    tool = app_state_tool()
    verdict = asyncio.run(tool.spend_check({"app": app_id}, None))  # the check reads no context
    if carded:
        assert verdict is None, "an MCP app connects the moment the call runs: the ordinary card"
    else:
        assert verdict is not None and verdict.ask is False


def app_state_tool() -> AppConnectTool:
    return AppConnectTool.__new__(AppConnectTool)


def test_a_deny_rule_still_refuses_app_connect_for_drive(app):
    """The no-card verdict never widens a DENY: the lanes read it only when
    the mode is not deny."""
    platform = app.state.platform
    platform.permissions._base["app_connect"] = "deny"  # the engine's own policy (copied at start)
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "google_drive"})], []])
    platform.router.stream = model
    frames = _stream(app, _body("connect my Google Drive"))
    finished = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
    assert not any(d.get("ok") for d in finished)
    assert _done(frames)["doors"] == []


# =========================================================================== #
# the offline demo model follows the "no media service" line
# =========================================================================== #


def test_the_demo_models_mark_is_the_chats_no_media_line():
    assert chat_turn.NO_MEDIA_CONNECT_LINE.lstrip("\n").startswith(mock_adapter.NO_MEDIA_MARK)
    assert chat_turn.NO_MEDIA_SETTINGS_LINE.lstrip("\n").startswith(mock_adapter.NO_MEDIA_MARK)


def test_a_fresh_install_asking_the_demo_for_a_picture_gets_the_pixio_key_card(app):
    """No model double: the install's own default (the offline demo) answers."""
    platform = app.state.platform
    assert str(platform.config.default_provider) in ("", "mock")
    cards: list = []
    frames = _stream(app, _body("make a picture of a cat surfing"), cards=cards)
    done = _done(frames)
    secret = [c for c in done["config_cards"] if c.get("kind") == "secret"]
    assert [c["name"] for c in secret] == ["connection.pixio"]
    assert "app_connect" in done["tools_used"]
    assert cards == [], "the key card is the only card"
    assert "Pixio key" in done["reply"] and "RESULT.md" not in done["reply"]


def test_control_the_demo_model_never_calls_app_connect_without_the_line():
    resp = mock_adapter.MockLLMAdapter._default_behavior(
        [type("M", (), {"role": "user", "content": "connect my notion"})()],
        [{"name": "app_connect"}],
        "# Tools\nnothing about media",
    )
    assert not resp.tool_calls or resp.tool_calls[0].name != "app_connect"


# =========================================================================== #
# Ollama: the press, the Overview list, the Accounts row's facts
# =========================================================================== #


def test_the_ollama_press_with_a_model_already_chosen_saves_nothing(app, monkeypatch):
    platform = app.state.platform
    platform.config.default_provider = "anthropic"
    probes: list = []
    monkeypatch.setattr(ollama_seen, "probe_now", lambda *a, **k: probes.append(1) or True)
    with TestClient(app) as client:
        r = client.post("/onboarding/use-model", json={"provider": "ollama"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["promoted"] is None and "You already chose" in body["reason"]
    assert not (platform.config.ollama_base_url or "").strip(), "no address was saved"
    assert probes == [], "nothing was even probed"


def test_the_overview_one_press_list_offers_a_detected_ollama_like_the_chat(app, monkeypatch):
    platform = app.state.platform
    monkeypatch.setattr(ollama_seen, "seen", lambda *a, **k: True)
    usable = readiness.model_choice(platform)["usable"]
    row = next(u for u in usable if u["provider"] == "ollama")
    assert row["local"] is True and row["label"] == "Ollama (on this PC)"
    # CONTROL: a saved address is the user's — the press would not adopt.
    platform.config.ollama_base_url = "http://10.0.0.9:11434"
    assert all(u["provider"] != "ollama" for u in readiness.model_choice(platform)["usable"])
    # CONTROL: not seen → not offered.
    platform.config.ollama_base_url = None
    monkeypatch.setattr(ollama_seen, "seen", lambda *a, **k: False)
    assert all(u["provider"] != "ollama" for u in readiness.model_choice(platform)["usable"])


def test_health_says_whether_an_ollama_address_is_saved(app):
    platform = app.state.platform
    with TestClient(app) as client:
        row = next(p for p in client.get("/health").json()["providers"] if p["provider"] == "ollama")
        assert row["address_saved"] is False
        platform.config.ollama_base_url = "http://10.0.0.9:11434"
        row = next(p for p in client.get("/health").json()["providers"] if p["provider"] == "ollama")
        assert row["address_saved"] is True


# =========================================================================== #
# the Directory's categories, the device's approval setting
# =========================================================================== #


def test_the_directory_lists_the_user_packs_own_category(app):
    with TestClient(app) as client:
        cats = client.get("/connectors").json()["categories"]
    assert USER_CATEGORY == "Added by you"
    assert USER_CATEGORY in cats and "Custom" not in cats


def test_approvals_in_chat_round_trips_with_the_chips_three_values(app):
    levels = _src("lib", "permissionLevels.ts")
    m = re.search(r"PERMISSION_MODES: readonly PermissionMode\[\] = \[([^\]]+)\]", levels)
    chip = [v.strip().strip('"') for v in m.group(1).split(",")]
    from iron_jarvis.settings import schema

    row = schema.get("device.approval_mode")
    assert [v for v, _ in row.options] == chip
    with TestClient(app) as client:
        dev = "dev_integrate01"
        assert client.get(f"/settings/device?device_id={dev}").json()["values"]["device.approval_mode"] is None
        for mode in chip:
            r = client.put("/settings/device", json={"device_id": dev, "values": {"device.approval_mode": mode}})
            assert r.status_code == 200, r.text
            got = client.get(f"/settings/device?device_id={dev}").json()["values"]["device.approval_mode"]
            assert got == mode
    # The chat page reads and writes exactly this row (lib/approvalDefault).
    helper = _src("lib", "approvalDefault.ts")
    assert 'DEVICE_APPROVAL_KEY = "device.approval_mode"' in helper
    assert "/settings/device?device_id=" in helper and 'put("/settings/device"' in helper


def test_failure_door_for_the_browser_alias_is_the_browser_fix_door():
    res = ToolResult(ok=False, error="no")
    assert failure_door_for("app_connect", {"app": "puppeteer"}, res) == PAIR_DOOR
    assert failure_door_for("app_connect", {"app": "google_drive"}, res) == DRIVE_DOOR
    # CONTROL: an MCP app's failure is the model's to explain — no door.
    assert failure_door_for("app_connect", {"app": "notion"}, res) is None
