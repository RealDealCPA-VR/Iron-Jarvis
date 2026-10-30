/**
 * v1.412.0 — the coding sessions (Chat › Code) start at the composer.
 *
 * The user: "to start a new session it requires that i first select a folder
 * … Id prefer if it instead started automatically with a no folder chosen in
 * the chat bar with a simple choose selector for folder … This should be the
 * same with my native harness … a popup modal that is the generic windows
 * file selector … Effort levels should also be displayed if available."
 *
 * Pinned through the REAL HarnessSession (and the sidebar):
 *   - New session starts one AT ONCE with no folder (no form);
 *   - before its first message, under the composer: the folder chip ("No
 *     folder" / Choose…) — gone once the session has a message;
 *   - Choose… opens Windows' own picker in the desktop app, the folder tree in
 *     a browser; a chosen folder MOVES the empty session (a new one in that
 *     folder with the same model, account, effort, approvals, mode and Local
 *     only, the words in the box carried; the empty one deleted);
 *   - a folder the daemon refuses is said on the chip, and nothing moves;
 *   - the effort chip only when the session's model lists levels.
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
    createFail: null as null | { status: number; message: string },
    models: [] as Record<string, unknown>[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    if (method === "GET") {
      if (path.startsWith("/build/git/folder")) {
        return Promise.resolve({ git: true, repo: false, branch: "", can_branch: false, note: "", worktrees_dir: "" });
      }
      return path in api.gets ? Promise.resolve(api.gets[path]) : Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    }
    if (method === "POST" && path === "/build/sessions") {
      if (api.createFail) return Promise.reject(new api.ApiError(api.createFail.message, api.createFail.status));
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
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: api.models, usable: api.models, loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({
  DirectoryTree: ({ onSelect }: { onSelect: (p: string) => void }) => (
    <button type="button" data-testid="tree-pick" onClick={() => onSelect("C:\\picked")}>
      pick
    </button>
  ),
}));

import { HarnessSession } from "@/components/harness/HarnessSession";
import { HarnessSidebar } from "@/components/harness/HarnessSidebar";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";

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

beforeEach(() => {
  api.gets = {
    "/build/sessions/s1": view(),
    "/build/accounts": new api.ApiError("no such route", 404),
    "/skills": { skills: [] },
    "/build/sessions/s1/skills": { skills: [], note: "" },
    "/build/sessions/s1/commands": { commands: [], note: "" },
  };
  api.calls = [];
  api.createFail = null;
  api.models = [];
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 200, headers: { "Content-Type": "text/event-stream" } })));
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

function renderSession(onBranched = vi.fn(), folderPickFrom: string | null = "C:\\work\\last") {
  render(
    <HarnessSession
      threadId="s1"
      terminal={terminal}
      listRunning={false}
      onChanged={() => {}}
      onGone={() => {}}
      onBranched={onBranched}
      folderPickFrom={folderPickFrom}
    />,
  );
  return onBranched;
}

const calls = (method: string, path: string) => api.calls.filter((c) => c.method === method && c.path === path);

describe("New session starts at once", () => {
  it("the left pane's New session makes a session with no folder — no form, one press", async () => {
    const onStart = vi.fn(async (_f: string) => null as string | null);
    render(
      <HarnessSidebar
        folders={[]}
        listError={null}
        selectedId={null}
        onSelect={() => {}}
        onStart={onStart}
        onRename={async () => null}
        terminals={[]}
        onOpenTerminal={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("harness-new-session"));
    await waitFor(() => expect(onStart).toHaveBeenCalledWith(""));
    expect(screen.queryByTestId("harness-new-session-form")).toBeNull();
    expect(screen.queryByRole("textbox", { name: /folder/i })).toBeNull();
  });
});

describe("the folder chip under the composer", () => {
  it("before the first message: No folder and Choose…, beside a composer ready to type", async () => {
    renderSession();
    const chip = await screen.findByTestId("harness-start-folder");
    expect(chip.textContent).toBe("No folder");
    expect(chip.getAttribute("title")).toContain(ROOT);
    expect(screen.getByTestId("harness-start-folder-choose").textContent).toBe("Choose…");
    expect(screen.getByTestId("harness-input")).toBeInTheDocument();
  });

  it("a session with a message has no folder chip (its folder stays)", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [{ role: "user", content: "hi", at: "2026-10-30T10:01:00" }],
    });
    renderSession();
    await screen.findByTestId("harness-input");
    expect(screen.queryByTestId("harness-start-folder")).toBeNull();
  });

  it("Choose… in the desktop app opens Windows' own picker where the user was; the chosen folder moves the session, with its setup and the words typed", async () => {
    api.gets["/build/sessions/s1"] = view({
      setup: { provider: "claude-cli", model: "claude-opus-5-5", reasoning: "high", approval_mode: "yolo", account: "p_work", local_only: true },
    });
    const pick = bridge({ path: "D:\\projects\\shop" });
    const onBranched = renderSession();
    const box = (await screen.findByTestId("harness-input")) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "fix the cart" } });
    fireEvent.click(screen.getByTestId("harness-start-folder-choose"));
    await waitFor(() => expect(onBranched).toHaveBeenCalled());
    expect(pick).toHaveBeenCalledWith("C:\\work\\last");
    expect(calls("POST", "/build/sessions").map((c) => c.body)).toEqual([
      {
        folder: "D:\\projects\\shop",
        provider: "claude-cli",
        model: "claude-opus-5-5",
        reasoning: "high",
        approval_mode: "yolo",
        account: "p_work",
        // v1.412.0 review (moved on purpose): Local only rides the create
        // itself (a separate PATCH could fail and drop it).
        local_only: true,
      },
    ]);
    expect(calls("PATCH", "/build/sessions/new1")).toEqual([]);
    // …and the empty session is deleted ONLY while it is still empty.
    expect(calls("DELETE", "/build/sessions/s1?only_if_empty=1")).toHaveLength(1);
    const [id, draft] = onBranched.mock.calls[0];
    expect(id).toBe("new1");
    expect(draft).toMatchObject({ text: "fix the cart" });
  });

  it("in a browser (no desktop picker): the app's folder tree", async () => {
    const onBranched = renderSession();
    await screen.findByTestId("harness-start-folder");
    fireEvent.click(screen.getByTestId("harness-start-folder-choose"));
    fireEvent.click(await screen.findByTestId("tree-pick"));
    expect(onBranched).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("harness-start-folder-tree-use"));
    await waitFor(() => expect(onBranched).toHaveBeenCalled());
    expect(calls("POST", "/build/sessions")[0]?.body).toEqual({ folder: "C:\\picked" });
  });

  it("a cancelled picker moves nothing", async () => {
    bridge({ cancelled: true });
    const onBranched = renderSession();
    await screen.findByTestId("harness-start-folder");
    fireEvent.click(screen.getByTestId("harness-start-folder-choose"));
    await new Promise((r) => setTimeout(r, 20));
    expect(calls("POST", "/build/sessions")).toHaveLength(0);
    expect(onBranched).not.toHaveBeenCalled();
  });

  it("a folder the daemon refuses is said on the chip; nothing moves, nothing is deleted", async () => {
    bridge({ path: "C:\\Windows" });
    api.createFail = { status: 400, message: "This folder cannot be used: it is protected" };
    const onBranched = renderSession();
    await screen.findByTestId("harness-start-folder");
    fireEvent.click(screen.getByTestId("harness-start-folder-choose"));
    expect(await screen.findByTestId("harness-start-folder-problem")).toHaveTextContent(
      "This folder cannot be used: it is protected",
    );
    expect(screen.getByTestId("harness-start-folder").dataset.problem).toBe("true");
    expect(calls("DELETE", "/build/sessions/s1")).toHaveLength(0);
    expect(onBranched).not.toHaveBeenCalled();
  });
});

describe("effort only when the model lists levels", () => {
  const row = (reasoning: string[] | undefined) => ({
    provider: "claude-cli",
    model: "claude-opus-5-5",
    label: "Opus 5.5",
    kind: "cli",
    available: true,
    ...(reasoning ? { reasoning } : {}),
  });

  it("a model with levels: the effort chip", async () => {
    api.models = [row(["low", "medium", "high"])];
    api.gets["/build/sessions/s1"] = view({ setup: { provider: "claude-cli", model: "claude-opus-5-5" } });
    renderSession();
    expect(await screen.findByTestId("reasoning-level")).toBeInTheDocument();
  });

  it("a model with none: no effort chip", async () => {
    api.models = [row(undefined)];
    api.gets["/build/sessions/s1"] = view({ setup: { provider: "claude-cli", model: "claude-opus-5-5" } });
    renderSession();
    await screen.findByTestId("harness-input");
    expect(screen.queryByTestId("reasoning-level")).toBeNull();
  });
});
