/**
 * THE CLIs A PERSON SIGNS IN TO WITH ONE PRESS (v1.449.0, Wave B, G-N item 6).
 *
 * Lifted out of lib/cliSignIn so a React-free module can read the list:
 * lib/failureActions decides the press beside a failed turn (the chat page
 * and a coding session's failure line) and used to read lib/onboarding's
 * SUBSCRIPTION_CLIS — Claude Code and Codex, the plan cards — so a Grok
 * sign-in failure (`fix_kind: "sign_in", fix_provider: "grok-cli"`) got no
 * press while the Connections row and the helper row had one (v1.420.0,
 * C6.8). lib/cliSignIn re-exports everything here; nothing else changes
 * for its callers.
 *
 * No React, no lib/api: lib/failureActions is imported by both chat
 * surfaces' failure lines, and ~70 test files mock lib/api wholesale.
 */

import { SUBSCRIPTION_CLIS } from "@/lib/onboarding";

/** A CLI the user SIGNS IN to with the one press (v1.420.0: Claude Code,
 *  Codex and Grok). Wider than lib/onboarding's `SubscriptionCli` (the
 *  connect doors' plan cards, which Grok is not one of). */
export interface SignInCli {
  provider: string;
  /** The Build Launch-catalog id `POST /terminals/launch` takes. */
  cli: string;
  name: string;
  paneHint: string;
  /** Where to get it when it is not on this PC (the Connections row).
   *  Absent when no page is KNOWN to be the right one (v1.420.0 review):
   *  no link beats a wrong one. */
  installUrl?: string;
  installLabel?: string;
}

/** v1.420.0 (C6.8): Grok's sign-in press. The Launch-catalog id "grok"
 *  starts Grok's own screen in a Build terminal on this PC's login
 *  (`account: "default"`); signed out, Grok opens its sign-in there, and its
 *  `/login` command does the same (Grok's own words: "Run /login to
 *  re-authenticate"). The name is the Connections row's ("Grok CLI",
 *  lib/onboarding providerDisplay; the daemon's `cli_auth.SIGN_IN_FIX`). */
export const GROK_SIGN_IN: SignInCli = {
  provider: "grok-cli",
  cli: "grok",
  name: "Grok CLI",
  paneHint: "Grok opens its sign-in there (or type `/login`); finish in your browser.",
  // v1.420.0 review: NO install link. The Grok the app drives is xAI's own
  // CLI (~/.grok/bin/grok.exe, ~/.grok/auth.json; its README installs it
  // with x.ai's script), and the Launch catalog's page
  // (github.com/superagent-ai/grok-cli) is a DIFFERENT, key-based CLI with
  // no sign-in: a person sent there installs something this press cannot
  // sign in. No page is known to be right, so none is linked.
};

/** Every CLI with a Sign in press, in the order the rows list them. */
export const SIGN_IN_CLIS: readonly SignInCli[] = [...SUBSCRIPTION_CLIS, GROK_SIGN_IN];

/** The CLI with a Sign in press a /health provider row names ("claude-cli"
 *  → Claude Code, "grok-cli" → Grok CLI), or undefined for anything else. */
export function signInCliFor(provider: string): SignInCli | undefined {
  return SIGN_IN_CLIS.find((c) => c.provider === provider);
}

/** The older name of `signInCliFor` (lib/cliSignIn's callers). */
export const subscriptionCliFor = signInCliFor;
