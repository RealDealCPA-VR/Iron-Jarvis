"""v1.420.0 review of the reply-derived session title (fix plan C2.9).

The title a session takes from its first reply is shown far beyond the
session: the list, Running now, the bell, the desktop notice and the phone.
So the reply's first sentence must never carry a SECRET (a model that says
"Set password=… in the env file" first), an absolute path into the user's
folders (only its last part), a markdown TABLE row or an HTML tag; a URL is
never capitalised into "Https://…"; and a "title" of fewer than three real
words is no title.
"""

from __future__ import annotations

from iron_jarvis.harness import sessions as S

LONG_Q = (
    "please look at the login page and figure out why the remember me box "
    "keeps getting unticked after a refresh"
)


def _title(reply: str) -> str:
    q = {"role": "user", "content": LONG_Q}
    a = {"role": "assistant", "content": reply}
    return S.session_title(S.NEW_SESSION_TITLE, [q], [q, a])


def _cut() -> str:
    return S.session_title(S.NEW_SESSION_TITLE, [], [{"role": "user", "content": LONG_Q}])


def test_a_secret_in_the_first_sentence_never_reaches_the_title():
    t = _title("Set password=hunter22 in the env file now.")
    assert "hunter22" not in t
    assert t.startswith("Set password=")
    t2 = _title("Use key sk-ant-api03-abcd1234 for the call now.")
    assert "abcd1234" not in t2
    t3 = _title("Push to https://bob:pw4321@git.co/r now please.")
    assert "pw4321" not in t3


def test_an_absolute_path_is_named_by_its_last_part():
    t = _title("Reading C:\\Users\\VR\\clients\\smith\\a.txt now ok please.")
    assert "clients" not in t and "Users" not in t
    assert t == "Reading a.txt now ok please"
    # A path relative to the folder stays as the reply wrote it.
    assert _title("Fixed the flag in auth/session.py today.") == "Fixed the flag in auth/session.py today"


def test_a_table_row_or_an_html_tag_is_not_a_title():
    assert _title("| a | b | c |\n|---|---|---|\n| 1 | 2 | 3 |") == _cut()
    t = _title("<details>Summary of the login page fix here</details>")
    assert "<" not in t and ">" not in t
    assert t == "Summary of the login page fix"


def test_a_url_is_not_capitalised():
    t = _title("https://example.com/login is the page that fails here.")
    assert not t.startswith("Https")


def test_three_real_words_or_the_question_stays():
    # Punctuation tokens are not words.
    assert _title("Ok — -> done.") == _cut()
