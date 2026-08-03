"""Named document themes (v1.134.0) — the DECLARATIVE beauty layer.

Local models write styling code badly, so the ENGINE owns beauty: a model (or
user) picks a theme NAME and the deterministic writers apply everything —
fonts, sizes, accent color, table header/band fills, page margins. This module
is pure data plus tiny lookup helpers: no I/O, no python-docx/openpyxl
imports, so it is importable from anywhere for free.

Font families are chosen for SAFETY: every one ships with Windows/Office
(Word and Excel substitute silently when a family is missing), so no font
files are bundled or required beyond what ``documents/fonts`` already carries
for PDF output.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    """One named look. All values are plain data the writers interpret."""

    name: str
    heading_font: str
    body_font: str
    #: Heading sizes in points, by level 1-4 (deeper levels reuse the last).
    heading_sizes_pt: tuple[int, int, int, int]
    body_size_pt: float
    #: Accent color as (R, G, B) — docx headings, cover title.
    accent_rgb: tuple[int, int, int]
    #: Solid fills / font colors as RRGGBB hex (openpyxl-style, no ``#``).
    table_header_fill: str
    table_header_font: str
    band_fill: str
    #: Page margins in inches: (top, right, bottom, left).
    margins_in: tuple[float, float, float, float]

    def heading_size(self, level: int) -> int:
        idx = min(max(int(level), 1), len(self.heading_sizes_pt)) - 1
        return self.heading_sizes_pt[idx]

    @property
    def accent_hex(self) -> str:
        return "%02X%02X%02X" % self.accent_rgb


THEMES: dict[str, Theme] = {
    # Navy/steel accents, serif headings, generous spacing — reports, memos.
    "professional": Theme(
        name="professional",
        heading_font="Georgia",
        body_font="Calibri",
        heading_sizes_pt=(22, 16, 13, 12),
        body_size_pt=11.0,
        accent_rgb=(31, 56, 100),  # navy
        table_header_fill="1F3864",
        table_header_font="FFFFFF",
        band_fill="EDF1F8",  # pale steel
        margins_in=(1.0, 1.0, 1.0, 1.0),
    ),
    # Near-black on white, tight — clean notes and specs.
    "minimal": Theme(
        name="minimal",
        heading_font="Segoe UI",
        body_font="Segoe UI",
        heading_sizes_pt=(18, 14, 12, 11),
        body_size_pt=10.5,
        accent_rgb=(17, 17, 17),  # near-black
        table_header_fill="F3F3F3",
        table_header_font="111111",
        band_fill="FAFAFA",
        margins_in=(0.75, 0.75, 0.75, 0.75),
    ),
    # Deep warm terracotta accent — proposals, decks, human-facing docs.
    "warm": Theme(
        name="warm",
        heading_font="Cambria",
        body_font="Calibri",
        heading_sizes_pt=(21, 15, 13, 12),
        body_size_pt=11.0,
        accent_rgb=(140, 58, 43),  # terracotta
        table_header_fill="8C3A2B",
        table_header_font="FFFFFF",
        band_fill="FAF1EC",
        margins_in=(0.9, 0.9, 0.9, 0.9),
    ),
}

#: The advertised names, in definition order — tool schemas enumerate these.
THEME_NAMES: tuple[str, ...] = tuple(THEMES)


def get_theme(name: "str | None") -> "Theme | None":
    """The Theme for ``name`` (case-insensitive); None for empty/unknown —
    an unknown name must degrade to "no theme", never crash a write."""
    if not name:
        return None
    return THEMES.get(str(name).strip().lower())
