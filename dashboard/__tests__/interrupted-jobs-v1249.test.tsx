/**
 * v1.249.0 (R-02) — work a restart cut off is offered back, where the user is.
 *
 * An update (or a crash) ends every running job and the boot reconcile marks
 * them FAILED. Until now that was the end of it: nothing said so, nothing
 * offered to pick the work up, and Continue existed only on the session's own
 * page — which the user had to know to go looking for.
 *
 * ONE source, TWO surfaces, and they must never disagree about what is
 * offered: the bell's row and the Overview's note read the same polled list
 * and post the same two actions.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const hooks = { api: {} as Record<string, unknown>, errors: {} as Record<string, unknown>, reloads: 0 };
const apiState = { posts: [] as { path: string; body: unknown }[], fail: null as null | number };

vi.mock("@/lib/useApi", () => ({
  useApi: (p: string | null) => ({
    data: p ? (hooks.api[p] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (p: string | null) => ({
    data: p ? (hooks.api[p] ?? null) : null,
    error: (p && hooks.errors[p]) || null,
    loading: false,
    reload: () => {
      hooks.reloads += 1;
    },
  }),
}));

// The class lives INSIDE the factory: vi.mock is hoisted above every top-level
// declaration, so a class defined out here is still in its TDZ when the
// component imports the module.
vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError: FakeApiError,
    get: () => Promise.resolve({}),
    post: async (p: string, body: unknown) => {
      apiState.posts.push({ path: p, body });
      if (apiState.fail !== null) throw new FakeApiError("session is gone", apiState.fail);
      return { ok: true };
    },
  };
});

import {
  CONTINUE_AFTER_RESTART,
  INTERRUPTED_PATH,
  InterruptedJobRow,
  InterruptedJobsNote,
  parseInterrupted,
} from "@/components/InterruptedJobs";

function readSrc(rel: string): string {
  return readFileSync(path.join(__dirname, "..", rel), "utf8").replace(/\r\n/g, "\n");
}

const JOB = {
  id: "sess_aaaaaaaa",
  task: "Rename the Q1 client files",
  agent_type: "coder",
  interrupted_at: "2026-09-11T03:29:00Z",
};

function serve(sessions: unknown[]) {
  hooks.api[INTERRUPTED_PATH] = { sessions };
}

beforeEach(() => {
  hooks.api = {};
  hooks.errors = {};
  hooks.reloads = 0;
  apiState.posts = [];
  apiState.fail = null;
});
afterEach(() => cleanup());

describe("what a restart left behind", () => {
  it("reads a row, and refuses one it cannot act on", () => {
    const ok = parseInterrupted(JOB);
    expect(ok?.id).toBe("sess_aaaaaaaa");
    expect(ok?.task).toBe("Rename the Q1 client files");
    // No id = nothing to continue or dismiss: never render a dead offer.
    expect(parseInterrupted({ task: "x" })).toBeNull();
    expect(parseInterrupted(null)).toBeNull();
    // A job with no description still gets a row the user can act on.
    expect(parseInterrupted({ id: "s1", task: "   " })?.task).toBe("(no description)");
  });

  it("says what happened in plain words, not 'FAILED'", () => {
    serve([JOB]);
    render(<InterruptedJobsNote />);
    expect(screen.getByTestId("interrupted-jobs-note")).toBeInTheDocument();
    expect(screen.getByText("1 job stopped when Iron Jarvis restarted")).toBeInTheDocument();
    expect(screen.getByText("Rename the Q1 client files")).toBeInTheDocument();
  });

  it("renders NOTHING when there is nothing to pick up", () => {
    serve([]);
    const { container } = render(<InterruptedJobsNote />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows three jobs and counts the rest", () => {
    serve([1, 2, 3, 4, 5].map((n) => ({ ...JOB, id: `sess_${n}`, task: `job ${n}` })));
    render(<InterruptedJobsNote />);
    expect(screen.getByText("5 jobs stopped when Iron Jarvis restarted")).toBeInTheDocument();
    expect(screen.getByText("job 3")).toBeInTheDocument();
    expect(screen.queryByText("job 4")).not.toBeInTheDocument();
    expect(screen.getByText("…and 2 more in the notification bell.")).toBeInTheDocument();
  });
});

describe("Continue picks the work up", () => {
  it("posts the session's own continue route, and says why", async () => {
    serve([JOB]);
    render(<InterruptedJobsNote />);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => {
      expect(apiState.posts).toHaveLength(1);
    });
    expect(apiState.posts[0].path).toBe("/sessions/sess_aaaaaaaa/continue");
    // An agent told only "continue" invents a reason; this one is told the truth.
    expect(apiState.posts[0].body).toEqual({ message: CONTINUE_AFTER_RESTART, wait: false });
    expect(CONTINUE_AFTER_RESTART).toMatch(/restarted/i);
    // The offer leaves on the click, rather than lingering until the next poll.
    await waitFor(() => {
      expect(screen.queryByTestId("interrupted-jobs-note")).not.toBeInTheDocument();
    });
  });

  it("Dismiss clears only the offer", async () => {
    serve([JOB]);
    render(<InterruptedJobsNote />);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => {
      expect(apiState.posts[0]?.path).toBe("/sessions/sess_aaaaaaaa/interrupted/dismiss");
    });
    await waitFor(() => {
      expect(screen.queryByTestId("interrupted-jobs-note")).not.toBeInTheDocument();
    });
  });

  it("keeps the buttons live when the daemon blips, and drops the row when the job is gone", async () => {
    serve([JOB]);
    apiState.fail = 503;
    render(<InterruptedJobsNote />);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    // A failed continue must not eat the offer — the work is still there.
    await waitFor(() => {
      expect(screen.getByText("session is gone")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Continue" })).not.toBeDisabled();

    cleanup();
    apiState.fail = 404; // the session was cleared elsewhere
    render(<InterruptedJobsNote />);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => {
      expect(screen.queryByTestId("interrupted-jobs-note")).not.toBeInTheDocument();
    });
  });

  it("a row in the bell offers the same two actions", async () => {
    const gone: string[] = [];
    render(
      <ul>
        <InterruptedJobRow
          job={{
            id: JOB.id,
            task: JOB.task,
            agentType: "coder",
            interruptedAt: JOB.interrupted_at,
          }}
          onGone={(id) => gone.push(id)}
        />
      </ul>,
    );
    expect(screen.getByTestId("bell-interrupted-job")).toBeInTheDocument();
    expect(screen.getByText("A job stopped when Iron Jarvis restarted")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(gone).toEqual([JOB.id]));
    expect(apiState.posts[0].path).toBe("/sessions/sess_aaaaaaaa/continue");
  });
});

describe("both surfaces are wired to the same source", () => {
  it("the bell counts, announces and renders the interrupted jobs", () => {
    const src = readSrc("components/NotificationBell.tsx");
    expect(src).toContain('from "@/components/InterruptedJobs"');
    expect(src).toContain("useInterruptedJobs(15000)");
    expect(src).toContain("interrupted.jobs.length");
    expect(src).toContain("<InterruptedJobRow");
    // The badge must count them, or the bell looks empty while work waits.
    const count = src.match(/const count =[\s\S]{0,220}?;/);
    expect(count?.[0]).toContain("interrupted.jobs.length");
    // And a poll failure must not be reported as "all clear".
    expect(src).toMatch(/interrupted\.error/);
    expect(src).toMatch(/stopped by a restart/);
  });

  it("the Overview renders the note where the user lands after a restart", () => {
    const src = readSrc("app/page.tsx");
    expect(src).toContain('from "@/components/InterruptedJobs"');
    expect(src).toContain("<InterruptedJobsNote />");
  });

  it("the page-side toast bridge no longer claims it works from the tray", () => {
    // R-03: hideToTray DESTROYS the renderer, so this component cannot speak
    // for a waiting job — the desktop app's own watcher does.
    const src = readSrc("components/DesktopNotifyBridge.tsx");
    expect(src).toMatch(/installAskWatcher/);
    expect(src).toMatch(/destroys the renderer/);
  });
});
