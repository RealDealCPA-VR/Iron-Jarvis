/**
 * v1.441.0 (lane g1): the Session tab's live controls in Build › Code.
 *
 *   - the tool's own background work (the overview's `background_work`) is
 *     listed with a ⋯ menu whose Stop posts
 *     `/build/app/sessions/{id}/background/{work_id}/stop`, then reads the tab
 *     again; a refusal is said in the daemon's words;
 *   - each MCP row the tool can act on (`actions`) gets a ⋯ menu with ONLY
 *     those actions, posting `/build/app/sessions/{id}/mcp/{server}`
 *     `{action}`; a sign-in opens the tool's own page in the browser;
 *   - an older Iron Jarvis (no `background_work`, no `actions`) shows the tab
 *     exactly as before: no menu anywhere;
 *   - the effort chip stays open while a reply runs when the daemon says the
 *     tool takes a new effort live (`effort_live`).
 *
 * The daemon's shapes are the ones tests/test_session_controls_routes_v14410.py
 * pins on the Python side.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
import { decodeCodeSession, decodeCodingTools } from "@/lib/buildApp";
import { decodeBackgroundWork, decodeCodeMcp, decodeCodeOverview } from "@/lib/codePanes";
import { effortWhileRunning } from "@/lib/buildAppHistory";
import { openableSignIn } from "@/components/buildCode/SessionControls";

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
  created_at: "2026-11-06T09:00:00Z",
  updated_at: "2026-11-06T09:00:00Z",
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
  efforts: ["low", "high"],
  effort_live: true,
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
      { name: "notes", state: "connected", words: "Connected", actions: ["reconnect", "off"] },
      { name: "old", state: "off", words: "Turned off", actions: ["on"] },
      { name: "quiet", state: "starting", words: "Starting…" },
    ],
  },
  background_work: [
    { id: "bztz1", title: "Long ping", kind: "command", can_stop: true },
    { id: "ag1", title: "builder", kind: "helper", can_stop: true },
  ],
};
const BASE = "/build/app/sessions/app_1";

function setGets(overview: Record<string, unknown>) {
  H.gets = {
    [`${BASE}/changes`]: { source: "none", words: "", changes: [], files: 0, added: 0, removed: 0 },
    [`${BASE}/usage`]: { harness: "claude-code", account: null, seen: null, totals: {}, context: null },
    [`${BASE}/overview`]: overview,
    [`${BASE}/context`]: { harness: "claude-code", reading: null, waiting: false },
    "/editor/options": { editors: [], preferred: "" },
  };
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

describe("background work with Stop", () => {
  it("lists what the tool still runs and stops ONE through its ⋯ menu", async () => {
    setGets(OVERVIEW);
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    const box = await screen.findByTestId("code-session-background-work");
    expect(within(box).getByTestId("code-session-work-bztz1").textContent).toContain("Long ping");
    expect(within(box).getByTestId("code-session-work-bztz1").textContent).toContain("Command");
    expect(within(box).getByTestId("code-session-work-ag1").textContent).toContain("Helper");
    // Calm: no Stop button on the row, only the ⋯ menu.
    expect(within(box).queryByRole("button", { name: "Stop" })).toBeNull();
    fireEvent.click(screen.getByTestId("code-session-work-more-bztz1"));
    const stop = screen.getByTestId("code-session-work-more-bztz1-stop");
    expect(stop.textContent).toBe("Stop");
    // After the stop the tab reads again: the list no longer has it.
    setGets({ ...OVERVIEW, background_work: [OVERVIEW.background_work[1]] });
    fireEvent.click(stop);
    await waitFor(() => expect(H.posts.map((p) => p.path)).toContain(`${BASE}/background/bztz1/stop`));
    await waitFor(() => expect(screen.queryByTestId("code-session-work-bztz1")).toBeNull());
    expect(screen.getByTestId("code-session-work-ag1")).toBeTruthy();
  });

  it("says a refused stop in the daemon's words", async () => {
    setGets(OVERVIEW);
    H.postFail[`${BASE}/background/bztz1/stop`] = "That task already ended.";
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    fireEvent.click(await screen.findByTestId("code-session-work-more-bztz1"));
    fireEvent.click(screen.getByTestId("code-session-work-more-bztz1-stop"));
    expect((await screen.findByRole("alert")).textContent).toBe("That task already ended.");
  });

  it("an older Iron Jarvis (no background_work) shows the Background section as before", async () => {
    const { background_work: _drop, ...older } = OVERVIEW;
    void _drop;
    setGets(older);
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    await screen.findByTestId("code-session-programs-none");
    expect(screen.queryByTestId("code-session-background-work")).toBeNull();
    expect(decodeCodeOverview(older)!.backgroundWork).toBeNull();
  });

  it("decodes only named rows, an unknown kind as a task", () => {
    expect(decodeBackgroundWork([{ id: "" }, { id: "a", kind: "weird", title: "", can_stop: 1 }, 5])).toEqual([
      { id: "a", title: "A task", kind: "task", canStop: false },
    ]);
    expect(decodeBackgroundWork(undefined)).toBeNull();
  });
});

describe("MCP rows: only the actions the tool offers", () => {
  it("each row's ⋯ menu holds its own actions, and a row with none has no menu", async () => {
    setGets(OVERVIEW);
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    await screen.findByTestId("code-session-mcp");
    expect(screen.queryByTestId("code-session-mcp-more-quiet")).toBeNull();
    fireEvent.click(screen.getByTestId("code-session-mcp-more-notes"));
    const menu = screen.getByRole("menu");
    expect(within(menu).getAllByRole("menuitem").map((b) => b.textContent)).toEqual(["Reconnect", "Turn off"]);
    expect(screen.getByTestId("code-session-mcp-more-notes-off").getAttribute("title")).toContain("remembers this for this folder");
    H.postAnswer[`${BASE}/mcp/notes`] = { words: "Turned off notes. Claude Code remembers this for this folder." };
    fireEvent.click(screen.getByTestId("code-session-mcp-more-notes-off"));
    await waitFor(() => expect(H.posts).toContainEqual({ path: `${BASE}/mcp/notes`, body: { action: "off" } }));
    expect((await screen.findByTestId("code-session-mcp-said-notes")).textContent).toContain("Turned off notes.");
    fireEvent.click(screen.getByTestId("code-session-mcp-more-old"));
    expect(within(screen.getByRole("menu")).getAllByRole("menuitem").map((b) => b.textContent)).toEqual(["Turn on"]);
  });

  it("Escape closes the menu", async () => {
    setGets(OVERVIEW);
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    fireEvent.click(await screen.findByTestId("code-session-mcp-more-notes"));
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("a sign-in opens the tool's own page in the browser; a blocked window leaves the user's own link", async () => {
    const url = "https://auth.example.com/authorize?state=x";
    setGets({
      ...OVERVIEW,
      mcp: { known: true, at: "", servers: [{ name: "authy", state: "needs_sign_in", words: "Needs you to sign in", actions: ["sign_in"] }] },
    });
    H.postAnswer[`${BASE}/mcp/authy`] = { words: "Codex waits for you to sign in to authy in your browser.", url };
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    fireEvent.click(await screen.findByTestId("code-session-mcp-more-authy"));
    fireEvent.click(screen.getByTestId("code-session-mcp-more-authy-sign_in"));
    await waitFor(() => expect(open).toHaveBeenCalledWith(url, "_blank", "noopener,noreferrer"));
    const said = await screen.findByTestId("code-session-mcp-said-authy");
    expect(within(said).getByRole("link", { name: "Open the sign-in page" }).getAttribute("href")).toBe(url);
  });

  it("never opens an address that is not a web page", () => {
    expect(openableSignIn("javascript:alert(1)")).toBe("");
    expect(openableSignIn("https://u:p@h/x")).toBe("");
    expect(openableSignIn("https://h/x")).toBe("https://h/x");
    expect(decodeCodeMcp({ known: true, servers: [{ name: "a", actions: ["off", "explode", "off"] }] })!.servers[0].actions).toEqual([
      "off",
    ]);
  });

  it("an older Iron Jarvis (no actions) shows no menu on any row", async () => {
    setGets({
      ...OVERVIEW,
      mcp: { known: true, at: "", servers: [{ name: "notes", state: "connected", words: "Connected" }] },
    });
    render(<CodePanes session={SESSION} tools={TOOLS} events={[]} running={false} />);
    await screen.findByTestId("code-session-mcp-notes");
    expect(screen.queryByTestId("code-session-mcp-more-notes")).toBeNull();
  });
});

describe("effort while a reply runs", () => {
  it("is open when the daemon says the tool takes it live, held as before otherwise", () => {
    expect(TOOLS[0].effortLive).toBe(true);
    expect(effortWhileRunning("claude-code", "Claude Code", true, true)).toEqual({ held: false, defaultHeld: false, title: "" });
    expect(effortWhileRunning("claude-code", "Claude Code", true).held).toBe(true);
    const older = decodeCodingTools({ harnesses: [{ ...TOOL, effort_live: undefined }] });
    expect(older[0].effortLive).toBeUndefined();
  });
});
