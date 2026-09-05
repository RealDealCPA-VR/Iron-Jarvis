/**
 * v1.227.0 wave 1 (A4, UI half) — a PAUSED run is visible as paused.
 *
 * The approvals audit: a run waiting on the user was indistinguishable from a
 * running one everywhere but the bell badge — kanban filed it under Active
 * ("Running now"), the session page said "active" and offered no way to
 * answer. The daemon now serialises `waiting_on: {approval_id, tool}` on the
 * session row while a run is paused; this pins what the dashboard does with
 * it:
 *
 *  - lib/kanban.laneFor puts a waiting session in the In Review lane, and the
 *    card wears an amber "Waiting for you · <tool>" chip. It has NO review, so
 *    the review-lane Approve/Reject buttons must not appear (they POST
 *    /reviews/{id}/approve and would 404); the card links to the session page.
 *  - sessions/[id] renders the SAME ApprovalCard chat shows, answering through
 *    the same POST /chat/approvals/{id} route, and the header badge says what
 *    the run is waiting for.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  get: (path: string) => {
    api.gets.push(path);
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    return Promise.resolve({});
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

// Path-keyed synchronous data for both the session page (useApi) and the
// board's /sessions/teams poll (usePolledApi).
vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? api.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? api.responses[path] ?? null : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: "",
    tools: [],
    phase: null,
    active: false,
    start: () => {},
    stop: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [] }) }));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    enabled: false,
    supported: false,
    toggle: () => {},
    speak: () => {},
    stop: () => {},
  }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: () => {} }) }));
vi.mock("@/components/ReviewPanel", () => ({ ReviewPanel: () => null }));
vi.mock("@/components/TracesPanel", () => ({ TracesPanel: () => null }));
vi.mock("@/components/SessionFeedback", () => ({ SessionFeedback: () => null }));
vi.mock("@/components/TimeTravelFeed", () => ({ TimeTravelFeed: () => null }));
vi.mock("@/components/chat/DocPreview", () => ({
  DocPreview: ({ path }: { path: string }) => <div data-testid="doc-preview">{path}</div>,
  appLabelFor: () => "app",
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
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
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

import { laneFor } from "@/lib/kanban";
import { KanbanBoard } from "@/components/kanban/KanbanBoard";
import SessionDetailPage from "@/app/sessions/[id]/page";
import type { Review, SessionView } from "@/lib/types";

afterEach(() => {
  cleanup();
  api.gets = [];
  api.posts = [];
  api.responses = {};
});

function sv(id: string, status: string, over: Partial<SessionView> = {}): SessionView {
  return {
    id,
    task: `task ${id}`,
    agent_type: "coder",
    provider: "mock",
    model: "mock-model",
    status,
    workspace_path: "C:/w",
    summary: "",
    created_at: "2026-09-04T10:00:00Z",
    finished_at: null,
    ...over,
  };
}

const WAITING = { approval_id: "apr_9", tool: "rename_file" };
const NO_REVIEWS = {} as Record<string, Review>;

/** The column whose header reads `title` (KanbanColumn's root is the nearest
 *  flex-col ancestor of its <h2>). */
function column(title: string): HTMLElement {
  return screen.getByRole("heading", { name: title }).closest(".flex-col") as HTMLElement;
}

/* ------------------------------------------------------------ lib/kanban */

describe("laneFor — a paused run is In Review, not Running now", () => {
  it("files a session with waiting_on under review, and without it under active", () => {
    expect(laneFor(sv("s-w", "active", { waiting_on: WAITING }), false)).toBe("review");
    expect(laneFor(sv("s-r", "active"), false)).toBe("active");
    expect(laneFor(sv("s-n", "active", { waiting_on: null }), false)).toBe("active");
  });
});

/* ---------------------------------------------------------- KanbanBoard */

describe("KanbanBoard — the waiting card", () => {
  it("renders in the In Review column with the amber chip, no review buttons, a link to answer", () => {
    api.responses["/sessions/teams"] = { parents: {} };
    render(
      <KanbanBoard
        sessions={[sv("s-w", "active", { waiting_on: WAITING }), sv("s-r", "active")]}
        reviews={NO_REVIEWS}
        reload={() => {}}
      />,
    );
    const review = column("In Review");
    expect(review).toHaveTextContent("task s-w");
    expect(column("Active")).not.toHaveTextContent("task s-w");
    expect(column("Active")).toHaveTextContent("task s-r");

    const chip = screen.getByTestId("session-waiting-chip");
    expect(chip).toHaveTextContent("Waiting for you · rename_file");
    expect(review.contains(chip)).toBe(true);

    // No review record → no Approve/Reject (they would POST /reviews/{id}).
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Reject/ })).not.toBeInTheDocument();
    const answer = screen.getByRole("link", { name: /Answer on the session page/ });
    expect(answer).toHaveAttribute("href", "/sessions/s-w");
  });

  it("a finished run that fell short wears the amber outcome chip in Completed", () => {
    api.responses["/sessions/teams"] = { parents: {} };
    render(
      <KanbanBoard
        sessions={[
          sv("s-short", "completed", { outcome: "needs_you", finished_at: "2026-09-04T11:00:00Z" }),
          sv("s-ok", "completed", { outcome: "completed", finished_at: "2026-09-04T11:00:00Z" }),
        ]}
        reviews={NO_REVIEWS}
        reload={() => {}}
      />,
    );
    const done = column("Completed");
    expect(done).toHaveTextContent("task s-short");
    const chips = screen.getAllByTestId("session-outcome-chip");
    expect(chips).toHaveLength(1); // s-ok earned plain green — no chip
    expect(chips[0]).toHaveTextContent("Completed · needs you");
  });
});

/* ------------------------------------------------------ sessions/[id] page */

/** `use(params)` reads a pre-settled promise synchronously; a bare
 *  Promise.resolve would suspend the page and render nothing (the same helper
 *  worklist-v1174 / session-files-v1168 use). */
function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function renderPage(session: SessionView) {
  api.responses[`/sessions/${session.id}`] = {
    session,
    transcript: { runs: [], tools: [] },
  };
  return render(<SessionDetailPage params={fakeParams(session.id)} />);
}

describe("sessions/[id] — the run is paused for you", () => {
  it("shows the ApprovalCard for waiting_on and the amber header chip", async () => {
    renderPage(sv("s-w", "active", { waiting_on: WAITING }));
    await act(async () => {});
    const card = await screen.findByTestId("session-approval");
    expect(card).toHaveTextContent("rename_file");
    expect(screen.getByTestId("session-waiting-chip")).toHaveTextContent(
      "Waiting for you · rename_file",
    );
    expect(screen.getByRole("button", { name: /Allow once/ })).toBeInTheDocument();
  });

  it("answers through the same /chat/approvals/{id} route chat uses", async () => {
    renderPage(sv("s-w", "active", { waiting_on: WAITING }));
    await act(async () => {});
    fireEvent.click(await screen.findByRole("button", { name: /Allow once/ }));
    await waitFor(() =>
      expect(
        api.posts.some(
          (p) =>
            p.path === "/chat/approvals/apr_9" &&
            (p.body as { decision?: string })?.decision === "once",
        ),
      ).toBe(true),
    );
  });

  it("renders no card and the plain status when nothing is pending", async () => {
    renderPage(sv("s-r", "active", { waiting_on: null }));
    await act(async () => {});
    expect(screen.queryByTestId("session-approval")).not.toBeInTheDocument();
    expect(screen.queryByTestId("session-waiting-chip")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Allow once/ })).not.toBeInTheDocument();
  });

  it("a completed run that needs you says so in the header instead of plain green", async () => {
    renderPage(
      sv("s-done", "completed", {
        outcome: "needs_you",
        finished_at: "2026-09-04T11:00:00Z",
      }),
    );
    await act(async () => {});
    expect(screen.getByTestId("session-outcome-chip")).toHaveTextContent(
      "Completed · needs you",
    );
    expect(screen.queryByTestId("session-approval")).not.toBeInTheDocument();
  });
});
