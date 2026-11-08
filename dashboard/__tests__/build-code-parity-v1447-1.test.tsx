/**
 * v1.447.0: Build › Code's Session tab, the dashboard half.
 *
 *   - Codex's whole-list "Reload MCP servers" (the overview's `mcp.reload`,
 *     tests/test_build_app_parity_v14471.py pins the daemon side): one quiet
 *     button under the list posting `/build/app/sessions/{id}/mcp-reload`,
 *     the daemon's words said after, a refusal in its words; no button when
 *     the overview does not offer it (Claude Code, an older Iron Jarvis).
 *   - Claude Code's Sign in… reaches the browser through the same row menu
 *     Codex's does (the daemon now offers it for a server waiting for one).
 *   - a Claude Code model picked with `[1m]` reads "<name> (1M context)".
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  postAnswer: {} as Record<string, unknown>,
  postFail: {} as Record<string, string>,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError: FakeApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    get: (path: string) => {
      const r = H.gets[path];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      if (H.postFail[path]) return Promise.reject(new FakeApiError(H.postFail[path], 409));
      return Promise.resolve(H.postAnswer[path] ?? {});
    },
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* () {},
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => null }));

import { CodePanes } from "@/components/buildCode/CodePanes";
import { decodeCodeSession, decodeCodingTools, oneMillionLabel } from "@/lib/buildApp";
import { decodeCodeMcp, MCP_RELOAD_WORDS } from "@/lib/codePanes";

const RAW_SESSION = {
  id: "app_1",
  harness: "codex",
  folder: "C:\\work\\shop",
  title: "Fix the cart",
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-11-07T09:00:00Z",
  updated_at: "2026-11-07T09:00:00Z",
  archived_at: null,
  last_error: "",
};
const TOOL = {
  id: "codex",
  label: "Codex",
  installed: true,
  signed_in: true,
  modes: [{ id: "ask", label: "Ask first", dangerous: false }],
  hidden_modes: [],
  models: [{ id: "", label: "Codex's default" }],
  efforts: ["low", "high"],
  accounts: [],
  commands: [],
};
const BASE = "/build/app/sessions/app_1";

function overview(mcp: Record<string, unknown>) {
  return {
    folder: "C:\\work\\shop",
    folder_exists: true,
    on_share: false,
    git: { repo: false },
    rules: [],
    context: null,
    tool_running: true,
    programs: [],
    mcp,
  };
}

const MCP = {
  known: true,
  at: new Date().toISOString(),
  servers: [
    { name: "tiny", state: "connected", words: "Connected" },
    { name: "tinyoff", state: "off", words: "Turned off" },
  ],
};

function setGets(over: Record<string, unknown>, harness = "codex") {
  H.gets = {
    [`${BASE}/changes`]: { source: "none", words: "", changes: [], files: 0, added: 0, removed: 0 },
    [`${BASE}/usage`]: { harness, account: null, seen: null, totals: {}, context: null },
    [`${BASE}/overview`]: over,
    [`${BASE}/context`]: { harness, reading: null, waiting: false },
    "/editor/options": { editors: [], preferred: "" },
  };
}

function renderPanes(harness = "codex") {
  const session = decodeCodeSession({ ...RAW_SESSION, harness })!;
  const tools = decodeCodingTools({ harnesses: [{ ...TOOL, id: harness, label: harness === "codex" ? "Codex" : "Claude Code" }] });
  return render(<CodePanes session={session} tools={tools} events={[]} running={false} />);
}

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem("ij.code.pane", "session");
  H.posts = [];
  H.postAnswer = {};
  H.postFail = {};
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Codex: Reload MCP servers", () => {
  it("decodes the overview's reload, and nothing for an older Iron Jarvis", () => {
    expect(decodeCodeMcp({ ...MCP, reload: true })!.reload).toBe(true);
    expect("reload" in decodeCodeMcp(MCP)!).toBe(false);
    expect("reload" in decodeCodeMcp({ ...MCP, reload: "yes" })!).toBe(false);
  });

  it("one button under the list posts the reload, says the daemon's words, and reads the tab again", async () => {
    setGets(overview({ ...MCP, reload: true }));
    H.postAnswer[`${BASE}/mcp-reload`] = { reloaded: true, words: "Codex read its MCP settings again." };
    renderPanes();
    const button = await screen.findByTestId("code-session-mcp-reload");
    expect(button.textContent).toBe(MCP_RELOAD_WORDS);
    expect(button.getAttribute("title")).toContain("Codex reads its extension settings again");
    setGets(overview({ ...MCP, servers: [MCP.servers[0]], reload: true }));
    fireEvent.click(button);
    await waitFor(() => expect(H.posts.map((p) => p.path)).toContain(`${BASE}/mcp-reload`));
    expect((await screen.findByTestId("code-session-mcp-reload-said")).textContent).toBe(
      "Codex read its MCP settings again.",
    );
    await waitFor(() => expect(screen.queryByTestId("code-session-mcp-tinyoff")).toBeNull());
  });

  it("a refusal is said in the daemon's words", async () => {
    setGets(overview({ ...MCP, reload: true }));
    H.postFail[`${BASE}/mcp-reload`] = "Wait for Codex's reply to finish, then reload its MCP servers.";
    renderPanes();
    fireEvent.click(await screen.findByTestId("code-session-mcp-reload"));
    const said = await screen.findByTestId("code-session-mcp-reload-said");
    expect(said.getAttribute("role")).toBe("alert");
    expect(said.textContent).toBe("Wait for Codex's reply to finish, then reload its MCP servers.");
  });

  it("no button when the overview offers none (Claude Code, an older Iron Jarvis)", async () => {
    setGets(overview(MCP), "claude-code");
    renderPanes("claude-code");
    await screen.findByTestId("code-session-mcp-tiny");
    expect(screen.queryByTestId("code-session-mcp-reload")).toBeNull();
  });
});

describe("Claude Code: Sign in… from the row menu", () => {
  it("opens Claude Code's own sign-in page and keeps the link", async () => {
    const url = "http://127.0.0.1:5000/authorize?response_type=code&client_id=c&state=s";
    setGets(
      overview({
        known: true,
        at: new Date().toISOString(),
        servers: [{ name: "oauthy", state: "needs_sign_in", words: "Needs you to sign in", actions: ["off", "sign_in"] }],
      }),
      "claude-code",
    );
    H.postAnswer[`${BASE}/mcp/oauthy`] = { words: "Claude Code waits for you to sign in to oauthy in your browser.", url };
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    renderPanes("claude-code");
    fireEvent.click(await screen.findByTestId("code-session-mcp-more-oauthy"));
    const item = screen.getByTestId("code-session-mcp-more-oauthy-sign_in");
    expect(item.textContent).toBe("Sign in…");
    fireEvent.click(item);
    await waitFor(() => expect(open).toHaveBeenCalledWith(url, "_blank", "noopener,noreferrer"));
    expect(H.posts.find((p) => p.path === `${BASE}/mcp/oauthy`)?.body).toEqual({ action: "sign_in" });
    expect((await screen.findByTestId("code-session-mcp-said-oauthy")).textContent).toContain("sign in to oauthy");
  });
});

describe("a Claude Code model with [1m]", () => {
  const models = [
    { id: "", label: "Claude Code's default" },
    { id: "claude-sonnet-5-5", label: "Sonnet 5.5" },
  ];
  it("reads as its listed name with 1M context", () => {
    expect(oneMillionLabel("claude-sonnet-5-5[1m]", models)).toBe("Sonnet 5.5 (1M context)");
    expect(oneMillionLabel("claude-sonnet-5-5[1M]", models)).toBe("Sonnet 5.5 (1M context)");
    expect(oneMillionLabel("claude-sonnet-5-5", models)).toBe("");
    expect(oneMillionLabel("claude-nope[1m]", models)).toBe("");
  });
});
