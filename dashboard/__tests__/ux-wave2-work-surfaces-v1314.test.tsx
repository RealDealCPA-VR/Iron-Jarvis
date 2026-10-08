/**
 * v1.314.0 — UX wave 2 ("words & empty states"), track T2 work surfaces:
 * Sessions list, Kanban, a session's own page, the New-session form and the
 * Overview's live-event card.
 *
 * WHAT THE USER SAW (scratch screenshots desk__sessions / desk__kanban /
 * tall__sessions_session / fresh__kanban / fresh__sessions):
 *  - Kanban cards wore a bare monospace `mock` chip (the ONLY demo disclosure
 *    on a card) and the origin filter listed raw kinds ("schedule", "job").
 *  - Every sessions row printed `session_8b33b7…` in monospace.
 *  - A session's Summary led with "mock / mock-1" and a three-line raw folder
 *    path, and the actual result sat under all of it.
 *  - The empty Kanban was one sentence pointing at another page; the empty
 *    Sessions list said "create one on the left" (on a phone the form is ABOVE).
 *  - The New-session form's default model read "Default · mock / …".
 *
 * What must NOT change (anti-vacuity controls, every describe has one): the
 * demo disclosure stays (MockChip, exactly once per card); the raw provider,
 * model, origin, id and folder stay reachable (title / data-origin / copy
 * button / the search box); filter VALUES stay raw; the empty-state action
 * opens the page's OWN form (one task box, never a second copy); Delete stays
 * two-press; "All sessions" stays on Kanban.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/* ---- api ------------------------------------------------------------------ */

const hooks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    posts: [] as { path: string; body?: unknown }[],
    connected: true,
    search: "",
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: hooks.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    const r = hooks.responses[path];
    return r === undefined
      ? Promise.reject(new hooks.FakeApiError(`unmocked GET ${path}`, 404))
      : Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    hooks.posts.push({ path, body });
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

// Path-keyed synchronous data (the kanban-teams-v1168 harness shape).
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("@/lib/useReviews", () => ({
  useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: hooks.connected }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({ enabled: false, supported: false, toggle: () => {}, speak: () => {}, stop: () => {} }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(hooks.search),
  usePathname: () => "/sessions",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (p: string, c: string) => p + c,
}));
// The session page's heavy neighbours — out of scope here (markdown-surfaces-v1230's set).
vi.mock("@/components/ReviewPanel", () => ({ ReviewPanel: () => null }));
vi.mock("@/components/TracesPanel", () => ({ TracesPanel: () => null }));
vi.mock("@/components/SessionFeedback", () => ({ SessionFeedback: () => null }));
vi.mock("@/components/TimeTravelFeed", () => ({ TimeTravelFeed: () => null }));
vi.mock("@/components/chat/DocPreview", () => ({ DocPreview: () => null, appLabelFor: () => "app" }));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "transition", "variants", "layout",
    "whileHover", "whileTap", "whileInView", "viewport",
  ]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import SessionsPage from "@/app/sessions/page";
import KanbanPage from "@/app/kanban/page";
import SessionDetailPage from "@/app/sessions/[id]/page";
import { KanbanBoard } from "@/components/kanban/KanbanBoard";
import { NewSessionForm } from "@/components/NewSessionForm";
import { EventStream } from "@/components/EventStream";
import { originLabel } from "@/components/sessions/OriginChip";
import { shortId } from "@/lib/format";
import type { Review, SessionView } from "@/lib/types";

/* ---- fixtures -------------------------------------------------------------- */

function sv(id: string, status: string, over: Partial<SessionView> = {}): SessionView {
  return {
    id,
    task: `task ${id}`,
    agent_type: "coder",
    provider: "mock",
    model: "mock-1",
    status,
    workspace_path: "C:/w",
    summary: "",
    created_at: "2026-10-01T10:00:00Z",
    finished_at: null,
    ...over,
  };
}

const NO_REVIEWS = {} as Record<string, Review>;
const LONG_ID = "session_8b33b7aa11223344";

/** Every element in `root` carrying a `title` that equals / contains `needle`. */
function titled(root: ParentNode, needle: string, exact = true): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("[title]")).filter((el) => {
    const t = el.getAttribute("title") ?? "";
    return exact ? t === needle : t.includes(needle);
  });
}

beforeEach(() => {
  hooks.responses = {};
  hooks.posts = [];
  hooks.connected = true;
  hooks.search = "";
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn(() => Promise.resolve()) },
  });
});
afterEach(cleanup);

/* ========================================================================== */
/*  Kanban cards — the demo disclosure is the MockChip, providers in words      */
/* ========================================================================== */

describe("Kanban card: no bare 'mock' chip, the disclosure is the MockChip", () => {
  function renderBoard(sessions: SessionView[]) {
    hooks.responses["/sessions/teams"] = { parents: {} };
    return render(<KanbanBoard sessions={sessions} reviews={NO_REVIEWS} reload={() => {}} />);
  }
  const cardOf = (task: string) => screen.getByText(task).closest("[data-team-depth]") as HTMLElement;

  it("a demo-model card shows 'demo model' exactly once and no bare 'mock' word", () => {
    renderBoard([sv("s-demo", "active")]);
    const card = cardOf("task s-demo");
    // Anti-vacuity: the disclosure is NOT dropped — it is the shared MockChip.
    expect(within(card).getAllByText(/demo model/)).toHaveLength(1);
    // The bare monospace provider chip is gone from what the eye reads.
    expect(card.textContent ?? "").not.toMatch(/\bmock\b/i);
  });

  it("a real provider keeps its chip, in plain words, with the raw id in its title", () => {
    renderBoard([sv("s-real", "active", { provider: "claude-cli", model: "claude-opus-4" })]);
    const card = cardOf("task s-real");
    const chip = within(card).getByText("Claude Code");
    expect(chip.closest("[title]")?.getAttribute("title") ?? "").toContain("claude-cli");
    expect(card.textContent ?? "").not.toContain("claude-cli");
    // No demo chip on a real run.
    expect(within(card).queryByText(/demo model/)).toBeNull();
  });
});

/* ========================================================================== */
/*  Kanban page — a fresh board teaches and offers the way forward             */
/* ========================================================================== */

describe("Kanban page at zero sessions", () => {
  it("offers 'Start a session' (a link to Sessions) inside the empty state", () => {
    hooks.responses["/sessions"] = { sessions: [] };
    render(<KanbanPage />);
    const empty = screen.getByTestId("empty-state");
    const link = within(empty).getByRole("link", { name: /start a session/i });
    expect(link.getAttribute("href") ?? "").toMatch(/^\/sessions(\?|$)/);
    // Anti-vacuity: the header's "All sessions" door is still there.
    expect(screen.getByRole("link", { name: /All sessions/ }).getAttribute("href")).toBe("/sessions");
  });

  it("still shows the four lanes, so the user learns what the board is", () => {
    hooks.responses["/sessions"] = { sessions: [] };
    hooks.responses["/sessions/teams"] = { parents: {} };
    render(<KanbanPage />);
    for (const label of [
      "No active sessions",
      "No sessions awaiting review",
      "No completed sessions",
      "No failed sessions",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });
});

/* ========================================================================== */
/*  Sessions list                                                              */
/* ========================================================================== */

describe("Sessions list: origin filter in plain words, values raw", () => {
  it("each per-kind option reads originLabel(kind) and keeps its raw 'kind:' value", () => {
    hooks.responses["/sessions"] = {
      sessions: [
        sv("s-sched", "completed", { origin: "schedule:nightly-brief" }),
        sv("s-job", "completed", { origin: "job:mission" }),
        sv("s-mail", "completed", { origin: "comm:email" }),
        sv("s-mine", "completed"),
      ],
    };
    hooks.responses["/projects"] = { projects: [] };
    render(<SessionsPage />);
    const select = screen.getByLabelText("Filter by origin") as HTMLSelectElement;
    const byValue = new Map(Array.from(select.options).map((o) => [o.value, o.textContent ?? ""]));
    // Raw values stay (the filter logic and kanban-teams-v1168 key on them).
    for (const k of ["schedule", "job", "comm"]) {
      expect(byValue.has(`kind:${k}`), k).toBe(true);
      expect(byValue.get(`kind:${k}`)).toBe(originLabel(k));
      expect(byValue.get(`kind:${k}`)).not.toBe(k);
    }
    // Anti-vacuity: the filter still filters by kind.
    fireEvent.change(select, { target: { value: "kind:schedule" } });
    expect(screen.getByText("task s-sched")).toBeInTheDocument();
    expect(screen.queryByText("task s-job")).toBeNull();
  });
});

describe("Sessions list: the id is behind a title/copy, not printed on every row", () => {
  function seed() {
    hooks.responses["/sessions"] = {
      sessions: [sv(LONG_ID, "completed", { task: "Draft the Q3 letter" }), sv("session_other0000000", "completed")],
    };
    hooks.responses["/projects"] = { projects: [] };
  }

  it("the short id is not visible text, and the full id is still in the row (title)", () => {
    seed();
    render(<SessionsPage />);
    expect(screen.queryByText(shortId(LONG_ID))).toBeNull();
    const row = screen.getByText("Draft the Q3 letter").closest("tr") as HTMLElement;
    expect(row).not.toBeNull();
    // Raw kept: a title (row link, chip or copy button) carries the full id.
    expect(titled(row, LONG_ID, false).length).toBeGreaterThan(0);
  });

  it("the search box still finds a session by its id", () => {
    seed();
    render(<SessionsPage />);
    fireEvent.change(screen.getByPlaceholderText(/Search task or id/), { target: { value: "8b33b7aa" } });
    expect(screen.getByText("Draft the Q3 letter")).toBeInTheDocument();
    expect(screen.queryByText("task session_other0000000")).toBeNull();
  });

  it("anti-vacuity: the demo disclosure and the two-press delete are still on the row", () => {
    seed();
    render(<SessionsPage />);
    const row = screen.getByText("Draft the Q3 letter").closest("tr") as HTMLElement;
    expect(within(row).getByText(/demo model/)).toBeInTheDocument();
    const del = within(row).getByRole("button", { name: /Delete session/ });
    fireEvent.click(del);
    expect(within(row).getByText("Confirm?")).toBeInTheDocument();
  });
});

describe("Sessions list at zero sessions: the empty state opens the page's own form", () => {
  it("a button in the empty state focuses the New-session task box (one box, not a copy)", async () => {
    hooks.responses["/sessions"] = { sessions: [] };
    hooks.responses["/projects"] = { projects: [] };
    render(<SessionsPage />);
    const empty = screen.getByTestId("empty-state");
    expect(empty.textContent ?? "").toMatch(/No sessions yet/);
    // "on the left" is false on a phone, where the form sits ABOVE the list.
    expect(empty.textContent ?? "").not.toMatch(/on the left/i);
    const go = within(empty).getByRole("button");
    const boxes = screen.getAllByPlaceholderText(/Describe what the agent should do/);
    expect(boxes).toHaveLength(1);
    fireEvent.click(go);
    await waitFor(() => expect(document.activeElement).toBe(boxes[0]));
    // Still exactly one task box: the action opened the existing form.
    expect(screen.getAllByPlaceholderText(/Describe what the agent should do/)).toHaveLength(1);
  });
});

/* ========================================================================== */
/*  A session's own page                                                       */
/* ========================================================================== */

const WS = "C:\\Users\\VR\\.ironjarvis\\workspaces\\session_abc123def456\\deep\\folder";
const DETAIL_ID = "session_abc123def456";

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function setDetail(over: Partial<SessionView> = {}) {
  hooks.responses[`/sessions/${DETAIL_ID}`] = {
    session: {
      ...sv(DETAIL_ID, "completed", {
        task: "Summarise the engagement letter",
        workspace_path: WS,
        summary: "Wrote a **one-page** summary to summary.md",
        finished_at: "2026-10-01T10:05:00Z",
      }),
      ...over,
    },
    transcript: { runs: [], tools: [] },
  };
}

describe("Session page: the result comes first, the folder and model are a record", () => {
  it("the summary sits above the meta grid (it is read before the folder path)", async () => {
    setDetail();
    render(<SessionDetailPage params={fakeParams(DETAIL_ID)} />);
    const summary = await screen.findByTestId("session-summary");
    const path = screen.getByText(WS);
    expect(summary.compareDocumentPosition(path) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("the folder is one truncated line with the full path in its title and a copy button", async () => {
    setDetail();
    render(<SessionDetailPage params={fakeParams(DETAIL_ID)} />);
    await screen.findByTestId("session-summary");
    const holders = titled(document.body, WS);
    expect(holders.length).toBeGreaterThan(0);
    expect(holders.some((h) => h.className.split(/\s+/).includes("truncate"))).toBe(true);
    const copy = screen.getByRole("button", { name: /copy.*(folder|path|workspace)/i });
    fireEvent.click(copy);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(WS);
  });

  it("a demo run names the model in words; the raw 'mock / mock-1' stays in a title", async () => {
    setDetail();
    render(<SessionDetailPage params={fakeParams(DETAIL_ID)} />);
    await screen.findByTestId("session-summary");
    const rec = titled(document.body, "mock / mock-1");
    expect(rec.length).toBe(1);
    expect(rec[0].textContent ?? "").toContain("Demo model (scripted)");
    expect(rec[0].textContent ?? "").not.toMatch(/\bmock\b/i);
    expect(screen.queryByText(/mock \/ mock-1/)).toBeNull();
    // Anti-vacuity: the honest demo chip is still in the Summary header.
    expect(screen.getByText(/demo model/)).toBeInTheDocument();
  });

  it("a real run reads 'OpenAI' plus the model, raw provider/model in the title", async () => {
    setDetail({ provider: "openai", model: "gpt-x" });
    render(<SessionDetailPage params={fakeParams(DETAIL_ID)} />);
    await screen.findByTestId("session-summary");
    const rec = titled(document.body, "openai / gpt-x");
    expect(rec.length).toBe(1);
    expect(rec[0].textContent ?? "").toContain("OpenAI");
    expect(rec[0].textContent ?? "").toContain("gpt-x");
    expect(screen.queryByText(/demo model/)).toBeNull();
  });

  it("anti-vacuity: the full session id is still on the page, and Rerun is still offered", async () => {
    setDetail();
    render(<SessionDetailPage params={fakeParams(DETAIL_ID)} />);
    await screen.findByTestId("session-summary");
    expect(screen.getAllByText(DETAIL_ID, { exact: false }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Rerun/ })).toBeInTheDocument();
  });
});

/* ========================================================================== */
/*  New-session form — providers through providerDisplay                       */
/* ========================================================================== */

describe("New-session form: no 'mock' in the model picker", () => {
  function seed() {
    hooks.responses["/health"] = {
      default_provider: "mock",
      default_model: "claude-opus-4",
      providers: [{ provider: "mock", available: true, class: "mock" }],
    };
    hooks.responses["/models"] = {
      models: [
        { provider: "mock", model: "mock-1", available: true },
        { provider: "fleet-a1b2", model: "llama-3", name: "My Spark", available: true },
      ],
    };
  }

  it("the default option names the demo model in words; its value stays ''", () => {
    seed();
    render(<NewSessionForm />);
    const sel = screen.getByLabelText("Model") as HTMLSelectElement;
    const def = Array.from(sel.options).find((o) => o.value === "")!;
    expect(def.textContent ?? "").toContain("Demo model (scripted)");
    expect(def.textContent ?? "").not.toMatch(/\bmock\b/i);
  });

  it("an unnamed mock model option reads in words; its value stays 'mock|mock-1'", () => {
    seed();
    render(<NewSessionForm />);
    const sel = screen.getByLabelText("Model") as HTMLSelectElement;
    const opt = Array.from(sel.options).find((o) => o.value === "mock|mock-1");
    expect(opt).toBeDefined();
    expect(opt!.textContent ?? "").toContain("Demo model (scripted)");
    expect(opt!.textContent ?? "").not.toMatch(/\bmock\b/i);
    // Anti-vacuity: a named entry still shows its friendly name.
    const named = Array.from(sel.options).find((o) => o.value === "fleet-a1b2|llama-3");
    expect(named!.textContent ?? "").toContain("My Spark");
  });

  it("anti-vacuity: the honest 'built-in offline model' banner and Connect door stay", () => {
    seed();
    render(<NewSessionForm />);
    expect(screen.getByText(/Running on the built-in offline model/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Connect a real model/ }).getAttribute("href")).toBe("/connections");
  });
});

/* ========================================================================== */
/*  Live events card (Overview → Advanced)                                     */
/* ========================================================================== */

describe("Live events card: no route path in front of a user", () => {
  it("the disconnected empty state does not print '/events'", () => {
    hooks.connected = false;
    render(<EventStream />);
    const empty = screen.getByTestId("empty-state");
    expect(empty.textContent ?? "").not.toContain("/events");
    expect((empty.textContent ?? "").trim().length).toBeGreaterThan(0);
  });
});
