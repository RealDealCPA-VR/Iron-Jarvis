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
