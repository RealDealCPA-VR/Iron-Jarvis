"use client";

import { useCallback, useEffect, useRef, useState } from "react";
// v1.250.0 (S-08): `m` + the layout's LazyMotion, so this banner no longer
// pulls framer-motion's whole feature set into the shared chunk.
import { AnimatePresence, m } from "framer-motion";
import { FolderOpen, RefreshCw, RotateCcw, ServerCrash, ShieldAlert, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { API_BASE } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";
import {
  DESKTOP_NOT_ANSWERING_HINT,
  DESKTOP_OFFLINE_HINT,
  STALLED_SENTENCE,
  cappedSentence,
  daemonStateOf,
  isDesktopShell,
  offlineStateOf,
  shellBridge,
  type ShellDaemonState,
  type ShellOfflineState,
} from "@/lib/desktopShell";
import { useVisibleInterval } from "@/lib/useVisibleInterval";

/** How often the desktop banner re-reads the shell's supervisor state while
 *  the daemon is offline (v1.312.0). A local IPC call, never a network one,
 *  and only while offline and visible: once the ladder gives up, the banner
 *  stops saying "restarting" within this bound. */
export const SHELL_POLL_MS = 3000;

/** Said when the window's Restart was refused or found nothing to restart.
 *  main.js changes nothing on a refusal, so the tray item it names is still
 *  there; quitting and reopening is the way back that always exists. */
export const RESTART_REFUSED_NOTE =
  "This window could not restart it. Use Restart Iron Jarvis in the tray menu instead (the Iron Jarvis icon by the clock — it may be under the ^ arrow), or quit Iron Jarvis and open it again.";

/** The port the dashboard is pointed at, surfaced in the "start it" hint. */
function apiPort(): string {
  try {
    return new URL(API_BASE).port || "8787";
  } catch {
    return "8787";
  }
}

/** v1.264.0: the desktop app registers `ironjarvis://` (desktop/main.js) and
 *  opens its own window at the path this link names. Only a dashboard path:
 *  nothing outside `/…`, and never a second host. */
export function appLink(pathname: string | null | undefined): string {
  const p = (pathname ?? "").trim();
  // No dots and no `//`: the same rule desktop/main.js applies on the other end.
  const safe = /^\/[A-Za-z0-9/_\-]*$/.test(p) && !p.includes("//") ? p : "/";
  return `ironjarvis://${safe.replace(/^\//, "")}`;
}

/** The desktop banner's words for what the shell says (v1.312.0). */
function DesktopOfflineWords({
  view,
  daemon,
}: {
  view: ShellOfflineState;
  daemon: ShellDaemonState | null;
}) {
  if (view === "capped") {
    return <>{cappedSentence(daemon?.cappedCrashes ?? null, daemon?.cappedWindowMin ?? null)}</>;
  }
  if (view === "stalled") {
    // v1.312.0 review: the watchdog's breaker — the other way the shell stops
    // restarting. Nothing is restarting here, so never the restarting words.
    return <>{STALLED_SENTENCE}</>;
  }
  if (view === "waiting") {
    // Up but not answering, before the watchdog acts: it WILL be restarted.
    return <>{DESKTOP_NOT_ANSWERING_HINT}</>;
  }
  if (view === "damaged") {
    // DEFENSIVE: main.js's repair dialog is modal and quits the app, so a page
    // rarely lives to read this. If one does, it must not say "restarting".
    return (
      <>
        Iron Jarvis found that its install is damaged, so it stopped restarting its service.
        Follow the repair window, or reinstall the latest version — your data is untouched.
      </>
    );
  }
  // The ladder's counter outlives the outage it counted (main.js clears it only
  // at the NEXT death): a daemon that crashed twice this morning and then hung
  // is not on "restart attempt 2". Number it only while a restart is under way
  // (that daemon is "waiting" above).
  const attempt = view === "restarting" && daemon?.restarting ? daemon.restarts : 0;
  return (
    <>
      {DESKTOP_OFFLINE_HINT}
      {attempt >= 1 ? ` This is restart attempt ${attempt}.` : null}
    </>
  );
}

/**
 * A single, app-wide banner shown when the daemon can't be reached. Dismissible
 * for the current view; reappears on the next route load if still offline.
 */
export function DaemonBanner() {
  const { online, unauthorized, requestError, checking, refresh } = useDaemon();
  // Retry used to be `window.location.reload()`. Against a FROZEN daemon that
  // reloads into an identical banner with no sign anything happened — reported
  // as "hitting retry didn't seem to do anything". It now re-probes and SAYS it
  // is probing; the health poll's own 8s timeout bounds the wait, after which
  // the banner still standing is the answer.
  const [retrying, setRetrying] = useState(false);
  // Track WHICH state was dismissed (not a shared flag) so dismissing the offline
  // banner never suppresses a later token/error banner, and vice-versa.
  const [dismissed, setDismissed] = useState<string | null>(null);
  const port = apiPort();
  const pathname = usePathname();
  // v1.312.0 (wave 4, desktop-offline-banner-lies-when-capped): inside the
  // desktop app the banner says what the SHELL knows. It used to print
  // "restarting its local service…" even after the supervisor had stopped
  // restarting a crash-looping daemon, beside a Retry that only re-probes.
  const desktop = isDesktopShell();
  const [daemonState, setDaemonState] = useState<ShellDaemonState | null>(null);
  const [restartPending, setRestartPending] = useState(false);
  const [shellNote, setShellNote] = useState<string | null>(null);
  // Only the newest read may land: a slow answer must not overwrite a newer one.
  const readSeq = useRef(0);

  // One current problem state, by priority. A fresh/different problem re-shows the
  // banner (the App Router root layout never remounts, so a plain flag was sticky).
  const state = checking
    ? null
    : !online
      ? "offline"
      : unauthorized
        ? "auth"
        : requestError
          ? "error"
          : null;
  useEffect(() => {
    if (state !== dismissed) setDismissed(null);
  }, [state, dismissed]);

  useEffect(() => {
    if (online) setRetrying(false);
  }, [online]);

  // Gate on the dismissed-state too, or the X buttons do nothing (the useEffect
  // above re-shows the banner when a DIFFERENT problem appears by clearing dismiss).
  const showOffline = state === "offline" && dismissed !== "offline";
  const showAuth = state === "auth" && dismissed !== "auth";
  const showError = state === "error" && dismissed !== "error";

  // Read the shell only while the desktop banner is up — never while online.
  // A browser tab, an older shell with no getState, or a refused read (null)
  // all land on "unknown": the plain hint, never a button that might not work.
  const watchShell = showOffline && desktop;
  const readShell = useCallback(() => {
    const seq = ++readSeq.current;
    const getState = shellBridge()?.getState;
    if (typeof getState !== "function") {
      setDaemonState(null);
      return;
    }
    let answer: Promise<unknown>;
    try {
      answer = Promise.resolve(getState());
    } catch {
      setDaemonState(null);
      return;
    }
    answer.then(
      (s) => {
        if (seq === readSeq.current) setDaemonState(daemonStateOf(s));
      },
      () => {
        if (seq === readSeq.current) setDaemonState(null);
      },
    );
  }, []);
  useEffect(() => {
    if (watchShell) {
      readShell();
      return;
    }
    // Back online (or dismissed): forget the old story and drop any read still
    // in flight, so a later outage starts from what the shell says then.
    readSeq.current += 1;
    setDaemonState(null);
    setShellNote(null);
  }, [watchShell, readShell]);
  useVisibleInterval(readShell, SHELL_POLL_MS, watchShell);

  // Only the offline desktop banner touches the newer shell helpers (an online
  // page renders exactly as before).
  const shellView: ShellOfflineState = watchShell ? offlineStateOf(daemonState) : "unknown";
  const bridge = watchShell ? shellBridge() : null;
  // Capped and stalled are the two states the shell has stopped restarting
  // in: both get the window's Restart (v1.312.0 review — stalled used to fall
  // through to "restarting" with only a Retry).
  const stopped = shellView === "capped" || shellView === "stalled";
  const canRestart = stopped && typeof bridge?.restartDaemon === "function";
  const canOpenLogs =
    (stopped || shellView === "damaged") && typeof bridge?.openLogs === "function";

  const restartDaemon = async () => {
    const restart = shellBridge()?.restartDaemon;
    if (typeof restart !== "function") return;
    setRestartPending(true);
    setShellNote(null);
    let answer: unknown = null;
    try {
      answer = await restart();
    } catch {
      answer = null;
    }
    setRestartPending(false);
    // true = a fresh daemon is starting. Either way read the shell again at
    // once, so the banner says "restarting" now rather than at the next poll;
    // anything but true (a refused sender, nothing to restart) says so and
    // names the tray, which is the other way back.
    if (answer !== true) setShellNote(RESTART_REFUSED_NOTE);
    readShell();
  };

  const openLogs = async () => {
    const open = shellBridge()?.openLogs;
    if (typeof open !== "function") return;
    setShellNote(null);
    try {
      const r = (await open()) as { ok?: boolean; path?: string } | null;
      if (r && r.ok === false) {
        setShellNote(`Could not open the logs folder${r.path ? ` (${r.path})` : ""}.`);
      }
    } catch {
      setShellNote("Could not open the logs folder.");
    }
  };

  return (
    <AnimatePresence>
      {showOffline && (
        <m.div
          role="status"
          aria-live="polite"
          data-testid="daemon-banner-offline"
          data-shell-state={shellView}
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
          className="notice-warn overflow-hidden border-b backdrop-blur-sm"
        >
          <div className="flex items-center gap-3 px-6 py-2.5 lg:px-10">
            <ServerCrash size={16} className="notice-warn-icon shrink-0" aria-hidden="true" />
            <div className="min-w-0 flex-1 text-sm">
              <span className="notice-warn-title font-semibold">Daemon offline.</span>{" "}
              <span className="notice-warn-body">
                {/* v1.226.0: the packaged app supervises its own daemon — the
                    CLI line only makes sense in a browser tab. */}
                {desktop ? (
                  <DesktopOfflineWords view={shellView} daemon={daemonState} />
                ) : (
                  <>
                    Start it with{" "}
                    <code className="notice-warn-code rounded px-1.5 py-0.5 font-mono text-xs">
                      uv run ironjarvis serve --port {port} --root .
                    </code>
                  </>
                )}
                {shellNote && (
                  <span data-testid="daemon-restart-note" className="mt-1 block">
                    {shellNote}
                  </span>
                )}
              </span>
            </div>
            {canRestart && (
              <button
                onClick={() => void restartDaemon()}
                disabled={restartPending}
                className="notice-warn-btn flex shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-60"
              >
                <RotateCcw
                  size={12}
                  aria-hidden="true"
                  className={restartPending ? "animate-spin" : undefined}
                />
                Restart Iron Jarvis
              </button>
            )}
            {canOpenLogs && (
              <button
                onClick={() => void openLogs()}
                className="notice-warn-btn flex shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors"
              >
                <FolderOpen size={12} aria-hidden="true" />
                Open logs
              </button>
            )}
            {/* A capped daemon is not coming back on its own: Retry would only
                re-probe for ever, so the Restart button takes its place. */}
            {!canRestart && (
              <button
                onClick={() => {
                  setRetrying(true);
                  refresh();
                  window.setTimeout(() => setRetrying(false), 9000);
                }}
                disabled={retrying}
                aria-label="Retry connection"
                className="notice-warn-btn flex shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-60"
              >
                <RefreshCw
                  size={12}
                  aria-hidden="true"
                  className={retrying ? "animate-spin" : undefined}
                />
                {retrying ? "Checking…" : "Retry"}
              </button>
            )}
            <button
              onClick={() => setDismissed("offline")}
              aria-label="Dismiss offline banner"
              className="shrink-0 rounded-lg p-1 text-amber-300/70 transition-colors hover:bg-amber-500/15 hover:text-amber-200"
            >
              <X size={15} aria-hidden="true" />
            </button>
          </div>
        </m.div>
      )}
      {showAuth && (
        <m.div
          role="alert"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
          className="overflow-hidden border-b border-rose-500/25 bg-rose-500/[0.08] backdrop-blur-sm"
        >
          <div className="flex items-center gap-3 px-6 py-2.5 lg:px-10">
            <ShieldAlert size={16} className="shrink-0 text-rose-300" aria-hidden="true" />
            <div className="min-w-0 flex-1 text-sm text-rose-100/90">
              <span className="font-semibold text-rose-200">Daemon rejected your token.</span>{" "}
              <span className="text-rose-100/70">
                {isDesktopShell() ? (
                  <>
                    The daemon is running but your access token is missing or stale — data
                    below may look empty. Re-enter it to reconnect.
                  </>
                ) : (
                  /* v1.264.0: the add-on's Open Jarvis button lands here — a
                     browser tab has no token, and "re-enter it" sent the user
                     hunting. Name the way out and the file. */
                  <>
                    This page was opened in a browser, so it has no token — data below may look
                    empty. Open it in the Iron Jarvis app instead, or paste the token from{" "}
                    <code className="rounded bg-rose-500/10 px-1 py-0.5 font-mono text-[11px]">
                      %APPDATA%\Iron Jarvis\token.txt
                    </code>
                    .
                  </>
                )}
              </span>
            </div>
            {!isDesktopShell() && (
              <a
                href={appLink(pathname)}
                data-testid="open-in-app"
                className="flex shrink-0 items-center gap-1.5 rounded-lg border border-rose-500/30 px-2.5 py-1 text-xs font-medium text-rose-200 transition-colors hover:bg-rose-500/15"
              >
                Open in the Iron Jarvis app
              </a>
            )}
            <Link
              href="/settings"
              className="flex shrink-0 items-center gap-1.5 rounded-lg border border-rose-500/30 px-2.5 py-1 text-xs font-medium text-rose-200 transition-colors hover:bg-rose-500/15"
            >
              Enter token
            </Link>
            <button
              onClick={() => setDismissed("auth")}
              aria-label="Dismiss token banner"
              className="shrink-0 rounded-lg p-1 text-rose-300/70 transition-colors hover:bg-rose-500/15 hover:text-rose-200"
            >
              <X size={15} aria-hidden="true" />
            </button>
          </div>
        </m.div>
      )}
      {showError && (
        <m.div
          role="alert"
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: "auto", opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
          className="overflow-hidden border-b border-amber-500/25 bg-amber-500/[0.08] backdrop-blur-sm"
        >
          <div className="flex items-center gap-3 px-6 py-2.5 lg:px-10">
            <ServerCrash size={16} className="shrink-0 text-amber-300" aria-hidden="true" />
            <div className="min-w-0 flex-1 text-sm text-amber-100/90">
              <span className="font-semibold text-amber-200">A request to the daemon failed.</span>{" "}
              <span className="text-amber-100/70">
                Some data below may be incomplete or out of date. It will refresh on the
                next poll — reload if it persists.
              </span>
            </div>
            <button
              onClick={() => window.location.reload()}
              aria-label="Reload"
              className="flex shrink-0 items-center gap-1.5 rounded-lg border border-amber-500/30 px-2.5 py-1 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-500/15"
            >
              <RefreshCw size={12} aria-hidden="true" /> Reload
            </button>
            <button
              onClick={() => setDismissed("error")}
              aria-label="Dismiss error banner"
              className="shrink-0 rounded-lg p-1 text-amber-300/70 transition-colors hover:bg-amber-500/15 hover:text-amber-200"
            >
              <X size={15} aria-hidden="true" />
            </button>
          </div>
        </m.div>
      )}
    </AnimatePresence>
  );
}
