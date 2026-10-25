"""Harness gaps REVIEW (after v1.388.2): defects the review found in the five
"coding sessions" fixes, each pinned red on the doer's code first.

1. Fix 1 — ``GET /chat/turns/running`` is a SYNC route: it runs on a worker
   thread while the event loop writes the same turns. ``TurnLog.open_asks``
   walked the turn's LIVE card dict (``_asks``) in a Python comprehension, and
   the loop adds a card (``_note_ask``) between two items of it — "dictionary
   changed size during iteration", which ``running_replies`` swallowed, so the
   list came back SHORT (a coding session missing from Running now) and a
   warning was logged. The interleaving is FORCED here (a thread switch lands
   at the membership check between two items), not hoped for.

2. Fix 2 — a failed reply's technical words are now KEPT on the reply line
   (``errorDetail``: on disk in the session, in every backup and mirror). The
   shared mask (``detections.redact.mask``) leaves a bare ``Bearer <value>``
   (one not after an ``Authorization`` key), a Google ``AIza…`` key, an xAI
   ``xai-…`` key, a Groq ``gsk_…`` key and a Hugging Face ``hf_…`` token as
   they are. The failure words' own mask now takes them out — in the live
   error frame (``failure_words.technical_text``, both chat lanes) and on the
   line (``routes/build.failure_detail``).

3. Fix 4 — the summary press's daemon refusal while a reply runs still said
   "before you compact it" (the page's own refusal said "summarize").

4. Fix 4 — the answer scanner's ALLOW list gained an entry whose piece was the
   retired word ITSELF (``harness_bench/refine.py``: "harness"), which
   allowed every line of that file holding the word, today's and tomorrow's.
   An ALLOW (or PENDING) piece must name its line: once its retired words are
   taken out, something is left.
"""

from __future__ import annotations

import json
import re
import threading

from iron_jarvis.daemon import durable_turns as D


# --------------------------------------------------------------------------- #
# 1. open_asks reads a snapshot (a worker thread never meets the loop's insert)
# --------------------------------------------------------------------------- #


def _ask(lg: D.TurnLog, aid: str, tool: str = "shell") -> None:
    lg._note_ask("approval", json.dumps({"id": aid, "tool": tool}))


class _LoopAddsACard:
    """The PENDING set the reader checks membership in. The first check is
    where the interpreter may switch threads (Python code between two items);
    there, the LOOP's thread shows a new approval card on the same turn."""

    def __init__(self, lg: D.TurnLog, ids: set[str]) -> None:
        self.lg = lg
        self.ids = ids
        self.checks = 0

    def __contains__(self, aid: object) -> bool:
        self.checks += 1
        if self.checks == 1:
            loop_thread = threading.Thread(target=_ask, args=(self.lg, "a3", "write_file"))
            loop_thread.start()
            loop_thread.join(5)
        return aid in self.ids


def test_open_asks_never_walks_the_live_card_dict_while_the_loop_adds_one():
    lg = D.TurnLog("t1", "s1")
    lg.surface = "build"
    _ask(lg, "a1")
    _ask(lg, "a2", "process_start")
    pending = _LoopAddsACard(lg, {"a1", "a2", "a3"})
    rows = lg.open_asks(pending)  # raised RuntimeError on the doer's code
    assert [r[0] for r in rows] == ["a1", "a2"], rows
    assert pending.checks == 2
    # The card the loop added is there for the NEXT read.
    assert [r[0] for r in lg.open_asks({"a1", "a2", "a3"})] == ["a1", "a2", "a3"]


def test_running_replies_lists_every_turn_when_a_card_lands_mid_read(monkeypatch):
    """The listing level: the read that met the loop's insert used to return
    the rows collected so far (the rest of the turns silently dropped)."""
    logs = D.TurnLogs(engine=None)
    first = D.TurnLog("t1", "s1")
    first.surface = "build"
    _ask(first, "a1")
    _ask(first, "a2")
    second = D.TurnLog("t2", "s2")
    second.surface = "build"
    logs._logs = {"t1": first, "t2": second}
    real = D.TurnLog.open_asks

    def racing(self, pending):
        # Force the same interleaving through the real method: the loop adds
        # a card between the first two membership checks of the FIRST turn.
        return real(self, _LoopAddsACard(self, set(pending)) if self is first else pending)

    monkeypatch.setattr(D.TurnLog, "open_asks", racing)
    rows = D.running_replies(logs, {}, ["a1", "a2"])
    assert [r["turn_id"] for r in rows] == ["t1", "t2"], rows
    assert rows[0]["waiting"] is True and rows[1]["waiting"] is False


# --------------------------------------------------------------------------- #
# 2. secrets never reach a kept failure line (nor the live frame)
# --------------------------------------------------------------------------- #

_SECRETS = {
    "bare bearer": ("proxy said: Bearer abcdefghijklmnop0123456789XYZ is not valid", "abcdefghijklmnop0123456789XYZ"),
    "bearer colon": ("Bearer: Zx81kq0PLMnbvcxz123456 refused", "Zx81kq0PLMnbvcxz123456"),
    "google key": ("400 key AI" "zaSyA-abcdefghijklmnopqrstuvwxyz012345 invalid", "AI" "zaSyA-abcdefghijklmnopqrstuvwxyz012345"),
    "xai key": ("401 xa" "i-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ", "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"),
    "groq key": ("gs" "k_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd rejected", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd"),
    "hf token": ("h" "f_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh is gone", "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh"),
    # Already masked by the shared mask — kept here so a change to either
    # mask cannot lose them.
    "authorization": ("authorization: Bearer abcdefghijklmnop0123456789XYZ", "abcdefghijklmnop0123456789XYZ"),
    "url password": ("https://admin:hunter2pass@proxy.example.com:8080 down", "hunter2pass"),
    "sk key": ("s" "k-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA used", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"),
}


def test_a_kept_failure_line_never_holds_a_secret_shape():
    from iron_jarvis.daemon.routes.build import failure_detail

    for name, (text, secret) in _SECRETS.items():
        kept = failure_detail(text)
        assert secret not in kept, (name, kept)
        assert kept.strip(), name


def test_the_live_failure_frame_masks_the_same_shapes():
    """The error frame's ``technical`` (both chat lanes, a coding session's
    live Details) is what the line keeps — one mask for both."""
    from iron_jarvis.providers.failure_words import technical_text

    for name, (text, secret) in _SECRETS.items():
        out = technical_text(None, text)
        assert secret not in out, (name, out)


def test_ordinary_words_beside_bearer_are_left_alone():
    """CONTROL: the extra rules take a token, never a sentence."""
    from iron_jarvis.providers.failure_words import technical_text

    for text in (
        "Bearer authentication failed: the token expired",
        "the Bearer token is missing",
        "status 401 Unauthorized",
        "ExpiredSignature: aizawa is a name",
    ):
        assert technical_text(None, text) == text


# --------------------------------------------------------------------------- #
# 3. the summary refusal says "summarize"
# --------------------------------------------------------------------------- #


def test_the_summary_refusal_while_a_reply_runs_says_summarize():
    from iron_jarvis.daemon.routes import build as build_routes

    words = build_routes.COMPACT_RUNNING
    assert "summarize" in words, words
    assert not re.search(r"\bcompact\b", words, re.IGNORECASE), words


def test_an_unused_folder_layer_is_named_the_way_the_bell_and_settings_name_it(tmp_path):
    """The "/" menu's note for a folder whose `.ironjarvis/harness` files are
    invalid (GET /build/sessions/{id}/skills) read "This folder's harness
    files … were not used" — the engine room's word the bell ("the coding
    session instructions") and Settings ("coding-session instructions")
    dropped in fix 4. The answer scanner cannot see it (a ``return`` from a
    helper is not an answer position), so it is pinned here. The folder's
    own NAME (``.ironjarvis/harness``) stays: it is the path the user edits."""
    from iron_jarvis.harness import components as C

    hdir = tmp_path / ".ironjarvis" / "harness"
    hdir.mkdir(parents=True)
    (hdir / "notes.txt").write_text("not a file the layer takes", encoding="utf-8")
    layer = C.read_project_layer(str(tmp_path))
    assert layer.present and not layer.used, layer
    lead = "This folder's coding-session instructions (.ironjarvis/harness) were not used: "
    assert layer.note.startswith(lead), layer.note


# --------------------------------------------------------------------------- #
# 4. an ALLOW piece names its line
# --------------------------------------------------------------------------- #


def test_no_allow_or_pending_piece_is_only_a_retired_word():
    from tests.test_vocabulary_beyond_dashboard_v13580 import RETIRED
    from tests.test_vocabulary_daemon_answers_v13600 import ALLOW, PENDING

    bare = []
    for f, piece, _why in ALLOW + PENDING:
        rest = piece
        for _word, rx in RETIRED:
            rest = rx.sub("", rest)
        if not rest.strip():
            bare.append((f, piece))
    assert not bare, f"an ALLOW/PENDING piece must name its line, not only the word: {bare}"
