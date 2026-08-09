"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ServerCrash, ShieldAlert, X, RefreshCw } from "lucide-react";
import Link from "next/link";
import { API_BASE } from "@/lib/api";
import { useDaemon } from "@/lib/daemon";

/** The port the dashboard is pointed at, surfaced in the "start it" hint. */
function apiPort(): string {
  try {
    return new URL(API_BASE).port || "8787";
  } catch {
    return "8787";
  }
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

  return (
    <AnimatePresence>
      {showOffline && (
        <motion.div
          role="status"
          aria-live="polite"
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
                Start it with{" "}
                <code className="notice-warn-code rounded px-1.5 py-0.5 font-mono text-xs">
                  uv run ironjarvis serve --port {port} --root .
                </code>
              </span>
            </div>
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
            <button
              onClick={() => setDismissed("offline")}
              aria-label="Dismiss offline banner"
              className="shrink-0 rounded-lg p-1 text-amber-300/70 transition-colors hover:bg-amber-500/15 hover:text-amber-200"
            >
              <X size={15} aria-hidden="true" />
            </button>
          </div>
        </motion.div>
      )}
      {showAuth && (
        <motion.div
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
                The daemon is running but your access token is missing or stale — data
                below may look empty. Re-enter it to reconnect.
              </span>
            </div>
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
        </motion.div>
      )}
      {showError && (
        <motion.div
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
        </motion.div>
      )}
    </AnimatePresence>
  );
}
