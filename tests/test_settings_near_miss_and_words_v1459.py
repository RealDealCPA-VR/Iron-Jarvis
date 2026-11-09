"""v1.459.0 — two of the re-audit's open items (docs/PRODUCT-READINESS-RESULTS.md 0k).

1. Settings labels that hold a CUE word armed the settings writers on
   sentences that were not asking: "only suggest, never act is my motto"
   ("never" is in the label "Only suggest, never act"), "I should learn from
   coding sessions" ("learn" is in "Learn from coding sessions"), and a
   setting's words followed later by the user's own document ("set the model
   server for coding work in our team doc"). ``settings/trigger.py``: a cue
   that is one of the phrase's own words counts only when the phrase opens a
   clause as an order and is not described right after it ("… is my motto");
   ``_content_after`` reads "in / into / on <the user's document>" up to eight
   words later. Real requests still arm the writers.
2. Daemon sentences still said "Build session" for what the app calls a
   coding session. A scan holds them out (the schema keeps old words as search
   aliases on purpose, and the parity table is an internal record).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from iron_jarvis.settings.trigger import settings_ask

NOT_ASKING = [
    "only suggest, never act is my motto",
    "Only suggest, never act was the rule at my last firm",
    "my manager says I should learn from coding sessions with seniors",
    "set the model server for coding work in our team doc",
    "change your model server's address in the README",
    "set send coding work to a model server on in the pipeline yaml",
]

ASKING = [
    "please only suggest, never act",
    "Only suggest, never act.",
    "turn on only suggest never act",
    "Learn from my coding sessions.",
    "learn from coding sessions",
    "turn off learning from coding sessions",
    "set the model server for coding work to fleet-custom:glm",
    "change your model server's address to http://10.0.0.5:8000",
    "set my brain profile to the work wiki",
    "turn on folder watchers",
    "make the text bigger",
]


@pytest.mark.parametrize("text", NOT_ASKING)
def test_a_sentence_that_is_not_asking_arms_no_writer(text):
    assert not settings_ask(text).writers, text


@pytest.mark.parametrize("text", ASKING)
def test_a_real_request_still_arms_the_writers(text):
    ask = settings_ask(text)
    assert ask.reader and ask.writers, text


_ROOT = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"
#: Where the old words stay on purpose: the schema's search aliases (so "build
#: session steps" still finds the row) and the parity table (an internal record).
_ALLOWED = {"settings/schema.py", "harness/parity.py"}


def _strings(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("debug", "info", "warning", "error", "exception")):
            skip.update(id(n) for n in ast.walk(node))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            yield node.lineno, node.value


def test_no_daemon_sentence_says_build_session():
    found = []
    for path in sorted(_ROOT.rglob("*.py")):
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _ALLOWED:
            continue
        for line, text in _strings(path):
            if "Build session" in text:
                found.append(f"{rel}:{line}: {text[:80]!r}")
    assert not found, "\n".join(found)
