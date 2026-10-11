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
        / "dashboard" / "components" / "settings" / "pages" / "LegacySettingsForm.tsx"
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


def test_the_status_view_is_the_picture_not_a_second_directory():
    """v1.334.0 retires v1.156.0's "Systems & admin" fold with everything else
    the Status view repeated from the Directory (the user: the status "should
    simply be a beautiful visual"). What is left is in this order: the
    first-run strip while setup is open, the notes, the map, then the vitals
    and the heartbeat.

    The v1.156.1 lesson still applies to what remains: a Reveal only animates
    when nothing between it and PageShell sets its own initial/animate, so the
    map's Reveal sits directly in the page shell."""
    src = (
        Path(__file__).resolve().parents[1] / "dashboard" / "components" / "overview" / "StatusOverview.tsx"
    ).read_text(encoding="utf-8")
    for gone in ("<CollapsibleCard", 'title="Systems & admin"', "<PanelSection", "<AppGrid", "<HealthCard", "<PowerTips"):
        assert gone not in src, f"{gone} is back on the Status view"
    # JSX tags, not the `usePolledApi<SystemMapResponse>` generic.
    strip = src.index("<FirstRunStrip onboarding=")
    the_map = src.index("<SystemMap data=")
    beat = src.index("<Heartbeat hours=")
    assert strip < the_map < beat
    assert "initial=" not in src and "animate=" not in src, "nothing here may stop Reveal's label propagation"


def test_the_roster_is_a_picker_not_a_list():
    """v1.158.0: the roster grew with the number of agents — 10 agents meant a
    604px strip pushing the actual work down the page. It is now a select plus
    the selected agent's detail (153px on the same roster, measured in a real
    browser).

    Provenance and health must stay visible in the CLOSED state: a picker that
    hides whether an agent is a remote, or offline, is the wrong trade for a
    tidier page.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "dashboard" / "components" / "agents" / "RosterStrip.tsx"
    ).read_text(encoding="utf-8")
    assert 'id="roster-pick"' in src, "the roster is not a picker"
    assert "<option" in src
    assert "(offline)" in src, "offline health is hidden when the picker is closed"
    assert "chat-only" in src
    assert "<li" not in src, "the per-agent list should be gone"
    # The detail the rows used to carry must still be reachable.
    # v1.171.0: AgentAvatar became AgentFace (the deterministic identity face)
    # — the pin's intent (an avatar in the picker detail) is unchanged.
    for kept in ("statsText(selected)", "AgentFace", "selected.description"):
        assert kept in src, f"the picker dropped {kept}"


def test_a_drag_on_a_tile_cannot_navigate():
    """v1.158.1: rearranging a tile opened its module.

    Diagnosed by tracing real pointer events, and the first three fixes were
    each measured and each WRONG — recorded here because the shape of this bug
    is not visible in the code:

      * guarding on the `dragging` STATE loses a race (drag-end sets it false
        before the browser dispatches click);
      * React's `onClick` on the tile DOES NOT FIRE after a drop at all — a
        plain click fires it every time — so guarding it guarded nothing;
      * a native listener on the tile's own wrapper never received the event.

    Document capture is the one place the click provably passes through, scoped
    to the grid so it can never swallow a click elsewhere. Verified in a real
    browser across five drag shapes; only an under-threshold nudge navigates,
    which is a click and should.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "dashboard" / "components" / "overview" / "AppGrid.tsx"
    ).read_text(encoding="utf-8")
    assert 'addEventListener("click", swallow, true)' in src, (
        "the document-capture guard is gone; a drag will open the module again"
    )
    assert "grid.contains(e.target)" in src, "the guard must be scoped to the grid"
    assert "suppressClick.current = true" in src, "nothing arms the guard"
    # Re-armed by the next press, or the guard eats the following real click.
    assert 'addEventListener("pointerdown", rearm, true)' in src
