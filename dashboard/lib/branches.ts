/**
 * BRANCHES — keep every version of a conversation (v1.325.0; the idea is
 * assistant-ui's branch picker, MIT — no code taken).
 *
 * Editing a question or asking for another answer used to THROW AWAY what
 * came after. Now the old ending is kept and the user can flip between
 * versions ("‹ 2 / 3 ›").
 *
 * Shape. A fork lives on the FIRST message of the live tail:
 *
 *   messages[index].branch = { tails: (M[] | null)[] }
 *
 * `null` marks the slot of the tail that is live right now (messages from
 * `index` on); every other entry is a whole alternative tail, stored as it
 * was. The first message of a stored tail carries no `branch` (its set is the
 * one being described); forks DEEPER inside a stored tail stay on their own
 * messages, so switching back brings them back too.
 *
 * Everything here is pure and returns new arrays — never mutates its input.
 * The thread is saved verbatim by the daemon, so the nested field persists;
 * the request path sends only role/content (toRequestMessages), so stored
 * versions never reach the model.
 *
 * Lives in lib/ because a Next page module may only export its route's own
 * symbols.
 */

/** At most this many versions per fork; past it the OLDEST stored one goes. */
export const MAX_BRANCHES = 10;

/** The versions kept at one fork point. Exactly one entry is `null` (live). */
export interface BranchSet<M> {
  tails: (M[] | null)[];
}

/** Anything that can carry a fork. */
export type Branchable<M> = { branch?: BranchSet<M> };

/** A well-formed set: ≥ 2 slots, exactly one live (`null`), every stored
 *  tail a non-empty array. Anything else (an old or hand-edited thread) is
 *  treated as "no fork" rather than trusted. */
function validSet<M>(b: unknown): BranchSet<M> | null {
  if (!b || typeof b !== "object") return null;
  const tails = (b as { tails?: unknown }).tails;
  if (!Array.isArray(tails) || tails.length < 2) return null;
  let live = 0;
  for (const t of tails) {
    if (t === null) live += 1;
    else if (!Array.isArray(t) || t.length === 0) return null;
  }
  return live === 1 ? (b as BranchSet<M>) : null;
}

/** The message without its `branch` field (a shallow copy; nothing else
 *  changes). Returns the same object when there is no field to drop. */
export function stripBranches<M extends Branchable<M>>(m: M): M {
  if (!m || typeof m !== "object" || !("branch" in m)) return m;
  const { branch: _drop, ...rest } = m;
  void _drop;
  return rest as M;
}

/** Where this message's fork stands: `pos` is the live version (0-based),
 *  `count` how many versions there are. Null when the message has no fork. */
export function branchInfo<M extends Branchable<M>>(
  m: M | null | undefined,
): { pos: number; count: number } | null {
  const set = validSet<M>(m?.branch);
  if (!set) return null;
  return { pos: set.tails.indexOf(null), count: set.tails.length };
}

function isIndex(n: number, max: number): boolean {
  return Number.isInteger(n) && n >= 0 && n <= max;
}

/**
 * Start a new version at `index`: the current tail (`messages.slice(index)`)
 * is kept as a stored version, `newTail` becomes live and is numbered LAST
 * (so the picker reads "n / n"). A fork already at that message is extended.
 * Past MAX_BRANCHES the OLDEST stored version is dropped — never the one
 * that was live until now (`dropped: true` — say so to the user rather than
 * lose it silently).
 *
 * - `index === messages.length` (nothing to keep) → a plain append, no fork.
 * - an empty `newTail` or a bad index → the input, unchanged.
 * - `newTail[0]`'s own `branch` (if any) is replaced by the fork's.
 *
 * Edit: `forkTail(before, i, [editedQuestion])` before the turn runs.
 * Another answer: `forkTail(before, last, [newReply])` once it lands.
 */
export function forkTail<M extends Branchable<M>>(
  messages: M[],
  index: number,
  newTail: M[],
): { messages: M[]; dropped: boolean } {
  if (!isIndex(index, messages.length) || newTail.length === 0) {
    return { messages, dropped: false };
  }
  if (index === messages.length) {
    return { messages: [...messages, ...newTail], dropped: false };
  }
  const head = messages[index];
  const stored = [stripBranches(head), ...messages.slice(index + 1)];
  const existing = validSet<M>(head.branch);
  const tails: (M[] | null)[] = existing
    ? existing.tails.map((t) => (t === null ? stored : t))
    : [stored];
  tails.push(null);
  // The version that was on screen a moment ago is never the one dropped —
  // the user was looking at it (they may have switched back to the first
  // version and asked again from there); the oldest OTHER one goes.
  let dropped = false;
  while (tails.length > MAX_BRANCHES) {
    const oldest = tails.findIndex((t) => t !== null && t !== stored);
    tails.splice(oldest, 1);
    dropped = true;
  }
  const first = { ...newTail[0], branch: { tails } } as M;
  return {
    messages: [...messages.slice(0, index), first, ...newTail.slice(1)],
    dropped,
  };
}

/**
 * The fork step for a turn that has ALREADY produced its messages: `before`
 * is the conversation as it was before the edit / "try again", `after` is
 * what the turn produced (the same prefix up to `index`, then the new tail).
 * The prefix is taken from `after` (it may carry fresh fields), the stored
 * tail from `before`.
 *
 * While the new tail does not exist yet (`after.length <= index`, e.g. the
 * save at turn start) → `after`, unchanged.
 */
export function forkOnto<M extends Branchable<M>>(
  before: M[],
  index: number,
  after: M[],
): { messages: M[]; dropped: boolean } {
  if (!isIndex(index, before.length) || after.length <= index) {
    return { messages: after, dropped: false };
  }
  return forkTail(
    [...after.slice(0, index), ...before.slice(index)],
    index,
    after.slice(index),
  );
}

/**
 * Show version `to` (0-based over the whole set) of the fork at `index`: the
 * live tail is stored in its slot and version `to` becomes live. Switching
 * away and back gives back the identical conversation. Bad input (no fork
 * there, `to` out of range, `to` already live) → the SAME array, so a caller
 * can skip the save with `next === messages`.
 */
export function switchBranch<M extends Branchable<M>>(
  messages: M[],
  index: number,
  to: number,
): M[] {
  if (!Number.isInteger(index) || index < 0 || index >= messages.length) return messages;
  const head = messages[index];
  const set = validSet<M>(head.branch);
  if (!set || !Number.isInteger(to) || to < 0 || to >= set.tails.length) return messages;
  const pos = set.tails.indexOf(null);
  if (to === pos) return messages;
  const target = set.tails[to] as M[];
  const tails = set.tails.slice();
  tails[pos] = [stripBranches(head), ...messages.slice(index + 1)];
  tails[to] = null;
  const first = { ...stripBranches(target[0]), branch: { tails } } as M;
  return [...messages.slice(0, index), first, ...target.slice(1)];
}
