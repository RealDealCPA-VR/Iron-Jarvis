"""The thread-row ⋯ menu (v1.114.0).

REQUESTED: "each thread can be renamed, added to memory, pinned or deleted with
4 symbols. All these should be contained in three dots to the right of the
thread whereby when the dots are selected a popout of these options comes out —
and add an option for add to project."

Source-level pins in the house style: the behaviour was verified in a real
browser against a live daemon (6 kebabs / 0 legacy icons, menu portaled to
<body> and painting on top by elementFromPoint, assign→wire project_id set→row
chip renders→remove round-trips, Esc/outside-click close). These pins stop the
load-bearing parts from being refactored away.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "dashboard" / "app" / "chat" / "page.tsx"


@pytest.fixture(scope="module")
def src() -> str:
    return PAGE.read_text(encoding="utf-8")


def test_one_kebab_replaces_the_four_row_icons(src: str):
    assert 'aria-label={`Options for ${t.title || "chat"}`}' in src
    assert "MoreHorizontal" in src
    # The old inline cluster is gone — its four title strings only exist (if at
    # all) inside the menu now, not as always-rendered row buttons.
    assert 'title="Delete this chat"' not in src
    assert 'title="Rename this chat"' not in src
    assert 'title="Pin to the top of the list"' not in src


def test_the_menu_is_portaled_out_of_the_clipping_card(src: str):
    """Load-bearing: the sidebar Card is overflow-hidden with an inner scroll
    area, and Mark 8 gives card surfaces backdrop-blur — which hijacks
    position:fixed for descendants. Only a portal to <body> escapes both."""
    assert "createPortal(" in src
    assert "document.body," in src
    i = src.index('role="menu"')
    assert '"fixed"' in src[i - 2000 : i + 2000]


def test_add_to_project_exists_and_speaks_the_wire_contract(src: str):
    """The new option. The daemon assigns on an EXPLICIT project_id key and
    clears on null — so the PUT must carry the key both ways."""
    assert "Add to project" in src
    assert "Remove from project" in src
    assert "project_id: pid," in src  # assignThreadProject sends assign AND null


def test_a_scroll_outside_the_menu_closes_it_but_inside_does_not(src: str):
    """A fixed menu strands at stale coordinates when the list scrolls under
    it. But the project sub-list scrolls INSIDE the menu — closing on that
    would make long project lists unusable."""
    i = src.index("const onScroll = (e: Event)")
    block = src[i : i + 220]
    assert "threadMenuRef.current?.contains(e.target" in block


def test_delete_is_separated_and_destructive_styled(src: str):
    i = src.index("Delete chat")
    assert "text-rose-300" in src[i - 600 : i]


def test_memory_keeps_the_menu_open_for_feedback(src: str):
    """rememberThread's spinner→check used to live on the row icon; in the menu
    the item must not close instantly or the only feedback disappears."""
    i = src.index('"Saved to memory"')
    # The memory item's onClick must NOT call setThreadMenu(null).
    button_start = src.rindex("<button", 0, i)
    assert "setThreadMenu(null)" not in src[button_start:i]
