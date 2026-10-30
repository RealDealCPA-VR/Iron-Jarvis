"""The animated backgrounds Iron Jarvis knows about (v1.410.0).

THIS LIST IS ALL THE INSTALLER CARRIES. The backgrounds themselves live in the
repository (``backgrounds/<id>/index.html``) and are downloaded from the
INSTALLED version's tag only when the user picks one, so the app stays lean
and a later commit — or a changed file — can never be what downloads: the
address names the tag, and the bytes must hash to ``sha256`` below.

The dashboard's twin is ``dashboard/lib/backgroundCatalog.ts`` (it also holds
each background's tiny preview). ``tests/test_backgrounds_v1410.py`` pins the
two lists equal AND every ``size`` / ``sha256`` to the file in the repository,
so a background edited without updating both catalogs fails CI.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import __version__

#: The public repository the files are downloaded from.
REPO = "RealDealCPA-VR/Iron-Jarvis"
RAW_HOST = "raw.githubusercontent.com"


@dataclass(frozen=True)
class Background:
    id: str
    name: str
    size: int
    sha256: str


BACKGROUNDS: tuple[Background, ...] = (
    Background("aurora", "Aurora", 9191, "effec7a0c0aa13c9ee35ae0fc942f0cfb47be210a50b43b400e037caf9a2971b"),
    Background("particles", "Drift", 8343, "18293ecc9405301c8747ab5325d242148ba04882350bb151aca14f9d364234cd"),
    Background("waves", "Waves", 7624, "354305421dac7c977573e84887f6d1faad58e869cdc72e4b40855871d843175d"),
    Background("grid-beams", "Grid", 9400, "75242e1c3a0590ba442d1e31fb52d15945fca90fb89df8bdd63ab3e3a008d176"),
    Background("reactor", "Reactor", 9059, "a938adbdfc4abfa3dab7963870bfd666243201095434dcf50b092b183acc26d3"),
)

BY_ID: dict[str, Background] = {b.id: b for b in BACKGROUNDS}


def get(bg_id: str) -> Background | None:
    return BY_ID.get(bg_id) if isinstance(bg_id, str) else None


def source_url(bg_id: str, version: str = __version__) -> str:
    """The ONE address a background is downloaded from: the file as it is at
    the tag of the version that is running."""
    return f"https://{RAW_HOST}/{REPO}/v{version}/backgrounds/{bg_id}/index.html"
