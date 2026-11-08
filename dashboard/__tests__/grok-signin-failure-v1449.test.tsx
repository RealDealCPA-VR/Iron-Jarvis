/**
 * v1.449.0 (Wave B, G-N item 6) — a Grok sign-in failure gets the Sign in press.
 *
 * lib/failureActions decided the press beside a failed turn from
 * lib/onboarding's SUBSCRIPTION_CLIS (Claude Code and Codex, the plan cards),
 * so `fix_kind: "sign_in", fix_provider: "grok-cli"` — the daemon's own
 * verdict since v1.420.0 (C6.8) — gave the sentence and no press, in chat and
 * in a coding session alike. It now reads the ONE list of CLIs with a press
 * (lib/signInClis, React-free: lib/cliSignIn re-exports it), which Grok is on.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const hooks = vi.hoisted(() => ({ posts: [] as { path: string; body: unknown }[] }));

vi.mock("@/lib/api", () => ({
  get: vi.fn(() => Promise.resolve({})),
  post: vi.fn((path: string, body?: unknown) => {
    hooks.posts.push({ path, body });
    return Promise.resolve({ id: "pane-9" });
  }),
}));

import { HarnessFailureLine } from "@/components/harness/HarnessFailure";
import { failureAction, signInLabel, signedOutCliFor } from "@/lib/failureActions";
import { GROK_SIGN_IN, SIGN_IN_CLIS, signInCliFor, subscriptionCliFor } from "@/lib/cliSignIn";
import * as signInClis from "@/lib/signInClis";
import type { BuildMessage } from "@/lib/buildSessions";
import type { Health } from "@/lib/types";

beforeEach(() => {
  hooks.posts = [];
});
afterEach(() => cleanup());

const GROK_REPLY = {
  role: "assistant",
  content: "",
  turnId: "t1",
  error: "Grok CLI isn't signed in. Press Sign in beside Grok CLI in Settings › Connections, then retry.",
  errorTitle: "Grok CLI isn't signed in",
  errorKind: "sign_in",
  errorProvider: "grok-cli",
  errorDetail: "grok-cli: Not logged in · Run /login",
} as unknown as BuildMessage;

describe("lib/failureActions reads every CLI with a Sign in press", () => {
  it("a Grok sign-in failure is a sign_in action with Grok's press (red before: null)", () => {
    expect(failureAction({ fixKind: "sign_in", fixProvider: "grok-cli" }, null, "")).toEqual({
      kind: "sign_in",
      cli: GROK_SIGN_IN,
    });
    expect(signInLabel(GROK_SIGN_IN)).toBe("Sign in to Grok CLI");
    // Claude Code and Codex are exactly as before.
    expect(failureAction({ fixKind: "sign_in", fixProvider: "claude-cli" }, null, "")?.kind).toBe("sign_in");
    expect(failureAction({ fixKind: "sign_in", fixProvider: "opencode-cli" }, null, "")).toBeNull();
  });

  it("a /health row saying Grok is installed and signed out names the same press", () => {
    const health = {
      providers: [{ provider: "grok-cli", available: false, installed: true, signed_in: false }],
    } as unknown as Health;
    expect(signedOutCliFor(health, "grok-cli")).toBe(GROK_SIGN_IN);
    expect(failureAction(null, health, "grok-cli")).toEqual({ kind: "sign_in", cli: GROK_SIGN_IN });
  });

  it("lib/signInClis is the one list, React-free, and lib/cliSignIn still names everything", () => {
    expect(signInCliFor).toBe(signInClis.signInCliFor);
    expect(subscriptionCliFor).toBe(signInClis.signInCliFor);
    expect(SIGN_IN_CLIS).toBe(signInClis.SIGN_IN_CLIS);
    expect(SIGN_IN_CLIS.map((c) => c.provider)).toEqual(["claude-cli", "codex-cli", "grok-cli"]);
    const src = readFileSync(join(process.cwd(), "lib", "signInClis.ts"), "utf8");
    expect(src).not.toMatch(/from ["']react["']/);
    expect(src).not.toMatch(/@\/lib\/api["']/);
    expect(src).not.toMatch(/^"use client"/m);
    // failureActions reads the list from there, never lib/onboarding's two.
    const fa = readFileSync(join(process.cwd(), "lib", "failureActions.ts"), "utf8");
    expect(fa).toContain('from "@/lib/signInClis"');
    expect(fa).not.toContain("SUBSCRIPTION_CLIS");
  });
});

describe("a coding session's failed reply on Grok", () => {
  it("shows the headline alone and the one Sign in press, which opens Grok's terminal", async () => {
    render(
      <HarnessFailureLine
        m={GROK_REPLY}
        newest
        busy={false}
        health={null}
        turnProvider="grok-cli"
        retry
        onRetry={() => {}}
        canRewind={false}
        onRewind={() => {}}
      />,
    );
    expect(screen.getByTestId("harness-reply-error").textContent?.trim()).toBe("Grok CLI isn't signed in");
    const press = screen.getByRole("button", { name: /Sign in to Grok CLI/ });
    fireEvent.click(press);
    await waitFor(() =>
      expect(hooks.posts).toEqual([{ path: "/terminals/launch", body: { cli: "grok", account: "default" } }]),
    );
    expect(screen.getByTestId("harness-retry")).toBeTruthy();
  });

  it("control: a provider with no press keeps the whole sentence and no Sign in", () => {
    const m = { ...GROK_REPLY, errorProvider: "opencode-cli", error: "OpenCode isn't signed in. Sign in there." } as BuildMessage;
    render(
      <HarnessFailureLine m={m} newest busy={false} health={null} turnProvider="opencode-cli" retry onRetry={() => {}} canRewind={false} onRewind={() => {}} />,
    );
    expect(screen.queryByRole("button", { name: /Sign in/ })).toBeNull();
    expect(screen.getByTestId("harness-reply-error").textContent).toContain("Sign in there");
  });
});
