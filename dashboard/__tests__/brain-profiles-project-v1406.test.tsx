/**
 * v1.406.0 — the REAL project page's "Brain profile" select (beside its
 * model): it names the app's choice, offers "None in this project" and each
 * profile, shows the project's saved `brain_profile`, and saves a pick through
 * the page's own `savePatch` (PATCH /projects/{id} {brain_profile}). An older
 * Iron Jarvis (GET /brain 404) gets no select at all.
 *
 * The harness is project-board-words-v13540's: the real page, the real
 * useApi; only the HTTP layer and leaves not under test are stood in.
 */


import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    patches: [] as { path: string; body: unknown }[],
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
  patch: async (p: string, body?: unknown) => {
    H.patches.push({ path: p, body });
    return {};
  },
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


const PROFILES = {
  profiles: [
    { id: "bp1", name: "Notion", primary: "notion", search_first: true, save_to: "primary", prefer_update: true,
      on_conflict: "ask", guide_ref: "", skills: [], repo_routing: false, created_at: "", updated_at: "" },
    { id: "bp2", name: "Obsidian vault", primary: "vault", search_first: true, save_to: "primary", prefer_update: true,
      on_conflict: "ask", guide_ref: "", skills: [], repo_routing: false, created_at: "", updated_at: "" },
  ],
  selected: "bp1",
  bases: [],
  pending: 0,
};

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function project(brain?: string) {
  return {
    project: {
      id: "p1",
      name: "Q3 Bookkeeping",
      root: "C:\\work\\q3",
      description: "",
      archived: false,
      session_count: 0,
      knowledge_count: 0,
      ...(brain === undefined ? {} : { brain_profile: brain }),
    },
    sessions: [],
  };
}

beforeEach(() => {
  __resetApiCache();
  H.calls = [];
  H.patches = [];
  H.gets = {
    "/projects/p1": project("bp2"),
    "/projects/p1/team": { project_id: "p1", members: [], team: [], suggestions: [] },
    "/ltm/sources": { active: [] },
    "/brain": PROFILES,
  };
  window.localStorage.clear();
  window.HTMLElement.prototype.scrollTo = vi.fn();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => cleanup());

describe("the project page's Brain profile select", () => {
  it("shows the project's choice and saves a new one with savePatch", async () => {
    render(<ProjectWorkspacePage params={fakeParams("p1")} />);
    const sel = (await screen.findByLabelText("Brain profile", {}, W)) as HTMLSelectElement;
    await waitFor(() => expect(sel.value).toBe("bp2"));
    expect(Array.from(sel.options).map((o) => o.textContent)).toEqual([
      "Same as the app (Notion)",
      "None in this project",
      "Notion",
      "Obsidian vault",
    ]);
    fireEvent.change(sel, { target: { value: "off" } });
    await waitFor(() => expect(H.patches).toEqual([{ path: "/projects/p1", body: { brain_profile: "off" } }]));
    // The page reads the project again after the save (savePatch's reload).
    await waitFor(() => expect(H.calls.filter((c) => c === "/projects/p1").length).toBeGreaterThanOrEqual(2));
  });

  it("following the app is the empty value", async () => {
    H.gets["/projects/p1"] = project("bp1");
    render(<ProjectWorkspacePage params={fakeParams("p1")} />);
    const sel = (await screen.findByLabelText("Brain profile", {}, W)) as HTMLSelectElement;
    await waitFor(() => expect(sel.value).toBe("bp1"));
    fireEvent.change(sel, { target: { value: "" } });
    await waitFor(() => expect(H.patches).toEqual([{ path: "/projects/p1", body: { brain_profile: "" } }]));
  });

  it("a project row from an older daemon (no key) follows the app", async () => {
    H.gets["/projects/p1"] = project();
    render(<ProjectWorkspacePage params={fakeParams("p1")} />);
    const sel = (await screen.findByLabelText("Brain profile", {}, W)) as HTMLSelectElement;
    expect(sel.value).toBe("");
  });

  it("an older Iron Jarvis (GET /brain 404) shows no select", async () => {
    delete H.gets["/brain"];
    render(<ProjectWorkspacePage params={fakeParams("p1")} />);
    await screen.findByLabelText("Model", {}, W);
    await waitFor(() => expect(H.calls).toContain("/brain"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(screen.queryByLabelText("Brain profile")).toBeNull();
  });
});
