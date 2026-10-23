/**
 * v1.379.0 REVIEW — the "/" and no-folder defects the review found, each red
 * on the doer's code first:
 *
 *  - Enter on "/review" ran /review-pr: the menu's first row was the one that
 *    SORTED first, never the exact name. An exact name now leads.
 *  - A custom command a skill shares its name with was offered, and its row
 *    ran the command — while typing the same name runs the skill (Claude
 *    Code: "A skill and a file in .claude/commands/: The skill"). Not offered.
 *  - "/compact <words>" dropped the words (Claude Code: instructions for the
 *    summary); they are sent as `focus`.
 *  - "/model <name>" only opened the menu; it picks that model when exactly
 *    one in the menu is called so.
 *  - The merge with v1.378.0: New session in front of a session with NO
 *    folder opens with no folder (the box empty, the folder list closed —
 *    Start makes another one), never the Terminals view's folder.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    models: [] as unknown[],
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
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: api.models, usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder }: { folder: string | null }) => <div data-testid="files-panel">{folder}</div>,
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { HarnessSidebar } from "@/components/harness/HarnessSidebar";
import { decodeCommands, modelByName, resolveTyped, sessionCommands, slashRows } from "@/lib/buildComposer";

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
    setup: { provider: "claude-cli", model: "sonnet" },
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    ...over,
  };
}

const COMMANDS = {
  commands: [
    { name: "review-pr", description: "Review a pull request", argument_hint: "", source: "personal", plugin: "", kind: "command" },
    { name: "review", description: "Review a file", argument_hint: "[file]", source: "personal", plugin: "", kind: "command" },
    { name: "lint", description: "The command file called lint", argument_hint: "", source: "project", plugin: "", kind: "command" },
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

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1": view(),
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [{ name: "lint", description: "your lint skill", source: "user" }] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
    "/build/sessions/s1/commands": COMMANDS,
  };
  api.calls = [];
  api.models = [];
  localStorage.clear();
  fetchMock = vi.fn(async () => sse([["done", { reply: "ok" }]]));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const NET = { timeout: 5000 };
const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
const type = (text: string) => fireEvent.change(box(), { target: { value: text } });
const key = (k: string) => fireEvent.keyDown(box(), { key: k });
const turnBody = (i = 0) => JSON.parse(String((fetchMock.mock.calls[i] as unknown as [string, RequestInit])[1].body));
const listsRead = () =>
  ["/skills", "/build/sessions/s1/skills", "/build/sessions/s1/commands"].every((p) =>
    api.calls.some((c) => c.method === "GET" && c.path === p),
  );

async function opened() {
  render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
  await screen.findByTestId("harness-empty");
}

describe("an exact name leads, and a name a skill holds is the skill's", () => {
  it("slashRows: the exact name first; a custom command shadowed by a skill is not offered", () => {
    const custom = decodeCommands(COMMANDS);
    const yours = [{ name: "lint", description: "", source: "yours" as const, origin: "user" }];
    const rows = slashRows("review", sessionCommands("act"), [], yours, null, custom);
    expect(rows.map((r) => (r.kind === "custom" ? r.command.name : r.kind))).toEqual(["review", "review-pr"]);
    const lint = slashRows("lint", sessionCommands("act"), [], yours, null, custom);
    expect(lint.map((r) => `${r.kind}:${r.kind === "custom" ? r.command.name : r.kind === "skill" ? r.skill.name : ""}`))
      .toEqual(["skill:lint"]);
    // Typing it runs the skill too: the menu and the typed name agree.
    expect(resolveTyped("/lint", sessionCommands("act"), [], yours, custom)).toMatchObject({ kind: "skill" });
  });

  it("Enter on “/review” runs /review — not /review-pr, which sorts first", async () => {
    await opened();
    type("/review");
    await waitFor(() => expect(listsRead()).toBe(true), NET);
    await waitFor(
      () => expect(screen.getAllByTestId("harness-slash-custom")[0]).toHaveAttribute("data-name", "review"),
      NET,
    );
    key("Enter");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1), NET);
    expect(turnBody()).toMatchObject({ command: "review", text: "" });
  });
});

describe("Claude Code's words after a built-in command are used", () => {
  it("“/compact keep the API notes” sends the words as the summary's focus", async () => {
    await opened();
    type("/compact keep the API notes");
    await waitFor(() => expect(listsRead()).toBe(true), NET);
    await act(async () => {});
    key("Enter");
    await waitFor(
      () => expect(api.calls.find((c) => c.method === "POST" && c.path === "/build/sessions/s1/compact")).toBeTruthy(),
      NET,
    );
    const call = api.calls.find((c) => c.method === "POST" && c.path === "/build/sessions/s1/compact");
    expect(call?.body).toEqual({ focus: "keep the API notes" });
  });

  it("“/model <name>” picks the one model called so; anything else opens the menu", async () => {
    api.models = [
      { provider: "claude-cli", model: "opus", label: "Claude Opus", available: true },
      { provider: "claude-cli", model: "sonnet", label: "Claude Sonnet", available: true },
      { provider: "codex-cli", model: "gpt-6", label: "GPT-6", available: true },
    ];
    await opened();
    type("/model gpt-6");
    await waitFor(() => expect(listsRead()).toBe(true), NET);
    await act(async () => {});
    key("Enter");
    await waitFor(
      () => expect(api.calls.find((c) => c.method === "PATCH" && c.path === "/build/sessions/s1")).toBeTruthy(),
      NET,
    );
    expect(api.calls.find((c) => c.method === "PATCH")?.body).toEqual({ provider: "codex-cli", model: "gpt-6" });
  });

  it("modelByName: id, provider::id or the shown name; ambiguous or unknown is null", () => {
    const rows = [
      { provider: "claude-cli", model: "opus", label: "Claude Opus", available: true },
      { provider: "anthropic", model: "opus", label: "Claude Opus (API)", available: true },
      { provider: "codex-cli", model: "gpt-6", available: false },
    ];
    expect(modelByName(rows, "Claude  OPUS")).toBe("claude-cli::opus");
    expect(modelByName(rows, "anthropic::opus")).toBe("anthropic::opus");
    expect(modelByName(rows, "opus")).toBeNull();
    expect(modelByName(rows, "opus", "anthropic")).toBe("anthropic::opus");
    expect(modelByName(rows, "gpt-6")).toBeNull();
    expect(modelByName(rows, "nothing")).toBeNull();
  });
});

describe("New session in front of a session with no folder (the merge with v1.378.0)", () => {
  const FOLDERS = [
    {
      path: "",
      name: "No folder",
      isProject: false,
      projectId: null,
      noFolder: true,
      sessions: [{ id: "n1", title: "Plan a trip", updatedAt: "2026-10-23T10:00:00", running: false, waiting: false, archived: false }],
    },
  ];

  it("opens with no folder — the box empty, the folder list closed — and Start makes one with no folder", async () => {
    const onStart = vi.fn(async () => null);
    render(
      <HarnessSidebar
        folders={FOLDERS as never}
        listError={null}
        selectedId="n1"
        onSelect={() => {}}
        onStart={onStart}
        onRename={async () => null}
        defaultFolder={""}
        startWithNoFolder
        terminals={[]}
        onOpenTerminal={() => {}}
        workRoot="C:\\Users\\me\\Documents\\Iron Jarvis"
      />,
    );
    fireEvent.click(screen.getByTestId("harness-new-session"));
    expect((screen.getByTestId("harness-new-folder") as HTMLInputElement).value).toBe("");
    expect(screen.queryByTestId("tree")).toBeNull();
    expect(screen.getByTestId("harness-new-no-folder-line")).toHaveTextContent("No folder");
    fireEvent.submit(screen.getByTestId("harness-new-session-form"));
    await waitFor(() => expect(onStart).toHaveBeenCalledWith(""), NET);
  });
});
