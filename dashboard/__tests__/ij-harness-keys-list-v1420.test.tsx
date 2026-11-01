/**
 * v1.420.0 (fix plan C2.9) — the coding sessions' KEYS work from the session
 * LIST, which is portaled into the app's sidebar (outside the view's root):
 *
 *  - after a click on a row, Ctrl+L puts the caret in the box and Ctrl+N
 *    opens a new session (they did nothing until the user clicked in the
 *    session: `pressIsInside` checked the view's root only);
 *  - Esc from the list is the harness's (the context says "inside");
 *  - ↑/↓ on a row move to the row above or below.
 *
 * (The daemon's half — a long question's session is named by its first
 * reply — is pinned in tests/test_build_session_title_v1420.py.)
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
  return { ApiError, gets: {} as Record<string, unknown>, calls: [] as { method: string; path: string }[] };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string) => {
    api.calls.push({ method, path });
    const hit = method === "GET" ? api.gets[path] : {};
    if (hit instanceof Error) return Promise.reject(hit);
    if (hit === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(hit ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string) => answer("POST", p),
    put: (p: string) => answer("PUT", p),
    patch: (p: string) => answer("PATCH", p),
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

import { IjHarnessView } from "@/components/harness/IjHarnessView";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { HARNESS_LIST_ATTR, HARNESS_ROW_ATTR, inHarness, moveRowFocus, pressIsInside } from "@/lib/harnessKeys";

const FOLDER = "C:\\work\\shop";
const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => null, close: () => {} };

function row(id: string, title: string) {
  return { id, title, updated_at: "2026-10-31T10:00:00", running: false, waiting: false, archived: false };
}
function view(id: string, title: string) {
  return {
    id,
    title,
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
    messages: [],
    session_id: `build:${id}`,
    queued: [],
    turn_id: "",
  };
}

beforeEach(() => {
  api.calls = [];
  api.gets = {
    "/build/sessions": {
      folders: [{ path: FOLDER, name: "shop", is_project: false, project_id: null, sessions: [row("a", "Cart"), row("b", "Login")] }],
    },
    "/build/sessions/a": view("a", "Cart"),
    "/build/sessions/b": view("b", "Login"),
    "/build/accounts": new api.ApiError("no such route", 404),
  };
  localStorage.clear();
  vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 404 })));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** The view with its list portaled into a slot OUTSIDE its root (the app's
 *  sidebar), as Chat › Code draws it. */
function renderWithSlot() {
  const slot = document.createElement("div");
  slot.setAttribute("data-testid", "app-sidebar-slot");
  document.body.appendChild(slot);
  const out = render(
    <div>
      <button data-testid="app-outside">outside</button>
      <IjHarnessView
        initialSession="a"
        terminals={[]}
        onOpenTerminal={() => {}}
        defaultFolder={null}
        terminal={terminal}
        listSlot={slot}
      />
    </div>,
  );
  return { ...out, slot };
}

describe("the keys work from the portaled session list", () => {
  it("the list is outside the view's root and marks itself as the harness's", async () => {
    const { slot } = renderWithSlot();
    const list = await screen.findByTestId("harness-sidebar");
    expect(slot.contains(list)).toBe(true);
    expect(screen.getByTestId("ij-harness-view").contains(list)).toBe(false);
    expect(list.hasAttribute(HARNESS_LIST_ATTR)).toBe(true);
    const rowA = await screen.findByTestId("harness-session-row-a");
    expect(rowA.hasAttribute(HARNESS_ROW_ATTR)).toBe(true);
    // A press with the focus on a row is the harness's; the app's own
    // controls are not.
    expect(pressIsInside(rowA)).toBe(true);
    expect(pressIsInside(screen.getByTestId("app-outside"))).toBe(false);
  });

  it("Ctrl+L from a clicked row puts the caret in the session's box", async () => {
    renderWithSlot();
    await screen.findByTestId("harness-input");
    const rowA = await screen.findByTestId("harness-session-row-a");
    fireEvent.pointerDown(rowA);
    fireEvent.click(rowA);
    rowA.focus();
    fireEvent.keyDown(rowA, { key: "l", ctrlKey: true });
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId("harness-input")));
    // From the app's own controls: nothing.
    const outside = screen.getByTestId("app-outside");
    outside.focus();
    fireEvent.keyDown(outside, { key: "l", ctrlKey: true });
    expect(document.activeElement).toBe(outside);
  });

  it("Ctrl+N from a row opens a new session (the start composer)", async () => {
    renderWithSlot();
    await screen.findByTestId("harness-input");
    const rowB = await screen.findByTestId("harness-session-row-b");
    rowB.focus();
    fireEvent.keyDown(rowB, { key: "n", ctrlKey: true });
    expect(await screen.findByTestId("harness-new-box")).toBeInTheDocument();
  });

  it("with nothing focused, a last click in the list still counts", async () => {
    renderWithSlot();
    await screen.findByTestId("harness-input");
    const rowA = await screen.findByTestId("harness-session-row-a");
    fireEvent.pointerDown(rowA);
    expect(pressIsInside(document.body)).toBe(true);
    fireEvent.pointerDown(screen.getByTestId("app-outside"));
    expect(pressIsInside(document.body)).toBe(false);
  });
});

describe("↑/↓ move between the session rows", () => {
  it("down and up move the focus; the ends stay put; a modifier does nothing", async () => {
    renderWithSlot();
    const rowA = await screen.findByTestId("harness-session-row-a");
    const rowB = screen.getByTestId("harness-session-row-b");
    rowA.focus();
    fireEvent.keyDown(rowA, { key: "ArrowDown" });
    expect(document.activeElement).toBe(rowB);
    fireEvent.keyDown(rowB, { key: "ArrowDown" });
    expect(document.activeElement).toBe(rowB);
    fireEvent.keyDown(rowB, { key: "ArrowUp", shiftKey: true });
    expect(document.activeElement).toBe(rowB);
    fireEvent.keyDown(rowB, { key: "ArrowUp" });
    expect(document.activeElement).toBe(rowA);
    // Enter on the focused row opens it (it is a button).
    await act(async () => {
      fireEvent.click(rowB);
    });
    await waitFor(() => expect(screen.getByTestId("harness-session-row-b").getAttribute("aria-current")).toBe("true"));
  });

  it("the helpers: inside means the view or a marked list; rows are read in order", () => {
    const view = document.createElement("div");
    view.setAttribute("data-testid", "ij-harness-view");
    const list = document.createElement("nav");
    list.setAttribute(HARNESS_LIST_ATTR, "");
    const r1 = document.createElement("button");
    const r2 = document.createElement("button");
    r1.setAttribute(HARNESS_ROW_ATTR, "");
    r2.setAttribute(HARNESS_ROW_ATTR, "");
    list.append(r1, r2);
    const stray = document.createElement("button");
    document.body.append(view, list, stray);
    try {
      expect(inHarness(r1, view)).toBe(true);
      expect(inHarness(stray, view)).toBe(false);
      expect(moveRowFocus(r1, 1)).toBe(true);
      expect(document.activeElement).toBe(r2);
      expect(moveRowFocus(r2, 1)).toBe(false);
      expect(moveRowFocus(stray, 1)).toBe(false);
    } finally {
      view.remove();
      list.remove();
      stray.remove();
    }
  });
});
