/**
 * The IJ harness's MODEL MENU, one group per COMPANY (v1.376.0).
 *
 * The user (2026-10-23): "In the model selection for the IJ harness, if the
 * user has Iron-proxy in use it should list which claude code account the
 * user wants to engage. Also there is an anthropic and a claude code in the
 * same list, but those are the same company."
 *
 * The chat page's menu lists one row per PROVIDER (components/chat/ModelMenu
 * `modelProvidersOf`) and stays exactly so. A Build session picks HOW it
 * reaches a company, so its menu reads (ModelMenu `companies`):
 *
 *   Claude (Anthropic)                      ← the company's group
 *     Claude Code      cloud · on your plan ← a way to reach it
 *       Work · Max                          ← one entry per Iron-Proxy account
 *         38% used · 7 days
 *       Personal · Pro
 *         Limit reached · resets 4:12 PM
 *     Anthropic API    cloud · pay per use  ← only for a real API key
 *   OpenAI
 *     Codex            cloud · on your plan
 *     OpenAI API       cloud · pay per use
 *   Ollama …                                ← every other provider as today
 *
 * NO DUPLICATE: a keyless Anthropic (or OpenAI) served through the signed-in
 * CLI (`inherited_from` on a /models row) IS that CLI — its rows are listed
 * once, under the CLI's way (a model the CLI already lists is dropped; one it
 * does not is listed there too), and a pick of it reads as the CLI's
 * (`choice`, Recent). An API KEY keeps its own pay-per-use way.
 *
 * ACCOUNTS: when the session's account list (GET /build/accounts,
 * lib/buildAccounts) holds accounts for a CLI's vendor, that way shows ONE
 * ENTRY PER ACCOUNT — name, plan, state in words (`accountMenuState`) — each
 * opening that CLI's models; a pick there names the account
 * (`MenuMatch.account`), and HarnessSession switches the account and the
 * model in one request. The session's own account is always listed and
 * marked; another account that cannot serve (signed out, turned off,
 * removed) is listed but cannot be picked, with its reason. No list (not
 * read yet, a 404 from an older Iron Jarvis, Iron-Proxy off) or no account
 * for that vendor: the way is the one row it was ("Claude Code", this PC's
 * own sign-in).
 *
 * Pure: everything here is computed from the catalog, the account list and
 * the session's account. ModelMenu imports only this file's TYPES, so the
 * chat page's menu does not load it.
 */

import { modelProvidersOf, type ProviderRow } from "@/components/chat/ModelMenu";
import {
  accountBlockedReason,
  accountMenuState,
  type AccountMenuState,
  type AccountVendor,
  type AccountsView,
  type BuildAccount,
} from "@/lib/buildAccounts";
import { foldPlanRows, friendlyProvider, modelGroupName, modelRowName } from "@/lib/onboarding";
import type { ModelOption } from "@/lib/types";

/** One company and the two ways a Build session reaches it. */
export interface Company {
  id: "claude" | "openai";
  /** The group's name ("Claude (Anthropic)", "OpenAI") — from the provider
   *  words table (lib/onboarding FRIENDLY), never typed twice. */
  label: string;
  /** The subscription CLI (its accounts live in Iron-Proxy). */
  cli: string;
  /** The pay-per-use API. */
  api: string;
  /** The account list's vendor for the CLI. */
  vendor: AccountVendor;
}

export const COMPANIES: readonly Company[] = [
  { id: "claude", label: `Claude (${friendlyProvider("anthropic")})`, cli: "claude-cli", api: "anthropic", vendor: "claude" },
  { id: "openai", label: friendlyProvider("openai"), cli: "codex-cli", api: "openai", vendor: "codex" },
];

/** provider → its company (both ways). */
const COMPANY_OF: Record<string, Company> = Object.fromEntries(
  COMPANIES.flatMap((c) => [
    [c.cli, c],
    [c.api, c],
  ]),
);

/** What a way to reach a company is called inside its group: the CLI by its
 *  own name ("Claude Code", "Codex"), the API by the company's ("Anthropic
 *  API", "OpenAI API") — "Anthropic" alone under "Claude (Anthropic)" says
 *  nothing about how it is reached. */
export function wayLabel(provider: string, row?: { provider: string; model?: string; name?: string }): string {
  const co = COMPANY_OF[provider];
  if (co && provider === co.api) return `${friendlyProvider(co.api)} API`;
  return row ? modelGroupName(row) : modelGroupName({ provider });
}

/** One Iron-Proxy account as a menu entry. */
export interface MenuAccount {
  id: string;
  name: string;
  /** "Max" / "Pro" — "" when Iron-Proxy does not know it. */
  plan: string;
  state: AccountMenuState;
  /** The session runs on this account (it is marked; a pick under it changes
   *  only the model). */
  current: boolean;
  /** It can be switched to (the session's own is always open). */
  pickable: boolean;
  /** Why not, in words ("" when it can). */
  reason: string;
}

/** A way to reach a company: a row of its own, or (with accounts) a heading
 *  over one entry per account. */
export interface MenuWay {
  provider: string;
  label: string;
  /** KIND_BADGE key: "cli" (on your plan) / "api" (pay per use) / … */
  kind: string;
  available: boolean;
  /** null = no accounts: the way itself opens the models. */
  accounts: MenuAccount[] | null;
  /** Iron-Proxy's own note when it cannot keep a session on one account. */
  note: string;
}

export type MenuEntry =
  | { type: "provider"; row: ProviderRow }
  | { type: "company"; id: Company["id"]; label: string; ways: MenuWay[] };

/** A row of the typed filter or of Recent: the model, the value a pick sends,
 *  the account it runs on (null = none), and the words naming where. */
export interface MenuMatch {
  row: ModelOption;
  value: string;
  account: MenuAccount | null;
  group: string;
}

/** Everything ModelMenu draws in company mode. */
export interface CompanyMenu {
  /** The rows listed: the catalog with a keyless API folded into its CLI. */
  models: ModelOption[];
  entries: MenuEntry[];
  /** The session's pick as the folded list spells it. */
  choice: string;
  /** The typed filter: every model, once per account it can run on. */
  match: (query: string, max: number) => MenuMatch[];
  /** Recent picks ("provider::model"), each on the account it would run on. */
  recent: (values: readonly string[]) => MenuMatch[];
}

function menuAccount(a: BuildAccount, currentId: string, now: Date): MenuAccount {
  const current = a.id === currentId;
  const reason = current ? "" : accountBlockedReason(a);
  return {
    id: a.id,
    name: a.name,
    plan: a.planLabel,
    state: accountMenuState(a, now),
    current,
    pickable: current || reason === "",
    reason,
  };
}

/** The account entries for one vendor: the list's accounts in Iron-Proxy's
 *  order, plus the session's own when the list leaves it out (turned off,
 *  removed) — the session's account is always shown. null = no accounts. */
function accountsFor(
  vendor: AccountVendor,
  view: AccountsView | null,
  current: BuildAccount | null,
  now: Date,
): MenuAccount[] | null {
  const listed = view?.groups[vendor] ?? [];
  const currentId = current?.id ?? "";
  // Review: the session's own account is ALWAYS listed — also when its
  // vendor's list is empty (its only account turned off: the list leaves
  // off accounts out; or the list not read yet). A plain "Claude Code" row
  // there read as this PC's own sign-in, while the session's turns still run
  // on (or are refused for) that account.
  if (listed.length === 0) {
    return current && current.vendor === vendor ? [menuAccount(current, currentId, now)] : null;
  }
  const rows = listed.map((a) => menuAccount(a, currentId, now));
  if (current && current.vendor === vendor && !listed.some((a) => a.id === current.id)) {
    rows.push(menuAccount(current, currentId, now));
  }
  return rows;
}

/** The catalog with a keyless API folded into the CLI that serves it. */
export interface FoldedModels {
  /** The rows listed: a keyless API's model the CLI already lists is gone, one
   *  it does not list is listed under the CLI (its provider rewritten). */
  listed: ModelOption[];
  /** catalog "provider::model" → the folded list's spelling (every folded
   *  row, the dropped twins too). */
  alias: Record<string, string>;
  /** v1.400.7: the folded list's spelling → the catalog pair it came from,
   *  for a row the fold ADDED under a CLI ("claude-cli::claude-opus-4-8" →
   *  "anthropic::claude-opus-4-8"). A caller that sends a pick the daemon
   *  checks against GET /models (Try on…) sends the catalog pair. */
  origin: Record<string, string>;
}

/**
 * Fold a keyless API (`inherited_from` its CLI) into the CLI that serves it
 * (v1.376.0; lifted out of `companyMenu` in v1.400.7 so the "Try on…" list
 * folds by the same rule). Pure.
 */
export function foldKeylessApis(models: readonly ModelOption[]): FoldedModels {
  const cliModels = new Map<string, Set<string>>();
  /** The CLI's own first row: what the daemon says the CLI offers. */
  const cliFirst = new Map<string, ModelOption>();
  for (const m of models) {
    const co = COMPANY_OF[m.provider];
    if (co && m.provider === co.cli) {
      const set = cliModels.get(co.cli) ?? new Set<string>();
      set.add(m.model);
      cliModels.set(co.cli, set);
      if (!cliFirst.has(co.cli)) cliFirst.set(co.cli, m);
    }
  }
  const alias: Record<string, string> = {};
  const origin: Record<string, string> = {};
  const listed: ModelOption[] = [];
  /** Review: the rows a fold ADDS to a CLI, placed after the CLI's own (its
   *  plan row and its live picker first, as the chat lists the CLI). The
   *  daemon lists a keyless API's block BEFORE the CLI's, so folding in
   *  catalog order put Opus 4.8 above "Your Claude plan". */
  const added = new Map<string, ModelOption[]>();
  for (const m of models) {
    const co = COMPANY_OF[m.provider];
    const served = co && m.provider === co.api && m.inherited_from === co.cli && cliModels.has(co.cli);
    if (!co || !served) {
      listed.push(m);
      continue;
    }
    const value = `${co.cli}::${m.model}`;
    alias[`${m.provider}::${m.model}`] = value;
    if (cliModels.get(co.cli)?.has(m.model)) continue; // the same model: once, under the CLI
    // Review: a folded row RUNS on the CLI, and the daemon applies a
    // reasoning level by the SERVING adapter (providers/router
    // `_applied_reasoning`: claude-cli and codex-cli take every level for
    // every model). So its levels are the CLI's own — read off the CLI's
    // rows — never the API table's (a keyless OpenAI's gpt-4o says none; the
    // first cut also lost the chip for every folded pick: the composer looked
    // the pick up in the unfolded catalog, where claude-cli never lists it).
    const own = cliFirst.get(co.cli);
    origin[value] = `${m.provider}::${m.model}`;
    const rows = added.get(co.cli) ?? [];
    rows.push({
      ...m,
      provider: co.cli,
      inherited_from: null,
      kind: "cli",
      // v1.401.2: never "Extra" (xhigh) here: the CLI offers it PER MODEL
      // (its live picker), and this model is one the picker does not list.
      reasoning: (own?.reasoning ?? m.reasoning)?.filter((l) => l !== "xhigh"),
      reasoning_default: own?.reasoning_default ?? "",
    });
    added.set(co.cli, rows);
  }
  for (const [cli, rows] of added) {
    let last = -1;
    listed.forEach((m, i) => {
      if (m.provider === cli) last = i;
    });
    listed.splice(last + 1, 0, ...rows);
  }
  return { listed, alias, origin };
}

/**
 * The harness menu for this catalog, account list and session account.
 * `view` undefined/null (not read yet, or a 404) and an empty vendor group
 * both mean "no accounts": the CLI's way is its single row.
 */
export function companyMenu(
  models: readonly ModelOption[],
  view: AccountsView | null | undefined,
  current: BuildAccount | null,
  choice: string,
  now: Date = new Date(),
): CompanyMenu {
  // ---- fold a keyless API into the CLI that serves it, then (v1.401.2) a
  // CLI's own-default row into the model it runs ("Your Claude plan" → the
  // Opus 5.5 row): one row per model, the plan's pick marked on that model.
  const keyless = foldKeylessApis(models);
  const planFold = foldPlanRows(keyless.listed);
  const listed = planFold.models;
  const alias: Record<string, string> = { ...keyless.alias, ...planFold.alias };

  // ---- the accounts of each CLI way
  const vw = view ?? null;
  // Iron-Proxy's own note on a way it lists accounts for: it needs an update
  // (it cannot keep a session on one account), or (review) it is OFF — the
  // session's own account is still listed, and its turns are refused until
  // Iron-Proxy is on again, so the menu says why.
  const ip = vw?.ironProxy;
  const note = ip && (ip.needsUpdate || !ip.enabled) ? ip.note : "";
  const accountsOf = new Map<string, MenuAccount[] | null>();
  for (const co of COMPANIES) accountsOf.set(co.cli, accountsFor(co.vendor, vw, current, now));

  // ---- the entries, in the chat's order (local first, offline last): a
  // company takes the place of its best-ranked way.
  const rows = modelProvidersOf(listed);
  const entries: MenuEntry[] = [];
  const byCompany = new Map<string, Extract<MenuEntry, { type: "company" }>>();
  for (const row of rows) {
    const co = COMPANY_OF[row.id];
    if (!co) {
      entries.push({ type: "provider", row });
      continue;
    }
    let entry = byCompany.get(co.id);
    if (!entry) {
      entry = { type: "company", id: co.id, label: co.label, ways: [] };
      byCompany.set(co.id, entry);
      entries.push(entry);
    }
    const accounts = row.id === co.cli ? (accountsOf.get(co.cli) ?? null) : null;
    entry.ways.push({
      provider: row.id,
      label: row.id === co.api ? wayLabel(row.id) : row.label,
      kind: row.kind,
      available: row.available,
      accounts,
      note: accounts ? note : "",
    });
  }

  const wayName = (m: ModelOption): string => wayLabel(m.provider, m);

  /** The account a Recent pick of `provider` runs on: the session's own when
   *  it is that vendor's, else the vendor's Default — the daemon's rule for a
   *  model of another vendor (routes/build `_vendor_switch`). */
  const runsOn = (provider: string): MenuAccount | null => {
    const accts = accountsOf.get(provider);
    if (!accts) return null;
    const own = accts.find((a) => a.current);
    if (own) return own;
    const vendor = COMPANY_OF[provider]?.vendor;
    const def = vendor ? vw?.groups[vendor].find((a) => a.isDefault) : undefined;
    return (def && accts.find((a) => a.id === def.id)) ?? accts[0] ?? null;
  };

  const match = (query: string, max: number): MenuMatch[] => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    const out: MenuMatch[] = [];
    for (const m of listed) {
      const co = COMPANY_OF[m.provider];
      const way = wayName(m);
      const shown = modelRowName(m, models);
      // The company's name finds every way to it ("anthropic" lists Claude
      // Code too: the same company).
      const base = `${m.label ?? ""} ${m.name ?? ""} ${shown} ${m.provider} ${m.model} ${way} ${co?.label ?? ""}`.toLowerCase();
      const value = `${m.provider}::${m.model}`;
      const accts = accountsOf.get(m.provider);
      if (accts) {
        for (const a of accts) {
          if (!`${base} ${a.name}`.toLowerCase().includes(q)) continue;
          out.push({ row: m, value, account: a, group: `${way} · ${a.name}` });
          if (out.length >= max) return out;
        }
      } else if (base.includes(q)) {
        out.push({ row: m, value, account: null, group: way });
        if (out.length >= max) return out;
      }
    }
    return out;
  };

  const recent = (values: readonly string[]): MenuMatch[] => {
    const out: MenuMatch[] = [];
    const seen = new Set<string>();
    for (const raw of values) {
      const value = alias[raw] ?? raw;
      if (seen.has(value)) continue;
      seen.add(value);
      const i = value.indexOf("::");
      const provider = value.slice(0, i);
      const model = value.slice(i + 2);
      const row = listed.find((m) => m.provider === provider && m.model === model);
      if (!row) continue;
      const account = runsOn(provider);
      const way = wayName(row);
      out.push({ row, value, account, group: account ? `${way} · ${account.name}` : way });
    }
    return out;
  };

  return { models: listed, entries, choice: alias[choice] ?? choice, match, recent };
}
