/**
 * v1.420.0 (fix plan C2.10 and C2.11) — a coding session's panes and its
 * composer row:
 *
 *  - C2.10: Changes re-reads every CHANGES_POLL_MS while a reply runs (and
 *    never while none does); the first tab is Changes when the newest reply
 *    changed files, else Session (never Plan's "No plan yet"); the column
 *    collapses to a strip and has two widths, both remembered.
 *  - C2.11: Plan is offered once — inside the permission chip (the "Plan
 *    first" chip is gone); the account is named once — in the model chip;
 *    Undo is two presses in the page, never `window.confirm`.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
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
    gets: {} as Record<string, unknown>,
    calls: [] as { method: string; path: string; body?: unknown }[],
    changes: { changes: [] } as unknown,
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    if (method === "POST" && path === "/chat/changes") return Promise.resolve(api.changes);
    if (method !== "GET") return Promise.resolve({});
    const hit = api.gets[path];
    if (hit instanceof Error) return Promise.reject(hit);
    if (hit === undefined) return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(hit);
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
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div data-testid="dir-tree" /> }));

import { HarnessSession } from "@/components/harness/HarnessSession";
import {
  CHANGES_POLL_MS,
  HarnessPanes,
  PANES_LAYOUT_KEY,
  defaultPaneTab,
  panesWidthClass,
  readPanesLayout,
  type SessionTerminalApi,
} from "@/components/harness/HarnessPanes";
import { decodeSessionView, replyChangedFiles, type BuildSessionView } from "@/lib/buildSessions";
import { HARNESS_FIT_ORDER } from "@/lib/toolbarFit";

const FOLDER = "C:\\work\\shop";
const FILE = "C:\\work\\shop\\cart.py";
const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => null, close: () => {} };

const QUESTION = { role: "user", content: "Add a cart", at: "2026-10-31T10:00:00", turnId: "t1" };
const REPLY_FILES = {
  role: "assistant",
  content: "Added the cart.",
  turnId: "t1",
  at: "2026-10-31T10:01:00",
  documents: [FILE],
  route: { provider: "mock", model: "mock-1", reason: "mock" },
};
const REPLY_NONE = { ...REPLY_FILES, content: "It is fine.", documents: [] };

function raw(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "Cart",
    updated_at: "2026-10-31T10:00:00",
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
    messages: [QUESTION],
    session_id: "build:s1",
    queued: [],
    turn_id: "",
    ...over,
  };
}
function view(over: Record<string, unknown> = {}): BuildSessionView {
  const v = decodeSessionView(raw(over));
  if (!v) throw new Error("bad fixture");
  return v;
}

const CHANGE = {
  path: FILE,
  rel: "cart.py",
  name: "cart.py",
  status: "modified",
  added: 2,
  removed: 1,
  diff: "@@ -1,2 +1,3 @@\n a\n-b\n+c\n+d\n",
};

beforeEach(() => {
  api.calls = [];
  api.changes = { changes: [] };
  api.gets = {
    "/build/sessions/s1": raw(),
    "/build/accounts": new api.ApiError("no such route", 404),
    "/browser/status": { paired: false },
  };
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const changeReads = () => api.calls.filter((c) => c.method === "POST" && c.path === "/chat/changes").length;

function panes(v: BuildSessionView, extra: Partial<React.ComponentProps<typeof HarnessPanes>> = {}) {
  return (
    <HarnessPanes view={v} livePlan={null} terminal={terminal} refreshKey={0} undoFor={() => null} onUndo={async () => {}} {...extra} />
  );
}

/* ----------------------------------------------------------- C2.10 */

describe("Changes is read again while a reply runs", () => {
  it("every CHANGES_POLL_MS while busy; never while idle", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    localStorage.setItem("ij.build.pane", "changes");
    const { rerender } = render(panes(view(), { busy: false }));
    await waitFor(() => expect(changeReads()).toBe(1));
    act(() => {
      vi.advanceTimersByTime(CHANGES_POLL_MS * 3);
    });
    expect(changeReads()).toBe(1); // idle: no timer at all
    rerender(panes(view(), { busy: true }));
    act(() => {
      vi.advanceTimersByTime(CHANGES_POLL_MS);
    });
    await waitFor(() => expect(changeReads()).toBe(2));
    act(() => {
      vi.advanceTimersByTime(CHANGES_POLL_MS);
    });
    await waitFor(() => expect(changeReads()).toBe(3));
    // The reply ends: the poll stops (the turn's end bumps refreshKey for the
    // one read after it).
    rerender(panes(view(), { busy: false, refreshKey: 1 }));
    await waitFor(() => expect(changeReads()).toBe(4));
    act(() => {
      vi.advanceTimersByTime(CHANGES_POLL_MS * 3);
    });
    expect(changeReads()).toBe(4);
  });
});

describe("the first tab", () => {
  it("Changes when the newest reply changed files, else Session; a remembered tab wins", () => {
    expect(replyChangedFiles([QUESTION, REPLY_FILES] as never)).toBe(true);
    expect(replyChangedFiles([QUESTION, REPLY_FILES, { ...QUESTION, turnId: "t2" }, REPLY_NONE] as never)).toBe(false);
    expect(replyChangedFiles([QUESTION] as never)).toBe(false);
    expect(defaultPaneTab([QUESTION, REPLY_FILES] as never)).toBe("changes");
    expect(defaultPaneTab([QUESTION, REPLY_NONE] as never)).toBe("session");

    render(panes(view({ messages: [QUESTION, REPLY_FILES] })));
    expect(screen.getByTestId("harness-pane-tab-changes").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("pane-changes")).toBeInTheDocument();
    cleanup();
    render(panes(view({ messages: [QUESTION, REPLY_NONE] })));
    expect(screen.getByTestId("harness-pane-tab-session").getAttribute("aria-selected")).toBe("true");
    expect(screen.queryByTestId("pane-plan")).toBeNull();
    cleanup();
    localStorage.setItem("ij.build.pane", "files");
    render(panes(view({ messages: [QUESTION, REPLY_FILES] })));
    expect(screen.getByTestId("harness-pane-tab-files").getAttribute("aria-selected")).toBe("true");
  });
});

describe("the pane column collapses and has two widths", () => {
  it("collapse, width, both remembered; reopened on the tab it had", async () => {
    render(
      <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
    );
    const aside = await screen.findByTestId("harness-panes-aside");
    expect(aside.getAttribute("data-panes")).toBe("narrow");
    expect(aside.className).toContain("xl:w-[320px]");
    // Wider.
    fireEvent.click(screen.getByTestId("harness-panes-width"));
    expect(aside.getAttribute("data-panes")).toBe("wide");
    expect(aside.className).toContain("xl:w-[480px]");
    expect(readPanesLayout()).toEqual({ collapsed: false, wide: true });
    // Pick a tab, then collapse: only the strip's button is left.
    fireEvent.click(screen.getByTestId("harness-pane-tab-files"));
    const toggle = screen.getByTestId("harness-panes-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(toggle);
    expect(aside.getAttribute("data-panes")).toBe("collapsed");
    expect(aside.className).toContain("xl:w-10");
    expect(screen.queryByTestId("harness-pane-tabs")).toBeNull();
    expect(screen.queryByTestId("files-panel")).toBeNull();
    expect(JSON.parse(localStorage.getItem(PANES_LAYOUT_KEY) ?? "{}")).toEqual({ collapsed: true, wide: true });
    // Open again: the same tab, the same width.
    fireEvent.click(screen.getByTestId("harness-panes-toggle"));
    expect(aside.getAttribute("data-panes")).toBe("wide");
    expect(screen.getByTestId("harness-pane-tab-files").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("files-panel")).toBeInTheDocument();
  });

  it("a remembered collapse opens collapsed; a bad value reads as open and narrow", async () => {
    localStorage.setItem(PANES_LAYOUT_KEY, JSON.stringify({ collapsed: true, wide: false }));
    render(
      <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
    );
    const aside = await screen.findByTestId("harness-panes-aside");
    await waitFor(() => expect(aside.getAttribute("data-panes")).toBe("collapsed"));
    expect(screen.getByTestId("harness-panes-toggle").getAttribute("aria-expanded")).toBe("false");
    localStorage.setItem(PANES_LAYOUT_KEY, "not json");
    expect(readPanesLayout()).toEqual({ collapsed: false, wide: false });
    expect(panesWidthClass({ collapsed: false, wide: false })).toContain("xl:w-[320px]");
  });

  it("the split's shared pane column has no collapse or width buttons", () => {
    render(panes(view()));
    expect(screen.queryByTestId("harness-panes-toggle")).toBeNull();
    expect(screen.queryByTestId("harness-panes-width")).toBeNull();
  });
});

/* ----------------------------------------------------------- C2.11 */

const SRC = () => readFileSync(join(__dirname, "..", "components", "harness", "HarnessSession.tsx"), "utf8").replace(/\r\n/g, "\n");

describe("fewer controls in one row", () => {
  it("Plan lives only in the permission chip; the toolbar holds no 'Plan first' chip", async () => {
    render(
      <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
    );
    const bar = await screen.findByTestId("harness-composer-toolbar");
    expect(within(bar).queryByTestId("harness-plan-mode")).toBeNull();
    expect(within(bar).queryByText("Plan first")).toBeNull();
    // The controls the row holds (no team, no reasoning, no meter here):
    // attach, the permission chip, Try on…, the model chip, Send.
    const controls = Array.from(bar.querySelectorAll("button, select")).filter(
      (el) => !el.closest('[role="menu"], [role="listbox"], [role="dialog"]'),
    );
    expect(controls.map((el) => el.getAttribute("data-testid") || el.getAttribute("aria-label") || el.textContent?.trim())).toHaveLength(5);
    // Plan is the permission chip's row.
    fireEvent.click(within(bar).getByTestId("permission-chip"));
    const items = await screen.findAllByTestId("permission-item");
    const plan = items.find((el) => /Plan/.test(el.textContent ?? ""));
    expect(plan).toBeTruthy();
    fireEvent.click(plan!);
    await waitFor(() =>
      expect(api.calls).toContainEqual({ method: "PATCH", path: "/build/sessions/s1", body: { mode: "plan" } }),
    );
    expect(HARNESS_FIT_ORDER).not.toContain("plan");
  });

  it("the file never asks with window.confirm", () => {
    expect(SRC()).not.toMatch(/window\.confirm\s*\(/);
  });
});

describe("the account's default moved to the Session tab", () => {
  it("'Make default' sets the session's account as the default; the default says so; a refusal is said", async () => {
    localStorage.setItem("ij.build.pane", "session");
    const work = { id: "work", name: "Work", vendor: "claude", status: "ready", isDefault: false, planLabel: "Max" };
    const made = vi.fn(async () => null as string | null);
    const { rerender } = render(panes(view(), { account: work as never, onMakeDefault: made }));
    fireEvent.click(await screen.findByTestId("session-rail-make-default"));
    await waitFor(() => expect(made).toHaveBeenCalledWith(work));
    rerender(panes(view(), { account: { ...work, isDefault: true } as never, onMakeDefault: made }));
    expect(screen.queryByTestId("session-rail-make-default")).toBeNull();
    expect(screen.getByTestId("session-rail-account-default")).toBeInTheDocument();
    const refused = vi.fn(async () => "Iron-Proxy is not answering." as string | null);
    rerender(panes(view(), { account: work as never, onMakeDefault: refused }));
    fireEvent.click(screen.getByTestId("session-rail-make-default"));
    expect(await screen.findByText("Iron-Proxy is not answering.")).toBeInTheDocument();
  });
});

describe("Undo is two presses in the page", () => {
  function stage() {
    api.gets["/build/sessions/s1"] = raw({ messages: [QUESTION, REPLY_FILES] });
    api.gets["/undo?session_id=build%3As1"] = {
      actions: [{ action_id: "u1", kind: "file_restore", path: "cart.py", workspace: FOLDER, undoable: true }],
    };
    api.changes = { changes: [CHANGE] };
  }
  const undos = () => api.calls.filter((c) => c.method === "POST" && c.path.startsWith("/undo/"));

  it("the row's Undo asks above the box; 'Undo it' undoes, nothing before", async () => {
    stage();
    const confirm = vi.spyOn(window, "confirm");
    render(
      <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
    );
    const pane = await screen.findByTestId("pane-changes");
    const undo = await within(pane).findByTestId("review-undo-0");
    await waitFor(() => expect((undo as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(undo);
    const ask = await screen.findByTestId("harness-undo-confirm");
    expect(ask).toHaveTextContent("This restores cart.py");
    expect(undos()).toHaveLength(0);
    fireEvent.click(screen.getByTestId("harness-undo-yes"));
    await waitFor(() => expect(undos()).toEqual([{ method: "POST", path: "/undo/u1", body: {} }]));
    expect(screen.queryByTestId("harness-undo-confirm")).toBeNull();
    expect(confirm).not.toHaveBeenCalled();
  });

  it("Keep undoes nothing", async () => {
    stage();
    render(
      <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
    );
    const pane = await screen.findByTestId("pane-changes");
    const undo = await within(pane).findByTestId("review-undo-0");
    await waitFor(() => expect((undo as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(undo);
    fireEvent.click(await screen.findByTestId("harness-undo-keep"));
    await waitFor(() => expect(screen.queryByTestId("harness-undo-confirm")).toBeNull());
    await new Promise((r) => setTimeout(r, 30));
    expect(undos()).toHaveLength(0);
  });
});
