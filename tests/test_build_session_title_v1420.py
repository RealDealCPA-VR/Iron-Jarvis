"""v1.420.0 (fix plan C2.9): a Build session's title is REAL.

A session named by a LONG first question (cut at 48 characters with "…")
is renamed by its first reply: three to six words of the reply's first
sentence, at most 48 characters, cut at a word boundary — deterministic, no
model call. A short question keeps its title; a title the user typed is
never touched; a later reply never renames it.
"""

from __future__ import annotations

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.harness import sessions as S

LONG_Q = (
    "please look at the login page and figure out why the remember me box "
    "keeps getting unticked after a refresh"
)


def _q(text: str = LONG_Q) -> dict:
    return {"role": "user", "content": text}


def _a(text: str, **kw) -> dict:
    return {"role": "assistant", "content": text, **kw}


def test_a_long_question_is_named_by_the_first_reply():
    cut = S.session_title(S.NEW_SESSION_TITLE, [], [_q()])
    assert cut.endswith("…") and len(cut) == 49
    t = S.session_title(cut, [_q()], [_q(), _a("The cookie is cleared on every reload. I changed it.")])
    assert t == "The cookie is cleared on every"


def test_the_title_is_three_to_six_words_and_at_most_48_characters():
    t = S.session_title(S.NEW_SESSION_TITLE, [], [_q(), _a("## **Fixed** the `remember_me` flag in auth/session.py today: details")])
    assert t == "Fixed the remember_me flag in auth/session.py"
    assert 3 <= len(t.split()) <= 6 and len(t) <= 48
    long_words = "Moved the config-loading-internationalisation code into shared."
    t2 = S.session_title(S.NEW_SESSION_TITLE, [], [_q(), _a(long_words)])
    assert t2 == "Moved the config-loading-internationalisation"  # the 4th word would pass 48
    # Too few words for a title: the cut question stays.
    cut = S.session_title(S.NEW_SESSION_TITLE, [], [_q()])
    assert S.session_title(S.NEW_SESSION_TITLE, [], [_q(), _a("Sure! Looking now.")]) == cut


def test_a_fence_and_a_link_are_not_the_title():
    reply = "```\ncode first\n```\n\n- See [the login form](http://x) for the bug. More."
    t = S.session_title(S.NEW_SESSION_TITLE, [], [_q(), _a(reply)])
    assert t == "See the login form for the"


def test_a_short_question_typed_title_failed_or_later_reply_keep_the_title():
    short = _q("fix the login")
    assert S.session_title(S.NEW_SESSION_TITLE, [], [short, _a("The cookie is cleared on reload.")]) == "fix the login"
    assert S.session_title("My own name", [_q()], [_q(), _a("The cookie is cleared on reload.")]) == "My own name"
    cut = S.session_title(S.NEW_SESSION_TITLE, [], [_q()])
    assert S.session_title(cut, [_q()], [_q(), _a("The cookie is cleared on reload.", error=True)]) == cut
    first = [_q(), _a("No reply words")]
    assert S.session_title(cut, first, first + [_a("The cookie is cleared on reload.")]) == cut


def test_append_lines_writes_the_reply_title(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    init_db(engine)
    row = S.create(engine, str(tmp_path), "")
    assert S.append_lines(engine, row.id, [_q()])["ok"]
    assert S.append_lines(engine, row.id, [_a("I traced the cookie to the reload handler. Fixed.")])["ok"]
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord

    with session_scope(engine) as db:
        title = db.get(ChatThreadRecord, row.id).title
    assert title == "I traced the cookie to the"
