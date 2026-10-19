/**
 * The receipt line of an @-agent's or a remote agent's reply (v1.355.0,
 * product-readiness G62).
 *
 * A reply from an agent in the chat had no receipt line (Jarvis's own reply
 * says "answered by <model>"), and a remote that TOOK a task left one grey
 * "will report back" line with no sign that the chat was still waiting, or
 * for how long. With message-back off the answer never comes back at all,
 * and nothing said so. Now:
 *
 *  - an agent's reply gets a quiet line: "from hermes · 6 min 12 s" — the
 *    time from the question to the reply, both read off the stamps the chat
 *    already saves (`at` on the question; `at` — stamped by the daemon's
 *    chat_append — or `panelAt` on the reply);
 *  - a remote's progress line with no later line from the same agent adds
 *    "· waiting 3 min" in the warn tone, dropped as soon as a later line
 *    from that agent arrives;
 *  - a remote with message-back OFF says so in its own line (the daemon's
 *    words, agents/threads.no_reply_back_line), and is never shown waiting.
 *
 * Pure, React-free; the page computes the per-row facts once per messages
 * change (strings and booleans, so the memoized rows hold), and the ticking
 * age lives in its own leaf (components/chat/PanelReceipt).
 */

/** The daemon's middle sentence for a remote with message-back OFF —
 *  pinned EQUAL to `agents/threads.NO_REPLY_BACK_MARK`. */
export const NO_REPLY_BACK_MARK = "Its answer will not come back here";

/** The slice of a chat message these helpers read. */
export interface PanelLineFacts {
  role: string;
  content?: string;
  at?: string;
  steer?: boolean;
  /** v1.356.0 integrate: the hidden "Continue" turn (v1.323.0) — sent for
   *  the user, never typed by them, so it is not a question. */
  continuation?: boolean;
  panelWho?: string;
  panelKind?: string;
  panelAt?: string;
  panelError?: boolean;
  /** v1.355.0: the typed twin of the words, when a daemon line carries it. */
  panelNoReply?: boolean;
}

/** When a panel line landed: the daemon's `at` stamp, else the room entry's
 *  `panelAt` (a mirrored inbound line), else undefined. */
export function lineAt(m: PanelLineFacts): string | undefined {
  return m.at || m.panelAt || undefined;
}

/** The words the daemon used for the same case before v1.355.0
 *  (`agents/threads.py`, v1.285.0 to v1.354.0: "<name> has the task and will
 *  report back on its own side when it has something."). A chat saved then
 *  still holds that line; its answer never comes back either, so it must not
 *  wait forever after an update. */
export const OLD_NO_REPLY_BACK_MARK = "will report back on its own side";

/** A remote said its answer will not come back to this chat. */
export function isNoReplyBack(m: PanelLineFacts): boolean {
  const text = m.content ?? "";
  return (
    m.panelNoReply === true ||
    text.includes(NO_REPLY_BACK_MARK) ||
    text.includes(OLD_NO_REPLY_BACK_MARK)
  );
}

function isRemote(who: string | undefined): boolean {
  return !!who && who.startsWith("remote:");
}

/** A line that means "still working on it" (not an answer). */
function isStillWorking(m: PanelLineFacts): boolean {
  return m.panelKind === "progress" || m.panelKind === "pending";
}

/**
 * Per message: the question's time for an agent's line (the nearest earlier
 * question the user typed, never a steer note and never the hidden Continue
 * prompt — v1.356.0 integrate: the waiting clock starts at the real
 * question), else undefined.
 *
 * An answer that follows the same agent's "still working" line counts from
 * the question THAT line answered: a remote's answer can land many minutes
 * later, after the user has asked something else, and the receipt must not
 * time it from the later message. The earliest unanswered "still working"
 * line wins (a remote's own progress updates do not move it), and the
 * answer closes it. A line that said its answer will not come back opens
 * nothing.
 */
export function panelAskedAt(msgs: readonly PanelLineFacts[]): (string | undefined)[] {
  const out: (string | undefined)[] = [];
  let asked: string | undefined;
  const waitingFrom = new Map<string, string | undefined>();
  for (const m of msgs) {
    if (m.role === "user" && !m.steer && !m.continuation) asked = m.at || undefined;
    const who = m.role === "assistant" ? m.panelWho : undefined;
    if (!who) {
      out.push(undefined);
      continue;
    }
    if (isStillWorking(m)) {
      if (!m.panelError && !isNoReplyBack(m) && !waitingFrom.has(who)) waitingFrom.set(who, asked);
      out.push(asked);
      continue;
    }
    if (waitingFrom.has(who)) {
      out.push(waitingFrom.get(who));
      waitingFrom.delete(who);
    } else {
      out.push(asked);
    }
  }
  return out;
}

/**
 * Per message: is this a remote's "still working" line that nothing from
 * the same agent has followed yet? Such a line shows "· waiting <age>". A
 * failed line, or one that said its answer will not come back, never waits.
 */
export function panelWaiting(msgs: readonly PanelLineFacts[]): boolean[] {
  const out: boolean[] = msgs.map(() => false);
  const later = new Set<string>();
  for (let i = msgs.length - 1; i >= 0; i--) {
    const m = msgs[i];
    const who = m.role === "assistant" ? m.panelWho : undefined;
    if (!who) continue;
    if (
      isRemote(who) &&
      isStillWorking(m) &&
      !m.panelError &&
      !isNoReplyBack(m) &&
      !later.has(who)
    ) {
      out[i] = true;
    }
    later.add(who);
  }
  return out;
}

/** Milliseconds from the question to the reply, or null when either stamp is
 *  missing or unreadable, or the reply would come before the question. */
export function panelReplyMs(
  replyAt: string | undefined,
  askedAt: string | undefined,
): number | null {
  if (!replyAt || !askedAt) return null;
  const a = Date.parse(askedAt);
  const r = Date.parse(replyAt);
  if (!Number.isFinite(a) || !Number.isFinite(r) || r < a) return null;
  return r - a;
}

/** How long a line has waited, in whole minutes: "waiting less than a
 *  minute", "waiting 3 min", "waiting 1 h 5 min". */
export function waitingWords(ms: number): string {
  const s = Number.isFinite(ms) ? Math.max(0, Math.floor(ms / 1000)) : 0;
  if (s < 60) return "waiting less than a minute";
  const min = Math.floor(s / 60);
  if (min < 60) return `waiting ${min} min`;
  const h = Math.floor(min / 60);
  const rest = min % 60;
  return rest ? `waiting ${h} h ${rest} min` : `waiting ${h} h`;
}
