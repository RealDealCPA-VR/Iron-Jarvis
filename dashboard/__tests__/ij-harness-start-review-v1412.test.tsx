/**
 * v1.412.0 review — Chat › Code's "Choose…" MOVE of an empty session.
 *
 * A session's folder is fixed once it is made, so choosing one before the
 * first message makes a NEW session in that folder and deletes the empty one.
 * The independent review found the move could delete a session the user had
 * just written in: the box stayed live while the create was pending, the turn
 * went to the OLD session, then the old one was deleted blindly. Pinned here
 * through the REAL HarnessSession:
 *   - while the move is in flight the box does not send (Send disabled, Enter
 *     sends nothing) and says "Moving to <folder>…";
 *   - the old session is deleted with `only_if_empty=1`, and a refusal keeps
 *     the user on the OLD session (the just-made one is taken back) and says so;
 *   - the words, files and skill are read when the move COMMITS (not from the
 *     render the press came from);
 *   - a file still uploading refuses the move in words (never dropped);
 *   - Local only, the cap and the team ride the CREATE (no separate PATCH).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    body?: unknown;
    constructor(message: string, status = 0, _cancelled = false, body?: unknown) {
      super(message);
      this.status = status;
      if (body !== undefined) this.body = body;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
    holdCreate: null as null | { release: () => void; promise: Promise<void> },
    holdUpload: null as null | { release: () => void; promise: Promise<void> },
    deleteFail: {} as Record<string, { status: number; message: string; body?: unknown }>,
    models: [] as Record<string, unknown>[],
  };
});

function hold(): { release: () => void; promise: Promise<void> } {
  let release = () => {};
  const promise = new Promise<void>((r) => {
    release = r;
  });
  return { release, promise };
}

vi.mock("@/lib/api", () => {
  const answer = async (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    if (method === "GET") {
      if (path.startsWith("/build/git/folder")) {
        return { git: true, repo: false, branch: "", can_branch: false, note: "", worktrees_dir: "" };
      }
      if (path in api.gets) return api.gets[path];
      throw new api.ApiError(`unmocked ${path}`, 404);
    }
    if (method === "POST" && path === "/build/sessions") {
      if (api.holdCreate) await api.holdCreate.promise;
      const folder = (body as { folder: string }).folder;
      return { id: "new1", title: "New session", folder, no_folder: folder === "" };
    }
    if (method === "POST" && path === "/documents/upload") {
      if (api.holdUpload) await api.holdUpload.promise;
      return { name: "plan.txt", path: "C:\\home\\uploads\\plan.txt" };
    }
    if (method === "DELETE") {
      const f = api.deleteFail[path];
      if (f) throw new api.ApiError(f.message, f.status, false, f.body);
    }
    return {};
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
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: api.models, usable: api.models, loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: () => <div data-testid="tree" />,
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { forgetSessionUse, sessionUsed } from "@/lib/emptySessions";

const ROOT = "C:\\Users\\me\\Documents\\Iron Jarvis";
const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "New session",
    updated_at: "2026-10-30T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "",
    folder_name: "No folder",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: [],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    no_folder: true,
    work_folder: "",
    work_root: ROOT,
    ...over,
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1": view(),
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
    "/build/sessions/s1/commands": { commands: [], note: "" },
  };
  api.calls = [];
  api.holdCreate = null;
  api.holdUpload = null;
  api.deleteFail = {};
  api.models = [];
  localStorage.clear();
  forgetSessionUse();
  fetchMock = vi.fn(async () => new Response("", { status: 200, headers: { "Content-Type": "text/event-stream" } }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const bridge = (answer: unknown) => {
  const pickFolder = vi.fn(async (_start: string) => answer);
  (window as unknown as { ironjarvis?: unknown }).ironjarvis = { isDesktop: true, dialog: { pickFolder } };
  return pickFolder;
};

function renderSession(onBranched = vi.fn()) {
  render(
    <HarnessSession
      threadId="s1"
      terminal={terminal}
      listRunning={false}
      onChanged={() => {}}
      onGone={() => {}}
      onBranched={onBranched}
      folderPickFrom="C:\\work\\last"
    />,
  );
  return onBranched;
}

const calls = (method: string, path: string) => api.calls.filter((c) => c.method === method && c.path === path);
const turnPosts = () => fetchMock.mock.calls.filter((c) => String(c[0]).includes("/turn"));

describe("the box while the move is in flight", () => {
  it("does not send — Send disabled, Enter sends nothing — and says where it is moving", async () => {
    api.holdCreate = hold();
    bridge({ path: "D:\\projects\\shop" });
    const onBranched = renderSession();
    const box = (await screen.findByTestId("harness-input")) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "fix the cart" } });
    expect(screen.getByTestId("harness-send")).not.toBeDisabled();
    fireEvent.click(screen.getByTestId("harness-start-folder-choose"));
    await waitFor(() => expect(calls("POST", "/build/sessions")).toHaveLength(1));
    expect(await screen.findByTestId("harness-moving")).toHaveTextContent("Moving to shop…");
    expect(screen.getByTestId("harness-send")).toBeDisabled();
    fireEvent.keyDown(box, { key: "Enter" });
    fireEvent.click(screen.getByTestId("harness-send"));
    await new Promise((r) => setTimeout(r, 30));
    expect(turnPosts()).toHaveLength(0);
    await act(async () => api.holdCreate!.release());
    await waitFor(() => expect(onBranched).toHaveBeenCalled());
    expect(turnPosts()).toHaveLength(0);
  });

  it("the old session is deleted ONLY if it is still empty", async () => {
    bridge({ path: "D:\\projects\\shop" });
    const onBranched = renderSession();
    await screen.findByTestId("harness-start-folder");
    fireEvent.click(screen.getByTestId("harness-start-folder-choose"));
    await waitFor(() => expect(onBranched).toHaveBeenCalled());
    expect(calls("DELETE", "/build/sessions/s1?only_if_empty=1")).toHaveLength(1);
    expect(calls("DELETE", "/build/sessions/s1")).toHaveLength(0);
  });

  it("a refused delete keeps the user on the OLD session, takes the new one back, and says so", async () => {
    bridge({ path: "D:\\projects\\shop" });
    api.deleteFail["/build/sessions/s1?only_if_empty=1"] = {
      status: 409,
      message: "This session already has a message in it, so it was kept.",
      body: { detail: "This session already has a message in it, so it was kept.", code: "not_empty" },
    };
    const onBranched = renderSession();
    await screen.findByTestId("harness-start-folder");
    fireEvent.click(screen.getByTestId("harness-start-folder-choose"));
    expect(await screen.findByTestId("harness-start-folder-problem")).toHaveTextContent(/stays in its folder|was kept/);
    expect(calls("DELETE", "/build/sessions/new1?only_if_empty=1")).toHaveLength(1);
    expect(onBranched).not.toHaveBeenCalled();
  });
});

describe("what the move carries", () => {
  it("reads the words in the box when the move commits, not when Choose… was pressed", async () => {
    api.holdCreate = hold();
    bridge({ path: "D:\\projects\\shop" });
    const onBranched = renderSession();
    const box = (await screen.findByTestId("harness-input")) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "fix" } });
    fireEvent.click(screen.getByTestId("harness-start-folder-choose"));
    await waitFor(() => expect(calls("POST", "/build/sessions")).toHaveLength(1));
    fireEvent.change(box, { target: { value: "fix the cart and the footer" } });
    await act(async () => api.holdCreate!.release());
    await waitFor(() => expect(onBranched).toHaveBeenCalled());
    expect(onBranched.mock.calls[0][1]).toMatchObject({ text: "fix the cart and the footer" });
  });

  it("a file still uploading refuses the move in words — nothing is made, the file is not dropped", async () => {
    api.holdUpload = hold();
    bridge({ path: "D:\\projects\\shop" });
    const onBranched = renderSession();
    await screen.findByTestId("harness-start-folder");
    const input = screen.getByTestId("harness-attach-input") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "plan.txt", { type: "text/plain" })] } });
    await waitFor(() => expect(calls("POST", "/documents/upload")).toHaveLength(1));
    fireEvent.click(screen.getByTestId("harness-start-folder-choose"));
    expect(await screen.findByTestId("harness-start-folder-problem")).toHaveTextContent(/uploading/);
    expect(calls("POST", "/build/sessions")).toHaveLength(0);
    expect(onBranched).not.toHaveBeenCalled();
    await act(async () => api.holdUpload!.release());
  });

  it("Local only, the cap and the team ride the create itself — no separate PATCH", async () => {
    api.gets["/build/sessions/s1"] = view({
      setup: {
        provider: "claude-cli",
        local_only: true,
        cap: { minutes: 30 },
        team: { workers: ["coder"], reviewer: "checker" },
      },
    });
    bridge({ path: "D:\\projects\\shop" });
    const onBranched = renderSession();
    await screen.findByTestId("harness-start-folder");
    fireEvent.click(screen.getByTestId("harness-start-folder-choose"));
    await waitFor(() => expect(onBranched).toHaveBeenCalled());
    expect(calls("POST", "/build/sessions")[0]?.body).toMatchObject({
      folder: "D:\\projects\\shop",
      local_only: true,
      cap: { minutes: 30 },
      team: { workers: ["coder"], reviewer: "checker" },
    });
    expect(api.calls.filter((c) => c.method === "PATCH")).toHaveLength(0);
  });
});

describe("a session the user put something into is marked (never cleaned up as abandoned)", () => {
  it("an untouched session is not marked; words typed in the box mark it", async () => {
    renderSession();
    const box = await screen.findByTestId("harness-input");
    await new Promise((r) => setTimeout(r, 20));
    expect(sessionUsed("s1")).toBe(false);
    fireEvent.change(box, { target: { value: "x" } });
    await waitFor(() => expect(sessionUsed("s1")).toBe(true));
  });
});
