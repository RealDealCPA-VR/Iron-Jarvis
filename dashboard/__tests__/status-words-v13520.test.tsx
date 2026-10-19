/**
 * v1.352.0 (readiness audit G17): ONE status word table for every surface.
 *
 * THE FAILURE. The same state read six ways: a running job was "Active",
 * "Working", "working", "running"; a queued teammate was "Waiting" on its
 * mission card and "Queued" in the mission list beside it; done was
 * "Completed", "Done", "Finished" or "✓ ok"; a run the user STOPPED wore a
 * red "Cancelled" (the shared tone map painted it like a failure); the
 * Sessions filter listed raw ids; the Your-team inbox showed raw assignment
 * statuses ("claimed", "blocked").
 *
 * ACCEPTANCE (as a new user reads it): a stopped run reads "Stopped" in slate
 * on Sessions, the mission list and the Session board; a queued teammate
 * reads "Queued" in both mission places; the Sessions filter offers Working /
 * Queued / Stopped.
 *
 * Every pin drives the REAL table (lib/statusWords) through the REAL
 * components that show it: SessionStatusBadge, the Sessions page and its
 * filter, the Session board, the inbox row, the mission cards and the
 * mission list. Only the network is faked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

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
    responses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  sseUrl: (p: string) => p,
  get: (path: string) => {
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 404));
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
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
vi.mock("@/lib/useModels", () => ({
  useModels: () => ({ models: [], data: { models: [] }, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ health: null, checking: false, online: true, refresh: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/components/NewSessionForm", () => ({ NewSessionForm: () => null, NEW_SESSION_TASK_ID: "new-task" }));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
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
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
    useReducedMotion: () => true,
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
  NOT_RUN_YET,
  SESSION_FILTER_STATUSES,
  outcomeWords,
  scheduleRunWords,
  scheduleStatusLabel,
  statusWords,
} from "@/lib/statusWords";
import { Badge, StatusDot, statusTone } from "@/components/ui";
import { SessionStatusBadge, outcomeLabel, stoppedChip } from "@/components/sessions/SessionStatusBadge";
import { AssignmentRow } from "@/components/agents/AgentInbox";
import { AgentCards } from "@/components/agents/mission/AgentCards";
import { MissionComposer } from "@/components/agents/mission/MissionComposer";
import { KanbanBoard } from "@/components/kanban/KanbanBoard";
import { LANES } from "@/lib/kanban";
import { statusWord, type MissionMember } from "@/lib/mission";
import SessionsPage from "@/app/sessions/page";
import type { Assignment, SessionView } from "@/lib/types";

beforeEach(() => {
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  api.responses = {};
});

function sv(id: string, status: string, over: Partial<SessionView> = {}): SessionView {
  return {
    id,
    task: `task ${id}`,
    agent_type: "builder",
    provider: "mock",
    model: "mock-model",
    status,
    workspace_path: "C:/w",
    summary: "",
    created_at: "2026-10-18T10:00:00Z",
    finished_at: null,
    ...over,
  };
}

/** The calm Badge's dot classes (the tone lives on the dot). */
function dotOf(chip: Element): string {
  return chip.querySelector("span")?.className ?? "";
}

/* ============================================================ the table */

describe("the one table: every state a person can meet, in words and a tone", () => {
  it("sessions: active / queued / completed / failed / cancelled", () => {
    expect(statusWords("active")).toEqual({ word: "Working", tone: "cyan" });
    expect(statusWords("queued")).toEqual({ word: "Queued", tone: "slate" });
    expect(statusWords("completed")).toEqual({ word: "Done", tone: "green" });
    expect(statusWords("failed")).toEqual({ word: "Failed", tone: "red" });
    // The headline of this gap: a stop is not a failure.
    expect(statusWords("cancelled")).toEqual({ word: "Stopped", tone: "slate" });
  });

  it("assignments and mission members use the SAME words for the same states", () => {
    expect(statusWords("claimed").word).toBe("Working");
    expect(statusWords("running").word).toBe("Working");
    expect(statusWords("working").word).toBe("Working");
    expect(statusWords("done").word).toBe("Done");
    expect(statusWords("blocked")).toEqual({ word: "Needs you", tone: "amber" });
    expect(statusWords("waiting_you")).toEqual({ word: "Needs you", tone: "amber" });
  });

  it("a completed run reads its ledger verdict; a failed or stopped one does not", () => {
    expect(statusWords("completed", "needs_you")).toEqual({ word: "Done · needs you", tone: "amber" });
    expect(statusWords("completed", "completed_with_failures")).toEqual({ word: "Done · part failed", tone: "amber" });
    expect(statusWords("completed", "completed")).toEqual({ word: "Done", tone: "green" });
    expect(statusWords("completed", null).word).toBe("Done");
    expect(statusWords("failed", "needs_you").word).toBe("Failed");
    expect(statusWords("cancelled", "completed_with_failures").word).toBe("Stopped");
    expect(outcomeWords("completed")).toBeNull();
  });

  it("workflow runs and steps: resuming, waiting, interrupted, error, skipped, pending", () => {
    expect(statusWords("resuming")).toEqual({ word: "Resuming", tone: "cyan" });
    expect(statusWords("waiting")).toEqual({ word: "Waiting for you", tone: "amber" });
    expect(statusWords("interrupted")).toEqual({ word: "Interrupted", tone: "amber" });
    expect(statusWords("error")).toEqual({ word: "Failed", tone: "red" });
    expect(statusWords("skipped")).toEqual({ word: "Skipped", tone: "slate" });
    expect(statusWords("pending")).toEqual({ word: "Pending", tone: "amber" });
  });

  it("case and spaces never change the word; an unknown state is shown as itself, never guessed", () => {
    expect(statusWords(" Cancelled ")).toEqual(statusWords("cancelled"));
    expect(statusWords("brand_new")).toEqual({ word: "Brand new", tone: "slate" });
    expect(statusWords("")).toEqual({ word: "Unknown", tone: "slate" });
    expect(statusWords(undefined).word).toBe("Unknown");
  });

  it("the table and the shared tone map agree on every run state they both know", () => {
    // components/ui's statusTone paints the dots (StatusDot) and any Badge
    // that names no tone; it must say what the table says, or a stopped
    // run's dot would still read red beside a slate "Stopped".
    for (const s of ["active", "running", "queued", "completed", "failed", "cancelled", "pending", "error"]) {
      expect(statusTone(s), s).toBe(statusWords(s).tone);
    }
  });

  it("schedules: every verdict the scheduler writes, in the same words", () => {
    expect(scheduleRunWords("ok")).toEqual(statusWords("completed"));
    expect(scheduleRunWords("needs_you")).toEqual(statusWords("completed", "needs_you"));
    expect(scheduleRunWords("partial")).toEqual(statusWords("completed", "completed_with_failures"));
    expect(scheduleRunWords("error")).toEqual(statusWords("failed"));
    expect(scheduleRunWords("failed")).toEqual(scheduleRunWords("error"));
    expect(scheduleRunWords("cancelled")).toEqual(statusWords("cancelled"));
    expect(scheduleRunWords("skipped")).toEqual({ word: "Skipped", tone: "amber" });
    expect(scheduleRunWords("missed")).toEqual({ word: "Missed", tone: "amber" });
    expect(scheduleRunWords("")).toBe(NOT_RUN_YET);
    expect(scheduleRunWords(null).word).toBe("Not run yet");
    expect(scheduleRunWords("brand_new").word).toBe("Brand new");
  });

  it("the schedule words in lib/scheduleOutcome's {label, tone} shape keep its short-row tones", () => {
    // A short row (error / failed / needs_you / partial) is warn or danger,
    // every other row is not — the rule wave1-seams pins for the bell.
    for (const s of ["error", "failed", "needs_you", "partial"]) {
      expect(["warn", "danger"]).toContain(scheduleStatusLabel(s).tone);
    }
    expect(scheduleStatusLabel("ok")).toEqual({ label: "Done", tone: "success" });
    expect(scheduleStatusLabel("cancelled")).toEqual({ label: "Stopped", tone: "muted" });
    expect(scheduleStatusLabel("")).toEqual({ label: "Not run yet", tone: "muted" });
  });
});

/* ================================================== Sessions: the badge */

describe("Sessions: a stopped run reads 'Stopped' in slate", () => {
  it("the bordered pill says Stopped in the slate tone, never the danger red", () => {
    render(<SessionStatusBadge session={{ status: "cancelled" }} />);
    const chip = screen.getByText("Stopped");
    expect(chip.className).toMatch(/text-zinc-300/);
    expect(chip.className).not.toMatch(/tone-danger/);
    expect(dotOf(chip)).toMatch(/bg-zinc-500/);
  });

  it("the calm chip (a project's Tasks) says Stopped with a slate dot", () => {
    render(<SessionStatusBadge session={{ status: "cancelled" }} variant="calm" />);
    const chip = screen.getByText("Stopped");
    expect(chip.getAttribute("data-tone")).toBe("slate");
    expect(dotOf(chip)).toMatch(/bg-zinc-500/);
  });

  it("a stopped run's dot is slate too (the shared tone map)", () => {
    const { container } = render(<StatusDot status="cancelled" />);
    expect((container.firstElementChild as HTMLElement).className).toMatch(/bg-zinc-500/);
    expect((container.firstElementChild as HTMLElement).className).not.toMatch(/tone-danger/);
  });

  it("a caller's own tone still wins (the session header's violet Queued)", () => {
    render(<SessionStatusBadge session={{ status: "queued" }} tone="violet" />);
    expect(screen.getByText("Queued").className).toMatch(/tone-violet/);
  });

  it("the outcome chip and the plain badge speak the table's words", () => {
    expect(outcomeLabel({ status: "completed", outcome: "needs_you" })).toBe("Done · needs you");
    expect(outcomeLabel({ status: "completed", outcome: "completed_with_failures" })).toBe("Done · part failed");
    render(<SessionStatusBadge session={{ status: "active" }} />);
    expect(screen.getByText("Working")).toBeInTheDocument();
    expect(screen.queryByText("active")).toBeNull();
  });

  it("stoppedChip: the board's word for a stopped card, and nothing for any other", () => {
    expect(stoppedChip({ status: "cancelled" })).toEqual({ word: "Stopped", tone: "slate" });
    expect(stoppedChip({ status: "failed" })).toBeNull();
    expect(stoppedChip({ status: "completed" })).toBeNull();
    expect(stoppedChip({ status: "active" })).toBeNull();
  });
});

/* ============================================== Sessions: page + filter */

describe("Sessions page: the badges and the filter speak words", () => {
  function mount(rows: SessionView[]) {
    api.responses["/sessions"] = { sessions: rows };
    api.responses["/projects"] = { projects: [] };
    render(<SessionsPage />);
  }

  it("a stopped row reads Stopped (slate) in the table", () => {
    mount([sv("s-stop", "cancelled"), sv("s-run", "active")]);
    const row = screen.getByRole("link", { name: "task s-stop" }).closest("tr") as HTMLElement;
    const chip = within(row).getByText("Stopped");
    expect(chip.className).toMatch(/text-zinc-300/);
    expect(within(row).queryByText(/cancelled/i)).toBeNull();
    const live = screen.getByRole("link", { name: "task s-run" }).closest("tr") as HTMLElement;
    expect(within(live).getByText("Working")).toBeInTheDocument();
  });

  it("the filter offers Working / Queued / Done / Failed / Stopped in words, values stay raw", () => {
    // Even with only one state present: a person can ask for "Stopped"
    // before they know whether anything stopped.
    mount([sv("s-run", "active")]);
    const select = screen.getByRole("combobox", { name: "Filter by status" }) as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => [o.value, o.textContent]);
    expect(options).toEqual([
      ["", "All statuses"],
      ["active", "Working"],
      ["queued", "Queued"],
      ["completed", "Done"],
      ["failed", "Failed"],
      ["cancelled", "Stopped"],
    ]);
    expect(SESSION_FILTER_STATUSES).toEqual(["active", "queued", "completed", "failed", "cancelled"]);
  });

  it("choosing Stopped shows the stopped runs only; a status the table does not know is still offered", () => {
    mount([sv("s-stop", "cancelled"), sv("s-run", "active"), sv("s-odd", "paused_by_admin")]);
    const select = screen.getByRole("combobox", { name: "Filter by status" }) as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.textContent)).toContain("Paused by admin");
    fireEvent.change(select, { target: { value: "cancelled" } });
    expect(screen.getByRole("link", { name: "task s-stop" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "task s-run" })).toBeNull();
    expect(screen.queryByRole("link", { name: "task s-odd" })).toBeNull();
  });
});

/* ======================================================= the Session board */

describe("the Session board: lanes in the table's words; a stopped card is slate", () => {
  it("the four lanes read Working / Waiting for you / Done / Failed, toned by the table", () => {
    expect(LANES.map((l) => [l.id, l.title, l.tone])).toEqual([
      ["active", "Working", "cyan"],
      ["review", "Waiting for you", "amber"],
      ["completed", "Done", "green"],
      ["failed", "Failed", "red"],
    ]);
    // The failed lane says a stopped run lives there too.
    expect(LANES.find((l) => l.id === "failed")?.hint).toMatch(/stopped/);
    // Every lane carries a sentence for its empty state.
    for (const l of LANES) expect(l.empty, l.id).toBeTruthy();
  });

  it("renders the lanes by those words, and a stopped card's dot is slate", () => {
    api.responses["/sessions/teams"] = { parents: {} };
    render(<KanbanBoard sessions={[sv("s-stop", "cancelled")]} reviews={{}} reload={() => {}} />);
    for (const title of ["Working", "Waiting for you", "Done", "Failed"]) {
      expect(screen.getByRole("heading", { level: 2, name: title })).toBeInTheDocument();
    }
    const failedLane = document.querySelector('[data-lane="failed"]') as HTMLElement;
    const card = within(failedLane).getByText("task s-stop").parentElement as HTMLElement;
    const dot = card.querySelector("span") as HTMLElement;
    expect(dot.className).toMatch(/bg-zinc-500/);
    expect(dot.className).not.toMatch(/tone-danger/);
  });
});

/* =============================================================== the inbox */

const assignment = (over: Partial<Assignment>): Assignment =>
  ({
    id: "a1",
    project_id: null,
    assignee: "builder",
    task: "Draft the supplier price list",
    title: "Draft the supplier price list",
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
    created_at: null,
    claimed_at: null,
    started_at: null,
    finished_at: null,
    updated_at: null,
    ...over,
  }) as Assignment;

describe("Your-team inbox: assignment states in words, never raw ids", () => {
  it.each([
    ["queued", "Queued", "bg-zinc-500"],
    ["claimed", "Working", "bg-accent"],
    ["running", "Working", "bg-accent"],
    ["done", "Done", "bg-tone-success"],
    ["failed", "Failed", "bg-tone-danger"],
    ["blocked", "Needs you", "bg-tone-warn"],
    ["cancelled", "Stopped", "bg-zinc-500"],
  ] as const)("%s reads %s (dot %s)", (status, word, dot) => {
    render(<AssignmentRow assignment={assignment({ status })} onChanged={() => {}} />);
    const row = screen.getByTestId(`inbox-${status}-a1`);
    const chip = within(row).getByText(word);
    expect(chip.getAttribute("data-badge-variant")).toBe("calm");
    expect(dotOf(chip)).toContain(dot);
    // The chip carries the word as written: "Needs you", never "Needs You".
    expect(chip.className).not.toMatch(/\bcapitalize\b/);
    expect(within(row).queryByText(status)).toBeNull();
  });
});

/* ======================================================== the mission screen */

function member(over: Partial<MissionMember>): MissionMember {
  return {
    session_id: "sess-m1",
    agent: "researcher",
    name: "Researcher",
    kind: "builtin",
    task: "Collect the March invoices",
    status: "queued",
    progress: { pct: 0, label: "Queued", basis: "queued" },
    activity: "",
    waiting_on: null,
    steps: 0,
    result: "",
    files: [],
    started_at: null,
    finished_at: null,
    provider: "",
    model: "",
    ...over,
  };
}

describe("the mission: a queued teammate reads Queued in both places", () => {
  it("statusWord is the table's word (it said 'Waiting' for queued)", () => {
    expect(statusWord("queued")).toBe("Queued");
    expect(statusWord("working")).toBe("Working");
    expect(statusWord("waiting_you")).toBe("Needs you");
    expect(statusWord("done")).toBe("Done");
    expect(statusWord("failed")).toBe("Failed");
    expect(statusWord("cancelled")).toBe("Stopped");
  });

  it("the teammate card reads Queued", () => {
    render(<AgentCards members={[member({ status: "queued" })]} />);
    const card = screen.getByTestId("mission-card-researcher");
    expect(within(card).getByText("Queued")).toBeInTheDocument();
    expect(within(card).queryByText("Waiting")).toBeNull();
  });

  it("the mission list reads Queued for a queued mission and Stopped (neutral) for a stopped one", () => {
    api.responses["/missions"] = {
      missions: [
        { id: "m-q", objective: "Reconcile March", status: "queued", waiting: false, created_at: "2026-10-18T10:00:00Z" },
        { id: "m-s", objective: "Draft the memo", status: "cancelled", waiting: false, created_at: "2026-10-18T09:00:00Z" },
        { id: "m-a", objective: "File the receipts", status: "active", waiting: false, created_at: "2026-10-18T08:00:00Z" },
        { id: "m-d", objective: "Sort the inbox", status: "completed", waiting: false, created_at: "2026-10-18T07:00:00Z" },
      ],
    };
    render(<MissionComposer onStarted={() => {}} onOpen={() => {}} />);
    const list = screen.getByTestId("mission-recent");
    const word = (objective: string) =>
      within(list).getByText(objective).closest("button")?.lastElementChild as HTMLElement;
    // The list and the cards say the SAME word for every state they share.
    expect(word("Reconcile March").textContent).toBe(statusWords("queued").word);
    expect(word("Draft the memo").textContent).toBe(statusWords("cancelled").word);
    expect(word("File the receipts").textContent).toBe(statusWords("active").word);
    expect(word("Sort the inbox").textContent).toBe(statusWords("completed").word);
    // Stopped is neutral there (zinc), never the danger tone.
    expect(word("Draft the memo").className).toMatch(/text-zinc-400/);
    expect(word("Draft the memo").className).not.toMatch(/tone-danger/);
  });
});

/* ===================================================== the Badge it draws */

describe("the table's words render with their own case", () => {
  it("a two-word state keeps its case through Badge keepCase", () => {
    const w = statusWords("blocked");
    render(<Badge value={w.word} tone={w.tone} keepCase />);
    expect(screen.getByText("Needs you").className).not.toMatch(/\bcapitalize\b/);
  });
});
