"use client";

/**
 * Sign in to Claude Code or Codex without a terminal (v1.353.0, G33).
 *
 * Lifted out of the connect doors (components/onboarding/ConnectDoors) so
 * every page that names a subscription CLI offers the SAME press: the chat
 * empty state's door and Settings › Connections › Accounts & keys. Before
 * this, the Accounts row told a signed-out user to "run `claude` in a
 * terminal, then /login, then Re-detect" — while the door beside the chat
 * already had a Sign in button.
 *
 * Sign in = a NEW Build pane with the CLI started in it
 * (`POST /terminals/launch {cli, account: "default"}`), on THIS PC's own
 * login, never an Iron-Proxy account the user did not pick. The click is the
 * consent (the ResumeStrip rule). The CLI still asks the user to finish the
 * sign-in there, so the caller shows `paneHint` (per CLI: Claude Code waits
 * for `/login`, Codex shows its own sign-in screen) instead of pretending.
 *
 * Lives in its own file, not lib/api.ts, because ~70 test files mock that
 * module wholesale.
 */

import { useState } from "react";
import { post } from "@/lib/api";
import { SUBSCRIPTION_CLIS } from "@/lib/onboarding";

/** One CLI's sign-in press: in flight, the pane it opened (`null` = opened,
 *  id unknown), or the daemon's refusal sentence. */
export interface CliSignInState {
  busy?: boolean;
  paneId?: string | null;
  error?: string;
}

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

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
export function subscriptionCliFor(provider: string): SignInCli | undefined {
  return SIGN_IN_CLIS.find((c) => c.provider === provider);
}

/** Open the sign-in pane. Resolves the new pane's id (null when the daemon
 *  did not say); rejects with the daemon's own sentence (a 409). */
export async function launchSignIn(c: Pick<SignInCli, "cli">): Promise<string | null> {
  const res = await post<{ id?: string }>("/terminals/launch", { cli: c.cli, account: "default" });
  return res && typeof res.id === "string" ? res.id : null;
}

/**
 * v1.356.0 (G60): open a Build pane running an APP's sign-in — a set-up job
 * the daemon names on the app's Directory row (`signin.cli`, e.g.
 * "microsoft-365-signin-work"; terminals/ai_clis.SETUP_LAUNCHES). The same
 * door as `launchSignIn`, by NAME only (the route accepts only catalog ids,
 * so nothing here can type a command), and with no `account`: an app's
 * sign-in is not an AI CLI and never runs on an Iron-Proxy account. Resolves
 * the new pane's id (null when the daemon did not say); rejects with the
 * daemon's own sentence.
 */
export async function launchAppSignIn(cli: string): Promise<string | null> {
  const res = await post<{ id?: string }>("/terminals/launch", { cli });
  return res && typeof res.id === "string" ? res.id : null;
}

/** Where "Open the Build pane" goes: the pane itself when its id is known. */
export function paneHref(paneId: string | null | undefined): string {
  return paneId ? `/terminals?focus=${encodeURIComponent(paneId)}` : "/terminals";
}

/** The pane hint as it reads after "A window opens; " — the first word
 *  lower-cased when it is an instruction ("Type `/login` there…"), a name
 *  kept as it is ("Codex shows its own sign-in screen…"). */
export function paneHintAfterSemicolon(hint: string): string {
  return /^(Type|Choose|Press|Pick|Sign)\b/.test(hint) ? hint[0].toLowerCase() + hint.slice(1) : hint;
}

/** The sentence a signed-out row says BEFORE the press — a Sign in button sits
 *  right under it, so it never sends anyone to a terminal. */
export function signedOutLine(c: Pick<SignInCli, "paneHint">): string {
  return `Not signed in yet — press Sign in below. A window opens; ${paneHintAfterSemicolon(c.paneHint)}`;
}

/** A sentence with `backticked` words to type, split for rendering: odd
 *  parts are the words to type (drawn as code by the caller). */
export function codeParts(text: string): { code: boolean; text: string }[] {
  return text.split(/`([^`]+)`/).map((part, i) => ({ code: i % 2 === 1, text: part }));
}

/**
 * The per-CLI sign-in press, as a hook. `onLaunched` runs after a pane
 * opened (the doors tell the wizard which provider was enabled and refresh
 * /health so the row turns green by itself once the user finishes).
 */
export function useCliSignIn<C extends SignInCli = SignInCli>(onLaunched?: (c: C) => void) {
  const [signIn, setSignIn] = useState<Record<string, CliSignInState>>({});

  async function start(c: C) {
    setSignIn((s) => ({ ...s, [c.cli]: { busy: true } }));
    try {
      const paneId = await launchSignIn(c);
      setSignIn((s) => ({ ...s, [c.cli]: { paneId } }));
      onLaunched?.(c);
    } catch (e) {
      setSignIn((s) => ({ ...s, [c.cli]: { error: errText(e) } }));
    }
  }

  return { signIn, start };
}
