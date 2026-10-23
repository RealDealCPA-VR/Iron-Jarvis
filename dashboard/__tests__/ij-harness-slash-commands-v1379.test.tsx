/**
 * v1.379.0 — the IJ harness's "/" like Claude Code's.
 *
 * THE USER: "I have a lot of skills that IJ recognizes, but the / does not
 * recognize them for use. I want to be able to use the same / commands i do
 * here there in the IJ harness" ("here" = Claude Code). Pinned:
 *
 *  - a message that STARTS with "/name words" runs `name` with the words as
 *    its arguments: a skill → the skill for that message + the words as its
 *    text; one of Claude Code's custom commands → `command` + the words (the
 *    daemon expands it); an unknown "/word" is ordinary text (a path such as
 *    /etc/hosts too);
 *  - Enter on a half-typed name picks the highlighted row (a custom command
 *    RUNS, Claude Code's rule; Tab puts "/name " in the box); after a space,
 *    Enter sends, and a strip above the box says what it runs;
 *  - the menu: the session's commands, the folder's skills, Claude Code's
 *    custom commands (only at the start of a message), your skills — each with
 *    where it comes from, in quiet words; it scrolls with 80+ rows;
 *  - Claude Code's own built-ins with an equivalent: /clear (a new session on
 *    the same folder), /resume (the session search), /usage (alias /cost),
 *    /init (an ordinary message), /plan with words (plan first, then send).
 *
 * The mocks are ij-harness-slash-at-v1373's (copied, not imported).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : undefined;
    const value = typeof hit === "function" ? (hit as (p: string) => unknown)(path) : hit;
    if (value instanceof Promise) return value;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value ?? {});
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

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import {
  INIT_PROMPT,
  decodeCommands,
  leadingSlash,
  resolveTyped,
  sessionCommands,
  sessionUsageWords,
  slashRows,
  sourceWords,
} from "@/lib/buildComposer";

/* ------------------------------------------------------------- fixtures */

const FOLDER = "C:\\work\\shop";
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Add a cart",
    updated_at: "2026-10-22T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: FOLDER,
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    ...over,
  };
}

const COMMANDS = {
  commands: [
    { name: "review", description: "Review a file", argument_hint: "[file]", source: "personal", plugin: "", kind: "command" },
    { name: "ship", description: "Ship it", argument_hint: "", source: "project", plugin: "", kind: "command" },
    { name: "tools:lint", description: "Lint it", argument_hint: "[path]", source: "plugin", plugin: "tools", kind: "command" },
    {
      name: "cowork:brief-me", description: "A brief", argument_hint: "", source: "plugin", plugin: "cowork",
      kind: "skill", skill: "brief-me",
    },
  ],
  note: "",
};

function sse(frames: [string, unknown][]): Response {
  const text = frames.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join("");
  return new Response(
    new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode(text));
        c.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

/** A controllable SSE body: `push` frames, `end` it. */
function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    push: (event: string, data: unknown) => ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => ctl.close(),
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1": view(),
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": {
      skills: [
        { name: "bulleteer", description: "answer as bullets", source: "user" },
        { name: "pixio-skill", description: "make pictures", source: "claude" },
        { name: "brief-me", description: "A brief", source: "claude" },
      ],
    },
    "/build/sessions/s1/skills": {
      skills: [{ name: "release-notes", description: "write release notes", rel: ".ironjarvis/harness/skills/release-notes/SKILL.md" }],
      note: "",
    },
    "/build/sessions/s1/commands": COMMANDS,
  };
  api.calls = [];
  localStorage.clear();
  fetchMock = vi.fn(async () => sse([["done", { reply: "ok" }]]));
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderSession(props: Record<string, unknown> = {}) {
  return render(
    <HarnessSession
      threadId="s1"
      terminal={terminal}
      listRunning={false}
      onChanged={() => {}}
      onGone={() => {}}
      {...props}
    />,
  );
}

const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const key = (k: string, extra: Record<string, unknown> = {}) => fireEvent.keyDown(box(), { key: k, ...extra });
const turnBody = (i = 0) => JSON.parse(String((fetchMock.mock.calls[i] as unknown as [string, RequestInit])[1].body));
const turnPath = (i = 0) => String((fetchMock.mock.calls[i] as unknown as [string, RequestInit])[0]);
const NET = { timeout: 5000 };
const rowNames = (testId: string) => screen.queryAllByTestId(testId).map((el) => el.getAttribute("data-name"));
/** The lists the "/" reads have all answered. */
const listsRead = () =>
  ["/skills", "/build/sessions/s1/skills", "/build/sessions/s1/commands"].every((p) =>
    api.calls.some((c) => c.method === "GET" && c.path === p),
  );

async function opened(props: Record<string, unknown> = {}) {
  renderSession(props);
  await screen.findByTestId("harness-empty");
}

/** Type a whole line that starts with "/" and wait until it can run. */
async function typed(text: string) {
  type(text);
  await waitFor(() => expect(listsRead()).toBe(true), NET);
  // One more turn of the loop for the answers to land in state.
  await act(async () => {});
}

/* ---------------------------------------------------------------- tests */

describe("a typed “/name words” runs that name with the words as its arguments", () => {
  it("a skill: the skill rides that one message and the words are its text", async () => {
    await opened();
    await typed("/pixio-skill make me a logo");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1), NET);
    expect(turnBody()).toMatchObject({ text: "make me a logo", skill: "pixio-skill" });
    expect(turnBody()).not.toHaveProperty("command");
    expect(await screen.findByTestId("harness-sent-skill")).toHaveTextContent("pixio-skill");
    expect(box().value).toBe("");
  });

  it("the folder's skill rides as `folder_skill`; a skill alone (no words) is a message", async () => {
    await opened();
    await typed("/release-notes");
    // No space yet: the menu is open and Enter PICKS the row (a chip)…
    key("Enter");
    expect(await screen.findByTestId("harness-skill-chip")).toHaveTextContent("release-notes");
    expect(fetchMock).not.toHaveBeenCalled();
    // …and the chip alone is a message.
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1), NET);
    expect(turnBody()).toMatchObject({ text: "", folder_skill: "release-notes" });
  });

  it("one of Claude Code's custom commands: `command` and its words; the question shows what was typed", async () => {
    const s = controlledStream();
    fetchMock.mockResolvedValueOnce(s.response);
    await opened();
    await typed("/review src/cart.ts please");
    // While its words are written: what Enter runs, its hint, its source.
    const strip = await screen.findByTestId("harness-slash-invoking");
    expect(strip.getAttribute("data-name")).toBe("review");
    expect(strip).toHaveTextContent("/review");
    expect(strip).toHaveTextContent("[file]");
    expect(strip).toHaveTextContent("from your commands");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1), NET);
    expect(turnPath()).toContain("/build/sessions/s1/turn");
    expect(turnBody()).toMatchObject({ text: "src/cart.ts please", command: "review" });
    expect(turnBody()).not.toHaveProperty("skill");
    expect(await screen.findByTestId("harness-user-bubble")).toHaveTextContent("/review src/cart.ts please");
    await act(async () => {
      s.push("done", { reply: "ok" });
      s.end();
    });
  });

  it("a plugin's command and a plugin's skill by Claude Code's own names", async () => {
    await opened();
    await typed("/tools:lint src");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1), NET);
    expect(turnBody()).toMatchObject({ text: "src", command: "tools:lint" });
    await waitFor(() => expect(screen.getByTestId("harness-send")).toBeTruthy(), NET);
    await typed("/cowork:brief-me today");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2), NET);
    // The app's own skill of that name runs.
    expect(turnBody(1)).toMatchObject({ text: "today", skill: "brief-me" });
  });

  it("an unknown “/word” is ordinary text — a path too", async () => {
    await opened();
    // A path is not a "/name" at all: nothing is even looked up.
    type("/etc/hosts is broken");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1), NET);
    expect(turnBody()).toEqual(expect.objectContaining({ text: "/etc/hosts is broken" }));
    expect(turnBody()).not.toHaveProperty("command");
    await waitFor(() => expect(screen.getByTestId("harness-send")).toBeTruthy(), NET);
    await typed("/nope do it");
    expect(screen.queryByTestId("harness-slash-invoking")).toBeNull();
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2), NET);
    expect(turnBody(1).text).toBe("/nope do it");
    expect(turnBody(1)).not.toHaveProperty("command");
    expect(turnBody(1)).not.toHaveProperty("skill");
  });

  it("Enter on a half-typed name RUNS the highlighted custom command; Tab puts “/name ” in the box", async () => {
    await opened();
    type("/revi");
    await waitFor(() => expect(rowNames("harness-slash-custom")).toEqual(["review"]), NET);
    key("Tab");
    expect(box().value).toBe("/review ");
    expect(fetchMock).not.toHaveBeenCalled();
    type("/review app.py");
    await act(async () => {});
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1), NET);
    expect(turnBody()).toMatchObject({ text: "app.py", command: "review" });
    await waitFor(() => expect(screen.getByTestId("harness-send")).toBeTruthy(), NET);
    type("/shi");
    await waitFor(() => expect(rowNames("harness-slash-custom")).toEqual(["ship"]), NET);
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2), NET);
    expect(turnBody(1)).toMatchObject({ text: "", command: "ship" });
  });

  it("Enter waits while the lists are still being read (it would send the words as plain text)", async () => {
    let release!: (v: unknown) => void;
    api.gets["/build/sessions/s1/commands"] = () => new Promise((r) => (release = r));
    await opened();
    type("/review app.py");
    await waitFor(() => expect(api.calls.some((c) => c.path === "/build/sessions/s1/commands")).toBe(true), NET);
    key("Enter");
    await act(async () => {});
    expect(fetchMock).not.toHaveBeenCalled();
    expect(box().value).toBe("/review app.py");
    await act(async () => release(COMMANDS));
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1), NET);
    expect(turnBody()).toMatchObject({ text: "app.py", command: "review" });
  });

  it("while a reply runs, a typed command waits to be sent after it (it is never a note)", async () => {
    const s = controlledStream();
    fetchMock.mockResolvedValueOnce(s.response);
    await opened();
    type("first");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1), NET);
    await typed("/review app.py");
    key("Enter");
    expect(await screen.findByTestId("harness-queued")).toHaveTextContent("Waiting to send: /review app.py");
    expect(api.calls.some((c) => c.path.endsWith("/steer"))).toBe(false);
    await act(async () => {
      s.push("done", { reply: "ok" });
      s.end();
    });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2), NET);
    expect(turnBody(1)).toMatchObject({ text: "app.py", command: "review" });
  });
});

describe("the “/” menu lists every command and skill with where it comes from", () => {
  it("commands, the folder's skills, Claude Code's commands, then yours — each with its source", async () => {
    await opened();
    type("/");
    const menu = await screen.findByTestId("harness-slash-menu");
    await waitFor(() => expect(rowNames("harness-slash-custom")).toEqual(["review", "ship", "tools:lint"]), NET);
    expect(rowNames("harness-slash-command")).toEqual([
      "plan", "compact", "rewind", "model", "clear", "resume", "usage", "init",
    ]);
    // The plugin's skill under Claude Code's name, in place of the app's row.
    expect(rowNames("harness-slash-skill")).toEqual(["release-notes", "bulleteer", "pixio-skill", "cowork:brief-me"]);
    const order = Array.from(menu.querySelectorAll("[role=option]")).map((el) => el.getAttribute("data-name"));
    expect(order.indexOf("init")).toBeLessThan(order.indexOf("release-notes"));
    expect(order.indexOf("release-notes")).toBeLessThan(order.indexOf("review"));
    expect(order.indexOf("tools:lint")).toBeLessThan(order.indexOf("bulleteer"));
    const source = (name: string) =>
      within(menu.querySelector(`[data-name="${name}"]`) as HTMLElement).queryByTestId("harness-slash-source")
        ?.textContent ?? "";
    expect(source("review")).toBe("from your commands");
    expect(source("ship")).toBe("this folder");
    expect(source("tools:lint")).toBe("from tools");
    expect(source("release-notes")).toBe("this folder");
    expect(source("bulleteer")).toBe("yours");
    expect(source("pixio-skill")).toBe("from Claude Code");
    expect(source("cowork:brief-me")).toBe("from cowork");
    expect(source("plan")).toBe("");
    // The argument hint rides the row.
    expect(menu.querySelector('[data-name="review"]')).toHaveTextContent("[file]");
  });

  it("Claude Code's custom commands are offered only at the START of a message", async () => {
    await opened();
    type("fix it /re");
    await waitFor(() => expect(rowNames("harness-slash-command")).toContain("rewind"), NET);
    await waitFor(() => expect(listsRead()).toBe(true), NET);
    await act(async () => {});
    expect(rowNames("harness-slash-custom")).toEqual([]);
    type("/re");
    await waitFor(() => expect(rowNames("harness-slash-custom")).toEqual(["review"]), NET);
  });

  it("it scrolls and stays usable with 80+ rows", async () => {
    const many = Array.from({ length: 90 }, (_, i) => ({
      name: `skill-${String(i).padStart(2, "0")}`,
      description: `skill number ${i}`,
      source: "claude",
    }));
    api.gets["/skills"] = { skills: many };
    const seen: Element[] = [];
    Element.prototype.scrollIntoView = function scrollIntoView(this: Element) {
      seen.push(this);
    } as typeof Element.prototype.scrollIntoView;
    await opened();
    type("/");
    const menu = await screen.findByTestId("harness-slash-menu");
    await waitFor(() => expect(screen.getAllByTestId("harness-slash-skill").length).toBe(91), NET);
    expect(menu.className).toContain("overflow-y-auto");
    const total = screen.getAllByRole("option").length;
    expect(total).toBeGreaterThan(80);
    for (let i = 0; i < total + 3; i += 1) key("ArrowDown");
    const rows = screen.getAllByRole("option");
    expect(rows[total - 1].getAttribute("aria-selected")).toBe("true");
    expect(seen[seen.length - 1]).toBe(rows[total - 1]);
    // ↑ comes back; Escape closes it without sending.
    key("ArrowUp");
    expect(screen.getAllByRole("option")[total - 2].getAttribute("aria-selected")).toBe("true");
    key("Escape");
    await waitFor(() => expect(screen.queryByTestId("harness-slash-menu")).toBeNull());
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("Claude Code's built-in commands with an IJ harness equivalent", () => {
  it("/clear starts a new session on the same folder (the picked folder of a branch session)", async () => {
    const onNewSession = vi.fn(async () => null);
    await opened({ onNewSession });
    await typed("/clear");
    key("Enter");
    await waitFor(() => expect(onNewSession).toHaveBeenCalledWith(FOLDER), NET);
    expect(fetchMock).not.toHaveBeenCalled();
    cleanup();
    api.gets["/build/sessions/s1"] = view({
      folder: "C:\\work\\shop.ij-worktrees\\cart-1a2b",
      git: { branch: "ij/cart-1a2b", base: "main", checkout: FOLDER, worktree: "C:\\work\\shop.ij-worktrees\\cart-1a2b" },
    });
    const again = vi.fn(async () => "That folder cannot be used.");
    await opened({ onNewSession: again });
    // Claude Code's alias for it.
    await typed("/new ");
    key("Enter");
    await waitFor(() => expect(again).toHaveBeenCalledWith(FOLDER), NET);
    expect(await screen.findByText("That folder cannot be used.")).toBeTruthy();
  });

  it("/resume puts the caret in the session search", async () => {
    const onFindSessions = vi.fn();
    await opened({ onFindSessions });
    await typed("/resume");
    key("Enter");
    await waitFor(() => expect(onFindSessions).toHaveBeenCalledTimes(1), NET);
  });

  it("/usage (and Claude Code's /cost) says what this session used", async () => {
    api.gets["/chat/threads/s1/usage"] = {
      turns: 3, failed_turns: 0, input_tokens: 1200, output_tokens: 300, cost_usd: 0, list_price_equivalent_usd: 0.42,
    };
    await opened();
    await typed("/cost ");
    key("Enter");
    const note = await screen.findByTestId("harness-usage-note", {}, NET);
    expect(note).toHaveTextContent("This session used 1,500 tokens over 3 replies · ≈$0.42 at list price (subscription).");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("/init is an ordinary message asking for a CLAUDE.md; a session with no folder has no /init", async () => {
    await opened();
    await typed("/init");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1), NET);
    expect(turnBody().text).toBe(INIT_PROMPT);
    expect(sessionCommands("act", { hasFolder: false }).map((c) => c.name)).not.toContain("init");
  });

  it("/plan with words plans first, then sends the words", async () => {
    await opened();
    await typed("/plan fix the login page");
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1), NET);
    expect(api.calls.find((c) => c.method === "PATCH" && c.path === "/build/sessions/s1")?.body).toEqual({ mode: "plan" });
    const patchAt = api.calls.findIndex((c) => c.method === "PATCH");
    expect(patchAt).toBeGreaterThanOrEqual(0);
    expect(turnBody().text).toBe("fix the login page");
  });
});

describe("the rules on their own", () => {
  it("leadingSlash: only at the start, a path is not a command", () => {
    expect(leadingSlash("/review a b")).toEqual({ name: "review", args: "a b", hasSpace: true });
    expect(leadingSlash("/review")).toEqual({ name: "review", args: "", hasSpace: false });
    expect(leadingSlash("  /x:y  z ")).toEqual({ name: "x:y", args: "z", hasSpace: true });
    expect(leadingSlash("/etc/hosts")).toBeNull();
    expect(leadingSlash("fix /review")).toBeNull();
    expect(leadingSlash("/")).toBeNull();
  });

  it("resolveTyped: the session's commands, then skills, then Claude Code's commands — case-insensitive", () => {
    const cmds = sessionCommands("act");
    const folder = [{ name: "ship", description: "", source: "folder" as const }];
    const yours = [{ name: "lint", description: "", source: "yours" as const, origin: "claude" }];
    const custom = decodeCommands({
      commands: [
        { name: "ship", source: "personal", kind: "command" },
        { name: "lint", source: "project", kind: "command" },
        { name: "deploy", source: "personal", kind: "command" },
        { name: "plan", source: "personal", kind: "command" },
      ],
    });
    expect(resolveTyped("/PLAN x", cmds, folder, yours, custom)?.kind).toBe("command");
    expect(resolveTyped("/checkpoint", cmds, folder, yours, custom)).toMatchObject({ kind: "command", command: { id: "rewind" } });
    // A skill beats a command file of the same name (Claude Code's rule).
    expect(resolveTyped("/ship now", cmds, folder, yours, custom)).toMatchObject({ kind: "skill", args: "now" });
    expect(resolveTyped("/lint", cmds, folder, yours, custom)).toMatchObject({ kind: "skill" });
    expect(resolveTyped("/Deploy prod", cmds, folder, yours, custom)).toMatchObject({ kind: "custom", args: "prod" });
    expect(resolveTyped("/nope", cmds, folder, yours, custom)).toBeNull();
  });

  it("decodeCommands keeps only what the daemon promised; sourceWords and usage words", () => {
    expect(decodeCommands({ commands: [{ name: "a b", source: "personal" }, { name: "x", source: "weird" }, null] })).toEqual([]);
    expect(decodeCommands({})).toEqual([]);
    const rows = slashRows("", sessionCommands("act"), [], [{ name: "s", description: "", source: "yours", origin: "codex" }]);
    expect(sourceWords(rows[rows.length - 1])).toBe("from Codex");
    expect(sessionUsageWords({ turns: 0 })).toBe("This session has not used anything yet.");
    expect(sessionUsageWords({ turns: 1, input_tokens: 10, output_tokens: 5, cost_usd: 0.004 })).toBe(
      "This session used 15 tokens over 1 reply · under $0.01.",
    );
  });
});
