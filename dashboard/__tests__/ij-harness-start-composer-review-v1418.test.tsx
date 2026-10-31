/**
 * v1.418.0 (fix plan C2.6) — the INDEPENDENT REVIEW's pins on the start
 * composer, each red on the first cut:
 *
 *  - Ctrl+N (and New session, and a folder's "+") while the composer is
 *    already open used to REMOUNT it: the words typed and the folder chosen
 *    were dropped without a word.
 *  - A first message the daemon refused BEFORE its turn started (a 400 on
 *    POST …/turn) left the session the start had just made as an EMPTY row
 *    (the v1.412.0 clean-up of abandoned sessions was removed with New
 *    session's create): now that row is removed with the daemon's own
 *    `only_if_empty` guard and the view goes back to the composer with the
 *    words, the chips and the refusal — nothing was made.
 *  - A refusal that is not about the folder (Iron Jarvis not answering, the
 *    model, a team) was said ON THE FOLDER CHIP, in red, as if the folder
 *    were wrong.
 *  - The posture chip was claimed to ride the create but never pinned.
 *
 * Driven through the REAL IjHarnessView + HarnessSession + composer against
 * an api double; the turn's stream is a fetch double.
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
    posts: {} as Record<string, unknown | ((path: string, body: unknown) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const table = method === "GET" ? api.gets : method === "POST" ? api.posts : {};
    const hit = table[path];
    const value = typeof hit === "function" ? (hit as (p: string, b?: unknown) => unknown)(path, body) : hit;
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
const DAEMON = {
  online: true,
  checking: false,
  health: { default_provider: "mock", default_model: "mock-1", providers: [] },
  refresh: () => {},
};
vi.mock("@/lib/daemon", () => ({ useDaemon: () => DAEMON }));
const MODELS = {
  models: [{ provider: "claude-cli", model: "opus", label: "Opus 5.5", available: true }],
  usable: [{ provider: "claude-cli", model: "opus", label: "Opus 5.5", available: true }],
  loading: false,
};
vi.mock("@/lib/useModels", () => ({ useModels: () => MODELS }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="tree" /> }));

import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { resetTurnCache } from "@/lib/turnCache";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";

const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };

function view(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    title: "New session",
    updated_at: "2026-10-31T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "",
    folder_name: "",
    no_folder: true,
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: [],
    session_id: `build:${id}`,
    queued: [],
    turn_id: "",
    ...over,
  };
}

function openStream() {
  const body = new ReadableStream<Uint8Array>({ start() {} });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

function stubTurn(answer: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (/\/build\/sessions\/new1\/turn/.test(String(url))) return answer();
      return new Response("{}", { status: 200 });
    }),
  );
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  resetTurnCache();
  window.history.replaceState(null, "", "/chat?view=code");
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  api.calls = [];
  api.gets = {
    "/build/sessions": { folders: [], work_root: "C:\\Users\\VR\\Documents\\Iron Jarvis" },
    "/build/sessions?archived=1": { folders: [] },
  };
  api.posts = {
    "/build/sessions": (_p: string, body: unknown) =>
      view("new1", {
        folder: (body as { folder?: string }).folder ?? "",
        no_folder: !(body as { folder?: string }).folder,
      }),
  };
  stubTurn(openStream);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

function renderView() {
  return render(
    <IjHarnessView initialSession={null} terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />,
  );
}

const count = (method: string, path: string) => api.calls.filter((c) => c.method === method && c.path === path).length;
const turnFetched = () =>
  (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.some((c) => /\/build\/sessions\/new1\/turn/.test(String(c[0])));
const deletes = () => api.calls.filter((c) => c.method === "DELETE").map((c) => c.path);

describe("review: the start composer keeps what the user wrote", () => {
  it("Ctrl+N inside the composer keeps the words", async () => {
    renderView();
    const box = await screen.findByTestId("harness-new-box");
    fireEvent.change(box, { target: { value: "Half a thought" } });
    fireEvent.keyDown(box, { key: "n", ctrlKey: true });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.getByTestId("harness-new-box")).toHaveValue("Half a thought");
    expect(count("POST", "/build/sessions")).toBe(0);
  });

  it("New session pressed while the composer is open keeps the words and the chosen folder", async () => {
    const pickFolder = vi.fn(async () => ({ path: "C:\\notes" }));
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = { isDesktop: true, dialog: { pickFolder } };
    api.gets["/build/git/folder?path=C%3A%5Cnotes"] = { git: true, repo: false, branch: "", can_branch: false, note: "" };
    renderView();
    const box = await screen.findByTestId("harness-new-box");
    fireEvent.click(screen.getByTestId("harness-new-folder-choose"));
    await waitFor(() => expect(screen.getByTestId("harness-new-folder")).toHaveAttribute("data-folder", "C:\\notes"));
    fireEvent.change(box, { target: { value: "Keep me" } });
    fireEvent.click(screen.getByTestId("harness-new-session"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.getByTestId("harness-new-box")).toHaveValue("Keep me");
    expect(screen.getByTestId("harness-new-folder")).toHaveAttribute("data-folder", "C:\\notes");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId("harness-new-box")));
  });

  it("a double Enter makes ONE session", async () => {
    renderView();
    const box = await screen.findByTestId("harness-new-box");
    fireEvent.change(box, { target: { value: "Once" } });
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.keyDown(box, { key: "Enter", ctrlKey: true });
    await waitFor(() => expect(turnFetched()).toBe(true), { timeout: 10_000 });
    await screen.findByTestId("harness-input");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(count("POST", "/build/sessions")).toBe(1);
    // … and the words went to it ONCE (one POST of its turn).
    const turns = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.filter((c) =>
      /\/build\/sessions\/new1\/turn/.test(String(c[0])),
    );
    expect(turns).toHaveLength(1);
  });

  it("the posture picked on the chip rides the one create", async () => {
    renderView();
    const box = await screen.findByTestId("harness-new-box");
    fireEvent.click(document.getElementById("harness-new-posture")!);
    const items = await screen.findAllByTestId("permission-item");
    const always = items.find((el) => /every/i.test(el.textContent ?? ""));
    expect(always).toBeDefined();
    fireEvent.click(always!);
    fireEvent.change(box, { target: { value: "Careful work" } });
    fireEvent.click(screen.getByTestId("harness-new-send"));
    await waitFor(() => expect(count("POST", "/build/sessions")).toBe(1));
    const created = api.calls.find((c) => c.method === "POST" && c.path === "/build/sessions");
    expect((created?.body as { approval_mode?: string }).approval_mode).toBe("always_ask");
  });

  it("a refusal that is not about the folder is said under the box, never on the folder chip", async () => {
    api.posts["/build/sessions"] = new api.ApiError("Iron Jarvis isn't responding.", 0);
    renderView();
    const box = await screen.findByTestId("harness-new-box");
    fireEvent.change(box, { target: { value: "Hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    const line = await screen.findByTestId("harness-new-problem");
    expect(line).toHaveTextContent("isn't responding");
    expect(screen.getByTestId("harness-new-folder")).not.toHaveAttribute("data-problem");
    expect(screen.getByTestId("harness-new-box")).toHaveValue("Hello");
  });

  it("a first message refused before its turn starts leaves NO empty session: back to the composer with the words", async () => {
    stubTurn(
      () =>
        new Response(JSON.stringify({ detail: "Only 4 files can go with one message." }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
    );
    renderView();
    const box = await screen.findByTestId("harness-new-box");
    fireEvent.click(screen.getByTestId("harness-new-local-only"));
    fireEvent.change(box, { target: { value: "Add a cart page" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(count("POST", "/build/sessions")).toBe(1));
    // The empty row the start made is removed with the daemon's own guard.
    await waitFor(() => expect(deletes()).toEqual(["/build/sessions/new1?only_if_empty=1"]), { timeout: 10_000 });
    await waitFor(() => expect(screen.getByTestId("harness-new-box")).toHaveValue("Add a cart page"), { timeout: 10_000 });
    expect(screen.getByTestId("harness-new-problem")).toHaveTextContent("Only 4 files");
    // The chips come back as they were (Local only stays on).
    expect(screen.getByTestId("harness-new-local-only")).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByTestId("harness-input")).toBeNull();
  });

  it("the archived list is read at open, never on the 4 s list poll", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      renderView();
      await screen.findByTestId("harness-new-box");
      await waitFor(() => expect(count("GET", "/build/sessions?archived=1")).toBe(1));
      const before = count("GET", "/build/sessions");
      await act(async () => {
        vi.advanceTimersByTime(4000 * 3);
        await new Promise((r) => setTimeout(r, 20));
      });
      // The live list WAS read again on the poll (the control) …
      expect(count("GET", "/build/sessions")).toBeGreaterThan(before);
      // … and the archived list was not.
      expect(count("GET", "/build/sessions?archived=1")).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("words handed back once are not handed back AGAIN to a later composer", async () => {
    stubTurn(
      () =>
        new Response(JSON.stringify({ detail: "Only 4 files can go with one message." }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
    );
    api.gets["/build/sessions"] = {
      folders: [
        {
          path: "C:\\work\\shop",
          name: "shop",
          is_project: false,
          project_id: null,
          sessions: [{ id: "a1", title: "Old work", updated_at: "2026-10-31T10:00:00", running: false, waiting: false, archived: false }],
        },
      ],
      work_root: "",
    };
    api.gets["/build/sessions/a1"] = view("a1", { folder: "C:\\work\\shop", folder_name: "shop", no_folder: false });
    api.posts["/build/sessions/a1/archive"] = {};
    renderView();
    // A listed session opens first; New session is the composer.
    await screen.findByTestId("harness-input");
    fireEvent.click(screen.getByTestId("harness-new-session"));
    const box = await screen.findByTestId("harness-new-box");
    fireEvent.change(box, { target: { value: "Add a cart page" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("harness-new-problem")).toBeInTheDocument(), { timeout: 10_000 });
    // Off to another session, which is then archived: the view falls back to
    // the composer — a NEW one, not the refused message's again.
    fireEvent.click(await screen.findByTestId("harness-session-row-a1"));
    await screen.findByTestId("harness-input");
    fireEvent.click(screen.getByLabelText("Session actions"));
    fireEvent.click(await screen.findByTestId("harness-archive"));
    const fresh = await screen.findByTestId("harness-new-box", undefined, { timeout: 10_000 });
    expect(fresh).toHaveValue("");
    expect(screen.queryByTestId("harness-new-problem")).toBeNull();
  });

  it("a first message whose turn STARTED keeps its session (nothing is deleted)", async () => {
    renderView();
    const box = await screen.findByTestId("harness-new-box");
    fireEvent.change(box, { target: { value: "First" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(turnFetched()).toBe(true), { timeout: 10_000 });
    await screen.findByTestId("harness-input");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(deletes()).toEqual([]);
  });
});
