"""v1.395.0: the team router's words are said in ONE vocabulary on both sides.

``agents/model_team.ROLE_NOUNS`` (the reasons: "good record for reading") has
a dashboard twin, ``dashboard/lib/modelTeam.ts`` ``ROLE_NOUNS`` (Settings ›
Model team's record line). The helper row's reason cap is the same number on
both sides (``harness/helpers.REASON_CHARS`` / ``lib/buildHelpers.ts``
``REASON_CHARS``). Read off the files (CRLF normalised at the reader — a CI
checkout has CRLF).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from iron_jarvis.agents import model_team as T
from iron_jarvis.harness import helpers as H

DASH = Path(__file__).resolve().parents[1] / "dashboard" / "lib"


def _read(name: str) -> str:
    return (DASH / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_role_nouns_are_equal_on_both_sides():
    src = _read("modelTeam.ts")
    m = re.search(r"export const ROLE_NOUNS: Record<string, string> = (\{[^}]*\});", src)
    assert m, "ROLE_NOUNS not found in dashboard/lib/modelTeam.ts"
    ts = json.loads(re.sub(r"(\w+):", r'"\1":', m.group(1)))
    assert ts == T.ROLE_NOUNS
    assert set(T.ROLE_NOUNS) == set(T.ROLE_IDS)


def test_the_reason_cap_is_equal_on_both_sides():
    src = _read("buildHelpers.ts")
    m = re.search(r"export const REASON_CHARS = (\d+);", src)
    assert m and int(m.group(1)) == H.REASON_CHARS
