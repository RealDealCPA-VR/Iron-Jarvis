/**
 * v1.377.0 review (IJ harness part 2, Phase 6): THE KEYS IN THE REAL APP SHELL.
 *
 * The doer's key tests stood in a bare window listener for the app's command
 * palette and rendered the session alone. Two things only the real pieces
 * show:
 *
 *  1. Esc NEVER STOPPED A REPLY once anything in the harness was EXPANDED.
 *     `popupOpenIn` counted every `aria-expanded="true"` inside the harness
 *     as "a popup owns this Escape" — but a DISCLOSURE is not a popup: a
 *     reply's folded plan, its "Worked for …" line, its receipt and the
 *     thinking fold all set aria-expanded when opened. One opened plan, and
 *     Esc stopped nothing for the rest of the session. Only a control that
 *     says it opens a popup (`aria-haspopup`), an open combobox, a dialog,
 *     a menu or a listbox counts now.
 *  2. Ctrl+K is the REAL CommandPalette's everywhere but inside the harness:
 *     inside, the model menu opens and the palette (its real listener, its
 *     real dialog) never does; from the title bar's search field, from a
 *     dialog over the page and on Build's Terminals view (no harness
 *     mounted), the palette opens.
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
    if (method !== "GET") return Promise.resolve({});
    const hit = api.gets[path];
    if (hit instanceof Error) return Promise.reject(hit);
    if (hit === undefined) {
      // The palette's own lanes and anything else: an empty answer.
      return path.startsWith("/build/") ? Promise.reject(new api.ApiError(`unmocked ${path}`, 404)) : Promise.resolve({});
    }
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
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/terminals",
  useSearchParams: () => new URLSearchParams(),
}));
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
import { CommandPalette } from "@/components/CommandPalette";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";

const terminal: SessionTerminalApi = {
  find: () => null,
  open: async () => null,
  render: () => <div data-testid="term-body" />,
  close: () => {},
};

function view(over: Record<string, unknown> = {}) {
  return {
    id: "s1", title: "Add a cart", updated_at: "2026-10-22T10:00:00", running: false, waiting: false,
    archived: false, folder: "C:\\work\\shop", folder_name: "shop", is_project: false, project_id: null,
    mode: "act", plan: null, setup: {}, messages: [], session_id: "build:s1", queued: [], turn_id: "",
    ...over,
  };
}

const PLAN = { steps: [{ goal: "read the cart", state: "done" }, { goal: "add the button", state: "done" }], total: 2 };

function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({ start(c) { ctl = c; } });
  return {
    response: new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
    end: () => ctl.close(),
  };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  api.gets = { "/build/sessions/s1": view(), "/build/accounts": new api.ApiError("no such route", 404) };
  api.calls = [];
  localStorage.clear();
  fetchMock = vi.fn(async () => new Response("", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** The app shell: the title bar's search, the REAL palette, the harness view. */
function renderShell(withHarness = true) {
  return render(
    <div>
      <header>
        <input data-testid="titlebar-search" aria-label="Search" />
      </header>
      <CommandPalette />
      {withHarness ? (
        <div data-testid="ij-harness-view">
          <HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />
        </div>
      ) : (
        <div data-testid="terminals-view">
          <button data-testid="pane">a terminal</button>
        </div>
      )}
    </div>,
  );
}

const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
const paletteOpen = () => !!document.querySelector('[role="dialog"][aria-modal="true"]');
const stops = () => api.calls.filter((c) => c.method === "POST" && /\/chat\/turns\/.+\/stop$/.test(c.path));

describe("Esc stops a reply even when a disclosure in the harness is open", () => {
  it("an opened plan of an earlier reply does not take the Escape from a running reply", async () => {
    api.gets["/build/sessions/s1"] = view({
      messages: [
        { role: "user", content: "Add a cart", turnId: "t1", at: "2026-10-22T10:00:00" },
        { role: "assistant", content: "Added.", turnId: "t1", plan: PLAN },
      ],
    });
    const stream = controlledStream();
    fetchMock.mockImplementation(async (url: string) =>
      String(url).endsWith("/turn") ? stream.response : new Response("", { status: 200 }),
    );
    renderShell();
    const planLine = await screen.findByTestId("harness-reply-plan");
    fireEvent.click(within(planLine).getByRole("button"));
    expect(within(planLine).getByRole("button")).toHaveAttribute("aria-expanded", "true");
    fireEvent.change(box(), { target: { value: "Go on" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await screen.findByTestId("harness-stop");
    box().focus();
    fireEvent.keyDown(box(), { key: "Escape" });
    await waitFor(() => expect(stops()).toHaveLength(1));
    stream.end();
  });

  it("an open menu still keeps its Escape (the session's own ⋯ menu)", async () => {
    const stream = controlledStream();
    fetchMock.mockImplementation(async (url: string) =>
      String(url).endsWith("/turn") ? stream.response : new Response("", { status: 200 }),
    );
    renderShell();
    await screen.findByTestId("harness-empty");
    fireEvent.change(box(), { target: { value: "Go" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await screen.findByTestId("harness-stop");
    fireEvent.click(screen.getByLabelText("Session actions"));
    fireEvent.keyDown(box(), { key: "Escape" });
    await new Promise((r) => setTimeout(r, 50));
    expect(stops()).toHaveLength(0);
    stream.end();
  });
});

describe("Ctrl+K: the model menu inside the harness, the REAL palette everywhere else", () => {
  it("inside the harness the model menu opens and the palette does not", async () => {
    renderShell();
    await screen.findByTestId("harness-empty");
    box().focus();
    fireEvent.keyDown(box(), { key: "k", ctrlKey: true });
    expect(await screen.findByTestId("model-filter")).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 30));
    expect(paletteOpen()).toBe(false);
  });

  it("from the title bar's search field the palette opens, and the model menu does not", async () => {
    renderShell();
    await screen.findByTestId("harness-empty");
    const search = screen.getByTestId("titlebar-search");
    search.focus();
    fireEvent.keyDown(search, { key: "k", ctrlKey: true });
    await waitFor(() => expect(paletteOpen()).toBe(true));
    expect(screen.queryByTestId("model-filter")).toBeNull();
  });

  it("from a dialog over the page (outside the harness) the palette opens", async () => {
    renderShell();
    await screen.findByTestId("harness-empty");
    const dlg = document.createElement("div");
    dlg.setAttribute("role", "dialog");
    const field = document.createElement("input");
    dlg.appendChild(field);
    document.body.appendChild(dlg);
    try {
      field.focus();
      fireEvent.keyDown(field, { key: "k", ctrlKey: true });
      await waitFor(() => expect(paletteOpen()).toBe(true));
      expect(screen.queryByTestId("model-filter")).toBeNull();
    } finally {
      dlg.remove();
    }
  });

  it("on Build's Terminals view (no harness mounted) Ctrl+K is the palette's", async () => {
    renderShell(false);
    const pane = screen.getByTestId("pane");
    pane.focus();
    fireEvent.keyDown(pane, { key: "k", ctrlKey: true });
    await waitFor(() => expect(paletteOpen()).toBe(true));
  });
});

describe("the key hint names only keys that work where the user is", () => {
  it("Ctrl+N only in the desktop app; the box says the keys", async () => {
    const { harnessKeysHint } = await import("@/lib/harnessKeys");
    expect(harnessKeysHint(false)).not.toContain("Ctrl+N");
    expect(harnessKeysHint(true)).toContain("Ctrl+N new session");
    renderShell();
    await screen.findByTestId("harness-empty");
    // jsdom is a browser tab, not the desktop app.
    expect(box().getAttribute("title")).toBe(harnessKeysHint(false));
  });

  it("an ordinary letter typed in the box does not read the page (only the harness's keys do)", async () => {
    renderShell();
    await screen.findByTestId("harness-empty");
    const spy = vi.spyOn(document, "querySelector");
    const before = spy.mock.calls.length;
    for (const key of ["a", "b", "c"]) fireEvent.keyDown(box(), { key });
    const harnessReads = spy.mock.calls.slice(before).filter(([sel]) => String(sel).includes("ij-harness-view"));
    expect(harnessReads).toHaveLength(0);
    fireEvent.keyDown(box(), { key: "l", ctrlKey: true });
    expect(spy.mock.calls.slice(before).some(([sel]) => String(sel).includes("ij-harness-view"))).toBe(true);
  });
});
