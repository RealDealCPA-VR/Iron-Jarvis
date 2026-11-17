/**
 * A chat turn the DAEMON runs (v1.336.0) is named by the page and can be
 * followed again after a reload. v1.339.0 lifts the two pieces the chat page
 * kept to itself so the Build pane's chat uses the same ones: the turn's name,
 * and the rule for whether the newest turn of a conversation is the one that
 * answers its last question.
 */

/** How far a turn's start may precede the question's `at` and still answer
 *  it: the page stamps `at` on its own clock, the daemon on its own. */
export const REJOIN_SKEW_MS = 10_000;

/** A name for the turn about to run, so it can be steered, stopped and
 *  followed again by name. Chosen here — the daemon mints none (v1.241.0). */
export function mintTurnId(): string {
  const rnd =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : Math.random().toString(36).slice(2);
  return `chat-${Date.now().toString(36)}-${rnd}`;
}

export interface TurnRow {
  turn_id: string;
  /** The record's own word: running, done, error, stopped, interrupted. */
  status?: string;
  running?: boolean;
  started_at?: string | null;
  thread_id?: string | null;
}

/** Does `turn` answer a question asked at `askedAt` (ISO)? Only one that
 *  started after the question (less the clock skew) can; an unreadable time
 *  on either side gives it the benefit of the doubt. Used ONLY for a
 *  question saved before questions carried their reply's name (`turnId`). */
export function turnAnswers(turn: TurnRow | undefined, askedAt: string | undefined): boolean {
  if (!turn?.turn_id) return false;
  const asked = askedAt ? Date.parse(askedAt) : NaN;
  const started = turn.started_at ? Date.parse(turn.started_at) : NaN;
  return Number.isNaN(asked) || Number.isNaN(started) || started >= asked - REJOIN_SKEW_MS;
}

/** What the daemon says about one reply it ran (`GET /chat/turns/{id}`):
 *  known (with its row), unknown (404: never started, or its record is gone),
 *  or no answer at all (offline, an error) — never guessed. */
export type TurnLookup =
  | { known: true; turn: TurnRow }
  | { known: false; reachable: boolean };

export async function lookupTurn(
  get: <T>(path: string) => Promise<T>,
  turnId: string,
): Promise<TurnLookup> {
  try {
    const row = await get<Partial<TurnRow> | null>(`/chat/turns/${encodeURIComponent(turnId)}`);
    if (!row || typeof row !== "object") return { known: false, reachable: true };
    return { known: true, turn: { ...row, turn_id: turnId } };
  } catch (e) {
    const status = (e as { status?: unknown } | null)?.status;
    return { known: false, reachable: status === 404 };
  }
}

/** The turn that answers a question. Third plan CS10: a question that carries
 *  its reply's name (`turnId`) is answered ONLY by the turn with exactly that
 *  name — a question whose own turn never started is never "answered" by
 *  replaying the one before it. A question saved before names were kept falls
 *  back to the newest turn of thread `threadId` when it started after the
 *  question was asked at `askedAt`. Undefined = no turn answers it (an older
 *  daemon, a failed read, or no such turn: the caller offers what it always
 *  did). */
export async function answeringTurn(
  get: <T>(path: string) => Promise<T>,
  threadId: string,
  askedAt: string | undefined,
  turnId?: string,
): Promise<TurnRow | undefined> {
  if (turnId) {
    const found = await lookupTurn(get, turnId);
    return found.known ? found.turn : undefined;
  }
  let turn: TurnRow | undefined;
  try {
    const res = await get<{ turns?: TurnRow[] }>(
      `/chat/turns?thread_id=${encodeURIComponent(threadId)}&limit=1`,
    );
    turn = Array.isArray(res?.turns) ? res.turns[0] : undefined;
  } catch {
    return undefined;
  }
  return turnAnswers(turn, askedAt) ? turn : undefined;
}

/** A chat message as far as its reply's name goes (third plan, group 18).
 *  `turnId` is the name the page mints for a turn: on the question, on the
 *  reply the page builds from that turn, and on the line the daemon writes
 *  into the chat itself (`savedBy: "daemon"`) when no page saved it. */
export interface TurnLine {
  role: string;
  turnId?: string;
  savedBy?: string;
}

/** Does `list` hold a reply for the turn named `turnId`? */
export function hasReplyFor(list: readonly TurnLine[], turnId: string | undefined): boolean {
  if (!turnId) return false;
  return list.some((m) => m.role === "assistant" && m.turnId === turnId);
}

/** `history` with its last message — the question about to be answered —
 *  carrying the name of the turn that answers it. A retry, a regenerate or a
 *  Continue mints a NEW name, so the new question/answer pair is told apart
 *  from the old one. A history that does not end on a question is returned
 *  as it is. */
export function stampQuestion<T extends TurnLine>(history: T[], turnId: string): T[] {
  const last = history[history.length - 1];
  if (!turnId || !last || last.role !== "user" || last.turnId === turnId) return history;
  return [...history.slice(0, -1), { ...last, turnId }];
}

/**
 * Lay the messages this window adds (`sent`, everything after the part the
 * stored chat already had) onto the stored chat (`stored`), matching replies
 * by their turn's name (third plan CS2):
 *  - a reply already stored for that turn is not added again (two windows
 *    that both followed one turn end with ONE reply);
 *  - a stored `savedBy: "daemon"` line is REPLACED, in its place, by this
 *    window's own message for the same turn (the page's carries the steps,
 *    receipts and files; the daemon's is the words only);
 *  - a message with no name (a chat from before names were kept) and every
 *    user message are appended exactly as before; order is kept.
 */
export function mergeByTurn<T extends TurnLine>(stored: readonly T[], sent: readonly T[]): T[] {
  const out = stored.slice();
  for (const m of sent) {
    if (m.role === "assistant" && m.turnId) {
      const at = out.findIndex((s) => s.role === "assistant" && s.turnId === m.turnId);
      if (at !== -1) {
        if (out[at].savedBy === "daemon" && m.savedBy !== "daemon") out[at] = m;
        continue;
      }
    }
    out.push(m);
  }
  return out;
}
