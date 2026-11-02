/**
 * v1.424.0 (audit D4) — the Iron-Proxy card's ONE line about the owner's
 * Claude Code setup on the other accounts.
 *
 *  - the daemon's words (`claude_setup.words`) are drawn under the Claude
 *    group, and only there (never under Codex or Grok);
 *  - the line says whether it is on (`data-on`), in the daemon's words;
 *  - an older daemon (no `claude_setup`) or empty words draws nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({ snap: null as unknown }));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => null,
    get: (p: string) =>
      p === "/iron-proxy" || p === "/iron-proxy?discover=0" ? Promise.resolve(hooks.snap) : Promise.resolve({}),
    post: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/connections",
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null }));

import { IronProxyCard } from "@/components/connections/IronProxyCard";
import { claudeSetupLine } from "@/lib/ironProxy";

function acct(over: Record<string, unknown>) {
  return {
    id: "a",
    title: "Work Claude",
    provider: "anthropic",
    lane: "cli",
    order: 0,
    enabled: true,
    state: { status: "ready", served: 0 },
    ...over,
  };
}

const ON =
  "Claude Code on your other accounts uses your MCP servers, skills and commands (2 MCP servers and 5 skills); " +
  "they show with “my:” in front, like /my:<name>. Turn this off in Settings › Connections.";

function snap(setup: unknown) {
  return {
    status: { enabled: true, running: true, owned: true, url: "", version: "0.6.0", error: null, bundled: true },
    accounts: [
      acct({ id: "tsi", title: "TSI" }),
      acct({ id: "cx", title: "Work Codex", provider: "openai" }),
    ],
    discovered: [],
    providers_used_by_jarvis: { anthropic: "claude-cli", openai: "codex-cli", xai: "grok-cli" },
    account_groups: {},
    ...(setup === undefined ? {} : { claude_setup: setup }),
  };
}

async function mount(setup: unknown) {
  hooks.snap = snap(setup);
  render(<IronProxyCard />);
  await waitFor(() => expect(document.getElementById("iron-proxy-account-tsi")).not.toBeNull());
}

beforeEach(() => {
  hooks.snap = null;
});
afterEach(() => cleanup());

describe("the Claude group's line about your setup", () => {
  it("draws the daemon's words under the Claude group only", async () => {
    await mount({ on: true, words: ON });
    const line = await screen.findByTestId("iron-proxy-claude-setup");
    expect(line.textContent).toBe(ON);
    expect(line.getAttribute("data-on")).toBe("true");
    expect(within(screen.getByTestId("iron-proxy-group-anthropic")).getByTestId("iron-proxy-claude-setup")).toBe(line);
    expect(within(screen.getByTestId("iron-proxy-group-openai")).queryByTestId("iron-proxy-claude-setup")).toBeNull();
  });

  it("says when it is off", async () => {
    await mount({ on: false, words: "Claude Code on your other accounts starts without your MCP servers." });
    const line = await screen.findByTestId("iron-proxy-claude-setup");
    expect(line.getAttribute("data-on")).toBe("false");
  });

  it("an older daemon, or empty words, draws nothing", async () => {
    await mount(undefined);
    expect(screen.queryByTestId("iron-proxy-claude-setup")).toBeNull();
    cleanup();
    await mount({ on: true, words: "   " });
    expect(screen.queryByTestId("iron-proxy-claude-setup")).toBeNull();
  });
});

describe("claudeSetupLine", () => {
  it("reads only a real line", () => {
    expect(claudeSetupLine(null)).toBeNull();
    expect(claudeSetupLine({})).toBeNull();
    expect(claudeSetupLine({ claude_setup: { on: true, words: "" } })).toBeNull();
    expect(claudeSetupLine({ claude_setup: "nope" as never })).toBeNull();
    expect(claudeSetupLine({ claude_setup: { on: false, words: " x " } })).toEqual({ on: false, words: "x" });
    expect(claudeSetupLine({ claude_setup: { words: "y" } as never })).toEqual({ on: true, words: "y" });
  });
});
