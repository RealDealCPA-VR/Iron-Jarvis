/**
 * v1.296.0 — give an agent a job and the job waits for it.
 *
 * WHAT THESE TESTS GUARD — the WIRE and the honest degradations:
 *  - the inbox renders its health line and sections from GET /agents/{name}/inbox,
 *    says held/blocked reasons, and renders NOTHING on a 404 (older daemon);
 *  - the composer POSTs /assignments with assignee/task/priority/project/reason;
 *  - Cancel / Unblock / Retry POST /assignments/{id}/{action};
 *  - the job card's "Queue it" POSTs /assignments with payload.max_steps and
 *    says the sentence; it is absent for the Team default;
 *  - ProjectTasks with an assignee posts `assignee` and shows the queued
 *    sentence; WITHOUT one the body carries no `assignee` key at all;
 *  - the roster's idle pill + health caption, and the bell's three mappings
 *    (a successful finish is quiet).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  errors: {} as Record<string, { status: number; message: string }>,
  posts: [] as Array<{ path: string; body: Record<string, unknown> | undefined }>,
  postResult: {} as unknown,
  postResults: {} as Record<string, unknown>,
  events: [] as unknown[],
  reloads: 0,
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: path ? (hooks.errors[path] ?? null) : null,
    loading: false,
    reload: () => {
      hooks.reloads += 1;
    },
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: path ? (hooks.errors[path] ?? null) : null,
    loading: false,
    reload: () => {
      hooks.reloads += 1;
    },
  }),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: hooks.events, connected: true }),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "",
    ijToken: () => "",
    get: (path: string) =>
      Promise.resolve(hooks.api[path] ?? { session: { id: "s1", status: "active" } }),
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (path: string, body?: Record<string, unknown>) => {
      hooks.posts.push({ path, body });
      return Promise.resolve(
        path in hooks.postResults ? hooks.postResults[path] : hooks.postResult,
      );
    },
    patch: () => Promise.resolve({}),
  };
});

vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (prev: string, chunk: string) => prev + chunk,
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "whileHover"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => tagFor(String(tag)),
    }),
  };
});

import { AlertTriangle, OctagonAlert, RotateCcw } from "lucide-react";
import { AgentInbox, AssignmentRow, healthLine, rowActions } from "@/components/agents/AgentInbox";
import { canBeAssignee } from "@/components/agents/AgentInbox";
import { RosterStrip, healthCaption, isIdle, type RosterEntry } from "@/components/agents/RosterStrip";
import { ProjectTasks, assigneeOptions } from "@/components/project/ProjectTasks";
import { toActivity } from "@/components/NotificationBell";
import type { Assignment, IJEvent } from "@/lib/types";

const NOW = Date.now();
const ago = (ms: number) => new Date(NOW - ms).toISOString();

function row(over: Partial<Assignment>): Assignment {
  return {
    id: "a0",
    project_id: null,
    assignee: "custom:analyst",
    task: "Summarize the Q3 ledger",
    title: "Summarize the Q3 ledger",
    priority: 0,
    status: "queued",
    source: "user",
    reason: "",
    payload: {},
    idempotency_key: null,
    coalesced_count: 0,
    attempts: 0,
    failure_count: 0,
    blocked_reason: null,
    held_reason: null,
    depth: 0,
    session_id: null,
    last_error: null,
    created_at: ago(60_000),
    claimed_at: null,
    started_at: null,
    finished_at: null,
    updated_at: ago(60_000),
    ...over,
  };
}

const INBOX_PATH = "/agents/custom%3Aanalyst/inbox";

function inboxPayload() {
  return {
    assignee: "custom:analyst",
    inbox: {
      queued: [row({ id: "q1", priority: 5, held_reason: "analyst is paused" })],
      claimed: [],
      running: [row({ id: "r1", status: "running", session_id: "sess-r1" })],
      blocked: [row({ id: "b1", status: "blocked", blocked_reason: "needs a file you have" })],
      recent: [
        row({ id: "f1", status: "failed", last_error: "boom", session_id: "sess-f1" }),
        row({ id: "d1", status: "done", session_id: "sess-d1" }),
      ],
    },
    health: {
      last_run_at: ago(12 * 60_000),
      last_outcome: "completed",
      last_error: null,
      last_wake_at: ago(5_000),
      queued: 1,
      running: 1,
      blocked: 1,
    },
  };
}

beforeEach(() => {
  // v1.307.0: the bare /agents is the MISSION screen; this file pins the
  // TEAM screens, which live at ?view=team.
  window.history.replaceState(null, "", "/agents?view=team");
  for (const k of Object.keys(hooks.api)) delete hooks.api[k];
  for (const k of Object.keys(hooks.errors)) delete hooks.errors[k];
  for (const k of Object.keys(hooks.postResults)) delete hooks.postResults[k];
  hooks.posts.length = 0;
  hooks.postResult = {};
  hooks.events = [];
  hooks.reloads = 0;
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
});

/* ---------------------------------------------------------------- inbox --- */

describe("AgentInbox", () => {
  it("renders the health line and the four sections from GET /agents/{name}/inbox", () => {
    hooks.api[INBOX_PATH] = inboxPayload();
    render(<AgentInbox name="custom:analyst" />);
    const health = screen.getByTestId("inbox-health-custom:analyst");
    expect(health.textContent).toMatch(/Last ran 12m ago — completed/);
    expect(health.getAttribute("data-tone")).toBe("green");
    expect(screen.getByTestId("inbox-running-r1")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-queued-q1")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-blocked-b1")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-failed-f1")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-done-d1")).toBeInTheDocument();
    // Links to the sessions that exist.
    const running = screen.getByTestId("inbox-running-r1");
    expect(within(running).getByRole("link").getAttribute("href")).toBe("/sessions/sess-r1");
    // Priority badge on the queued row, held reason in amber, blocked reason.
    expect(screen.getByTestId("inbox-priority-q1").textContent).toBe("high");
    expect(screen.getByTestId("inbox-held-q1").textContent).toBe("held: analyst is paused");
    expect(screen.getByTestId("inbox-blocked-reason-b1").textContent).toBe("needs a file you have");
    // The actions each status allows.
    expect(screen.getByTestId("inbox-cancel-q1")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-unblock-b1")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-cancel-b1")).toBeInTheDocument();
    expect(screen.getByTestId("inbox-retry-f1")).toBeInTheDocument();
    expect(screen.queryByTestId("inbox-retry-d1")).toBeNull();
    expect(screen.queryByTestId("inbox-cancel-d1")).toBeNull();
  });

  it("renders NOTHING on a 404 (older daemon) and nothing before the first answer", () => {
    // Anti-vacuity: the hook HOLDS a previous answer beside the 404 (useApi
    // keeps its data on an error), so only the 404 guard can hide the panel.
    hooks.api[INBOX_PATH] = inboxPayload();
    hooks.errors[INBOX_PATH] = { status: 404, message: "404 Not Found" };
    const { container } = render(<AgentInbox name="custom:analyst" />);
    expect(container.firstChild).toBeNull();
    expect(screen.queryByTestId("inbox-assign-custom:analyst")).toBeNull();
    cleanup();
    delete hooks.errors[INBOX_PATH];
    delete hooks.api[INBOX_PATH];
    const second = render(<AgentInbox name="custom:analyst" />);
    expect(second.container.firstChild).toBeNull();
  });

  it("the composer POSTs /assignments with assignee, task, priority, project and reason", async () => {
    hooks.api[INBOX_PATH] = inboxPayload();
    hooks.api["/projects"] = {
      projects: [
        { id: "p-z", name: "Zeta", status: "active" },
        { id: "p-a", name: "Alpha", status: "active" },
        { id: "p-x", name: "Gone", status: "archived" },
      ],
    };
    hooks.postResults["/assignments"] = { assignment: row({ id: "new" }), created: true };
    render(<AgentInbox name="custom:analyst" />);
    const form = screen.getByTestId("inbox-assign-custom:analyst");
    const project = within(form).getByLabelText("Project (optional)") as HTMLSelectElement;
    expect(Array.from(project.options).map((o) => o.textContent)).toEqual([
      "No project",
      "Alpha",
      "Zeta",
    ]);
    fireEvent.change(within(form).getByLabelText("Assign work"), {
      target: { value: "Reconcile the bank feed" },
    });
    fireEvent.change(within(form).getByLabelText("Reason (optional)"), {
      target: { value: "month end" },
    });
    fireEvent.change(within(form).getByLabelText("Priority"), { target: { value: "5" } });
    fireEvent.change(project, { target: { value: "p-a" } });
    fireEvent.click(within(form).getByRole("button", { name: /Queue for analyst/ }));
    // The success note is set at the END of the handler.
    await waitFor(() =>
      expect(screen.getByText("Queued for analyst — it runs when analyst is free.")).toBeInTheDocument(),
    );
    const call = hooks.posts.find((p) => p.path === "/assignments");
    expect(call?.body).toEqual({
      assignee: "custom:analyst",
      task: "Reconcile the bank feed",
      priority: 5,
      project_id: "p-a",
      reason: "month end",
    });
    expect(hooks.reloads).toBeGreaterThan(0);
  });

  it("Cancel / Unblock / Retry POST /assignments/{id}/{action} and ask for a refetch", async () => {
    hooks.api[INBOX_PATH] = inboxPayload();
    render(<AgentInbox name="custom:analyst" />);
    fireEvent.click(screen.getByTestId("inbox-cancel-q1"));
    await waitFor(() => expect(hooks.posts.map((p) => p.path)).toContain("/assignments/q1/cancel"));
    fireEvent.click(screen.getByTestId("inbox-unblock-b1"));
    await waitFor(() => expect(hooks.posts.map((p) => p.path)).toContain("/assignments/b1/unblock"));
    fireEvent.click(screen.getByTestId("inbox-retry-f1"));
    await waitFor(() => expect(hooks.posts.map((p) => p.path)).toContain("/assignments/f1/retry"));
    await waitFor(() => expect(hooks.reloads).toBe(3));
  });

  it("refetches on an assignment.* event for THIS assignee only (and on a requeue)", () => {
    hooks.api[INBOX_PATH] = inboxPayload();
    hooks.events = [
      { id: "e1", type: "assignment.started", session_id: null, ts: "", payload: { assignee: "builder" } },
    ];
    const { rerender } = render(<AgentInbox name="custom:analyst" />);
    expect(hooks.reloads).toBe(0);
    hooks.events = [
      { id: "e2", type: "assignment.finished", session_id: null, ts: "", payload: { assignee: "custom:analyst" } },
      ...hooks.events,
    ];
    rerender(<AgentInbox name="custom:analyst" />);
    expect(hooks.reloads).toBe(1);
    rerender(<AgentInbox name="custom:analyst" />);
    expect(hooks.reloads).toBe(1); // the same frame is not a second refetch
    hooks.events = [
      { id: "e3", type: "assignment.requeued", session_id: null, ts: "", payload: { count: 2, ids: [] } },
      ...hooks.events,
    ];
    rerender(<AgentInbox name="custom:analyst" />);
    expect(hooks.reloads).toBe(2);
  });

  it("healthLine: never ran / needs you / failed with the error", () => {
    expect(healthLine(null)).toEqual({ text: "never ran", tone: "slate" });
    expect(healthLine({ last_run_at: null, last_outcome: null, last_error: null, last_wake_at: null, queued: 0, running: 0, blocked: 0 }).tone).toBe("slate");
    expect(healthLine({ last_run_at: ago(3_600_000), last_outcome: "needs_you", last_error: null, last_wake_at: null, queued: 0, running: 0, blocked: 0 })).toEqual({ text: "Last ran 1h ago — needs you", tone: "amber" });
    expect(healthLine({ last_run_at: ago(30_000), last_outcome: "failed", last_error: "disk full", last_wake_at: null, queued: 0, running: 0, blocked: 0 })).toEqual({ text: "Last ran 30s ago — failed: disk full", tone: "red" });
    expect(rowActions("claimed")).toEqual({ cancel: true, unblock: false, retry: false });
    expect(rowActions("cancelled")).toEqual({ cancel: false, unblock: false, retry: true });
  });

  it("AssignmentRow with showAssignee names the agent on the row", () => {
    render(
      <ul>
        <AssignmentRow assignment={row({ id: "x1" })} onChanged={() => {}} showAssignee />
      </ul>,
    );
    expect(screen.getByTestId("inbox-queued-x1").textContent).toContain("analyst ·");
  });
});

/* -------------------------------------------------------------- job card --- */

const ROSTER: RosterEntry[] = [
  {
    name: "builder",
    kind: "builtin",
    description: "doer",
    delegable: true,
    healthy: true,
    stats: null,
  },
  {
    name: "remote:box",
    kind: "remote",
    description: "remote",
    delegable: true,
    healthy: true,
    stats: null,
  },
];


/* ---------------------------------------------------------- project tasks --- */

describe("ProjectTasks — Assign to", () => {
  const AGENTS = {
    builtin: ["builder", "planner", "supervisor", "guide"],
    dynamic: [{ name: "analyst", description: "" }],
  };

  it("assigneeOptions: builtins by name, custom agents as custom:<name>", () => {
    // planner and supervisor are coordinators (refused as assignees); builder
    // and guide are still offered — the exclusion is NOT "no builtins".
    expect(assigneeOptions(AGENTS).map((o) => o.value)).toEqual(["builder", "guide", "custom:analyst"]);
    expect(assigneeOptions({ builtin: ["supervisor"], dynamic: [] })).toEqual([]);
    expect(assigneeOptions(null)).toEqual([]);
  });

  it("with an assignee the button reads Queue for <name>, posts `assignee`, and shows the queued sentence", async () => {
    hooks.api["/agents"] = AGENTS;
    hooks.postResults["/projects/p1/task/plan"] = { tools: [] };
    hooks.postResults["/projects/p1/task"] = {
      assignment: row({ id: "pa1", assignee: "custom:analyst", project_id: "p1" }),
      queued: true,
    };
    render(<ProjectTasks projectId="p1" hasRoot sessions={[]} />);
    fireEvent.change(screen.getByTestId("project-task-assignee"), { target: { value: "custom:analyst" } });
    expect(screen.getByRole("button", { name: /Queue for analyst/ })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Task for an agent in this project"), {
      target: { value: "Reconcile the bank feed" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Queue for analyst/ }));
    await waitFor(() =>
      expect(screen.getByTestId("project-task-queued").textContent).toBe(
        "Queued for analyst — it runs when analyst is free.",
      ),
    );
    const call = hooks.posts.find((p) => p.path === "/projects/p1/task");
    expect(call?.body).toEqual({
      text: "Reconcile the bank feed",
      output: "chat",
      allow_tools: [],
      assignee: "custom:analyst",
    });
    // No session strip: there is no session yet.
    expect(screen.queryByText(/open session/)).toBeNull();
    expect(hooks.reloads).toBeGreaterThan(0);
  });

  it("WITHOUT an assignee the body is today's — no `assignee` key — and the run strip appears", async () => {
    hooks.api["/agents"] = AGENTS;
    hooks.postResults["/projects/p1/task/plan"] = { tools: [] };
    hooks.postResults["/projects/p1/task"] = {
      id: "s1",
      project_id: "p1",
      task: "x",
      status: "active",
      output: "chat",
      target_path: null,
      created_at: ago(1000),
    };
    render(<ProjectTasks projectId="p1" hasRoot sessions={[]} />);
    expect(screen.getByRole("button", { name: /^Run$/ })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Task for an agent in this project"), {
      target: { value: "Summarize the PDFs" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Run$/ }));
    await waitFor(() => expect(screen.getByText(/open session/)).toBeInTheDocument());
    const call = hooks.posts.find((p) => p.path === "/projects/p1/task");
    expect(call?.body).toEqual({ text: "Summarize the PDFs", output: "chat", allow_tools: [] });
    expect(call?.body).not.toHaveProperty("assignee");
    expect(screen.queryByTestId("project-task-queued")).toBeNull();
  });

  it("lists the project's assignments with the shared row, and nothing on a 404", () => {
    hooks.api["/assignments?project_id=p1"] = {
      assignments: [
        row({ id: "pa1", project_id: "p1", assignee: "builder", status: "blocked", blocked_reason: "needs a key" }),
      ],
    };
    render(<ProjectTasks projectId="p1" hasRoot sessions={[]} />);
    const list = screen.getByTestId("project-assignments");
    expect(within(list).getByTestId("inbox-blocked-pa1").textContent).toContain("builder ·");
    expect(within(list).getByTestId("inbox-blocked-reason-pa1").textContent).toBe("needs a key");
    expect(within(list).getByTestId("inbox-unblock-pa1")).toBeInTheDocument();
    cleanup();
    delete hooks.api["/assignments?project_id=p1"];
    hooks.errors["/assignments?project_id=p1"] = { status: 404, message: "404" };
    render(<ProjectTasks projectId="p1" hasRoot sessions={[]} />);
    expect(screen.queryByTestId("project-assignments")).toBeNull();
  });
});

/* --------------------------------------------------------------- roster --- */

describe("RosterStrip — idle pill + health caption", () => {
  const health = {
    last_run_at: ago(3 * 3_600_000),
    last_outcome: "completed",
    last_error: null,
    last_wake_at: null,
    queued: 2,
    running: 0,
    blocked: 1200,
  };

  it("healthCaption and isIdle", () => {
    expect(healthCaption(null)).toBeNull();
    expect(healthCaption(undefined)).toBeNull();
    expect(healthCaption(health)).toBe("last ran 3h ago — completed · 2 queued · 1.2k blocked");
    expect(healthCaption({ ...health, last_run_at: null, queued: 0, blocked: 0 })).toBe("never ran");
    expect(isIdle({ ...ROSTER[0], activity: "idle" })).toBe(true);
    expect(isIdle({ ...ROSTER[0], activity: "busy" })).toBe(false);
    expect(isIdle({ ...ROSTER[0], activity: "idle", paused: true, healthy: false })).toBe(false);
    expect(isIdle({ ...ROSTER[0] })).toBe(false);
  });

  it("an idle entry renders the zinc idle pill (its own testid) and the caption; no health → no caption", () => {
    render(<RosterStrip entries={[{ ...ROSTER[0], activity: "idle", health }]} />);
    const pill = screen.getByTestId("roster-idle-builder");
    expect(pill.textContent).toContain("idle");
    expect(pill.getAttribute("data-activity")).toBe("idle");
    expect(pill.className).toContain("text-zinc-400");
    expect(screen.queryByTestId("roster-activity-builder")).toBeNull();
    expect(screen.getByTestId("roster-health-builder").textContent).toBe(
      "last ran 3h ago — completed · 2 queued · 1.2k blocked",
    );
    cleanup();
    render(<RosterStrip entries={[{ ...ROSTER[0], activity: "busy" }]} />);
    expect(screen.queryByTestId("roster-idle-builder")).toBeNull();
    expect(screen.getByTestId("roster-activity-builder")).toBeInTheDocument();
    expect(screen.queryByTestId("roster-health-builder")).toBeNull();
  });
});

/* ----------------------------------------------------------------- bell --- */

describe("toActivity maps the assignment events", () => {
  const ev = (type: string, payload: Record<string, unknown>): IJEvent => ({
    id: "e1",
    type,
    session_id: null,
    ts: "2026-10-01T09:00:00Z",
    payload,
  });

  // v1.309.0: the agent's inbox lives in its detail on Your team; a requeue
  // names no agent, so it opens Your team itself.
  it("assignment.blocked → the assignee on Your team, OctagonAlert, the title and the reason", () => {
    expect(
      toActivity(ev("assignment.blocked", { id: "a1", assignee: "builder", title: "Rename files", blocked_reason: "needs a key" })),
    ).toMatchObject({
      href: "/agents?view=team&agent=builder",
      icon: OctagonAlert,
      title: "Blocked: Rename files",
      body: "builder — needs a key",
    });
  });

  it("assignment.finished: a failure rings (session link), a success is QUIET", () => {
    expect(
      toActivity(ev("assignment.finished", { id: "a1", assignee: "builder", title: "Rename files", ok: false, session_id: "s9", error: "disk full" })),
    ).toMatchObject({
      href: "/sessions/s9",
      icon: AlertTriangle,
      title: "Failed: Rename files",
      body: "builder: disk full",
    });
    expect(
      toActivity(ev("assignment.finished", { id: "a1", assignee: "builder", title: "Rename files", ok: true, session_id: "s9", error: null })),
    ).toBeNull();
  });

  it("assignment.requeued → Your team, RotateCcw, the count", () => {
    expect(toActivity(ev("assignment.requeued", { count: 2, ids: ["a", "b"] }))).toMatchObject({
      href: "/agents?view=team",
      icon: RotateCcw,
      title: "2 assignments picked back up after a restart",
      body: "",
    });
    expect(toActivity(ev("assignment.requeued", { count: 1, ids: ["a"] }))?.title).toBe(
      "1 assignment picked back up after a restart",
    );
    // started / created are not bell items.
    expect(toActivity(ev("assignment.started", { id: "a1", assignee: "builder", title: "x", session_id: "s" }))).toBeNull();
    expect(toActivity(ev("assignment.created", { id: "a1", assignee: "builder", title: "x", source: "user" }))).toBeNull();
  });
});

/* ------------------------------------------------ the agents page, wired --- */

