// Pane progress visibility (v1.212.0) — the PURE half.
//
// The Build page flips each pane between a Terminal layer and a Chat layer
// with visibility only (v1.206.0 — both stay mounted, nothing unmounts on a
// flip). The hidden layer can still be WORKING: the chat streaming a turn or
// paused on an ApprovalCard the daemon holds for up to 180s, the terminal
// printing new output. These helpers are the testable seams behind the
// toggle-button badges that surface that progress.

/** What a pane's chat reports to the page whenever it changes. */
export interface PaneChatStatus {
  /** A turn is in flight — includes the pre-stream POST window (the same
   *  `sending || stream.streaming` derivation as the composer's spinner, so
   *  the badge never flickers off between the POST landing and the first
   *  streamed token). */
  streaming: boolean;
  /** The turn is PAUSED on an ApprovalCard waiting for the user. */
  approval: boolean;
  /** v1.213.0: name of the LAST still-running tool card of the live stream,
   *  "" when none (or when the chat is idle — a finished turn's stale card
   *  list must never read as current activity). */
  tool: string;
  /** v1.213.0: tail of the streamed text so far ({@link textTail}, ~90
   *  chars, whitespace-collapsed), "" while idle. Changes every token — the
   *  reporter throttles reports it drives (see TAIL_REPORT_MS). */
  textTail: string;
}

/** Peek-strip sizing/timing (v1.213.0) — shared by PaneChat, the page, and
 *  the tests so no surface hardcodes its own copy. */
export const CHAT_TAIL_CHARS = 90;
export const TERM_LINE_CHARS = 120;
/** Minimum gap between textTail-DRIVEN status reports (ms). Transitions of
 *  streaming/approval/tool always report immediately; only the every-token
 *  text tail is paced, so a token burst cannot re-render the whole Build
 *  canvas per token. */
export const TAIL_REPORT_MS = 400;
/** How long the terminal peek line stays up after the LAST output frame (ms).
 *  Timestamp state + one timeout scheduled from the last update — no polling. */
export const TERM_PEEK_QUIET_MS = 15_000;

// ---- ANSI/OSC stripping (v1.213.0) ----------------------------------------
// The terminal peek line shows raw PTY bytes to a human OUTSIDE xterm, so the
// escape traffic has to go. Hand-rolled and conservative on purpose (no
// dependency):
//  - OSC (ESC ] … BEL / ESC \): the payload match is BOUNDED at 256 chars so
//    an UNTERMINATED OSC (its terminator still in the next frame) can never
//    swallow the rest of a chunk's real output. Cost of the bound: a
//    terminated-but-huge OSC leaves its overflow behind — window titles are
//    tens of chars, real output loss is the expensive failure.
//  - CSI (ESC [ params intermediates final): the standard byte-range grammar.
//  - Leftover ESC + optional intermediate + optional final covers charset
//    designations (ESC ( B) and stray Fe escapes.
//  - "\r" becomes "\n" BEFORE the control sweep: progress bars redraw with
//    bare carriage returns, and deleting "\r" outright would weld every
//    redraw into one line ("10%50%90%") — as line breaks, lastLine honestly
//    picks the LATEST redraw.
//  - Remaining control chars except "\n" are removed, including "�"
//    (a frame boundary can split a UTF-8 sequence; the best-effort decode's
//    replacement char is decode noise, not output).
// eslint-disable-next-line no-control-regex
const OSC_RE = /\x1b\][^\x07\x1b]{0,256}(?:\x07|\x1b\\)?/g;
// eslint-disable-next-line no-control-regex
const CSI_RE = /\x1b\[[0-?]*[ -/]*[@-~]/g;
// eslint-disable-next-line no-control-regex
const ESC_RE = /\x1b[ -/]?[0-~]?/g;
// eslint-disable-next-line no-control-regex
const CTRL_RE = /[\0-\x09\x0b-\x1f\x7f�]/g;

/** Remove CSI/OSC/single-char escapes and control chars (except \n). */
export function stripAnsi(text: string): string {
  return text
    .replace(OSC_RE, "")
    .replace(CSI_RE, "")
    .replace(ESC_RE, "")
    .replace(/\r/g, "\n")
    .replace(CTRL_RE, "");
}

/** The last NON-EMPTY line of `text` after {@link stripAnsi}, trimmed (a
 *  prompt's trailing whitespace says nothing) and capped to `cap` chars —
 *  the NEWEST chars win, with a leading ellipsis owning up to the cut.
 *  "" = genuinely nothing to show (the no-empty-husk rule). */
export function lastLine(text: string, cap: number = TERM_LINE_CHARS): string {
  const lines = stripAnsi(text).split("\n");
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = lines[i].trim();
    if (line) return line.length > cap ? `…${line.slice(-(cap - 1))}` : line;
  }
  return "";
}

/** Collapse ALL whitespace (a peek strip is one line) and keep the TAIL,
 *  capped to `cap` chars with a leading ellipsis owning up to the cut. */
export function textTail(text: string, cap: number = CHAT_TAIL_CHARS): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > cap ? `…${flat.slice(-(cap - 1))}` : flat;
}

/** Minimum gap between onOutput notifications (ms). PTY output arrives as a
 *  storm of small frames (the daemon's pump polls every 10ms), and the page
 *  only needs "something new happened", not a callback per frame. */
export const OUTPUT_NOTIFY_MS = 300;

/**
 * Does this ws frame carry actual terminal output? On the terminal attach
 * socket every SERVER frame IS PTY bytes: the daemon only ever
 * `send_bytes()`es PTY reads, the scrollback replay, and the shell-exited
 * note (daemon/routes/terminals.py) — the JSON control frames (resize) and
 * raw keystrokes travel client→server only, and the shell-exit signal is a
 * CLOSE code (4000), not a message. So classification is "a non-empty
 * text/binary frame", defensive about emptiness and unknown shapes rather
 * than parsing something that carries no envelope.
 */
export function isOutputFrame(data: unknown): boolean {
  if (typeof data === "string") return data.length > 0;
  if (typeof ArrayBuffer !== "undefined" && data instanceof ArrayBuffer) {
    return data.byteLength > 0;
  }
  return false;
}

/**
 * The whole notify decision for one ws frame, pure: returns the timestamp to
 * store as "last notified" when the callback should fire, or null to stay
 * quiet. Quiet when:
 *  - the frame carries no output ({@link isOutputFrame});
 *  - `now` is inside the post-(re)connect replay window. The scrollback
 *    catch-up is NOT distinguishable by frame shape — the daemon replays it
 *    as ordinary send_bytes with no marker — so this honestly reuses the
 *    same time-window heuristic the pane's answerback suppression already
 *    trusts (replayGuardUntil); the throttle below and the page's view gate
 *    absorb whatever the window misses;
 *  - the last notification was under {@link OUTPUT_NOTIFY_MS} ago.
 */
export function outputNotifyAt(
  data: unknown,
  now: number,
  lastNotified: number,
  replayGuardUntil: number,
): number | null {
  if (!isOutputFrame(data)) return null;
  if (now < replayGuardUntil) return null;
  if (now - lastNotified < OUTPUT_NOTIFY_MS) return null;
  return now;
}
