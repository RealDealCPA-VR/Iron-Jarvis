/**
 * v1.557.0 — the `/` list in Build › Code works BEFORE a session exists, and
 * a session's list is never stuck on what was known when it was first asked.
 *
 * The owner: "In the build > code area, the / commands for skills dont work.
 * These should be available at any point within this prompting window."
 *
 * What was wrong (read from the live app): the start box had no `/` menu at
 * all, and a session's box asked for its list once. Claude Code's list is read
 * per folder in the background, so that one answer could be its built-in
 * commands only (or nothing) and the user's own skills never showed.
 *
 * Only the network (lib/api) and the stream reader are stand-ins.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: {} as Record<string, unknown>,
  asked: [] as string[],
  posts: [] as { path: string; body: unknown }[],
  postReply: {} as Record<string, unknown>,
  streams: [] as { path: string; body: unknown }[],
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    get: (path: string) => {
      H.asked.push(path);
      const r = H.gets[path.replace(/\?limit=1$/, "")];
      if (r === undefined) return Promise.reject(new FakeApiError(`unmocked GET ${path}`, 404));
      return Promise.resolve(typeof r === "function" ? (r as () => unknown)() : r);
    },
    post: (path: string, body?: unknown) => {
      H.posts.push({ path, body });
      return Promise.resolve(H.postReply[path] ?? {});
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useChatStream", () => ({
  streamSSE: async function* (path: string, body: unknown) {
    H.streams.push({ path, body });
  },
  useLiveText: (s: { text?: string }) => s?.text ?? "",
}));

import { BuildCodeView } from "@/components/buildCode/BuildCodeView";
import { StartComposer } from "@/components/buildCode/StartComposer";
import { slashStatusWords } from "@/components/buildCode/useToolCommands";
import { decodeCodingTools } from "@/lib/buildApp";

const MODES = [
  { id: "ask", label: "Ask first", dangerous: false },
  { id: "full", label: "Full access", dangerous: true },
];
const tool = (id: string, label: string, over: Record<string, unknown> = {}) => ({
  id,
  label,
  installed: true,
  signed_in: true,
  modes: MODES,
  hidden_modes: [],
  models: [{ id: "", label: `${label}'s default` }],
  efforts: [],
  accounts: [],
  commands: [],
  ...over,
});
const CLAUDE = tool("claude-code", "Claude Code");
const CODEX = tool("codex", "Codex", { commands: [{ name: "compact", description: "Summarize the conversation" }] });

const sess = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  harness: "claude-code",
  folder: "C:\\work\\shop",
  title: `Session ${id}`,
  account: "",
  model: "",
  effort: "",
  mode: "ask",
  harness_session_id: "",
  created_at: "2026-11-22T09:00:00Z",
  updated_at: "2026-11-22T09:00:00Z",
  archived_at: null,
  last_error: "",
  ...over,
});

const NO_FOLDER = "/build/app/commands?harness=claude-code";
const COMPACT = { name: "compact", description: "Free up context", argument_hint: "<focus>" };
const SKILL = { name: "pixio-skill", description: "Make pictures with Pixio" };

function seed(sessions: Record<string, unknown>[] = [], tools: Record<string, unknown>[] = [CLAUDE, CODEX]) {
  H.gets = {
    "/build/app/harnesses": { harnesses: tools },
    "/build/app/sessions": { sessions, work_root: "C:\\Users\\me\\Documents\\Iron Jarvis" },
    "/editor/options": { editors: [], preferred: "" },
  };
}

beforeEach(() => {
  H.asked = [];
  H.posts = [];
  H.postReply = {};
  H.streams = [];
  seed();
  window.localStorage.clear();
});

afterEach(() => cleanup());

async function startBox(toolLabel = "Claude Code"): Promise<HTMLTextAreaElement> {
  render(<BuildCodeView initialSession={null} onOpenedTerminal={() => {}} />);
  await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain(toolLabel));
  // (The chip is drawn in the commit that queues the composer's own reset.)
  await act(async () => {});
  return screen.getByTestId("code-start-box") as HTMLTextAreaElement;
}

const rowsShown = (menu: HTMLElement) => within(menu).getAllByRole("option").map((o) => o.getAttribute("data-testid"));

describe("the start box has the tool's `/` list", () => {
  it("lists the commands and skills for NO folder, picks with Enter, and the first message runs it", async () => {
    H.gets[NO_FOLDER] = { commands: [COMPACT, SKILL], reading: false };
    H.postReply["/build/app/sessions"] = sess("n1", { folder: "" });
    H.gets["/build/app/sessions/n1"] = { session: sess("n1", { folder: "" }), events: [], running: null, pending: [] };
    const box = await startBox();

    fireEvent.change(box, { target: { value: "/" } });
    const menu = await screen.findByTestId("code-start-slash-menu");
    expect(rowsShown(menu)).toEqual(["code-start-command-compact", "code-start-command-pixio-skill"]);
    expect(menu.getAttribute("aria-label")).toBe("Claude Code's commands");
    expect(within(menu).getByTestId("code-command-hint").textContent).toBe("<focus>");

    fireEvent.change(box, { target: { value: "/pix" } });
    await waitFor(() => expect(rowsShown(screen.getByTestId("code-start-slash-menu"))).toEqual(["code-start-command-pixio-skill"]));
    // Enter takes the row in front: it never starts a session with "/pix".
    fireEvent.keyDown(box, { key: "Enter" });
    expect(box.value).toBe("/pixio-skill ");
    expect(screen.queryByTestId("code-start-slash-menu")).toBeNull();
    expect(H.posts.some((p) => p.path === "/build/app/sessions")).toBe(false);

    fireEvent.change(box, { target: { value: "/pixio-skill a red fox" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(H.streams.find((s) => s.path === "/build/app/sessions/n1/turn")).toBeTruthy());
    expect(H.streams.find((s) => s.path === "/build/app/sessions/n1/turn")?.body).toMatchObject({
      text: "/pixio-skill a red fox",
    });
  });

  it("asks for the list of the FOLDER chosen there", async () => {
    const path = `/build/app/commands?harness=claude-code&folder=${encodeURIComponent("C:\\work\\shop")}`;
    H.gets[path] = { commands: [{ name: "shop-deploy", description: "Deploy the shop (project)" }], reading: false };
    render(
      <StartComposer
        tools={decodeCodingTools({ harnesses: [CLAUDE] })}
        toolsError={null}
        startFolder={"C:\\work\\shop"}
        onStarted={() => {}}
      />,
    );
    const box = screen.getByTestId("code-start-box");
    await waitFor(() => expect(screen.getByTestId("code-start-tool").textContent).toContain("Claude Code"));
    fireEvent.change(box, { target: { value: "/" } });
    expect(await screen.findByTestId("code-start-command-shop-deploy")).toBeTruthy();
    expect(H.asked).toContain(path);
    expect(H.asked).not.toContain(NO_FOLDER);
  });

  it("looks again while the list is still being read: your own skill arrives without retyping", async () => {
    let looks = 0;
    H.gets[NO_FOLDER] = () => {
      looks += 1;
      return looks < 3 ? { commands: [COMPACT], reading: true } : { commands: [COMPACT, SKILL], reading: false };
    };
    const box = await startBox();
    fireEvent.change(box, { target: { value: "/" } });
    const menu = await screen.findByTestId("code-start-slash-menu");
    expect(rowsShown(menu)).toEqual(["code-start-command-compact"]);
    await waitFor(() => expect(screen.getByTestId("code-start-command-pixio-skill")).toBeTruthy(), { timeout: 6000 });
    const settledAt = looks;
    // Read: no more looks.
    await act(() => new Promise((r) => setTimeout(r, 900)));
    expect(looks).toBe(settledAt);
  }, 15000);

  it("says the list is being read, and says when the tool listed nothing", async () => {
    let answer: Record<string, unknown> = { commands: [], reading: true };
    H.gets[NO_FOLDER] = () => answer;
    const box = await startBox();
    fireEvent.change(box, { target: { value: "/" } });
    const line = await screen.findByTestId("code-start-slash-status");
    expect(line.textContent).toBe("Reading Claude Code's commands…");
    answer = { commands: [], reading: false };
    await waitFor(
      () =>
        expect(screen.getByTestId("code-start-slash-status").textContent).toBe(
          "Claude Code listed no commands. A command you type is still sent to Claude Code as typed.",
        ),
      { timeout: 6000 },
    );
    // Words not at the start of a line open nothing.
    fireEvent.change(box, { target: { value: "fix a/b" } });
    expect(screen.queryByTestId("code-start-slash-status")).toBeNull();
    expect(screen.queryByTestId("code-start-slash-menu")).toBeNull();
  }, 15000);

  it("Esc closes the menu, and Enter then starts with the words as typed", async () => {
    H.gets[NO_FOLDER] = { commands: [COMPACT, SKILL], reading: false };
    H.postReply["/build/app/sessions"] = sess("n2", { folder: "" });
    H.gets["/build/app/sessions/n2"] = { session: sess("n2", { folder: "" }), events: [], running: null, pending: [] };
    const box = await startBox();
    fireEvent.change(box, { target: { value: "/comp" } });
    await screen.findByTestId("code-start-slash-menu");
    fireEvent.keyDown(box, { key: "Escape" });
    expect(screen.queryByTestId("code-start-slash-menu")).toBeNull();
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(H.streams.find((s) => s.path === "/build/app/sessions/n2/turn")).toBeTruthy());
    expect(H.streams.find((s) => s.path === "/build/app/sessions/n2/turn")?.body).toMatchObject({ text: "/comp" });
  });

  it("the arrows move the row in front, and a click picks too", async () => {
    H.gets[NO_FOLDER] = { commands: [COMPACT, SKILL], reading: false };
    const box = await startBox();
    fireEvent.change(box, { target: { value: "first line\n/" } });
    const menu = await screen.findByTestId("code-start-slash-menu");
    const selected = () => within(menu).getAllByRole("option").map((o) => o.getAttribute("aria-selected"));
    expect(selected()).toEqual(["true", "false"]);
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(selected()).toEqual(["false", "true"]);
    fireEvent.keyDown(box, { key: "Tab" });
    // Only the `/word` on its own line is replaced.
    expect(box.value).toBe("first line\n/pixio-skill ");

    fireEvent.change(box, { target: { value: "/" } });
    fireEvent.click(await screen.findByTestId("code-start-command-compact"));
    expect(box.value).toBe("/compact ");
  });

  it("a command only the tool's own terminal has says why and types nothing", async () => {
    H.gets[NO_FOLDER] = {
      commands: [COMPACT, { name: "login", description: "Sign in", note: "Claude Code signs in on its own screen." }],
      reading: false,
    };
    const box = await startBox();
    fireEvent.change(box, { target: { value: "/login" } });
    fireEvent.click(await screen.findByTestId("code-start-command-login"));
    expect(box.value).toBe("/login");
    expect(screen.getByTestId("code-start-slash-note").textContent).toBe(
      "/login: Claude Code signs in on its own screen.",
    );
    expect(screen.queryByTestId("code-start-slash-menu")).toBeNull();
    // The line goes when the words change.
    fireEvent.change(box, { target: { value: "/logi" } });
    expect(screen.queryByTestId("code-start-slash-note")).toBeNull();
  });

  it("a tool with its own prefix is typed its own way, and an older daemon still shows the tool's listed commands", async () => {
    seed([], [CODEX]);
    // (No /build/app/commands answer: an older daemon has no such read.)
    const box = await startBox("Codex");
    fireEvent.change(box, { target: { value: "/" } });
    expect(await screen.findByTestId("code-start-command-compact")).toBeTruthy();
    cleanup();

    H.gets["/build/app/commands?harness=codex"] = {
      commands: [{ name: "review-pr", description: "Review a pull request", prefix: "$" }],
      reading: false,
    };
    const box2 = await startBox("Codex");
    fireEvent.change(box2, { target: { value: "/rev" } });
    fireEvent.click(await screen.findByTestId("code-start-command-review-pr"));
    expect(box2.value).toBe("$review-pr ");
  });
});

describe("a session's box reads its list again each time the menu opens", () => {
  it("the view listed Claude Code's built-ins while the folder was read: your own skill still shows", async () => {
    seed([sess("a1")]);
    H.gets["/build/app/sessions/a1"] = {
      session: sess("a1"),
      events: [],
      running: false,
      pending: [],
      commands: [COMPACT],
      commands_reading: true,
    };
    H.gets["/build/app/sessions/a1/commands"] = { commands: [COMPACT, SKILL], reading: false };
    render(<BuildCodeView initialSession="a1" onOpenedTerminal={() => {}} />);
    const box = (await screen.findByTestId("code-box")) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "/pix" } });
    fireEvent.click(await screen.findByTestId("code-command-pixio-skill"));
    expect(box.value).toBe("/pixio-skill ");
  });

  it("says the list is being read instead of showing nothing", async () => {
    seed([sess("a2")]);
    H.gets["/build/app/sessions/a2"] = { session: sess("a2"), events: [], running: false, pending: [], commands: [] };
    H.gets["/build/app/sessions/a2/commands"] = { commands: [], reading: true };
    render(<BuildCodeView initialSession="a2" onOpenedTerminal={() => {}} />);
    const box = await screen.findByTestId("code-box");
    fireEvent.change(box, { target: { value: "/" } });
    expect((await screen.findByTestId("code-slash-status")).textContent).toBe("Reading Claude Code's commands…");
  });
});

describe("the words", () => {
  it("nothing is said once there is a list", () => {
    expect(slashStatusWords("Codex", true, 3)).toBeNull();
    expect(slashStatusWords("Codex", false, 1)).toBeNull();
    expect(slashStatusWords("Codex", true, 0)).toBe("Reading Codex's commands…");
    expect(slashStatusWords("Pi", false, 0)).toBe("Pi listed no commands. A command you type is still sent to Pi as typed.");
  });
});
