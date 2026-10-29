/**
 * v1.361.0 lane 11A — one notice per outcome, and NONE LOST.
 *
 * The final hunt found notices v1.360.0's one-notice rules lost; each worked
 * at v1.359.0. The rules stay (one window speaks; This PC quiet for what the
 * bell rings); what changed is WHICH window speaks and WHAT the surviving
 * notice says:
 *
 *  (1) A POPPED-OUT window's bell pings when the desktop app says it speaks
 *      (`popout.speaks()` — the main window is closed to the tray), and stays
 *      quiet otherwise. v1.360.1 silenced every pop-out, so with the main
 *      window in the tray most notices raised no toast at all.
 *  (3) A FAILED workflow is pinged and counted even after an EARLIER attempt
 *      of one of its steps was: only the row of the step that ENDED the run
 *      (`workflow.completed` `failed_session`) stands in for it. The events
 *      are the REAL daemon's (fixtures/no-notice-lost-v13610.json, written by
 *      tests/test_no_notice_lost_v13610.py).
 *  (4) ONE new ask is named in the "N pending" ping, in its bell row's words
 *      (the only desktop notice for an ask while a window is open).
 *
 * Real NotificationBell; only the network, the event socket, the polled
 * hooks and the desktop notification hook are stubbed.
 */
import type { ReactNode } from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const { getMock, notifyMock, eventsRef, H } = vi.hoisted(() => ({
  getMock: vi.fn(),
  notifyMock: vi.fn(),
  eventsRef: { current: [] as unknown[] },
  H: { responses: {} as Record<string, unknown> },
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
    post: vi.fn(async () => ({})),
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
vi.mock("@/lib/useApi", () => ({
  useApi: (p: string | null) => ({ data: p ? (H.responses[p] ?? null) : null, error: null, loading: false, reload: () => {} }),
  usePolledApi: (p: string | null) => ({ data: p ? (H.responses[p] ?? null) : null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/chat",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import {
  NotificationBell,
  agentAskWords,
  chatAskWords,
  newAskWords,
  oneRowPerWorkflow,
  toActivity,
  waitingAskWords,
  type ActivityItem,
} from "@/components/NotificationBell";
import { popoutSpeaks, quietWindow } from "@/lib/desktopShell";
import { auditEntriesToEvents } from "@/lib/bellAway";
import { __resetApiCache } from "@/lib/apiCache";
import type { IJEvent } from "@/lib/types";

/* ---- the REAL daemon's events ----------------------------------------- */

type Fixture = Record<string, { audit: unknown[] }>;
function readFixture(): Fixture {
  const file = path.join(__dirname, "fixtures", "no-notice-lost-v13610.json");
  return JSON.parse(readFileSync(file, "utf-8").replace(/\r\n/g, "\n")) as Fixture;
}
const FIX = readFixture();
const TYPES = ["session.completed", "workflow.completed"];
/** One case's events as the live socket delivers them (newest first). */
function liveEvents(key: string): IJEvent[] {
  return auditEntriesToEvents(FIX[key].audit, TYPES);
}
const failedStepOf = (evs: IJEvent[]) =>
  evs.filter((e) => e.type === "session.completed" && (e.payload as { status?: string }).status === "failed");

const RUNS_PATH = "/workflows/runs?status=waiting&slim=true&limit=200";
const PENDING_PATH = "/chat/approvals/pending";
const NIGHTLY = {
  id: "wr1",
  workflow_name: "Nightly",
  status: "waiting",
  waiting_json: JSON.stringify({ index: 0, step: "Approve", question: "Send it?" }),
};
const AGENT_ASK = {
  id: "apr_1",
  tool: "write_file",
  session_id: "s_brief",
  requested_at: "2026-10-20T03:00:00",
  origin: "schedule:nightly-brief",
  task: "Draft the Q3 letter",
};
const MISSION_FAILED = {
  id: "m1",
  type: "session.completed",
  ts: "2026-10-20T03:00:00",
  session_id: "ms1",
  payload: { origin: "job:mission", status: "failed", ok: false, summary: "The team stopped." },
};

function setPopout(speaks: (() => boolean) | null) {
  const w = window as unknown as { ironjarvis?: unknown };
  w.ironjarvis = {
    isDesktop: true,
    popout: { open: () => {}, isPopout: true, path: "/chat", ...(speaks ? { speaks } : {}) },
  };
}

beforeEach(() => {
  __resetApiCache();
  getMock.mockReset();
  getMock.mockImplementation(async (p: unknown) =>
    String(p).startsWith("/audit") ? { entries: [], next_cursor: null, total: 0 } : {},
  );
  notifyMock.mockReset();
  eventsRef.current = [];
  H.responses = {};
  window.localStorage.clear();
  document.title = "Iron Jarvis";
});
afterEach(() => {
  cleanup();
  delete (window as unknown as { ironjarvis?: unknown }).ironjarvis;
});

const pings = () => notifyMock.mock.calls.map((c) => [c[0], c[1]] as [string, string]);

/** Mount with nothing waiting, then let the polls answer (the count RISES,
 *  as it does when a poll lands). */
async function mountThenPoll(responses: Record<string, unknown>) {
  const view = render(<NotificationBell />);
  await new Promise((r) => setTimeout(r, 30));
  Object.assign(H.responses, responses);
  view.rerender(<NotificationBell />);
  return view;
}

async function openBell() {
  fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
}

/* ====================================== (1) which window speaks */

describe("(1) the window that speaks: the main window, else ONE pop-out", () => {
  it("lib/desktopShell: a pop-out speaks only when the desktop app says so", () => {
    expect(popoutSpeaks()).toBe(false); // a browser: no shell
    expect(quietWindow()).toBe(false); // ... and never quiet
    setPopout(null); // an older shell: no speaks() at all
    expect(popoutSpeaks()).toBe(false);
    expect(quietWindow()).toBe(true);
    setPopout(() => false); // the main window is open
    expect(quietWindow()).toBe(true);
    setPopout(() => true); // the main window is in the tray: this one speaks
    expect(popoutSpeaks()).toBe(true);
    expect(quietWindow()).toBe(false);
    setPopout(() => {
      throw new Error("mid-teardown");
    });
    expect(popoutSpeaks()).toBe(false);
  });

  it("main window in the tray: the speaking pop-out pings the parked question AND the mission (v1.359.0's two notices)", async () => {
    setPopout(() => true);
    const view = render(<NotificationBell />);
    await new Promise((r) => setTimeout(r, 50));
    H.responses[RUNS_PATH] = { runs: [NIGHTLY] };
    eventsRef.current = [MISSION_FAILED];
    view.rerender(<NotificationBell />);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(2));
    const got = pings();
    const pending = got.find(([t]) => t === "Iron Jarvis: 1 pending")!;
    expect(pending[1]).toContain("Nightly");
    expect(pending[1]).toContain("Send it?");
    expect(got).toContainEqual(["Your objective could not be finished", "The team stopped."]);
  });

  it("main window open: a pop-out shows the same rows but never pings (no duplicate)", async () => {
    setPopout(() => false);
    const view = render(<NotificationBell />);
    await new Promise((r) => setTimeout(r, 50));
    H.responses[RUNS_PATH] = { runs: [NIGHTLY] };
    eventsRef.current = [MISSION_FAILED];
    view.rerender(<NotificationBell />);
    // The parked question counts (a mission row is not an attention row).
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell(); // both rows are there: nothing was lost, only not pinged
    await screen.findByText("Your objective could not be finished");
    expect(screen.getByText(/Send it\?/)).toBeTruthy();
    expect(notifyMock).not.toHaveBeenCalled();
  });

  it("a quiet pop-out remembers what the main window pinged, so becoming the speaker never repeats a workflow its step already said", async () => {
    const speaker = { now: false };
    setPopout(() => speaker.now);
    const evs = liveEvents("halted_by_agent_step");
    eventsRef.current = failedStepOf(evs); // the main window pings this one
    const view = render(<NotificationBell />);
    await new Promise((r) => setTimeout(r, 50));
    expect(notifyMock).not.toHaveBeenCalled();
    speaker.now = true; // the main window goes to the tray
    eventsRef.current = evs; // the workflow's own failure: the same outcome
    view.rerender(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await new Promise((r) => setTimeout(r, 100));
    expect(notifyMock).not.toHaveBeenCalled();
    // ... and a NEW outcome after the switch is said.
    eventsRef.current = [MISSION_FAILED, ...evs];
    view.rerender(<NotificationBell />);
    await waitFor(() =>
      expect(pings()).toEqual([["Your objective could not be finished", "The team stopped."]]),
    );
  });
});

/* ===================== (3) a failed workflow after a retried step */

describe("(3) only the step that ENDED the run stands in for the workflow", () => {
  it("the real daemon: a retried attempt was pinged, then a tool step ended the run — the workflow's failure is pinged too", async () => {
    const evs = liveEvents("retried_then_tool_failed");
    eventsRef.current = failedStepOf(evs);
    const view = render(<NotificationBell />);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    eventsRef.current = evs;
    view.rerender(<NotificationBell />);
    await waitFor(() =>
      expect(pings().map((p) => p[0])).toEqual([
        "A step of your workflow Nightly could not finish",
        "Your workflow Nightly could not finish",
      ]),
    );
  });

  it("... and counts on the badge after the user looked at the step's ping", async () => {
    const evs = liveEvents("retried_then_tool_failed");
    eventsRef.current = failedStepOf(evs);
    const view = render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByRole("button", { name: "Notifications" });
    fireEvent.keyDown(document, { key: "Escape" });
    await new Promise((r) => setTimeout(r, 50));
    eventsRef.current = evs;
    view.rerender(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
  });

  it("control: the step whose run ENDED it was pinged and seen — one outcome, one ping, no second count", async () => {
    const evs = liveEvents("halted_by_agent_step");
    eventsRef.current = failedStepOf(evs);
    const view = render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByRole("button", { name: "Notifications" });
    fireEvent.keyDown(document, { key: "Escape" });
    await new Promise((r) => setTimeout(r, 50));
    eventsRef.current = evs;
    view.rerender(<NotificationBell />);
    await new Promise((r) => setTimeout(r, 150));
    expect(screen.getByRole("button", { name: "Notifications" })).toBeTruthy();
    expect(pings().map((p) => p[0])).toEqual(["A step of your workflow Nightly could not finish"]);
  });

  const row = (over: Partial<ActivityItem> & { id: string }): ActivityItem =>
    ({ ts: "", href: "/", icon: () => null, title: over.id, body: "", ...over }) as unknown as ActivityItem;

  it("oneRowPerWorkflow: folds every covered step, names only the one that ended it", () => {
    const s1 = row({ id: "e1", session: "s1", attention: true });
    const s2 = row({ id: "e2", session: "s2", attention: true });
    const ended = (endedBy?: string) =>
      oneRowPerWorkflow([row({ id: "w", covers: ["s1", "s2"], attention: true, ...(endedBy === undefined ? {} : { endedBy }) }), s1, s2])[0];
    expect(ended("").absorbs).toEqual(["e1", "e2"]);
    expect(ended("").standsInFor).toEqual([]);
    expect(ended("s2").standsInFor).toEqual(["e2"]);
    // An event an older Iron Jarvis wrote names no step: every folded row
    // stands in, as in v1.360.0 (never a duplicate).
    expect(ended(undefined).standsInFor).toEqual(["e1", "e2"]);
  });

  it("toActivity reads failed_session off the real event", () => {
    const [wf] = liveEvents("halted_by_agent_step").filter((e) => e.type === "workflow.completed");
    const [step] = failedStepOf(liveEvents("halted_by_agent_step"));
    expect(toActivity(wf)!.endedBy).toBe(step.session_id);
    const [wf2] = liveEvents("retried_then_tool_failed").filter((e) => e.type === "workflow.completed");
    expect(toActivity(wf2)!.endedBy).toBe("");
  });
});

/* ================================= (4) ONE new ask, named */

describe("(4) the 'N pending' ping names ONE new ask in its row's words", () => {
  it("a parked workflow question: the workflow and the question", async () => {
    await mountThenPoll({ [RUNS_PATH]: { runs: [NIGHTLY] } });
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    const [title, body] = pings()[0];
    expect(title).toBe("Iron Jarvis: 1 pending");
    expect(body).toBe("1 workflow question waiting: “Nightly” asks “Send it?”");
  });

  it("an agent ask: which run, what it wants to do, for which task", async () => {
    await mountThenPoll({ [PENDING_PATH]: { approvals: [AGENT_ASK] } });
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    expect(pings()[0]).toEqual([
      "Iron Jarvis: 1 pending",
      "1 agent asking permission: Your nightly-brief run wants to save a file for “Draft the Q3 letter”",
    ]);
  });

  it("a chat parked on its card: what the call would do (names only)", async () => {
    await mountThenPoll({
      [PENDING_PATH]: {
        approvals: [{ id: "chat:thr_1", kind: "chat", thread_id: "thr_1", tool: "write_file" }],
      },
    });
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    expect(pings()[0][1]).toBe("1 chat waiting for your OK: it wants to save a file");
  });

  it("a Build session parked on its card is said as the session, never as a chat (v1.369.0 review)", async () => {
    await mountThenPoll({
      [PENDING_PATH]: {
        approvals: [
          { id: "chat:bs_1", kind: "chat", thread_id: "bs_1", tool: "write_file", surface: "build" },
        ],
      },
    });
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    expect(pings()[0][1]).toBe("1 coding session waiting for your OK: it wants to save a file");
  });

  it("several at once keep their counts alone", async () => {
    await mountThenPoll({ [RUNS_PATH]: { runs: [NIGHTLY] }, [PENDING_PATH]: { approvals: [AGENT_ASK] } });
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    expect(pings()[0]).toEqual([
      "Iron Jarvis: 2 pending",
      "1 workflow question waiting · 1 agent asking permission",
    ]);
  });

  it("one answered elsewhere while two arrive in the same poll: the count rises by one, but two are new, so counts alone", async () => {
    const view = await mountThenPoll({ [PENDING_PATH]: { approvals: [AGENT_ASK] } });
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    H.responses[PENDING_PATH] = {
      approvals: [{ id: "chat:thr_1", kind: "chat", thread_id: "thr_1", tool: "write_file" }],
    };
    H.responses[RUNS_PATH] = { runs: [NIGHTLY] };
    view.rerender(<NotificationBell />);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(2));
    expect(pings()[1]).toEqual([
      "Iron Jarvis: 2 pending",
      "1 workflow question waiting · 1 chat waiting for your OK",
    ]);
  });

  it("one more arriving beside one already pending: the NEW one is named", async () => {
    const view = await mountThenPoll({ [RUNS_PATH]: { runs: [NIGHTLY] } });
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(1));
    H.responses[PENDING_PATH] = { approvals: [AGENT_ASK] };
    view.rerender(<NotificationBell />);
    await waitFor(() => expect(notifyMock).toHaveBeenCalledTimes(2));
    expect(pings()[1]).toEqual([
      "Iron Jarvis: 2 pending",
      "1 workflow question waiting · 1 agent asking permission: Your nightly-brief run wants to save a file for “Draft the Q3 letter”",
    ]);
  });

  it("the words, both ways", () => {
    expect(waitingAskWords({ workflow: "Nightly", question: "Send it?" })).toBe("“Nightly” asks “Send it?”");
    expect(agentAskWords({ tool: "write_file", origin: "schedule:x", count: 3 })).toBe(
      "Your x run wants to save a file × 3",
    );
    expect(agentAskWords({ tool: "approve_plan", origin: "job:mission" })).toBe(
      "Your objective wants you to approve its plan",
    );
    expect(agentAskWords({ tool: "unknown" })).toBe("An agent run is waiting for your OK");
    expect(chatAskWords("")).toBe("");
    const asks = {
      waiting: [{ runId: "r", key: "r#0", workflow: "N", question: "Q?", options: [], startedAt: "" }],
      agentAsks: [],
      chatAsks: [],
    };
    expect(newAskWords(1, asks, new Set()).waiting).toBe("“N” asks “Q?”");
    // Not new since the last look, or the count rose by more than one: no name.
    expect(newAskWords(1, asks, new Set(["w:r#0"])).waiting).toBe("");
    expect(newAskWords(2, asks, new Set()).waiting).toBe("");
    // Two new asks behind a count that rose by one (one answered elsewhere):
    // neither is named.
    const two = {
      ...asks,
      chatAsks: [{ id: "chat:t", threadId: "t" }],
    };
    expect(newAskWords(1, two, new Set(), new Map([["chat:t", "write_file"]]))).toEqual({
      waiting: "",
      agent: "",
      chat: "",
    });
    expect(newAskWords(1, { ...two, waiting: [] }, new Set(), new Map([["chat:t", "write_file"]])).chat).toBe(
      "it wants to save a file",
    );
  });
});
