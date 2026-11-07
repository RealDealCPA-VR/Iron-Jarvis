/**
 * v1.443.0 — the two coding views' dead ends (ease-of-use review, item 11).
 *
 *  - Build › Code with NO coding tool on this PC says so in one line, offers
 *    "Get Claude Code" / "Get Codex" (the daemon's own install pages, the
 *    Launch catalog's `url`; the dashboard's own pages while it is read) and
 *    "Or let Jarvis write the code in Chat › Code" — the box used to say
 *    "Start a session with Claude Code, Codex…" over a grey Send and nothing
 *    else;
 *  - a SIGNED-OUT Claude Code / Codex / Grok shows the app's one-press Sign
 *    in beside the reason (the start box and the tool menu row), never "open
 *    a terminal, run it, and sign in there"; a tool with no press keeps those
 *    words;
 *  - the two "Code" views point at each other: Build › Code's start box →
 *    Chat › Code, Chat › Code's start box → Build › Code.
 *
 * On the REAL StartComposer and HarnessStartComposer against an api double.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    if (method !== "GET") return Promise.resolve({ id: "pane-9" });
    const value = api.gets[path];
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined) return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value);
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string, b?: unknown) => answer("POST", p, b),
    put: (p: string, b?: unknown) => answer("PUT", p, b),
    patch: (p: string, b?: unknown) => answer("PATCH", p, b),
    del: (p: string) => answer("DELETE", p),
  };
});
const DAEMON = {
  online: true,
  checking: false,
  health: { default_provider: "mock", default_model: "mock-1", providers: [] },
  refresh: () => {},
};
vi.mock("@/lib/daemon", () => ({ useDaemon: () => DAEMON }));
const MODELS = { models: [], usable: [], loading: false };
vi.mock("@/lib/useModels", () => ({ useModels: () => MODELS }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));

import { StartComposer } from "@/components/buildCode/StartComposer";
import { HarnessStartComposer } from "@/components/harness/HarnessStartComposer";
import {
  NO_TOOL_ON_PC,
  decodeCodingTools,
  getToolLinks,
  noToolCanStart,
  toolSignInProvider,
  toolUnavailable,
} from "@/lib/buildApp";
import { SUBSCRIPTION_CLIS } from "@/lib/onboarding";
import { resetFolderDefaultsCache } from "@/lib/folderDefaults";

const MODES = [{ id: "ask", label: "Ask first", dangerous: false }];

function row(id: string, label: string, extra: Record<string, unknown> = {}) {
  return { id, label, installed: false, signed_in: null, modes: MODES, models: [], accounts: [], ...extra };
}

const NOTHING_INSTALLED = decodeCodingTools({
  harnesses: [row("claude-code", "Claude Code"), row("codex", "Codex"), row("pi", "Pi"), row("opencode", "OpenCode")],
});

const CLAUDE_SIGNED_OUT = decodeCodingTools({
  harnesses: [
    row("claude-code", "Claude Code", { installed: true, signed_in: false }),
    row("codex", "Codex"),
    row("pi", "Pi"),
    row("opencode", "OpenCode"),
  ],
});

const CODEX_READY_CLAUDE_OUT = decodeCodingTools({
  harnesses: [
    row("claude-code", "Claude Code", { installed: true, signed_in: false }),
    row("codex", "Codex", { installed: true, signed_in: true }),
    row("pi", "Pi"),
    row("opencode", "OpenCode"),
  ],
});

const CATALOG = {
  clis: [
    { id: "claude", label: "Claude Code", url: "https://claude.com/claude-code", installed: false },
    { id: "codex", label: "Codex", url: "https://developers.openai.com/codex/cli", installed: false },
  ],
};

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetFolderDefaultsCache();
  api.calls = [];
  api.gets = { "/terminals/ai-clis": CATALOG };
  window.history.replaceState(null, "", "/terminals?view=code");
});
afterEach(() => cleanup());

function renderBuildStart(tools: ReturnType<typeof decodeCodingTools>) {
  return render(<StartComposer tools={tools} toolsError={null} onStarted={() => {}} />);
}

describe("lib/buildApp: the start words", () => {
  it("a tool with the one-press Sign in says only that it is signed out; one without keeps the way by hand", () => {
    const [claude] = CLAUDE_SIGNED_OUT;
    expect(toolUnavailable(claude)).toBe("Claude Code is signed out.");
    const pi = decodeCodingTools({ harnesses: [row("pi", "Pi", { installed: true, signed_in: false })] })[0];
    expect(toolUnavailable(pi)).toBe("Pi is signed out. Open a terminal, run it, and sign in there first.");
    expect(toolSignInProvider("claude-code")).toBe("claude-cli");
    expect(toolSignInProvider("codex")).toBe("codex-cli");
    expect(toolSignInProvider("grok")).toBe("grok-cli");
    expect(toolSignInProvider("pi")).toBe("");
  });

  it("noToolCanStart: false while the list is read and when one tool can start", () => {
    expect(noToolCanStart(null)).toBe(false);
    expect(noToolCanStart(NOTHING_INSTALLED)).toBe(true);
    expect(noToolCanStart(CLAUDE_SIGNED_OUT)).toBe(true);
    expect(noToolCanStart(CODEX_READY_CLAUDE_OUT)).toBe(false);
  });

  it("the Get links: the daemon's page wins, the dashboard's page stands in, never a non-web address", () => {
    expect(getToolLinks(CATALOG.clis).map((l) => l.url)).toEqual([
      "https://claude.com/claude-code",
      "https://developers.openai.com/codex/cli",
    ]);
    const fb = (cli: string) => SUBSCRIPTION_CLIS.find((c) => c.cli === cli)?.installUrl;
    expect(getToolLinks(null, fb).map((l) => l.label)).toEqual(["Get Claude Code", "Get Codex"]);
    expect(getToolLinks([{ id: "claude", url: "javascript:alert(1)" }]).map((l) => l.harness)).toEqual([]);
  });
});

describe("Build › Code's start box with no coding tool on this PC", () => {
  it("says so in one line, links to get Claude Code and Codex, and offers Chat › Code", async () => {
    renderBuildStart(NOTHING_INSTALLED);
    const line = screen.getByTestId("code-start-no-tool-line");
    expect(line.textContent).toContain(NO_TOOL_ON_PC);
    // The daemon's own install pages, once the Launch catalog is read.
    await waitFor(() =>
      expect(screen.getByTestId("code-start-get-claude-code")).toHaveAttribute("href", "https://claude.com/claude-code"),
    );
    expect(screen.getByTestId("code-start-get-codex")).toHaveAttribute("href", "https://developers.openai.com/codex/cli");
    expect(screen.getByTestId("code-start-get-claude-code")).toHaveAttribute("target", "_blank");
    expect(api.calls.some((c) => c.path === "/terminals/ai-clis")).toBe(true);
    const jarvis = screen.getByTestId("code-start-let-jarvis");
    expect(jarvis.textContent).toBe("Or let Jarvis write the code in Chat › Code.");
    expect(within(jarvis).getByRole("link", { name: "Chat › Code" })).toHaveAttribute("href", "/chat?view=code");
    // The tool chip names the state truthfully, and Send says why it is grey.
    expect(screen.getByTestId("code-start-tool").textContent).toContain("No coding tool on this PC");
    expect(screen.getByTestId("code-start-send")).toBeDisabled();
    expect(screen.getByTestId("code-start-send")).toHaveAttribute("title", NO_TOOL_ON_PC);
    // Chat › Code is offered once, not twice.
    expect(screen.queryByTestId("code-to-chat-code")).toBeNull();
  });

  it("the dashboard's own install pages stand in when the catalog cannot be read", async () => {
    api.gets = { "/terminals/ai-clis": new api.ApiError("down", 0) };
    renderBuildStart(NOTHING_INSTALLED);
    const claudeUrl = SUBSCRIPTION_CLIS.find((c) => c.provider === "claude-cli")?.installUrl;
    expect(screen.getByTestId("code-start-get-claude-code")).toHaveAttribute("href", claudeUrl);
    await waitFor(() => expect(api.calls.some((c) => c.path === "/terminals/ai-clis")).toBe(true));
    expect(screen.getByTestId("code-start-get-claude-code")).toHaveAttribute("href", claudeUrl);
  });
});

describe("a signed-out coding tool offers the one-press Sign in", () => {
  it("in the start box: the reason and Sign in to Claude Code, which opens the sign-in terminal", async () => {
    renderBuildStart(CLAUDE_SIGNED_OUT);
    const line = screen.getByTestId("code-start-signed-out-claude-code");
    expect(line.textContent).toContain("Claude Code is signed out.");
    expect(line.textContent).not.toContain("Open a terminal");
    fireEvent.click(within(line).getByRole("button", { name: /Sign in to Claude Code/ }));
    await waitFor(() =>
      expect(api.calls).toContainEqual({ method: "POST", path: "/terminals/launch", body: { cli: "claude", account: "default" } }),
    );
    await screen.findByTestId("code-start-pane-hint-claude-cli");
    expect(screen.getByTestId("code-start-let-jarvis")).toBeInTheDocument();
    // Signed out is not "not installed": no Get links.
    expect(screen.queryByTestId("code-start-get-claude-code")).toBeNull();
    expect(screen.getByTestId("code-start-tool").textContent).toContain("No coding tool ready");
  });

  it("in the tool menu: beside the signed-out row, outside the row's own button", () => {
    renderBuildStart(CODEX_READY_CLAUDE_OUT);
    fireEvent.click(screen.getByTestId("code-start-tool"));
    const menu = screen.getByTestId("code-start-tool-menu");
    expect(within(menu).getByTestId("code-start-tool-why-claude-code").textContent).toBe("Claude Code is signed out.");
    const press = within(menu).getByTestId("code-start-tool-signin-claude-code");
    expect(within(press).getByRole("button", { name: /Sign in to Claude Code/ })).toBeEnabled();
    // Never inside the disabled row's button (a button never holds another).
    expect(screen.getByTestId("code-start-tool-claude-code").contains(press)).toBe(false);
    // Codex can start: no press for it, and the start box has no dead end.
    expect(within(menu).queryByTestId("code-start-tool-signin-codex")).toBeNull();
    expect(screen.queryByTestId("code-start-no-tool")).toBeNull();
  });
});

describe("the two Code views point at each other", () => {
  it("Build › Code's start box: Want Jarvis to write the code itself? Use Chat › Code.", () => {
    renderBuildStart(CODEX_READY_CLAUDE_OUT);
    const line = screen.getByTestId("code-to-chat-code");
    expect(line.textContent).toBe("Want Jarvis to write the code itself? Use Chat › Code.");
    expect(within(line).getByRole("link", { name: "Chat › Code" })).toHaveAttribute("href", "/chat?view=code");
  });

  it("Chat › Code's start box: Want your own Claude Code or Codex instead? Open Build › Code.", () => {
    api.gets = {
      "/build/accounts": { iron_proxy: false, claude: [], codex: [] },
      "/build/folders/team": null,
      "/build/folders/defaults": null,
    };
    window.history.replaceState(null, "", "/chat?view=code");
    render(<HarnessStartComposer onStart={async () => null} />);
    const line = screen.getByTestId("harness-to-build-code");
    expect(line.textContent).toBe("Want your own Claude Code or Codex instead? Open Build › Code.");
    expect(within(line).getByRole("link", { name: "Build › Code" })).toHaveAttribute("href", "/terminals?view=code");
  });
});
