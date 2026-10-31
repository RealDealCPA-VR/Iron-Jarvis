/**
 * v1.418.0 (fix plan C2.3): a coding reply as the ORDERED list of what it
 * did — [text, a run of steps, text, …] — the way Build › Code draws its
 * conversation (components/buildCode/CodeConversation's items), instead of
 * every step above all of the text.
 *
 * LIVE: the frames arrive in order (tokens and tool_call frames
 * interleaved), so each tool card is placed at the length the reply's text
 * had when the card first appeared (`liveReplyItems`).
 *
 * SETTLED: the daemon keeps only the reply's LAST round of words as its
 * content (`done.reply`; the narration of earlier rounds is not saved) and
 * every step with its start. So a settled reply is placed from what this
 * window saw while it ran (`keepReplyShape`, keyed by the turn; bounded):
 * the earlier rounds' words, then each run of saved steps (by count), then
 * the saved content. A reply this window did not watch (a reload, another
 * window) has every step BEFORE its content — which is true: the words kept
 * are the last round's, written after the last step.
 *
 * The finish check's own steps (`check: "finish"`)
 * are not the reply's work and stay out of the runs (its "Checked" line says
 * them); a step that started a helper (`helper`) is drawn by the helper's own
 * row. Pure, except the small module memory below.
 */

export type ReplyItem<S> = { kind: "text"; text: string } | { kind: "steps"; steps: S[] };

/** Where a run of steps sat in the live text: its offset and how many. */
export interface ReplyCut {
  offset: number;
  count: number;
}

/** What this window saw of a reply while it ran. */
export interface ReplyShape {
  text: string;
  cuts: ReplyCut[];
}

/** How many replies' shapes are kept (this window's life, the newest). */
export const MAX_REPLY_SHAPES = 32;

const shapes = new Map<string, ReplyShape>();

/** Remember what a running reply looked like (called while it runs). */
export function keepReplyShape(turnId: string, shape: ReplyShape): void {
  if (!turnId) return;
  shapes.delete(turnId);
  shapes.set(turnId, shape);
  while (shapes.size > MAX_REPLY_SHAPES) {
    const oldest = shapes.keys().next().value;
    if (oldest === undefined) break;
    shapes.delete(oldest);
  }
}

export function replyShapeFor(turnId: string | null | undefined): ReplyShape | null {
  return turnId ? (shapes.get(turnId) ?? null) : null;
}

/** Where each live card of a running reply first appeared (card id → the
 *  text's length then), kept per turn for this window's life so a session
 *  left and opened again mid-reply places its steps as before. */
const offsets = new Map<string, Map<string, number>>();

export function liveOffsetsFor(turnId: string): Map<string, number> {
  let m = offsets.get(turnId);
  if (!m) {
    m = new Map();
    offsets.set(turnId, m);
    while (offsets.size > MAX_REPLY_SHAPES) {
      const oldest = offsets.keys().next().value;
      if (oldest === undefined) break;
      offsets.delete(oldest);
    }
  }
  return m;
}

/** For tests: forget every shape. */
export function forgetReplyShapes(): void {
  shapes.clear();
  offsets.clear();
}

/**
 * The live reply in order: `text` split at the offset each card first
 * appeared at (`offsetOf`; clamped into the text, never backwards), cards
 * with the same offset in one run. Empty text between two runs is no item.
 */
export function liveReplyItems<C>(text: string, cards: readonly C[], offsetOf: (c: C) => number): ReplyItem<C>[] {
  const items: ReplyItem<C>[] = [];
  let prev = 0;
  let run: C[] | null = null;
  for (const c of cards) {
    const raw = offsetOf(c);
    const at = Math.max(prev, Math.min(text.length, Number.isFinite(raw) ? raw : text.length));
    if (run && at === prev) {
      run.push(c);
      continue;
    }
    const seg = text.slice(prev, at);
    if (seg.trim()) items.push({ kind: "text", text: seg });
    run = [c];
    items.push({ kind: "steps", steps: run });
    prev = at;
  }
  const tail = text.slice(prev);
  if (tail.trim()) items.push({ kind: "text", text: tail });
  return items;
}

/** The cuts of a live list (what `keepReplyShape` keeps). */
export function cutsOf<C>(text: string, items: readonly ReplyItem<C>[]): ReplyCut[] {
  const cuts: ReplyCut[] = [];
  let at = 0;
  for (const it of items) {
    if (it.kind === "text") at += it.text.length;
    else cuts.push({ offset: Math.min(at, text.length), count: it.steps.length });
  }
  return cuts;
}

function isRec(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

/** The saved steps that are the reply's own work (see the module note). */
export function replyWorkSteps(raw: unknown): Record<string, unknown>[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter(
    (st): st is Record<string, unknown> =>
      isRec(st) && typeof st.name === "string" && !!st.name.trim() && st.check !== "finish" && st.helper !== true,
  );
}

/**
 * A settled reply in order (see the module note). `shape` is what this
 * window saw while it ran (null: not watched here) — used only when its runs
 * fit inside the saved steps; anything it does not place joins the last run.
 */
export function settledReplyItems(
  content: string,
  rawSteps: unknown,
  shape: ReplyShape | null,
): ReplyItem<Record<string, unknown>>[] {
  const steps = replyWorkSteps(rawSteps);
  const items: ReplyItem<Record<string, unknown>>[] = [];
  if (!steps.length) {
    if (content) items.push({ kind: "text", text: content });
    return items;
  }
  const placed = shape ? shape.cuts.reduce((n, c) => n + c.count, 0) : 0;
  if (shape && shape.cuts.length && placed <= steps.length) {
    let prev = 0;
    let idx = 0;
    let last: Record<string, unknown>[] | null = null;
    for (const cut of shape.cuts) {
      const seg = shape.text.slice(prev, Math.max(prev, cut.offset));
      // Review: words the saved content already holds (the approved plan
      // put before the reply, a closing offer's answer combined with what
      // came after) are drawn once — there, in the content.
      if (seg.trim() && !content.includes(seg.trim())) items.push({ kind: "text", text: seg });
      last = steps.slice(idx, idx + cut.count);
      items.push({ kind: "steps", steps: last });
      idx += cut.count;
      prev = Math.max(prev, cut.offset);
    }
    if (idx < steps.length && last) last.push(...steps.slice(idx));
  } else {
    items.push({ kind: "steps", steps });
  }
  if (content) items.push({ kind: "text", text: content });
  return items;
}
