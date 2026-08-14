import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

/**
 * v1.174.0 P3 — the worklist panel: "N of M done", from the RECORD.
 *
 * THE FAILURE. A 26-file bulk job reported "FAILED — reached max steps before
 * completion" and nothing else, so the user could not tell whether it had
 * finished 0 files or 24. The daemon now keeps a durable per-item worklist;
 * this panel is the only place the user reads it.
 *
 * What is guarded, each with a silent failure mode:
 *
 *  - the counts come from `summary`, which the daemon computes over EVERY row.
 *    Deriving them from `items` instead would silently under-report the moment
 *    the server clips the list — the panel would say "6 of 6 done" over a job
 *    with 300 items.
 *  - FAILED items are shown FIRST and carry their notes. "3 failed" with no
 *    reason is exactly the missing information this wave exists to end.
 *  - an empty board renders NOTHING. Most sessions are not bulk jobs, and a
 *    permanent empty "Worklist" card is noise, not accountability.
 *  - "+N more" counts against the SUMMARY total, not the rows in hand: with a
 *    clipped response the row list is short, and subtracting from it would
 *    quietly claim the remainder is smaller than it is.
 *  - the panel is MOUNTED by the session page (a component nothing renders is
 *    a feature the user never sees), and it polls only while the run is live.
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
  API_BASE: "http://api.test",
  ijToken: () => "tok",
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

/* ---- Session detail page harness (mirrors session-files-v1168's) --------- */
const pageApi = vi.hoisted(() => ({
  detail: null as unknown,
  events: [] as unknown[],
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path && /^\/sessions\/[^/]+$/.test(path) ? pageApi.detail : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
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
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: pageApi.events }) }));
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

import {
  WorklistPanel,
  type WorklistResponse,
  type WorklistItemView,
} from "@/components/sessions/WorklistPanel";
import SessionDetailPage from "@/app/sessions/[id]/page";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  api.calls = [];
  api.responses = {};
  pageApi.detail = null;
  pageApi.events = [];
});

/* ---------------------------------------------------------------- fixtures */

const WS = "C:\\Users\\VR\\Downloads\\Test Folder";

function item(over: Partial<WorklistItemView> & { key: string }): WorklistItemView {
  return {
    id: `wl_${over.key}`,
    label: "",
    status: "pending",
    note: "",
    claimed_by: "",
    result_key: "",
    updated_at: "2026-08-14T10:00:00Z",
    ...over,
  };
}

/** The acceptance folder's shape: 26 items, 11 image-only scans. */
function board(over: Partial<WorklistResponse> = {}): WorklistResponse {
  return {
    board_id: "s-1",
    summary: {
      board_id: "s-1",
      total: 26,
      done: 12,
      failed: 2,
      pending: 10,
      doing: 2,
      remaining: 12,
      complete: false,
    },
    items: [
      item({ key: `${WS}\\CENTRUS W2.pdf`, status: "done" }),
      item({
        key: `${WS}\\IRS 1099-INT.pdf`,
        status: "failed",
        note: "image-only scan — no text layer",
      }),
      item({ key: `${WS}\\DOD CIV W2.pdf`, status: "failed", note: "password protected" }),
      item({ key: `${WS}\\WWC W2.pdf`, status: "doing", claimed_by: "run-2" }),
      item({ key: `${WS}\\Owner_1099_2025.pdf`, status: "pending" }),
    ],
    clipped: false,
    ...over,
  };
}

async function renderPanel(response: unknown, active = false) {
  if (response !== undefined) api.responses["/worklist/s-1"] = response;
  render(<WorklistPanel sessionId="s-1" active={active} />);
  await act(async () => {});
}

/* -------------------------------------------------------------- the panel */

describe("WorklistPanel", () => {
  it("states progress from the SUMMARY counts, not from the rows it holds", async () => {
    await renderPanel(board());
    // 12 of 26 — the response carries only 5 rows, one of them done. A panel
    // counting `items` would say "1 of 5".
    expect(screen.getByText(/Worklist · 12 of 26 done/)).toBeInTheDocument();
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("46"); // 12/26
    const badges = screen.getByTestId("worklist-badges").textContent ?? "";
    expect(badges).toContain("2 in progress");
    expect(badges).toContain("10 pending");
    expect(badges).toContain("2 failed");
    expect(badges).not.toContain("complete");
  });

  it("shows every failure WITH its reason — the scans are the story", async () => {
    await renderPanel(board());
    const failed = screen.getByTestId("worklist-failed").textContent ?? "";
    expect(failed).toContain("Failed · 2");
    expect(failed).toContain("IRS 1099-INT.pdf");
    expect(failed).toContain("image-only scan — no text layer");
    expect(failed).toContain("password protected");
  });

  it("lists what is still outstanding, marking the claimed one as in progress", async () => {
    await renderPanel(board());
    const pending = screen.getByTestId("worklist-pending").textContent ?? "";
    expect(pending).toContain("Still to do · 12");
    expect(pending).toContain("WWC W2.pdf");
    expect(pending).toContain("in progress");
    expect(pending).toContain("Owner_1099_2025.pdf");
    // Two rows shown, twelve outstanding — the remainder is counted against the
    // SUMMARY, so a clipped response cannot understate the backlog.
    expect(pending).toContain("… and 10 more");
  });

  it("renders NOTHING for a session that queued no work", async () => {
    await renderPanel({
      board_id: "s-1",
      summary: {
        board_id: "s-1",
        total: 0,
        done: 0,
        failed: 0,
        pending: 0,
        doing: 0,
        remaining: 0,
        complete: false,
      },
      items: [],
      clipped: false,
    });
    expect(screen.queryByText(/Worklist/)).toBeNull();
  });

  it("renders nothing when the endpoint is absent (an old daemon) instead of throwing", async () => {
    render(<WorklistPanel sessionId="s-1" active={false} />);
    await act(async () => {});
    expect(screen.queryByText(/Worklist/)).toBeNull();
  });

  it("says the list is clipped rather than letting the rows imply the total", async () => {
    await renderPanel(
      board({
        clipped: true,
        summary: {
          board_id: "s-1",
          total: 900,
          done: 300,
          failed: 0,
          pending: 600,
          doing: 0,
          remaining: 600,
          complete: false,
        },
      }),
    );
    expect(screen.getByText(/Showing 5 of 900 items/)).toBeInTheDocument();
    expect(screen.getByText(/Worklist · 300 of 900 done/)).toBeInTheDocument();
  });

  it("a finished job says so, with no outstanding section", async () => {
    await renderPanel({
      board_id: "s-1",
      summary: {
        board_id: "s-1",
        total: 26,
        done: 26,
        failed: 0,
        pending: 0,
        doing: 0,
        remaining: 0,
        complete: true,
      },
      items: [item({ key: `${WS}\\CENTRUS W2.pdf`, status: "done" })],
      clipped: false,
    });
    expect(screen.getByTestId("worklist-badges").textContent).toContain("complete");
    expect(screen.queryByTestId("worklist-pending")).toBeNull();
    expect(screen.queryByTestId("worklist-failed")).toBeNull();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("100");
  });

  it("polls while the run is live and stops once it is not", async () => {
    vi.useFakeTimers();
    api.responses["/worklist/s-1"] = board();
    const view = render(<WorklistPanel sessionId="s-1" active />);
    await act(async () => {});
    expect(api.calls.filter((c) => c === "/worklist/s-1")).toHaveLength(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(11000);
    });
    expect(api.calls.filter((c) => c === "/worklist/s-1").length).toBeGreaterThan(2);

    const after = api.calls.length;
    view.rerender(<WorklistPanel sessionId="s-1" active={false} />);
    await act(async () => {});
    api.calls = [];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(api.calls).toHaveLength(0);
    expect(after).toBeGreaterThan(0);
  });

  it("a later poll's counts REPLACE the earlier ones (progress must not stick)", async () => {
    vi.useFakeTimers();
    api.responses["/worklist/s-1"] = board();
    render(<WorklistPanel sessionId="s-1" active />);
    await act(async () => {});
    expect(screen.getByText(/12 of 26 done/)).toBeInTheDocument();

    api.responses["/worklist/s-1"] = board({
      summary: {
        board_id: "s-1",
        total: 26,
        done: 24,
        failed: 2,
        pending: 0,
        doing: 0,
        remaining: 0,
        complete: true,
      },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(screen.getByText(/24 of 26 done/)).toBeInTheDocument();
    expect(screen.queryByText(/12 of 26 done/)).toBeNull();
  });

  it("never renders one session's worklist under another session's heading", async () => {
    // The App Router REUSES this component across /sessions/A -> /sessions/B.
    // Without a reset, B renders A's "12 of 26 done" until B's fetch lands —
    // and B's fetch 404s on an install where the route is not mounted, so it
    // renders A's numbers indefinitely. Absent beats wrong.
    api.responses["/worklist/s-1"] = board();
    const view = render(<WorklistPanel sessionId="s-1" active={false} />);
    await act(async () => {});
    expect(screen.getByText(/Worklist · 12 of 26 done/)).toBeInTheDocument();

    view.rerender(<WorklistPanel sessionId="s-2" active={false} />);
    // SYNCHRONOUSLY, before s-2's fetch has resolved: nothing of s-1 is shown.
    expect(screen.queryByText(/12 of 26 done/)).toBeNull();
    await act(async () => {});
    expect(screen.queryByText(/Worklist/)).toBeNull();
    expect(api.calls).toContain("/worklist/s-2");
  });

  it("a failed refresh CLEARS the board instead of freezing the last good one", async () => {
    vi.useFakeTimers();
    api.responses["/worklist/s-1"] = board();
    render(<WorklistPanel sessionId="s-1" active />);
    await act(async () => {});
    expect(screen.getByText(/12 of 26 done/)).toBeInTheDocument();

    delete api.responses["/worklist/s-1"]; // the daemon stops answering
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(screen.queryByText(/12 of 26 done/)).toBeNull();
  });

  it("keeps the board when only `active` flips (a finished job still reports)", async () => {
    api.responses["/worklist/s-1"] = board();
    const view = render(<WorklistPanel sessionId="s-1" active />);
    await act(async () => {});
    expect(screen.getByText(/12 of 26 done/)).toBeInTheDocument();
    // The run ends -> active goes false. Clearing on that would blank the one
    // panel that says what a failed run actually finished.
    view.rerender(<WorklistPanel sessionId="s-1" active={false} />);
    expect(screen.getByText(/12 of 26 done/)).toBeInTheDocument();
  });
});

/* --------------------------------------------- session detail page wiring */

function fakeParams(id: string): Promise<{ id: string }> {
  const p = Promise.resolve({ id });
  Object.assign(p as object, { status: "fulfilled", value: { id } });
  return p;
}

function setDetail(id: string, over: Record<string, unknown> = {}) {
  pageApi.detail = {
    session: {
      id,
      task: "Rename all files in this folder to a name that is more appropriate",
      agent_type: "supervisor",
      provider: "anthropic",
      model: "claude",
      status: "failed",
      workspace_path: WS,
      summary: "stopped: reached max steps before completion",
      origin: null,
      created_at: "2026-08-14T10:00:00Z",
      finished_at: "2026-08-14T10:20:00Z",
      ...over,
    },
    transcript: { runs: [], tools: [] },
  };
}

describe("session detail page wiring", () => {
  it("MOUNTS the worklist panel, so a failed bulk run still reports what it finished", async () => {
    setDetail("s-1");
    api.responses["/worklist/s-1"] = board();
    render(<SessionDetailPage params={fakeParams("s-1")} />);

    // The session says "reached max steps"; the panel says what got done.
    expect(await screen.findByText(/Worklist · 12 of 26 done/)).toBeInTheDocument();
    expect(screen.getByTestId("worklist-failed").textContent).toContain(
      "image-only scan",
    );
  });

  it("a session with no worklist adds no panel to the page", async () => {
    setDetail("s-1");
    render(<SessionDetailPage params={fakeParams("s-1")} />);
    await act(async () => {});
    expect(screen.queryByText(/Worklist/)).toBeNull();
    // and the rest of the page still rendered
    expect(screen.getByText("Run controls")).toBeInTheDocument();
  });
});
