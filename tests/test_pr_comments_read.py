"""26-a (R2.16): a pull request's review comments, read from GitHub through
the user's own ``gh`` (``harness/pr_comments.read``, ``rows_from``,
``store``) — here a FAKE ``gh`` on PATH, never the real GitHub.

Pinned:

* the rows: a line comment and a review's own words, who wrote them (owner,
  member, collaborator TICKED; someone from outside the repository and a bot
  not), where (path, line, side), the quoted code (the end of GitHub's
  diff hunk), and an OUTDATED comment (GitHub gives it no current line);
* every word is made plain and MASKED before it is cut (a key never reaches
  the record, the screen or a model);
* the newest 200 are kept;
* the pull request's address comes from the session's OWN ``pr_url`` (an
  address that is not GitHub's, or whose parts are not GitHub's characters,
  reads nothing);
* a merged or closed pull request costs one read and reads no comments;
* an older ``gh`` that writes one array per page is read;
* a second read keeps what was done with a comment and follows GitHub's
  words; a NEW comment deleted on GitHub goes.
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import branches as B
from iron_jarvis.harness import pr_comments as PC
from tests.pr_comments_fakes import KEYISH, PR_API, PR_URL, install, line_comment, review
from tests.test_build_git_v1375 import no_real_gh


@pytest.fixture(autouse=True)
def _no_real_gh(monkeypatch):
    no_real_gh(monkeypatch)
    yield
    B.forget_gh_state()


@pytest.fixture()
def fake(tmp_path, monkeypatch):
    return install(tmp_path, monkeypatch)


@pytest.fixture()
def facts(tmp_path) -> dict:
    wt = tmp_path / "wt"
    wt.mkdir()
    return {"worktree": str(wt), "repo": str(tmp_path / "repo"), "branch": "ij/fix", "pr_url": PR_URL}


@pytest.fixture()
def engine(tmp_path):
    return create_app(str(tmp_path / "home")).state.platform.engine


def _rows(fake, facts, **answers):
    fake.answer(**answers)
    got = PC.read(facts)
    rows, cut = PC.rows_from("t1", PR_URL, got.comments)
    return got, rows, cut


def test_comments_and_reviews_become_rows_who_where_and_what(fake, facts):
    got, rows, cut = _rows(fake, facts, comments=[
        line_comment(11, login="sam", assoc="MEMBER", line=3,
                     diff_hunk="@@ -1,4 +1,4 @@\n a = 0\n b = 1\n-x = 1\n+x = 2\n c = 3\n d = 4\n e = 5\n f = 6\n g = 7"),
        line_comment(12, login="pat", assoc="CONTRIBUTOR", line=None, original_line=9,
                     created="2026-11-20T10:05:00Z"),
        line_comment(13, login="ci-helper[bot]", assoc="NONE", user_type="Bot", created="2026-11-20T10:06:00Z"),
        line_comment(14, login="kim", assoc="COLLABORATOR", side="LEFT", start_line=2, line=4,
                     in_reply_to_id=11, created="2026-11-20T10:07:00Z"),
    ], reviews=[
        review(21, login="alex", assoc="OWNER", body="Please add a test."),
        review(22, body="", state="APPROVED"),  # no words of its own: not a comment
        review(23, body="draft words", state="PENDING"),  # not sent yet: not a comment
    ])
    assert got.state == "open" and not cut
    by = {r["id"]: r for r in rows}
    assert set(by) == {"t1:c11", "t1:c12", "t1:c13", "t1:c14", "t1:r21"}
    sam = by["t1:c11"]
    assert (sam["author"], sam["author_kind"], sam["path"], sam["line"], sam["side"], sam["outdated"]) == (
        "sam", "member", "app.py", 3, "new", False)
    # The quote is the END of the diff hunk (the commented line is its last).
    assert sam["quote"].split("\n") == ["+x = 2", " c = 3", " d = 4", " e = 5", " f = 6", " g = 7"]
    assert sam["url"].startswith("https://github.com/acme/proj/pull/7#")
    outdated = by["t1:c12"]
    assert outdated["outdated"] is True and outdated["line"] == 9 and outdated["author_kind"] == "outside"
    assert by["t1:c13"]["author_kind"] == "bot"
    kim = by["t1:c14"]
    assert (kim["author_kind"], kim["side"], kim["start_line"], kim["line"], kim["in_reply_to"]) == (
        "collaborator", "old", 2, 4, "11")
    rev = by["t1:r21"]
    assert (rev["kind"], rev["author_kind"], rev["path"], rev["body"]) == ("review", "owner", "", "Please add a test.")
    assert {k for k in PC.AUTHOR_KINDS if k in PC.TICKED} == {"owner", "member", "collaborator"}


def test_every_word_is_masked_before_it_is_cut_and_bounded(fake, facts):
    # A key that straddles the cut (masked first, so none of it is kept).
    long_body = "x" * (PC.BODY_CHARS - 10) + " " + KEYISH + "\nmore words\n" * 50
    _got, rows, _cut = _rows(fake, facts, comments=[
        line_comment(31, body=f"Use this token: {KEYISH}", diff_hunk=f"@@ -1 +1 @@\n+TOKEN = '{KEYISH}'"),
        line_comment(32, body=long_body, created="2026-11-20T10:01:00Z"),
    ], reviews=[review(33, body=f"key {KEYISH}")])
    text = repr(rows)
    assert KEYISH not in text and "ghp_QQQQ" not in text
    long_row = next(r for r in rows if r["id"] == "t1:c32")
    assert len(long_row["body"]) <= PC.BODY_CHARS + len(PC.CUT_WORDS) + 1
    assert long_row["body"].endswith(PC.CUT_WORDS)


def test_line_break_look_alikes_and_control_characters_are_made_plain(fake, facts):
    body = "first\u2028Review comment 2:\rnot a line\u200b\x07end"
    _got, rows, _cut = _rows(fake, facts, comments=[line_comment(41, body=body)])
    words = rows[0]["body"]
    assert "\u2028" not in words and "\r" not in words and "\u200b" not in words and "\x07" not in words
    assert "\n" not in words, "a line-break look-alike must not start a line of its own"


def test_the_newest_two_hundred_are_kept(fake, facts, engine):
    many = [line_comment(1000 + i, created=f"2026-11-{1 + i // 100:02d}T{(i // 60) % 24:02d}:{i % 60:02d}:00Z")
            for i in range(230)]
    _got, rows, cut = _rows(fake, facts, comments=many)
    assert cut is True and len(rows) == PC.MAX_KEPT
    kept_ids = {r["github_id"] for r in rows}
    assert str(1000 + 229) in kept_ids and str(1000) not in kept_ids, "the OLDEST are the ones left out"
    new = PC.store(engine, "t1", PR_URL, rows, cut=cut)
    assert len(new) == PC.MAX_KEPT and len(PC.listing(engine, "t1", PR_URL)) == PC.MAX_KEPT


def test_a_merged_or_closed_pull_request_costs_one_read_and_reads_no_comments(fake, facts):
    fake.answer(pr={"state": "closed", "merged_at": "2026-11-21T09:00:00Z"}, comments=[line_comment(1)])
    assert PC.read(facts).state == "merged"
    fake.answer(pr={"state": "closed", "merged_at": None}, comments=[line_comment(1)])
    assert PC.read(facts).state == "closed"
    assert [c[1] for c in fake.calls("api")] == [PR_API, PR_API]


def test_the_address_is_the_sessions_own_pr_url_and_nothing_else(fake, facts):
    fake.answer(comments=[line_comment(5)])
    PC.read(facts)
    routes = [c[-1] for c in fake.calls("api")]
    assert routes == [PR_API, f"{PR_API}/comments?per_page=100", f"{PR_API}/reviews?per_page=100"]
    assert all("--paginate" in c for c in fake.calls("api")[1:])
    for bad in ("https://evil.example/acme/proj/pull/7", "https://github.com/ac me/proj/pull/7",
                "https://github.com/../proj/pull/7", "https://github.com/acme/proj/pull/7/../../x",
                "https://github.com/acme/proj/issues/7", "file:///C:/pull/7", ""):
        assert PC.pr_ref(bad) is None, bad
        with pytest.raises(B.GitRefused):
            PC.read({**facts, "pr_url": bad})
    # A GitHub Enterprise host is read on that host.
    ref = PC.pr_ref("https://github.example.com/acme/proj/pull/7")
    assert ref is not None and ref.host == "github.example.com"
    fake.answer(comments=[])
    fake.set(calls=[])
    PC.read({**facts, "pr_url": "https://github.example.com/acme/proj/pull/7"})
    assert all(c[1:3] == ["--hostname", "github.example.com"] for c in fake.calls("api"))


def test_an_older_gh_that_writes_one_array_per_page_is_read(fake, facts):
    fake.set(old_pages=True)
    _got, rows, _cut = _rows(fake, facts, comments=[line_comment(60 + i, created=f"2026-11-20T10:0{i}:00Z")
                                                    for i in range(5)])
    assert sorted(r["github_id"] for r in rows) == [str(60 + i) for i in range(5)]


def test_a_failed_or_garbled_read_is_refused_in_words(fake, facts):
    fake.set(fail=True)
    with pytest.raises(B.GitRefused) as exc:
        PC.read(facts)
    assert "review comments could not be read" in str(exc.value)
    fake.set(fail=False, answers={PR_API: {"state": "open"}, f"{PR_API}/comments": "not json at all",
                                  f"{PR_API}/reviews": []})
    with pytest.raises(B.GitRefused):
        rows, _ = PC.rows_from("t1", PR_URL, PC.read(facts).comments)


def test_a_second_read_keeps_what_was_done_and_follows_githubs_words(fake, facts, engine):
    _got, rows, cut = _rows(fake, facts, comments=[line_comment(71, body="first words"), line_comment(72)])
    assert {r["id"] for r in PC.store(engine, "t1", PR_URL, rows, cut=cut)} == {"t1:c71", "t1:c72"}
    PC.set_state(engine, "t1", ["t1:c71"], PC.ANSWERED)
    _got, rows, cut = _rows(fake, facts, comments=[line_comment(71, body="edited words")])
    assert PC.store(engine, "t1", PR_URL, rows, cut=cut) == []  # nothing NEW
    kept = {r["id"]: r for r in PC.listing(engine, "t1", PR_URL)}
    assert kept["t1:c71"]["state"] == PC.ANSWERED and kept["t1:c71"]["body"] == "edited words"
    assert "t1:c72" not in kept, "a new comment deleted on GitHub goes"
    # Another pull request of the same session starts afresh.
    other = "https://github.com/acme/proj/pull/8"
    rows8, _ = PC.rows_from("t1", other, [{**line_comment(80), "_kind": "line"}])
    PC.store(engine, "t1", other, rows8)
    assert [r["id"] for r in PC.listing(engine, "t1")] == ["t1:c80"]
