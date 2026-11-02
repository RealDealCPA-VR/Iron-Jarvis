/**
 * v1.426.0 (Wave 6 lane B, E4): the real CLI's OWN setup is visible in
 * Build › Code.
 *
 *   - the Session tab lists the tool's own MCP servers as it reported them
 *     (the overview's `mcp`, `hosted/tool_setup.view`): each one's state in
 *     words, and for one waiting for a sign-in, the tool's own way to sign
 *     in; a tool that never said says so; an older Iron Jarvis (no `mcp`)
 *     shows nothing;
 *   - Codex's own `/` items: its skills come with the prefix "$" (Codex's own
 *     way to invoke one) and picking one types `$<name> `, never `/<name>`.
 *
 * The daemon's answers are the shapes tests/test_build_app_tool_setup_v14260.py
 * pins on the Python side.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
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
      return Promise.resolve({});
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
import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { decodeCodeSession, decodeCodingTools, decodeCommands } from "@/lib/buildApp";
import { decodeCodeMcp, decodeCodeOverview } from "@/lib/codePanes";
import { pickCommand } from "@/lib/buildAppHistory";

const RAW_SESSION = {
  id: "app_1",
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: "Fix the cart",
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-10-30T09:00:00Z",
  updated_at: "2026-10-30T09:00:00Z",
  archived_at: null,
  last_error: "",
};
const TOOL = {
  id: "claude-code",
  label: "Claude Code",
  installed: true,
  signed_in: true,
  modes: [{ id: "ask", label: "Ask first", dangerous: false }],
  hidden_modes: [],
  models: [{ id: "", label: "Claude Code's default" }],
  efforts: [],
  accounts: [],
  commands: [],
};
const SESSION = decodeCodeSession(RAW_SESSION)!;
const TOOLS = decodeCodingTools({ harnesses: [TOOL] });

const OVERVIEW = {
  folder: "C:\\work\\shop",
  folder_exists: true,
  on_share: false,
  git: { repo: false },
  rules: [],
  context: null,
  tool_running: true,
  programs: [],
  mcp: {
    known: true,
    at: new Date().toISOString(),
    servers: [
      { name: "notes", state: "connected", words: "Connected" },
      { name: "broken-tool", state: "failed", words: "Did not start", error: "MCP client failed to start" },
      {
        name: "tracker",
        state: "needs_sign_in",
        words: "Needs you to sign in",
        sign_in:
          "To sign in, press Open in terminal, type /mcp there, choose tracker and pick Authenticate; Claude Code uses it from its next start here.",
      },
    ],
  },
};

function setGets(overview: Record<string, unknown>) {
  H.gets = {
    "/build/app/sessions/app_1/changes": { source: "none", words: "", changes: [], files: 0, added: 0, removed: 0 },
    "/build/app/sessions/app_1/usage": { harness: "claude-code", account: null, seen: null, totals: {}, context: null },
    "/build/app/sessions/app_1/overview": overview,
    "/build/app/sessions/app_1/context": { harness: "claude-code", reading: null, waiting: false },
    "/editor/options": { editors: [], preferred: "" },
  };
}

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem("ij.code.pane", "session");
  H.posts = [];
});

afterEach(() => {
  cleanup();
});

describe("the Session tab: the tool's own MCP servers", () => {
  it("lists each server with its state in words, and the tool's own way to sign in", async () => {
    setGets(OVERVIEW);
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    const list = await screen.findByTestId("code-session-mcp");
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "code-session-mcp-notes",
      "code-session-mcp-broken-tool",
      "code-session-mcp-tracker",
    ]);
    expect(screen.getByTestId("code-session-mcp-notes").textContent).toContain("Connected");
    expect(screen.getByTestId("code-session-mcp-broken-tool").textContent).toContain("Did not start");
    expect(screen.getByTestId("code-session-mcp-broken-tool").textContent).toContain("MCP client failed to start");
    expect(screen.getByTestId("code-session-mcp-tracker").getAttribute("data-state")).toBe("needs_sign_in");
    const how = screen.getByTestId("code-session-mcp-sign-in-tracker").textContent ?? "";
    expect(how).toContain("/mcp");
    expect(how).toContain("Authenticate");
    expect(screen.queryByTestId("code-session-mcp-sign-in-notes")).toBeNull();
    expect(screen.getByText(/As Claude Code reported them just now, when it started\./)).toBeTruthy();
  });

  it("a tool that never said shows the daemon's sentence, and an older Iron Jarvis shows nothing", async () => {
    setGets({ ...OVERVIEW, mcp: { known: false, servers: [], note: "Pi does not report its own MCP servers here." } });
    const view = render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    await waitFor(() =>
      expect(screen.getByTestId("code-session-mcp-note").textContent).toBe("Pi does not report its own MCP servers here."),
    );
    view.unmount();
    const { mcp: _drop, ...older } = OVERVIEW;
    void _drop;
    setGets(older);
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    await screen.findByTestId("code-session-folder");
    await waitFor(() => expect(H.gets).toBeTruthy());
    expect(screen.queryByTestId("code-session-mcp")).toBeNull();
    expect(screen.queryByTestId("code-session-mcp-note")).toBeNull();
  });

  it("decodes only named servers and an unknown state as did-not-start", () => {
    const m = decodeCodeMcp({
      known: true,
      servers: [{ name: "" }, { state: "connected" }, { name: "x", state: "weird", sign_in: 5 }],
    })!;
    expect(m.servers).toEqual([{ name: "x", state: "failed", words: "Did not start", error: "", signIn: "" }]);
    expect(decodeCodeMcp(null)).toBeNull();
    expect(decodeCodeOverview({ folder: "" })!.mcp).toBeNull();
  });
});

describe("Codex's own `/` items", () => {
  it("a skill keeps its \"$\" and is typed `$<name>`; nothing else gets a prefix", () => {
    const cmds = decodeCommands({
      commands: [
        { name: "compact", description: "Summarize" },
        { name: "release-notes", description: "Notes", prefix: "$" },
        { name: "odd", description: "x", prefix: "#" },
      ],
    })!;
    expect(cmds).toEqual([
      { name: "compact", description: "Summarize" },
      { name: "release-notes", description: "Notes", prefix: "$" },
      { name: "odd", description: "x" },
    ]);
    expect(pickCommand("fix it\n/rel", { start: 7, query: "rel" }, "release-notes", "$").text).toBe(
      "fix it\n$release-notes ",
    );
    expect(pickCommand("/com", { start: 0, query: "com" }, "compact").text).toBe("/compact ");
  });

  it("the menu shows a skill as `$name` and picking it types `$name `", async () => {
    const codex = { ...TOOL, id: "codex", label: "Codex" };
    const sess = { ...RAW_SESSION, id: "c1", harness: "codex" };
    H.gets = {
      "/build/app/harnesses": { harnesses: [codex] },
      "/build/app/sessions": { sessions: [sess] },
      "/editor/options": { editors: [], preferred: "" },
      "/build/app/sessions/c1": {
        session: sess,
        events: [],
        running: false,
        pending: [],
        commands: [
          { name: "compact", description: "Summarize the conversation to free up room (Codex's own compact)." },
          { name: "release-notes", description: "Write the release notes for a version", prefix: "$" },
        ],
      },
    };
    render(<BuildCodeView initialSession="c1" onOpenedTerminal={() => {}} />);
    const box = (await screen.findByTestId("code-box")) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "/" } });
    const menu = await screen.findByTestId("code-slash-menu");
    expect(within(menu).getByTestId("code-command-release-notes").textContent).toContain("$release-notes");
    expect(within(menu).getByTestId("code-command-compact").textContent).toContain("/compact");
    fireEvent.click(screen.getByTestId("code-command-release-notes"));
    expect(box.value).toBe("$release-notes ");
  });
});
