"""Chat composer affordances: the drop target and the "/" skill picker (v1.104.0).

REPORTED: "if I'm dragging and dropping into a chat I'd like some indicator
within the chat window that it can accept my file, like a perforated border …
also in chat I'd like the capability to invoke a skill with a forward slash
command."

Both already existed, which is the interesting part:

1. Drag-and-drop worked, but the entire signal was ``ring-2 ring-accent/60`` on
   the card edge — that reads as "this card has focus", not "let go and I'll
   take that file". A dashed inset border is the convention, and it says what
   happens on release.
2. The "/" picker was fully built — listbox, filtering, arrow keys — and gated
   to ``mode === "chat"``. In Agent mode typing "/" did nothing at all, so the
   feature was indistinguishable from missing for anyone who lives in Agent
   mode.

These assert against the page source because the behaviour IS the markup: there
is no daemon endpoint to exercise. Verified in a real browser besides — the
overlay renders dashed 2px rgba(34,211,238,.7) and the picker lists 17 skills
in Agent mode.
"""

from __future__ import annotations

import re

from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "dashboard" / "app" / "chat" / "page.tsx"


@pytest.fixture(scope="module")
def src() -> str:
    return PAGE.read_text(encoding="utf-8")


# --- the drop target --------------------------------------------------------


def test_the_drop_indicator_is_a_dashed_border(src: str):
    """The literal ask was a "perforated" border."""
    assert "border-dashed" in src


def test_the_overlay_says_what_dropping_does(src: str):
    assert "Drop to attach" in src


def _overlay_class(src: str) -> str:
    """The drop overlay's own className. Anchored on the element rather than on
    "text near border-dashed" — a window-based match silently passed while
    pointer-events-none was deleted, because it kept finding an UNRELATED one
    elsewhere in a 4,800-line file."""
    m = re.search(r'className="(pointer-events-none absolute inset-0[^"]*)"', src)
    return m.group(1) if m else ""


def test_the_overlay_cannot_swallow_the_drop(src: str):
    """Load-bearing: the drop is handled by WINDOW listeners, so an overlay that
    accepted pointer events would break the gesture it advertises — and it would
    break it only on release, long after the affordance looked right."""
    assert _overlay_class(src).startswith("pointer-events-none")


# Tailwind's default opacity scale. A modifier outside it (bg-zinc-950/92)
# silently emits NO rule — the scrim renders fully transparent and the class
# name still reads perfectly plausible in review. That exact bug happened here.
_TW_OPACITY = {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50,
               55, 60, 65, 70, 75, 80, 85, 90, 95, 100}


def test_the_scrim_opacity_is_a_class_tailwind_actually_emits(src: str):
    m = re.search(r"bg-zinc-950/(\d+)", _overlay_class(src))
    assert m, "the drop overlay lost its scrim"
    assert int(m.group(1)) in _TW_OPACITY, (
        f"bg-zinc-950/{m.group(1)} is not on Tailwind's opacity scale — it "
        "compiles to nothing and the scrim disappears"
    )


# --- the "/" skill picker ---------------------------------------------------


def test_slash_is_not_gated_to_chat_mode(src: str):
    """The whole defect. Agent mode must open the picker too."""
    line = next(ln for ln in src.splitlines() if "const slashActive" in ln)
    assert 'mode === "chat"' not in line


def test_an_agent_is_told_which_skill_to_use(src: str):
    """SessionCreate has no `skill` field — chat sends one and the daemon
    injects the playbook, an agent is NAMED the skill and loads it with the
    skill_load tool it already carries. If this line goes, picking a skill in
    Agent mode becomes a silent no-op that still shows a chip."""
    assert "skill_load" in src
    assert re.search(r'Use the "\$\{activeSkill\}" skill', src)


def test_the_active_skill_chip_renders_in_both_modes(src: str):
    """A picker whose selection leaves no trace on screen is indistinguishable
    from one that failed."""
    assert '{mode === "chat" && activeSkill !== "" && (' not in src
    assert '{activeSkill !== "" && (' in src


def test_the_plus_menu_offers_skills_in_both_modes(src: str):
    """Otherwise "/" is the only route in — findable only by someone who
    already knew it existed, which is how this got reported as missing."""
    head = src.split("Skills\n")[0]
    tail = head[-1400:]
    assert 'mode === "chat" && (' not in tail


def test_the_picker_follows_the_caret_not_just_typing(src: str):
    """v1.105.0. "/" opens the picker wherever the caret is, so the composer has
    to TRACK the caret — and React's onSelect alone does not fire for a collapsed
    caret move. Measured in a real browser: DOM selectionStart went 21 -> 8 on
    ArrowLeft while the tracked value stayed 21, so moving back into an earlier
    "/word" silently failed to reopen the picker. keyup and click are the events
    that actually fire for that; losing either brings the bug back, and no unit
    test of the pure helper can see it because the helper was always correct.
    """
    block = src[src.index("onKeyDown={onKeyDown}") - 1400 : src.index("onKeyDown={onKeyDown}")]
    assert "onKeyUp=" in block, "arrow keys / Home / End stop moving the picker"
    assert "onClick=" in block, "clicking into an earlier /word stops reopening it"


def test_the_composer_advertises_the_slash_command(src: str):
    """Discoverability was the actual failure mode: the feature shipped, the
    user never learned it was there."""
    assert "/ for skills" in src
