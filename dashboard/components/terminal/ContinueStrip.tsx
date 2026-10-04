"use client";

/**
 * CONTINUE ON THE NEXT ACCOUNT (v1.303.0).
 *
 * A Claude pane runs on ONE subscription account for its whole life (v1.302.0:
 * the account is the CLI's home folder, read once when it starts). When that
 * account runs out, Claude Code says so in the pane ("Usage limit reached ·
 * …") and the daemon's activity row carries `limit`. This strip — the
 * ResumeStrip pattern (v1.245.0) — offers the one move that helps: open a NEW
 * pane on the next free Claude account, carrying the conversation over when it
 * can (`POST /terminals/{id}/continue-on-next`). The click is the consent.
 *
 *  - Shown only for a pane on an IRON-PROXY Claude account with a `limit`.
 *  - Names the account it would move to from the LIGHT Iron-Proxy read
 *    (`?discover=0`), read only while the strip is on screen.
 *  - No other free account: says when the first frees, and has no button.
 *  - Dismiss hides it for this limit EPISODE (`limitKey`: its `since`), not
 *    its line — a countdown repaints the line (review F5).
 *  - Before the press it says the conversation is COPIED to the other account
 *    and continues there, sent to Anthropic under it (review F4, K3).
 *  - The new pane's header says "Continued from “Work Max”" with the answer's
 *    note until that line is dismissed (`ContinuedFromLine`).
 *
 * Exported apart from TerminalPane so a test can mount it (xterm cannot).
 */

import { useState, useSyncExternalStore } from "react";
import { ArrowRightLeft, Loader2, X } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { usePolledApi } from "@/lib/useApi";
import { localTime } from "@/lib/ironProxy";
import {
  IRON_PROXY_LIGHT,
  LAUNCH_ACCOUNTS_POLL_MS,
  continuableAccount,
  continueNote,
  isContinueAnswer,
  limitKey,
  limitResetWords,
  limitWords,
  nextAccount,
  type PaneAccount,
  type PaneAccounts,
  type PaneLimit,
} from "@/lib/paneAccounts";

/* ---- per-window memory -------------------------------------------------------
 * A pane's component unmounts when you leave Build (its terminal does not —
 * v1.243.0), so what was dismissed and which pane was continued from where is
 * kept per window, outside React, the way paneHost keeps the terminals. */

const dismissedLine = new Map<string, string>();
const continued = new Map<string, { from: string; note: string }>();
const listeners = new Set<() => void>();
let version = 0;
function changed() {
  version += 1;
  for (const l of listeners) l();
}
function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}
function useStoreVersion() {
  return useSyncExternalStore(subscribe, () => version, () => version);
}

/** Test seam: forget everything this window remembered. */
export function resetContinueMemory() {
  dismissedLine.clear();
  continued.clear();
  changed();
}

/** The new pane remembers where it came from (shown until dismissed). */
export function rememberContinued(paneId: string, from: string, note: string) {
  continued.set(paneId, { from, note });
  changed();
}

export function continuedInfo(paneId: string): { from: string; note: string } | null {
  return continued.get(paneId) ?? null;
}

/** A pane as `continue-on-next` answers it (the new pane row). */
export interface ContinuedPane {
  id: string;
  [k: string]: unknown;
}

/** The strip's slot in a pane: renders it only for an Iron-Proxy Claude
 *  account with a limit the user has not dismissed. */
export function PaneContinue({
  paneId,
  accounts,
  limit,
  onOpened,
}: {
  paneId: string;
  accounts: PaneAccounts | null | undefined;
  limit: PaneLimit | null | undefined;
  onOpened: (pane: ContinuedPane) => void;
}) {
  useStoreVersion();
  const account = continuableAccount(accounts);
  if (!account || !limit || dismissedLine.get(paneId) === limitKey(limit)) return null;
  return (
    <ContinueStrip
      paneId={paneId}
      account={account}
      limit={limit}
      onOpened={onOpened}
      onDismiss={() => {
        dismissedLine.set(paneId, limitKey(limit));
        changed();
      }}
    />
  );
}

function ContinueStrip({
  paneId,
  account,
  limit,
  onOpened,
  onDismiss,
}: {
  paneId: string;
  account: PaneAccount;
  limit: PaneLimit;
  onOpened: (pane: ContinuedPane) => void;
  onDismiss: () => void;
}) {
  // Mounted only while the strip is shown, so this read happens only then.
  const { data: snap } = usePolledApi<unknown>(IRON_PROXY_LIGHT, LAUNCH_ACCOUNTS_POLL_MS);
  const next = nextAccount(snap, account.id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const resets = limitResetWords(account, limit);

  async function go() {
    setBusy(true);
    setError(null);
    try {
      const res = await post<unknown>(`/terminals/${encodeURIComponent(paneId)}/continue-on-next`);
      if (!isContinueAnswer(res)) throw new Error("The daemon did not open a new pane.");
      rememberContinued(res.pane.id, res.from?.title || account.title, continueNote(res));
      // This pane is still at its limit: it has been answered, so it stops asking.
      dismissedLine.set(paneId, limitKey(limit));
      changed();
      onOpened(res.pane);
    } catch (err) {
      // The daemon's one plain sentence (409: no free account, this PC's login…).
      setError(err instanceof ApiError || err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      id={`pane-continue-${paneId}`}
      data-testid="continue-strip"
      className="flex shrink-0 flex-wrap items-center gap-2 border-b border-amber-500/25 bg-amber-500/[0.07] px-3 py-1 text-[11px] text-amber-200"
    >
      <ArrowRightLeft size={12} className="shrink-0" />
      <span className="min-w-0 flex-1">
        Claude&apos;s account “{account.title}” {limitWords(limit)}
        {resets ? ` (${resets})` : ""}.
        {next.kind === "wait" &&
          (next.until
            ? ` No other Claude account is free — “${next.account.title}” frees at ${localTime(next.until)}.`
            : ` No other Claude account is free right now.`)}
        {next.kind === "none" &&
          " No other Claude account can take over — add or sign one in on the Connections page."}
      </span>
      {(next.kind === "next" || next.kind === "unknown") && (
        <button
          type="button"
          data-testid={`pane-continue-go-${paneId}`}
          onClick={() => void go()}
          disabled={busy}
          title="Opens a new pane on that account in this folder and carries the conversation over when it can"
          className="shrink-0 rounded-md border border-amber-400/50 px-2 py-0.5 font-medium text-amber-100 transition-colors hover:bg-amber-500/15 disabled:opacity-60"
        >
          {busy ? (
            <span className="flex items-center gap-1">
              <Loader2 size={11} className="animate-spin" /> Opening…
            </span>
          ) : next.kind === "next" ? (
            `Continue on “${next.account.title}”`
          ) : (
            "Continue on the next account"
          )}
        </button>
      )}
      <button
        type="button"
        onClick={onDismiss}
        className="shrink-0 rounded-md px-1.5 py-0.5 text-amber-200/80 transition-colors hover:bg-amber-500/10"
      >
        Dismiss
      </button>
      {(next.kind === "next" || next.kind === "unknown") && (
        <span
          data-testid={`pane-continue-privacy-${paneId}`}
          title="Claude Code sends the conversation to Anthropic under the account it runs on. The copy stays in that account's own history; nothing is deleted from either."
          className="basis-full text-[10.5px] text-amber-200/80"
        >
          Your conversation is copied to{" "}
          {next.kind === "next" ? `“${next.account.title}”` : "the next account"} and continues
          there (sent to Anthropic under that account); “{account.title}” keeps its copy.
        </span>
      )}
      {error && (
        <span role="alert" data-testid={`pane-continue-error-${paneId}`} className="basis-full text-rose-200">
          {error}
        </span>
      )}
    </div>
  );
}

/** The new pane's "Continued from “Work Max”" line, with the answer's note,
 *  until it is dismissed. Renders nothing for any other pane. */
export function ContinuedFromLine({ paneId }: { paneId: string }) {
  useStoreVersion();
  const info = continued.get(paneId);
  if (!info) return null;
  return (
    <div
      id={`pane-continued-${paneId}`}
      className="flex shrink-0 items-center gap-2 border-b border-accent/20 bg-accent/[0.06] px-3 py-1 text-[11px] text-accent-soft"
    >
      <ArrowRightLeft size={12} className="shrink-0" />
      <span className="min-w-0 flex-1">
        Continued from “{info.from}” — {info.note}
      </span>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => {
          continued.delete(paneId);
          changed();
        }}
        className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-accent-soft/80 hover:bg-accent/15"
      >
        <X size={12} />
      </button>
    </div>
  );
}
