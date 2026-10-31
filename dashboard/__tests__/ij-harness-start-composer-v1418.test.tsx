/**
 * v1.418.0 (fix plan C2.6 + C2.8) — Chat › Code starts AT THE COMPOSER, and
 * archiving is reversible.
 *
 *  C2.6 the empty view (and New session) is a box with the caret in it and
 *       one row of chips; Send makes the session with ONE create (folder,
 *       branch, model, posture, Local only) and sends the words to it — no
 *       move, no delete, no second read of the session it just made. The
 *       branch switch shows only for a folder in a git repository that can
 *       start a branch.
 *  C2.8 "Archived (N)" at the bottom of the list; Bring back on its row and
 *       on the archived session's banner puts it back in the list.
 *
 * Driven through the REAL IjHarnessView + HarnessSession against an api
 * double; the turn's stream is a fetch double that stays open.
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
import { HarnessSession } from "@/components/harness/HarnessSession";
import { startCreateBody } from "@/components/harness/HarnessStartComposer";
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
const row = (id: string, title: string, archived = false) => ({
  id,
  title,
  updated_at: "2026-10-31T10:00:00",
  running: false,
  waiting: false,
  archived,
});
const group = (path: string, name: string, sessions: unknown[]) => ({
  path,
  name,
  is_project: false,
  project_id: null,
  sessions,
});

function openStream() {
  const body = new ReadableStream<Uint8Array>({ start() {} });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
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
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (/\/build\/sessions\/new1\/turn/.test(String(url))) return openStream();
      return new Response("{}", { status: 200 });
    }),
  );
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

/* ---------------------------------------------------------------- C2.6 */

describe("C2.6 the empty view is a composer with the caret in it", () => {
  it("shows the box (focused) and its chips, and makes nothing on the daemon", async () => {
    renderView();
    const box = await screen.findByTestId("harness-new-box");
    await waitFor(() => expect(document.activeElement).toBe(box));
    expect(screen.getByTestId("harness-new-folder")).toBeInTheDocument();
    expect(screen.getByTestId("harness-new-model")).toBeInTheDocument();
    expect(screen.getByTestId("harness-new-local-only")).toBeInTheDocument();
    expect(document.getElementById("harness-new-posture")).not.toBeNull();
    expect(count("POST", "/build/sessions")).toBe(0);
  });

  it("a first message is ONE create, no delete, no second read, and the words go to the session", async () => {
    renderView();
    const box = await screen.findByTestId("harness-new-box");
    fireEvent.change(box, { target: { value: "Add a cart page" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(turnFetched()).toBe(true), { timeout: 10_000 });
    expect(count("POST", "/build/sessions")).toBe(1);
    expect(api.calls.filter((c) => c.method === "DELETE")).toEqual([]);
    expect(count("GET", "/build/sessions/new1")).toBe(0);
    const created = api.calls.find((c) => c.method === "POST" && c.path === "/build/sessions");
    expect(created?.body).toEqual({ folder: "" });
    // The question is in the session's transcript.
    await waitFor(() => expect(screen.getByTestId("harness-user-bubble")).toHaveTextContent("Add a cart page"));
    const sent = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.find((c) =>
      /\/build\/sessions\/new1\/turn/.test(String(c[0])),
    );
    expect(String((sent?.[1] as RequestInit)?.body ?? "")).toContain("Add a cart page");
  });

  it("the chips ride the one create: a chosen git folder (branch on), the model, the posture and Local only", async () => {
    const pickFolder = vi.fn(async () => ({ path: "C:\\work\\shop" }));
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = { isDesktop: true, dialog: { pickFolder } };
    api.gets["/build/git/folder?path=C%3A%5Cwork%5Cshop"] = {
      git: true,
      repo: true,
      branch: "main",
      can_branch: true,
      note: "",
      worktrees_dir: "",
    };
    renderView();
    const box = await screen.findByTestId("harness-new-box");
    expect(screen.queryByTestId("harness-new-branch-switch")).toBeNull();
    fireEvent.click(screen.getByTestId("harness-new-folder-choose"));
    // The switch shows only once the folder is known to be a repository that
    // can start a branch.
    const sw = await screen.findByTestId("harness-new-branch-switch");
    expect(sw).toBeChecked();
    fireEvent.change(screen.getByTestId("harness-new-model"), { target: { value: "claude-cli::opus" } });
    fireEvent.click(screen.getByTestId("harness-new-local-only"));
    fireEvent.change(box, { target: { value: "Fix the cart" } });
    fireEvent.click(screen.getByTestId("harness-new-send"));
    await waitFor(() => expect(count("POST", "/build/sessions")).toBe(1));
    const created = api.calls.find((c) => c.method === "POST" && c.path === "/build/sessions");
    expect(created?.body).toEqual({
      folder: "C:\\work\\shop",
      branch: true,
      provider: "claude-cli",
      model: "opus",
      local_only: true,
    });
    await waitFor(() => expect(turnFetched()).toBe(true), { timeout: 10_000 });
    expect(api.calls.filter((c) => c.method === "DELETE")).toEqual([]);
  });

  it("a folder outside a repository shows no branch switch", async () => {
    const pickFolder = vi.fn(async () => ({ path: "C:\\notes" }));
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = { isDesktop: true, dialog: { pickFolder } };
    api.gets["/build/git/folder?path=C%3A%5Cnotes"] = {
      git: true,
      repo: false,
      branch: "",
      can_branch: false,
      note: "",
      worktrees_dir: "",
    };
    renderView();
    await screen.findByTestId("harness-new-box");
    fireEvent.click(screen.getByTestId("harness-new-folder-choose"));
    await waitFor(() => expect(screen.getByTestId("harness-new-folder")).toHaveAttribute("data-folder", "C:\\notes"));
    await waitFor(() => expect(count("GET", "/build/git/folder?path=C%3A%5Cnotes")).toBe(1));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(screen.queryByTestId("harness-new-branch-switch")).toBeNull();
  });

  it("New session opens the composer (no create) and a refused folder keeps the words and makes nothing", async () => {
    api.gets["/build/sessions"] = { folders: [group("C:\\work\\shop", "shop", [row("a1", "Old work")])], work_root: "" };
    api.gets["/build/sessions/a1"] = view("a1", { folder: "C:\\work\\shop", folder_name: "shop", no_folder: false });
    api.posts["/build/sessions"] = new api.ApiError("This folder cannot be used: it is not writable", 400);
    render(<IjHarnessView initialSession="a1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    await screen.findByTestId("harness-input");
    fireEvent.click(screen.getByTestId("harness-new-session"));
    const box = await screen.findByTestId("harness-new-box");
    expect(count("POST", "/build/sessions")).toBe(0);
    fireEvent.change(box, { target: { value: "Try again" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.getByTestId("harness-new-folder")).toHaveAttribute("data-problem", "true"));
    expect(screen.getByTestId("harness-new-box")).toHaveValue("Try again");
    expect(api.calls.filter((c) => c.method === "DELETE")).toEqual([]);
  });

  it("startCreateBody sends only what the user chose", () => {
    expect(startCreateBody({ folder: "", text: "x", files: [] })).toEqual({});
    expect(startCreateBody({ folder: "C:\\a", branch: false, approval_mode: "always_ask", text: "x", files: [] })).toEqual({
      branch: false,
      approval_mode: "always_ask",
    });
  });
});

/* ---------------------------------------------------------------- C2.8 */

describe("C2.8 archive is reversible and listed", () => {
  it("an archived session is listed under Archived (N); Bring back puts it back in the list", async () => {
    let archived = true;
    api.gets["/build/sessions"] = () => ({
      folders: [
        group("C:\\work\\shop", "shop", archived ? [row("a1", "Live work")] : [row("a1", "Live work"), row("z9", "Old cart")]),
      ],
      work_root: "",
    });
    api.gets["/build/sessions?archived=1"] = () => ({
      folders: [group("C:\\work\\shop", "shop", [row("a1", "Live work"), row("z9", "Old cart", archived)])],
    });
    api.gets["/build/sessions/a1"] = view("a1", { folder: "C:\\work\\shop", folder_name: "shop", no_folder: false });
    api.posts["/build/sessions/z9/unarchive"] = () => {
      archived = false;
      return {};
    };
    render(<IjHarnessView initialSession="a1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    const toggle = await screen.findByTestId("harness-archived-toggle");
    expect(toggle).toHaveTextContent("Archived (1)");
    expect(screen.queryByTestId("harness-session-row-z9")).toBeNull();
    fireEvent.click(toggle);
    const list = screen.getByTestId("harness-archived-list");
    expect(within(list).getByTestId("harness-archived-row-z9")).toHaveTextContent("Old cart");
    fireEvent.click(within(list).getByTestId("harness-bring-back-z9"));
    await waitFor(() => expect(screen.getByTestId("harness-session-row-z9")).toBeInTheDocument());
    expect(count("POST", "/build/sessions/z9/unarchive")).toBe(1);
    await waitFor(() => expect(screen.queryByTestId("harness-archived")).toBeNull());
  });

  it("an archived session open in the view loses its banner when its ROW brings it back", async () => {
    let archived = true;
    api.gets["/build/sessions"] = () => ({
      folders: [group("C:\\work\\shop", "shop", archived ? [] : [row("z9", "Old cart")])],
      work_root: "",
    });
    api.gets["/build/sessions?archived=1"] = () => ({
      folders: [group("C:\\work\\shop", "shop", [row("z9", "Old cart", archived)])],
    });
    api.gets["/build/sessions/z9"] = () =>
      view("z9", { archived, folder: "C:\\work\\shop", folder_name: "shop", no_folder: false });
    api.posts["/build/sessions/z9/unarchive"] = () => {
      archived = false;
      return {};
    };
    render(<IjHarnessView initialSession="z9" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    await screen.findByTestId("harness-archived-banner");
    fireEvent.click(await screen.findByTestId("harness-archived-toggle"));
    fireEvent.click(screen.getByTestId("harness-bring-back-z9"));
    await waitFor(() => expect(screen.queryByTestId("harness-archived-banner")).toBeNull());
    expect(screen.getByTestId("harness-session-row-z9")).toBeInTheDocument();
  });

  it("an archived session opened by link says so and brings itself back with one press", async () => {
    let archived = true;
    api.gets["/build/sessions/z9"] = () =>
      view("z9", { archived, folder: "C:\\work\\shop", folder_name: "shop", no_folder: false });
    api.posts["/build/sessions/z9/unarchive"] = () => {
      archived = false;
      return {};
    };
    const changed = vi.fn();
    render(<HarnessSession threadId="z9" terminal={terminal} listRunning={false} onChanged={changed} onGone={() => {}} />);
    const banner = await screen.findByTestId("harness-archived-banner");
    expect(banner).toHaveTextContent("This session is archived.");
    fireEvent.click(within(banner).getByTestId("harness-bring-back"));
    await waitFor(() => expect(screen.queryByTestId("harness-archived-banner")).toBeNull());
    expect(count("POST", "/build/sessions/z9/unarchive")).toBe(1);
    expect(changed).toHaveBeenCalled();
  });
});
