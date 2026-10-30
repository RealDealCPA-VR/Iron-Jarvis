/**
 * v1.413.0 — Chat › Code: does a pasted picture reach the turn on the
 * composer-first START (a new session with no folder and no message yet), and
 * in an open session? The user suspected it did not ("I suspect it is the same
 * with the native harness"); checked by hand through the REAL HarnessSession
 * and the real stream (lib/useChatStream) against a fetch double — and pinned.
 *
 *   - the start (no folder, no message): a pasted picture goes up and its
 *     path rides the FIRST turn (the daemon makes the folder with it);
 *   - a picture pasted before Choose… moves WITH the session to its folder
 *     (the move's draft), and the new session's box holds it;
 *   - an open session with messages: the same.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    gets: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    if (method === "GET") {
      if (path.startsWith("/build/git/folder")) {
        return Promise.resolve({ git: true, repo: false, branch: "", can_branch: false, note: "", worktrees_dir: "" });
      }
      const v = api.gets[path];
      if (v instanceof Error) return Promise.reject(v);
      return v !== undefined ? Promise.resolve(v) : Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    }
    if (method === "POST" && path === "/documents/upload") {
      const f = (body as { filename: string }).filename;
      return Promise.resolve({ path: `C:/ij/uploads/${f}`, name: f, bytes: 4 });
    }
    if (method === "POST" && path === "/build/sessions") {
      const folder = (body as { folder: string }).folder;
      return Promise.resolve({ id: "new1", title: "New session", folder, no_folder: folder === "" });
    }
    return Promise.resolve({});
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
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";

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
    work_root: "C:\\Users\\me\\Documents\\Iron Jarvis",
    ...over,
  };
}

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
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
    "/build/sessions/s1/commands": { commands: [], note: "" },
  };
  api.calls = [];
  localStorage.clear();
  fetchMock = vi.fn(async () => sse([["done", { reply: "ok" }]]));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const png = (name = "image.png") => new File([new Uint8Array([137, 80, 78, 71])], name, { type: "image/png" });
const turnCall = () =>
  fetchMock.mock.calls.find((c) => String((c as unknown as [string])[0]).endsWith("/build/sessions/s1/turn")) as
    | [string, RequestInit]
    | undefined;
const uploads = () => api.calls.filter((c) => c.method === "POST" && c.path === "/documents/upload");

function renderSession(onBranched = vi.fn()) {
  render(
    <HarnessSession
      threadId="s1"
      terminal={terminal}
      listRunning={false}
      onChanged={() => {}}
      onGone={() => {}}
      onBranched={onBranched}
      folderPickFrom="C:\\work"
    />,
  );
  return onBranched;
}

describe("Chat › Code: a pasted picture", () => {
  it("on the composer-first START (no folder, no message) goes up and rides the first turn", async () => {
    renderSession();
    await screen.findByTestId("harness-start-folder");
    const box = screen.getByTestId("harness-input") as HTMLTextAreaElement;
    fireEvent.paste(box, { clipboardData: { files: [png()] } });
    await waitFor(() => expect(uploads()).toHaveLength(1));
    const name = (uploads()[0].body as { filename: string }).filename;
    expect(name).toMatch(/^pasted-.*\.png$/);
    expect(await screen.findByTestId("harness-attachment-thumb")).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "build a page like this" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(turnCall()).toBeTruthy());
    const body = JSON.parse(String(turnCall()![1].body));
    expect(body.text).toBe("build a page like this");
    expect(body.attachments).toEqual([`C:/ij/uploads/${name}`]);
  });

  it("pasted before Choose…, it moves WITH the session to its folder", async () => {
    (window as unknown as { ironjarvis?: unknown }).ironjarvis = {
      isDesktop: true,
      dialog: { pickFolder: vi.fn(async () => ({ path: "D:\\shop" })) },
    };
    const onBranched = renderSession();
    await screen.findByTestId("harness-start-folder");
    fireEvent.paste(screen.getByTestId("harness-input"), { clipboardData: { files: [png()] } });
    await screen.findByTestId("harness-attachment-thumb");
    fireEvent.click(screen.getByTestId("harness-start-folder-choose"));
    await waitFor(() => expect(onBranched).toHaveBeenCalled());
    const [, draft] = onBranched.mock.calls[0];
    expect(draft.attachments).toHaveLength(1);
    expect(draft.attachments[0]).toMatch(/^C:\/ij\/uploads\/pasted-.*\.png$/);
  });

  it("in an open session with messages goes up and rides the turn", async () => {
    api.gets["/build/sessions/s1"] = view({
      no_folder: false,
      folder: "C:\\work\\shop",
      folder_name: "shop",
      messages: [
        { role: "user", content: "hi", at: "2026-10-30T10:01:00", turnId: "t0" },
        { role: "assistant", content: "hello", at: "2026-10-30T10:01:05", turnId: "t0" },
      ],
    });
    renderSession();
    const box = (await screen.findByTestId("harness-input")) as HTMLTextAreaElement;
    expect(screen.queryByTestId("harness-start-folder")).toBeNull();
    fireEvent.paste(box, { clipboardData: { files: [png()] } });
    await screen.findByTestId("harness-attachment-thumb");
    fireEvent.change(box, { target: { value: "and this?" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(turnCall()).toBeTruthy());
    expect(JSON.parse(String(turnCall()![1].body)).attachments).toHaveLength(1);
  });
});
