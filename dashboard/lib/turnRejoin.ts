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
  status?: string;
  running?: boolean;
  started_at?: string | null;
}

/** Does `turn` answer a question asked at `askedAt` (ISO)? Only one that
 *  started after the question (less the clock skew) can; an unreadable time
 *  on either side gives it the benefit of the doubt. */
export function turnAnswers(turn: TurnRow | undefined, askedAt: string | undefined): boolean {
  if (!turn?.turn_id) return false;
  const asked = askedAt ? Date.parse(askedAt) : NaN;
  const started = turn.started_at ? Date.parse(turn.started_at) : NaN;
  return Number.isNaN(asked) || Number.isNaN(started) || started >= asked - REJOIN_SKEW_MS;
}

/** The newest turn of thread `threadId` when it answers a question asked at
 *  `askedAt`, else undefined (an older daemon, a failed read, or no such
 *  turn: the caller offers what it always did). */
export async function answeringTurn(
  get: <T>(path: string) => Promise<T>,
  threadId: string,
  askedAt: string | undefined,
): Promise<TurnRow | undefined> {
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
