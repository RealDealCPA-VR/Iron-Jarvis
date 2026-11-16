"""North-star fix 8.3 (v1.507.0): no "lane" on screen.

The POST chat lane (``daemon/chat_turn.py``, used by the phone and by any
caller that cannot show a card) refuses a call that needs an approval. Its
words reached people in two engine phrases: the reply's footnote "needs an
approval this lane cannot ask for", and the refusal the model reads (and
the Activity page shows) "this lane cannot ask for approval; use the chat
page (POST /chat/stream)". Now both say it in the app's words: the call
"needs your OK, which can only be given in the Iron Jarvis window".

THE SCAN. The daemon's twin of the dashboard's vocabulary scan
(``tests/test_vocabulary_daemon_answers_v13600.py``) reads the retired-word
list the dashboard keeps, and "lane" cannot join that list: the Session
board's columns are lanes on screen. So "lane" is scanned here, for
daemon-sent text only, in two places:

* every sentence the v13600 readers find (the route modules read whole, and
  answer positions everywhere in ``src/iron_jarvis``);
* every module-level string table in ``src/iron_jarvis/daemon/`` (where the
  lanes keep their refusal words: ``SPEND_NOT_ASKED_TAIL``,
  ``ONCE_NOT_ASKED_TAIL``, ``REFUSAL_FOOTNOTE_WORDS``), which the v13600
  readers do not read.

Comments and docstrings are not read: "lane" is the code's own word for the
two chat paths, used in hundreds of comments.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

from iron_jarvis.daemon import chat_turn as ct
from tests.test_vocabulary_beyond_dashboard_v13580 import py_user_strings
from tests.test_vocabulary_daemon_answers_v13600 import FULL_READ, TASK_DATA, answer_strings

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iron_jarvis"
DAEMON = SRC / "daemon"

LANE = re.compile(r"\blanes?\b", re.IGNORECASE)
APP_WORDS = "needs your OK, which can only be given in the Iron Jarvis window"

#: (file, a piece of the sentence, why it may say "lane").
ALLOW: list[tuple[str, str, str]] = [
    ("src/iron_jarvis/daemon/pages.py", "card in its lane",
     "the Session board's columns are lanes on screen: a lane of a board, not a chat path"),
    ("src/iron_jarvis/harness_bench/bench.py", "--path lane",
     "a developer command's option value (`--path lane`), typed as written"),
    ("src/iron_jarvis/harness_bench/runners.py", "--path lane",
     "a developer command's option value (`--path lane`), typed as written"),
    ("src/iron_jarvis/harness_bench/runners.py", "runs on the lane path only",
     "the benchmark's own `lane` path, named as its option value"),
]


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _module_tables(src: str) -> list[tuple[int, str]]:
    """Every string in a module-level assignment (a table or a constant)."""
    out: list[tuple[int, str]] = []
    for node in ast.parse(src).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            for sub in ast.walk(node.value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    out.append((sub.lineno, sub.value))
    return out


def _hits() -> list[tuple[str, int, str]]:
    full = {p.resolve() for p in FULL_READ}
    hits: set[tuple[str, int, str]] = set()
    for path in sorted(SRC.rglob("*.py")):
        if TASK_DATA in path.parents:
            continue
        src = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        reader = py_user_strings if path.resolve() in full else answer_strings
        found = list(reader(src))
        if DAEMON in path.parents:
            found += _module_tables(src)
        for line, text in found:
            if LANE.search(text):
                hits.add((_rel(path), line, text))
    return sorted(hits)


def _allowed(hit) -> bool:
    return any(hit[0] == f and piece in hit[2] for f, piece, _why in ALLOW)


def test_the_post_lanes_refusal_says_it_in_the_apps_words():
    for check in (SimpleNamespace(ask=True, words="this would spend 2 credits", once_only=False),
                  SimpleNamespace(ask=True, words="a commit on the session's branch", once_only=True)):
        reason = ct.spend_not_asked_invoke_kwargs(check)["deny_reason"]
        assert APP_WORDS in reason
        assert not LANE.search(reason) and "POST /" not in reason and "/chat/stream" not in reason
        # The footnote still knows its own refusal (the registry's prefix).
        assert ct.refusal_label(f"{ct.SPEND_NOT_ASKED_LABEL}: {reason}") == ct.SPEND_NOT_ASKED_FOOTNOTE
    words = ct.REFUSAL_FOOTNOTE_WORDS[ct.SPEND_NOT_ASKED_FOOTNOTE]
    assert words == APP_WORDS and not LANE.search(words)


def test_no_lane_in_anything_the_daemon_says():
    bad = [h for h in _hits() if not _allowed(h)]
    assert not bad, "'lane' in daemon-sent text:\n" + "\n".join(f"  {f}:{n} {t[:120]!r}" for f, n, t in bad)


def test_every_allow_entry_still_matches_something():
    hits = _hits()
    for entry in ALLOW:
        assert any(h[0] == entry[0] and entry[1] in h[2] for h in hits), entry


def test_the_scan_reads_the_lanes_string_tables():
    """Anti-vacuity: the module-table reader reaches the refusal words."""
    src = (DAEMON / "chat_turn.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    texts = [t for _n, t in _module_tables(src)]
    assert any(APP_WORDS in t for t in texts)
    assert any("Iron Jarvis window" in t for t in texts)
