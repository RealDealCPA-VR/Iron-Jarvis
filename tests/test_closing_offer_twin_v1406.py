"""v1.406.0 part 4 — the NEXT-STEP BOX reads a reply's ending the daemon's way.

The box under the newest settled reply offers "Yes, do it" when the reply
ends on an offer of more work, and only NAMES (fills the box with) an offer
whose action is the user's to decide. It reads the saved reply on the
dashboard (``dashboard/lib/closingOffer.ts``), so that reading must be the
daemon's ``harness/closing_offer.offer_kind`` / ``offered_action`` exactly:

* the TS patterns are GENERATED from the compiled Python ones
  (``scripts/gen_closing_offer_ts.py --check`` must pass), and
* every table row of ``tests/test_closing_offer_v1402.py`` (plus the box's
  own rows below) is written to a fixture the dashboard test runs the TS
  reading over (``dashboard/__tests__/closing-offer-twin-v1406.test.ts``).

Regenerate after an intended change: ``IJ_REGEN_CLOSING_OFFER=1``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.harness import closing_offer as CO
from tests.test_closing_offer_v1402 import NOT_OFFERS, OFFERS, USER_OWNED

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "dashboard" / "__tests__" / "fixtures" / "closing-offer-v1406.json"

#: The box's own rows: what a button would say or put in the box.
BOX_ROWS = [
    "I can also add logging if you want.",
    "I can send it to the team if you'd like.",
    "Should I proceed with sending the invoices?",
    "Would you like me to send it, or keep it as a draft?",
    "Happy to add tests for this too.",
    "Done.\n\nWould you like me to commit these changes?",
    "All set. Want me to open a pull request?",
    "The tests pass.\n\nShall I push the branch?",
    "Fixed it. Want me to also update the README — it still names the old flag?",
    "Want me to add a very long list of things including a parser, a lexer, a formatter, a linter and a whole test suite for it?",
    "Résumé parsing is fixed. Want me to add the café fixture too?",
]


def rows() -> list[dict]:
    out = []
    seen = set()
    for text in [*OFFERS, *USER_OWNED, *NOT_OFFERS, *BOX_ROWS]:
        if text in seen:
            continue
        seen.add(text)
        out.append({
            "text": text,
            "sentence": CO.last_sentence(text),
            "kind": CO.offer_kind(text),
            "action": CO.offered_action(text),
        })
    return out


def test_the_reading_agrees_with_ends_on_offer_and_user_owns():
    for text in OFFERS:
        assert CO.offer_kind(text) == "work", text
        assert CO.offered_action(text) or text.startswith("Shall I proceed"), text
    # An outward action asked WITHOUT an offer shape ("Should I deploy it?":
    # "should I" + deploy is not one of the offer verbs) is no offer at all —
    # the box then offers nothing, which is the safe side. Every row is
    # never "work".
    users = 0
    for text in USER_OWNED:
        kind = CO.offer_kind(text)
        assert kind in ("user", ""), text
        assert bool(CO.offered_action(text)) == (kind == "user"), text
        users += kind == "user"
    assert users >= len(USER_OWNED) - 3
    for text in NOT_OFFERS:
        assert CO.offer_kind(text) == "", text
        assert CO.offered_action(text) == "", text


def test_the_actions_are_the_offers_own_words():
    assert CO.offered_action("Done. Want me to also add tests?") == "add tests"
    assert CO.offered_action("I can also add logging if you want.") == "add logging"
    assert CO.offered_action("I can send it to the team if you'd like.") == "send it to the team"
    assert CO.offered_action("Would you like me to send it, or keep it as a draft?") == "send it"
    assert CO.offered_action("Happy to add tests for this too.") == "add tests for this"
    long = CO.offered_action(BOX_ROWS[9])
    assert len(long) <= CO.OFFER_ACTION_MAX and not long.endswith(" ")


def test_the_dashboard_patterns_are_generated_from_these():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_closing_offer_ts.py"), "--check"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_fixture_is_current():
    data = {"rows": rows()}
    if os.environ.get("IJ_REGEN_CLOSING_OFFER") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_bytes((json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))
    if not FIXTURE.exists():
        pytest.fail(f"{FIXTURE} is missing: run with IJ_REGEN_CLOSING_OFFER=1")
    held = json.loads(FIXTURE.read_bytes().decode("utf-8"))
    assert held == data, "the fixture is stale: run with IJ_REGEN_CLOSING_OFFER=1"
    kinds = {r["kind"] for r in data["rows"]}
    assert kinds == {"work", "user", ""}
