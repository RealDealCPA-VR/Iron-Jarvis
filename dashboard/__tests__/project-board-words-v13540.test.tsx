/**
 * v1.354.0 lane 4D review (G59) — the acceptance line on the REAL Projects
 * page: a project's Board tab whose read fails says "The board couldn't load.
 * Try again." with no HTTP code and no "daemon" in the visible text, the code
 * only in the line's title, and "Try again" reading the board again at once.
 *
 * The lane's own file pinned this page's board with a source pin ("jsdom
 * cannot mount that page"), but the page mounts fine (ux-wave3-projects-v1315
 * does it). So this drives the page itself: the real ProjectWorkspacePage,
 * the real useApi / usePolledApi / apiCache and the real LoadFailedLine; only
 * the HTTP layer and leaves that are not under test are stood in. One more
 * pin: the panel's "Iron Jarvis isn't responding." is the shell banner's own
 * headline (lib/offlineWords.ts), imported, so the two cannot drift.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: {} as Record<string, unknown>,
    calls: [] as string[],
  };
});

vi.mock("@/lib/api", () => ({
  API_BASE: "http://test",
  ApiError: H.FakeApiError,
  ijToken: () => "",
  setIjToken: () => {},
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  wsUrl: (p: string) => `ws://test${p}`,
  sseUrl: (p: string) => `http://test${p}`,
  get: async (p: string) => {
    H.calls.push(p);
    const r = H.gets[p];
    if (typeof r === "function") {
      const v = (r as () => unknown)();
      if (v instanceof Error) throw v;
      return v;
    }
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${p}`, 404);
    return r;
  },
  post: async () => ({}),
  put: async () => ({}),
  patch: async () => ({}),
  del: async () => ({}),
  api: async () => ({}),
}));
vi.mock("@/lib/useModels", () => ({
  useModels: () => ({ data: { models: [] }, models: [], usable: [], error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useReviews", () => ({ useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }) }));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("next/link", () => ({
  default: ({ children, href, prefetch: _p, ...rest }: React.ComponentProps<"a"> & { prefetch?: unknown }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/projects/p1",
}));
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (p: string, c: string) => p + c,
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/FilePickerModal", () => ({ FilePickerModal: () => null }));
// Leaves of the page that are not under test.
vi.mock("@/components/kanban/KanbanBoard", () => ({ KanbanBoard: () => <div data-testid="kanban-stub" /> }));
vi.mock("@/components/project/KnowledgePanel", () => ({ KnowledgePanel: () => null }));
vi.mock("@/components/project/ProjectSurfaces", () => ({ ProjectApprovals: () => null }));
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "transition", "variants", "layout", "layoutId",
    "whileHover", "whileTap", "whileInView", "viewport",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    // Every framer-motion mock exports `m` (v1.250.0 mock contract).
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    LazyMotion: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    domAnimation: {},
    useReducedMotion: () => true,
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { __resetApiCache } from "@/lib/apiCache";
import { NOT_RESPONDING } from "@/lib/loadFailed";
import { OFFLINE_TITLE } from "@/lib/offlineWords";
const ProjectWorkspacePage = (await import("@/app/projects/[id]/page")).default;

const W = { timeout: 10_000 };
const BOARD = "/sessions?project_id=p1";

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

const lineWith = (text: string) => (_: string, el: Element | null) => el?.tagName === "P" && el.textContent === text;

beforeEach(() => {
  __resetApiCache();
  H.calls = [];
  H.gets = {
    "/projects/p1": {
      project: {
        id: "p1",
        name: "Q3 Bookkeeping",
        root: "C:\\work\\q3",
        description: "",
        archived: false,
        session_count: 0,
        knowledge_count: 0,
      },
      sessions: [],
    },
    "/projects/p1/team": { project_id: "p1", members: [], team: [], suggestions: [] },
    "/ltm/sources": { active: [] },
  };
  window.localStorage.clear();
  window.HTMLElement.prototype.scrollTo = vi.fn();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

async function openBoardTab() {
  render(<ProjectWorkspacePage params={fakeParams("p1")} />);
  const tab = await screen.findByRole("button", { name: "Board" }, W);
  fireEvent.click(tab);
}

describe("G59 on the Projects page: a board that fails to load", () => {
  it("says 'The board couldn't load. Try again.' (no code, no daemon); the code is the title; Try again re-reads", async () => {
    let fail = true;
    H.gets[BOARD] = () => (fail ? new H.FakeApiError("Internal Server Error", 500) : { sessions: [] });
    await openBoardTab();

    const p = await screen.findByText(lineWith("The board couldn't load. Try again."), {}, W);
    expect(p.textContent).not.toMatch(/HTTP|daemon|500/i);
    expect(p.getAttribute("title")).toBe("HTTP 500: Internal Server Error");

    // Counted right after the press, so the board's own 4 s poll cannot be
    // what read again.
    const reads = H.calls.filter((c) => c === BOARD).length;
    fail = false;
    await act(async () => {
      fireEvent.click(within(p).getByRole("button", { name: "Try again" }));
    });
    expect(H.calls.filter((c) => c === BOARD).length).toBeGreaterThan(reads);
    expect(await screen.findByText(/No sessions in this project yet/, {}, W)).toBeInTheDocument();
  });

  it("the no-answer words ARE the offline banner's headline, imported rather than copied (one truth)", () => {
    expect(NOT_RESPONDING).toBe(OFFLINE_TITLE);
    const src = readFileSync(join(process.cwd(), "lib", "loadFailed.ts"), "utf8").replace(/\r\n/g, "\n");
    expect(src).toContain('import { OFFLINE_TITLE } from "@/lib/offlineWords";');
    expect(src).toMatch(/export const NOT_RESPONDING = OFFLINE_TITLE;/);
  });

  it("when nothing answers it says so in the app's name, with no press and no title", async () => {
    H.gets[BOARD] = () => new H.FakeApiError("Failed to fetch", 0);
    await openBoardTab();
    const p = await screen.findByText(lineWith("The board couldn't load. Iron Jarvis isn't responding."), {}, W);
    expect(p.getAttribute("title")).toBeNull();
    expect(within(p).queryByRole("button")).toBeNull();
    expect(document.body.textContent).not.toMatch(/daemon/i);
  });
});
