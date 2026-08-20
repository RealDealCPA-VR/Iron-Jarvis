import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * v1.192.0 — the Projects surface tells the truth, and a paused task can be
 * answered where the user is standing.
 *
 * P41: three components rendered "No sessions in this project yet" off a bare
 * `mine.length === 0`, while `usePolledApi`'s data is null until the FIRST
 * response lands (and, in ActivityList, while the daemon is offline — it never
 * destructured `error` at all). That is an assertion about the project made on
 * a guess; the codebase's own rule (workflows/page.tsx) is that loading or
 * errored means UNKNOWN, never "you have nothing yet".
 *
 * P15 (UI half): the runtime pauses an ask-tier tool call and publishes
 * `approval.requested` tagged with the session id, but the ONLY renderer was
 * the chat page, scoped to the one session chat itself was awaiting. With
 * Projects tasks now stamped `origin="project:<id>"`, an unrendered ask turns
 * an instant honest denial into a silent 300s wait ending in timeout-deny —
 * strictly worse. The Projects surface now renders the SAME ApprovalCard and
 * answers through the SAME POST /chat/approvals/{id} route.
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
    api.calls.push(path);
    const r = api.responses[path];
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    }
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r);
  },
  post: (path: string, body: unknown) => {
    api.posts.push({ path, body });
    return Promise.resolve({ ok: true });
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

const bus = vi.hoisted(() => ({ events: [] as unknown[] }));
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: bus.events, connected: true }),
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

// Heavy neighbours of the board/tasks surfaces — this file pins the honesty
// guards and the approval wiring, not the runner or the kanban internals.
vi.mock("@/components/project/ProjectTasks", () => ({
  ProjectTasks: () => <div data-testid="project-tasks-stub" />,
}));
vi.mock("@/components/project/ProjectSchedules", () => ({
  ProjectSchedules: () => null,
}));
vi.mock("@/components/kanban/KanbanBoard", () => ({
  KanbanBoard: () => <div data-testid="kanban-stub" />,
}));
vi.mock("@/lib/useReviews", () => ({
  useReviews: () => ({ reviews: {}, reload: () => {} }),
}));

import {
  ProjectApprovals,
  ProjectSurface,
} from "@/components/project/ProjectSurfaces";

afterEach(() => {
  cleanup();
  api.calls = [];
  api.posts = [];
  api.responses = {};
  bus.events = [];
});

const BOARD = "/sessions?project_id=proj_1";
const MEDIA = "/creative/items?project_id=proj_1&limit=200";

/** Shadow `document.hidden` with an own property for one test; returns the
 *  undo, which deletes the shadow and re-exposes jsdom's prototype getter. */
function hideDocument(): () => void {
  Object.defineProperty(document, "hidden", {
    configurable: true,
    get: () => true,
  });
  return () => {
    delete (document as unknown as Record<string, unknown>).hidden;
  };
}

function requested(
  id: string,
  sessionId: string,
  tool: string,
  args: Record<string, unknown> = {},
) {
  return {
    id: `ev_${id}`,
    type: "approval.requested",
    session_id: sessionId,
    ts: "2026-08-20T10:00:00Z",
    payload: { approval_id: id, tool, args, timeout_s: 300 },
  };
}

function resolvedEv(id: string, sessionId: string, tool: string) {
  return {
    id: `ev_res_${id}`,
    type: "approval.resolved",
    session_id: sessionId,
    ts: "2026-08-20T10:00:01Z",
    payload: { approval_id: id, tool, decision: "once" },
  };
}

/* ------------------------------------------ P41: the board's empty CLAIM */

describe("SurfaceBoard never asserts an empty project on a guess", () => {
  it("says nothing while the first /sessions response is still in flight", () => {
    // A forever-pending GET is exactly the window the defect lived in: data is
    // null, loading is true, and the old code read that as "no sessions".
    api.responses[BOARD] = new Promise(() => {});
    render(<ProjectSurface projectId="proj_1" hasRoot view="board" />);

    expect(
      screen.queryByText(/No sessions in this project yet/),
    ).toBeNull();
    // …and it does not invent the offline story either.
    expect(
      screen.queryByText(/the daemon looks offline/),
    ).toBeNull();
  });

  it("claims the project is empty only once the response says so", async () => {
    api.responses[BOARD] = { sessions: [] };
    render(<ProjectSurface projectId="proj_1" hasRoot view="board" />);

    expect(
      await screen.findByText(/No sessions in this project yet/),
    ).toBeInTheDocument();
  });

  it("renders the board when sessions land", async () => {
    api.responses[BOARD] = {
      sessions: [{ id: "sess_1", project_id: "proj_1", status: "completed" }],
    };
    render(<ProjectSurface projectId="proj_1" hasRoot view="board" />);

    expect(await screen.findByTestId("kanban-stub")).toBeInTheDocument();
    expect(screen.queryByText(/No sessions in this project yet/)).toBeNull();
  });

  it("an offline daemon still says offline, not empty", async () => {
    // BOARD unmocked → status 0.
    render(<ProjectSurface projectId="proj_1" hasRoot view="board" />);

    expect(
      await screen.findByText("Board unavailable — the daemon looks offline."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No sessions in this project yet/)).toBeNull();
  });

  it("a hidden tab does not invent an error that never happened", () => {
    // `useDocumentVisible` nulls the poll path while the tab is hidden, and
    // `useApi` then parks at data=null / error=null / loading=false. Gating
    // the error sentence on `!loading` alone made the board announce an HTTP
    // failure with no request behind it — swapping one fabricated assertion
    // ("you have nothing") for another ("the daemon errored").
    const restore = hideDocument();
    try {
      api.responses[BOARD] = new Promise(() => {});
      // No waiting: React flushes the visibility effect, the path-null rerun
      // and its setLoading(false) inside render()'s own act(), so this IS the
      // settled state — the old code paints the false sentence right here.
      const { container } = render(
        <ProjectSurface projectId="proj_1" hasRoot view="board" />,
      );

      expect(screen.queryByText(/returned an error/)).toBeNull();
      expect(screen.queryByText(/No sessions in this project yet/)).toBeNull();
      expect(screen.queryByText(/looks offline/)).toBeNull();
      // …and it is honestly UNKNOWN, i.e. still loading-shaped.
      expect(container.querySelectorAll(".skeleton").length).toBeGreaterThan(0);
    } finally {
      restore();
    }
  });
});

/* -------------------------------------- P41: the media surface's empty CLAIM */

describe("SurfaceMedia never asserts an empty project on a failed request", () => {
  it("reports the HTTP error instead of claiming no media", async () => {
    api.responses[MEDIA] = new api.FakeApiError("boom", 500);
    render(<ProjectSurface projectId="proj_1" hasRoot view="media" />);

    expect(
      await screen.findByText(
        /Media unavailable — the daemon returned an error \(HTTP 500\)/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/No media in this project yet/)).toBeNull();
  });

  it("claims the project has no media only once the response says so", async () => {
    api.responses[MEDIA] = { items: [] };
    render(<ProjectSurface projectId="proj_1" hasRoot view="media" />);

    expect(
      await screen.findByText(/No media in this project yet/),
    ).toBeInTheDocument();
  });
});

/* --------------------------------- P41: the page's two copies (source-pin) */

describe("the Projects page's own Board + Activity guards", () => {
  const page = readFileSync(
    join(process.cwd(), "app", "projects", "[id]", "page.tsx"),
    "utf8",
  );
  const body = (name: string) => {
    const start = page.indexOf(`function ${name}(`);
    expect(start).toBeGreaterThan(-1);
    return page.slice(start, start + 3000);
  };

  it("ProjectBoard consults the first response before claiming empty", () => {
    const src = body("ProjectBoard");
    expect(src).toMatch(/const \{ data, error, loading, reload \}/);
    // The unknown-guard must come BEFORE the empty claim, or the claim wins.
    expect(src.indexOf("if (!data)")).toBeGreaterThan(-1);
    expect(src.indexOf("if (!data)")).toBeLessThan(
      src.indexOf("if (mine.length === 0)"),
    );
  });

  it("ActivityList destructures error/loading and guards its empty state", () => {
    const src = body("ActivityList");
    // It never even looked at `error` — so it asserted "no sessions" offline.
    expect(src).toMatch(/const \{ data, error, loading \}/);
    expect(src.indexOf("{!data ?")).toBeGreaterThan(-1);
    expect(src.indexOf("{!data ?")).toBeLessThan(
      src.indexOf("sessions.length === 0 ?"),
    );
  });

  it("ProjectMedia guards its empty claim against a FAILED request", () => {
    const src = body("ProjectMedia");
    // `items` is `data?.items ?? []`, so after the loading branch and the
    // status-0 (offline) branch a 500/404 fell straight through to "No media in
    // this project yet" — the same false-empty assertion, on the media surface.
    expect(src).toMatch(/const \{ data, loading, error \}/);
    expect(src.indexOf(") : !data ? (")).toBeGreaterThan(-1);
    expect(src.indexOf(") : !data ? (")).toBeLessThan(
      src.indexOf("items.length === 0 ?"),
    );
    // …and it names an HTTP error only when there IS one.
    expect(src).toMatch(/returned an error \(HTTP \{error\.status\}\)/);
  });

  it("neither copy claims an error while `error` is null", () => {
    // A hidden tab nulls the poll path, so useApi parks at data=null /
    // error=null / loading=false. Both copies must fall back to the loading
    // shape there — `!loading` alone announced an HTTP failure with no request
    // behind it. jsdom cannot mount this page, so the gate is source-pinned.
    for (const name of ["ProjectBoard", "ActivityList"]) {
      const src = body(name);
      expect(src).toContain("loading || !error");
      // The old fabricating interpolation must be gone, in both copies.
      expect(src).not.toContain("${error ? ` (HTTP ${error.status})` : \"\"}");
      expect(src).not.toMatch(/error \? ` \(HTTP/);
    }
  });
});

/* ------------------------------------- P15 (UI half): the ask reaches here */

describe("a paused project task asks on the Projects surface", () => {
  it("renders the shared ApprovalCard and answers via POST /chat/approvals", async () => {
    api.responses["/sessions/sess_1"] = {
      session: { id: "sess_1", project_id: "proj_1" },
      transcript: [],
    };
    bus.events = [requested("apr_1", "sess_1", "shell", { command: "pytest -q" })];
    render(<ProjectApprovals projectId="proj_1" />);

    const card = await screen.findByTestId("chat-approval-card");
    expect(card).toBeInTheDocument();
    // The command is shown VERBATIM — approving what you cannot read is not a
    // decision (the card's own contract).
    expect(screen.getByText("pytest -q")).toBeInTheDocument();

    screen.getByText("Allow once").click();
    await waitFor(() =>
      expect(api.posts).toContainEqual({
        path: "/chat/approvals/apr_1",
        body: { decision: "once" },
      }),
    );
  });

  it("ignores a pause belonging to a DIFFERENT project", async () => {
    api.responses["/sessions/sess_other"] = {
      session: { id: "sess_other", project_id: "proj_2" },
    };
    api.responses["/sessions/sess_1"] = {
      session: { id: "sess_1", project_id: "proj_1" },
    };
    // Newest-first, as the socket delivers: the foreign ask is OLDER, so it is
    // decided first — this project's card appearing proves the foreign one was
    // already fully processed and deliberately dropped.
    bus.events = [
      requested("apr_mine", "sess_1", "repl"),
      requested("apr_foreign", "sess_other", "shell"),
    ];
    render(<ProjectApprovals projectId="proj_1" />);

    await screen.findByTestId("chat-approval-card");
    expect(screen.getAllByTestId("chat-approval-card")).toHaveLength(1);
    expect(screen.queryByText("shell")).toBeNull();
  });

  it("a resolution clears only its own card", async () => {
    api.responses["/sessions/sess_1"] = {
      session: { id: "sess_1", project_id: "proj_1" },
    };
    // Chronologically: apr_1 asked → apr_1 resolved → apr_2 asked.
    bus.events = [
      requested("apr_2", "sess_1", "repl"),
      resolvedEv("apr_1", "sess_1", "shell"),
      requested("apr_1", "sess_1", "shell"),
    ];
    render(<ProjectApprovals projectId="proj_1" />);

    await screen.findByTestId("chat-approval-card");
    expect(screen.getAllByTestId("chat-approval-card")).toHaveLength(1);
    expect(screen.queryByText("shell")).toBeNull();
  });

  it("survives an unrelated event landing during the membership lookup", async () => {
    // THE DROPPED-ASK BUG. `useEvents` does `setEvents(prev => [data, ...prev])`,
    // so EVERY frame from ANY session hands back a new array and re-runs this
    // effect. The id is stamped into `seen` BEFORE the awaited GET, so a scan
    // that bailed on its own cancellation left the approval seen-but-unrouted
    // and no later scan could recover it: no card, a silent 300s pause, and a
    // timeout-deny — strictly worse than the instant denial P15 replaced.
    let answer: (v: unknown) => void = () => {};
    api.responses["/sessions/sess_1"] = new Promise((res) => {
      answer = res;
    });
    bus.events = [requested("apr_1", "sess_1", "shell", { command: "pytest -q" })];
    const { rerender } = render(<ProjectApprovals projectId="proj_1" />);
    await waitFor(() => expect(api.calls).toContain("/sessions/sess_1"));

    // One unrelated frame arrives mid-lookup, from a different session.
    bus.events = [
      {
        id: "ev_noise",
        type: "tool.executed",
        session_id: "sess_other",
        ts: "2026-08-20T10:00:00Z",
        payload: { tool: "read_file", ok: true, mode: "auto" },
      },
      ...bus.events,
    ];
    rerender(<ProjectApprovals projectId="proj_1" />);

    // …and only THEN does the lookup answer: the session is in this project.
    await act(async () => {
      answer({ session: { id: "sess_1", project_id: "proj_1" }, transcript: [] });
    });

    expect(await screen.findByTestId("chat-approval-card")).toBeInTheDocument();
    expect(screen.getByText("pytest -q")).toBeInTheDocument();
  });

  it("an unreachable daemon shows NO card, and retries rather than caching a guess", async () => {
    // /sessions/sess_1 unmocked → the membership question is UNKNOWN. A card
    // here would claim another project's pause as this project's.
    // The negative rides an END SIGNAL, never a proxy: sess_ok's ask is NEWER,
    // so the batch (oldest first) decides sess_1 before it, and the awaits are
    // sequential — sess_ok's card on screen proves sess_1 was fully processed
    // and deliberately dropped, which "the GET was issued" would not.
    api.responses["/sessions/sess_ok"] = {
      session: { id: "sess_ok", project_id: "proj_1" },
    };
    bus.events = [
      requested("apr_ok", "sess_ok", "repl"),
      requested("apr_1", "sess_1", "shell"),
    ];
    const { rerender } = render(<ProjectApprovals projectId="proj_1" />);
    await screen.findByTestId("chat-approval-card");
    expect(screen.getAllByTestId("chat-approval-card")).toHaveLength(1);
    expect(screen.queryByText("shell")).toBeNull();

    // The daemon comes back; the very same approval must still be answerable —
    // a FAILED lookup must not have been cached as "not ours".
    api.responses["/sessions/sess_1"] = {
      session: { id: "sess_1", project_id: "proj_1" },
    };
    bus.events = [...bus.events];
    rerender(<ProjectApprovals projectId="proj_1" />);
    await waitFor(() =>
      expect(screen.getAllByTestId("chat-approval-card")).toHaveLength(2),
    );
    expect(screen.getByText("shell")).toBeInTheDocument();
  });

  it("a run superseded by a PROJECT SWITCH cannot poison the membership cache", async () => {
    // The membership verdict is computed from the closure's `projectId`, but
    // the cache write used to run BEFORE the staleness check. Switching
    // projects mid-lookup therefore wrote proj_1's answer into the map the
    // [projectId] effect had just cleared FOR PROJ_2 — and proj_2 then trusted
    // it. Here sess_x really belongs to proj_2, so the poisoned entry says
    // "not ours" about the very project it belongs to: its next ask is dropped,
    // the run waits out its 300s and times out into a deny — the exact outcome
    // rendering these cards exists to prevent.
    let answer: (v: unknown) => void = () => {};
    api.responses["/sessions/sess_x"] = new Promise((res) => {
      answer = res;
    });
    bus.events = [requested("apr_1", "sess_x", "shell")];
    const { rerender } = render(<ProjectApprovals projectId="proj_1" />);
    await waitFor(() => expect(api.calls).toContain("/sessions/sess_x"));

    // The user moves to proj_2 while proj_1's lookup is still in flight, and
    // the event buffer has rolled past that ask.
    bus.events = [];
    rerender(<ProjectApprovals projectId="proj_2" />);
    await act(async () => {
      answer({ session: { id: "sess_x", project_id: "proj_2" }, transcript: [] });
    });

    // The same session pauses again, now with proj_2 on screen.
    api.responses["/sessions/sess_x"] = {
      session: { id: "sess_x", project_id: "proj_2" },
    };
    bus.events = [requested("apr_2", "sess_x", "repl", { code: "1+1" })];
    rerender(<ProjectApprovals projectId="proj_2" />);

    expect(await screen.findByTestId("chat-approval-card")).toBeInTheDocument();
    expect(screen.getByText("repl")).toBeInTheDocument();
  });

  it("a run superseded by a PROJECT SWITCH cannot poison the SEEN set", async () => {
    // The other half of the same supersession, and the unrecoverable one.
    // `seen.current.add(aid)` is stamped BEFORE the awaited lookup, and the
    // [projectId] effect replaces `seen` with a fresh Set on a switch — so a
    // superseded run that keeps walking its batch stamps ids into the NEW
    // project's set, and the new project's own run then hits
    // `seen.current.has(aid)` and skips them FOREVER (unlike the member cache,
    // nothing re-derives it). Here apr_b belongs to proj_2, the project now on
    // screen: without the loop-top bail its card never renders, the task waits
    // out its 300s and times out into a deny.
    let answerA: (v: unknown) => void = () => {};
    let answerB: (v: unknown) => void = () => {};
    api.responses["/sessions/sess_a"] = new Promise((res) => {
      answerA = res;
    });
    api.responses["/sessions/sess_b"] = new Promise((res) => {
      answerB = res;
    });
    // Newest-first on the wire → the batch (oldest first) is [apr_a, apr_b],
    // so the proj_1 run is parked on sess_a's lookup with apr_b still ahead.
    bus.events = [
      requested("apr_b", "sess_b", "repl", { code: "1+1" }),
      requested("apr_a", "sess_a", "shell", { command: "pytest -q" }),
    ];
    const { rerender } = render(<ProjectApprovals projectId="proj_1" />);
    await waitFor(() => expect(api.calls).toContain("/sessions/sess_a"));

    // The user switches projects while sess_a's lookup is in flight.
    rerender(<ProjectApprovals projectId="proj_2" />);

    await act(async () => {
      answerA({ session: { id: "sess_a", project_id: "proj_1" } });
      answerB({ session: { id: "sess_b", project_id: "proj_2" } });
    });

    // sess_b belongs to the project on screen, so exactly its card is here.
    await waitFor(() =>
      expect(screen.getAllByTestId("chat-approval-card")).toHaveLength(1),
    );
    expect(screen.getByText("repl")).toBeInTheDocument();
    expect(screen.queryByText("shell")).toBeNull();
  });

  it("renders nothing at all when no task is paused", async () => {
    const { container } = render(<ProjectApprovals projectId="proj_1" />);
    await waitFor(() => expect(container.firstChild).toBeNull());
  });
});

/* ------------------------------------------------ the call sites (P15/P41) */

describe("the approval renderer is actually mounted", () => {
  it("the Projects workspace page mounts it above the tabs", () => {
    const page = readFileSync(
      join(process.cwd(), "app", "projects", "[id]", "page.tsx"),
      "utf8",
    );
    expect(page).toContain("<ProjectApprovals projectId={id} />");
    expect(page).toContain(
      'import { ProjectApprovals } from "@/components/project/ProjectSurfaces"',
    );
  });

  it("every in-chat project surface mounts it too", () => {
    const src = readFileSync(
      join(process.cwd(), "components", "project", "ProjectSurfaces.tsx"),
      "utf8",
    );
    // Inside ProjectSurface, not inside one view's branch — the run keeps
    // waiting whichever tab is open.
    const surface = src.slice(src.indexOf("export function ProjectSurface("));
    expect(surface).toContain("<ProjectApprovals projectId={projectId} />");
  });
});
