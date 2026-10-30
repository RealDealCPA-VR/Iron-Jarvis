/**
 * v1.379.0 — an IJ harness session needs NO folder.
 *
 * THE USER: "When in the IJ harness it should not require that I am in a
 * specific folder. Like when i start here [Claude Code] i get to choose to
 * attach a folder, but i have use of the harness even without it." Pinned:
 *
 *  - New session: the folder is optional ("Folder to work in (optional)"),
 *    one line says where a session with no folder keeps its files, and
 *    "Start with no folder" (or Start with the box empty) posts no folder;
 *  - the list: one plain "No folder" group, nothing to rename, its "+"
 *    starts another session with no folder;
 *  - the session's header says it has no folder and where its files go — and
 *    once its first message made its own folder, names it with Open; its
 *    empty conversation and its box say "Ask for anything";
 *  - "@" before the first message says there are no files yet (and Enter is
 *    not held for a list that will never come).
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
      return path in api.gets ? Promise.resolve(api.gets[path]) : Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    }
    if (method === "POST" && path === "/build/sessions") {
      return Promise.resolve({ id: "new1", title: "New session", folder: "", no_folder: (body as { folder: string }).folder === "" });
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
vi.mock("@/components/terminal/FilesPanel", () => ({
  FilesPanel: ({ folder, noFolderText }: { folder: string | null; noFolderText?: string }) => (
    <div data-testid="files-panel">{folder ?? noFolderText}</div>
  ),
}));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: ({ onSelect }: { onSelect: (p: string) => void }) => (
    <button type="button" data-testid="tree-pick" onClick={() => onSelect("C:\\picked")}>
      pick
    </button>
  ),
}));

import { HarnessSidebar } from "@/components/harness/HarnessSidebar";
import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { decodeFolders, decodeSessionView, decodeWorkRoot, noFolderLine } from "@/lib/buildSessions";

const ROOT = "C:\\Users\\me\\Documents\\Iron Jarvis";
const MADE = `${ROOT}\\2026-10-23 make a list`;
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
    updated_at: "2026-10-23T10:00:00",
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

const LIST = {
  work_root: ROOT,
  folders: [
    {
      path: "C:\\work\\shop",
      name: "shop",
      is_project: false,
      project_id: null,
      sessions: [{ id: "a1", title: "Add a cart", updated_at: "2026-10-22T10:00:00", running: false, waiting: false, archived: false }],
    },
    {
      path: "",
      name: "No folder",
      is_project: false,
      project_id: null,
      no_folder: true,
      sessions: [{ id: "n1", title: "Plan a trip", updated_at: "2026-10-23T09:00:00", running: false, waiting: false, archived: false }],
    },
  ],
};

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
  fetchMock = vi.fn(async () => new Response("", { status: 200, headers: { "Content-Type": "text/event-stream" } }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function sidebar(onStart = vi.fn(async (_folder: string) => null as string | null)) {
  render(
    <HarnessSidebar
      folders={decodeFolders(LIST)}
      listError={null}
      selectedId={null}
      onSelect={() => {}}
      onStart={onStart}
      onRename={async () => null}
      terminals={[]}
      onOpenTerminal={() => {}}
    />,
  );
  return onStart;
}

// v1.412.0 (moved on purpose): there is no form — New session starts a
// session with NO folder at once; its folder chip (HarnessStartFolder) says
// where its files go and chooses one (ij-harness-start-v1412.test.tsx).
describe("New session: no folder until one is chosen", () => {
  it("starts a session with no folder at once", async () => {
    const onStart = sidebar();
    fireEvent.click(screen.getByTestId("harness-new-session"));
    await waitFor(() => expect(onStart).toHaveBeenCalledWith(""));
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("harness-new-session-form")).toBeNull();
  });
});

describe("the list: one plain “No folder” group", () => {
  it("names it, offers no rename, and its + starts another session with no folder", async () => {
    const onStart = sidebar();
    const groups = screen.getAllByTestId("harness-folder-group");
    const none = groups.find((g) => g.getAttribute("data-no-folder") === "true") as HTMLElement;
    expect(none).toBeTruthy();
    expect(within(none).getByTestId("harness-folder-name")).toHaveTextContent("No folder");
    expect(within(none).queryByTestId("harness-folder-rename")).toBeNull();
    expect(within(none).getByTestId("harness-session-row-n1")).toHaveTextContent("Plan a trip");
    fireEvent.click(within(none).getByTestId("harness-folder-new"));
    await waitFor(() => expect(onStart).toHaveBeenCalledWith(""));
    // A real folder's group is as before.
    const shop = groups.find((g) => g.getAttribute("data-path") === "C:\\work\\shop") as HTMLElement;
    expect(within(shop).getByTestId("harness-folder-rename")).toBeTruthy();
  });

  it("decodes the group and the session's folder facts by whitelist", () => {
    const folders = decodeFolders(LIST);
    expect(folders[1]).toMatchObject({ path: "", name: "No folder", noFolder: true, isProject: false });
    expect(folders[0].noFolder).toBeUndefined();
    expect(decodeFolders({ folders: [{ path: "", name: "x", sessions: [] }] })).toEqual([]);
    const v = decodeSessionView(view({ work_folder: MADE, folder: MADE }));
    expect(v).toMatchObject({ noFolder: true, workFolder: MADE, workRoot: ROOT, folder: MADE });
    const plain = decodeSessionView({ ...view(), no_folder: undefined, work_root: ROOT });
    expect(plain).toMatchObject({ noFolder: false, workFolder: "", workRoot: "" });
    expect(noFolderLine("")).toBe(
      "No folder: the session keeps its files in a new folder of its own, made with your first message.",
    );
  });
});

describe("a session with no folder says where its files go", () => {
  function renderSession() {
    return render(
      <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
    );
  }

  it("before its first message: no folder yet, “Ask for anything”, and “@” has no files yet", async () => {
    renderSession();
    const empty = await screen.findByTestId("harness-empty");
    expect(empty).toHaveTextContent(
      `Ask for anything. Its files go in a new folder in ${ROOT}, made with your first message.`,
    );
    const line = screen.getByTestId("harness-folder");
    expect(line.getAttribute("data-no-folder")).toBe("true");
    expect(line).toHaveTextContent("No folder");
    expect(line).toHaveTextContent("its files get a folder of their own with your first message");
    expect(screen.queryByTestId("harness-open-work-folder")).toBeNull();
    const box = screen.getByTestId("harness-input") as HTMLTextAreaElement;
    expect(box.placeholder).toContain("Ask for anything…");
    fireEvent.change(box, { target: { value: "@not" } });
    expect(await screen.findByTestId("harness-at-no-folder")).toHaveTextContent(
      "This session's folder is made with your first message, so it has no files yet.",
    );
    // No folder listing was asked for, and Enter is not held for one: the
    // words go as they are.
    expect(api.calls.some((c) => c.path.startsWith("/fs/files"))).toBe(false);
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(String((fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body)).text).toBe("@not");
    // The Files tab says the same.
    fireEvent.click(screen.getByTestId("harness-pane-tab-files"));
    expect(await screen.findByTestId("files-panel")).toHaveTextContent(
      "This session's folder is made with your first message, so it has no files yet.",
    );
  });

  it("after its first message: names its own folder and opens it", async () => {
    api.gets["/build/sessions/s1"] = view({ folder: MADE, work_folder: MADE, title: "make a list" });
    renderSession();
    const line = await screen.findByTestId("harness-folder");
    expect(screen.getByTestId("harness-work-folder")).toHaveTextContent("· files in 2026-10-23 make a list");
    expect(line.getAttribute("title")).toBe(MADE);
    fireEvent.click(screen.getByTestId("harness-open-work-folder"));
    await waitFor(() =>
      expect(api.calls.find((c) => c.method === "POST" && c.path === "/documents/open")?.body).toEqual({ path: MADE }),
    );
  });
});
