/**
 * v1.406.0 part 7 — two REAL coding sessions side by side.
 *
 * IjHarnessView with two real HarnessSessions (each with its own reply
 * streaming from its own turn):
 *  1. BOTH STREAM AT ONCE, and a word in either never re-renders the view
 *     around them (the v1.250.0 rule — counted in renders of the view's own
 *     session list, which it draws on every render of its own);
 *  2. THE KEYS ARE THE FOCUSED COLUMN'S: Esc stops only that column's reply;
 *     Enter with nothing focused answers only that column's card (never the
 *     other column's Allow);
 *  3. BOTH ARE LOOKED AT (lib/buildWatching — neither's finish notice pings
 *     while it is on screen), and closing one never takes the other's note.
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
  return { ApiError, gets: {} as Record<string, unknown>, calls: [] as { method: string; path: string; body?: unknown }[] };
});
const counts = vi.hoisted(() => ({ view: 0 }));

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    if (method !== "GET") return Promise.resolve({});
    const value = api.gets[path];
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined) return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value);
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
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/chat",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));
vi.mock("@/components/terminal/DirectoryTree", () => ({ DirectoryTree: () => <div /> }));
// The view draws its session list on EVERY render of its own: count those.
vi.mock("@/components/harness/HarnessSidebar", async (orig) => {
  const real = await orig<typeof import("@/components/harness/HarnessSidebar")>();
  const R = await vi.importActual<typeof import("react")>("react");
  return {
    ...real,
    HarnessSidebar: (p: React.ComponentProps<typeof real.HarnessSidebar>) => {
      counts.view += 1;
      return R.createElement(real.HarnessSidebar, p);
    },
  };
});

import { IjHarnessView } from "@/components/harness/IjHarnessView";
import { __resetLooking, lookingAt } from "@/lib/buildWatching";
import { __resetSplitMemory } from "@/lib/codeSplit";

function view(id: string, turn: string, title: string) {
  return {
    id,
    title,
    updated_at: "2026-10-29T10:00:00",
    running: true,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: [{ role: "user", content: `the ${title} job`, turnId: turn }],
    session_id: `build:${id}`,
    queued: [],
    turn_id: turn,
  };
}

function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  return {
    push: (event: string, data: unknown, id?: string) =>
      ctl.enqueue(enc.encode(`${id ? `id: ${id}\n` : ""}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => {
      try {
        ctl.close();
      } catch {
        /* already closed */
      }
    },
    response: (init?: RequestInit) => {
      init?.signal?.addEventListener("abort", () => {
        try {
          ctl.error(new DOMException("The operation was aborted.", "AbortError"));
        } catch {
          /* already closed */
        }
      });
      return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    },
  };
}

let streams: Record<string, ReturnType<typeof controlledStream>>;
const terminal = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };

beforeEach(() => {
  counts.view = 0;
  __resetLooking();
  __resetSplitMemory();
  localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/chat?view=code&session=s1&beside=s2");
  api.calls = [];
  api.gets = {
    "/build/sessions": {
      folders: [
        {
          path: "C:\\work\\shop",
          name: "shop",
          sessions: [
            { id: "s1", title: "Cart", running: true },
            { id: "s2", title: "Footer", running: true },
          ],
        },
      ],
    },
    "/build/sessions/s1": view("s1", "tr1", "Cart"),
    "/build/sessions/s2": view("s2", "tr2", "Footer"),
    "/build/accounts": new api.ApiError("no such route", 404),
  };
  streams = { tr1: controlledStream(), tr2: controlledStream() };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const m = /\/chat\/turns\/(tr\d)\/stream/.exec(String(url));
      if (m) return streams[m[1]].response(init);
      return new Response("{}", { status: 200 });
    }),
  );
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
});

afterEach(() => {
  for (const s of Object.values(streams)) s.end();
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const live = (side: "left" | "right") =>
  screen.getByTestId(`harness-column-${side}`).querySelector('[data-testid="harness-live-text"]')?.textContent ?? "";
const stops = () => api.calls.filter((c) => c.method === "POST" && /\/chat\/turns\/.+\/stop$/.test(c.path)).map((c) => c.path);
const answers = () => api.calls.filter((c) => c.method === "POST" && c.path.startsWith("/chat/approvals/")).map((c) => c.path);

async function openTwo() {
  render(<IjHarnessView initialSession="s1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
  await waitFor(() => expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "columns"), {
    timeout: 10_000,
  });
  await waitFor(
    () => {
      expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/chat/turns/tr1/stream"), expect.anything());
      expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/chat/turns/tr2/stream"), expect.anything());
    },
    { timeout: 10_000 },
  );
  await act(async () => {
    for (const s of Object.values(streams)) {
      s.push("resume", { resumed: false, running: true });
      s.push("caught_up", { id: "" });
    }
    await new Promise((r) => setTimeout(r, 30));
  });
}

describe("two sessions stream at the same time", () => {
  it("each column draws its own reply, and words never re-render the view around them", async () => {
    await openTwo();
    // Let the first reads settle, then count.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 60));
    });
    const before = counts.view;
    for (let i = 0; i < 30; i++) {
      await act(async () => {
        streams.tr1.push("token", { text: `a${i} ` }, `0:${i}`);
        streams.tr2.push("token", { text: `b${i} ` }, `0:${i}`);
        await new Promise((r) => setTimeout(r, 5));
      });
    }
    await act(async () => {
      await new Promise((r) => setTimeout(r, 60));
    });
    expect(live("left")).toContain("a0 ");
    expect(live("left")).toContain("a29");
    expect(live("left")).not.toMatch(/\bb\d/);
    expect(live("right")).toContain("b29");
    expect(live("right")).not.toMatch(/\ba\d/);
    // 60 words: the view itself did not draw again for them.
    expect(counts.view - before).toBeLessThanOrEqual(1);
  }, 30_000);
});

describe("the keys belong to the column with the focus", () => {
  it("Esc in the right column's box stops ONLY its reply; Esc with nothing focused stops the focused column's", async () => {
    await openTwo();
    const rightBox = screen.getByTestId("harness-column-right").querySelector<HTMLTextAreaElement>('[data-testid="harness-input"]')!;
    rightBox.focus();
    fireEvent.keyDown(rightBox, { key: "Escape" });
    await waitFor(() => expect(stops()).toEqual(["/chat/turns/tr2/stop"]));
    // A click on the LEFT column's text (no focus): the left has the focus now.
    rightBox.blur();
    fireEvent.pointerDown(screen.getByTestId("harness-column-left").querySelector('[data-testid="harness-transcript"]')!);
    await waitFor(() => expect(screen.getByTestId("harness-column-left")).toHaveAttribute("data-focused", "true"));
    fireEvent.keyDown(document.body, { key: "Escape" });
    await waitFor(() => expect(stops()).toEqual(["/chat/turns/tr2/stop", "/chat/turns/tr1/stop"]));
  }, 30_000);

  it("Enter with nothing focused answers ONLY the focused column's card — never the other's Allow", async () => {
    await openTwo();
    await act(async () => {
      streams.tr1.push("approval", { id: "apr_1", call_id: "c1", tool: "read_file", args: { path: "a.txt" } }, "0:1");
      streams.tr2.push("approval", { id: "apr_2", call_id: "c2", tool: "read_file", args: { path: "b.txt" } }, "0:1");
      await new Promise((r) => setTimeout(r, 30));
    });
    await waitFor(() => expect(document.querySelectorAll('[data-dock-primary]').length).toBe(2), { timeout: 10_000 });
    // The cards arm their keys after a short pause.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 450));
    });
    fireEvent.pointerDown(screen.getByTestId("harness-column-right").querySelector('[data-testid="harness-transcript"]')!);
    await waitFor(() => expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-focused", "true"));
    (document.activeElement as HTMLElement | null)?.blur?.();
    fireEvent.keyDown(document.body, { key: "Enter" });
    await waitFor(() => expect(answers()).toEqual(["/chat/approvals/apr_2"]));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 60));
    });
    expect(answers()).toEqual(["/chat/approvals/apr_2"]);
  }, 30_000);
});

describe("a card in the OTHER column never takes the caret (review)", () => {
  it("typing in the left box when the right column asks: the caret stays, letters and Enter never answer the right card", async () => {
    await openTwo();
    const leftBox = screen.getByTestId("harness-column-left").querySelector<HTMLTextAreaElement>('[data-testid="harness-input"]')!;
    leftBox.focus();
    await waitFor(() => expect(screen.getByTestId("harness-column-left")).toHaveAttribute("data-focused", "true"));
    await act(async () => {
      streams.tr2.push("approval", { id: "apr_2", call_id: "c2", tool: "read_file", args: { path: "b.txt" } }, "0:1");
      await new Promise((r) => setTimeout(r, 30));
    });
    await waitFor(
      () => expect(screen.getByTestId("harness-column-right").querySelector("[data-dock-primary]")).not.toBeNull(),
      { timeout: 10_000 },
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 450));
    });
    // The caret is still in the left box, and the left column keeps the focus.
    expect(document.activeElement).toBe(leftBox);
    expect(screen.getByTestId("harness-column-left")).toHaveAttribute("data-focused", "true");
    // What the user types next goes where they were typing.
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Enter" });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 60));
    });
    expect(answers()).toEqual([]);
  }, 30_000);
});

describe("Switch model (Ctrl K, the demo strip) opens the FOCUSED column's menu (review)", () => {
  it("only the column with the focus opens its model menu", async () => {
    await openTwo();
    const chip = (side: "left" | "right") =>
      screen.getByTestId(`harness-column-${side}`).querySelector<HTMLButtonElement>('button[title="Switch model"]');
    await waitFor(() => {
      expect(chip("left")).not.toBeNull();
      expect(chip("right")).not.toBeNull();
    }, { timeout: 10_000 });
    fireEvent.pointerDown(screen.getByTestId("harness-column-right").querySelector('[data-testid="harness-transcript"]')!);
    await waitFor(() => expect(screen.getByTestId("harness-column-right")).toHaveAttribute("data-focused", "true"));
    (document.activeElement as HTMLElement | null)?.blur?.();
    act(() => {
      window.dispatchEvent(new Event("ij:open-switcher"));
    });
    await waitFor(() => expect(chip("right")).toHaveAttribute("aria-expanded", "true"));
    expect(chip("left")).toHaveAttribute("aria-expanded", "false");
    expect(document.querySelectorAll('[data-testid="model-menu"]').length).toBe(1);
  }, 30_000);
});

describe("the window crossing the split width (review)", () => {
  it("columns ⇄ tabs keeps both sessions mounted — the same elements, the replies still drawn", async () => {
    let matches = true;
    const listeners = new Set<() => void>();
    vi.stubGlobal("matchMedia", (q: string) => ({
      media: q,
      get matches() {
        return matches;
      },
      addEventListener: (_: string, f: () => void) => listeners.add(f),
      removeEventListener: (_: string, f: () => void) => listeners.delete(f),
    }));
    await openTwo();
    await act(async () => {
      streams.tr1.push("token", { text: "left words" }, "0:1");
      streams.tr2.push("token", { text: "right words" }, "0:1");
      await new Promise((r) => setTimeout(r, 40));
    });
    const session = (side: "left" | "right") =>
      screen.getByTestId(`harness-column-${side}`).querySelector('[data-testid="harness-session"]');
    const before = { left: session("left"), right: session("right") };
    expect(before.left).not.toBeNull();
    const cross = async (wide: boolean) => {
      matches = wide;
      await act(async () => {
        for (const f of listeners) f();
        await new Promise((r) => setTimeout(r, 30));
      });
    };
    await cross(false);
    expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "tabs");
    expect(session("left")).toBe(before.left);
    expect(session("right")).toBe(before.right);
    await cross(true);
    expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "columns");
    expect(session("left")).toBe(before.left);
    expect(session("right")).toBe(before.right);
    expect(live("left")).toContain("left words");
    expect(live("right")).toContain("right words");
    // One stream per session the whole time (no remount re-followed a turn).
    const follows = (turn: string) =>
      (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.filter((c) => String(c[0]).includes(`/chat/turns/${turn}/stream`)).length;
    expect(follows("tr1")).toBe(1);
    expect(follows("tr2")).toBe(1);
  }, 30_000);
});

describe("both sessions on screen are looked at", () => {
  it("neither's finish pings while both show; closing one keeps the other's note", async () => {
    await openTwo();
    await waitFor(() => {
      expect(lookingAt("s1")).toBe(true);
      expect(lookingAt("s2")).toBe(true);
    });
    fireEvent.click(screen.getByRole("button", { name: "Close Cart" }));
    await waitFor(() => expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "one"));
    expect(lookingAt("s1")).toBe(false);
    expect(lookingAt("s2")).toBe(true);
  }, 30_000);

  it("on a narrow window only the tab shown is looked at (the other streams on, hidden)", async () => {
    vi.stubGlobal("matchMedia", (q: string) => ({
      media: q,
      matches: false,
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
    render(<IjHarnessView initialSession="s1" terminals={[]} onOpenTerminal={() => {}} defaultFolder={null} terminal={terminal} />);
    await waitFor(() => expect(screen.getByTestId("harness-columns")).toHaveAttribute("data-split", "tabs"), {
      timeout: 10_000,
    });
    await waitFor(() => expect(screen.getByTestId("harness-column-left").querySelector('[data-testid="harness-input"]')).not.toBeNull(), {
      timeout: 10_000,
    });
    await waitFor(() => expect(screen.getByTestId("harness-column-right").querySelector('[data-testid="harness-input"]')).not.toBeNull(), {
      timeout: 10_000,
    });
    // Review: each tab names the panel it shows (a screen reader follows it).
    for (const side of ["left", "right"] as const) {
      const tab = screen.getByTestId(`harness-split-tab-${side}`);
      const panel = document.getElementById(tab.getAttribute("aria-controls") ?? "");
      expect(panel).toBe(screen.getByTestId(`harness-column-${side}`));
      expect(panel).toHaveAttribute("role", "tabpanel");
      expect(panel).toHaveAttribute("aria-labelledby", tab.id);
    }
    // The address opened on its first session's tab.
    await waitFor(() => expect(lookingAt("s1")).toBe(true));
    expect(lookingAt("s2")).toBe(false);
    // The hidden tab's reply streams on all the same.
    await act(async () => {
      streams.tr2.push("resume", { resumed: false, running: true });
      streams.tr2.push("caught_up", { id: "" });
      streams.tr2.push("token", { text: "hidden words" }, "0:1");
      await new Promise((r) => setTimeout(r, 40));
    });
    expect(screen.getByTestId("harness-column-right").querySelector('[data-testid="harness-live-text"]')?.textContent).toBe(
      "hidden words",
    );
    fireEvent.click(screen.getByTestId("harness-split-tab-right"));
    await waitFor(() => expect(lookingAt("s2")).toBe(true));
    expect(lookingAt("s1")).toBe(false);
  }, 30_000);
});
