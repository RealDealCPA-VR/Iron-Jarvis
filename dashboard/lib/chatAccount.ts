/**
 * The ACCOUNT a plain chat runs on (v1.424.0, wave 4 D6).
 *
 * The audit (gap D6): only a Build coding session could be put on one
 * Iron-Proxy account; every chat turn leased Iron-Proxy's FIRST FREE account.
 * Now the chat page's model menu lists, under Claude Code / Codex / Grok, the
 * same account entries the coding session's menu has (lib/harnessModelMenu
 * `cliAccounts` — ONE rule), plus "Any free account" (Iron-Proxy picks, as
 * before). The pick rides every turn as `account` (the daemon checks it —
 * `iron_proxy/choice` — and pins every lease of the turn to it; a refusal is
 * a 409 in words) and is kept in the chat's setup.
 *
 * Iron-Proxy off, an older Iron Jarvis (a 404), or no account for a CLI: no
 * entries — the menu is exactly what it was. Pure except
 * {@link readChatAccounts} (one GET, every failure answers null).
 */

import {
  ACCOUNT_VENDORS,
  fetchAccounts,
  type AccountVendor,
  type AccountsView,
  type BuildAccount,
} from "@/lib/buildAccounts";
import { cliAccounts } from "@/lib/harnessModelMenu";
import type { ChatAccounts } from "@/components/chat/ModelMenu";

/** The chat's chosen account: its id, its name (for the chip when the list
 *  is not read) and the CLI provider it serves. */
export interface ChatAccountPick {
  id: string;
  name: string;
  provider: string;
}

/** vendor -> the CLI provider its accounts serve (the daemon's VENDORS). */
export const CLI_OF_VENDOR: Record<AccountVendor, string> = {
  claude: "claude-cli",
  codex: "codex-cli",
  grok: "grok-cli",
};

/** The vendor a CLI provider's accounts belong to, else null. */
export function vendorOfCli(provider: string): AccountVendor | null {
  return ACCOUNT_VENDORS.find((v) => CLI_OF_VENDOR[v] === provider) ?? null;
}

/** The note under a CLI's accounts when Iron-Proxy cannot serve a chat's. */
export const CHAT_ACCOUNT_OFF_NOTE =
  "Iron-Proxy is off, so this chat's account cannot be used — turn it on in Settings › Connections, or choose Any free account.";
export const CHAT_ACCOUNT_UPDATE_NOTE = "Iron-Proxy needs an update to keep a chat on one account.";

function accountIn(view: AccountsView | null | undefined, id: string): BuildAccount | null {
  if (!view || !id) return null;
  for (const v of ACCOUNT_VENDORS) {
    const hit = view.groups[v].find((a) => a.id === id);
    if (hit) return hit;
  }
  return null;
}

/** The chat's own account as a list row: the list's (its live state) when it
 *  holds it, else the pick's record ("missing" when the list was read and
 *  does not hold it; "unknown" when no list was read). */
function currentRow(view: AccountsView | null | undefined, pick: ChatAccountPick | null): BuildAccount | null {
  if (!pick) return null;
  const listed = accountIn(view, pick.id);
  if (listed) return listed;
  const vendor = vendorOfCli(pick.provider);
  if (!vendor) return null;
  return {
    id: pick.id,
    name: pick.name || pick.id,
    isDefault: false,
    vendor,
    vendorLabel: "",
    planLabel: "",
    status: view ? "missing" : "unknown",
    resetsAt: null,
    limitKind: "",
    usage: null,
  };
}

/**
 * What the chat's model menu draws (ModelMenu `chatAccounts`), or undefined
 * when there is nothing to draw — no list and no chosen account: the menu is
 * then exactly the menu it was.
 */
export function chatAccountsFor(
  view: AccountsView | null | undefined,
  pick: ChatAccountPick | null,
  now: Date = new Date(),
): ChatAccounts | undefined {
  const current = currentRow(view, pick);
  const byProvider = cliAccounts(view ?? null, current, now);
  const any = [...byProvider.values()].some((a) => a && a.length > 0);
  if (!any) return undefined;
  const ip = view?.ironProxy;
  const note = ip && !ip.enabled ? CHAT_ACCOUNT_OFF_NOTE : ip?.needsUpdate ? CHAT_ACCOUNT_UPDATE_NOTE : "";
  return { byProvider, current: pick?.id ?? "", note };
}

/**
 * The chat's account after a model pick:
 *
 * * picked under an account (`account` is its id): that account;
 * * picked under "Any free account" (`account` is ""): none;
 * * picked anywhere else (Recent, the typed filter, the default model, a row
 *   with no accounts — `account` undefined): the account it had, while it
 *   serves the picked provider; else none (an account never rides a model
 *   of another vendor — the daemon would refuse it).
 */
export function accountAfterPick(
  prev: ChatAccountPick | null,
  value: string,
  account: string | undefined,
  view: AccountsView | null | undefined,
): ChatAccountPick | null {
  const provider = value.includes("::") ? value.slice(0, value.indexOf("::")) : "";
  if (account === "") return null;
  if (account) {
    const row = accountIn(view, account);
    return { id: account, name: row?.name ?? "", provider };
  }
  return prev && prev.provider === provider ? prev : null;
}

/** The `account` a turn sends: the chat's, only for a turn on the provider
 *  it serves ("" otherwise — "Try again with…" another model sends none). */
export function accountForTurn(pick: ChatAccountPick | null, choice: string): string {
  if (!pick) return "";
  const provider = choice.includes("::") ? choice.slice(0, choice.indexOf("::")) : "";
  return provider && provider === pick.provider ? pick.id : "";
}

/** The account list for the chat's menu: null when this Iron Jarvis has none
 *  (a 404) or it cannot be read — never an error on the page. */
export async function readChatAccounts(): Promise<AccountsView | null> {
  try {
    return await fetchAccounts();
  } catch {
    return null;
  }
}
