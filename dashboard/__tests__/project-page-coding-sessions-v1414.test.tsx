/**
 * v1.414.0 — the REAL Projects page lists the project's CODING SESSIONS
 * (both Code views, found by folder by the daemon) on its Board tab, each
 * opening in its own view; a project with none (or an older daemon that sends
 * no `coding_sessions`) shows the board exactly as before. The mocks are
 * project-board-words-v13540's.
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

import { __resetApiCache } from "@/lib/apiCache";
const ProjectWorkspacePage = (await import("@/app/projects/[id]/page")).default;

const W = { timeout: 10_000 };

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

const PROJECT = {
  id: "p1",
  name: "Q3 Bookkeeping",
  root: "C:\work\q3",
  description: "",
  archived: false,
  session_count: 0,
  knowledge_count: 0,
};

const CODING = [
  {
    kind: "coding_session",
    view: "chat_code",
    id: "chat_1",
    title: "Fix the ledger import",
    tool: "",
    state: "working",
    outcome: null,
    updated_at: "2026-10-30T10:00:00+00:00",
    link: "/chat?view=code&session=chat_1",
  },
  {
    kind: "coding_session",
    view: "build_code",
    id: "app_2",
    title: "Claude Code: tidy the tests",
    tool: "Claude Code",
    state: "finished",
    outcome: "completed",
    updated_at: "2026-10-30T09:00:00+00:00",
    link: "/terminals?view=code&code=app_2",
  },
];

beforeEach(() => {
  __resetApiCache();
  H.calls = [];
  H.gets = {
    "/projects/p1": { project: PROJECT, sessions: [], coding_sessions: CODING },
    "/projects/p1/team": { project_id: "p1", members: [], team: [], suggestions: [] },
    "/ltm/sources": { active: [] },
    "/sessions?project_id=p1": { sessions: [] },
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

describe("the project page lists its coding sessions", () => {
  it("on the Board tab, each opening in its own view, with its state", async () => {
    await openBoardTab();
    const list = await screen.findByTestId("project-coding-sessions", {}, W);
    const a = within(list).getByTestId("project-coding-session-chat_1");
    expect(a.getAttribute("href")).toBe("/chat?view=code&session=chat_1");
    expect(a.textContent).toContain("Working");
    const b = within(list).getByTestId("project-coding-session-app_2");
    expect(b.getAttribute("href")).toBe("/terminals?view=code&code=app_2");
    expect(b.textContent).toContain("Claude Code · Finished");
    // The board is still there beneath it.
    expect(await screen.findByText(/No sessions in this project yet/, {}, W)).toBeInTheDocument();
  });

  it("a project with none (an older daemon sends no key) shows the board as before", async () => {
    H.gets["/projects/p1"] = { project: PROJECT, sessions: [] };
    await openBoardTab();
    expect(await screen.findByText(/No sessions in this project yet/, {}, W)).toBeInTheDocument();
    expect(screen.queryByTestId("project-coding-sessions")).toBeNull();
  });
});
