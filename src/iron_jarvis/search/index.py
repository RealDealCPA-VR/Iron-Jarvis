"""SQLite FTS5 history search — the substrate every "what did we say about X"
feature reads.

Iron Jarvis remembers conversations in four places (browser-owned chat threads,
daemon-owned comm threads, Agents-page rounds, agent sessions) and, until this
module, could not SEARCH any of them: the memory fabric's ``sessions`` source
full-scanned 400 rows and counted overlapping words. :class:`SearchIndex` gives
all four a single ranked, filterable, offline, sub-millisecond index.

--------------------------------------------------------------------------
DECISION 1 — the FTS5 table stores its OWN copy of the text (not external-content)
--------------------------------------------------------------------------
``searchdoc_fts`` is a plain FTS5 table keyed by an explicit ``rowid`` that
equals :attr:`SearchDocRecord.n`::

    CREATE VIRTUAL TABLE searchdoc_fts USING fts5(text, tokenize='porter unicode61')

The two alternatives were weighed and rejected:

* **external content** (``content='searchdocrecord', content_rowid='n'``) halves
  storage, but its delete protocol is
  ``INSERT INTO fts(fts, rowid, text) VALUES('delete', ?, <THE ORIGINAL TEXT>)``
  and it MUST run BEFORE the content row disappears. Iron Jarvis deletes history
  from at least three places outside this module — ``DELETE /chat/threads/{id}``,
  ``prune_events``' bulk ``sa_delete`` (which bypasses ORM events entirely), and
  a future steward — and any one of them running first silently corrupts the
  index's internal doc-size accounting, which then mis-ranks EVERY later query
  with no error anywhere. Three other pairs write against this API; an ordering
  rule that corrupts data when broken is the wrong thing to hand them.
* **contentless** (``content=''``) cannot serve ``snippet()`` at all (there is no
  stored text to snippet), and ``contentless_delete=1`` needs SQLite ≥ 3.43 —
  the frozen build ships the BUILD python's ``sqlite3.dll``, so that is a
  version floor this project cannot assert.

Own-content costs a second copy of at most :data:`~.models.MAX_TEXT` chars per
doc and buys: deletes are ``DELETE FROM searchdoc_fts WHERE rowid IN (...)``
(order-independent, idempotent, impossible to corrupt), ``snippet()`` works, and
no SQLite version floor. The duplication is not really additive either — the
``basic`` LIKE fallback needs ``SearchDocRecord.text`` present regardless, so the
row table's copy was never optional. :meth:`rebuild` re-derives the whole index
from the row table.

--------------------------------------------------------------------------
DECISION 2 — score normalization: corpus-relative, bounded to [0.35, 0.95]
--------------------------------------------------------------------------
SQLite's ``bm25()`` is unbounded and NEGATIVE-signed (more negative = better),
and its magnitude collapses toward 0 when a term appears in most documents
(measured: ``-1.1e-06`` for a 4-doc corpus vs ``-0.87`` for a selective term).
An absolute transform such as ``1/(1+|bm25|)`` therefore maps a perfectly good
hit in a small corpus to ~0, which ``MemoryFabric.recall``'s ``min_score=0.05``
would drop outright. So the mapping is RELATIVE to the result set::

    r_i    = max(0, -bm25_i)                  # relevance, larger = better
    ratio  = r_i / max(r)   (1.0 if max(r) == 0)
    decay  = 1 / (1 + 0.05 * i)               # i = 0-based rank
    score  = 0.35 + (0.95 - 0.35) * ratio * decay

Bounds are structural: ``ratio ∈ [0,1]`` and ``decay ∈ (0,1]``, so
``score ∈ [0.35, 0.95] ⊂ [0,1]`` for every possible corpus — pinned by
``test_scores_stay_in_bounds_over_a_real_corpus``.

The 0.35 FLOOR is deliberate: FTS5's implicit operator between bare terms is
AND, so a hit contains EVERY query term — exactly what
``fabric._lexical`` would score 1.0. A real match must never be filtered out by
``min_score``. The 0.95 CEILING leaves headroom so a perfect cosine hit (1.0)
still outranks a lexical one when the fabric sorts across sources. ``decay``
only breaks ties: BM25 saturates flat on common terms, and without it a page of
equally-weighted hits would all pin at the ceiling and lose their ordering.

ONE deliberate exception to the floor: a hit found only by the WIDENING tier
(DECISION 3, tier 4) is not conjunctive — it carries SOME of the query's content
words, not all — so :func:`_damp_widened` scales it by that fraction and by
:data:`LOOSE_PENALTY` afterwards, which can and should take it under 0.35. The
floor exists to protect a hit that contains every term; a partial match has no
claim on it. What survives unconditionally is the outer contract every consumer
actually depends on: ``0 < score <= 0.95``, so a real hit can never be confused
with the no-hits case (the arithmetic floor is
``0.35 × 0.7 × 2/8 ≈ 0.061``, because the overlap check rejects anything under
two of at most eight content words).

--------------------------------------------------------------------------
DECISION 3 — query hardening: four tiers, and NUL is the one that bites
--------------------------------------------------------------------------
FTS5's query language is a real grammar, and ordinary user text is full of
syntax errors: ``s-corp`` is ``no such column: corp``, ``S-corp AND (election``
is unbalanced, ``*`` is ``unknown special query``, ``""`` / whitespace / a bare
``AND`` are all ``syntax error``. :meth:`search` never lets any of it escape:

1. the cleaned query VERBATIM — so ``OR`` / ``NEAR()`` / ``"phrases"`` / ``col:``
   still work for anyone who means them;
2. on ``OperationalError``, the whole query as ONE double-quoted phrase (quotes
   doubled). Measured against the full hostile battery, this tier cannot raise —
   and it recovers the common cases (``s-corp`` → the phrase ``"s-corp"`` finds
   the right rows);
3. if tiers 1-2 both returned ZERO rows, a prefix retry (``"tok" "tok" "last"*``)
   so a partially-typed word still matches. Additive by construction: it can
   only ever turn an empty result into a non-empty one;
4. if tiers 1-3 ALL returned zero rows and the caller did not write query syntax
   of their own, the WIDENING: an ``OR`` of the query's content words.

Tier 4 is the one that decides whether a sentence is searchable at all. Every
tier above it is conjunctive — FTS5's implicit operator between bare terms is
AND — so a hit has to contain EVERY word the user typed, and nobody's stored
message contains "what did we say about the rental property depreciation" in
full. Measured against one seeded conversation and seven realistic ways of
asking for it, the strict ladder found it **2 times out of 7**: only the two
phrasings that happened to be bare keywords. That is not a recall problem, it is
a "find that conversation from March about the S-corp election" problem, and it
was reachable from the ``history_search`` tool and the Ctrl+K palette as well as
from ``MemoryFabric.recall``, which is why the fix lives HERE rather than in one
consumer.

Four rules keep the reach from costing answer quality:

* it is a LAST tier — a query that matched conjunctively is never touched, so
  exact search behaviour is bit-identical to before;
* :func:`_has_operator` declines to widen anything carrying ``"``, ``*``, ``^``
  or an UPPERCASE ``AND``/``OR``/``NOT``/``NEAR``: someone who wrote query
  language gets it honoured, including the honest miss;
* a widened hit must carry :data:`LOOSE_MIN_OVERLAP` of the question's content
  words (prefix-tolerant, because the index stems and this counter does not), or
  a single shared common word would drag an unrelated conversation in;
* its score is the normalized score scaled by the measured overlap FRACTION and
  by :data:`LOOSE_PENALTY`, so a partial match ranks BELOW an exact one — see
  DECISION 2 for why it is fine (and wanted) for that to fall under the 0.35
  floor, and :func:`_damp_widened` for the arithmetic.

The trap: a NUL byte breaks BOTH tier 1 AND tier 2 (SQLite's parser reads a C
string, so ``"\\x00bad"`` is an *unterminated string* even fully quoted). C0
control characters are therefore STRIPPED before any tier runs, and the whole
chain sits under a final catch-all.

Tier 4 is FTS5-only. The ``basic`` LIKE fallback stays conjunctive: it has no
``OR`` to reach for and no stemming to be tolerant of, and that mode is already
declared honestly degraded rather than quietly approximate.

--------------------------------------------------------------------------
House rules honoured here
--------------------------------------------------------------------------
* Reads NEVER raise — :meth:`search` / :meth:`stats` / :meth:`available` return
  an empty/degraded answer instead. Writes ``log.exception`` and continue: a
  broken index must never break a chat save.
* Every write takes ``db=`` so Pair S2 can sync INSIDE its existing
  ``session_scope`` (and its existing lock) — the row write and its index write
  then commit or roll back together.
* Write EXCLUSION is therefore path-dependent, and that is DECISION 4 on
  :class:`SearchIndex`: the ``db=`` path takes this instance's lock; the
  self-owned path takes SQLite's writer slot up front (``BEGIN IMMEDIATE``) and
  NO Python lock, so a background backfill can never stall a chat save.
* No FTS5 in this SQLite build → :meth:`available` is False, ``mode`` is
  ``"basic"``, and search degrades to a bounded LIKE scan returning the SAME
  :class:`SearchHit` shape. Honest degradation, never a 500.
"""

from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Sequence

from sqlalchemy import DateTime as SADateTime
from sqlalchemy import Engine, bindparam
from sqlalchemy import text as sa_text
from sqlmodel import Session as DBSession
from sqlmodel import select

from ..core.db import session_scope
from ..core.logging import get_logger
from .models import (
    FTS_TABLE,
    MAX_ENTRIES,
    MAX_TEXT,
    SEARCH_KINDS,
    SearchDocRecord,
    SearchHit,
)

log = get_logger("search.index")

#: The mapped row table's name (kept next to FTS_TABLE so the raw SQL below and
#: the model can never drift apart).
DOC_TABLE = SearchDocRecord.__tablename__  # "searchdocrecord"

#: Score normalization constants — see DECISION 2 in the module docstring.
SCORE_FLOOR = 0.35
SCORE_CEIL = 0.95
RANK_DECAY = 0.05

#: Hard ceiling on ``search(limit=...)``. Also the tail cap a thread sync keeps
#: (:data:`~.models.MAX_ENTRIES`), so the index can never hold more of a thread
#: than ``PUT /chat/threads/{id}`` itself keeps.
MAX_LIMIT = 200

#: Longest query we hand to SQLite. A 5000-char query is not a search, it is a
#: paste; truncating keeps the parser (and the LIKE fallback) bounded.
MAX_QUERY_CHARS = 512

#: Tokens of context in an FTS5 ``snippet()``, and the char clip applied after —
#: matched to ``fabric._SNIPPET_CHARS`` so hits read the same in a ground block.
SNIPPET_TOKENS = 20
SNIPPET_CHARS = 280

#: Rows the ``basic`` (no-FTS5) fallback may pull before ranking in Python.
BASIC_SCAN_MIN = 200
BASIC_SCAN_MAX = 1000

#: Max bind parameters per statement (SQLite's default limit is 999).
_CHUNK = 400

#: Score multiplier for a hit only the WIDENING tier found (DECISION 3, tier 4).
#: A partial-overlap match is a real answer — it is how ``fabric._lexical`` has
#: always scored the non-embedded stores — but it is a weaker claim than a
#: conjunctive hit, so it is damped below the band an exact match occupies.
#: Applied ON TOP of the measured overlap fraction, so the damping is
#: calibrated rather than arbitrary: a hit carrying every content word keeps
#: 0.95 × 0.7 = 0.665, one carrying two of three keeps 0.44 — under a typical
#: cosine hit and well over ``ground()``'s 0.05 floor.
LOOSE_PENALTY = 0.7

#: Content words a widened hit must actually carry, and (the same number, on
#: purpose) the fewest content words a query must have before it is widened at
#: all. Two is the smallest threshold that means "about the same thing".
LOOSE_MIN_OVERLAP = 2

#: Chars compared when counting overlap against a porter-stemmed index.
STEM_CHARS = 5

#: Stripped before the widening tier builds its ``OR``. NOT a general stopword
#: list — just the connective tissue of a SPOKEN question, which is exactly what
#: turns a natural sentence into a zero-hit FTS5 conjunction.
ASK_WORDS = frozenset(
    """a an and are about again as at be but by can could did do does for from
    get give go had has have how in into is it its me my of on or our out please
    pull said say show so tell that the their them then there these they this to
    told up us was we were what whats when where which who why will with would
    you your""".split()
)

#: C0 controls + DEL. ``\x00`` in particular breaks EVERY FTS5 query tier.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TOKEN = re.compile(r"\w+", re.UNICODE)
#: Words the widening tier considers "content" (matches ``fabric._WORD``).
_CONTENT_WORD = re.compile(r"[a-z0-9]{2,}")
#: Query text that means the caller WROTE FTS5 syntax — a phrase, a prefix, a
#: column/initial-token anchor, or one of the UPPERCASE-only boolean operators.
#: Its presence disables the widening tier (see :func:`_has_operator`).
_OPERATOR = re.compile(r'["*^]|(?<![A-Za-z])(?:AND|OR|NOT|NEAR)(?![A-Za-z])')
#: Backfill phases, in order. ``session`` last because sessions are cheapest.
_PHASES = ("chat", "round", "session")


# ---------------------------------------------------------------- helpers ---
def _clip(value: str, n: int = SNIPPET_CHARS) -> str:
    value = (value or "").strip().replace("\n", " ")
    return value if len(value) <= n else value[: n - 1] + "…"


def _clean_query(query: Any) -> str:
    """User text → something safe to hand SQLite (never None, never a NUL)."""
    text = _CONTROL.sub(" ", str(query or ""))
    return text.strip()[:MAX_QUERY_CHARS].strip()


def _phrase_expr(query: str) -> str:
    """Tier 2: the whole query as one FTS5 phrase literal (quotes doubled)."""
    return '"' + query.replace('"', '""') + '"'


def _prefix_expr(query: str) -> str:
    """Tier 3: ``"tok" "tok" "last"*`` — every term quoted, last one a prefix."""
    tokens = _TOKEN.findall(query.lower())[:8]
    if not tokens:
        return ""
    quoted = [f'"{t}"' for t in tokens[:-1]]
    quoted.append(f'"{tokens[-1]}"*')
    return " ".join(quoted)


def _has_operator(query: str) -> bool:
    """True when the user WROTE FTS5 query language and means it.

    Tier 4 must never rewrite such a query: ``"reasonable compensation"`` is a
    phrase the caller asked for exactly, ``seedance OR compensation`` is already
    a disjunction, ``a*`` is a deliberate prefix, and ``AND OR NEAR`` is an
    honest miss rather than an invitation to guess. Only the UPPERCASE forms are
    FTS5 operators, which is what keeps an ordinary sentence ("what did we say
    about...") out of this predicate.
    """
    return bool(_OPERATOR.search(query or ""))


def _content_words(query: str, limit: int = 8) -> list[str]:
    """The query's content words, in order, deduplicated, question-words removed."""
    out: list[str] = []
    for w in _CONTENT_WORD.findall((query or "").lower()):
        if w not in ASK_WORDS and w not in out:
            out.append(w)
    return out[:limit]


def _overlap(words: Sequence[str], text: str) -> int:
    """How many of *words* the text carries, prefix-tolerant.

    The index tokenizes with ``porter``, this counter does not, so ``schedule``
    vs ``schedules`` and ``file`` vs ``filing`` have to agree on their first
    :data:`STEM_CHARS` characters or every stemmed hit would be miscounted as
    noise and thrown away by the overlap floor.
    """
    have = {w for w in _CONTENT_WORD.findall((text or "").lower())}
    stems = {w[:STEM_CHARS] for w in have}
    return sum(1 for w in words if w in have or w[:STEM_CHARS] in stems)


def _loose_expr(words: Sequence[str]) -> str:
    """Tier 4: the query reduced to an FTS5 ``OR`` of its content words.

    ``""`` when there is nothing worth widening to (fewer than
    :data:`LOOSE_MIN_OVERLAP` content words) — a one-word query is already the
    loosest form of itself, and widening it could only mean matching a word the
    user did not type.
    """
    if len(words) < LOOSE_MIN_OVERLAP:
        return ""
    return " OR ".join(f'"{w}"' for w in words)


def _damp_widened(hits: list[SearchHit], words: Sequence[str]) -> list[SearchHit]:
    """Filter + re-score tier 4's hits, then re-rank them (see DECISION 3).

    ``OR`` will happily return a conversation that shares ONE common word with
    the question ("what did we decide about the payroll tax deposit *schedule*"
    matching a thread about depreciation *schedules*), and one such line in a
    four-line grounding block is a real cost — so a hit must carry at least
    :data:`LOOSE_MIN_OVERLAP` content words to survive, and what it does carry
    sets its score.
    """
    kept: list[SearchHit] = []
    for h in hits:
        n = _overlap(words, f"{h.snippet} {h.title}")
        if n < LOOSE_MIN_OVERLAP:
            continue
        h.score = round(float(h.score) * LOOSE_PENALTY * (n / len(words)), 4)
        kept.append(h)
    kept.sort(key=lambda h: h.score, reverse=True)
    return kept


def _coerce_dt(value: Any) -> datetime | None:
    """Anything a caller or a stored row might carry → an aware UTC datetime.

    Accepts a ``datetime``, an ISO string (with or without ``Z``/offset, with a
    space or ``T`` separator — SQLAlchemy's SQLite DATETIME storage format is
    ``YYYY-MM-DD HH:MM:SS.ffffff``), or an epoch in seconds or milliseconds
    (the dashboard's ``Date.now()``). ``None`` when it cannot be read — callers
    substitute a sane default rather than raise.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value or "").strip()
    if not raw:
        return None
    iso = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(iso)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        epoch = float(raw)
    except (TypeError, ValueError):
        return None
    if epoch > 1e11:  # milliseconds
        epoch /= 1000.0
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _normalize_scores(relevances: Sequence[float]) -> list[float]:
    """BM25 (or lexical) relevance → ``[SCORE_FLOOR, SCORE_CEIL]``.

    See DECISION 2. Inputs must already be ORDERED best-first and non-negative.
    """
    if not relevances:
        return []
    top = max(relevances)
    out: list[float] = []
    for i, r in enumerate(relevances):
        ratio = 1.0 if top <= 0 else max(0.0, min(1.0, r / top))
        decay = 1.0 / (1.0 + RANK_DECAY * i)
        out.append(round(SCORE_FLOOR + (SCORE_CEIL - SCORE_FLOOR) * ratio * decay, 4))
    return out


def _like_escape(token: str) -> str:
    for ch in ("\\", "%", "_"):
        token = token.replace(ch, "\\" + ch)
    return token


def _basic_snippet(text: str, tokens: Sequence[str]) -> str:
    """A deterministic ``[marked]`` snippet for the no-FTS5 path.

    Shape parity with FTS5's ``snippet()`` matters beyond looks:
    ``MemoryFabric._dedupe`` keys on ``snippet[:120].lower()``, so the same row
    and query must always produce the same string.
    """
    flat = (text or "").replace("\n", " ")
    low = flat.lower()
    first = min(
        (low.find(t) for t in tokens if t and low.find(t) >= 0),
        default=-1,
    )
    start = 0 if first < 0 else max(0, first - 60)
    window = flat[start : start + SNIPPET_CHARS]
    for t in tokens:
        if not t:
            continue
        m = re.search(re.escape(t), window, re.IGNORECASE)
        if m:
            window = f"{window[: m.start()]}[{m.group(0)}]{window[m.end() :]}"
    out = window.strip()
    if start > 0:
        out = "…" + out
    if start + SNIPPET_CHARS < len(flat):
        out = out + "…"
    return out


class SearchIndex:
    """Ranked full-text search over Iron Jarvis' conversation history.

    Construct once per engine and share it — the canonical accessor is
    ``core.db.search_index(engine)`` (the platform exposes the same object as
    ``platform.search_index``). Sharing is what makes the capability probe a
    one-time cost and what makes the write exclusion below MEAN anything: two
    instances on one engine would be two locks, i.e. no lock at all.

    --------------------------------------------------------------------------
    DECISION 4 — the write lock is taken ONLY on the ``db=`` path (v1.142.0)
    --------------------------------------------------------------------------
    There are two write paths and they need two different kinds of exclusion:

    * **``db=`` (riding a caller's transaction)** — the caller owns the
      transaction, and ``_replace`` does a read-modify-write across it (SELECT
      the doomed rowids → delete them from the FTS shadow → delete the rows →
      insert the new ones). A caller's Session may not have begun a write
      transaction yet, so SQLite alone does not close that window; :attr:`_lock`
      does. Held for microseconds, never across a transaction this object owns.
    * **``db=None`` (self-owned)** — the same read-modify-write, but in a
      transaction opened HERE. It is made atomic by ``BEGIN IMMEDIATE`` (see
      :meth:`_begin_immediate`), which takes SQLite's writer slot before the
      first SELECT, so the whole RMW is inside one exclusive transaction. This
      path deliberately takes NO Python lock.

    Holding :attr:`_lock` across a self-owned transaction (what this class did
    until v1.142.0) is an ABBA inversion against SQLite's single writer slot:
    every live write seam is already inside a ``session_scope`` when it calls in
    with ``db=``, so seam-holds-writer / backfill-holds-lock resolves only when
    ``busy_timeout`` fires 30 s later. MEASURED on this repo, one shared index
    with a backfill thread running against 12 concurrent chat-save-shaped
    writers: p50 save **33,011 ms** / p95 **33,015 ms** and 70% of the
    backfill's docs lost to "database is locked". After this change, on the same
    bench: p50 **1.6 ms**, p95 **3.7 ms**, zero writes lost on either side.
    Pinned by ``test_a_backfill_sized_write_cannot_stall_a_chat_save``.

    The invariant that keeps the remaining path acyclic: **a ``db=`` caller must
    not already hold SQLite's writer slot when it calls in** (lock → writer, in
    that order, always). Every seam honours it — ``routes/chat.py`` dropped its
    pre-``sync_thread`` flush for exactly this reason, and comm/round appends
    plus ``prune_events`` only READ before calling (pysqlite runs those in
    autocommit, so no transaction is open yet). The capability probe therefore
    gets its OWN lock: ``available()`` can fire from inside a caller's open
    transaction, and blocking there on the write lock would re-create the cycle
    by the back door.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        # Guards the read-modify-write of a thread's docs on the ``db=`` path
        # ONLY (see DECISION 4). Reentrant so a compound write (sync several
        # threads in one caller transaction) can nest without deadlocking
        # itself. ALWAYS the inner lock: nothing here calls back into a
        # caller's locked code, and it is never held across a transaction this
        # object opened.
        self._lock = threading.RLock()
        # Separate from ``_lock`` on purpose: the one-shot capability probe must
        # never make a writer that already holds SQLite's writer slot queue
        # behind the write lock.
        self._probe_lock = threading.Lock()
        self._available: bool | None = None

    # ------------------------------------------------------------ capability -
    def available(self) -> bool:
        """True when this SQLite build can serve FTS5 (cached after the first
        call). False degrades every read to the bounded LIKE scan.

        Deliberately READ-ONLY in the steady state. The first ``available()``
        can fire from inside :meth:`_replace`, i.e. while a CALLER's write
        transaction is open (Pair S2 syncs inside its own ``session_scope``);
        a probe that took SQLite's single writer slot would sit on
        ``busy_timeout`` and then fail that caller's write. Only when the read
        fails does it fall back to creating the table — which self-heals a
        ``SearchIndex`` built on an engine whose ``init_db`` predates
        ``_ensure_fts``. Never raises.

        Serialized by :attr:`_probe_lock`, NOT the write lock — see DECISION 4.
        """
        if self._available is None:
            with self._probe_lock:
                if self._available is None:
                    self._available = self._probe()
        return self._available

    _PROBE_SQL = (
        f"SELECT rowid FROM {FTS_TABLE} "
        f"WHERE {FTS_TABLE} MATCH 'ironjarvis_capability_probe' LIMIT 1"
    )

    def _probe(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.exec_driver_sql(self._PROBE_SQL).fetchall()
            return True
        except Exception:  # noqa: BLE001 — table missing, or no FTS5 at all
            log.debug("FTS5 read probe failed; trying to create the table", exc_info=True)
        try:
            with self.engine.begin() as conn:
                conn.exec_driver_sql(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
                    "USING fts5(text, tokenize='porter unicode61')"
                )
                conn.exec_driver_sql(self._PROBE_SQL).fetchall()
            return True
        except Exception:  # noqa: BLE001 — no FTS5 here; degrade, never raise
            log.warning("FTS5 unavailable — history search degrades to LIKE", exc_info=True)
            return False

    @property
    def mode(self) -> str:
        return "fts5" if self.available() else "basic"

    # --------------------------------------------------------------- writes --
    def sync_thread(
        self,
        thread_id: str,
        kind: str,
        title: str,
        project_id: str,
        entries: Iterable[dict[str, Any]],
        *,
        ref: str | None = None,
        db: DBSession | None = None,
    ) -> int:
        """Re-index one thread: delete every doc for it, insert the new tail.

        Delete-all-then-insert (rather than a diff) is what makes this SAFE to
        call from the chat autosave, which rewrites the whole message array on
        every keystroke-batch: the index simply becomes whatever the thread now
        is, and the ``entries[-200:]`` tail cap gives the index the exact same
        200-message horizon ``PUT /chat/threads/{id}`` keeps. Idempotent —
        syncing the same thread twice leaves the same rows.

        *entries* are the raw stored messages; ``role``/``who``, ``content``/
        ``text``, ``at`` and ``seq`` are all read leniently, so a caller can pass
        chat messages, comm messages, or round entries unchanged. ``seq`` is the
        index in the FULL list (not the capped tail) so a deep link still points
        at the right message.

        Pass ``db=`` to write inside the caller's ``session_scope`` — the docs
        then commit atomically with the thread row (Pair S2 calls it that way,
        inside its own lock, before its commit). Returns the docs indexed; 0 and
        a logged exception on any failure — an index write must never break the
        write it is shadowing.
        """
        try:
            thread_id = str(thread_id or "").strip()
            if not thread_id:
                return 0
            rows = self._docs_for_thread(thread_id, kind, title, project_id, entries, ref)
            with self._scope(db) as (session, owns):
                self._replace(
                    session,
                    "thread_id = :tid",
                    {"tid": thread_id},
                    rows,
                )
                if owns:
                    session.commit()
            return len(rows)
        except Exception:  # noqa: BLE001 — never break the caller's write
            log.exception("search sync_thread failed for %s", thread_id)
            return 0

    def sync_session(self, session_row: Any, *, db: DBSession | None = None) -> int:
        """Upsert the ONE doc describing an agent session (task + summary +
        result). Called from the orchestrator's post-run step, so a finished run
        becomes searchable the moment it lands. Never raises."""
        session_id = ""
        try:
            session_id = str(getattr(session_row, "id", "") or "").strip()
            if not session_id:
                return 0
            blob = "\n".join(
                part
                for part in (
                    str(getattr(session_row, "task", "") or ""),
                    str(getattr(session_row, "summary", "") or ""),
                    str(getattr(session_row, "result", "") or ""),
                )
                if part.strip()
            )
            when = _coerce_dt(getattr(session_row, "finished_at", None)) or _coerce_dt(
                getattr(session_row, "created_at", None)
            )
            doc = SearchDocRecord(
                kind="session",
                thread_id="",
                ref=session_id,
                seq=0,
                role="session",
                text=blob[:MAX_TEXT],
                project_id=str(getattr(session_row, "project_id", "") or ""),
                at=when or datetime.now(timezone.utc),
                title=_clip(str(getattr(session_row, "task", "") or "session"), 80),
            )
            rows = [doc] if doc.text.strip() else []
            with self._scope(db) as (sess, owns):
                self._replace(
                    sess,
                    "kind = 'session' AND ref = :ref",
                    {"ref": session_id},
                    rows,
                )
                if owns:
                    sess.commit()
            return len(rows)
        except Exception:  # noqa: BLE001
            log.exception("search sync_session failed for %s", session_id)
            return 0

    def drop_thread(self, thread_id: str, *, db: DBSession | None = None) -> int:
        """Forget a whole thread (``DELETE /chat/threads/{id}``). Never raises."""
        return self._drop("thread_id = :v", str(thread_id or ""), db)

    def drop_session(self, session_id: str, *, db: DBSession | None = None) -> int:
        """Forget one session doc (``prune_events``). Never raises."""
        return self._drop("kind = 'session' AND ref = :v", str(session_id or ""), db)

    def drop_run(self, run_id: str, *, db: DBSession | None = None) -> int:
        """Forget every doc pointing at an agent run. Never raises."""
        return self._drop("ref = :v", str(run_id or ""), db)

    def drop_refs(self, refs: Iterable[str], *, db: DBSession | None = None) -> int:
        """Bulk :meth:`drop_run` — for ``prune_events``, which expires ids by the
        thousand and must not issue a statement per id. Never raises."""
        try:
            wanted = [str(r) for r in refs if str(r or "").strip()]
            if not wanted:
                return 0
            removed = 0
            with self._scope(db) as (session, owns):
                for i in range(0, len(wanted), _CHUNK):
                    chunk = wanted[i : i + _CHUNK]
                    keys = {f"r{j}": v for j, v in enumerate(chunk)}
                    clause = f"ref IN ({', '.join(':' + k for k in keys)})"
                    removed += self._replace(session, clause, keys, [])
                if owns:
                    session.commit()
            return removed
        except Exception:  # noqa: BLE001
            log.exception("search drop_refs failed")
            return 0

    def rebuild(self, *, db: DBSession | None = None) -> int:
        """Re-derive the ENTIRE FTS index from the row table.

        The own-content design's answer to external content's ``'rebuild'``
        command: drop every shadow row and re-insert from ``searchdocrecord``,
        which stays the single source of truth. Repairs an index damaged by a
        raw DELETE issued outside this module. Returns rows re-indexed."""
        if not self.available():
            return 0
        try:
            with self._scope(db) as (session, owns):
                session.execute(sa_text(f"DELETE FROM {FTS_TABLE}"))
                rows = session.execute(
                    sa_text(f"SELECT n, text FROM {DOC_TABLE} ORDER BY n")
                ).all()
                payload = [{"rowid": r[0], "text": r[1] or ""} for r in rows]
                if payload:
                    session.execute(
                        sa_text(f"INSERT INTO {FTS_TABLE}(rowid, text) VALUES (:rowid, :text)"),
                        payload,
                    )
                if owns:
                    session.commit()
            return len(payload)
        except Exception:  # noqa: BLE001
            log.exception("search rebuild failed")
            return 0

    # ---------------------------------------------------------------- reads --
    def search(
        self,
        query: str,
        *,
        kinds: "str | Sequence[str] | None" = None,
        project_id: str | None = None,
        after: Any = None,
        before: Any = None,
        limit: int = 20,
    ) -> list[SearchHit]:
        """Ranked hits for *query*. NEVER raises and never 500s — a malformed
        query, a missing table, or a corrupt row yields ``[]``.

        ``after``/``before`` accept a ``datetime`` or an ISO string and are
        INCLUSIVE bounds on the doc's ``at``. ``kinds`` accepts one kind or a
        list. ``limit`` is clamped to :data:`MAX_LIMIT`. ``SearchHit.at`` comes
        back as an AWARE UTC datetime (matching ``core.ids.utcnow``), so
        ``as_dict()`` emits an offset-bearing ISO string a browser parses right.

        *query* may be a natural SENTENCE, not only keywords: when every strict
        (conjunctive) tier misses, DECISION 3's tier 4 retries an ``OR`` of the
        query's content words and damps what it finds below the exact band. A
        query carrying FTS5 syntax of its own is never rewritten.
        """
        try:
            cleaned = _clean_query(query)
            if not cleaned:
                return []
            limit = max(1, min(MAX_LIMIT, int(limit or 20)))
            kind_list = self._kind_list(kinds)
            lo = _coerce_dt(after)
            hi = _coerce_dt(before)
            if self.available():
                return self._search_fts(cleaned, kind_list, project_id, lo, hi, limit)
            return self._search_basic(cleaned, kind_list, project_id, lo, hi, limit)
        except Exception:  # noqa: BLE001 — a read must never take out a caller
            log.exception("search failed for %r", str(query)[:80])
            return []

    def stats(self) -> dict[str, Any]:
        """Index size + capability, for ``/diagnostics`` and the awareness line.
        Honest zeros (never raises) when the table is missing."""
        out: dict[str, Any] = {
            "docs": 0,
            "threads": 0,
            "sessions": 0,
            "available": False,
            "mode": "basic",
        }
        try:
            out["available"] = self.available()
            out["mode"] = "fts5" if out["available"] else "basic"
            with self.engine.connect() as conn:
                row = conn.execute(
                    sa_text(
                        f"SELECT COUNT(*), "
                        f"COUNT(DISTINCT CASE WHEN thread_id != '' THEN thread_id END), "
                        f"SUM(CASE WHEN kind = 'session' THEN 1 ELSE 0 END) "
                        f"FROM {DOC_TABLE}"
                    )
                ).first()
            if row:
                out["docs"] = int(row[0] or 0)
                out["threads"] = int(row[1] or 0)
                out["sessions"] = int(row[2] or 0)
        except Exception:  # noqa: BLE001
            log.warning("search stats read failed", exc_info=True)
        return out

    # ------------------------------------------------------------- backfill --
    def backfill(
        self, batch: int = 200, cursor: str | None = None, *, force: bool = False
    ) -> dict[str, Any]:
        """Index one CHUNK of pre-existing history; resumable via ``cursor``.

        Synchronous and bounded — the daemon calls it through
        ``asyncio.to_thread`` in a loop, feeding back the returned ``cursor``
        until ``done``. Phases run in order: chat/comm threads → round tables →
        sessions. The cursor is a keyset (``phase|iso|id``), NOT an offset, so
        concurrent inserts can't make it skip a row.

        IDEMPOTENT: a thread that already has docs is SKIPPED (live sync keeps it
        fresh; backfill only fills the historical gap), so a second full pass
        reports ``indexed: 0`` and changes nothing. ``force=True`` re-indexes
        regardless, for a repair.

        Returns ``{"indexed", "cursor", "done"}`` (plus ``scanned``, and
        ``error: True`` when something went wrong). Failure is contained at
        THREE levels so neither a bad row nor a bad page can wedge — or STARVE —
        the backfill:

        * one unindexable ROW is swallowed by :meth:`sync_thread` /
          :meth:`sync_session` (they return 0), and the cursor advances past it;
        * one unlistable PAGE is ISOLATED by :meth:`_isolate_page` — retried at
          ``batch=1`` to find the poison row, which is then logged and STEPPED
          OVER so the REST OF THE PHASE still gets indexed. Skipping straight to
          the next phase (what v1.142.0 did before this fix) abandoned every row
          after the bad one on EVERY future sweep, because the phase guard runs
          again from the same cursor and skips forward again — permanent,
          silent, and worst for the largest histories;
        * only a phase whose KEYS cannot be read at all (the table is gone, not
          one row is bad) skips to the next phase, and only a failure in the LAST
          phase parks (``done: True, error: True``).

        The caller's periodic re-check resumes from the start, which is cheap
        precisely because already-indexed threads are skipped.
        """
        try:
            return self._backfill(batch, cursor, force)
        except Exception:  # noqa: BLE001
            log.exception("search backfill failed at cursor %r", cursor)
            return {"indexed": 0, "scanned": 0, "cursor": cursor, "done": True, "error": True}

    # ------------------------------------------------------------- internals -
    @contextmanager
    def _scope(self, db: DBSession | None) -> Iterator[tuple[DBSession, bool]]:
        """Yield ``(session, owns_transaction)`` — and take the RIGHT exclusion
        for whichever of the two write paths this is (DECISION 4).

        With a caller-supplied ``db`` we NEVER commit (the caller's transaction
        decides — the whole point of letting a seam sync inside its own
        ``session_scope``) and we DO take :attr:`_lock`, because the caller's
        transaction may not have begun a write yet and SQLite would then leave
        the read-modify-write window open.

        Self-owned, we take NO Python lock and instead open the transaction with
        ``BEGIN IMMEDIATE``: the writer slot is held from before the first SELECT
        to the commit, which is strictly stronger than the old lock (it excludes
        every OTHER process and connection too, not just this instance) and,
        unlike the old lock, it is a resource SQLite itself can order against a
        caller's transaction instead of deadlocking with it.
        """
        if db is not None:
            with self._lock:
                yield db, False
        else:
            with session_scope(self.engine) as session:
                self._begin_immediate(session)
                yield session, True

    @staticmethod
    def _begin_immediate(session: DBSession) -> None:
        """Take SQLite's writer slot UP FRONT for a transaction we own.

        pysqlite opens a DEFERRED transaction (and runs a leading SELECT in
        autocommit), so without this the writer slot is grabbed mid-way — after
        ``_replace`` has already read the rowids it is about to delete. Under
        WAL that upgrade can also fail OUTRIGHT with ``SQLITE_BUSY_SNAPSHOT``,
        which ``busy_timeout`` does NOT retry.

        Issued on the RAW DBAPI cursor rather than through the Session so a
        failure here cannot poison the caller's SQLAlchemy state. Best-effort by
        design: a non-SQLite engine, or a connection already in a transaction,
        simply keeps the old deferred behaviour."""
        try:
            raw = session.connection().connection
            driver = getattr(raw, "driver_connection", None) or raw
            if getattr(driver, "in_transaction", False):
                return
            cur = raw.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
            finally:
                cur.close()
        except Exception:  # noqa: BLE001 — degrade to a deferred transaction
            log.debug("BEGIN IMMEDIATE unavailable; using a deferred transaction", exc_info=True)

    @staticmethod
    def _kind_list(kinds: "str | Sequence[str] | None") -> list[str]:
        if kinds is None:
            return []
        if isinstance(kinds, str):
            kinds = [kinds]
        return [str(k).strip() for k in kinds if str(k or "").strip()]

    def _docs_for_thread(
        self,
        thread_id: str,
        kind: str,
        title: str,
        project_id: str,
        entries: Iterable[dict[str, Any]],
        ref: str | None,
    ) -> list[SearchDocRecord]:
        all_entries = [e for e in (entries or []) if isinstance(e, dict)]
        tail = all_entries[-MAX_ENTRIES:]
        offset = len(all_entries) - len(tail)
        kind = str(kind or "chat").strip() or "chat"
        if kind not in SEARCH_KINDS:
            kind = "chat"
        title = _clip(str(title or ""), 120)
        pid = str(project_id or "")
        target = str(ref or thread_id)
        fallback_at = datetime.now(timezone.utc)
        docs: list[SearchDocRecord] = []
        for i, entry in enumerate(tail):
            body = entry.get("content")
            if body is None:
                body = entry.get("text")
            body = str(body or "").strip()
            if not body:
                continue
            role = entry.get("role") or entry.get("who") or ""
            try:
                seq = int(entry["seq"]) if entry.get("seq") is not None else offset + i
            except (TypeError, ValueError):
                seq = offset + i
            docs.append(
                SearchDocRecord(
                    kind=kind,
                    thread_id=thread_id,
                    ref=target,
                    seq=seq,
                    role=str(role)[:64],
                    text=body[:MAX_TEXT],
                    project_id=pid,
                    at=_coerce_dt(entry.get("at")) or fallback_at,
                    title=title,
                )
            )
        return docs

    def _drop(self, clause: str, value: str, db: DBSession | None) -> int:
        if not value.strip():
            return 0
        try:
            with self._scope(db) as (session, owns):
                removed = self._replace(session, clause, {"v": value}, [])
                if owns:
                    session.commit()
            return removed
        except Exception:  # noqa: BLE001
            log.exception("search drop failed (%s=%s)", clause, value)
            return 0

    def _replace(
        self,
        db: DBSession,
        clause: str,
        params: dict[str, Any],
        rows: list[SearchDocRecord],
    ) -> int:
        """Delete the docs matching *clause*, then insert *rows* — mapped table
        and FTS shadow together, in ONE transaction.

        The FTS delete is attempted UNCONDITIONALLY (not gated on
        :meth:`available`): a stale shadow row is index corruption, and skipping
        the delete because the capability flag happens to be off would leave
        one behind. A build without FTS5 just has nothing to delete, so the
        statement is swallowed quietly."""
        doomed = [
            r[0]
            for r in db.execute(
                sa_text(f"SELECT n FROM {DOC_TABLE} WHERE {clause}"), params
            ).all()
        ]
        if doomed:
            self._fts_delete(db, doomed)
            db.execute(sa_text(f"DELETE FROM {DOC_TABLE} WHERE {clause}"), params)
        if rows:
            db.add_all(rows)
            db.flush()  # assigns SearchDocRecord.n == the FTS rowid
            if self.available():
                db.execute(
                    sa_text(f"INSERT INTO {FTS_TABLE}(rowid, text) VALUES (:rowid, :text)"),
                    [{"rowid": r.n, "text": r.text} for r in rows],
                )
        return len(doomed)

    @staticmethod
    def _fts_delete(db: DBSession, rowids: Sequence[int]) -> None:
        for i in range(0, len(rowids), _CHUNK):
            chunk = rowids[i : i + _CHUNK]
            keys = {f"n{j}": v for j, v in enumerate(chunk)}
            try:
                db.execute(
                    sa_text(
                        f"DELETE FROM {FTS_TABLE} "
                        f"WHERE rowid IN ({', '.join(':' + k for k in keys)})"
                    ),
                    keys,
                )
            except Exception:  # noqa: BLE001 — no FTS5 build → nothing to delete
                log.debug("FTS shadow delete skipped", exc_info=True)
                return

    # -- FTS5 read path ------------------------------------------------------
    def _search_fts(
        self,
        query: str,
        kinds: list[str],
        project_id: str | None,
        after: datetime | None,
        before: datetime | None,
        limit: int,
    ) -> list[SearchHit]:
        rows = self._match(_clean_query(query), kinds, project_id, after, before, limit)
        if rows is None:  # tier 1 was a syntax error → tier 2, the phrase
            rows = self._match(_phrase_expr(query), kinds, project_id, after, before, limit)
        if not rows:
            prefix = _prefix_expr(query)
            if prefix:
                rows = self._match(prefix, kinds, project_id, after, before, limit) or []
        # Tier 4 — the widening. Fires ONLY when every stricter tier came back
        # empty AND the caller did not write query syntax of their own, so an
        # exact/operator query is still answered verbatim and this tier can only
        # turn "nothing" into "something weaker". Its hits are damped below the
        # normalized band before they leave, never merged with strict ones.
        widened_on: list[str] = []
        if not rows and not _has_operator(query):
            words = _content_words(query)
            loose = _loose_expr(words)
            if loose:
                found = self._match(loose, kinds, project_id, after, before, limit) or []
                if found:
                    rows, widened_on = found, words
        rows = rows or []
        relevances = [max(0.0, -float(r[9] or 0.0)) for r in rows]
        scores = _normalize_scores(relevances)
        hits: list[SearchHit] = []
        for row, score in zip(rows, scores):
            hits.append(
                SearchHit(
                    kind=str(row[0] or ""),
                    ref=str(row[1] or ""),
                    thread_id=str(row[2] or ""),
                    title=str(row[3] or ""),
                    snippet=_clip(str(row[8] or "")),
                    role=str(row[4] or ""),
                    at=_coerce_dt(row[5]),
                    project_id=str(row[6] or ""),
                    score=score,
                    seq=int(row[7] or 0),
                )
            )
        return _damp_widened(hits, widened_on) if widened_on else hits

    def _match(
        self,
        expr: str,
        kinds: list[str],
        project_id: str | None,
        after: datetime | None,
        before: datetime | None,
        limit: int,
    ) -> "list[Any] | None":
        """Run ONE FTS5 MATCH. ``None`` means "that expression was a syntax
        error, try the next tier"; a list (possibly empty) means it ran."""
        if not expr:
            return None
        where = [f"{FTS_TABLE} MATCH :q"]
        params: dict[str, Any] = {"q": expr, "lim": limit}
        binds = []
        self._filters(where, params, binds, kinds, project_id, after, before)
        sql = (
            "SELECT d.kind, d.ref, d.thread_id, d.title, d.role, d.at, d.project_id, "
            f"d.seq, snippet({FTS_TABLE}, 0, '[', ']', '…', {SNIPPET_TOKENS}) AS snip, "
            f"bm25({FTS_TABLE}) AS bm "
            f"FROM {FTS_TABLE} JOIN {DOC_TABLE} AS d ON d.n = {FTS_TABLE}.rowid "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY bm ASC, d.at DESC, d.n ASC LIMIT :lim"
        )
        stmt = sa_text(sql)
        if binds:
            stmt = stmt.bindparams(*binds)
        try:
            with self.engine.connect() as conn:
                return list(conn.execute(stmt, params).all())
        except Exception:  # noqa: BLE001 — FTS5 syntax error → next tier
            log.debug("FTS5 expression rejected: %r", expr[:80], exc_info=True)
            return None

    @staticmethod
    def _filters(
        where: list[str],
        params: dict[str, Any],
        binds: list[Any],
        kinds: list[str],
        project_id: str | None,
        after: datetime | None,
        before: datetime | None,
    ) -> None:
        """Shared WHERE fragments so the FTS5 and LIKE paths filter IDENTICALLY.

        ``after``/``before`` are bound with an explicit ``DateTime`` type so
        SQLAlchemy's SQLite DATETIME bind processor formats them exactly the way
        the stored column was written — comparing a hand-formatted string
        against that column is how date filters silently return nothing."""
        if kinds:
            keys = {f"k{i}": k for i, k in enumerate(kinds)}
            where.append(f"d.kind IN ({', '.join(':' + k for k in keys)})")
            params.update(keys)
        if project_id:
            where.append("d.project_id = :pid")
            params["pid"] = str(project_id)
        if after is not None:
            where.append("d.at >= :after")
            params["after"] = after
            binds.append(bindparam("after", type_=SADateTime))
        if before is not None:
            where.append("d.at <= :before")
            params["before"] = before
            binds.append(bindparam("before", type_=SADateTime))

    # -- LIKE fallback read path (mode "basic") ------------------------------
    def _search_basic(
        self,
        query: str,
        kinds: list[str],
        project_id: str | None,
        after: datetime | None,
        before: datetime | None,
        limit: int,
    ) -> list[SearchHit]:
        """No FTS5: a bounded, newest-first LIKE scan ranked in Python.

        Honestly degraded — no stemming, no BM25, and only the most recent
        :data:`BASIC_SCAN_MAX` matching rows are considered — but the returned
        shape, the filters, the ``[marked]`` snippet, and the ``[0.35, 0.95]``
        score band are IDENTICAL to the FTS5 path, so every consumer is
        indifferent to which one ran."""
        tokens = _TOKEN.findall(query.lower())[:8]
        if not tokens:
            return []
        scan = max(BASIC_SCAN_MIN, min(BASIC_SCAN_MAX, limit * 20))
        where: list[str] = []
        params: dict[str, Any] = {"lim": scan}
        binds: list[Any] = []
        for i, tok in enumerate(tokens):
            where.append(f"d.text LIKE :t{i} ESCAPE '\\'")
            params[f"t{i}"] = f"%{_like_escape(tok)}%"
        self._filters(where, params, binds, kinds, project_id, after, before)
        sql = (
            "SELECT d.kind, d.ref, d.thread_id, d.title, d.role, d.at, d.project_id, "
            f"d.seq, d.text FROM {DOC_TABLE} AS d "
            f"WHERE {' AND '.join(where)} ORDER BY d.at DESC, d.n DESC LIMIT :lim"
        )
        stmt = sa_text(sql)
        if binds:
            stmt = stmt.bindparams(*binds)
        with self.engine.connect() as conn:
            rows = list(conn.execute(stmt, params).all())
        scored: list[tuple[float, Any]] = []
        for row in rows:
            body = str(row[8] or "")
            low = body.lower()
            rel = 0.0
            for tok in tokens:
                if re.search(rf"(?<!\w){re.escape(tok)}(?!\w)", low):
                    rel += 1.0
                elif tok in low:
                    rel += 0.5
            scored.append((rel / len(tokens), row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        scored = scored[:limit]
        scores = _normalize_scores([s for s, _ in scored])
        hits: list[SearchHit] = []
        for (_, row), score in zip(scored, scores):
            hits.append(
                SearchHit(
                    kind=str(row[0] or ""),
                    ref=str(row[1] or ""),
                    thread_id=str(row[2] or ""),
                    title=str(row[3] or ""),
                    snippet=_clip(_basic_snippet(str(row[8] or ""), tokens)),
                    role=str(row[4] or ""),
                    at=_coerce_dt(row[5]),
                    project_id=str(row[6] or ""),
                    score=score,
                    seq=int(row[7] or 0),
                )
            )
        return hits

    # -- backfill internals --------------------------------------------------
    @staticmethod
    def _parse_cursor(cursor: str | None) -> tuple[str, datetime | None, str]:
        if not cursor:
            return _PHASES[0], None, ""
        parts = str(cursor).split("|", 2)
        phase = parts[0] if parts and parts[0] in _PHASES else _PHASES[0]
        when = _coerce_dt(parts[1]) if len(parts) > 1 else None
        last = parts[2] if len(parts) > 2 else ""
        return phase, when, last

    @staticmethod
    def _next_phase(phase: str) -> str | None:
        i = _PHASES.index(phase)
        return _PHASES[i + 1] if i + 1 < len(_PHASES) else None

    def _keyset(self, model: Any, when: datetime | None, last: str, batch: int) -> list[Any]:
        stmt = select(model).order_by(model.created_at, model.id).limit(batch)
        if when is not None:
            stmt = stmt.where(
                (model.created_at > when)
                | ((model.created_at == when) & (model.id > last))
            )
        with session_scope(self.engine) as db:
            return list(db.exec(stmt))

    def _already_indexed(self, column: str, values: list[str], kinds: list[str]) -> set[str]:
        if not values or not kinds:
            return set()
        found: set[str] = set()
        with self.engine.connect() as conn:
            for i in range(0, len(values), _CHUNK):
                chunk = values[i : i + _CHUNK]
                keys = {f"v{j}": v for j, v in enumerate(chunk)}
                kkeys = {f"kk{j}": k for j, k in enumerate(kinds)}
                sql = (
                    f"SELECT DISTINCT {column} FROM {DOC_TABLE} "
                    f"WHERE {column} IN ({', '.join(':' + k for k in keys)}) "
                    f"AND kind IN ({', '.join(':' + k for k in kkeys)})"
                )
                params = {**keys, **kkeys}
                found.update(str(r[0]) for r in conn.execute(sa_text(sql), params).all())
        return found

    def _run_phase(
        self, phase: str, batch: int, when: datetime | None, last: str, force: bool
    ) -> tuple[int, int, str]:
        """Index ONE page of *phase*. May raise — :meth:`_isolate_page` is the
        containment, not a bare skip."""
        if phase == "chat":
            return self._backfill_chat(batch, when, last, force)
        if phase == "round":
            return self._backfill_round(batch, when, last, force)
        return self._backfill_sessions(batch, when, last, force)

    @staticmethod
    def _phase_model(phase: str) -> Any:
        """The row model a phase pages over, or ``None`` when this DB has no
        such table (round tables live in another package and may be absent)."""
        try:
            if phase == "chat":
                from ..core.models import ChatThreadRecord

                return ChatThreadRecord
            if phase == "round":
                from ..agents.threads import AgentThreadRecord

                return AgentThreadRecord
            from ..core.models import Session

            return Session
        except Exception:  # noqa: BLE001 — no such table here
            log.debug("no row model for backfill phase %r", phase, exc_info=True)
            return None

    def _advance_phase(self, phase: str, *, error: bool = False) -> dict[str, Any]:
        nxt = self._next_phase(phase)
        out: dict[str, Any] = {
            "indexed": 0,
            "scanned": 0,
            "cursor": None if nxt is None else f"{nxt}||",
            "done": nxt is None,
        }
        if error:
            out["error"] = True
        return out

    def _poison_head(
        self, phase: str, when: datetime | None, last: str
    ) -> "tuple[str, str] | None":
        """The keyset coordinates of the FIRST row after ``(when, last)``, read
        with a two-COLUMN select instead of a whole-entity load.

        That narrowness is the point: the usual reason a page cannot be listed
        is one row whose ORM load raises (an unreadable column, a value the type
        refuses to coerce), and ``created_at``/``id`` are still perfectly
        readable on that row. Those two values are all a cursor needs to step
        OVER it. ``None`` when even this cannot be read — then the phase itself,
        not a row, is broken."""
        model = self._phase_model(phase)
        if model is None:
            return None
        try:
            stmt = (
                select(model.created_at, model.id)
                .order_by(model.created_at, model.id)
                .limit(1)
            )
            if when is not None:
                stmt = stmt.where(
                    (model.created_at > when)
                    | ((model.created_at == when) & (model.id > last))
                )
            with session_scope(self.engine) as db:
                row = db.exec(stmt).first()
        except Exception:  # noqa: BLE001
            log.debug("backfill could not read keys for phase %r", phase, exc_info=True)
            return None
        if not row:
            return None
        created = _coerce_dt(row[0])
        row_id = str(row[1] or "")
        if created is None or not row_id:
            # Without BOTH halves the next cursor would not be a keyset — an
            # empty timestamp reads back as "start of phase" and would loop.
            return None
        return created.isoformat(), row_id

    def _isolate_page(
        self, phase: str, when: datetime | None, last: str, force: bool
    ) -> dict[str, Any]:
        """A page of *phase* raised. Narrow it to the ONE poison row, step past
        it, and KEEP GOING in this phase (see :meth:`backfill`)."""
        try:
            indexed, scanned, tail = self._run_phase(phase, 1, when, last, force)
        except Exception:  # noqa: BLE001 — the very first row is the poison one
            log.debug("backfill batch=1 retry still failed in phase %r", phase, exc_info=True)
        else:
            if scanned:
                # The single row was fine, so the poison is later in the page.
                # Crawling one row per pass is slow but STRICTLY progressing,
                # and it converges on the bad row within one page.
                return {
                    "indexed": indexed,
                    "scanned": scanned,
                    "cursor": f"{phase}|{tail}",
                    "done": False,
                    "error": True,
                }
            return self._advance_phase(phase, error=True)
        head = self._poison_head(phase, when, last)
        if head is None:
            log.warning(
                "search backfill phase %r cannot be listed at all; skipping to the next", phase
            )
            return self._advance_phase(phase, error=True)
        stamp, row_id = head
        log.warning(
            "search backfill skipping unindexable %s row %s; continuing the phase",
            phase,
            row_id,
        )
        return {
            "indexed": 0,
            "scanned": 1,
            "cursor": f"{phase}|{stamp}|{row_id}",
            "done": False,
            "error": True,
        }

    def _backfill(self, batch: int, cursor: str | None, force: bool) -> dict[str, Any]:
        batch = max(1, min(1000, int(batch or 200)))
        phase, when, last = self._parse_cursor(cursor)
        try:
            rows = self._run_phase(phase, batch, when, last, force)
        except Exception:  # noqa: BLE001 — one bad PAGE must not starve its phase
            # This used to skip straight to the NEXT phase. That looked safe
            # (the whole backfill no longer parked) and was not: the phase guard
            # fires again from the same cursor on every future sweep, so every
            # row AFTER the bad one was abandoned forever — for the chat phase,
            # that is the user's whole chat history, silently missing from
            # search. Isolate the row instead.
            log.exception("search backfill page failed in phase %r; isolating it", phase)
            return self._isolate_page(phase, when, last, force)
        indexed, scanned, tail = rows
        if scanned == 0:
            return self._advance_phase(phase)
        return {"indexed": indexed, "scanned": scanned, "cursor": f"{phase}|{tail}", "done": False}

    @staticmethod
    def _tail_cursor(row: Any) -> str:
        created = getattr(row, "created_at", None)
        stamp = created.isoformat() if isinstance(created, datetime) else ""
        return f"{stamp}|{getattr(row, 'id', '')}"

    def _backfill_chat(
        self, batch: int, when: datetime | None, last: str, force: bool
    ) -> tuple[int, int, str]:
        from ..core.models import ChatThreadRecord  # local import: avoids cycles

        rows = self._keyset(ChatThreadRecord, when, last, batch)
        if not rows:
            return 0, 0, ""
        known: set[str] = set()
        if not force:
            known = self._already_indexed(
                "thread_id", [r.id for r in rows], ["chat", "comm"]
            )
        indexed = 0
        for rec in rows:
            if rec.id in known:
                continue
            kind = "comm" if (rec.owner == "daemon" or rec.comm_channel) else "chat"
            try:
                msgs = json.loads(rec.messages_json or "[]")
            except (TypeError, ValueError):
                msgs = []
            if not isinstance(msgs, list):
                msgs = []
            for m in msgs:
                if isinstance(m, dict) and not m.get("at"):
                    m["at"] = (rec.updated_at or rec.created_at)
            indexed += self.sync_thread(
                rec.id, kind, rec.title or "", rec.project_id or "", msgs
            )
        return indexed, len(rows), self._tail_cursor(rows[-1])

    def _backfill_round(
        self, batch: int, when: datetime | None, last: str, force: bool
    ) -> tuple[int, int, str]:
        try:
            # Local import: agents/threads.py is another pair's file and will
            # import THIS module for its live sync — importing it at module
            # level would close the cycle.
            from ..agents.threads import AgentThreadRecord

            rows = self._keyset(AgentThreadRecord, when, last, batch)
        except Exception:  # noqa: BLE001 — no round tables in this DB: skip the phase
            log.debug("round-table backfill skipped", exc_info=True)
            return 0, 0, ""
        if not rows:
            return 0, 0, ""
        known: set[str] = set()
        if not force:
            known = self._already_indexed("thread_id", [r.id for r in rows], ["round"])
        indexed = 0
        for rec in rows:
            if rec.id in known:
                continue
            try:
                msgs = json.loads(rec.messages_json or "[]")
            except (TypeError, ValueError):
                msgs = []
            if not isinstance(msgs, list):
                msgs = []
            for m in msgs:
                if isinstance(m, dict) and not m.get("at"):
                    m["at"] = (rec.updated_at or rec.created_at)
            indexed += self.sync_thread(rec.id, "round", rec.title or "", "", msgs)
        return indexed, len(rows), self._tail_cursor(rows[-1])

    def _backfill_sessions(
        self, batch: int, when: datetime | None, last: str, force: bool
    ) -> tuple[int, int, str]:
        from ..core.models import Session  # local import: avoids cycles

        rows = self._keyset(Session, when, last, batch)
        if not rows:
            return 0, 0, ""
        known: set[str] = set()
        if not force:
            known = self._already_indexed("ref", [r.id for r in rows], ["session"])
        indexed = 0
        for rec in rows:
            if rec.id in known:
                continue
            indexed += self.sync_session(rec)
        return indexed, len(rows), self._tail_cursor(rows[-1])
