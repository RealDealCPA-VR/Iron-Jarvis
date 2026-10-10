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
import { ArrowRightLeft, LogIn, Loader2, RotateCcw, X } from "lucide-react";
import { ApiError, get, post } from "@/lib/api";
import { usePolledApi } from "@/lib/useApi";
import { localTime, terminalsHref } from "@/lib/ironProxy";
import {
  IRON_PROXY_LIGHT,
  LAUNCH_ACCOUNTS_POLL_MS,
  continuableAccount,
  continueNote,
  handoffNote,
  openedPaneId,
  resumeFailedKey,
  resumeFailedWords,
  signInNeededKey,
  type SignInNeeded,
  type ContinuedFrom,
  type HandoffAnswer,
  type ResumeFailed,
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
/** v1.303.2: the resume-failed episode (its `since`) dismissed per pane. */
const dismissedFailure = new Map<string, string>();
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
  dismissedFailure.clear();
  dismissedSignedIn.clear();
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
      className="flex shrink-0 flex-wrap items-center gap-2 border-b border-tone-warn/25 bg-tone-warn/[0.07] px-3 py-1 text-[11px] text-tone-warn"
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
          className="shrink-0 rounded-md border border-tone-warn/50 px-2 py-0.5 font-medium text-tone-warn transition-colors hover:bg-tone-warn/15 disabled:opacity-60"
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
        className="shrink-0 rounded-md px-1.5 py-0.5 text-tone-warn/80 transition-colors hover:bg-tone-warn/10"
      >
        Dismiss
      </button>
      {(next.kind === "next" || next.kind === "unknown") && (
        <span
          data-testid={`pane-continue-privacy-${paneId}`}
          title="Claude Code sends the conversation to Anthropic under the account it runs on. The copy stays in that account's own history; nothing is deleted from either."
          className="basis-full text-[10px] text-tone-warn/80"
        >
          Your conversation is copied to{" "}
          {next.kind === "next" ? `“${next.account.title}”` : "the next account"} and continues
          there (sent to Anthropic under that account); “{account.title}” keeps its copy.
        </span>
      )}
      {error && (
        <span role="alert" data-testid={`pane-continue-error-${paneId}`} className="basis-full text-tone-danger">
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

/* ---- v1.303.2: the next account could not pick the conversation up -------- */

/**
 * A continued Claude session can be refused by the account it was carried to
 * (Anthropic is not obliged to accept another account's transcript). The
 * daemon's activity row then carries `resume_failed: {line, since}` and this
 * strip — ContinueStrip's pattern — offers the way out: start a NEW Claude
 * session on this pane's account, handed a short summary of the conversation
 * so far (`POST /terminals/{id}/start-fresh-with-handoff`). `/clear` stays the
 * user's own way to start empty. Dismiss hides it for this failure episode.
 */
export function ResumeFailedStrip({
  paneId,
  failed,
  signIn,
  accounts,
  continuedFrom,
  onOpened,
}: {
  paneId: string;
  failed: ResumeFailed | null | undefined;
  /** The activity row's `sign_in_needed`: the next account's login expired.
   *  Its own variant — a fresh start would not fix a login. */
  signIn?: SignInNeeded | null;
  accounts: PaneAccounts | null | undefined;
  /** The pane row's `continued_from` (where the conversation came from). */
  continuedFrom?: ContinuedFrom | null;
  /** A DIFFERENT pane answered: show and focus it. */
  onOpened: (pane: ContinuedPane) => void;
}) {
  useStoreVersion();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (signIn && dismissedFailure.get(paneId) !== signInNeededKey(signIn)) {
    return <SignInNeededStrip paneId={paneId} need={signIn} onOpened={onOpened} />;
  }
  if (!failed || dismissedFailure.get(paneId) === resumeFailedKey(failed)) return null;
  const key = resumeFailedKey(failed);
  const here = accounts?.anthropic?.title ? `“${accounts.anthropic.title}”` : "This account";
  const hereShort = accounts?.anthropic?.title ? `“${accounts.anthropic.title}”` : "this account";

  async function go() {
    setBusy(true);
    setError(null);
    try {
      const res = (await post<unknown>(
        `/terminals/${encodeURIComponent(paneId)}/start-fresh-with-handoff`,
      )) as HandoffAnswer | null;
      const target = res?.pane && typeof res.pane.id === "string" && res.pane.id ? res.pane.id : paneId;
      const from = continued.get(paneId)?.from ?? continuedFrom?.from_title ?? "the other account";
      continued.set(target, { from, note: handoffNote(res) });
      dismissedFailure.set(paneId, key);
      changed();
      if (target !== paneId && res?.pane) onOpened(res.pane as ContinuedPane);
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      id={`pane-resume-failed-${paneId}`}
      data-testid="resume-failed-strip"
      className="flex shrink-0 flex-wrap items-center gap-2 border-b border-tone-warn/25 bg-tone-warn/[0.07] px-3 py-1 text-[11px] text-tone-warn"
    >
      <RotateCcw size={12} className="shrink-0" />
      <span className="min-w-0 flex-1">
        {here} could not pick up this conversation ({resumeFailedWords(failed)}).
      </span>
      <button
        type="button"
        data-testid={`pane-start-fresh-${paneId}`}
        onClick={() => void go()}
        disabled={busy}
        className="shrink-0 rounded-md border border-tone-warn/50 px-2 py-0.5 font-medium text-tone-warn transition-colors hover:bg-tone-warn/15 disabled:opacity-60"
      >
        {busy ? (
          <span className="flex items-center gap-1">
            <Loader2 size={11} className="animate-spin" /> Starting…
          </span>
        ) : (
          "Start fresh with what we were doing"
        )}
      </button>
      <span className="shrink-0 text-tone-warn/70">or type /clear to start empty</span>
      <button
        type="button"
        onClick={() => {
          dismissedFailure.set(paneId, key);
          changed();
        }}
        className="shrink-0 rounded-md px-1.5 py-0.5 text-tone-warn/80 transition-colors hover:bg-tone-warn/10"
      >
        Dismiss
      </button>
      <span data-testid={`pane-start-fresh-how-${paneId}`} className="basis-full text-[10px] text-tone-warn/80">
        Iron Jarvis writes a short summary of the conversation so far (your first request, the last few
        messages, the files involved) and starts a new Claude session on {hereShort} with it.
      </span>
      {error && (
        <span
          role="alert"
          data-testid={`pane-start-fresh-error-${paneId}`}
          className="basis-full whitespace-pre-line text-tone-danger"
        >
          {error}
        </span>
      )}
    </div>
  );
}

/** The sign-in variant of the resume-failed strip: the account this pane was
 *  continued on needs its login renewed. Sign in opens Iron-Proxy's sign-in
 *  terminal for THAT account (the card's own route) and focuses it. */
function SignInNeededStrip({
  paneId,
  need,
  onOpened,
}: {
  paneId: string;
  need: SignInNeeded;
  onOpened: (pane: ContinuedPane) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      const res = await post<unknown>(`/iron-proxy/accounts/${encodeURIComponent(need.account.id)}/signin`);
      const id = openedPaneId(res);
      if (!id) throw new Error("The daemon did not open a sign-in terminal.");
      // The page adopts a FULL pane row; the sign-in answer is only its id.
      let row: ContinuedPane | undefined;
      try {
        const list = await get<{ terminals?: ContinuedPane[] }>("/terminals");
        row = list?.terminals?.find((t) => t.id === id);
      } catch {
        row = undefined;
      }
      if (row) onOpened(row);
      else window.location.assign(terminalsHref(id));
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      id={`pane-resume-failed-${paneId}`}
      data-testid="resume-failed-strip"
      data-variant="sign-in"
      className="flex shrink-0 flex-wrap items-center gap-2 border-b border-tone-warn/25 bg-tone-warn/[0.07] px-3 py-1 text-[11px] text-tone-warn"
    >
      <LogIn size={12} className="shrink-0" />
      <span className="min-w-0 flex-1">
        “{need.account.title}” needs to sign in again ({resumeFailedWords(need)}).
      </span>
      <button
        type="button"
        data-testid={`pane-sign-in-${paneId}`}
        onClick={() => void signIn()}
        disabled={busy}
        className="shrink-0 rounded-md border border-tone-warn/50 px-2 py-0.5 font-medium text-tone-warn transition-colors hover:bg-tone-warn/15 disabled:opacity-60"
      >
        {busy ? (
          <span className="flex items-center gap-1">
            <Loader2 size={11} className="animate-spin" /> Opening…
          </span>
        ) : (
          "Sign in"
        )}
      </button>
      <button
        type="button"
        onClick={() => {
          dismissedFailure.set(paneId, signInNeededKey(need));
          changed();
        }}
        className="shrink-0 rounded-md px-1.5 py-0.5 text-tone-warn/80 transition-colors hover:bg-tone-warn/10"
      >
        Dismiss
      </button>
      {error && (
        <span
          role="alert"
          data-testid={`pane-sign-in-error-${paneId}`}
          className="basis-full whitespace-pre-line text-tone-danger"
        >
          {error}
        </span>
      )}
    </div>
  );
}

/* ---- v1.303.3: the sign-in pane's login is done ----------------------------- */

const dismissedSignedIn = new Set<string>();

/**
 * A sign-in pane (opened by Sign in on the Iron-Proxy card or the sign-in
 * strip) whose login finished: the activity row says `signed_in: true`. The
 * user once mistook Claude Code's one-time first-run welcome for a second
 * login, so this says what to do next and what that welcome is.
 */
export function SignedInStrip({
  paneId,
  signedIn,
}: {
  paneId: string;
  signedIn: { title: string } | null | undefined;
}) {
  useStoreVersion();
  if (!signedIn || dismissedSignedIn.has(paneId)) return null;
  const named = signedIn.title === "this account" ? "this account" : `“${signedIn.title}”`;
  return (
    <div
      id={`pane-signed-in-${paneId}`}
      data-testid="signed-in-strip"
      className="flex shrink-0 items-center gap-2 border-b border-tone-success/25 bg-tone-success/[0.07] px-3 py-1 text-[11px] text-tone-success"
    >
      <LogIn size={12} className="shrink-0" />
      <span className="min-w-0 flex-1">
        Signed in to {named}. Type <code className="rounded bg-white/[0.08] px-1 font-mono">claude</code> here
        to use it — Claude Code shows its one-time welcome the first time.
      </span>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => {
          dismissedSignedIn.add(paneId);
          changed();
        }}
        className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-tone-success/80 hover:bg-tone-success/15"
      >
        <X size={12} />
      </button>
    </div>
  );
}
