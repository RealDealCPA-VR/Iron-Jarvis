/**
 * v1.372.0 (IJ harness part 2, Phase 1) — a Build session's HELPER AGENTS on
 * the screen (components/harness/HarnessHelpers + HarnessSession), driven
 * through the REAL chat stream (lib/useChatStream) against a fetch double.
 *
 *  - each helper is ONE row while the turn runs (the chat's own helper row),
 *    its steps arrive live from the turn's `helper` frames — no reload — and
 *    its own `delegate` tool card is not drawn a second time;
 *  - a helper waiting to start says so ("3 helpers are already working");
 *  - a helper's own ask is the chat's approval card, answered on its row
 *    through POST /chat/approvals/{id};
 *  - when the turn ends the reply keeps each helper (the line's `helpers`):
 *    "The researcher worked for … · N steps", "Open run", and opened, what it
 *    was asked, its report and its steps; a helper refused before it started
 *    says so in one failed row.
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
    postAnswers: {} as Record<string, unknown>,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : api.postAnswers[`${method} ${path}`];
    const value = typeof hit === "function" ? (hit as (p: string) => unknown)(path) : hit;
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
import { applyHelperFrame, decodeSavedHelpers, helperName } from "@/lib/buildHelpers";
import { decodeMessage } from "@/lib/buildSessions";

/* ------------------------------------------------------------- fixtures */

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Find config reads",
    updated_at: "2026-10-22T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
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
  api.gets = { "/build/sessions/s1": view(), "/build/accounts": new api.ApiError("no such route", 404) };
  api.calls = [];
  api.postAnswers = {};
  localStorage.clear();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderSession() {
  return render(
    <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
async function send(text: string) {
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.keyDown(box(), { key: "Enter" });
}

const helperRow = (id: string) =>
  screen.getAllByTestId("helper-row").find((el) => el.getAttribute("data-helper") === id) ?? null;

/** The live tool lists that are NOT inside a helper's row. */
const ownToolRows = () =>
  screen.queryAllByTestId("live-tool-rows").filter((el) => !el.closest('[data-testid="helper-row"]'));

/* ---------------------------------------------------------------- tests */

describe("a helper is ONE row while the turn runs, fed by the turn's own stream", () => {
  it("shows who, what it was asked and its steps as they come, without a reload", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("find every config read");
    const reads = api.calls.filter((c) => c.method === "GET" && c.path === "/build/sessions/s1").length;
    await act(async () => {
      s.push("tool_call", { id: "h1", name: "delegate", status: "started", args: { task: "Search src." } });
      s.push("helper", { id: "h1", agent: "researcher", task: "Search src.", state: "starting", steps: 0, since: 1000 });
    });
    await waitFor(() => expect(helperRow("h1")).not.toBeNull());
    expect(helperRow("h1")).toHaveTextContent("The researcher is starting");
    await act(async () => {
      s.push("helper", { id: "h1", agent: "researcher", task: "Search src.", state: "running", steps: 0, since: 1000, session: "session_k1" });
      s.push("helper", {
        id: "h1", agent: "researcher", task: "Search src.", state: "running", steps: 0, since: 1000, session: "session_k1",
        step: { id: "r1", name: "read_file", status: "started", args: { path: "src/config.py" } },
      });
    });
    await waitFor(() => expect(helperRow("h1")).toHaveAttribute("data-state", "running"));
    const row = helperRow("h1")!;
    expect(row).toHaveTextContent("The researcher is working");
    expect(within(row).getByTestId("helper-asked")).toHaveTextContent("Asked: Search src.");
    expect(within(row).getByTestId("live-tool-rows")).toBeInTheDocument();
    // The helper's own `delegate` card is not drawn a second time.
    expect(ownToolRows()).toEqual([]);
    // No re-read of the session was needed to see any of it.
    expect(api.calls.filter((c) => c.method === "GET" && c.path === "/build/sessions/s1").length).toBe(reads);
    // It ends: the same row now reads as settled, with its report.
    await act(async () => {
      s.push("helper", {
        id: "h1", agent: "researcher", task: "Search src.", state: "running", steps: 1, since: 1000, session: "session_k1",
        step: { id: "r1", name: "read_file", status: "finished", ok: true },
      });
    });
    await waitFor(() => expect(helperRow("h1")).toHaveTextContent("1 step so far"));
    await act(async () => {
      s.push("helper", {
        id: "h1", agent: "researcher", task: "Search src.", state: "done", steps: 1, since: 1000, ended: 43000,
        session: "session_k1", result: "src/config.py reads it.",
      });
    });
    await waitFor(() => expect(helperRow("h1")).toHaveAttribute("data-state", "done"));
    expect(helperRow("h1")).toHaveTextContent("The researcher worked for");
    expect(helperRow("h1")).toHaveTextContent("1 step");
    s.end();
  });

  it("a helper that waits for a free slot says so", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("four parts");
    await act(async () => {
      s.push("helper", { id: "q4", agent: "builder", task: "Part 4.", state: "queued", steps: 0, since: 1, note: "3 helpers are already working" });
    });
    await waitFor(() => expect(helperRow("q4")).not.toBeNull());
    expect(helperRow("q4")).toHaveTextContent("The builder is waiting to start");
    expect(helperRow("q4")).toHaveTextContent("3 helpers are already working");
    s.end();
  });

  it("a helper's own ask is the approval card on its row, answered there", async () => {
    const s = controlledStream();
    fetchMock.mockImplementation(async () => s.response);
    renderSession();
    await screen.findByTestId("harness-empty");
    await send("run it");
    await act(async () => {
      s.push("helper", {
        id: "h2", agent: "builder", task: "Run echo.", state: "waiting", steps: 0, since: 1, session: "session_k2",
        ask: { id: "apr_1", tool: "shell", args: { command: "echo hi" } },
      });
    });
    const ask = await screen.findByTestId("helper-ask");
    expect(within(ask).getByTestId("chat-approval-card")).toHaveTextContent("run a command");
    expect(ask).toHaveTextContent("echo hi");
    expect(helperRow("h2")).toHaveTextContent("Waiting for your OK");
    // An agent run's wider answer is named for the run, never the chat.
    expect(within(ask).queryByText(/for this chat/i)).toBeNull();
    fireEvent.click(within(ask).getByRole("button", { name: /Allow once/ }));
    await waitFor(() =>
      expect(api.calls).toContainEqual({ method: "POST", path: "/chat/approvals/apr_1", body: { decision: "once" } }),
    );
    // The answer landed: the next frame clears the ask.
    await act(async () => {
      s.push("helper", { id: "h2", agent: "builder", task: "Run echo.", state: "running", steps: 0, since: 1, session: "session_k2", ask: null });
    });
    await waitFor(() => expect(screen.queryByTestId("helper-ask")).toBeNull());
    s.end();
  });
});

describe("the reply keeps each helper", () => {
  const savedLine = {
    role: "assistant",
    content: "Both helpers reported.",
    turnId: "t1",
    route: { provider: "mock", model: "mock-1", reason: "mock" },
    steps: [{ name: "delegate", ok: true, ms: 40000 }],
    timing: { startedAt: 1, firstTokenAt: 2, endedAt: 3 },
    helpers: [
      {
        id: "a1", session: "session_k1", agent: "researcher", task: "Search src.", state: "done", steps: 2,
        startedAt: 1000, endedAt: 43000, result: "src/config.py reads it.",
        work: [{ name: "read_file", ok: true, ms: null }, { name: "grep", ok: true, ms: null }],
      },
      { id: "a2", agent: "remote:hermes", task: "Ask hermes.", state: "failed", steps: 0, result: "'remote:hermes' runs on another computer." },
    ],
  };

  it("draws a settled row that opens to what it was asked, its report and its steps", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [{ role: "user", content: "find config reads", turnId: "t1" }, savedLine],
    });
    renderSession();
    await screen.findByText("Both helpers reported.");
    const row = helperRow("a1")!;
    expect(row).toHaveAttribute("data-state", "done");
    expect(row).toHaveTextContent("The researcher worked for 42 s");
    expect(row).toHaveTextContent("2 steps");
    expect(within(row).getByTestId("agent-run-open")).toHaveAttribute("href", "/sessions/session_k1");
    fireEvent.click(within(row).getByTestId("agent-run-toggle"));
    expect(within(row).getByTestId("agent-run-why")).toHaveTextContent("Asked: Search src.");
    expect(within(row).getByTestId("helper-result")).toHaveTextContent("src/config.py reads it.");
    expect(within(row).getByTestId("work-line")).toBeInTheDocument();
    // Refused before it started: one failed row with the reason, no "Open run".
    const refused = helperRow("a2")!;
    expect(refused).toHaveTextContent("The hermes remote agent could not start");
    expect(refused).toHaveTextContent("runs on another computer");
    expect(within(refused).queryByTestId("agent-run-open")).toBeNull();
  });

  it("a helper stopped while it waited for a slot says it never started", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [
        { role: "user", content: "go", turnId: "t2" },
        { ...savedLine, turnId: "t2", helpers: [{ id: "q4", agent: "builder", task: "Part 4.", state: "stopped", steps: 0, result: "Stopped by the user before the helper finished." }] },
      ],
    });
    renderSession();
    await waitFor(() => expect(helperRow("q4")).not.toBeNull());
    expect(helperRow("q4")).toHaveTextContent("The builder was stopped before it started");
    expect(helperRow("q4")).not.toHaveTextContent("could not start");
  });

  it("the session's line decoder keeps `helpers` (the daemon's LINE_KEYS)", () => {
    const m = decodeMessage(savedLine);
    expect(m && (m as { helpers?: unknown }).helpers).toEqual(savedLine.helpers);
  });
});

describe("lib/buildHelpers reads only what it can trust", () => {
  it("folds frames in call order and ignores junk", () => {
    let hs = applyHelperFrame([], { id: "x", agent: "builder", state: "running", steps: 0 });
    hs = applyHelperFrame(hs, { id: "y", agent: "researcher", state: "queued", steps: 0 });
    hs = applyHelperFrame(hs, { id: "x", state: "not-a-state", steps: "many" });
    hs = applyHelperFrame(hs, { state: "done" });
    expect(hs.map((h) => [h.id, h.state])).toEqual([["x", "running"], ["y", "queued"]]);
    hs = applyHelperFrame(hs, { id: "x", step: { id: "s", name: "read_file", status: "finished", ok: true } });
    expect(hs[0].tools).toEqual([expect.objectContaining({ id: "s", name: "read_file", status: "done", ok: true })]);
  });

  it("decodes saved helpers defensively", () => {
    expect(decodeSavedHelpers("nope")).toEqual([]);
    expect(decodeSavedHelpers([{ agent: "builder" }, null, { id: "z", state: "weird", work: [{ name: 3 }] }])).toEqual([
      expect.objectContaining({ id: "z", state: "stopped", work: [] }),
    ]);
    expect(helperName("researcher")).toBe("The researcher");
    expect(helperName("custom:scout")).toBe("Your scout agent");
    expect(helperName("")).toBe("A helper");
  });
});
