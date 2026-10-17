/**
 * v1.345.0 — "an @ reply comes home". Pure helpers for the chat page's
 * RE-READ of a saved conversation after the DAEMON appended to it.
 *
 * The bug this wave fixes: an @-round (POST /chat/panel) that took six
 * minutes answered after the user had opened another chat, and the page's
 * continuation wrote `[@, reply]` over WHATEVER CHAT WAS OPEN — the other
 * chat's two messages were overwritten on disk and the reply never appeared
 * in the chat it was asked in. Now the daemon appends the reply to the chat
 * it was asked in (server-side, one writer), and the page RE-READS the thread
 * and ADOPTS the server's array instead of composing + PUTting its own.
 *
 * Nothing here touches React: the page keeps its refs and its save box;
 * these functions only answer small questions about a thread's messages.
 */

/** The subset of a chat line these helpers read. The page's `ChatMessage`
 *  satisfies it; so does a raw line off GET /chat/threads/{id}. */
export interface RereadLine {
  role?: string;
  content?: string;
  /** The page's own key for "this reply came from that agent session". */
  fromSession?: string;
  /** The daemon's key for the same fact (chat_append's whitelist). */
  sessionId?: string;
  /** The page's own key for the run's ledger card. */
  runResult?: unknown;
  /** The daemon's key for the same card (chat_append's whitelist). */
  receipt?: unknown;
  documents?: string[];
  panelWho?: string;
}

/** The line the daemon appended for agent session `sessionId`, or
 *  undefined — read under BOTH keys, so a page-composed line (`fromSession`)
 *  and a daemon-appended one (`sessionId`) are found alike. */
export function daemonLineFor<M extends RereadLine>(
  msgs: readonly M[] | undefined,
  sessionId: string,
): M | undefined {
  if (!sessionId || !Array.isArray(msgs)) return undefined;
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    if (!m || m.role !== "assistant") continue;
    if (m.sessionId === sessionId || m.fromSession === sessionId) return m;
  }
  return undefined;
}

/** True when `receipt` has the shape of the page's RunResult card (the
 *  ledger's `found` verdict and a session id) — anything else is left alone. */
function looksLikeRunResult(receipt: unknown): boolean {
  if (!receipt || typeof receipt !== "object") return false;
  const r = receipt as Record<string, unknown>;
  return typeof r.found === "boolean" && typeof r.session_id === "string";
}

/** A daemon-appended agent line read AS the page's own shape: `sessionId`
 *  stands in for `fromSession` and a RunResult-shaped `receipt` for
 *  `runResult`, each ONLY when the page's key is absent (a page-composed line
 *  is returned untouched, by reference). The receipt/footer under a reply
 *  keys on the page's names, so this is what keeps "nothing changes what a
 *  line looks like" true for a line the daemon wrote. Every other line is
 *  returned as it was — same object — so reference checks downstream (the
 *  save rebase) still hold. */
export function adoptDaemonLines<M extends RereadLine>(msgs: readonly M[] | undefined): M[] {
  if (!Array.isArray(msgs)) return [];
  return msgs.map((m) => {
    if (!m || m.role !== "assistant") return m;
    const needsSession = typeof m.sessionId === "string" && m.sessionId && !m.fromSession;
    const needsResult = m.runResult === undefined && looksLikeRunResult(m.receipt);
    if (!needsSession && !needsResult) return m;
    // The caller's line type is wider than RereadLine (the page's ChatMessage
    // types `runResult` as its RunResult card); `looksLikeRunResult` checked
    // the shape the page reads, so the widened copy is handed back as M.
    return {
      ...m,
      ...(needsSession ? { fromSession: m.sessionId } : {}),
      ...(needsResult ? { runResult: m.receipt } : {}),
    } as M;
  });
}

/** Is the server's version stamp a version this window does not hold yet?
 *  A missing stamp on either side reads as "newer" — an older daemon sends
 *  none, and a re-read with nothing to compare must still land. */
export function threadIsNewer(serverStamp: string | null | undefined, held: string | undefined): boolean {
  if (!serverStamp || !held) return true;
  return serverStamp !== held;
}

/** "builder is working" / "builder and critic are working" — the live
 *  bubble's words while an @-round runs. `names` are the names typed after
 *  "@" (the panel body's `mentions`), never participant keys. */
export function panelWorkingLabel(names: readonly string[]): string {
  const list = names.map((n) => n.trim()).filter(Boolean);
  if (list.length === 0) return "Working";
  if (list.length === 1) return `${list[0]} is working`;
  const head = list.slice(0, -1).join(", ");
  return `${head} and ${list[list.length - 1]} are working`;
}
