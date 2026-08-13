import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

/**
 * v1.168.0 P5 — provenance chips + team nesting on the Kanban board.
 *
 * What can fail silently here, and is therefore pinned:
 *
 *  - layoutTeams moves a child into its PARENT'S lane. Getting the join wrong
 *    (mapping the run id instead of the session id, or child/parent swapped)
 *    still renders a plausible board — with the wrong cards nested.
 *  - a child whose parent is off the board (filtered, other project) must
 *    render exactly as before — flat, in its OWN lane. Dropping it is
 *    invisible work.
 *  - corrupt links (self-parent, mutual cycle) must degrade flat, never spin
 *    or swallow a session.
 *  - the origin filter's sentinels are prefixed because the origin charset
 *    allows underscores — a literal "__mine__" origin must not read as the
 *    sentinel's rows.
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { FakeApiError };
});

const hooks = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
}));

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  get: () => Promise.resolve({}),
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

// Path-keyed synchronous data — the board polls /sessions/teams through
// usePolledApi, the sessions page pulls /sessions + /projects.
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? hooks.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? hooks.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({
      href,
      children,
      ...rest
    }: {
      href: string;
      children?: React.ReactNode;
    }) => createElement("a", { href, ...rest }, children),
  };
});

// NewSessionForm has its own data lifecycle (agents/models/health fetches,
// useSearchParams) — out of scope for the list/filter assertions.
vi.mock("@/components/NewSessionForm", () => ({ NewSessionForm: () => null }));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "layout",
    "whileHover",
    "whileTap",
    "whileInView",
    "viewport",
  ]);
  const tagFor =
    (tag: string) => (props: Record<string, unknown>) => {
      const rest: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(props)) {
        if (!MOTION_ONLY.has(k)) rest[k] = v;
      }
      return createElement(tag, rest);
    };
  const cache = new Map<string, unknown>();
  return {
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import {
  KanbanBoard,
  assignBoardLanes,
  layoutTeams,
  type BoardLaneId,
  type TeamRow,
} from "@/components/kanban/KanbanBoard";
import SessionsPage from "@/app/sessions/page";
import type { Review, SessionView } from "@/lib/types";

afterEach(() => {
  cleanup();
  hooks.responses = {};
});

/* --------------------------------------------------------------- fixtures */

function sv(
  id: string,
  status: string,
  over: Partial<SessionView> = {},
): SessionView {
  return {
    id,
    task: `task ${id}`,
    agent_type: "coder",
    provider: "mock",
    model: "mock-model",
    status,
    workspace_path: "C:/w",
    summary: "",
    created_at: "2026-08-12T10:00:00Z",
    finished_at: null,
    ...over,
  };
}

const NO_REVIEWS = {} as Record<string, Review>;
const NONE: ReadonlySet<string> = new Set();

function mkReview(sessionId: string): Review {
  return { session_id: sessionId, changed_files: [], diff: "", risk: "low" };
}

function laneIds(rows: Record<BoardLaneId, TeamRow[]>, lane: BoardLaneId) {
  return rows[lane].map((r) => [r.session.id, r.depth] as const);
}

/* ------------------------------------------------------ layoutTeams (pure) */

describe("layoutTeams", () => {
  it("a child renders right after its parent, depth 1, in the PARENT'S lane", () => {
    // Child is COMPLETED — its own lane would be Completed; the team layout
    // pulls it under the active parent instead, and Completed goes empty.
    const lanes = assignBoardLanes(
      [sv("s-parent", "active"), sv("s-child", "completed")],
      NO_REVIEWS,
    );
    const { rows, counts } = layoutTeams(lanes, { "s-child": "s-parent" }, NONE);
    expect(laneIds(rows, "active")).toEqual([
      ["s-parent", 0],
      ["s-child", 1],
    ]);
    expect(rows.completed).toEqual([]);
    expect(counts.get("s-parent")).toBe(1);
  });

  it("nests grandchildren at depth 2 and orders siblings by created_at", () => {
    const lanes = assignBoardLanes(
      [
        sv("s-root", "active"),
        sv("s-b", "active", { created_at: "2026-08-12T10:02:00Z" }),
        sv("s-a", "active", { created_at: "2026-08-12T10:01:00Z" }),
        sv("s-grand", "completed"),
      ],
      NO_REVIEWS,
    );
    const { rows, counts } = layoutTeams(
      lanes,
      { "s-a": "s-root", "s-b": "s-root", "s-grand": "s-a" },
      NONE,
    );
    // s-a (earlier) before s-b, s-grand nested under s-a at depth 2.
    expect(laneIds(rows, "active")).toEqual([
      ["s-root", 0],
      ["s-a", 1],
      ["s-grand", 2],
      ["s-b", 1],
    ]);
    expect(counts.get("s-root")).toBe(3);
    expect(counts.get("s-a")).toBe(1);
  });

  it("a collapsed parent hides ALL descendants but keeps the full count", () => {
    const lanes = assignBoardLanes(
      [sv("s-root", "active"), sv("s-a", "active"), sv("s-grand", "completed")],
      NO_REVIEWS,
    );
    const { rows, counts } = layoutTeams(
      lanes,
      { "s-a": "s-root", "s-grand": "s-a" },
      new Set(["s-root"]),
    );
    expect(laneIds(rows, "active")).toEqual([["s-root", 0]]);
    expect(rows.completed).toEqual([]);
    expect(counts.get("s-root")).toBe(2); // the badge still says "Team of 2"
  });

  it("a child whose parent is OFF the board renders flat in its own lane", () => {
    const lanes = assignBoardLanes([sv("s-child", "completed")], NO_REVIEWS);
    const { rows, counts } = layoutTeams(
      lanes,
      { "s-child": "s-parent-filtered-out" },
      NONE,
    );
    expect(laneIds(rows, "completed")).toEqual([["s-child", 0]]);
    expect(counts.size).toBe(0);
  });

  it("no team links → the flat board, byte-for-byte lane order", () => {
    const lanes = assignBoardLanes(
      [sv("s-1", "active"), sv("s-2", "completed"), sv("s-3", "failed")],
      NO_REVIEWS,
    );
    const { rows } = layoutTeams(lanes, {}, NONE);
    expect(laneIds(rows, "active")).toEqual([["s-1", 0]]);
    expect(laneIds(rows, "completed")).toEqual([["s-2", 0]]);
    expect(laneIds(rows, "failed")).toEqual([["s-3", 0]]);
  });

  it("a self-link is ignored, not a nest-under-itself", () => {
    const lanes = assignBoardLanes([sv("s-a", "active")], NO_REVIEWS);
    const { rows, counts } = layoutTeams(lanes, { "s-a": "s-a" }, NONE);
    expect(laneIds(rows, "active")).toEqual([["s-a", 0]]);
    expect(counts.get("s-a")).toBeUndefined();
  });

  it("a mutual cycle terminates and BOTH sessions still render", () => {
    const lanes = assignBoardLanes(
      [sv("s-a", "active"), sv("s-b", "active")],
      NO_REVIEWS,
    );
    const { rows } = layoutTeams(
      lanes,
      { "s-a": "s-b", "s-b": "s-a" },
      NONE,
    );
    const all = ([] as (readonly [string, number])[]).concat(
      ...(["queued", "active", "review", "completed", "failed"] as const).map(
        (l) => laneIds(rows, l),
      ),
    );
    expect(new Set(all.map(([id]) => id))).toEqual(new Set(["s-a", "s-b"]));
  });
});

/* ------------------------------------------------------- board rendering */

describe("KanbanBoard team rendering", () => {
  function renderBoard(sessions: SessionView[], parents: Record<string, string>) {
    hooks.responses["/sessions/teams"] = { parents };
    return render(
      <KanbanBoard sessions={sessions} reviews={NO_REVIEWS} reload={() => {}} />,
    );
  }

  it("nests the child under the parent's card with a Team badge, and collapse works", () => {
    renderBoard(
      [
        sv("s-parent", "active", { origin: "schedule:nightly" }),
        sv("s-child", "completed"),
      ],
      { "s-child": "s-parent" },
    );

    // Child renders (in the parent's lane) at depth 1, AFTER the parent.
    const childCard = screen
      .getByText("task s-child")
      .closest("[data-team-depth]") as HTMLElement;
    expect(childCard.getAttribute("data-team-depth")).toBe("1");
    const parentCard = screen
      .getByText("task s-parent")
      .closest("[data-team-depth]") as HTMLElement;
    expect(parentCard.getAttribute("data-team-depth")).toBe("0");
    expect(
      parentCard.compareDocumentPosition(childCard) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // The child left its own lane — Completed shows its empty state.
    expect(screen.getByText("No completed sessions")).toBeInTheDocument();

    // The parent's badge names the real member count.
    const badge = screen.getByTestId("team-badge");
    expect(badge).toHaveTextContent("Team of 1");
    expect(badge).toHaveAttribute("aria-expanded", "true");

    // Collapse hides the member; expand brings it back.
    fireEvent.click(badge);
    expect(screen.queryByText("task s-child")).not.toBeInTheDocument();
    expect(screen.getByTestId("team-badge")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    fireEvent.click(screen.getByTestId("team-badge"));
    expect(screen.getByText("task s-child")).toBeInTheDocument();
  });

  it("renders the origin chip on a card, and none for untagged sessions", () => {
    renderBoard(
      [sv("s-auto", "active", { origin: "schedule:nightly" }), sv("s-me", "active")],
      {},
    );
    const chips = screen.getAllByTestId("origin-chip");
    expect(chips).toHaveLength(1);
    expect(chips[0]).toHaveTextContent("schedule:nightly");
    // No team links → no badges anywhere.
    expect(screen.queryByTestId("team-badge")).not.toBeInTheDocument();
  });

  it("a child whose parent is scoped out (projectId board) renders flat", () => {
    hooks.responses["/sessions/teams"] = { parents: { "s-child": "s-parent" } };
    render(
      <KanbanBoard
        sessions={[
          sv("s-parent", "active", { project_id: "p-other" }),
          sv("s-child", "completed", { project_id: "p-mine" }),
        ]}
        reviews={NO_REVIEWS}
        reload={() => {}}
        projectId="p-mine"
      />,
    );
    const childCard = screen
      .getByText("task s-child")
      .closest("[data-team-depth]") as HTMLElement;
    expect(childCard.getAttribute("data-team-depth")).toBe("0");
    expect(screen.queryByText("task s-parent")).not.toBeInTheDocument();
    expect(screen.queryByTestId("team-badge")).not.toBeInTheDocument();
  });
});

/* ------------------------------- nested cards keep their OWN lane's powers */

describe("nested cards keep their own lane's affordances (v1.168.0 review fix)", () => {
  function renderBoard(
    sessions: SessionView[],
    parents: Record<string, string>,
    reviews: Record<string, Review> = NO_REVIEWS,
  ) {
    hooks.responses["/sessions/teams"] = { parents };
    return render(
      <KanbanBoard sessions={sessions} reviews={reviews} reload={() => {}} />,
    );
  }

  it("a failed child nested under an active parent keeps Retry/Dismiss", () => {
    renderBoard([sv("s-parent", "active"), sv("s-child", "failed")], {
      "s-child": "s-parent",
    });
    // Nested in the parent's (Active) column…
    const childCard = screen
      .getByText("task s-child")
      .closest("[data-team-depth]") as HTMLElement;
    expect(childCard.getAttribute("data-team-depth")).toBe("1");
    // …but it is still a FAILED session: its footer survives the move.
    expect(screen.getByText("Retry")).toBeInTheDocument();
    expect(screen.getByText("Dismiss")).toBeInTheDocument();
  });

  it("a review child nested under an active parent keeps Approve/Reject (and only the child)", () => {
    renderBoard(
      [sv("s-parent", "active"), sv("s-child", "active")],
      { "s-child": "s-parent" },
      { "s-child": mkReview("s-child") },
    );
    const approve = screen.getAllByText("Approve");
    expect(approve).toHaveLength(1);
    const childCard = screen
      .getByText("task s-child")
      .closest("[data-team-depth]") as HTMLElement;
    expect(childCard.contains(approve[0])).toBe(true);
    // The review-lane "Add context" footer follows the TRUE lane too.
    expect(screen.getByText("Add context")).toBeInTheDocument();
  });

  it("an active child nested under a REVIEW parent is not offered Approve/Reject", () => {
    renderBoard(
      [sv("s-parent", "active"), sv("s-child", "active")],
      { "s-child": "s-parent" },
      { "s-parent": mkReview("s-parent") },
    );
    // Exactly one Approve — the parent's. The active child must not carry a
    // review footer whose POST /reviews/{child}/approve cannot succeed.
    const approve = screen.getAllByText("Approve");
    expect(approve).toHaveLength(1);
    const parentCard = screen
      .getByText("task s-parent")
      .closest("[data-team-depth]") as HTMLElement;
    expect(parentCard.contains(approve[0])).toBe(true);
    const childCard = screen
      .getByText("task s-child")
      .closest("[data-team-depth]") as HTMLElement;
    expect(childCard.textContent).not.toContain("Approve");
  });

  it("the Clear toolbar mirrors the displayed columns — no phantom 'Clear failed'", () => {
    renderBoard([sv("s-parent", "active"), sv("s-child", "failed")], {
      "s-child": "s-parent",
    });
    // The failed child renders inside the Active column, so the Failed column
    // is honestly empty…
    expect(screen.getByText("No failed sessions")).toBeInTheDocument();
    // …and the toolbar must not contradict it one line above.
    expect(screen.queryByText(/Clear failed/)).not.toBeInTheDocument();
  });

  it("flat finished sessions still get Clear buttons counted from the columns", () => {
    renderBoard([sv("s-f", "failed"), sv("s-c", "completed")], {});
    expect(screen.getByText("Clear failed (1)")).toBeInTheDocument();
    expect(screen.getByText("Clear completed (1)")).toBeInTheDocument();
  });
});

/* --------------------------------------------- sessions page: origin chips */

describe("sessions page origin provenance", () => {
  function seedSessions() {
    hooks.responses["/sessions"] = {
      sessions: [
        sv("s-sched", "completed", { origin: "schedule:nightly-brief" }),
        sv("s-job", "completed", { origin: "job:agents" }),
        sv("s-mine", "completed"),
      ],
    };
    hooks.responses["/projects"] = { projects: [] };
  }

  it("renders an origin chip per tagged row and nothing for untagged rows", () => {
    seedSessions();
    render(<SessionsPage />);
    const chips = screen.getAllByTestId("origin-chip");
    expect(chips.map((c) => c.textContent)).toEqual(
      expect.arrayContaining(["schedule:nightly-brief", "job:agents"]),
    );
    expect(chips).toHaveLength(2); // s-mine gets NO chip — absence is honest
  });

  it('filter "Automated" keeps only tagged rows; "Mine" only untagged', () => {
    seedSessions();
    render(<SessionsPage />);
    const select = screen.getByLabelText("Filter by origin");

    fireEvent.change(select, { target: { value: "__auto__" } });
    expect(screen.getByText("task s-sched")).toBeInTheDocument();
    expect(screen.getByText("task s-job")).toBeInTheDocument();
    expect(screen.queryByText("task s-mine")).not.toBeInTheDocument();

    fireEvent.change(select, { target: { value: "__mine__" } });
    expect(screen.getByText("task s-mine")).toBeInTheDocument();
    expect(screen.queryByText("task s-sched")).not.toBeInTheDocument();
    expect(screen.queryByText("task s-job")).not.toBeInTheDocument();
  });

  it("per-kind options exist for each present kind and filter to that kind only", () => {
    seedSessions();
    render(<SessionsPage />);
    const select = screen.getByLabelText("Filter by origin") as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toContain("kind:schedule");
    expect(values).toContain("kind:job");

    fireEvent.change(select, { target: { value: "kind:schedule" } });
    expect(screen.getByText("task s-sched")).toBeInTheDocument();
    expect(screen.queryByText("task s-job")).not.toBeInTheDocument();
    expect(screen.queryByText("task s-mine")).not.toBeInTheDocument();
  });

  it("a literal '__mine__' ORIGIN is automated, not the Mine sentinel's rows", () => {
    hooks.responses["/sessions"] = {
      sessions: [
        sv("s-tricky", "completed", { origin: "__mine__" }),
        sv("s-plain", "completed"),
      ],
    };
    hooks.responses["/projects"] = { projects: [] };
    render(<SessionsPage />);
    const select = screen.getByLabelText("Filter by origin");
    fireEvent.change(select, { target: { value: "__mine__" } });
    // The sentinel means "no origin tag" — the session whose origin literally
    // says "__mine__" was dispatched by SOMETHING and must stay out.
    expect(screen.getByText("task s-plain")).toBeInTheDocument();
    expect(screen.queryByText("task s-tricky")).not.toBeInTheDocument();
  });
});
