/**
 * v1.420.0 — two remainders of the readiness re-audit.
 *
 * C6.8: Grok has the ONE Sign in press (components/connections/CliSignInPress
 * → POST /terminals/launch {cli: "grok", account: "default"}), like Claude
 * Code and Codex — never "run `grok login`" in a terminal.
 *
 * C6.5: a Build helper that failed reads in plain words. The daemon now says
 * the run's one sentence; a row saved before v1.420.0 may still hold the raw
 * "crashed: ProviderError: …" text, whose exception class never shows.
 */

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

import { CliSignInPress } from "@/components/connections/CliSignInPress";
import { GROK_SIGN_IN, SIGN_IN_CLIS, subscriptionCliFor } from "@/lib/cliSignIn";
import { helperFailureText } from "@/components/harness/HarnessHelpers";

beforeEach(() => {
  hooks.posts = [];
});
afterEach(() => cleanup());

describe("C6.8 Grok's Sign in press", () => {
  it("Grok CLI is one of the CLIs with a press, after Claude Code and Codex", () => {
    expect(SIGN_IN_CLIS.map((c) => c.provider)).toEqual(["claude-cli", "codex-cli", "grok-cli"]);
    expect(subscriptionCliFor("grok-cli")).toBe(GROK_SIGN_IN);
    expect(GROK_SIGN_IN.name).toBe("Grok CLI");
    expect(GROK_SIGN_IN.paneHint).not.toMatch(/grok login/);
  });

  it("the press opens a Build terminal running Grok on this PC's own login", async () => {
    render(<CliSignInPress provider="grok-cli" />);
    fireEvent.click(screen.getByRole("button", { name: /Sign in to Grok CLI/ }));
    await waitFor(() => expect(screen.getByTestId("cli-pane-hint-grok-cli")).toBeTruthy());
    expect(hooks.posts).toEqual([{ path: "/terminals/launch", body: { cli: "grok", account: "default" } }]);
    expect(screen.getByTestId("cli-pane-hint-grok-cli").textContent).toContain("Grok CLI running");
  });

  it("control: a provider with no sign-in renders nothing", () => {
    const { container } = render(<CliSignInPress provider="opencode-cli" />);
    expect(container.textContent).toBe("");
  });
});

describe("C6.5 a failed helper's line in plain words", () => {
  it("the daemon's plain sentence is shown without its prefix", () => {
    expect(
      helperFailureText(
        "delegated agent 'builder' could not finish: Codex is busy. Run it again shortly.",
      ),
    ).toBe("Codex is busy. Run it again shortly.");
    expect(helperFailureText("spawned agent 'x' could not finish: Done badly.")).toBe("Done badly.");
  });

  it("an older row's raw crash never shows the exception class", () => {
    const out = helperFailureText("delegated agent 'glm' crashed: ProviderError: connection refused");
    expect(out).toBe("It stopped on an error: connection refused");
    expect(out).not.toMatch(/ProviderError|crashed/);
  });

  it("anything else is left as it is", () => {
    expect(helperFailureText("Stopped at its step limit (60 steps).")).toBe(
      "Stopped at its step limit (60 steps).",
    );
  });
});
