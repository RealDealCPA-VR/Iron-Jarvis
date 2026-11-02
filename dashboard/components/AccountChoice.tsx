"use client";

/**
 * "Runs on account" — the Iron-Proxy account a piece of work runs on
 * (v1.424.0, wave 4 D6): a custom agent's job card and a night task.
 *
 * Shown only for a model on a subscription CLI (Claude Code, Codex, Grok —
 * or a keyless Anthropic / OpenAI the CLI serves, the catalog's
 * `inherited_from`) whose vendor has accounts in Iron-Proxy's list, or when
 * an account is already set (it stays selectable, so a select never hides
 * the truth). "No account of its own" = Iron-Proxy's normal pick, exactly as
 * before. The daemon checks the choice by its one rule (`iron_proxy/choice`)
 * and pins every lease of the work to it.
 *
 * The list is read once when the field mounts (lib/chatAccount
 * `readChatAccounts`: a 404 or any failure reads as no list — the field is
 * then not drawn).
 */

import { useEffect, useMemo, useState } from "react";
import type { AccountsView } from "@/lib/buildAccounts";
import { readChatAccounts, vendorOfCli } from "@/lib/chatAccount";
import type { ModelOption } from "@/lib/types";

/** The words for "no account": Iron-Proxy picks. */
export const NO_OWN_ACCOUNT = "No account of its own";

/** The CLI a provider is served by: itself, or (a keyless Anthropic / OpenAI)
 *  the CLI the catalog says serves it; "" otherwise. Pure. */
export function servedCli(provider: string, models: readonly ModelOption[]): string {
  if (vendorOfCli(provider)) return provider;
  const row = models.find((m) => m.provider === provider);
  const served = (row?.inherited_from as string | null | undefined) ?? "";
  return vendorOfCli(served) ? served : "";
}

export function AccountChoice({
  provider,
  models,
  value,
  onChange,
  testId,
  ariaLabel,
}: {
  /** The work's model provider ("" = the default model: no choice drawn). */
  provider: string;
  models: readonly ModelOption[];
  /** The account id ("" = none). */
  value: string;
  onChange: (account: string) => void;
  testId: string;
  ariaLabel: string;
}) {
  const [view, setView] = useState<AccountsView | null>(null);
  useEffect(() => {
    let live = true;
    void readChatAccounts().then((v) => {
      if (live) setView(v);
    });
    return () => {
      live = false;
    };
  }, []);
  const vendor = vendorOfCli(servedCli(provider, models));
  const rows = useMemo(() => (vendor && view ? view.groups[vendor] : []), [vendor, view]);
  if (!vendor || (rows.length === 0 && !value)) return null;
  return (
    <label className="block">
      <span className="mb-0.5 block text-[12px] font-medium text-zinc-500">Account</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={ariaLabel}
        data-testid={testId}
        className="field text-xs"
      >
        <option value="">{NO_OWN_ACCOUNT}</option>
        {rows.map((a) => (
          <option key={a.id} value={a.id} disabled={a.status === "off" || a.status === "missing"}>
            {a.planLabel ? `${a.name} · ${a.planLabel}` : a.name}
          </option>
        ))}
        {value && !rows.some((a) => a.id === value) ? <option value={value}>The account it was set to</option> : null}
      </select>
    </label>
  );
}
