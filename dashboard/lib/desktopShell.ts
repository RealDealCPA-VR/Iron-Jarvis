/**
 * v1.226.0: are we running inside the packaged desktop shell? The Electron
 * preload exposes `window.ironjarvis.isDesktop` (desktop/preload.js); in a
 * browser tab it is absent. Offline copy branches on this: the desktop app
 * SUPERVISES its daemon (restart ladder in main.js), so telling that user to
 * run `uv run ironjarvis serve` is wrong advice.
 */
export function isDesktopShell(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return !!(window as unknown as { ironjarvis?: { isDesktop?: boolean } }).ironjarvis
      ?.isDesktop;
  } catch {
    return false;
  }
}

/** The one line both offline surfaces show inside the desktop shell.
 *  v1.312.0: it points at the tray's own "Restart Iron Jarvis" item, never at
 *  Quit — a relaunch is the long way round. Worded as what WILL happen (wave 4
 *  review): the tray offers that item only once the automatic restarts stop
 *  (crash cap or the watchdog's breaker), so "use it now" would point at a
 *  menu item that is not there yet. */
export const DESKTOP_OFFLINE_HINT =
  "Iron Jarvis is restarting its local service… if it keeps failing, a Restart Iron Jarvis option will appear in the tray menu.";

/** Up but not answering, and nothing is restarting it YET (v1.312.0 review):
 *  the watchdog checks every 30 s and kills it after three misses, so the
 *  honest words are "it will be", not "it is being", restarted. */
export const DESKTOP_NOT_ANSWERING_HINT =
  "Iron Jarvis's local service is not answering. Iron Jarvis checks on it every 30 seconds and restarts it if it stays silent.";

/** What the desktop supervisor knows about the daemon (v1.312.0) —
 *  `shellState().services.daemon` in desktop/main.js. */
export interface ShellDaemonState {
  capped: boolean;
  /** The ladder counter. NOT cleared while the daemon stays up, so it is only
   *  "the current attempt" while `restarting` is true. */
  restarts: number;
  lastExit: number | null;
  /** Defensive: main.js's repair dialog is modal and quits, so a page rarely
   *  gets to see this. */
  damaged: boolean;
  /** A restart is really under way: a backoff pending, or a fresh spawn still
   *  booting. False for a daemon that is up but not answering. */
  restarting: boolean;
  /** Once capped: the crash count and window the tray's toast reported
   *  ("crashed 10 times in 15 minutes"); null otherwise. */
  cappedCrashes: number | null;
  cappedWindowMin: number | null;
  /** v1.312.0 review: up, not answering, and the watchdog's breaker has
   *  stopped killing it — the second way the shell stops restarting. */
  stalled: boolean;
}

/** The shell bridge (desktop/preload.js `shell`). Every member is optional: an
 *  older shell lacks the newer ones, and each call resolves null when the main
 *  process refuses the sender. */
export interface ShellBridge {
  getState?: () => Promise<unknown>;
  openLogs?: () => Promise<unknown>;
  restartDaemon?: () => Promise<boolean | null>;
}

/** The bridge, or null in a browser tab or a shell whose preload has none. */
export function shellBridge(): ShellBridge | null {
  if (typeof window === "undefined") return null;
  try {
    const b = (window as unknown as { ironjarvis?: { shell?: unknown } }).ironjarvis?.shell;
    return b && typeof b === "object" ? (b as ShellBridge) : null;
  } catch {
    return null;
  }
}

/** Read `services.daemon` out of a `shell.getState()` answer. Null for a
 *  refused read, an older shell (no `services`), or anything malformed — the
 *  banner then keeps the plain hint rather than guess. */
export function daemonStateOf(state: unknown): ShellDaemonState | null {
  if (!state || typeof state !== "object") return null;
  const d = (state as { services?: { daemon?: unknown } }).services?.daemon;
  if (!d || typeof d !== "object") return null;
  const r = d as Record<string, unknown>;
  const count = (v: unknown): number | null =>
    typeof v === "number" && Number.isFinite(v) ? Math.max(0, Math.floor(v)) : null;
  return {
    capped: r.capped === true,
    restarts: count(r.restarts) ?? 0,
    lastExit: typeof r.lastExit === "number" ? r.lastExit : null,
    damaged: r.damaged === true,
    // Missing = false: never claim a restart the shell did not report.
    restarting: r.restarting === true,
    cappedCrashes: count(r.cappedCrashes),
    cappedWindowMin: count(r.cappedWindowMin),
    // Missing = false (an older shell): never claim the restarts stopped.
    stalled: r.stalled === true,
  };
}

/** Which story the offline banner tells. `unknown` = a browser, an older shell
 *  or a refused read: the plain hint, no buttons that might not work.
 *  `stalled` = the watchdog's breaker stopped restarting a hung daemon;
 *  `waiting` = up but not answering, the watchdog has not acted yet (v1.312.0
 *  review: neither may say "restarting"). */
export type ShellOfflineState =
  | "restarting"
  | "waiting"
  | "stalled"
  | "capped"
  | "damaged"
  | "unknown";

export function offlineStateOf(d: ShellDaemonState | null): ShellOfflineState {
  if (!d) return "unknown";
  if (d.damaged) return "damaged";
  if (d.capped) return "capped";
  if (d.stalled) return "stalled";
  return d.restarting ? "restarting" : "waiting";
}

/** The stalled sentence (v1.312.0 review): the toast's own story, and the
 *  same way forward as the capped one. */
export const STALLED_SENTENCE =
  "Iron Jarvis's service stopped answering, and automatic restarts are paused after several tries. " +
  "Restart it to try again, or open the logs to see why.";

/** "stopped after 10 crashes in 15 minutes" — the capped sentence, with the
 *  same numbers the tray's toast gave (v1.312.0 review: never the ladder's
 *  `restarts`, which a long healthy run resets mid-window). Without them it
 *  says "repeated crashes" rather than guess a number. */
export function cappedSentence(crashes: number | null, windowMin: number | null): string {
  const what =
    crashes && crashes > 0
      ? `${crashes} ${crashes === 1 ? "crash" : "crashes"}${windowMin ? ` in ${windowMin} minutes` : ""}`
      : "repeated crashes";
  return (
    `Iron Jarvis's service stopped after ${what}, so it is no longer being restarted on its own. ` +
    "Restart it to try again, or open the logs to see why."
  );
}

/** The desktop shell's pop-out bridge (v1.283.0) — see desktop/preload.js. */
export interface PopoutBridge {
  isPopout: boolean;
  path: string;
  open: (path: string) => Promise<{ ok: boolean; path: string; reused?: boolean } | null>;
  list: () => Promise<string[] | null>;
  focus: (path: string) => Promise<{ ok: boolean; path: string } | null>;
  close: (path: string) => Promise<{ ok: boolean; path: string } | null>;
}

/** The bridge, or null outside the desktop shell (or on an older shell). */
export function popoutBridge(): PopoutBridge | null {
  if (typeof window === "undefined") return null;
  try {
    const b = (window as unknown as { ironjarvis?: { popout?: Partial<PopoutBridge> } }).ironjarvis
      ?.popout;
    return b && typeof b.open === "function" ? (b as PopoutBridge) : null;
  } catch {
    return null;
  }
}

/** Is THIS window a popped-out module? False in the main window and in a browser. */
export function isPopoutWindow(): boolean {
  return popoutBridge()?.isPopout === true;
}
