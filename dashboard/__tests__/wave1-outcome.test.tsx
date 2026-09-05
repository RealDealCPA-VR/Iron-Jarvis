/**
 * v1.227.0 wave 1 (A5 / U1) — a finished job that fell short does not wear
 * green, and the failed items can be re-run.
 *
 * Converted from __audit_20260904__/bell-and-result-card.audit.test.tsx D4,
 * RED at v1.226.0: the live GET /sessions/session_7e5621fb449b/result read
 * status "completed", rename_real_file 24 used / 24 failed, no files changed
 * — every mutating call had expired unanswered — and RunResultCard headlined
 * "Task complete". The usability drive found the same green on four surfaces
 * (session header, sessions list, kanban card, ProjectTasks recent runs).
 *
 * The daemon now carries `outcome` (completed | completed_with_failures |
 * needs_you) on every session row and `outcome` + `unanswered_asks` on
 * /result, and the worklist can flip its failed rows back to todo
 * (POST /sessions/{id}/worklist/reset-failed → {reset: N}). This pins:
 *
 *  - the headline words, from the CONTRACT fields (a plain completed run
 *    still reads "Task complete" — the change is not "everything is amber");
 *  - the ONE chip renderer the four surfaces share, on two of them end to end
 *    (the sessions list and ProjectTasks; kanban + the session header are in
 *    wave1-waiting);
 *  - the re-run flow: reset-failed FIRST, then /continue with exactly the
 *    message the runtime is told, then the result shown — and the button is
 *    absent while the run is live (re-opening rows under a running claim
 *    would race it).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    responses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
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
    const r = api.postResponses[path];
    if (r instanceof Error) return Promise.reject(r);
    return Promise.resolve(r ?? {});
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));
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
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/components/NewSessionForm", () => ({ NewSessionForm: () => null }));
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (prev: string, chunk: string) => prev + chunk,
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

import { RunResultCard, resultHeadline, type RunResult } from "@/components/chat/RunResultCard";
import {
  SessionStatusBadge,
  outcomeLabel,
  waitingLabel,
} from "@/components/sessions/SessionStatusBadge";
import { WorklistPanel, type WorklistResponse } from "@/components/sessions/WorklistPanel";
import { ProjectTasks } from "@/components/project/ProjectTasks";
import SessionsPage from "@/app/sessions/page";
import type { SessionView } from "@/lib/types";

beforeEach(() => {
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  api.gets = [];
  api.posts = [];
  api.responses = {};
  api.postResponses = {};
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
    finished_at: "2026-09-04T11:00:00Z",
    ...over,
  };
}

/** The live /result of session_7e56 (2026-09-04) plus the wave-1 fields. */
function result(over: Partial<RunResult> = {}): RunResult {
  return {
    found: true,
    session_id: "session_7e5621fb449b",
    status: "completed",
    task: "look at all the files in this folder and rename to the correct names",
    summary: "**Task incomplete — 3 of 28 files renamed, 25 failed pending your approval.**",
    steps: 46,
    tools_used: [
      { tool: "read_document", count: 19 },
      { tool: "rename_real_file", count: 24 },
      { tool: "worklist_done", count: 28 },
    ],
    tools_failed: [{ tool: "rename_real_file", count: 24 }],
    files_created: [],
    files_changed: [],
    errors: [{ tool: "rename_real_file", error: "the approval request timed out with no answer" }],
    revertable: 0,
    duration_s: 2407,
    ...over,
  };
}

/* --------------------------------------------------------- the chip words */

describe("outcomeLabel / waitingLabel", () => {
  it("names the two short outcomes and stays quiet for an earned green", () => {
    expect(outcomeLabel({ status: "completed", outcome: "needs_you" })).toBe(
      "Completed · needs you",
    );
    expect(outcomeLabel({ status: "completed", outcome: "completed_with_failures" })).toBe(
      "Completed · with failures",
    );
    expect(outcomeLabel({ status: "completed", outcome: "completed" })).toBeNull();
    expect(outcomeLabel({ status: "completed" })).toBeNull(); // an older row
    expect(outcomeLabel({ status: "completed", outcome: null })).toBeNull();
    // Red already tells the truth about a failed run.
    expect(outcomeLabel({ status: "failed", outcome: "needs_you" })).toBeNull();
  });

  it("names the tool a paused run is waiting on", () => {
    expect(
      waitingLabel({ status: "active", waiting_on: { approval_id: "a1", tool: "shell" } }),
    ).toBe("Waiting for you · shell");
    expect(waitingLabel({ status: "active", waiting_on: null })).toBeNull();
    expect(waitingLabel({ status: "active" })).toBeNull();
  });

  it("SessionStatusBadge falls back to the plain status badge", () => {
    render(<SessionStatusBadge session={{ status: "completed", outcome: "completed" }} />);
    expect(screen.queryByTestId("session-outcome-chip")).not.toBeInTheDocument();
    expect(screen.getByText("completed")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------- RunResultCard (D4) */

describe("RunResultCard — the headline tells the truth about a short run", () => {
  it("a COMPLETED session whose every mutating call expired does not headline 'Task complete'", async () => {
    render(<RunResultCard result={result({ outcome: "needs_you", unanswered_asks: 24 })} />);
    await waitFor(() =>
      expect(screen.getByTestId("run-result-headline")).toHaveTextContent(
        "Finished — 24 calls were never approved",
      ),
    );
    expect(screen.queryByText("Task complete"), "headline says the job was done").toBeNull();
    // And says what is left for the user.
    expect(screen.getByText(/asks expired unanswered/)).toBeInTheDocument();
  });

  it("'Finished with failures' for completed_with_failures", () => {
    render(<RunResultCard result={result({ outcome: "completed_with_failures" })} />);
    expect(screen.getByTestId("run-result-headline")).toHaveTextContent("Finished with failures");
    expect(screen.queryByText("Task complete")).toBeNull();
  });

  it("a plain completed run still reads 'Task complete' — honesty is not blanket amber", () => {
    render(
      <RunResultCard
        result={result({
          outcome: "completed",
          unanswered_asks: 0,
          tools_failed: [],
          errors: [],
        })}
      />,
    );
    expect(screen.getByTestId("run-result-headline")).toHaveTextContent("Task complete");
  });

  it("resultHeadline: singular ask, failed status, and the nothing-ran case keep their words", () => {
    expect(resultHeadline(result({ unanswered_asks: 1 }))).toBe(
      "Finished — 1 call was never approved",
    );
    expect(resultHeadline(result({ status: "failed", unanswered_asks: 3 }))).toBe("Task failed");
    expect(resultHeadline(result({ outcome: "needs_you" }))).toBe("Finished — needs you");
    expect(resultHeadline(result({ tools_used: [], tools_failed: [], errors: [] }))).toBe(
      "Finished — but nothing ran",
    );
  });
});

/* -------------------------------------------------- the list + ProjectTasks */

describe("the sessions list and ProjectTasks wear the outcome chip", () => {
  it("sessions list: amber 'Completed · with failures' instead of green", () => {
    api.responses["/sessions"] = {
      sessions: [
        sv("s-short", "completed", { outcome: "completed_with_failures" }),
        sv("s-ok", "completed", { outcome: "completed" }),
      ],
    };
    api.responses["/projects"] = { projects: [] };
    render(<SessionsPage />);
    const chips = screen.getAllByTestId("session-outcome-chip");
    expect(chips).toHaveLength(1);
    expect(chips[0]).toHaveTextContent("Completed · with failures");
  });

  it("ProjectTasks recent runs: amber 'Completed · needs you'", () => {
    render(
      <ProjectTasks
        projectId="proj_1"
        hasRoot={false}
        sessions={[
          sv("s-short", "completed", { outcome: "needs_you", project_id: "proj_1" }),
          sv("s-ok", "completed", { outcome: "completed", project_id: "proj_1" }),
        ]}
      />,
    );
    const chips = screen.getAllByTestId("session-outcome-chip");
    expect(chips).toHaveLength(1);
    expect(chips[0]).toHaveTextContent("Completed · needs you");
  });
});

/* ------------------------------------------------- WorklistPanel: re-run */

function board(failed = 2): WorklistResponse {
  const items = [
    {
      id: "wl_a",
      key: "C:\\w\\IRS 1099-INT.pdf",
      label: "",
      status: "failed",
      note: "the approval request timed out with no answer",
      claimed_by: "",
      result_key: "",
      updated_at: "2026-09-04T10:00:00Z",
    },
    {
      id: "wl_b",
      key: "C:\\w\\DOD CIV W2.pdf",
      label: "",
      status: "failed",
      note: "the approval request timed out with no answer",
      claimed_by: "",
      result_key: "",
      updated_at: "2026-09-04T10:00:00Z",
    },
  ].slice(0, failed);
  return {
    board_id: "s-1",
    summary: {
      board_id: "s-1",
      total: 28,
      done: 28 - failed,
      failed,
      pending: 0,
      doing: 0,
      remaining: failed,
      complete: false,
    },
    items,
    clipped: false,
  };
}

describe("WorklistPanel — Re-run the N failed items", () => {
  it("resets the failed rows FIRST, then continues with exactly those items, and shows the result", async () => {
    api.responses["/worklist/s-1"] = board(2);
    api.postResponses["/sessions/s-1/worklist/reset-failed"] = { reset: 2 };
    api.postResponses["/sessions/s-1/continue"] = { id: "s-2", status: "active" };
    render(<WorklistPanel sessionId="s-1" active={false} />);
    await act(async () => {});

    const btn = await screen.findByRole("button", { name: /Re-run the 2 failed items/ });
    fireEvent.click(btn);

    await waitFor(() =>
      expect(screen.getByTestId("worklist-rerun-note")).toHaveTextContent(
        "Re-opened 2 items — a new run is continuing them.",
      ),
    );
    const paths = api.posts.map((p) => p.path);
    expect(paths.indexOf("/sessions/s-1/worklist/reset-failed")).toBe(0);
    expect(paths.indexOf("/sessions/s-1/continue")).toBe(1);
    const cont = api.posts[1].body as { message: string; wait: boolean };
    expect(cont.message).toBe("Continue with the 2 worklist items that were re-opened.");
    expect(cont.wait).toBe(false);
    expect(screen.getByRole("link", { name: /open the new run/ })).toHaveAttribute(
      "href",
      "/sessions/s-2",
    );
  });

  it("does not continue when nothing was re-opened", async () => {
    api.responses["/worklist/s-1"] = board(1);
    api.postResponses["/sessions/s-1/worklist/reset-failed"] = { reset: 0 };
    render(<WorklistPanel sessionId="s-1" active={false} />);
    await act(async () => {});
    fireEvent.click(await screen.findByRole("button", { name: /Re-run the 1 failed item$/ }));
    await waitFor(() =>
      expect(screen.getByTestId("worklist-rerun-note")).toHaveTextContent(/Nothing to re-run/),
    );
    expect(api.posts.map((p) => p.path)).toEqual(["/sessions/s-1/worklist/reset-failed"]);
  });

  it("names a 404 honestly and never reaches /continue", async () => {
    api.responses["/worklist/s-1"] = board(2);
    api.postResponses["/sessions/s-1/worklist/reset-failed"] = new api.FakeApiError(
      "no board",
      404,
    );
    render(<WorklistPanel sessionId="s-1" active={false} />);
    await act(async () => {});
    fireEvent.click(await screen.findByRole("button", { name: /Re-run the 2 failed items/ }));
    await waitFor(() =>
      expect(screen.getByTestId("worklist-rerun-error")).toHaveTextContent(
        "This session has no worklist to re-open.",
      ),
    );
    expect(api.posts.map((p) => p.path)).toEqual(["/sessions/s-1/worklist/reset-failed"]);
  });

  it("offers no re-run while the run is live, or when nothing failed", async () => {
    api.responses["/worklist/s-1"] = board(2);
    const live = render(<WorklistPanel sessionId="s-1" active />);
    await act(async () => {});
    expect(screen.queryByRole("button", { name: /Re-run the/ })).not.toBeInTheDocument();
    live.unmount();

    api.responses["/worklist/s-1"] = board(0);
    render(<WorklistPanel sessionId="s-1" active={false} />);
    await act(async () => {});
    expect(screen.queryByRole("button", { name: /Re-run the/ })).not.toBeInTheDocument();
  });
});
