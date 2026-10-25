"use client";

/**
 * THE ONE SIGN-IN PRESS (readiness 12A, G33).
 *
 * v1.353.0 gave Settings › Connections' Claude Code and Codex rows a Sign in
 * button (lib/cliSignIn: a NEW Build terminal with the CLI started in it, on
 * this PC's own login), but every OTHER place that says a subscription CLI is
 * not signed in still told the user to "open a terminal, run `claude`, then
 * `/login`": the Rescan note (inside a green "complete" note), the welcome
 * card's Optional extras, the chat's warning above the message box and the
 * New task door. Lifted out of the Connections page so all of them offer the
 * SAME press, and none of them a typed terminal recipe.
 *
 * `variant`:
 *   - "row"    — under a row's words (Connections, the welcome card): the
 *                press, then the window's own hint once it opened;
 *   - "inline" — inside one line of text (the chat's warning, the New task
 *                door): a small "Sign in" press, then one short line.
 *
 * Takes no hook of the daemon (dozens of tests mock lib/daemon wholesale):
 * the caller passes `onLaunched` (Connections and the doors refresh /health,
 * so the row turns green by itself once the user finishes).
 */

import Link from "next/link";
import { Loader2, LogIn } from "lucide-react";
import { codeParts, paneHref, subscriptionCliFor, useCliSignIn } from "@/lib/cliSignIn";
import { announceBuildTerminalLink } from "@/lib/buildLinks";
import { CALM_ACTION } from "@/components/connections/endpointChip";

const ROW_LINK =
  "font-medium text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:text-accent";

/** A sentence with `backticked` words to type, drawn as code. */
function withCode(text: string) {
  return codeParts(text).map((part, i) =>
    part.code ? (
      <code key={i} className="font-mono text-zinc-300">
        {part.text}
      </code>
    ) : (
      part.text
    ),
  );
}

export function CliSignInPress({
  provider,
  variant = "row",
  testIdPrefix = "cli",
  onLaunched,
}: {
  /** The /health provider id ("claude-cli", "codex-cli"). Anything else
   *  renders nothing — there is no press to offer. */
  provider: string;
  variant?: "row" | "inline";
  /** The test-id family (Connections keeps its `cli-sign-in-<provider>`). */
  testIdPrefix?: string;
  onLaunched?: () => void;
}) {
  const cli = subscriptionCliFor(provider);
  const { signIn, start } = useCliSignIn(() => onLaunched?.());
  if (!cli) return null;
  const si = signIn[cli.cli] ?? {};
  if (variant === "inline") {
    return (
      <span data-testid={`${testIdPrefix}-sign-in-${provider}`} className="inline">
        {si.paneId !== undefined ? (
          <span data-testid={`${testIdPrefix}-pane-hint-${provider}`}>
            A Build terminal opened.{" "}
            <Link
              href={paneHref(si.paneId)}
              // Harness gaps (fix 2): pressed ON Build (a coding session's
              // failed reply), the link is announced — Build shows the
              // terminal (lib/buildLinks BUILD_TERMINAL_EVENT).
              onClick={() => announceBuildTerminalLink(paneHref(si.paneId))}
              className={ROW_LINK}
            >
              Finish signing in there
            </Link>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => void start(cli)}
            disabled={si.busy}
            className="inline-flex items-center gap-1 font-medium text-accent-soft underline decoration-accent/40 underline-offset-2 transition-colors hover:text-accent disabled:opacity-50"
          >
            {si.busy ? (
              <Loader2 size={11} className="animate-spin motion-reduce:animate-none" aria-hidden />
            ) : (
              <LogIn size={11} aria-hidden />
            )}
            Sign in to {cli.name}
          </button>
        )}
        {si.error && (
          <span role="alert" className="ml-1 text-tone-danger">
            {si.error}
          </span>
        )}
      </span>
    );
  }
  return (
    <div data-testid={`${testIdPrefix}-sign-in-${provider}`} className="mt-1 text-[11px] leading-relaxed text-zinc-500">
      {si.paneId !== undefined ? (
        <span data-testid={`${testIdPrefix}-pane-hint-${provider}`}>
          A Build terminal opened with {cli.name} running. {withCode(cli.paneHint)} This turns green by
          itself once you&apos;re signed in.{" "}
          <Link
            href={paneHref(si.paneId)}
            onClick={() => announceBuildTerminalLink(paneHref(si.paneId))}
            className={ROW_LINK}
          >
            Open the Build terminal
          </Link>
        </span>
      ) : (
        <button
          type="button"
          onClick={() => void start(cli)}
          disabled={si.busy}
          className={`-ml-2.5 ${CALM_ACTION}`}
        >
          {si.busy ? (
            <Loader2 size={13} className="animate-spin motion-reduce:animate-none" aria-hidden />
          ) : (
            <LogIn size={13} aria-hidden />
          )}
          Sign in to {cli.name}
        </button>
      )}
      {si.error && (
        <span role="alert" className="mt-0.5 block text-tone-danger">
          {si.error}
        </span>
      )}
    </div>
  );
}
