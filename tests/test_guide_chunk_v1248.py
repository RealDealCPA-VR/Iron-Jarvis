"""v1.248.0 — the Guide cuts a long list between items, never mid-sentence.

A Handbook bullet list has no blank lines, so the chunker saw ONE paragraph
and cut it at fixed 1600-char offsets. Adding the v1.248.0 Build bullet moved
a cut into the middle of the unrelated "Updates from a phone" bullet, and the
Guide's answer to "how do updates install" stopped just before "Restart to
update" (``tests/test_guide_v1224.py`` went red on the release suite). Any
Handbook edit could do that to any later bullet; these pin the rule that
makes chunks survive edits.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from iron_jarvis.guide.corpus import _chunk, split_markdown


def _body(section) -> str:
    # Section(slug, title, heading, text) — read the fourth field by position
    # so the test does not depend on its name.
    return getattr(section, fields(section)[3].name)


def _bullets(n: int, width: int = 150, tag: str = "Item") -> str:
    return "\n".join(
        f"- **{tag} {i}** " + ("word " * (width // 5)).strip() + f" END{tag}{i}."
        for i in range(n)
    )


def test_a_long_list_is_cut_between_items_not_inside_one():
    body = _bullets(40)
    parts = _chunk(body, 1600)
    assert len(parts) > 1
    for part in parts:
        assert len(part) <= 1600
        assert part.lstrip().startswith("- **Item")  # every piece starts at an item
    joined = "\n".join(parts)
    for i in range(40):
        assert joined.count(f"- **Item {i}** ") == 1
        # the item and its last words are in the SAME piece
        assert any(f"- **Item {i}** " in part and f"ENDItem{i}." in part for part in parts)


def test_an_edit_above_never_splits_an_unrelated_item():
    """The v1.248.0 failure, generalised: whatever is inserted above, the
    later item keeps its last words."""
    target = (
        "- **Updates** the page says *install from the desktop app (tray →\n"
        "  Restart to update)* — nothing there installs."
    )
    base = _bullets(8) + "\n" + target + "\n" + _bullets(3, tag="Tail")
    for extra in range(30):
        above = _bullets(extra % 5 + 1, width=100 + extra * 7, tag="New")
        parts = _chunk(above + "\n" + base, 1600)
        part = next(p for p in parts if "**Updates**" in p)
        assert "Restart to update" in part, extra


def test_a_single_line_longer_than_the_budget_is_still_cut():
    assert [len(p) for p in _chunk("x" * 4000, 1600)] == [1600, 1600, 800]


def test_short_paragraphs_are_unchanged():
    body = "one paragraph\n\nanother paragraph"
    assert _chunk(body, 1600) == [body]


def test_the_handbook_updates_answer_keeps_its_last_words():
    text = (Path(__file__).resolve().parents[1] / "docs" / "HANDBOOK.md").read_text(
        encoding="utf-8"
    )
    sections = split_markdown("handbook", "The Handbook", text)
    hits = [s for s in sections if "Updates install from the desktop app" in _body(s)]
    assert hits
    assert all("Restart to update" in _body(s) for s in hits)
