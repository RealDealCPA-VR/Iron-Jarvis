"""Somewhere to say how big your model actually is (v1.156.0).

Reported: "I don't see any easy location to fix the default context windows
because it's showing way smaller than my local model capability of 1m context."

Nothing was broken in the daemon — ``model_context_windows`` resolves correctly
and always has. It simply had NO UI, and the reporting user's config was ``{}``
while their default route was a 1M-context model. Everything downstream was
therefore budgeted at the 32k assumption: history trimmed roughly 30x earlier
than necessary, compaction offering at ~22k instead of ~700k, and conservative
attachment budgets — all invisible, because a 32k ASSUMPTION and a 32k FACT
look identical in every other surface.

These tests pin the contract the new Settings card depends on, plus the one
distinction the card exists to make visible: pinned vs assumed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _context_window


def _deps(client: TestClient):
    return SimpleNamespace(platform=client.app.state.platform)


def test_an_unpinned_model_reports_unknown(tmp_path):
    """None is what makes the planner fall back to DEFAULT_WINDOW — the 32k
    assumption the user was silently living with."""
    client = TestClient(create_app(str(tmp_path)))
    assert _context_window(_deps(client), "", "") is None


def test_a_pin_is_accepted_and_actually_used(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.put(
        "/settings", json={"values": {"model_context_windows": {"mock": 1_000_000}}}
    )
    assert r.status_code == 200
    assert r.json()["settings"]["model_context_windows"] == {"mock": 1_000_000}
    assert _context_window(_deps(client), "", "") == 1_000_000


def test_the_pin_reaches_a_real_turn(tmp_path):
    """The number has to arrive where it matters: the turn's own budget."""
    client = TestClient(create_app(str(tmp_path)))
    client.put(
        "/settings", json={"values": {"model_context_windows": {"mock": 1_000_000}}}
    )
    ctx = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert ctx.status_code == 200
    assert ctx.json()["context"]["window"] == 1_000_000


def test_the_most_specific_key_wins(tmp_path):
    """provider::model beats model beats provider — the order the card tells
    the user about, so it had better be the order the daemon uses."""
    client = TestClient(create_app(str(tmp_path)))
    client.put(
        "/settings",
        json={
            "values": {
                "model_context_windows": {
                    "mock": 200_000,
                    "mock::mock-1": 900_000,
                }
            }
        },
    )
    assert _context_window(_deps(client), "mock", "mock-1") == 900_000
    assert _context_window(_deps(client), "mock", "other") == 200_000


def test_a_pin_survives_a_restart(tmp_path):
    """It is written to config, not held in memory — otherwise every restart
    silently returns the user to the 32k assumption."""
    client = TestClient(create_app(str(tmp_path)))
    client.put(
        "/settings", json={"values": {"model_context_windows": {"mock": 512_000}}}
    )
    reopened = TestClient(create_app(str(tmp_path)))
    assert _context_window(_deps(reopened), "", "") == 512_000


def test_removing_a_pin_returns_to_unknown(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    client.put("/settings", json={"values": {"model_context_windows": {"mock": 99_000}}})
    client.put("/settings", json={"values": {"model_context_windows": {}}})
    assert _context_window(_deps(client), "", "") is None


# --------------------------------------------------------------------------- #
# The UI itself — a correct daemon with no way to reach it is the whole bug.
# --------------------------------------------------------------------------- #
def _settings_src() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "dashboard" / "app" / "settings" / "page.tsx"
    ).read_text(encoding="utf-8")


def test_settings_has_a_context_window_editor():
    src = _settings_src()
    assert "ContextWindowsCard" in src, "there is still nowhere to set this"
    assert "model_context_windows" in src, "the card never writes the setting"


def test_the_editor_distinguishes_pinned_from_assumed():
    """The reason someone comes looking: everywhere else, an assumed 32k and a
    pinned 32k are indistinguishable."""
    src = _settings_src()
    assert "assumed" in src
    assert "pinned" in src


def test_the_editor_explains_the_matching_order():
    src = _settings_src()
    assert "provider::model" in src


def test_the_overview_has_one_admin_section_not_seven():
    """v1.156.0's other half: everything below the tiles now rests behind a
    single title instead of seven independently-collapsing cards."""
    src = (
        Path(__file__).resolve().parents[1] / "dashboard" / "app" / "page.tsx"
    ).read_text(encoding="utf-8")
    assert src.count("<CollapsibleCard") == 1, (
        f"expected exactly one collapsible on the overview, found "
        f"{src.count('<CollapsibleCard')}"
    )
    assert 'title="Systems & admin"' in src
    assert "<PanelSection" in src, "the former cards should now be plain sections"


def test_the_hero_and_tiles_stay_above_it():
    """The user asked for the top of the page to be left ALONE. Order matters:
    hero band, then tiles, then the stats card, and only then the admin fold."""
    src = (
        Path(__file__).resolve().parents[1] / "dashboard" / "app" / "page.tsx"
    ).read_text(encoding="utf-8")
    grid = src.index("<AppGrid />")
    health = src.index("<HealthCard")
    admin = src.index('title="Systems & admin"')
    assert grid < health < admin, "the admin fold must sit below tiles and stats"
