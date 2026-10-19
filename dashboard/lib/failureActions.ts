/**
 * THE PRESS BESIDE A FAILED TURN (v1.355.0, product-readiness G29).
 *
 * Since v1.354.0 the chat's failure line says the daemon's plain sentence
 * (lib/failureLine: `title. fix`). That sentence told a signed-out Claude
 * Code user to "Press Sign in beside Claude Code in Settings › Connections"
 * while the same Sign in press exists right here (lib/cliSignIn, G33), and a
 * refused key had no link at all. This module decides the ONE press that
 * fixes the failure, from typed facts only — never a string guess:
 *
 *  - the daemon's own `fix_kind` / `fix_provider` on the error
 *    (`chat_turn.fix_action`, read off the same typed fields the headline
 *    comes from): "sign_in" + the CLI, or "key" + the provider;
 *  - else the failed turn's provider row on /health (or the CLI row it is
 *    served through, `inherited_from`) saying the CLI is installed and
 *    `signed_in: false` — the same rule the Accounts row and the composer's
 *    preflight note read (lib/onboarding.cliDoorState).
 *
 * When a press is offered the line reads the headline alone ("Claude Code
 * isn't signed in"): the press does what the fix sentence said to go and do
 * somewhere else. Without one the line is exactly the v1.354.0 sentence.
 *
 * Its own file (not lib/api.ts, which ~70 test files mock wholesale) and
 * React-free: it reads the error by SHAPE, never by class, like failureLine.
 */

import type { Health } from "@/lib/types";
import { healthRow, SUBSCRIPTION_CLIS, type SubscriptionCli } from "@/lib/onboarding";

/** What fixes the failure: sign in to a subscription CLI, or update a key. */
export type FailureAction =
  | { kind: "sign_in"; cli: SubscriptionCli }
  | { kind: "key"; provider: string };

/** The link a refused key gets (Settings › Connections, where keys live). */
export const KEY_LINK_WORDS = "Update the key in Connections";
export const KEY_LINK_HREF = "/connections";

/** Said under the line after a failed turn put the typed message back. */
export const DRAFT_BACK_WORDS = "Your message is back in the box.";

/** The fold that holds the server's own words (masked by the daemon). */
export const DETAILS_WORDS = "Details";

/** The press's words: "Sign in to Claude Code". */
export function signInLabel(cli: Pick<SubscriptionCli, "name">): string {
  return `Sign in to ${cli.name}`;
}

/** The fields a failed turn may carry (`StreamError` has them all). */
export interface FailureActionFields {
  fixKind?: unknown;
  fixProvider?: unknown;
}

function text(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

/** What the failure line keeps of a failed turn's error, read by shape (a
 *  `StreamError`, a test's stand-in, or anything else): the headline and
 *  the press fields, "" when absent. */
export function failureFacts(e: unknown): { title: string; fixKind: string; fixProvider: string } {
  const f = e && typeof e === "object" ? (e as { title?: unknown } & FailureActionFields) : {};
  return { title: text(f.title), fixKind: text(f.fixKind), fixProvider: text(f.fixProvider) };
}

function cliOf(provider: string): SubscriptionCli | undefined {
  return SUBSCRIPTION_CLIS.find((c) => c.provider === provider);
}

/**
 * The subscription CLI behind *provider* that /health says is installed and
 * NOT signed in — the provider's own row, else the CLI row a keyless API
 * provider is served through (`inherited_from`). Only an explicit
 * `signed_in: false` counts: null is "not checked yet", never signed out.
 */
export function signedOutCliFor(
  health: Health | null | undefined,
  provider: string,
): SubscriptionCli | undefined {
  const p = text(provider);
  if (!p) return undefined;
  const row = healthRow(health, p);
  const via = row?.inherited_from ? healthRow(health, row.inherited_from) : undefined;
  for (const r of [row, via]) {
    if (!r) continue;
    const cli = cliOf(r.provider);
    if (cli && r.installed === true && r.signed_in === false) return cli;
  }
  return undefined;
}

/**
 * The press for a failed turn, or null. *turnProvider* is the provider the
 * turn ran on (the pick, else the default — lib/providerFallback
 * `providerTrouble(...).provider`).
 */
export function failureAction(
  err: FailureActionFields | null | undefined,
  health: Health | null | undefined,
  turnProvider: string,
): FailureAction | null {
  const kind = text(err?.fixKind);
  const provider = text(err?.fixProvider);
  if (kind === "sign_in") {
    const cli = cliOf(provider) ?? signedOutCliFor(health, turnProvider);
    if (cli) return { kind: "sign_in", cli };
  }
  if (kind === "key" && provider) return { kind: "key", provider };
  const cli = signedOutCliFor(health, turnProvider);
  return cli ? { kind: "sign_in", cli } : null;
}
