"""Long-term memory manager (§21).

``LongTermMemory`` registers LTM connectors and routes search/append either to a
single named source or — for search — across every registered connector, merging
results round-robin so each store is fairly represented.

QUERY DECOMPOSITION (v1.173.0)
------------------------------
Search is DELEGATED to whatever the store can do, and several stores match
LITERALLY. Measured against the user's live MCP-served wiki (a real brain that
several agents share):

===========================  ==================================================
query                        hits
===========================  ==================================================
``s-corp``                   4 (including ``comparisons/s-corp-vs-llc``)
``llc``                      4
``comparisons``              2
``s-corp vs llc``            **0**
``scorp llc comparison``     **0**
===========================  ==================================================

Single terms work; the natural multi-word QUESTION returns nothing — so an
agent asking the obvious thing gets silence from a brain that holds the answer.
That is not "no such note", it is a degraded retrieval wearing the same face,
which is exactly the failure this project refuses to ship.

The fix lives HERE, in the one place every connector's results pass through, so
MCP, Notion, cloud, http_rag and local markdown all inherit it and no connector
grows a special case. The rules (see :meth:`LongTermMemory._expanded_search`):

1. The query AS GIVEN runs first, always. A server that handles the whole
   phrase keeps winning and pays nothing — a working search is never degraded.
2. Only when that pass returns FEWER THAN ``k`` hits, and the query carries at
   least two significant terms, do decomposed passes run.
3. Hits merge and de-duplicate by ``(source, ref)``; a hit credited with MORE
   of the query's terms ranks above a single-term hit, and a full-query hit
   always outranks a fallback hit that covered the same terms.
4. Every fallback hit is MARKED (``match="partial"``) — a caller (and the model
   reading the block) can tell "found by decomposing your question" from "found
   exactly", instead of both looking equally authoritative.
5. A fallback pass that raises is swallowed: the extra passes are a bonus and
   must never cost the primary result.

Three rules exist because the FIRST cut of this got them wrong, measurably:

* **The topic noun must never lose a pass to the words that ASK for it.**
  Ranking the terms by length made ``look into the history of the llc`` spend
  its three passes on ``history``, ``look`` and ``into`` and never ask ``llc``
  at all — the exact silence this module exists to end, reproduced through the
  cure. Length is not specificity once the calling vocabulary (v1.173.0 §P3)
  is made of long words, so :data:`_CALLING_WORDS` DEMOTES them: they still
  count as significant, they still get passes when the query holds nothing
  else, but a term that names the topic is always asked first.
* **A variant is a guess, so it is credited only with what it can prove.**
  A whole-query rewrite that returns a hit says nothing about WHICH terms
  matched; crediting it with all of them put unverified hits at the top of the
  ranking wearing a ``matched_terms`` list nothing checked. Fallback credit is
  now verified against the hit's own title/snippet/ref — ``matched_terms``
  never names a term that cannot be found in the text.
* **A whole-sentence slug is not a rewrite, it is a coinage.**
  ``look-into-the-history-of-the-llc`` is a string nobody typed and no store
  holds. Only a SLUG-SHAPED query (:data:`_MAX_SLUG_TOKENS` words or fewer,
  the case that was measured to work) is worth spending a pass on.

Bounds, because this runs inside a chat turn (and today's chat lane calls it on
the event loop): at most :data:`_MAX_FALLBACK_TERMS` term passes plus the
whole-query hyphen/space variants, at most ``k * 3`` results considered, and a
wall-clock ceiling (:data:`_FALLBACK_BUDGET_S`, measured from BEFORE the
primary — the primary's own latency is the only honest estimate of what each
extra round trip will cost). Callers that loop this over several bases or
sources pass ONE :func:`shared_deadline` so the loop spends a single ceiling
between them instead of one apiece. The FIRST fallback pass of each call always
runs regardless of that ceiling: a slow base is a reason to stop stacking round
trips, never a reason to answer "nothing found" about a note that exists.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .base import LTMConnector

log = logging.getLogger(__name__)

#: Punctuation shaved off a term's EDGES only. Interior characters survive on
#: purpose: ``s-corp`` must stay ``s-corp``. Squashing it to ``scorp`` would
#: invent a spelling that appears nowhere in the vault and hand a literal
#: matcher a term guaranteed to miss.
_EDGE_PUNCT = re.compile(r"^[^\w]+|[^\w]+$")

#: Shortest term worth a pass of its own. Two-letter fragments ("of", "vs",
#: "id") match everywhere and rank nothing.
_MIN_TERM_LEN = 3

#: DELIBERATELY TINY. An over-eager stopword list is its own bug: drop "tax"
#: or "return" as "common" and the firm's most-searched nouns stop being
#: searchable. Only words that are pure connective tissue in a question are
#: here — nothing domain-specific, nothing longer than a function word.
_STOPWORDS = frozenset(
    {
        "vs", "the", "a", "an", "and", "or", "for", "of", "to", "in", "into",
        "on", "about", "what", "is", "are", "our", "my", "your",
    }
)

#: The words that name the ACT of asking, not the thing asked about — v1.173.0
#: §P3's general calling vocabulary ("search your memory", "look into the
#: history", "what do we have on", "check your notes", "dig up") plus the
#: pronouns/auxiliaries a request is wrapped in. A CLOSED list, taken from the
#: plan; it is not an open-ended stopword grab and must not grow into one.
#:
#: These are DEMOTED, not dropped. They stay significant (so a query made of
#: them still decomposes, and "the history of the S-corp election" still counts
#: its words), but they never take a pass away from a term that names the
#: topic: :func:`_ranked_pass_terms` puts topic terms first and falls back to
#: these only when the query holds nothing else. The failure this prevents was
#: measured through the fix itself — "look into the history of the llc" spent
#: all three of its passes on `history`, `look`, `into` and never asked `llc`,
#: while `llc` alone returned the note.
_CALLING_WORDS = frozenset(
    {
        "search", "searches", "find", "look", "looking", "check", "checking",
        "pull", "dig", "recall", "remember", "tell", "know", "knows", "have",
        "has", "give", "show", "need", "want", "please", "can", "you",
        "anything", "everything", "memory", "memories", "note", "notes",
        "history", "knowledge",
    }
)

#: At most this many single-term passes, most specific term first — and
#: "specific" means a TOPIC term (see :data:`_CALLING_WORDS`), longest first
#: within that pool.
_MAX_FALLBACK_TERMS = 3

#: A whole-query space→hyphen rewrite is only tried for a query this short.
#: A slug is what a path-matching store holds ("comparisons/s-corp-vs-llc"), so
#: `s-corp vs llc` → `s-corp-vs-llc` is a real probe; a whole SENTENCE welded
#: into `look-into-the-history-of-the-llc` is a coinage that cannot match a
#: literal store and only displaces a pass that could have.
_MAX_SLUG_TOKENS = 4

#: Total results considered across all passes, as a multiple of ``k``.
_CONSIDER_MULTIPLIER = 3

#: Wall-clock ceiling for the WHOLE search (primary included) past which no
#: FURTHER fallback pass is started. Module-level so a caller/test can retune
#: it; read at call time.
_FALLBACK_BUDGET_S = 2.5

#: Fallback hits carry this marker so nothing downstream mistakes a decomposed
#: match for an exact one (``memory.fabric._notes`` damps their rank with it).
PARTIAL_MATCH = "partial"


def significant_terms(query: str) -> list[str]:
    """The query's searchable terms, in the order typed, case PRESERVED.

    Case is preserved because the matching happens on the far side of a remote
    tool we do not control — lower-casing a term would be us guessing that the
    server is case-insensitive. Duplicates are dropped case-INsensitively.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in (query or "").split():
        term = _EDGE_PUNCT.sub("", raw)
        low = term.lower()
        if len(low) < _MIN_TERM_LEN or low in _STOPWORDS or low in seen:
            continue
        seen.add(low)
        out.append(term)
    return out


def query_variants(query: str) -> list[str]:
    """Whole-query hyphen/space rewrites — ``s-corp vs llc`` becomes both
    ``s corp vs llc`` and ``s-corp-vs-llc`` (the shape of a slug/filename, which
    is what a path-matching store actually holds).

    Rewrites only; no invented spellings. ``scorp`` is NOT generated — a term
    the user never typed and the vault never used would only add latency.

    The hyphen→space direction is unconditional (it is a rewrite of something
    the user actually typed). The space→hyphen direction is gated on
    :data:`_MAX_SLUG_TOKENS`: welding a natural-language QUESTION into
    ``what-do-we-have-on-reasonable-compensation-rules`` produces a string no
    store holds and no user typed — guaranteed zero on a literal matcher, and
    on an always-answering one it burns a slot of the ``k*3`` ceiling that a
    real term pass needed.
    """
    flat = " ".join((query or "").split())
    out: list[str] = []
    if "-" in flat:
        out.append(flat.replace("-", " "))
    tokens = flat.split()
    if len(tokens) > 1 and len(tokens) <= _MAX_SLUG_TOKENS:
        out.append(flat.replace(" ", "-"))
    return out


def _ranked_pass_terms(terms: list[str]) -> list[str]:
    """*terms* worth a round trip of their own, best first.

    TOPIC terms outrank the calling vocabulary absolutely (see
    :data:`_CALLING_WORDS`); within each pool the longest goes first, which is
    the pass measured to work against a literal matcher (``s-corp`` → 4 hits
    where ``s-corp vs llc`` → 0). When the query is ALL calling words ("check
    your notes"), those words are all there is to ask, so they are used — a
    query is never left with nothing to decompose into.
    """
    topic = [t for t in terms if t.lower() not in _CALLING_WORDS]
    pool = topic or list(terms)
    return sorted(pool, key=len, reverse=True)  # stable: ties keep typed order


def shared_deadline(budget_s: float | None = None) -> float:
    """One wall-clock ceiling that several :meth:`LongTermMemory.search` calls
    can spend BETWEEN them (a monotonic timestamp — compare, never subtract
    from wall time).

    :data:`_FALLBACK_BUDGET_S` bounds ONE call. Callers that loop search over
    several bases (``memory.fabric._notes`` per bound base, the chat lane per
    toggled memory source) would otherwise start a fresh ceiling per iteration
    and multiply it by the number of bases, which is exactly the shape of the
    "nothing blocking on the event loop" rule this project keeps re-learning.
    Build one deadline before the loop and hand it to every call.

    Each call still runs its first fallback pass even past the deadline: that
    floor is per BASE on purpose — a slow first base must not silently turn the
    second base's held note into "no such note".
    """
    return time.monotonic() + (
        _FALLBACK_BUDGET_S if budget_s is None else float(budget_s)
    )


def _hit_text(hit: dict[str, Any]) -> str:
    """The searchable text a hit actually carries, lower-cased — the only
    evidence available locally for whether a term really matched."""
    return " ".join(
        str(hit.get(field_, "") or "") for field_ in ("title", "snippet", "ref")
    ).lower()


def _score_of(hit: dict[str, Any]) -> float:
    """A connector-supplied score, or 0.0. Most connectors supply none — the
    value is only ever used to pick WHICH duplicate copy to keep."""
    try:
        return float(hit.get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class _Merged:
    """One de-duplicated hit plus the evidence used to rank it."""

    hit: dict[str, Any]
    order: int
    terms: set[str] = field(default_factory=set)
    primary: bool = False
    first_pass: int = 0
    best_rank: int = 0
    score: float = 0.0

#: Bumped on every successful append (v1.146.1). Read by
#: ``memory.index_block._local_notes`` to invalidate its folder-scan cache.
#:
#: WHY A COUNTER AND NOT THE FOLDER'S mtime (which is what the cache used to
#: rely on): appending writes a file, and a new file was assumed to move the
#: directory's mtime, making the note visible on the very next turn. On NTFS it
#: does not reliably — Windows stamps timestamps off the system clock (~15.6ms
#: tick) and updates directory metadata lazily, so two appends inside one tick
#: produce a byte-identical ``st_mtime``. The cache then read "nothing changed"
#: and served the pre-append scan: "remember this" followed by a question about
#: it showed the OLD note count and titles for up to the 60s TTL. Measured 9
#: failures in 24 runs of the regression test on Windows.
#:
#: The counter moves because WE WROTE, which no filesystem timestamp
#: granularity can defeat. It is process-local and deliberately global: an
#: append is rare next to a turn, so busting every base's cached scan costs one
#: redundant glob, and per-source bookkeeping is not worth it. Writes made
#: OUTSIDE the app (editing the vault in Obsidian, a sync client) do not bump
#: it and keep falling back to mtime + TTL exactly as before — the cache now
#: busts when the mtime moved OR when we appended.
_APPEND_EPOCH = 0


def append_epoch() -> int:
    """How many appends this process has made. Only ever compared for equality
    — callers cache it alongside a scan and re-scan when it has moved."""
    return _APPEND_EPOCH


class LongTermMemory:
    """Front door to all registered long-term-memory connectors."""

    def __init__(self) -> None:
        self._connectors: dict[str, LTMConnector] = {}

    def register(self, connector: LTMConnector) -> LTMConnector:
        if not getattr(connector, "name", ""):
            raise ValueError("LTM connector must have a name")
        self._connectors[connector.name] = connector
        return connector

    def deregister(self, name: str) -> bool:
        """Remove a registered connector (a deleted custom source takes effect
        LIVE, not on the next restart). False when no such source."""
        return self._connectors.pop(name, None) is not None

    def connectors(self) -> list[LTMConnector]:
        return list(self._connectors.values())

    def sources(self) -> list[str]:
        return list(self._connectors)

    def get(self, source: str) -> LTMConnector | None:
        return self._connectors.get(source)

    def default_source(self) -> str | None:
        """The store appends route to when none is named — ``brain`` if present."""
        if "brain" in self._connectors:
            return "brain"
        return next(iter(self._connectors), None)

    def search(
        self,
        query: str,
        k: int = 5,
        source: str | None = None,
        *,
        expand: bool = True,
        deadline: float | None = None,
    ) -> list[dict[str, Any]]:
        """Up to *k* hits from one named source, or merged across every source.

        ``expand`` (v1.173.0, default on) allows the decomposed fallback passes
        described in the module docstring when the query as given comes back
        thin. Pass ``expand=False`` for a strictly literal search — the exact
        pre-v1.173.0 behaviour, byte for byte.

        ``deadline`` (v1.173.0) is a monotonic timestamp from
        :func:`shared_deadline`: a caller looping this over several bases hands
        the SAME deadline to every call so the loop spends one wall-clock
        ceiling in total rather than one per base. Omitted, the call gets its
        own :data:`_FALLBACK_BUDGET_S` — unchanged behaviour.

        A connector error still propagates on the ``source=`` path (callers
        rely on being told which base is broken); only FALLBACK passes are
        swallowed.
        """
        if source is not None:
            conn = self._connectors.get(source)
            if conn is None:
                raise ValueError(f"unknown LTM source '{source}'")
            if not expand:
                return conn.search(query, k=k)
            return self._expanded_search(
                lambda q, n: conn.search(q, k=n), query, k, deadline=deadline
            )
        if not expand:
            return self._merge_search(query, k)
        return self._expanded_search(
            lambda q, n: self._merge_search(q, n), query, k, deadline=deadline
        )

    def append(self, title: str, content: str, source: str) -> str:
        conn = self._connectors.get(source)
        if conn is None:
            raise ValueError(f"unknown LTM source '{source}'")
        ref = conn.append(title, content)
        # AFTER the write succeeded — a failed append changed nothing, and
        # bumping anyway would throw away a valid cached scan for free. This is
        # the ONLY place in the tree that calls a connector's append (verified
        # by grep), so every path — routes, tools, the CLI, the importers —
        # rides it. See :data:`_APPEND_EPOCH` for why this exists.
        global _APPEND_EPOCH
        _APPEND_EPOCH += 1
        return ref

    # -- internals --------------------------------------------------------
    def _expanded_search(
        self,
        run: Callable[[str, int], list[dict[str, Any]]],
        query: str,
        k: int,
        *,
        deadline: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run *run* for the query as given, then — only if that came back thin
        — for decomposed variants, and merge the lot.

        *run* is one PASS over whatever the caller scoped: a single connector,
        or the round-robin merge across all of them. Keeping it a callable is
        what makes this work identically for ``search(source=...)`` (the path
        chat's toggled-connector block and every project-bound base take) and
        for the merged path, with one implementation.
        """
        started = time.monotonic()
        if deadline is None:
            deadline = started + _FALLBACK_BUDGET_S
        primary = run(query, k) or []
        terms = significant_terms(query)
        # THE FAST PATH IS THE OLD PATH. A search that already answered, or a
        # query with nothing to decompose, returns the connector's own list
        # untouched — same objects, same order, no marker keys.
        if k <= 0 or len(primary) >= k or len(terms) < 2:
            return primary

        entries: dict[tuple[str, str], _Merged] = {}
        self._absorb(entries, primary, credited=terms, primary_pass=True, pass_idx=0)

        considered = len(primary)
        ceiling = k * _CONSIDER_MULTIPLIER
        ran = 0
        for pass_idx, (pass_query, credited) in enumerate(
            self._fallback_plan(query, terms), start=1
        ):
            if considered >= ceiling:
                break
            # The FIRST extra pass always runs, however slow the base is: a
            # base that answers slowly is a reason to stop stacking round
            # trips, never a reason to report "no such note". Every pass after
            # it must fit the wall clock — which may be shared with the other
            # bases of a caller's loop (see :func:`shared_deadline`).
            if ran and time.monotonic() >= deadline:
                break
            ran += 1
            try:
                # Ask for no more than the ceiling still allows, so "k*3
                # results considered" is a real bound and not a rounding.
                hits = run(pass_query, min(k, ceiling - considered)) or []
            except Exception as exc:  # noqa: BLE001 — a bonus pass, never fatal
                log.debug("LTM fallback pass %r failed: %s", pass_query, exc)
                continue
            # verify=True: a fallback pass only gets credit for terms the hit
            # it returned can be SEEN to contain. A rewrite pass says nothing
            # about which terms matched, and a semantic store may answer a term
            # pass with a note that never uses the word — crediting either with
            # the whole query would put an unproven claim at the top of the
            # ranking wearing a ``matched_terms`` list nothing checked.
            self._absorb(
                entries,
                hits,
                credited=credited,
                primary_pass=False,
                pass_idx=pass_idx,
                verify=True,
            )
            considered += len(hits)

        # RANK: how much of the question a hit answers comes first; only then
        # which pass found it. ``first_pass`` is what makes a full-query hit
        # outrank a rewrite at equal term count — the query as given IS pass 0,
        # so no separate "primary wins" term is needed (a second, redundant
        # tie-break would only be one more thing to keep in sync).
        ordered = sorted(
            entries.values(),
            key=lambda e: (-len(e.terms), e.first_pass, e.best_rank, e.order),
        )
        out: list[dict[str, Any]] = []
        for entry in ordered[:k]:
            if entry.primary:
                out.append(entry.hit)
                continue
            marked = dict(entry.hit)
            marked["match"] = PARTIAL_MATCH
            marked["matched_terms"] = [t for t in terms if t in entry.terms]
            out.append(marked)
        return out

    @staticmethod
    def _fallback_plan(
        query: str, terms: list[str]
    ) -> list[tuple[str, tuple[str, ...]]]:
        """The extra passes to try, in order, as ``(query, terms it covers)``.

        The most specific single term goes first because that is the pass
        MEASURED to work against a literal matcher (``s-corp`` → 4 hits where
        ``s-corp vs llc`` → 0). "Most specific" is decided by
        :func:`_ranked_pass_terms`: the topic before the words that ask for it,
        longest first inside the topic. Ranking by raw length instead is what
        made ``look into the history of the llc`` never ask ``llc``.

        The whole-query rewrites follow: speculative, and credited only with
        what the hits they return can be shown to contain. The remaining topic
        terms come last, capped at :data:`_MAX_FALLBACK_TERMS` passes in total.

        Never repeats a query string (a one-word rewrite of a one-word query,
        or a term identical to the query, would just pay for the same call).
        """
        ranked = _ranked_pass_terms(terms)
        # Variants are the WHOLE query, so they can carry any term — but only
        # the ones that carry the question: crediting a rewrite hit with
        # "have" or "check" would rank it on the words the user asked WITH.
        every = tuple(ranked)
        plan: list[tuple[str, tuple[str, ...]]] = []
        if ranked:
            plan.append((ranked[0], (ranked[0],)))
        plan += [(v, every) for v in query_variants(query)]
        plan += [(t, (t,)) for t in ranked[1:_MAX_FALLBACK_TERMS]]

        seen = {" ".join(query.split()).lower()}
        out: list[tuple[str, tuple[str, ...]]] = []
        for pass_query, credited in plan:
            key = " ".join(pass_query.split()).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append((pass_query, credited))
        return out

    @staticmethod
    def _absorb(
        entries: dict[tuple[str, str], _Merged],
        hits: list[dict[str, Any]],
        *,
        credited: "list[str] | tuple[str, ...]",
        primary_pass: bool,
        pass_idx: int,
        verify: bool = False,
    ) -> None:
        """Fold one pass's hits into *entries*, de-duplicating by (source, ref).

        A hit seen again in a later pass is not a new result — it is EVIDENCE
        that it answers more of the question, so its credited terms union and
        its best rank/pass survive.

        IDENTITY: ``ref`` when there is one. ``ref`` can legitimately be empty
        (a plain prose reply from an MCP server, a Notion page group) and such
        a hit has NO stable identity, so title alone must not be treated as
        one: two distinct answers under one repeated heading would collapse
        into the first-arrived copy and the better one would be dropped. The
        title plus a snippet fingerprint is the closest thing to an identity
        available.

        CREDIT: with ``verify``, a term is credited only if it appears in the
        hit's own title/snippet/ref. ``matched_terms`` is a CLAIM about the
        hit; a claim nothing checked is exactly the "degraded retrieval wearing
        the face of an exact one" this wave exists to prevent. The primary pass
        is not verified — the store was asked the user's whole question and its
        answer is the store's own claim, which we report unmarked as such.
        """
        for rank, hit in enumerate(hits):
            if not isinstance(hit, dict):
                continue
            ref = str(hit.get("ref") or "")
            if ref:
                ident = ref
            else:
                title = str(hit.get("title") or "")
                ident = f"{title}|{str(hit.get('snippet', ''))[:80]}"
            key = (str(hit.get("source", "")), ident)
            proven = set(credited)
            if verify:
                text = _hit_text(hit)
                proven = {t for t in proven if t.lower() in text}
            cur = entries.get(key)
            if cur is None:
                entries[key] = _Merged(
                    hit=hit,
                    order=len(entries),
                    terms=proven,
                    primary=primary_pass,
                    first_pass=pass_idx,
                    best_rank=rank,
                    score=_score_of(hit),
                )
                continue
            cur.terms |= proven
            cur.first_pass = min(cur.first_pass, pass_idx)
            cur.best_rank = min(cur.best_rank, rank)
            score = _score_of(hit)
            # Keep the best-scored copy of a duplicate — but only among copies
            # of the SAME class. ``primary`` describes the object we hand back
            # (it is emitted unmarked, as an exact match), so swallowing a
            # fallback pass's copy into a primary entry would hand the caller a
            # fallback body — different snippet window, different score —
            # presented as the exact hit. ``cur.primary`` is therefore also the
            # class of ``cur.hit``: the primary IS pass 0, so nothing can raise
            # it later.
            if score > cur.score and primary_pass == cur.primary:
                cur.hit = hit
                cur.score = score

    def _merge_search(self, query: str, k: int) -> list[dict[str, Any]]:
        per_source: list[list[dict[str, Any]]] = []
        for conn in self._connectors.values():
            try:
                per_source.append(conn.search(query, k=k))
            except Exception:  # one failing connector must not break the merge
                per_source.append([])
        merged: list[dict[str, Any]] = []
        rank = 0
        while len(merged) < k and any(rank < len(lst) for lst in per_source):
            for lst in per_source:
                if rank < len(lst):
                    merged.append(lst[rank])
                    if len(merged) >= k:
                        break
            rank += 1
        return merged
