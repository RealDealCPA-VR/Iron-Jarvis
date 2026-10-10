"use client";

/**
 * Build panes on Iron-Proxy accounts (v1.302.0) — the two pieces TerminalPane
 * renders, kept here so they can be mounted in a test (TerminalPane drags
 * xterm into jsdom and is source-pinned instead):
 *
 *  - `PaneAccountChip`: "Claude · Work Max" in the pane header. Amber when that
 *    account is parked (with "until 3:00 PM") or needs signing in, muted red
 *    when it is gone. Its tooltip says the account is fixed for the pane's life
 *    and how to switch. Renders nothing for a pane without `accounts`.
 *  - `LaunchAccountRows`: under a Claude Code / Codex / Grok row of the Launch
 *    menu, one "as <account>" row per Iron-Proxy CLI account (parked /
 *    needs-sign-in disabled, the reason as tooltip) and "as this PC's login".
 *    The pane's OWN account types into this pane exactly as the plain row
 *    does; any other account asks the daemon for a NEW pane started on it
 *    (`POST /terminals/launch`) — a running CLI cannot change accounts.
 */

import { useState, type ReactNode } from "react";
import { Laptop, Loader2, UserRound } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import type { IronProxyAccount } from "@/lib/ironProxy";
import {
  isPaneOwnAccount,
  launchBody,
  offerRowState,
  openedPaneId,
  paneAccountBadge,
  type AccountTone,
  type PaneAccount,
  type PaneAccounts,
} from "@/lib/paneAccounts";

const TONE_CLASS: Record<AccountTone, string> = {
  plain: "border-white/10 bg-white/[0.04] text-zinc-300",
  amber: "border-tone-warn/30 bg-tone-warn/10 text-tone-warn",
  red: "border-tone-danger/20 bg-tone-danger/[0.06] text-tone-danger/80",
};

export function PaneAccountChip({
  paneId,
  accounts,
  agentCli,
}: {
  paneId: string;
  accounts: PaneAccounts | null;
  agentCli?: string | null;
}) {
  const badge = paneAccountBadge(accounts, agentCli);
  if (!badge) return null;
  return (
    <span
      id={`pane-account-${paneId}`}
      data-tone={badge.tone}
      title={badge.tooltip}
      onMouseDown={(e) => e.stopPropagation()}
      className={`inline-flex max-w-[12rem] shrink-0 items-center gap-1 truncate rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${TONE_CLASS[badge.tone]}`}
    >
      <UserRound size={10} className="shrink-0" />
      <span className="truncate">{badge.label}</span>
    </span>
  );
}

/** A pane row as `POST /terminals/launch` answers it (at least its id). */
export interface OpenedPane {
  id: string;
  [k: string]: unknown;
}

export function LaunchAccountRows({
  cliId,
  cliLabel,
  paneId,
  paneAccount,
  accounts,
  onTypeHere,
  onOpened,
}: {
  cliId: string;
  cliLabel: string;
  paneId: string;
  /** What this pane runs that provider on (its `accounts[provider]`). */
  paneAccount: PaneAccount | null | undefined;
  /** The provider's usable Iron-Proxy CLI accounts (`launchOffer`). */
  accounts: IronProxyAccount[];
  /** The pane's own account: type the CLI into THIS pane, exactly as today. */
  onTypeHere: () => void;
  /** Another account: the daemon started a new pane — show and focus it. */
  onOpened: (pane: OpenedPane) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function choose(choiceId: string | null) {
    if (isPaneOwnAccount(paneAccount, choiceId)) {
      onTypeHere();
      return;
    }
    const key = choiceId ?? "default";
    setBusy(key);
    setError(null);
    try {
      const res = await post<unknown>("/terminals/launch", launchBody(cliId, choiceId, paneId));
      const id = openedPaneId(res);
      if (!id) throw new Error("The daemon did not open a new pane.");
      onOpened({ ...(res as object), id } as OpenedPane);
    } catch (err) {
      // The daemon's one plain sentence (409: parked / needs sign-in / missing).
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  const row = (
    key: string,
    choiceId: string | null,
    title: string,
    word: string | null,
    disabled: boolean,
    why: string | null,
    icon: ReactNode,
  ) => {
    const own = isPaneOwnAccount(paneAccount, choiceId);
    return (
      <button
        key={key}
        type="button"
        data-testid={`launch-as-${cliId}-${key}`}
        disabled={disabled || busy !== null}
        title={
          why ??
          (own
            ? `This pane already runs ${cliLabel} on this account — types it here.`
            : `Opens a new pane next to this one with ${cliLabel} started on this account.`)
        }
        onClick={() => void choose(choiceId)}
        className="flex w-full items-center justify-between gap-2 rounded-lg py-1 pl-7 pr-2 text-left text-[11px] text-zinc-300 transition-colors hover:bg-accent/10 hover:text-accent-soft disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent disabled:hover:text-zinc-300"
      >
        <span className="flex min-w-0 items-center gap-1.5">
          {busy === key ? <Loader2 size={11} className="shrink-0 animate-spin" /> : icon}
          <span className="truncate">as {title}</span>
        </span>
        <span className="shrink-0 text-[10px] text-zinc-500">
          {own ? "this pane" : disabled ? word : [word, "new pane"].filter(Boolean).join(" · ")}
        </span>
      </button>
    );
  };

  return (
    <div data-testid={`launch-accounts-${cliId}`} className="pb-1">
      {accounts.map((a) => {
        const s = offerRowState(a);
        return row(
          a.id,
          a.id,
          a.title,
          s.word,
          s.disabled,
          s.why,
          <UserRound size={11} className="shrink-0 text-accent-soft/70" />,
        );
      })}
      {row(
        "default",
        null,
        "this PC's login",
        null,
        false,
        null,
        <Laptop size={11} className="shrink-0 text-zinc-500" />,
      )}
      {error && (
        <div
          role="alert"
          data-testid={`launch-as-error-${cliId}`}
          className="mx-2 mt-1 text-[11px] leading-relaxed text-tone-danger"
        >
          {error}
        </div>
      )}
    </div>
  );
}
