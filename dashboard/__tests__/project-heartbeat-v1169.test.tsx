import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

/**
 * v1.169.0 P1 — project heartbeat: the Tasks surface grows a Schedules card.
 *
 * What is guarded, each with a silent failure mode:
 *
 *  - `projectSchedules` keeps ONLY task-kind rows bound to THIS project — a
 *    filter bug shows another project's automation (a plausible-looking lie)
 *    or hides this project's own;
 *  - `scheduleProjectId` prefers the server-decoded field and falls back to
 *    parsing the payload blob; garbage/non-string values yield "" instead of
 *    crashing the surface;
 *  - the card tells the v1.119.0 outcome truth: ✓ ok / ✗ failed + detail /
 *    "not run yet", and `last_session_id` deep-links to the REAL session;
 *  - next run renders as the row's `next_run` local time (the Schedules
 *    page's formatting approach), never the raw ISO string, with the trigger
 *    HUMANIZED in the tooltip (preset cron → its friendly label);
 *  - a project with NO schedules renders NOTHING — a permanent empty card on
 *    every project is noise, not a heartbeat — but a FAILED list says so
 *    (offline vs daemon error): unknown must never masquerade as absent;
 *  - the Tasks surface actually MOUNTS the card (deleting the call site must
 *    fail a test, not just the unit specs).
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
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    }
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
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

// The Tasks surface's heavy neighbors — this file pins the schedules card and
// its WIRING into the surface, not the task runner / board internals.
vi.mock("@/components/project/ProjectTasks", () => ({
  ProjectTasks: () => <div data-testid="project-tasks-stub" />,
}));
vi.mock("@/components/kanban/KanbanBoard", () => ({
  KanbanBoard: () => null,
}));
vi.mock("@/lib/useReviews", () => ({
  useReviews: () => ({ reviews: {}, reload: () => {} }),
}));

import {
  ProjectSchedules,
  projectSchedules,
  scheduleProjectId,
  scheduleRepeatLabel,
  scheduleTask,
} from "@/components/project/ProjectSchedules";
import { ProjectSurface } from "@/components/project/ProjectSurfaces";
import type { Schedule } from "@/lib/types";

afterEach(() => {
  cleanup();
  api.calls = [];
  api.responses = {};
});

/* ---------------------------------------------------------------- fixtures */

const NEXT_RUN = "2026-08-14T09:00:00Z";

function sched(over: Partial<Schedule> = {}): Schedule {
  return {
    name: "weekly-taxes",
    cron: "0 9 * * 5",
    kind: "task",
    enabled: true,
    next_run: NEXT_RUN,
    last_run: null,
    trigger_type: "cron",
    payload_json: JSON.stringify({
      task: "Review open items.",
      project_id: "proj_1",
    }),
    project_id: "proj_1",
    last_status: "ok",
    last_detail: "session completed",
    last_session_id: "session_abc",
    ...over,
  } as Schedule;
}

/* ------------------------------------------------- scheduleProjectId (pure) */

describe("scheduleProjectId", () => {
  it("prefers the server-decoded project_id over the payload blob", () => {
    const s = sched({
      project_id: "proj_server",
      payload_json: JSON.stringify({ project_id: "proj_blob" }),
    });
    expect(scheduleProjectId(s)).toBe("proj_server");
  });

  it("falls back to parsing payload_json when the server field is absent", () => {
    const s = sched({ project_id: undefined });
    expect(scheduleProjectId(s)).toBe("proj_1");
  });

  it("unparseable payload yields '' — no claim, no crash", () => {
    const s = sched({ project_id: undefined, payload_json: "{not json" });
    expect(scheduleProjectId(s)).toBe("");
  });

  it("a non-string payload project_id yields '', never a coerced value", () => {
    const s = sched({
      project_id: undefined,
      payload_json: JSON.stringify({ project_id: 123 }),
    });
    expect(scheduleProjectId(s)).toBe("");
  });
});

/* -------------------------------------------------- projectSchedules (pure) */

describe("projectSchedules", () => {
  const rows = [
    sched(), // task, proj_1 — the one row that belongs
    sched({ name: "other-project", project_id: "proj_2", payload_json: "{}" }),
    sched({ name: "unbound", project_id: "", payload_json: "{}" }),
    sched({
      name: "wf-bound",
      kind: "workflow",
      payload_json: JSON.stringify({ workflow: "w", project_id: "proj_1" }),
    }),
  ];

  it("keeps only task-kind rows bound to THIS project", () => {
    expect(projectSchedules(rows, "proj_1").map((s) => s.name)).toEqual([
      "weekly-taxes",
    ]);
  });

  it("an empty projectId matches nothing (never 'all unbound rows')", () => {
    expect(projectSchedules(rows, "")).toEqual([]);
  });
});

/* ------------------------------------------------------ other pure helpers */

describe("scheduleTask / scheduleRepeatLabel", () => {
  it("reads the task text out of the payload", () => {
    expect(scheduleTask(sched())).toBe("Review open items.");
    expect(scheduleTask(sched({ payload_json: "{oops" }))).toBe("");
  });

  it("labels cron / interval / one-time triggers the Schedules-page way", () => {
    // Preset crons humanize to the Schedules page's exact labels…
    expect(scheduleRepeatLabel(sched({ cron: "0 16 * * 5" }))).toBe(
      "Weekly Fri 4pm",
    );
    expect(scheduleRepeatLabel(sched({ cron: "0 9 * * *" }))).toBe(
      "Daily at 9am",
    );
    // …and an unknown cron says so WITHOUT dropping the raw expression (this
    // label is the only place the trigger shows, unlike the page's two lines).
    expect(scheduleRepeatLabel(sched())).toBe("Custom cron · 0 9 * * 5");
    expect(
      scheduleRepeatLabel(
        sched({ trigger_type: "interval", cron: "", interval_seconds: 300 }),
      ),
    ).toBe("Every 300s");
    const runAt = "2026-08-20T15:00:00Z";
    expect(
      scheduleRepeatLabel(sched({ trigger_type: "date", cron: "", run_at: runAt })),
    ).toBe(`Once · ${new Date(runAt).toLocaleString()}`);
  });
});

/* ------------------------------------------------- ProjectSchedules render */

describe("ProjectSchedules", () => {
  it("renders this project's rows with name, task, ✓ ok, session link, next run", async () => {
    api.responses["/schedules"] = {
      schedules: [
        sched(),
        sched({ name: "other-project", project_id: "proj_2", payload_json: "{}" }),
      ],
    };
    render(<ProjectSchedules projectId="proj_1" />);

    expect(await screen.findByText("Schedules · 1")).toBeInTheDocument();
    expect(screen.getByText("weekly-taxes")).toBeInTheDocument();
    expect(screen.getByText("Review open items.")).toBeInTheDocument();
    expect(screen.getByText("✓ ok")).toBeInTheDocument();
    // Another project's automation must NOT leak in.
    expect(screen.queryByText("other-project")).toBeNull();
    // Deep link to the REAL session the last fire spawned.
    const link = screen.getByText(/open session/).closest("a");
    expect(link?.getAttribute("href")).toBe("/sessions/session_abc");
    // Next run is the LOCAL time of next_run, not the raw ISO string, and its
    // tooltip carries the HUMANIZED trigger (never the bare cron line).
    const nextRun = screen.getByText(new Date(NEXT_RUN).toLocaleString());
    expect(nextRun).toBeInTheDocument();
    expect(nextRun.closest("[title]")?.getAttribute("title")).toBe(
      "Custom cron · 0 9 * * 5",
    );
    expect(screen.queryByText(NEXT_RUN)).toBeNull();
    // The header's manage link points at the Schedules page.
    expect(screen.getByText("manage →").getAttribute("href")).toBe("/schedules");
  });

  it("a failed last fire shows ✗ failed with the detail", async () => {
    api.responses["/schedules"] = {
      schedules: [
        sched({ last_status: "error", last_detail: "provider unreachable" }),
      ],
    };
    render(<ProjectSchedules projectId="proj_1" />);
    expect(
      await screen.findByText("✗ failed — provider unreachable"),
    ).toBeInTheDocument();
    expect(screen.queryByText("✓ ok")).toBeNull();
  });

  it("a never-fired schedule says 'not run yet' and offers no session link", async () => {
    api.responses["/schedules"] = {
      schedules: [
        sched({ last_status: "", last_detail: "", last_session_id: "" }),
      ],
    };
    render(<ProjectSchedules projectId="proj_1" />);
    expect(await screen.findByText("not run yet")).toBeInTheDocument();
    expect(screen.queryByText(/open session/)).toBeNull();
  });

  it("renders NOTHING when the project has no schedules", async () => {
    api.responses["/schedules"] = {
      schedules: [
        sched({ name: "other-project", project_id: "proj_2", payload_json: "{}" }),
      ],
    };
    const { container } = render(<ProjectSchedules projectId="proj_1" />);
    await waitFor(() => expect(api.calls).toContain("/schedules"));
    expect(container.firstChild).toBeNull();
  });

  it("says so when the daemon is unreachable — never fakes 'no schedules'", async () => {
    // /schedules is unmocked → the fetch rejects with status 0. Schedules are
    // UNKNOWN here, not absent; vanishing would be indistinguishable from a
    // project with nothing scheduled (same hint idiom as SurfaceMedia).
    render(<ProjectSchedules projectId="proj_1" />);
    expect(
      await screen.findByText("Schedules unavailable — the daemon looks offline."),
    ).toBeInTheDocument();
  });

  it("a non-0 error (e.g. HTTP 500) shows an error hint, not an empty surface", async () => {
    api.responses["/schedules"] = new api.FakeApiError("boom", 500);
    render(<ProjectSchedules projectId="proj_1" />);
    expect(
      await screen.findByText(
        "Schedules unavailable — the daemon returned an error (HTTP 500).",
      ),
    ).toBeInTheDocument();
    // And it is honest about WHICH failure: not the offline wording.
    expect(
      screen.queryByText("Schedules unavailable — the daemon looks offline."),
    ).toBeNull();
  });
});

/* --------------------------------------------- Tasks-surface wiring (P1) */

describe("ProjectSurface tasks view", () => {
  it("mounts the schedules card under the task runner", async () => {
    api.responses["/projects/proj_1"] = { sessions: [] };
    api.responses["/schedules"] = { schedules: [sched()] };
    render(<ProjectSurface projectId="proj_1" hasRoot view="tasks" />);

    expect(await screen.findByTestId("project-tasks-stub")).toBeInTheDocument();
    expect(await screen.findByTestId("project-schedules")).toBeInTheDocument();
    expect(screen.getByText("weekly-taxes")).toBeInTheDocument();
  });

  it("with no schedules the tasks view shows only the task runner — no empty card", async () => {
    api.responses["/projects/proj_1"] = { sessions: [] };
    api.responses["/schedules"] = { schedules: [] };
    render(<ProjectSurface projectId="proj_1" hasRoot view="tasks" />);

    expect(await screen.findByTestId("project-tasks-stub")).toBeInTheDocument();
    await waitFor(() => expect(api.calls).toContain("/schedules"));
    expect(screen.queryByTestId("project-schedules")).toBeNull();
  });
});
