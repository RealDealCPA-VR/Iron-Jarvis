/** Ranking brain for the v1.111.0 global search / command palette.
 *
 * THE NEED: "I know the thing exists — I just don't know which page it's on."
 * Iron Jarvis is 39 routes deep, and the way you reach a skill, a project, a
 * chat thread and a settings toggle is four different navigations. One box that
 * takes what you'd say out loud ("rename endpoint", "usage") and puts the right
 * row first is the whole feature; the ordering below IS the feature.
 *
 * Kept pure — no React, no fetch — so every ranking rule can be pinned by a
 * unit test instead of being re-discovered by hand in the running app.
 */
export interface PaletteItem {
  id: string;
  /** "history" (v1.142.0) is a hit from the daemon's full-text index over past
   *  conversations. It is named here because the palette's row type is shared,
   *  NOT because it is ranked here: history rows are fetched already ranked by
   *  the index and are merged into the list as their own segment. They are
   *  never passed to scorePalette — its AND-substring matcher knows nothing
   *  about message bodies and would simply throw the whole lane away. */
  kind: "page" | "action" | "skill" | "project" | "thread" | "history";
  label: string;
  blurb?: string;
  /** What the user might CALL it, when that isn't what we named it. */
  aliases?: string[];
  href?: string;
}

export interface ScoredPaletteItem extends PaletteItem {
  score: number;
}

/** Ties break toward what the user can act on immediately: a page is a
 *  destination they meant to go to; a thread is history they might have meant. */
const KIND_RANK: Record<PaletteItem["kind"], number> = {
  page: 0,
  action: 1,
  skill: 2,
  project: 3,
  thread: 4,
  // Unreachable in practice (nothing hands a history item to scorePalette) but
  // the map is total by type, and a rank is cheaper than a lie: if one ever
  // does arrive, it sorts last rather than tying with a page.
  history: 5,
};

// The gaps are wide (not 4/3/2/1) so that a strong hit on ONE word of a
// multi-word query can never be out-summed by a pile of weak hits.
const LABEL_PREFIX = 1000; // "us" -> "Usage": what you're typing IS the name
const LABEL_WORD = 800; // "fleet" -> "Local Fleet": you named a word of it
const ALIAS_HIT = 600; // "rename" -> an item that answers to "rename"
const SUBSTRING = 300; // buried mid-word in a label or alias
const BLURB = 100; // only the description knows about it — weakest signal

/** A character that a word can be buried INSIDE. Unicode-aware on purpose: an
 *  ASCII-only /[a-z0-9]/ counts "ü" as a word boundary, so "ller" would score
 *  against "Müller" as if the user had named a word of it. Matching is done
 *  with indexOf (never a RegExp built from the query), so "c++" and "a.b" are
 *  searched literally instead of throwing on every keystroke. */
const WORD_CHAR = /[\p{L}\p{N}]/u;

/** 3 = opens the text, 2 = opens a word inside it, 1 = buried, 0 = absent. */
function hit(text: string | undefined, word: string): 0 | 1 | 2 | 3 {
  if (!text) return 0;
  const t = text.toLowerCase();
  const first = t.indexOf(word);
  if (first < 0) return 0;
  if (first === 0) return 3;
  // Any LATER occurrence may still open a word ("chat" in "Rename chat"), so
  // scan them all before demoting to a buried substring.
  for (let i = first; i >= 0; i = t.indexOf(word, i + 1)) {
    if (!WORD_CHAR.test(t[i - 1])) return 2;
  }
  return 1;
}

/** Best evidence that this one query word refers to this item; 0 = no evidence. */
function wordScore(item: PaletteItem, word: string): number {
  const h = hit(item.label, word);
  if (h === 3) return LABEL_PREFIX;
  if (h === 2) return LABEL_WORD;
  let best = h === 1 ? SUBSTRING : 0;
  for (const alias of item.aliases ?? []) {
    const a = hit(alias, word);
    if (a >= 2) return ALIAS_HIT; // nothing left below can beat this
    if (a === 1) best = Math.max(best, SUBSTRING);
  }
  if (best) return best;
  return hit(item.blurb, word) ? BLURB : 0;
}

/**
 * Rank `items` against `query`, best first, unmatched dropped.
 *
 * Words are ANDed. OR-ing them feels generous and is unusable: on "rename
 * endpoint" every page whose blurb happens to say "endpoint" floods the list,
 * so typing a SECOND word — the one moment the user is being more specific —
 * makes the results worse. AND means each extra word can only narrow.
 */
export function scorePalette(
  query: string,
  items: PaletteItem[],
  limit = 12,
): ScoredPaletteItem[] {
  const words = query.toLowerCase().trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return []; // an empty box shows nothing, not everything
  const ranked: { item: PaletteItem; score: number; index: number }[] = [];
  items.forEach((item, index) => {
    let score = 0;
    for (const word of words) {
      const s = wordScore(item, word);
      if (s === 0) return; // one missing word disqualifies the item outright
      score += s;
    }
    ranked.push({ item, score, index });
  });
  ranked.sort(
    (a, b) =>
      b.score - a.score ||
      KIND_RANK[a.item.kind] - KIND_RANK[b.item.kind] ||
      a.index - b.index, // explicit: never rely on sort stability for this
  );
  // Clamp: a caller computing its own limit ("rows left on screen") can hand us
  // a negative, and slice(0, -1) would silently return everything EXCEPT the
  // worst match — the opposite of the "show fewer" that was asked for.
  return ranked
    .slice(0, Math.max(0, limit))
    .map(({ item, score }) => ({ ...item, score }));
}
