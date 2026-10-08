/**
 * v1.315.0 — UX wave 3 ("the surfaces people use most"), track T2 agents &
 * sessions: the Sessions list, the Session board (/kanban), a project's board
 * inside the mission screen, a session's own page and its Time-travel feed.
 *
 * Findings pinned here (scratchpad ux_wave3.json, fix_adjustment wins):
 *  - one-ai-identity-split: a mission's coordinator (agent_type "supervisor",
 *    origin EXACTLY "job:mission" — the root a POST /missions stamps; see
 *    lib/missionLinks.isMissionRow) reads "Jarvis" on the board card, the
 *    sessions table and the session page. Any other run keeps its raw id as
 *    TEXT and is title-cased by the CSS `capitalize` class (so getByText on a
 *    lowercase id keeps working). The raw id stays in a `title`.
 *  - kanban-orphan-route: the board is the "Session board" (h1), its
 *    Completed lane hint reads "Finished" (not git's "Merged & done"), and
 *    "Clear completed" sits in the Completed lane — still two-press.
 *  - mission-project-board-cramped: the board embedded in a project's mission
 *    screen (WorldBoard) is COMPACT — never four lanes at xl — and an empty
 *    lane collapses at rest (only at rest: an empty lane is a drop target).
 *  - session-detail-reads-like-a-log: a FINISHED run never says "No live
 *    events yet"; the page leads with the story (summary, then the files the
 *    run handed over, then the controls); every Time-travel row leads with
 *    WORDS ("Session finished", "Save a file"), the kind/tool id is a muted
 *    chip with the raw id in a title, and Undo sits on the headline's line —
 *    still two-press. Time-travel keeps its NAME (page-copy-v1232 pin).
 *
 * Anti-vacuity: every control and consent step still present (Approve/Reject,
 * Clear two-press, Undo two-press, Delete two-press, Rerun, follow-up box,
 * every filter tab), raw ids still reachable, and real flows run end to end
 * (filter by raw agent id, Undo posts /undo/<id>, Clear posts /sessions/clear).
 */

import React from "react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/* ---- api ------------------------------------------------------------------ */

const w3 = vi.hoisted(() => {
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
    gets: [] as string[],
    posts: [] as { path: string; body?: unknown }[],
    events: [] as unknown[],
    audit: { entries: [] as unknown[], next_cursor: null as string | null, total: 0 },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: w3.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  sseUrl: (p: string) => p,
  get: (path: string) => {
    w3.gets.push(path);
    if (path.startsWith("/audit")) return Promise.resolve(w3.audit);
    const r = w3.responses[path];
    return r === undefined
      ? Promise.reject(new w3.FakeApiError(`unmocked GET ${path}`, 404))
      : Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    w3.posts.push({ path, body });
    if (path === "/sessions/clear") return Promise.resolve({ cleared: 1 });
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

// Path-keyed synchronous data (the kanban-teams-v1168 / ux-wave2 harness).
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (w3.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (w3.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("@/lib/useReviews", () => ({
  useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: w3.events, connected: true }),
}));
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({ enabled: false, supported: false, toggle: () => {}, speak: () => {}, stop: () => {} }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
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
vi.mock("@/components/ReviewPanel", () => ({ ReviewPanel: () => null }));
vi.mock("@/components/TracesPanel", () => ({ TracesPanel: () => null }));
vi.mock("@/components/SessionFeedback", () => ({ SessionFeedback: () => null }));
vi.mock("@/components/chat/DocPreview", () => ({ DocPreview: () => null, appLabelFor: () => "app" }));
// The files a run handed over: a marker, so the page ORDER can be read.
vi.mock("@/components/sessions/SessionFiles", () => ({
  SessionFiles: () => <section data-testid="session-files-marker">Files this run made</section>,
}));

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
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
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
import { KanbanColumn } from "@/components/kanban/KanbanColumn";
import { DndContext } from "@dnd-kit/core";
import { LANES } from "@/lib/kanban";
import { WorldBoard } from "@/components/agents/world/WorldBoard";
import { TimeTravelFeed } from "@/components/TimeTravelFeed";
import * as agentWorlds from "@/lib/agentWorlds";
import { toolWords } from "@/lib/toolWords";
import type { AuditEntry, Review, SessionView } from "@/lib/types";

/* ---- fixtures + helpers ----------------------------------------------------- */

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

/** The mission coordinator, a plain supervisor run, and a mission teammate. */
const COORD = sv("s-coord", "active", {
  task: "Write the quarter-end memo",
  agent_type: "supervisor",
  origin: "job:mission",
});
const PLAIN_SUP = sv("s-plain", "active", {
  task: "Nightly tidy-up",
  agent_type: "supervisor",
  origin: "schedule:nightly-brief",
});
const MATE = sv("s-mate", "active", {
  task: "Draft section two",
  agent_type: "builder",
  origin: "job:mission-member",
});

const ROOT = join(__dirname, "..");
const src = (...p: string[]) => readFileSync(join(ROOT, ...p), "utf8").replace(/\r\n/g, "\n");
const classes = (el: Element | null | undefined) => (el?.getAttribute("class") ?? "").split(/\s+/);
const hasClass = (el: Element | null | undefined, c: string) => classes(el).includes(c);

/** Every element in `root` carrying a `title` that contains `needle`. */
function titled(root: ParentNode, needle: string): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("[title]")).filter((el) =>
    (el.getAttribute("title") ?? "").includes(needle),
  );
}

/** The first non-blank text a reader meets inside `root`. */
function firstText(root: Element): string {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = (n.textContent ?? "").trim();
    if (t) return t;
  }
  return "";
}

/** Reachable now, or after pressing ONE collapsed disclosure. */
function reachableInOneClick(text: string | RegExp): boolean {
  if (screen.queryAllByText(text).length > 0) return true;
  const closed = [
    ...Array.from(document.querySelectorAll<HTMLElement>('button[aria-expanded="false"]')),
    ...Array.from(document.querySelectorAll<HTMLElement>("details:not([open]) > summary")),
  ];
  for (const d of closed) {
    act(() => {
      fireEvent.click(d);
    });
    if (screen.queryAllByText(text).length > 0) return true;
    act(() => {
      fireEvent.click(d);
    });
  }
  return false;
}

beforeEach(() => {
  w3.responses = {};
  w3.gets = [];
  w3.posts = [];
  w3.events = [];
  w3.audit = { entries: [], next_cursor: null, total: 0 };
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

/* ========================================================================== */
/*  One AI identity — the helper                                               */
/* ========================================================================== */

describe("agentDisplayName: the mission coordinator is Jarvis, nobody else is renamed", () => {
  const fn = (agentWorlds as unknown as Record<string, unknown>).agentDisplayName as
    | ((agentType: string, origin?: string | null) => string)
    | undefined;

  it("is exported from lib/agentWorlds", () => {
    expect(typeof fn).toBe("function");
  });

  it("supervisor + origin 'job:mission' (the mission's root) reads 'Jarvis'", () => {
    expect(fn?.("supervisor", "job:mission")).toBe("Jarvis");
  });

  it("any other run keeps its raw id as text (casing is CSS's job)", () => {
    expect(fn?.("supervisor", null)).toBe("supervisor");
    expect(fn?.("supervisor", undefined)).toBe("supervisor");
    expect(fn?.("supervisor", "")).toBe("supervisor");
    expect(fn?.("supervisor", "schedule:nightly-brief")).toBe("supervisor");
    // a TEAMMATE of a mission is not its coordinator (missionLinks: root only)
    expect(fn?.("supervisor", "job:mission-member")).toBe("supervisor");
    // the coordinator's teammates keep their own names
    expect(fn?.("builder", "job:mission")).toBe("builder");
    expect(fn?.("builder", "job:mission-member")).toBe("builder");
  });
});

/* ========================================================================== */
/*  One AI identity — the board card                                           */
/* ========================================================================== */

describe("Session board card: the coordinator reads Jarvis, other ids are title-cased by CSS", () => {
  function renderBoard(sessions: SessionView[]) {
    w3.responses["/sessions/teams"] = { parents: {} };
    return render(<KanbanBoard sessions={sessions} reviews={NO_REVIEWS} reload={() => {}} />);
  }
  const cardOf = (task: string) => screen.getByText(task).closest("[data-team-depth]") as HTMLElement;

  it("a mission coordinator's card says 'Jarvis' and never shows the word 'supervisor'", () => {
    renderBoard([COORD]);
    const card = cardOf(COORD.task);
    expect(within(card).getByText("Jarvis")).toBeInTheDocument();
    expect(card.textContent ?? "").not.toMatch(/supervisor/i);
    // Raw kept: the agent id rides a title on the card.
    expect(titled(card, "supervisor").length).toBeGreaterThan(0);
    // Anti-vacuity: the origin chip still says it came from a mission.
    expect(within(card).getByTestId("origin-chip").textContent).toBe("Mission");
  });

  it("a supervisor run that is NOT a mission keeps its id as text, title-cased by `capitalize`", () => {
    renderBoard([PLAIN_SUP, MATE]);
    const plain = within(cardOf(PLAIN_SUP.task)).getByText("supervisor");
    expect(hasClass(plain, "capitalize") || hasClass(plain.parentElement, "capitalize")).toBe(true);
    expect(within(cardOf(PLAIN_SUP.task)).queryByText("Jarvis")).toBeNull();
    // a mission teammate keeps its own name
    const mate = within(cardOf(MATE.task)).getByText("builder");
    expect(hasClass(mate, "capitalize") || hasClass(mate.parentElement, "capitalize")).toBe(true);
  });

  it("anti-vacuity: a card in review still offers Approve and Reject, and Approve posts", async () => {
    const review = { [COORD.id]: { session_id: COORD.id } as unknown as Review };
    w3.responses["/sessions/teams"] = { parents: {} };
    render(<KanbanBoard sessions={[COORD]} reviews={review} reload={() => {}} />);
    const card = cardOf(COORD.task);
    expect(within(card).getByRole("button", { name: /Reject/ })).toBeInTheDocument();
    fireEvent.click(within(card).getByRole("button", { name: /Approve/ }));
    await waitFor(() => expect(w3.posts.some((p) => p.path === `/reviews/${COORD.id}/approve`)).toBe(true));
  });
});

/* ========================================================================== */
/*  One AI identity — the sessions table and the session page                  */
/* ========================================================================== */

describe("Sessions list: the Agent column speaks the same name", () => {
  function seed() {
    w3.responses["/sessions"] = { sessions: [COORD, PLAIN_SUP] };
    w3.responses["/projects"] = { projects: [] };
  }
  const agentCell = (task: string) => {
    const row = screen.getByText(task).closest("tr") as HTMLElement;
    return row.querySelectorAll("td")[1] as HTMLElement;
  };

  it("the coordinator's row reads 'Jarvis' with the raw id in a title", () => {
    seed();
    render(<SessionsPage />);
    const cell = agentCell(COORD.task);
    expect(cell.textContent?.trim()).toBe("Jarvis");
    expect(titled(cell, "supervisor").length + (cell.getAttribute("title")?.includes("supervisor") ? 1 : 0)).toBeGreaterThan(0);
  });

  it("any other row keeps the raw id as text, title-cased by `capitalize`", () => {
    seed();
    render(<SessionsPage />);
    const cell = agentCell(PLAIN_SUP.task);
    expect(cell.textContent?.trim()).toBe("supervisor");
    const holder = within(cell).getByText("supervisor");
    expect(hasClass(holder, "capitalize")).toBe(true);
  });

  it("anti-vacuity: the agent filter still filters by the RAW id (both supervisor rows match)", () => {
    seed();
    w3.responses["/sessions"] = { sessions: [COORD, PLAIN_SUP, MATE] };
    render(<SessionsPage />);
    const select = screen.getByLabelText("Filter by agent") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(expect.arrayContaining(["supervisor", "builder"]));
    fireEvent.change(select, { target: { value: "supervisor" } });
    expect(screen.getByText(COORD.task)).toBeInTheDocument();
    expect(screen.getByText(PLAIN_SUP.task)).toBeInTheDocument();
    expect(screen.queryByText(MATE.task)).toBeNull();
  });

  // Reviewer fix round: the option LABEL reads what the rows read; the VALUE
  // stays raw (the anti-vacuity test above proves the filter itself).
  it("the agent filter's option label names Jarvis; its value stays the raw id", () => {
    seed();
    w3.responses["/sessions"] = { sessions: [COORD, PLAIN_SUP, MATE] };
    const { unmount } = render(<SessionsPage />);
    let select = screen.getByLabelText("Filter by agent") as HTMLSelectElement;
    let opt = Array.from(select.options).find((o) => o.value === "supervisor")!;
    expect(opt.textContent).toBe("Jarvis / supervisor");
    unmount();
    w3.responses["/sessions"] = { sessions: [COORD, MATE] };
    render(<SessionsPage />);
    select = screen.getByLabelText("Filter by agent") as HTMLSelectElement;
    opt = Array.from(select.options).find((o) => o.value === "supervisor")!;
    expect(opt.textContent).toBe("Jarvis");
    expect(Array.from(select.options).find((o) => o.value === MATE.agent_type)?.textContent).toBe(MATE.agent_type);
  });
});

/* ========================================================================== */
/*  The Session board page (/kanban)                                           */
/* ========================================================================== */

describe("Session board page: named for what it is, lanes in plain words", () => {
  function seed() {
    w3.responses["/sessions"] = {
      sessions: [sv("s-run", "active"), sv("s-done", "completed"), sv("s-bad", "failed")],
    };
    w3.responses["/sessions/teams"] = { parents: {} };
  }

  it("the page heading is 'Session board', not the developer word 'Kanban'", () => {
    seed();
    render(<KanbanPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Session board" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1, name: /Kanban/ })).toBeNull();
    // and the source agrees (the crumb rule in ux-wave1-shell reads PageHeader's title)
    expect(src("app", "kanban", "page.tsx")).toMatch(/<PageHeader\s+title="Session board"/);
  });

  it("the Completed lane says 'Finished', never git's 'Merged & done'", () => {
    seed();
    render(<KanbanPage />);
    expect(screen.queryByText("Merged & done")).toBeNull();
    expect(screen.getByText("Finished")).toBeInTheDocument();
  });

  it("'Clear completed' sits in the Completed lane and is still two-press", async () => {
    seed();
    render(<KanbanPage />);
    const lane = document.querySelector('[data-lane="completed"]') as HTMLElement | null;
    expect(lane).not.toBeNull();
    const clear = within(lane!).getByRole("button", { name: /Clear completed \(1\)/ });
    fireEvent.click(clear);
    // first press only arms
    expect(w3.posts.some((p) => p.path === "/sessions/clear")).toBe(false);
    fireEvent.click(within(lane!).getByRole("button", { name: /Confirm clear\?/ }));
    await waitFor(() =>
      expect(w3.posts.find((p) => p.path === "/sessions/clear")?.body).toEqual({ statuses: ["completed"] }),
    );
  });

  it("anti-vacuity: the four lanes, Clear failed, the drag hint and 'All sessions' are all still here", () => {
    seed();
    render(<KanbanPage />);
    for (const title of ["Active", "In Review", "Completed", "Failed"]) {
      expect(screen.getByRole("heading", { level: 2, name: title })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: /Clear failed \(1\)/ })).toBeInTheDocument();
    expect(screen.getByText(/drag a card from In Review onto Completed to approve/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /All sessions/ }).getAttribute("href")).toBe("/sessions");
  });
});

/* ========================================================================== */
/*  A project's board inside the mission screen — compact                      */
/* ========================================================================== */

describe("Project board (WorldBoard) is compact: two lanes a row, empty lanes collapse at rest", () => {
  const P = "p-acme";
  function seed() {
    w3.responses[`/sessions?project_id=${encodeURIComponent(P)}`] = {
      sessions: [sv("s-p1", "active", { project_id: P, task: "Prepare the quarter-end pack" })],
    };
    w3.responses["/sessions/teams"] = { parents: {} };
  }
  const gridOf = () => screen.getByRole("heading", { level: 2, name: "Active" }).closest(".grid") as HTMLElement;

  it("the embedded board never asks for four lanes at xl — it is two a row", () => {
    seed();
    render(<WorldBoard projectId={P} />);
    const grid = gridOf();
    expect(grid).not.toBeNull();
    const cls = classes(grid);
    expect(cls).not.toContain("xl:grid-cols-4");
    expect(cls).not.toContain("xl:grid-cols-5");
    expect(cls.some((c) => /^(\w+:)?grid-cols-2$/.test(c))).toBe(true);
  });

  it("an EMPTY lane collapses at rest; a lane with cards does not", () => {
    seed();
    render(<WorldBoard projectId={P} />);
    const done = document.querySelector('[data-lane="completed"]');
    const active = document.querySelector('[data-lane="active"]');
    expect(done?.getAttribute("data-collapsed")).toBe("true");
    expect(active?.getAttribute("data-collapsed")).not.toBe("true");
    // the lane itself is still there (same four lanes, same drop targets)
    expect(screen.getByRole("heading", { level: 2, name: "Completed" })).toBeInTheDocument();
  });

  it("the collapse is a REST state: it reads the drag (an empty lane is a drop target while dragging)", () => {
    const both = src("components", "kanban", "KanbanColumn.tsx") + "\n" + src("components", "kanban", "KanbanBoard.tsx");
    expect(both).toContain("data-collapsed");
    const lines = both.split("\n").filter((l) => /collaps/i.test(l) && !/^\s*(\/\/|\*|\/\*)/.test(l));
    expect(lines.some((l) => /draggingFrom|activeId|isOver/.test(l))).toBe(true);
  });

  // Reviewer fix round (mutation M2b): the source regex above is satisfied by
  // a collapse line naming `isOver` alone, so dropping `!draggingFrom` went
  // unnoticed. An empty Completed/Failed lane is the drop target for
  // drag-to-approve/reject — RENDER it mid-drag and assert it stays full height.
  it("an empty compact lane stays FULL HEIGHT while a drag is active (drop target for approve)", () => {
    const completed = LANES.find((l) => l.id === "completed")!;
    const noop = () => {};
    const col = (draggingFrom: "review" | null) => (
      <DndContext>
        <KanbanColumn
          lane={completed}
          sessions={[]}
          compact
          draggingFrom={draggingFrom}
          busyId={null}
          onApprove={noop}
          onReject={noop}
        />
      </DndContext>
    );
    const { rerender } = render(col("review"));
    const lane = () => document.querySelector('[data-lane="completed"]');
    expect(lane()).not.toBeNull();
    expect(lane()?.getAttribute("data-collapsed")).not.toBe("true");
    // CONTROL: the same empty compact lane at rest DOES fold to one line.
    rerender(col(null));
    expect(lane()?.getAttribute("data-collapsed")).toBe("true");
  });

  it("CONTROL: the standalone board (/kanban) keeps four lanes at xl and never collapses", () => {
    w3.responses["/sessions/teams"] = { parents: {} };
    render(<KanbanBoard sessions={[sv("s-a", "active")]} reviews={NO_REVIEWS} reload={() => {}} />);
    expect(classes(gridOf())).toContain("xl:grid-cols-4");
    expect(document.querySelectorAll('[data-collapsed="true"]').length).toBe(0);
  });
});

/* ========================================================================== */
/*  A session's own page reads as a story                                      */
/* ========================================================================== */

const DETAIL_ID = "session_story0001";

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function setDetail(over: Partial<SessionView> = {}) {
  w3.responses[`/sessions/${DETAIL_ID}`] = {
    session: {
      ...sv(DETAIL_ID, "completed", {
        task: "Summarise the engagement letter",
        summary: "Wrote a one-page summary to summary.md",
        finished_at: "2026-10-01T10:05:00Z",
      }),
      ...over,
    },
    transcript: {
      runs: [
        { id: "run_1", agent_type: "coder", state: "completed", steps: 3, parent_id: null, result: "done" },
      ],
      tools: [{ id: "t_1", tool: "write_file", verdict: "allow", ok: true, output: "wrote summary.md" }],
    },
  };
}

describe("Session page: a finished run tells its story", () => {
  it("a FINISHED run with no live events never claims 'No live events yet'", async () => {
    setDetail();
    render(<SessionDetailPage params={fakeParams(DETAIL_ID)} />);
    await screen.findByTestId("session-summary");
    expect(screen.queryByText(/No live events yet/)).toBeNull();
  });

  it("CONTROL: a RUNNING run with no events still says activity will stream in", async () => {
    setDetail({ status: "active", finished_at: null });
    render(<SessionDetailPage params={fakeParams(DETAIL_ID)} />);
    await screen.findByTestId("session-summary");
    expect(screen.getByText(/No live events yet/)).toBeInTheDocument();
  });

  it("story order: the summary, then the files it made, then the run controls", async () => {
    setDetail();
    render(<SessionDetailPage params={fakeParams(DETAIL_ID)} />);
    const summary = await screen.findByTestId("session-summary");
    const files = screen.getByTestId("session-files-marker");
    const controls = screen.getByRole("heading", { level: 2, name: "Run controls" });
    expect(summary.compareDocumentPosition(files) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(files.compareDocumentPosition(controls) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("a mission coordinator's page names its agent 'Jarvis' (raw id in a title)", async () => {
    setDetail({ agent_type: "supervisor", origin: "job:mission" });
    render(<SessionDetailPage params={fakeParams(DETAIL_ID)} />);
    await screen.findByTestId("session-summary");
    const label = screen.getByText("Agent", { selector: "div" });
    const value = label.nextElementSibling as HTMLElement;
    expect(value.textContent?.trim()).toBe("Jarvis");
    expect(titled(value, "supervisor").length).toBeGreaterThan(0);
  });

  it("anti-vacuity: the raw record is still one click away, and every control is still here", async () => {
    setDetail();
    render(<SessionDetailPage params={fakeParams(DETAIL_ID)} />);
    await screen.findByTestId("session-summary");
    // Time-travel keeps its NAME (page-copy-v1232) and the transcript tables stay reachable.
    expect(reachableInOneClick("Time-travel")).toBe(true);
    expect(reachableInOneClick("Agent runs · 1")).toBe(true);
    expect(reachableInOneClick("Tool invocations · 1")).toBe(true);
    expect(screen.getByRole("button", { name: /Rerun/ })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Send a follow-up/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Export \.md/ })).toBeInTheDocument();
    // Delete stays two-press.
    fireEvent.click(screen.getByRole("button", { name: /Delete session/ }));
    expect(screen.getByRole("button", { name: /Confirm delete\?/ })).toBeInTheDocument();
  });
});

/* ========================================================================== */
/*  Time-travel rows lead with words; Undo sits beside what it reverses        */
/* ========================================================================== */

const ENTRIES: AuditEntry[] = [
  {
    id: "e-life",
    ts: "2026-10-01T10:05:00Z",
    kind: "lifecycle",
    actor: "",
    session_id: DETAIL_ID,
    summary: "session.completed",
  },
  {
    id: "e-write",
    ts: "2026-10-01T10:04:00Z",
    kind: "tool",
    actor: "",
    session_id: DETAIL_ID,
    tool: "write_file",
    verdict: "allow",
    ok: true,
    reversible: true,
    undoable: true,
    summary: "write_file ok",
  },
  {
    id: "e-tok",
    ts: "2026-10-01T10:03:00Z",
    kind: "token",
    actor: "",
    session_id: DETAIL_ID,
    input_tokens: 120,
    output_tokens: 40,
    summary: "mock/mock-1 · 120+40 tok",
  },
];

describe("Time-travel: each row's headline is what happened, in words", () => {
  async function renderFeed() {
    w3.audit = { entries: ENTRIES, next_cursor: null, total: ENTRIES.length };
    render(<TimeTravelFeed sessionId={DETAIL_ID} />);
    await screen.findByText(/Session finished/);
  }
  const rowOf = (text: RegExp) => screen.getByText(text).closest("li") as HTMLElement;

  it("a progress row leads with 'Session finished' (the kind is a chip after it)", async () => {
    await renderFeed();
    const row = rowOf(/Session finished/);
    expect(firstText(row)).toBe("Session finished");
    // Raw kept: the kind word is still on the row, the ledger's raw summary in a title.
    expect(row.textContent ?? "").toContain("Progress");
    expect(titled(row, "session.completed").length).toBeGreaterThan(0);
  });

  it("a token row leads with the model in words and the counts", async () => {
    await renderFeed();
    const row = rowOf(/120\+40 tok/);
    expect(firstText(row)).toBe("Demo model · 120+40 tok");
    expect(row.textContent ?? "").not.toMatch(/\bmock\b/);
    // the token chip stays
    expect(row.textContent ?? "").toContain("120↓ 40↑");
  });

  it("a tool row leads with the tool in words, the raw id is a muted chip/title", async () => {
    await renderFeed();
    const row = screen
      .getAllByRole("button", { name: /Undo/ })[0]
      .closest("li") as HTMLElement;
    const head = firstText(row);
    expect(head.toLowerCase()).toContain((toolWords("write_file") ?? "save a file").toLowerCase());
    expect(head).not.toMatch(/write_file/);
    // The exact id is a record (a grant is keyed on it) — still reachable.
    expect(titled(row, "write_file").length).toBeGreaterThan(0);
  });

  it("Undo sits on the headline's line, and is still two-press", async () => {
    await renderFeed();
    const undo = screen.getByRole("button", { name: /^Undo$/ });
    const row = undo.closest("li") as HTMLElement;
    const line = within(row).getByTestId("timeline-line");
    expect(line.contains(undo)).toBe(true);
    expect(firstText(line)).toBe(firstText(row));
    // two-press: the first press only arms
    fireEvent.click(undo);
    expect(w3.posts.some((p) => p.path.startsWith("/undo/"))).toBe(false);
    fireEvent.click(within(row).getByRole("button", { name: /Confirm undo\?/ }));
    await waitFor(() => expect(w3.posts.some((p) => p.path === "/undo/e-write")).toBe(true));
  });

  it("anti-vacuity: every filter tab is still there and still filters", async () => {
    await renderFeed();
    for (const label of ["Everything", "Actions", "Tokens", "Decisions", "Progress"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole("button", { name: "Actions" }));
    await waitFor(() => expect(w3.gets.some((g) => g.startsWith("/audit") && g.includes("kind=tool"))).toBe(true));
  });
});
