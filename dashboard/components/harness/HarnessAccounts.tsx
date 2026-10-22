"use client";

/**
 * The IJ harness session's ACCOUNT pieces (v1.369.0, plan Phase 6, drawn on
 * Phase 5's shapes in lib/buildAccounts):
 *
 *  - `AccountChip` — in the session header: the vendor's mark and the
 *    account's name. It opens the account list, grouped "Claude Code
 *    accounts" / "Codex accounts", one card per account: its name, a Default
 *    badge on the default, vendor + plan ("Claude Code · Max") and ONE status
 *    line (Ready, "Limit reached · resets 4:12 PM", or a usage bar
 *    "38% · 7 days" — the window nearest its limit, only a percent the daemon
 *    sent), with "Make default". Iron-Proxy's own note (off, not read yet,
 *    needs an update) is said at the top of the list.
 *  - `AccountLimitCard` — in the conversation when the session's account
 *    could not serve: the mockup's words for a limit (the account and its
 *    reset time, Resume when ready — Cancel while it waits — and Choose
 *    account); sign-in, a removed account and a turned-off one in their own
 *    words, with no reset time and no Resume unless the daemon says so.
 *
 * An Iron Jarvis without the account list (a 404) gets no chip at all — the
 * older-daemon rule; nothing here guesses an account.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Bot, Check, ChevronDown, CircleAlert, Loader2 } from "lucide-react";
import { ProviderMark } from "@/components/BrandGlyph";
import {
  ACCOUNT_VENDORS,
  CHOOSE_ACCOUNT,
  GROUP_TITLES,
  RESUME_WHEN_READY,
  accountStatusLine,
  limitCardLine,
  limitCardWords,
  resetWords,
  vendorPlanLine,
  type AccountLimit,
  type AccountVendor,
  type AccountsView,
  type BuildAccount,
} from "@/lib/buildAccounts";

/** The vendor's mark: Claude's real one, a quiet bot for Codex (OpenAI's mark
 *  was removed from Simple Icons at its request — components/BrandGlyph). */
export function VendorMark({ vendor, size = 14 }: { vendor: AccountVendor | null; size?: number }) {
  return (
    <span aria-hidden className="inline-grid shrink-0 place-items-center">
      {vendor === "claude" ? (
        <ProviderMark id="claude-cli" size={size} fallback={<Bot size={size} />} />
      ) : (
        <Bot size={size} className="text-zinc-300" />
      )}
    </span>
  );
}

/** A status line that asks the user to act (warn ink). */
function warns(kind: string): boolean {
  return kind === "limit" || kind === "sign_in" || kind === "off" || kind === "missing";
}

/** One account card in the list. */
export function AccountCard({
  account,
  current,
  onPick,
  onMakeDefault,
  busy,
}: {
  account: BuildAccount;
  /** The session runs on this account. */
  current: boolean;
  /** Pick it for the session (absent = the list only shows). */
  onPick?: () => void;
  onMakeDefault?: () => void;
  busy?: boolean;
}) {
  const line = accountStatusLine(account);
  return (
    <div
      data-testid={`account-card-${account.id}`}
      data-current={current ? "true" : undefined}
      className={`rounded-xl px-3 py-2.5 transition-colors ${current ? "bg-accent/[0.08]" : "hover:bg-white/[0.04]"}`}
    >
      <div className="flex min-w-0 items-center gap-2">
        <VendorMark vendor={account.vendor} />
        {onPick ? (
          <button
            type="button"
            data-testid={`account-pick-${account.id}`}
            onClick={onPick}
            disabled={busy}
            className="min-w-0 flex-1 truncate text-left text-[13px] font-medium text-zinc-100 hover:text-accent-soft focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-50"
          >
            {account.name}
          </button>
        ) : (
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-zinc-100">{account.name}</span>
        )}
        {current ? <Check size={13} aria-label="This session's account" className="shrink-0 text-accent-soft" /> : null}
        {account.isDefault ? (
          <span
            data-testid="account-default-badge"
            className="shrink-0 rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[11px] text-zinc-300"
          >
            Default
          </span>
        ) : null}
      </div>
      <p data-testid="account-vendor-plan" className="mt-0.5 pl-[22px] text-[12px] text-zinc-500">
        {vendorPlanLine(account)}
      </p>
      {line.text ? (
        <div className="mt-1 pl-[22px]" data-testid="account-status" data-kind={line.kind}>
          {line.kind === "usage" && typeof line.percent === "number" ? (
            <div className="flex items-center gap-2">
              <div
                role="meter"
                aria-label={`${account.name}: ${line.text} used`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(line.percent)}
                className="h-1.5 w-24 shrink-0 overflow-hidden rounded-full bg-white/[0.08]"
              >
                <div
                  className={`h-full rounded-full ${line.percent >= 90 ? "bg-tone-warn" : "bg-accent"}`}
                  style={{ width: `${Math.max(2, Math.min(100, line.percent))}%` }}
                />
              </div>
              <span className="text-[12px] text-zinc-400">{line.text}</span>
            </div>
          ) : (
            <span className={`text-[12px] ${warns(line.kind) ? "text-tone-warn" : "text-zinc-400"}`}>{line.text}</span>
          )}
        </div>
      ) : null}
      {onMakeDefault && !account.isDefault ? (
        <button
          type="button"
          data-testid={`account-make-default-${account.id}`}
          onClick={onMakeDefault}
          disabled={busy}
          className="ml-[22px] mt-1 rounded-md px-1.5 py-0.5 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-40"
        >
          Make default
        </button>
      ) : null}
    </div>
  );
}

/** The account list, grouped by vendor, with Iron-Proxy's note on top. */
export function AccountList({
  view,
  currentId,
  onPick,
  onMakeDefault,
  busy,
  title,
}: {
  view: AccountsView;
  currentId: string | null;
  onPick?: (a: BuildAccount) => void;
  onMakeDefault: (a: BuildAccount) => void;
  busy?: boolean;
  title?: string;
}) {
  const { groups, ironProxy } = view;
  const empty = ACCOUNT_VENDORS.every((v) => groups[v].length === 0);
  return (
    <div data-testid="account-list" className="space-y-3">
      {title ? <p className="px-1 text-[12px] text-zinc-500">{title}</p> : null}
      {ironProxy?.note ? (
        <p
          data-testid="account-iron-proxy-note"
          className={`px-1 text-[12px] ${ironProxy.needsUpdate ? "text-tone-warn" : "text-zinc-400"}`}
        >
          {ironProxy.note}
        </p>
      ) : null}
      {empty ? (
        ironProxy?.note ? null : (
          <p className="px-1 text-[13px] text-zinc-400">
            No Claude Code or Codex accounts yet. Add one on the Connections page (Iron-Proxy).
          </p>
        )
      ) : (
        ACCOUNT_VENDORS.filter((v) => groups[v].length > 0).map((v) => (
          <section key={v} aria-label={GROUP_TITLES[v]} data-testid={`account-group-${v}`}>
            <h3 className="mb-1 px-1 text-[12px] font-medium text-zinc-400">{GROUP_TITLES[v]}</h3>
            <div className="space-y-0.5">
              {groups[v].map((a) => (
                <AccountCard
                  key={a.id}
                  account={a}
                  current={a.id === currentId}
                  onPick={onPick ? () => onPick(a) : undefined}
                  onMakeDefault={() => onMakeDefault(a)}
                  busy={busy}
                />
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}

/** The header chip and its list. */
export function AccountChip({
  account,
  view,
  onPick,
  onMakeDefault,
  onOpenChange,
  busy,
  note,
}: {
  /** The account the session runs on, or null (none chosen yet). */
  account: BuildAccount | null;
  view: AccountsView;
  onPick: (a: BuildAccount) => void;
  onMakeDefault: (a: BuildAccount) => void;
  /** Told when the list opens or closes (the caller reads it again while open). */
  onOpenChange?: (open: boolean) => void;
  busy?: boolean;
  /** One line under the list (a save that failed, in words). */
  note?: string | null;
}) {
  const [open, setOpenState] = useState(false);
  const setOpen = (v: boolean) => {
    setOpenState(v);
    onOpenChange?.(v);
  };
  const boxRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  const line = account ? accountStatusLine(account) : null;
  const warn = !!line && warns(line.kind);
  return (
    <div ref={boxRef} className="relative flex min-w-0 items-center gap-1.5">
      <button
        type="button"
        data-testid="account-chip"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-haspopup="true"
        onClick={() => setOpen(!open)}
        title={
          account
            ? [account.name, vendorPlanLine(account), line?.text].filter(Boolean).join(" · ")
            : "Choose an account"
        }
        className={`inline-flex h-7 max-w-[14rem] items-center gap-1.5 rounded-lg px-2 text-[12px] transition-colors hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 ${
          warn ? "text-tone-warn" : "text-zinc-300"
        }`}
      >
        <VendorMark vendor={account?.vendor ?? null} size={13} />
        <span data-testid="account-chip-name" className="min-w-0 truncate">
          {account ? account.name : "Choose an account"}
        </span>
        {warn ? <CircleAlert size={12} aria-hidden className="shrink-0" /> : null}
        <ChevronDown size={12} aria-hidden className="shrink-0 opacity-70" />
      </button>
      {/* An account that cannot serve says so beside its name, before the
          user types: a NEW session starts on the Default account even when
          that account is at its limit (the daemon's choice, v1.368.0), and
          its first reply is then the limit card. */}
      {warn && line ? (
        <span
          data-testid="account-chip-status"
          title={line.text}
          className="shrink-0 whitespace-nowrap text-[12px] text-tone-warn"
        >
          {/* The short words only ("Limit reached"): the reset time is in
              the tooltip and on the limit card, and a long line here
              squeezed the session's title to a few letters. */}
          {line.text.split(" · ")[0]}
        </span>
      ) : null}
      {open ? (
        <div
          id={listId}
          data-testid="account-menu"
          className="absolute right-0 top-full z-30 mt-1 max-h-[min(28rem,70vh)] w-[min(20rem,calc(100vw-2rem))] overflow-y-auto rounded-xl border border-white/10 bg-zinc-900 p-2 shadow-lg shadow-black/40"
        >
          <AccountList
            view={view}
            currentId={account?.id ?? null}
            onPick={(a) => {
              onPick(a);
              setOpen(false);
            }}
            onMakeDefault={onMakeDefault}
            busy={busy}
          />
          {note ? (
            <p role="status" className="mt-2 px-1 text-[12px] text-tone-warn">
              {note}
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** The card in the conversation when the account could not serve. */
export function AccountLimitCard({
  limit,
  waiting,
  waitingResetsAt,
  busy,
  error,
  onResume,
  onCancelWait,
  onChoose,
  chooser,
}: {
  limit: AccountLimit;
  /** "Resume when ready" is armed (the daemon waits for the reset). */
  waiting: boolean;
  /** The armed wait's own reset time, when the daemon sent one. */
  waitingResetsAt?: string | null;
  busy?: boolean;
  error?: string | null;
  onResume: () => void;
  onCancelWait: () => void;
  onChoose: () => void;
  /** The account list, drawn under the card after "Choose account". */
  chooser?: ReactNode;
}) {
  const words = limitCardWords(limit);
  const when = resetWords(waitingResetsAt ?? limit.resetsAt);
  return (
    <div
      role="status"
      data-testid="account-limit-card"
      data-kind={limit.kind}
      className="space-y-2 rounded-2xl bg-tone-warn/[0.06] px-4 py-3 shadow-[0_0_0_0.5px_rgb(var(--tone-warn)/0.45)]"
    >
      <p className="text-[13px] leading-relaxed text-zinc-100">
        <span className="font-medium">{words.title}</span> {words.body}
      </p>
      <p data-testid="account-limit-line" className="text-[12px] italic text-zinc-400">
        {limitCardLine(limit)}
      </p>
      {waiting ? (
        <div className="flex flex-wrap items-center gap-2">
          <span data-testid="account-limit-waiting" className="inline-flex items-center gap-1.5 text-[12px] text-zinc-300">
            <Loader2 size={12} className="animate-spin" aria-hidden />
            {when ? `Waiting for the reset (${when}), then it continues.` : "Waiting for it to be ready, then it continues."}
          </span>
          <button
            type="button"
            data-testid="account-limit-cancel"
            onClick={onCancelWait}
            disabled={busy}
            className="rounded-md px-2 py-1 text-[12px] text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 disabled:opacity-40"
          >
            Cancel
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {words.canResume ? (
            <button
              type="button"
              data-testid="account-limit-resume"
              onClick={onResume}
              disabled={busy}
              className="btn-accent text-xs"
            >
              {RESUME_WHEN_READY}
            </button>
          ) : null}
          <button
            type="button"
            data-testid="account-limit-choose"
            onClick={onChoose}
            disabled={busy}
            className={words.canResume ? "btn-ghost text-xs" : "btn-accent text-xs"}
          >
            {CHOOSE_ACCOUNT}
          </button>
        </div>
      )}
      {error ? (
        <p role="alert" className="text-[12px] text-tone-danger">
          {error}
        </p>
      ) : null}
      {chooser}
    </div>
  );
}
