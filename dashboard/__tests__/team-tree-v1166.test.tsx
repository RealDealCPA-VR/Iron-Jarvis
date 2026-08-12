import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { StrictMode } from "react";

/**
 * v1.166.0 — the session detail page grows team surfaces (P8).
 *
 * Three things are being guarded here, each with a way to fail silently:
 *
 *  - the TEAM TREE nests by RUN ownership: `parent_run_id` names a run, the
 *    `runs` list maps runs to their owning session, and getting that join
 *    wrong files a grandchild under the root — visually plausible, factually
 *    wrong. Worse, an unresolvable parent must surface AT THE ROOT, because a
 *    dropped delegation is invisible work.
 *  - both panels render NOTHING for a solo session — regressing that turns
 *    every session page into a billboard for machinery that isn't in play.
 *  - the QUEUED lane: laneFor predates status "queued" and files it under
 *    Active ("Running now" about work that has not started). boardLaneFor must
 *    say "queued", and with an empty queue the board must be EXACTLY today's
 *    four lanes.
 */

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    calls: [] as string[],
    responses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  get: (path: string) => {
    api.calls.push(path);
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    }
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

/* -- Session detail page harness (defects 1+2 of the v1.166.0 review) -------
 * The page's data hooks are mocked so the two live-stream regressions can be
 * pinned at the WIRING level, where both bugs actually lived. `stream` records
 * every start/stop in order — the StrictMode test is an assertion about that
 * exact sequence. */
const pageApi = vi.hoisted(() => ({
  detail: null as unknown,
}));
const stream = vi.hoisted(() => {
  const s = {
    text: "",
    active: false,
    calls: [] as string[],
    start(id: string) {
      s.calls.push(`start:${id}`);
      s.active = true;
    },
    stop() {
      s.calls.push("stop");
      s.active = false;
    },
    reset() {
      s.text = "";
      s.active = false;
      s.calls = [];
    },
  };
  return s;
});

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    // Only the nested detail endpoint gets data; evaluation/review stay empty.
    data: path && /^\/sessions\/[^/]+$/.test(path) ? pageApi.detail : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: () => ({
    data: null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: stream.text,
    tools: [],
    phase: null,
    active: stream.active,
    start: stream.start,
    stop: stream.stop,
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
// Panels with their own data lifecycles — out of scope for these two defects.
vi.mock("@/components/ReviewPanel", () => ({ ReviewPanel: () => null }));
vi.mock("@/components/TracesPanel", () => ({ TracesPanel: () => null }));
vi.mock("@/components/SessionFeedback", () => ({ SessionFeedback: () => null }));
vi.mock("@/components/TimeTravelFeed", () => ({ TimeTravelFeed: () => null }));

// App-router Link needs a router context in jsdom; a plain anchor keeps the
// href assertions honest without one.
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
  ]);
  const tagFor =
    (tag: string) => (props: Record<string, unknown>) => {
      const rest: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(props)) {
        if (!MOTION_ONLY.has(k)) rest[k] = v;
      }
      return createElement(tag, rest);
    };
  // Cache per tag: returning a FRESH component from every `motion.div` access
  // makes React see a new element type each render and REMOUNT the subtree,
  // which silently breaks any test asserting on a DOM node across rerenders
  // (the live-stream scroll tests below hold a <pre> handle).
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
  TeamTree,
  buildTeamTree,
  teamSize,
  type TeamChild,
  type TeamResponse,
} from "@/components/sessions/TeamTree";
import { BlackboardPanel } from "@/components/sessions/BlackboardPanel";
import {
  boardLaneFor,
  assignBoardLanes,
  visibleLanes,
  QUEUED_LANE,
} from "@/components/kanban/KanbanBoard";
import { LANES } from "@/lib/kanban";
import SessionDetailPage from "@/app/sessions/[id]/page";
import type { Review, SessionView } from "@/lib/types";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  api.calls = [];
  api.responses = {};
  pageApi.detail = null;
  stream.reset();
});

/* ------------------------------------------------------------- fixtures */

function child(
  id: string,
  parentRun: string,
  over: Partial<TeamChild> = {},
): TeamChild {
  return {
    id,
    task: `task for ${id}`,
    agent_type: "coder",
    provider: "mock",
    model: "mock-model",
    status: "active",
    workspace_path: "C:/w",
    summary: "",
    created_at: "2026-08-12T10:00:00Z",
    finished_at: null,
    parent_run_id: parentRun,
    ...over,
  };
}

/** root run r-root spawned s-child; s-child's run r-child spawned s-grand. */
const TEAM: TeamResponse = {
  found: true,
  session_id: "s-root",
  children: [
    child("s-child", "r-root"),
    child("s-grand", "r-child", { agent_type: "researcher", status: "completed" }),
  ],
  runs: [
    {
      id: "r-root",
      session_id: "s-root",
      parent_id: null,
      agent_type: "supervisor",
      state: "completed",
    },
    {
      id: "r-child",
      session_id: "s-child",
      parent_id: "r-root",
      agent_type: "coder",
      state: "running",
    },
  ],
};

/* -------------------------------------------------- buildTeamTree (pure) */

describe("buildTeamTree", () => {
  it("nests a grandchild under the child whose RUN spawned it", () => {
    const tree = buildTeamTree(TEAM);
    expect(tree).toHaveLength(1);
    expect(tree[0].session.id).toBe("s-child");
    expect(tree[0].children).toHaveLength(1);
    expect(tree[0].children[0].session.id).toBe("s-grand");
    expect(tree[0].children[0].children).toEqual([]);
  });

  it("counts every node, nested included", () => {
    expect(teamSize(buildTeamTree(TEAM))).toBe(2);
  });

  it("a child with an unresolvable parent_run_id surfaces AT THE ROOT, not dropped", () => {
    const team: TeamResponse = {
      ...TEAM,
      children: [child("s-orphan", "r-nobody-knows")],
    };
    const tree = buildTeamTree(team);
    expect(tree.map((n) => n.session.id)).toEqual(["s-orphan"]);
  });

  it("a self-parented child renders at the root instead of vanishing", () => {
    const team: TeamResponse = {
      found: true,
      session_id: "s-root",
      children: [child("s-loop", "r-loop")],
      runs: [
        {
          id: "r-loop",
          session_id: "s-loop",
          parent_id: null,
          agent_type: "coder",
          state: "running",
        },
      ],
    };
    const tree = buildTeamTree(team);
    expect(tree.map((n) => n.session.id)).toEqual(["s-loop"]);
  });

  it("a mutual cycle terminates and BOTH sessions render", () => {
    const team: TeamResponse = {
      found: true,
      session_id: "s-root",
      children: [child("s-a", "r-b"), child("s-b", "r-a")],
      runs: [
        { id: "r-a", session_id: "s-a", parent_id: null, agent_type: "x", state: "running" },
        { id: "r-b", session_id: "s-b", parent_id: null, agent_type: "x", state: "running" },
      ],
    };
    const tree = buildTeamTree(team);
    expect(teamSize(tree)).toBe(2);
    const ids = new Set<string>();
    const walk = (nodes: ReturnType<typeof buildTeamTree>) => {
      for (const n of nodes) {
        ids.add(n.session.id);
        walk(n.children);
      }
    };
    walk(tree);
    expect(ids).toEqual(new Set(["s-a", "s-b"]));
  });
});

/* ------------------------------------------------------- TeamTree render */

describe("TeamTree", () => {
  it("renders nothing when the endpoint says found:false", async () => {
    api.responses["/sessions/s-x/team"] = {
      found: false,
      session_id: "s-x",
      children: [],
      runs: [],
    };
    const { container } = render(<TeamTree sessionId="s-x" active={false} />);
    await waitFor(() => expect(api.calls).toContain("/sessions/s-x/team"));
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when found but childless (a solo session grows no Team box)", async () => {
    api.responses["/sessions/s-solo/team"] = {
      found: true,
      session_id: "s-solo",
      children: [],
      runs: [{ id: "r1", session_id: "s-solo", parent_id: null, agent_type: "coder", state: "completed" }],
    };
    const { container } = render(<TeamTree sessionId="s-solo" active={false} />);
    await waitFor(() => expect(api.calls).toContain("/sessions/s-solo/team"));
    expect(container.firstChild).toBeNull();
  });

  it("renders the tree: header count, child links to /sessions/<id>, status text, nesting", async () => {
    api.responses["/sessions/s-root/team"] = TEAM;
    render(<TeamTree sessionId="s-root" active={false} />);

    expect(await screen.findByText("Team · 2 agents")).toBeInTheDocument();

    const childLink = screen.getByText("coder").closest("a");
    expect(childLink).not.toBeNull();
    expect(childLink!.getAttribute("href")).toBe("/sessions/s-child");

    const grandLink = screen.getByText("researcher").closest("a");
    expect(grandLink!.getAttribute("href")).toBe("/sessions/s-grand");

    // Status chips carry the real values, not a hardcoded label.
    expect(screen.getByText("active")).toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();

    // Nesting is structural: the grandchild sits inside an indented branch.
    expect(grandLink!.closest(".border-l")).not.toBeNull();
    expect(childLink!.closest(".border-l")).toBeNull();

    // Tasks are shown so the user can tell delegations apart.
    expect(screen.getByText("task for s-child")).toBeInTheDocument();
  });

  it("polls (~8s) while the session is active, and only then", async () => {
    vi.useFakeTimers();
    api.responses["/sessions/s-root/team"] = TEAM;
    render(<TeamTree sessionId="s-root" active />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const initial = api.calls.filter((c) => c === "/sessions/s-root/team").length;
    expect(initial).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(api.calls.filter((c) => c === "/sessions/s-root/team").length).toBe(2);
  });

  it("does NOT poll for a finished session", async () => {
    vi.useFakeTimers();
    api.responses["/sessions/s-root/team"] = TEAM;
    render(<TeamTree sessionId="s-root" active={false} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(api.calls.filter((c) => c === "/sessions/s-root/team").length).toBe(1);
  });
});

/* ------------------------------------------------------- BlackboardPanel */

describe("BlackboardPanel", () => {
  const NOTE = {
    id: "bb_1",
    author: "run_alpha",
    kind: "note",
    to_agent: null,
    text: "Found the config bug in loader.py",
    created_at: "2026-08-12T10:05:00Z",
  };
  const MESSAGE = {
    id: "bb_2",
    author: "run_alpha",
    kind: "message",
    to_agent: "run_beta",
    text: "Please re-run the failing suite",
    created_at: "2026-08-12T10:06:00Z",
  };

  it("renders nothing for an empty board", async () => {
    api.responses["/blackboard/s-root"] = { board_id: "s-root", records: [] };
    const { container } = render(
      <BlackboardPanel sessionId="s-root" active={false} />,
    );
    await waitFor(() => expect(api.calls).toContain("/blackboard/s-root"));
    expect(container.firstChild).toBeNull();
  });

  it("renders notes and directed messages with author + recipient run ids", async () => {
    api.responses["/blackboard/s-root"] = {
      board_id: "s-root",
      records: [NOTE, MESSAGE],
    };
    render(<BlackboardPanel sessionId="s-root" active={false} />);

    expect(await screen.findByText("Blackboard · 2")).toBeInTheDocument();
    expect(
      screen.getByText("Found the config bug in loader.py"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Please re-run the failing suite"),
    ).toBeInTheDocument();
    // Kind labels distinguish a broadcast note from a directed message.
    expect(screen.getByText("note")).toBeInTheDocument();
    expect(screen.getByText("message")).toBeInTheDocument();
    // Author appears for both records; the recipient only on the message.
    expect(screen.getAllByText("run_alpha")).toHaveLength(2);
    expect(screen.getByText("→ run_beta")).toBeInTheDocument();
  });

  it("polls (~5s) while active", async () => {
    vi.useFakeTimers();
    api.responses["/blackboard/s-root"] = {
      board_id: "s-root",
      records: [NOTE],
    };
    render(<BlackboardPanel sessionId="s-root" active />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(api.calls.filter((c) => c === "/blackboard/s-root").length).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(api.calls.filter((c) => c === "/blackboard/s-root").length).toBe(2);
  });

  it("does not poll when the session is finished", async () => {
    vi.useFakeTimers();
    api.responses["/blackboard/s-root"] = {
      board_id: "s-root",
      records: [NOTE],
    };
    render(<BlackboardPanel sessionId="s-root" active={false} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(api.calls.filter((c) => c === "/blackboard/s-root").length).toBe(1);
  });
});

/* ------------------------------------------------- Kanban "Queued" lane */

function sv(id: string, status: string): SessionView {
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
  };
}

describe("kanban queued lane (B6)", () => {
  it('boardLaneFor: status "queued" → the queued lane, not Active', () => {
    expect(boardLaneFor(sv("s1", "queued"), false)).toBe("queued");
    expect(boardLaneFor(sv("s1", "QUEUED"), false)).toBe("queued");
  });

  it("review precedence is preserved — a reviewed session never shows queued", () => {
    expect(boardLaneFor(sv("s1", "queued"), true)).toBe("review");
  });

  it("every pre-existing status keeps its lane (byte-identical default board)", () => {
    expect(boardLaneFor(sv("s1", "active"), false)).toBe("active");
    expect(boardLaneFor(sv("s1", "completed"), false)).toBe("completed");
    expect(boardLaneFor(sv("s1", "failed"), false)).toBe("failed");
    expect(boardLaneFor(sv("s1", "cancelled"), false)).toBe("failed");
  });

  it("assignBoardLanes buckets queued separately from active", () => {
    const lanes = assignBoardLanes(
      [sv("s-q", "queued"), sv("s-a", "active"), sv("s-c", "completed")],
      {} as Record<string, Review>,
    );
    expect(lanes.queued.map((s) => s.id)).toEqual(["s-q"]);
    expect(lanes.active.map((s) => s.id)).toEqual(["s-a"]);
    expect(lanes.completed.map((s) => s.id)).toEqual(["s-c"]);
    expect(lanes.failed).toEqual([]);
    expect(lanes.review).toEqual([]);
  });

  it("visibleLanes: empty queue → EXACTLY today's four lanes", () => {
    const lanes = assignBoardLanes([sv("s-a", "active")], {});
    expect(visibleLanes(lanes)).toBe(LANES);
  });

  it("visibleLanes: an occupied queue prepends Queued BEFORE Active", () => {
    const lanes = assignBoardLanes([sv("s-q", "queued")], {});
    const ids = visibleLanes(lanes).map((l) => l.id as string);
    expect(ids).toEqual(["queued", "active", "review", "completed", "failed"]);
    expect(QUEUED_LANE.title).toBe("Queued");
  });
});

/* --------------------------------------- session detail page: live stream */

/** React's `use()` reads an instrumented thenable's value synchronously, so
 *  the page renders without a Suspense round-trip in jsdom. */
function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function setDetail(id: string, status: string) {
  pageApi.detail = {
    session: sv(id, status),
    transcript: { runs: [], tools: [] },
  };
}

describe("session detail live stream (B2)", () => {
  it("StrictMode remount does NOT strand the stream behind the once-guard (review defect 1)", async () => {
    // The bug: mount started the stream and set the guard ref; StrictMode's
    // simulated unmount closed the socket WITHOUT flipping `active`; the
    // remounted effect was then blocked by the still-true ref — a permanent
    // "Live run" card over no stream, and the stream-end reload never fired.
    // The fix's cleanup must release the guard AND call stop(), so the
    // remount cycle is exactly start → stop → start.
    setDetail("s-live", "active");
    render(
      <StrictMode>
        <SessionDetailPage params={fakeParams("s-live")} />
      </StrictMode>,
    );
    await act(async () => {});
    expect(stream.calls).toEqual(["start:s-live", "stop", "start:s-live"]);
  });

  it("a queued session never opens the stream", async () => {
    setDetail("s-q", "queued");
    render(
      <StrictMode>
        <SessionDetailPage params={fakeParams("s-q")} />
      </StrictMode>,
    );
    await act(async () => {});
    expect(stream.calls.filter((c) => c.startsWith("start:"))).toEqual([]);
  });

  it("the queued→active flip starts the stream (guard reset by the cleanup)", async () => {
    setDetail("s-flip", "queued");
    const r = render(<SessionDetailPage params={fakeParams("s-flip")} />);
    await act(async () => {});
    expect(stream.calls.filter((c) => c.startsWith("start:"))).toEqual([]);

    setDetail("s-flip", "active");
    r.rerender(<SessionDetailPage params={fakeParams("s-flip")} />);
    await act(async () => {});
    expect(stream.calls.filter((c) => c.startsWith("start:"))).toEqual([
      "start:s-flip",
    ]);
  });

  it("live output follows the newest tokens while pinned to the bottom (review defect 2)", async () => {
    setDetail("s-live", "active");
    stream.active = true;
    stream.text = "line 1\n";
    const r = render(<SessionDetailPage params={fakeParams("s-live")} />);
    const pre = await screen.findByTestId("live-run-text");
    // jsdom has no layout — give the box real-looking metrics.
    Object.defineProperty(pre, "scrollHeight", {
      configurable: true,
      get: () => 1000,
    });
    Object.defineProperty(pre, "clientHeight", {
      configurable: true,
      get: () => 100,
    });

    stream.text = "line 1\nline 2\n";
    r.rerender(<SessionDetailPage params={fakeParams("s-live")} />);
    await act(async () => {});
    expect(pre.scrollTop).toBe(1000);
  });

  it("a reader who scrolled up is NOT yanked back down; returning to the bottom re-pins", async () => {
    setDetail("s-live", "active");
    stream.active = true;
    stream.text = "line 1\n";
    const r = render(<SessionDetailPage params={fakeParams("s-live")} />);
    const pre = await screen.findByTestId("live-run-text");
    Object.defineProperty(pre, "scrollHeight", {
      configurable: true,
      get: () => 1000,
    });
    Object.defineProperty(pre, "clientHeight", {
      configurable: true,
      get: () => 100,
    });

    // Scroll to the top (re-reading earlier output) → unpinned.
    pre.scrollTop = 0;
    fireEvent.scroll(pre);
    stream.text = "line 1\nline 2\n";
    r.rerender(<SessionDetailPage params={fakeParams("s-live")} />);
    await act(async () => {});
    expect(pre.scrollTop).toBe(0);

    // Return to (near) the bottom → pinned again, new text follows.
    pre.scrollTop = 950;
    fireEvent.scroll(pre);
    stream.text = "line 1\nline 2\nline 3\n";
    r.rerender(<SessionDetailPage params={fakeParams("s-live")} />);
    await act(async () => {});
    expect(pre.scrollTop).toBe(1000);
  });
});
