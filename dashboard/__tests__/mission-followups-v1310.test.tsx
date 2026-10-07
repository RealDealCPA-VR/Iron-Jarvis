// v1.310.0 (wave 2, track D) — wave-1 mission carry-overs, dashboard half.
//
// Written BEFORE the fix; every test pins one thing a user sees:
//   * the bell's paused-ask row for a MISSION's ask carries "Open the
//     mission" to that mission's screen (W2-5's mission_id / project_id), so
//     the user answers the ask in context — and a plain job's ask has none;
//   * a teammate card's model line speaks in the member's own tense, the
//     coordinator receipt's rule (failed -> "Tried", stopped -> "Ran on",
//     still at work -> "Working on", done -> "Answered by");
//   * a refused "Retry the N failed items" (409) shows the daemon's own plain
//     sentence — "a follow-up is already running", "still running" — never
//     the canned "Nothing is marked failed any more", never a raw error.
//
// Harness: the mission-wave1-v1309 pattern (the REAL /agents page, useApi
// served from H.api, a recorded post), plus the REAL NotificationBell over the
// same mocks (usePolledApi reads H.api too).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: Record<string, unknown> | undefined }>,
  postResults: {} as Record<string, unknown>,
  postErrors: {} as Record<string, { message: string; status: number }>,
  daemon: null as unknown,
}));

function apiState(path: string | null) {
  return { data: path ? (H.api[path] ?? null) : null, error: null, loading: false, reload: () => {} };
}

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => apiState(path),
  usePolledApi: (path: string | null) => apiState(path),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    cancelled = false;
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
    sseUrl: (p: string) => p,
    get: (path: string) => Promise.resolve(H.api[path] ?? {}),
    post: (path: string, body?: Record<string, unknown>) => {
      H.posts.push({ path, body });
      const err = H.postErrors[path];
      if (err) return Promise.reject(new ApiError(err.message, err.status));
      return Promise.resolve(path in H.postResults ? H.postResults[path] : {});
    },
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/daemon", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useDaemon: () => H.daemon,
}));
vi.mock("@/lib/useModels", () => ({
  useModels: () => ({ data: { models: [] }, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: () => {},
  }),
}));
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));
vi.mock("@/lib/useReviews", () => ({
  useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }),
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
  const cache = new Map<string, (props: Record<string, unknown>) => unknown>();
  const tagFor = (tag: string) => {
    let c = cache.get(tag);
    if (!c) {
      c = (props: Record<string, unknown>) => {
        const rest: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
        return createElement(tag, rest);
      };
      cache.set(tag, c);
    }
    return c;
  };
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) => createElement(Fragment, null, children as never),
    useReducedMotion: () => true,
    motion: new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) }),
  };
});
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (prev: string, chunk: string) => prev + chunk,
}));
vi.mock("@/components/chat/DocPreview", () => ({
  DocPreview: (p: { path: string }) => <div data-testid="doc-preview" data-path={p.path} />,
}));
vi.mock("@/components/Markdown", async (orig) => {
  const { memo } = await import("react");
  const Plain = ({ content }: { content: string }) => <div data-md="">{content}</div>;
  return { ...(await orig<Record<string, unknown>>()), Markdown: Plain, MemoMarkdown: memo(Plain) };
});
vi.mock("@/components/agents/AgentFace", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  default: (p: { name: string }) => <span data-testid="face" data-name={p.name} />,
}));
vi.mock("@/components/kanban/KanbanBoard", () => ({ KanbanBoard: () => null }));
vi.mock("@/components/agents/RosterStrip", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  RosterStrip: () => null,
}));
vi.mock("@/components/agents/SetupCard", () => ({ SetupCard: () => null }));
vi.mock("@/components/agents/AgentsModal", () => ({ AgentsPanel: () => <div data-testid="agents-panel" /> }));
vi.mock("@/components/agents/world/WorldBoard", () => ({ WorldBoard: () => null }));
vi.mock("@/components/agents/world/TeamEditor", () => ({ TeamEditor: () => null }));

import AgentsPage from "@/app/agents/page";
import { NotificationBell } from "@/components/NotificationBell";
import { missionPath } from "@/lib/mission";

/* ----------------------------------------------------------- fixtures --- */

function member(agent: string, status: string, extra: Record<string, unknown> = {}) {
  const done = status === "done";
  return {
    session_id: `c-${agent}`,
    agent,
    name: agent,
    kind: "builtin",
    task: `the ${agent} part`,
    status,
    progress: done ? { pct: 100, label: "Done", basis: "done" } : { pct: null, label: "Working · step 2", basis: "steps" },
    activity: "",
    waiting_on: null,
    steps: 2,
    result: done ? "did it" : "",
    files: [],
    started_at: "2026-10-07T10:00:01",
    finished_at: done ? "2026-10-07T10:02:00" : null,
    provider: "fleet-custom",
    model: "glm-5.3-flash",
    ...extra,
  };
}

function view(
  status: string,
  members: Array<Record<string, unknown>>,
  extra: Record<string, unknown> = {},
) {
  const terminal = status === "completed" || status === "failed" || status === "cancelled";
  return {
    found: true,
    session: {
      id: "s1",
      task: "file the 1099s",
      objective: "file the 1099s",
      status,
      outcome: terminal ? status : null,
      project_id: null,
      created_at: "2026-10-07T10:00:00",
      finished_at: terminal ? "2026-10-07T10:05:00" : null,
      interrupted: false,
      continued_as: null,
      provider: "",
      model: "",
      route_note: "",
    },
    coordinator: { name: "Jarvis", status: terminal ? "done" : "working", waiting_on: null, steps: 2 },
    members,
    progress: { done: 0, total: members.length },
    activity: [],
    deliverable: { text: "", documents: [], worklist: null },
    ...extra,
  };
}

function at(search: string) {
  window.history.replaceState(null, "", `/agents${search}`);
}

beforeEach(() => {
  H.api = {
    "/agents/roster": { roster: [] },
    "/projects": { projects: [] },
    "/missions": { missions: [] },
    "/agents/worlds": { worlds: [], general: { thread_count: 0 } },
    "/agents/threads": { threads: [] },
    "/agents": { builtin: ["builder"], dynamic: [] },
    "/agents/remote": { agents: [] },
  };
  H.posts = [];
  H.postResults = {};
  H.postErrors = {};
  H.daemon = {
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    epoch: 0,
    refresh: () => {},
    provided: true,
    health: { status: "ok", version: "1.310.0", default_provider: "", default_model: "", providers: [] },
  };
  at("");
});
afterEach(() => {
  cleanup();
});

/** Expand one teammate card and read its model line. */
async function modelLine(agent: string): Promise<string> {
  const card = await screen.findByTestId(`mission-card-${agent}`);
  fireEvent.click(within(card).getAllByRole("button")[0]);
  const line = await screen.findByTestId(`mission-card-model-${agent}`);
  return (line.textContent ?? "").trim();
}

/* ---------------------------------- 1. the bell opens the mission --- */

describe("a mission's paused ask in the bell opens the mission (W2-5)", () => {
  const ASK = {
    id: "apr_mission01",
    tool: "shell",
    session_id: "c-builder",
    requested_at: "2026-10-07T10:00:00",
  };

  async function openBellRow() {
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
    return await screen.findByTestId("bell-agent-approval");
  }

  it("a teammate's ask links 'Open the mission' to that mission's screen, inside its project", async () => {
    H.api["/chat/approvals/pending"] = { approvals: [{ ...ASK, mission_id: "m1", project_id: "p1" }] };
    const row = await openBellRow();
    const link = within(row).getByTestId("bell-approval-mission");
    expect(link.textContent).toMatch(/open the mission/i);
    expect(link.getAttribute("href")).toBe(missionPath("m1", "p1"));
    // The answer buttons stay on the row: the link adds context, it does not
    // replace answering from the bell.
    expect(within(row).getByRole("button", { name: "Approve once" })).toBeTruthy();
  });

  it("a mission with no project opens /agents?mission=<id> alone", async () => {
    H.api["/chat/approvals/pending"] = { approvals: [{ ...ASK, mission_id: "m2", project_id: null }] };
    const row = await openBellRow();
    expect(within(row).getByTestId("bell-approval-mission").getAttribute("href")).toBe(missionPath("m2", ""));
  });

  it("control: a plain job's ask (mission_id null, or an older daemon with no key) has no mission link", async () => {
    H.api["/chat/approvals/pending"] = { approvals: [{ ...ASK, mission_id: null, project_id: "p1" }] };
    let row = await openBellRow();
    expect(within(row).queryByTestId("bell-approval-mission")).toBeNull();
    // the session link is still there
    expect(row.querySelector('a[href="/sessions/c-builder"]')).not.toBeNull();
    cleanup();

    H.api["/chat/approvals/pending"] = { approvals: [ASK] };
    row = await openBellRow();
    expect(within(row).queryByTestId("bell-approval-mission")).toBeNull();
  });
});

/* -------------------- 2. a teammate card speaks in its own tense --- */

describe("a teammate card's model line follows the member's status (the receipt rule)", () => {
  it("a FAILED teammate tried its model — nothing answered", async () => {
    H.api["/sessions/s1/mission"] = view("failed", [member("writer", "failed")]);
    at("?mission=s1");
    render(<AgentsPage />);
    const text = await modelLine("writer");
    expect(text).toMatch(/^Tried fleet-custom/);
    expect(text).not.toMatch(/answered/i);
  });

  it("a teammate still at work is working on it (working and waiting on you)", async () => {
    H.api["/sessions/s1/mission"] = view("active", [
      member("writer", "working"),
      member("checker", "waiting_you"),
    ]);
    at("?mission=s1");
    render(<AgentsPage />);
    expect(await modelLine("writer")).toMatch(/^Working on fleet-custom/);
    expect(await modelLine("checker")).toMatch(/^Working on fleet-custom/);
  });

  it("a DONE teammate was answered by it", async () => {
    H.api["/sessions/s1/mission"] = view("completed", [member("researcher", "done")]);
    at("?mission=s1");
    render(<AgentsPage />);
    expect(await modelLine("researcher")).toMatch(/^Answered by fleet-custom · glm-5\.3-flash/);
  });

  it("control: a STOPPED teammate ran on it, and a mock teammate still says no real model ran", async () => {
    H.api["/sessions/s1/mission"] = view("cancelled", [
      member("writer", "cancelled"),
      member("researcher", "done", { provider: "mock", model: "mock-1" }),
    ]);
    at("?mission=s1");
    render(<AgentsPage />);
    expect(await modelLine("writer")).toMatch(/^Ran on fleet-custom/);
    expect(await modelLine("researcher")).toMatch(/no real model ran/i);
  });
});

/* ----------------- 3. a refused retry says why, in plain words --- */

describe("a refused 'Retry the N failed items' says the daemon's reason in plain words", () => {
  function failedWithTwo() {
    H.api["/sessions/s1/mission"] = view("failed", [member("researcher", "done")], {
      deliverable: { text: "", documents: [], worklist: { total: 5, done: 3, failed: 2, pending: 0, doing: 0 } },
    });
  }

  async function pressRetry(): Promise<void> {
    at("?mission=s1");
    render(<AgentsPage />);
    const retry = await screen.findByTestId("mission-retry-failed");
    await waitFor(() => expect((retry as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(retry);
  }

  it("a follow-up already running: says so and to wait — not 'nothing is marked failed'", async () => {
    failedWithTwo();
    H.postErrors["/missions/s1/retry-failed"] = {
      status: 409,
      message: "a follow-up of this mission is already running or queued — wait for it to finish before retrying",
    };
    await pressRetry();
    await waitFor(() =>
      expect(screen.getByTestId("mission-action-note").textContent ?? "").toMatch(/already running or queued/i),
    );
    const note = (screen.getByTestId("mission-action-note").textContent ?? "").trim();
    expect(note).toMatch(/wait for it to finish/i);
    expect(note).not.toMatch(/nothing is marked failed/i);
    expect(note[0]).toBe(note[0].toUpperCase()); // a sentence, not a raw fragment
    expect(note).not.toMatch(/409|\{|detail/);
  });

  it("the mission itself still running: says it is still running", async () => {
    failedWithTwo();
    H.postErrors["/missions/s1/retry-failed"] = {
      status: 409,
      message: "this mission is still running — wait for it to finish before retrying",
    };
    await pressRetry();
    await waitFor(() =>
      expect(screen.getByTestId("mission-action-note").textContent ?? "").toMatch(/still running/i),
    );
    const note = (screen.getByTestId("mission-action-note").textContent ?? "").trim();
    expect(note).not.toMatch(/nothing is marked failed/i);
    expect(note[0]).toBe(note[0].toUpperCase());
  });

  it("control: nothing failed any more still says nothing failed", async () => {
    failedWithTwo();
    H.postErrors["/missions/s1/retry-failed"] = { status: 409, message: "nothing failed in this mission" };
    await pressRetry();
    await waitFor(() =>
      expect(screen.getByTestId("mission-action-note").textContent ?? "").toMatch(/nothing.*failed/i),
    );
  });
});
