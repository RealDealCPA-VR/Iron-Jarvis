/**
 * v1.281.0 — a Build pane outside any project can make one in one press.
 *
 * The pane chat's chip says which project grounds the pane; when none does,
 * the folder was just a folder — the assist bar, this chat and any agent
 * handed work here knew nothing about it. Now the folder name carries
 * "Make this a project": one press POSTs the project rooted at the pane's
 * folder (the chat page's own promotion), the chip appears, and the next
 * turn carries project_id.
 *
 * Header, mocks and helpers are the pane-chat-v1206 harness verbatim, with
 * the POST mock taught to answer /projects.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

// ------------------------------------------------------------- module mocks

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      threads: {} as Record<string, unknown>,
      /** Thread ids whose GET fails with a NON-404 (db locked, offline). */
      failThreads: new Set<string>(),
      projects: [] as { id: string; name: string; root?: string }[],
      nextThreadId: "th_1",
      /** GET /undo?session_id=chat rows (newest first, the route's order). */
      undoRows: [] as Record<string, unknown>[],
      /** When set, POST /undo/{id} fails with this (the guard's refusal). */
      undoFail: null as { detail: string; status: number } | null,
      /** When set, POST /projects refuses with this detail (a bad folder). */
      projectFail: null as string | null,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    if (path === "/projects") return { projects: H.api.projects };
    if (path === "/undo?session_id=chat") return { actions: H.api.undoRows };
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    if (m) {
      const id = decodeURIComponent(m[1]);
      if (H.api.failThreads.has(id)) throw new H.FakeApiError("db locked", 500);
      const t = H.api.threads[id];
      if (!t) throw new H.FakeApiError("thread not found", 404);
      return t;
    }
    throw new H.FakeApiError(`unexpected GET ${path}`, 500);
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    if (path === "/documents/upload")
      return { path: `C:/up/${body.filename}`, name: body.filename };
    if (path.startsWith("/undo/")) {
      if (H.api.undoFail)
        throw new H.FakeApiError(H.api.undoFail.detail, H.api.undoFail.status);
      return { undone: decodeURIComponent(path.slice("/undo/".length)) };
    }
    if (path === "/documents/open") return { ok: true, app: "Notepad" };
    if (path === "/projects") {
      if (H.api.projectFail) throw new H.FakeApiError(H.api.projectFail, 400);
      return { id: "proj_new", name: body.name, root: body.root, status: "active" };
    }
    return { ok: true };
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    const id = m && m[1] !== "new" ? m[1] : H.api.nextThreadId;
    return { id, title: (body?.title as string) ?? "t" };
  },
}));

const S = vi.hoisted(() => {
  class FakeStreamError extends Error {
    status: number;
    committed: boolean;
    offline: boolean;
    partial: string;
    constructor(
      message: string,
      status = 0,
      committed = false,
      offline = false,
      partial = "",
    ) {
      super(message);
      this.name = "StreamError";
      this.status = status;
      this.committed = committed;
      this.offline = offline;
      this.partial = partial;
    }
  }
  return {
    FakeStreamError,
    stream: {
      bodies: [] as Record<string, unknown>[],
      result: { reply: "ok" } as Record<string, unknown>,
      reject: null as Error | null,
      streaming: false,
      text: "",
      tools: [] as Record<string, unknown>[],
      // A mid-turn approval frame the hook is currently holding (BC1 D1).
      approval: null as Record<string, unknown> | null,
    },
  };
});

vi.mock("@/lib/useChatStream", () => ({
  // v1.250.0 (S-03): the live bubble reads streamed text through `useLiveText`.
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  StreamError: S.FakeStreamError,
  useChatStream: () => ({
    streaming: S.stream.streaming,
    text: S.stream.text,
    tools: S.stream.tools,
    approval: S.stream.approval,
    run: async (body: Record<string, unknown>) => {
      S.stream.bodies.push(body);
      if (S.stream.reject) throw S.stream.reject;
      return S.stream.result;
    },
    abort: () => {},
  }),
}));

const D = vi.hoisted(() => ({
  daemon: {
    online: true,
    checking: false,
    providers: [
      { provider: "claude-cli", available: true, class: "cli" },
      { provider: "lmstudio", available: true, class: "local" },
      { provider: "openai", available: false, class: "api" },
    ],
  },
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: D.daemon.online,
    unauthorized: false,
    requestError: false,
    checking: D.daemon.checking,
    refresh: () => {},
    health: {
      status: "ok",
      version: "test",
      default_provider: "mock",
      default_model: "m",
      providers: D.daemon.providers,
    },
  }),
}));

// The markdown pipeline is not under test here (house idiom).
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import { PaneChat } from "@/components/terminal/PaneChat";
import {
  engineOptions,
  mergeSetup,
  paneTitle,
  paneThreadKey,
  pathIsUnder,
  projectForCwd,
  runnableBlocks,
} from "@/components/terminal/paneChatCore";

const CWD = "C:\\work\\demo";

beforeEach(() => {
  H.api.gets = [];
  H.api.posts = [];
  H.api.puts = [];
  H.api.threads = {};
  H.api.failThreads = new Set();
  H.api.projects = [];
  H.api.nextThreadId = "th_1";
  H.api.undoRows = [];
  H.api.undoFail = null;
  H.api.projectFail = null;
  S.stream.bodies = [];
  S.stream.result = { reply: "ok" };
  S.stream.reject = null;
  S.stream.streaming = false;
  S.stream.text = "";
  S.stream.tools = [];
  S.stream.approval = null;
  D.daemon.online = true;
  D.daemon.checking = false;
  window.localStorage.clear();
});
afterEach(() => cleanup());

async function typeAndSend(text: string) {
  const box = screen.getByLabelText("Message");
  await waitFor(() => expect(box).toBeEnabled());
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByLabelText("Send"));
}

describe("a pane outside any project can make one (v1.281.0)", () => {
  it("shows the door beside the folder name; one press creates the project, shows the chip, and the next turn carries project_id", async () => {
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const door = await screen.findByTestId("pane-chat-make-project");
    expect(screen.queryByTestId("pane-chat-project")).toBeNull();
    expect(door.textContent).toContain("Make this a project");
    fireEvent.click(door);
    await waitFor(() =>
      expect(H.api.posts.filter((p) => p.path === "/projects")).toHaveLength(1),
    );
    // The folder's own name, rooted at the pane's folder — the chat page's promotion, exactly.
    expect(H.api.posts.find((p) => p.path === "/projects")!.body).toEqual({ name: "demo", root: CWD });
    const chip = await screen.findByTestId("pane-chat-project");
    expect(chip.textContent).toContain("demo");
    expect(screen.queryByTestId("pane-chat-make-project")).toBeNull();
    await typeAndSend("hi");
    await waitFor(() => expect(S.stream.bodies.length).toBe(1));
    expect(S.stream.bodies[0].project_id).toBe("proj_new");
    expect(S.stream.bodies[0].workspace_dir).toBe(CWD);
  });

  it("is absent when a project already covers the folder", async () => {
    H.api.projects = [{ id: "p_demo", name: "Demo", root: CWD }];
    render(<PaneChat paneId="p1" cwd={CWD} />);
    const chip = await screen.findByTestId("pane-chat-project");
    expect(chip.textContent).toContain("Demo");
    expect(screen.queryByTestId("pane-chat-make-project")).toBeNull();
  });

  it("a refused creation says why and keeps the door", async () => {
    H.api.projectFail = "that folder is not a directory";
    render(<PaneChat paneId="p1" cwd={CWD} />);
    fireEvent.click(await screen.findByTestId("pane-chat-make-project"));
    expect(await screen.findByText(/that folder is not a directory/)).not.toBeNull();
    expect(screen.queryByTestId("pane-chat-project")).toBeNull();
    expect(screen.getByTestId("pane-chat-make-project")).toBeEnabled();
  });
});
