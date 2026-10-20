/**
 * v1.360.0 lane 10A — one notice per outcome; a run's verdict keeps its
 * colour.
 *
 *  (1) THE BELL (the re-audit's G03 regression). While a window is open the
 *      bell's ping is the ONE desktop notice for a run's outcome: the daemon
 *      keeps "This PC" quiet for exactly the rows it pings, and the desktop
 *      app says its attention rows while it sits in the tray. All three read
 *      `fixtures/one-notice-v13600.json` (tests/test_one_notice_v13600.py
 *      holds the daemon and main.js to the same cases). A failed workflow is
 *      ONE row and ONE ping: its failed step's row folds into it.
 *  (2) THE HELPER ROW (v1.358.0's one receipt). A settled helper run's
 *      verdict ("failed", "part failed", "needs you") sits OUTSIDE the
 *      truncating part of the line, in its statusWords tone token, so a
 *      narrow window never cuts it and it is never grey; the full line is the
 *      words' title.
 *  (3) SESSIONS (G80's other half). The armed "Clean up now?" press wears the
 *      calm danger cue, like "Delete them all?" beside it.
 *
 * Real NotificationBell, real AgentRunSettled, real Sessions page and real
 * ConfirmButton; only the network, the event socket and the desktop
 * notification hook are stubbed.
 */
import type { ReactNode } from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const { getMock, postMock, notifyMock, eventsRef, H } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  notifyMock: vi.fn(),
  eventsRef: { current: [] as unknown[] },
  H: {
    audit: [] as Array<Record<string, unknown>>,
    responses: {} as Record<string, unknown>,
    posts: [] as { path: string; body?: unknown }[],
  },
}));

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: getMock,
    post: postMock,
    put: vi.fn(async () => ({})),
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: eventsRef.current, connected: true }),
}));

vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: notifyMock,
  }),
}));

// The Sessions page's own hooks (the sessions-armed-delete-v13580 harness).
vi.mock("@/lib/useApi", () => ({
  useApi: (p: string | null) => ({ data: p ? (H.responses[p] ?? null) : null, error: null, loading: false, reload: () => {} }),
  usePolledApi: (p: string | null) => ({ data: p ? (H.responses[p] ?? null) : null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useReviews", () => ({ useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }) }));
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], loading: false, error: null, reload: () => {} }) }));
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({ online: true, checking: false, health: null, refresh: () => {}, epoch: 0 }),
}));
vi.mock("@/components/NewSessionForm", () => ({ NewSessionForm: () => null, NEW_SESSION_TASK_ID: "new-task" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/sessions",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "transition", "variants", "layout",
    "whileHover", "whileTap", "whileInView", "viewport",
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
    AnimatePresence: ({ children }: { children?: ReactNode }) => createElement(Fragment, null, children),
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
  NotificationBell,
  oneRowPerWorkflow,
  toActivity,
  type ActivityItem,
} from "@/components/NotificationBell";
import { AgentRunSettled, settledRunParts, settledRunWords } from "@/components/chat/AgentRunRow";
import { CALM_CONFIRM } from "@/components/ui";
import SessionsPage from "@/app/sessions/page";
import { __resetApiCache } from "@/lib/apiCache";
import type { AgentRunStamp } from "@/lib/agentRun";
import type { IJEvent } from "@/lib/types";

/* ---- the shared fixture ------------------------------------------------ */

interface Case {
  case: string;
  event: IJEvent & { ts: string };
  pings: boolean;
  attention: boolean;
  title?: string;
  body?: string;
  href?: string;
}

function readFixture(): Case[] {
  const file = path.join(__dirname, "fixtures", "one-notice-v13600.json");
  return (JSON.parse(readFileSync(file, "utf-8").replace(/\r\n/g, "\n")) as { cases: Case[] }).cases;
}
const CASES = readFixture();
const C = (name: string) => CASES.find((c) => c.case === name)!.event;

function mockDaemon() {
  getMock.mockImplementation(async (p: unknown) => {
    const path_ = String(p);
    if (path_.startsWith("/audit")) {
      const entries = [...H.audit].sort((a, b) => String(b.ts).localeCompare(String(a.ts)));
      return { entries, next_cursor: null, total: entries.length };
    }
    if (path_ === "/chat/approvals/pending") return { approvals: [] };
    return {};
  });
}

beforeEach(() => {
  __resetApiCache();
  getMock.mockReset();
  postMock.mockReset();
  notifyMock.mockReset();
  eventsRef.current = [];
  H.audit = [];
  H.responses = {};
  H.posts = [];
  window.localStorage.clear();
  document.title = "Iron Jarvis";
  mockDaemon();
});

afterEach(() => {
  cleanup();
});

/* ======================================================== (1) the bell */

describe("(1) the bell rings exactly the fixture's outcomes, in its words", () => {
  for (const c of CASES) {
    it(`${c.case}: pings=${c.pings}, attention=${c.attention}`, () => {
      const item = toActivity(c.event);
      expect(!!item && !item.quiet, "pings").toBe(c.pings);
      expect(!!item?.attention, "attention").toBe(c.attention);
      if (c.title) expect(item!.title).toBe(c.title);
      if (c.body !== undefined) expect(item!.body).toBe(c.body);
      if (c.href) expect(item!.href).toBe(c.href);
    });
  }
});

const row = (over: Partial<ActivityItem> & { id: string }): ActivityItem =>
  ({ ts: "", href: "/", icon: () => null, title: over.id, body: "", ...over }) as unknown as ActivityItem;

describe("(1) oneRowPerWorkflow: a failed workflow is one row", () => {
  it("the failed workflow's row stands in for its failed step's row, and says so", () => {
    const step = toActivity(C("workflow-step-failed"))!;
    const wf = toActivity(C("workflow-failed"))!;
    expect(step.session).toBe("session_w1");
    expect(wf.covers).toEqual(["session_w1"]);
    const out = oneRowPerWorkflow([wf, step]);
    expect(out.map((r) => r.id)).toEqual([wf.id]);
    expect(out[0].absorbs).toEqual([step.id]);
  });

  it("a step row whose workflow did not fail stays (nothing else says it)", () => {
    const step = row({ id: "s", session: "x1", attention: true });
    const done = toActivity(C("workflow-done"))!; // quiet, no covers
    expect(oneRowPerWorkflow([done, step]).map((r) => r.id)).toEqual([done.id, "s"]);
    // A step of ANOTHER run is not folded either.
    const other = row({ id: "w", covers: ["x2"], attention: true });
    expect(oneRowPerWorkflow([other, step]).map((r) => r.id)).toEqual(["w", "s"]);
  });

  it("a replayed step row folds into a LIVE workflow row", () => {
    const step = row({ id: "s", session: "x1", attention: true });
    const live = row({ id: "w", covers: ["x1"], attention: true });
    expect(oneRowPerWorkflow([step], [live])).toEqual([]);
    expect(oneRowPerWorkflow([step], []).map((r) => r.id)).toEqual(["s"]);
  });
});

async function openBell() {
  fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
}

describe("(1) the real bell: one failed workflow, one row, one ping", () => {
  it("step first, then its workflow: ONE desktop ping, ONE row, badge 1", async () => {
    eventsRef.current = [C("workflow-step-failed")];
    const view = render(<NotificationBell />);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    expect(notifyMock.mock.calls[0][0]).toBe("A step of your workflow Weekly report could not finish");
    // The workflow's own completion lands a moment later.
    eventsRef.current = [C("workflow-failed"), C("workflow-step-failed")];
    view.rerender(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByText("Your workflow Weekly report could not finish");
    expect(screen.queryByText("A step of your workflow Weekly report could not finish")).toBeNull();
    // Never a second ping for the same outcome.
    expect(notifyMock).toHaveBeenCalledTimes(1);
  });

  it("both in one batch: the workflow's words, pinged once", async () => {
    eventsRef.current = [C("workflow-failed"), C("workflow-step-failed")];
    render(<NotificationBell />);
    await waitFor(() =>
      expect(notifyMock).toHaveBeenCalledWith(
        "Your workflow Weekly report could not finish",
        "The Workflows page shows which step stopped.",
        expect.any(Function),
      ),
    );
    expect(notifyMock).toHaveBeenCalledTimes(1);
    await screen.findByRole("button", { name: "1 notifications" });
  });

  it("a step the user already looked at keeps its workflow's row off the badge", async () => {
    eventsRef.current = [C("workflow-step-failed")];
    const view = render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell(); // seen
    await screen.findByRole("button", { name: "Notifications" });
    // Closed again before the workflow's own completion lands (an open bell
    // marks every row it shows as seen anyway).
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByText("A step of your workflow Weekly report could not finish")).toBeNull());
    eventsRef.current = [C("workflow-failed"), C("workflow-step-failed")];
    view.rerender(<NotificationBell />);
    // The workflow's row stands in for a step already seen: no new count,
    // and (the step was pinged) no second ping.
    expect(screen.queryByRole("button", { name: "1 notifications" })).toBeNull();
    expect(screen.getByRole("button", { name: "Notifications" })).toBeTruthy();
    expect(notifyMock).toHaveBeenCalledTimes(1);
  });

  it("a failed schedule while the window is open: the bell's ONE ping, in the row's words", async () => {
    eventsRef.current = [C("schedule-failed")];
    render(<NotificationBell />);
    await waitFor(() =>
      expect(notifyMock).toHaveBeenCalledWith(
        "Your schedule morning-brief could not finish",
        "The run failed: the inbox could not be reached.",
        expect.any(Function),
      ),
    );
    expect(notifyMock).toHaveBeenCalledTimes(1);
  });

  it("a popped-out module's bell shows the row but never pings: the main window speaks for the app", async () => {
    const w = window as unknown as { ironjarvis?: unknown };
    w.ironjarvis = { popout: { open: () => {}, isPopout: true } };
    try {
      eventsRef.current = [C("schedule-failed")];
      render(<NotificationBell />);
      await screen.findByRole("button", { name: "1 notifications" });
      await openBell();
      await screen.findByText("Your schedule morning-brief could not finish");
      expect(notifyMock).not.toHaveBeenCalled();
    } finally {
      delete w.ironjarvis;
    }
  });

  it("what happened while the window was closed: one row per failed workflow there too", async () => {
    const auditOf = (e: IJEvent & { ts: string }) => ({ ...e, kind: "lifecycle", actor: "x", summary: e.type });
    H.audit = [auditOf(C("workflow-step-failed")), auditOf(C("workflow-failed"))];
    render(<NotificationBell />);
    await openBell();
    await screen.findByText("Your workflow Weekly report could not finish");
    expect(screen.queryByText("A step of your workflow Weekly report could not finish")).toBeNull();
    // Replayed rows never ping (unchanged).
    expect(notifyMock).not.toHaveBeenCalled();
  });
});

/* ================================================== (2) the helper row */

const RUN: AgentRunStamp = { session: "s1", startedAt: 1_000, endedAt: 126_000, steps: 6 };
const tokens = (el: Element) => (el.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);

function settled(run: AgentRunStamp) {
  render(<AgentRunSettled name="The researcher" run={run} why="Handing this to the researcher." />);
  return {
    words: screen.getByTestId("agent-run-words"),
    lead: screen.getByTestId("agent-run-lead"),
    verdict: screen.queryByTestId("agent-run-verdict"),
  };
}

describe("(2) a helper run's verdict keeps its colour and is never cut off", () => {
  it("a failed run: '· failed' in the danger token, OUTSIDE the truncating part; the full line is the title", () => {
    const { words, lead, verdict } = settled({ ...RUN, status: "failed" });
    // The words a reader (and every older test) reads are unchanged.
    expect(words.textContent).toBe("The researcher worked for 2 min 5 s · 6 steps · failed");
    expect(words.getAttribute("title")).toBe(words.textContent);
    expect(verdict).not.toBeNull();
    expect(verdict!.textContent).toBe("failed");
    expect(tokens(verdict!)).toContain("!text-tone-danger");
    expect(tokens(verdict!)).toContain("shrink-0");
    // The part that truncates is the length and the steps, never the verdict.
    expect(tokens(lead)).toContain("truncate");
    expect(lead.contains(verdict!)).toBe(false);
    expect(lead.textContent).toBe("The researcher worked for 2 min 5 s · 6 steps");
    expect(tokens(verdict!)).not.toContain("truncate");
  });

  it("needs you and part failed read in the warn token; a step limit too (the card's own tone)", () => {
    for (const [over, word] of [
      [{ status: "completed", outcome: "needs_you" }, "needs you"],
      [{ status: "completed", outcome: "completed_with_failures" }, "part failed"],
      [{ status: "failed", stepLimit: true }, "hit its step limit"],
    ] as const) {
      const { verdict } = settled({ ...RUN, ...over } as AgentRunStamp);
      expect(verdict!.textContent).toBe(word);
      expect(tokens(verdict!)).toContain("!text-tone-warn");
      expect(tokens(verdict!)).not.toContain("!text-tone-danger");
      cleanup();
    }
  });

  it("a stopped run keeps its word visible but uncoloured (a Stop is not a failure)", () => {
    const { verdict } = settled({ ...RUN, status: "cancelled" });
    expect(verdict!.textContent).toBe("stopped");
    expect(tokens(verdict!).filter((c) => c.includes("tone-"))).toEqual([]);
  });

  it("a run that simply finished has no verdict to colour: the line reads as it did", () => {
    const { words, verdict } = settled({ ...RUN, status: "completed", outcome: "completed" });
    expect(verdict).toBeNull();
    expect(words.textContent).toBe("The researcher worked for 2 min 5 s · 6 steps");
  });

  it("with no known length the verdict IS the sentence: still coloured, still outside the cut", () => {
    const run = { session: "s1", startedAt: null, endedAt: null, steps: 6, status: "failed" } as AgentRunStamp;
    const p = settledRunParts("The researcher", run);
    expect(p).toMatchObject({ lead: "The researcher", sep: " ", verdict: "could not finish", rest: " · 6 steps", tone: "red" });
    expect(settledRunWords("The researcher", run)).toBe("The researcher could not finish · 6 steps");
    const { words, verdict } = settled(run);
    expect(words.textContent).toBe("The researcher could not finish · 6 steps");
    expect(tokens(verdict!)).toContain("!text-tone-danger");
  });
});

/* ================================================= (3) Sessions page */

describe("(3) Sessions: the armed 'Clean up now?' wears the danger cue", () => {
  it("first press neutral (as before); armed press: tone-danger on its fill, nothing grey painted over it", async () => {
    H.responses["/sessions"] = { sessions: [] };
    H.responses["/projects"] = { projects: [] };
    postMock.mockImplementation(async (p: string, body?: unknown) => {
      H.posts.push({ path: p, body });
      if (p === "/worktrees/prune") return { pruned: [] };
      return {};
    });
    render(<SessionsPage />);
    const clean = screen.getByRole("button", { name: /Clean up leftover folders/ });
    expect(clean.getAttribute("data-armed")).toBe("false");
    // Idle: exactly the page's old neutral look.
    expect(tokens(clean)).toContain("!text-zinc-400");
    expect(tokens(clean)).toContain("hover:!text-accent-soft");
    fireEvent.click(clean);
    const armed = screen.getByRole("button", { name: /Clean up now\?/ });
    expect(armed.getAttribute("data-armed")).toBe("true");
    const cls = tokens(armed);
    for (const c of CALM_CONFIRM.armed.split(/\s+/)) expect(cls, c).toContain(c);
    expect(cls).toContain("text-tone-danger");
    expect(cls.filter((c) => /^!text-|^hover:!text-/.test(c))).toEqual([]);
    // Still two presses.
    expect(H.posts.some((p) => p.path === "/worktrees/prune")).toBe(false);
    fireEvent.click(armed);
    await waitFor(() => expect(H.posts.some((p) => p.path === "/worktrees/prune")).toBe(true));
    // Once it ran, the press rests again in its neutral look.
    await waitFor(() =>
      expect(
        tokens(within(document.body).getByRole("button", { name: /Clean up leftover folders/ })),
      ).toContain("!text-zinc-400"),
    );
  });
});
