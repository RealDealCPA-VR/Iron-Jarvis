/**
 * v1.351.0 lane 1B (product-readiness wave 1) — the bell tells the truth
 * about work nobody was watching, and remembers it across a closed window.
 *
 *  G03  A schedule / goal / workflow step / reflex / phone / autonomy run
 *       that FAILED or FELL SHORT rings: "Your schedule morning-brief could
 *       not finish", linking to the run, counted on the badge until the bell
 *       is opened. A clean finish is a QUIET row (no badge, no ping). A
 *       failed workflow run rings; an assignment whose outcome fell short
 *       rings. A chat parked on its own card reads "A chat is waiting for
 *       your OK" and links to that chat.
 *  G04  On mount the bell reads GET /audit for the types it maps, newer than
 *       `ij.bell.lastSeen`, and shows them under "While you were away";
 *       opening the bell writes lastSeen, so a later mount asks from there
 *       and does not repeat them.
 *
 * Driven through the REAL NotificationBell and the real toActivity / lib
 * helpers; only the network (lib/api), the event socket and the desktop
 * notification hook are stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const { getMock, postMock, putMock, notifyMock, eventsRef, H } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  putMock: vi.fn(),
  notifyMock: vi.fn(),
  eventsRef: { current: [] as unknown[] },
  H: {
    /** Rows GET /chat/approvals/pending answers. */
    pending: [] as unknown[],
    /** The daemon's persisted events, as GET /audit entries (naive UTC ts). */
    audit: [] as Array<Record<string, unknown>>,
    /** Every /audit path asked. */
    auditPaths: [] as string[],
  },
}));

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: getMock,
    post: postMock,
    put: putMock,
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
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

import {
  BELL_ACTIVITY_TYPES,
  BELL_EVENT_TYPES,
  NotificationBell,
  parseChatAsk,
  pickShown,
  toActivity,
  unattendedDoor,
  type ActivityItem,
} from "@/components/NotificationBell";
import {
  AWAY_FALLBACK_MS,
  BELL_LAST_SEEN_KEY,
  auditEntriesToEvents,
  awayPath,
  utcStamp,
} from "@/lib/bellAway";
import { __resetApiCache } from "@/lib/apiCache";
import type { IJEvent } from "@/lib/types";

/* ---- fixtures ----------------------------------------------------------- */

const ev = (
  type: string,
  payload: Record<string, unknown>,
  id = "e1",
  session_id: string | null = "s1",
  ts = "2026-10-18T07:00:05+00:00",
): IJEvent => ({ id, type, ts, session_id, payload }) as IJEvent;

/** A `session.completed` payload exactly as orchestrator._completion_payload
 *  shapes it (status, summary, origin, project_id, outcome; ok on failure). */
const finished = (
  origin: string | null,
  over: Record<string, unknown> = {},
  id = "e-sched",
  sid = "s1",
): IJEvent =>
  ev(
    "session.completed",
    {
      status: "completed",
      summary: "Morning brief written.",
      origin,
      project_id: null,
      outcome: "completed",
      ...over,
    },
    id,
    sid,
  );

const FAILED_7AM = finished(
  "schedule:morning-brief",
  { status: "failed", ok: false, outcome: null, summary: "The model was unreachable." },
  "e-7am",
  "sess_7am",
);

/** The daemon's /audit: filter the persisted entries by the `type` list and
 *  `since` it was asked for, newest first — the real route's contract. */
function auditReply(path: string): unknown {
  H.auditPaths.push(path);
  const q = new URL(path, "http://ij.test").searchParams;
  const types = (q.get("type") ?? "").split(",").filter(Boolean);
  const since = Date.parse(q.get("since") ?? "");
  const entries = H.audit
    .filter((e) => types.includes(String(e.type)))
    .filter((e) => Number.isNaN(since) || Date.parse(`${String(e.ts)}Z`) > since)
    .sort((a, b) => String(b.ts).localeCompare(String(a.ts)));
  return { entries, next_cursor: null, total: entries.length };
}

function mockDaemon() {
  getMock.mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p.startsWith("/audit")) return auditReply(p);
    if (p === "/chat/approvals/pending") return { approvals: H.pending };
    return {};
  });
}

/** An ISO moment `ms` ago, with a zone (what the bell stores). */
const isoAgo = (ms: number) => new Date(Date.now() - ms).toISOString();
/** The same moment as the daemon's audit ts: NAIVE UTC (no zone). */
const naiveAgo = (ms: number) => isoAgo(ms).replace(/Z$/, "");
const HOUR = 60 * 60 * 1000;

/** An /audit entry as AuditTimeline._event_entry projects one (naive ts). */
const entry = (
  id: string,
  type: string,
  ts: string,
  payload: Record<string, unknown>,
  session_id: string | null = null,
) => ({ id, type, ts, kind: "lifecycle", actor: "x", session_id, summary: type, payload });

beforeEach(() => {
  __resetApiCache();
  getMock.mockReset();
  postMock.mockReset();
  putMock.mockReset();
  notifyMock.mockReset();
  eventsRef.current = [];
  H.pending = [];
  H.audit = [];
  H.auditPaths = [];
  window.localStorage.clear();
  document.title = "Iron Jarvis";
  mockDaemon();
});

afterEach(() => {
  cleanup();
});

async function openBell() {
  fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
}

/* ======================================================= G03: the words */

describe("G03: an unattended run that fails or falls short rings, in plain words", () => {
  it("a failed 7 am schedule: 'Your schedule morning-brief could not finish', linked to the run, on the badge", () => {
    const item = toActivity(FAILED_7AM)!;
    expect(item).not.toBeNull();
    expect(item.title).toBe("Your schedule morning-brief could not finish");
    expect(item.href).toBe("/sessions/sess_7am");
    expect(item.attention).toBe(true);
    expect(item.quiet).toBeUndefined();
    expect(item.body).toBe("The model was unreachable.");
  });

  it("a clean finish is a QUIET row: no badge flag, no desktop ping", () => {
    const item = toActivity(finished("schedule:morning-brief"))!;
    expect(item.title).toBe("Your schedule morning-brief finished");
    expect(item.quiet).toBe(true);
    expect(item.attention).toBeUndefined();
  });

  it("falling short reads as the mission rows do", () => {
    expect(toActivity(finished("schedule:morning-brief", { outcome: "needs_you" }))!.title).toBe(
      "Your schedule morning-brief needs you",
    );
    expect(
      toActivity(finished("schedule:morning-brief", { outcome: "completed_with_failures" }))!.title,
    ).toBe("Your schedule morning-brief finished, but part of it failed");
    // No outcome is never "finished" alone with a badge: it says to look.
    const bare = toActivity(finished("schedule:morning-brief", { outcome: null }))!;
    expect(bare.title).toBe("Your schedule morning-brief finished. Check what it did");
    expect(bare.attention).toBe(true);
  });

  it("each unattended door names its run", () => {
    const failed = { status: "failed", ok: false, outcome: null };
    const title = (origin: string) => toActivity(finished(origin, failed))!.title;
    expect(title("goal:goal_3f2a9c")).toBe("Your goal's run could not finish"); // never the id
    expect(title("workflow:Weekly report")).toBe("A step of your workflow Weekly report could not finish");
    expect(title("reflex:invoice-drop")).toBe("Your reflex invoice-drop could not finish");
    expect(title("comm:telegram")).toBe("Your request from telegram could not finish");
    expect(title("autonomy")).toBe("An autonomy run could not finish");
  });

  it("a user Stop rings nothing; a clean workflow STEP says nothing (its workflow row does)", () => {
    expect(toActivity(finished("schedule:morning-brief", { status: "cancelled" }))).toBeNull();
    expect(toActivity(finished("workflow:Weekly report"))).toBeNull();
  });

  it("controls: chat, a teammate, an untagged run stay silent; a mission keeps its own words", () => {
    expect(toActivity(finished("chat"))).toBeNull();
    expect(toActivity(finished("job:mission-member"))).toBeNull();
    expect(toActivity(finished(null))).toBeNull();
    expect(unattendedDoor("job:mission")).toBeNull();
    const mission = toActivity(
      finished("job:mission", { status: "failed", ok: false, outcome: null }, "e-m", "m1"),
    )!;
    expect(mission.title).toBe("Your objective could not be finished");
    expect(mission.attention).toBeUndefined(); // unchanged: a mission row never badged
  });

  it("workflow.completed: failed rings and opens /workflows; completed is quiet; cancelled is silent", () => {
    const failed = toActivity(
      ev("workflow.completed", { workflow: "Weekly report", status: "failed", run_id: "wr1", sessions: [] }, "w1", null),
    )!;
    expect(failed.title).toBe("Your workflow Weekly report could not finish");
    expect(failed.href).toBe("/workflows");
    expect(failed.attention).toBe(true);
    const ok = toActivity(
      ev("workflow.completed", { workflow: "Weekly report", status: "completed" }, "w2", null),
    )!;
    expect(ok.quiet).toBe(true);
    expect(ok.attention).toBeUndefined();
    expect(toActivity(ev("workflow.completed", { workflow: "x", status: "cancelled" }, "w3", null))).toBeNull();
  });

  it("assignment.finished: a shortfall outcome rings; no outcome keeps today's behaviour", () => {
    const base = { id: "a1", assignee: "bookkeeper", title: "Reconcile May", session_id: "s9" };
    const needs = toActivity(ev("assignment.finished", { ...base, ok: true, outcome: "needs_you" }))!;
    expect(needs.title).toBe("Needs you: Reconcile May");
    expect(needs.href).toBe("/sessions/s9");
    expect(needs.attention).toBe(true);
    expect(
      toActivity(ev("assignment.finished", { ...base, ok: true, outcome: "completed_with_failures" }))!.title,
    ).toBe("Part of it failed: Reconcile May");
    // Today's behaviour, unchanged: success with no outcome is silent…
    expect(toActivity(ev("assignment.finished", { ...base, ok: true }))).toBeNull();
    expect(toActivity(ev("assignment.finished", { ...base, ok: true, outcome: "completed" }))).toBeNull();
    // …a failure says "Failed:" (and now counts on the badge)…
    const failed = toActivity(ev("assignment.finished", { ...base, ok: false, error: "disk full" }))!;
    expect(failed.title).toBe("Failed: Reconcile May");
    expect(failed.body).toBe("bookkeeper: disk full");
    expect(failed.attention).toBe(true);
    // …and a job the USER cancelled keeps its row but never badges.
    const stopped = toActivity(ev("assignment.finished", { ...base, ok: false, status: "cancelled" }))!;
    expect(stopped.attention).toBeUndefined();
  });

  it("the bell subscribes to workflow.completed, and its replay list is exactly what toActivity maps", () => {
    expect(BELL_EVENT_TYPES).toContain("workflow.completed");
    for (const t of BELL_ACTIVITY_TYPES) expect(BELL_EVENT_TYPES).toContain(t);
    // Not the badge/refetch triggers: those are not rows.
    for (const t of ["review.*", "approval.requested", "workflow.waiting", "runs.busy"]) {
      expect(BELL_ACTIVITY_TYPES).not.toContain(t);
    }
  });
});

/* ================================================= G03: the real bell */

describe("G03: the real bell counts an unattended failure until it is opened", () => {
  it("a failure while the window is open: badge 1, title (1), ONE desktop ping in the row's own words", async () => {
    eventsRef.current = [FAILED_7AM];
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await waitFor(() => expect(document.title).toBe("(1) Iron Jarvis"));
    await waitFor(() =>
      expect(notifyMock).toHaveBeenCalledWith(
        "Your schedule morning-brief could not finish",
        "The model was unreachable.",
        expect.any(Function),
      ),
    );
    // Never a second "N pending" ping for the same failure.
    expect(notifyMock.mock.calls.filter((c) => String(c[0]).startsWith("Iron Jarvis:"))).toEqual([]);

    await openBell();
    const title = await screen.findByText("Your schedule morning-brief could not finish");
    expect(title.closest("a")!.getAttribute("href")).toBe("/sessions/sess_7am");
    // Looked at: the badge clears and the moment is remembered.
    await screen.findByRole("button", { name: "Notifications" });
    await waitFor(() => expect(document.title).toBe("Iron Jarvis"));
    expect(window.localStorage.getItem(BELL_LAST_SEEN_KEY)).not.toBeNull();
  });

  it("a clean finish appears as a quiet row with no badge and no ping", async () => {
    eventsRef.current = [finished("schedule:morning-brief", {}, "e-ok", "sess_ok")];
    render(<NotificationBell />);
    await openBell();
    await screen.findByText("Your schedule morning-brief finished");
    expect(screen.getByRole("button", { name: "Notifications" })).toBeTruthy();
    expect(notifyMock).not.toHaveBeenCalled();
  });

  it("quiet rows can never push a failure out of the list (or off the badge)", async () => {
    const quiet = Array.from({ length: 8 }, (_, i) =>
      finished(`schedule:tick-${i}`, {}, `e-q${i}`, `sq${i}`),
    );
    // newest first: eight quiet successes, then the older failure
    eventsRef.current = [...quiet, FAILED_7AM];
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    await screen.findByText("Your schedule morning-brief could not finish");
  });

  it("pickShown keeps attention rows first and is the plain first-N without them", () => {
    const row = (id: string, attention = false): ActivityItem =>
      ({ id, ts: "", href: "/", icon: () => null, title: id, body: "", ...(attention ? { attention } : {}) }) as unknown as ActivityItem;
    const plain = ["a", "b", "c", "d", "e", "f", "g"].map((id) => row(id));
    expect(pickShown(plain).map((r) => r.id)).toEqual(["a", "b", "c", "d", "e", "f"]);
    const mixed = [...plain, row("late", true)];
    expect(pickShown(mixed).map((r) => r.id)).toEqual(["a", "b", "c", "d", "e", "late"]);
  });
});

describe("G03: a chat parked on its card", () => {
  const CHAT_ROW = { id: "chat:thr_42", kind: "chat", thread_id: "thr_42", tool: "write_file" };

  it("reads 'A chat is waiting for your OK', links to that chat, has no answer buttons, counts", async () => {
    H.pending = [CHAT_ROW];
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    const row = await screen.findByTestId("bell-chat-waiting");
    expect(row.textContent).toContain("A chat is waiting for your OK");
    expect(row.textContent).toContain("Open the chat");
    expect(within(row).getByRole("link").getAttribute("href")).toBe("/chat?thread=thr_42");
    expect(within(row).queryByRole("button")).toBeNull();
    // Not mistaken for an agent ask (whose Approve would 404 on this id).
    expect(screen.queryByTestId("bell-agent-approval")).toBeNull();
    // The footer points at the chat, not at the Agents page.
    expect(screen.getByRole("link", { name: /^Open the chat$/ }).getAttribute("href")).toBe(
      "/chat?thread=thr_42",
    );
  });

  it("parseChatAsk reads only kind 'chat' rows", () => {
    expect(parseChatAsk(CHAT_ROW)).toEqual({ id: "chat:thr_42", threadId: "thr_42" });
    expect(parseChatAsk({ id: "appr_1", tool: "shell", session_id: "s1" })).toBeNull();
    expect(parseChatAsk({ kind: "chat" })).toBeNull();
  });
});

/* ======================================== G04: the bell's memory, real */

describe("G04: what happened while the window was closed", () => {
  it("a mission finished while away -> 'Your objective is done' under 'While you were away'; a reopen after looking does not repeat it", async () => {
    const lastSeen = isoAgo(2 * HOUR);
    window.localStorage.setItem(BELL_LAST_SEEN_KEY, lastSeen);
    H.audit = [
      entry(
        "evt_mission_done",
        "session.completed",
        naiveAgo(1 * HOUR),
        { status: "completed", outcome: "completed", origin: "job:mission", project_id: null, summary: "Plan written." },
        "m77",
      ),
      // Older than lastSeen: the daemon leaves it out (the user saw it).
      entry(
        "evt_old",
        "session.completed",
        naiveAgo(3 * HOUR),
        { status: "failed", ok: false, origin: "schedule:old-one" },
        "s_old",
      ),
    ];

    const first = render(<NotificationBell />);
    await waitFor(() => expect(H.auditPaths.length).toBe(1));
    const q = new URL(H.auditPaths[0], "http://ij.test").searchParams;
    expect(q.get("since")).toBe(lastSeen);
    expect((q.get("type") ?? "").split(",")).toEqual([...BELL_ACTIVITY_TYPES]);

    await openBell();
    await screen.findByTestId("bell-away-heading");
    expect(screen.getByTestId("bell-away-heading").textContent).toBe("While you were away");
    const rows = screen.getAllByTestId("bell-away-row");
    expect(rows.map((r) => r.textContent)).toEqual([expect.stringContaining("Your objective is done")]);
    expect(within(rows[0]).getByRole("link").getAttribute("href")).toBe("/agents?mission=m77");
    // A backlog is never re-fired as desktop toasts.
    expect(notifyMock).not.toHaveBeenCalled();
    // Opening wrote the new lastSeen.
    const seen = window.localStorage.getItem(BELL_LAST_SEEN_KEY)!;
    expect(Date.parse(seen)).toBeGreaterThan(Date.parse(lastSeen));
    first.unmount();

    // The window is closed again and reopened: the read starts at the new
    // lastSeen, so the mission is not repeated.
    render(<NotificationBell />);
    await waitFor(() => expect(H.auditPaths.length).toBe(2));
    expect(new URL(H.auditPaths[1], "http://ij.test").searchParams.get("since")).toBe(seen);
    await openBell();
    await screen.findByText("You're all caught up.");
    expect(screen.queryByTestId("bell-away-heading")).toBeNull();
  });

  it("an unattended failure from while away counts on the badge until the bell is opened", async () => {
    window.localStorage.setItem(BELL_LAST_SEEN_KEY, isoAgo(10 * HOUR));
    H.audit = [
      entry(
        "evt_7am",
        "session.completed",
        naiveAgo(2 * HOUR),
        { status: "failed", ok: false, origin: "schedule:morning-brief", outcome: null, summary: "boom" },
        "sess_7am",
      ),
    ];
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await openBell();
    const row = await screen.findByTestId("bell-away-row");
    expect(row.textContent).toContain("Your schedule morning-brief could not finish");
    expect(row.getAttribute("data-attention")).toBe("true");
    await screen.findByRole("button", { name: "Notifications" });
  });

  it("never opened on this machine: the first read looks back one day", async () => {
    const before = Date.now();
    render(<NotificationBell />);
    await waitFor(() => expect(H.auditPaths.length).toBe(1));
    const since = Date.parse(new URL(H.auditPaths[0], "http://ij.test").searchParams.get("since") ?? "");
    expect(since).toBeGreaterThanOrEqual(before - AWAY_FALLBACK_MS - 1000);
    expect(since).toBeLessThanOrEqual(Date.now() - AWAY_FALLBACK_MS + 1000);
  });

  it("an event both replayed and live is shown once (live wins)", async () => {
    window.localStorage.setItem(BELL_LAST_SEEN_KEY, isoAgo(10 * HOUR));
    H.audit = [
      entry("e-7am", "session.completed", naiveAgo(2 * HOUR), FAILED_7AM.payload, "sess_7am"),
    ];
    eventsRef.current = [FAILED_7AM]; // same id as the replayed entry
    render(<NotificationBell />);
    await waitFor(() => expect(H.auditPaths.length).toBe(1));
    await openBell();
    await screen.findByText("Your schedule morning-brief could not finish");
    expect(screen.getAllByText("Your schedule morning-brief could not finish")).toHaveLength(1);
    expect(screen.queryByTestId("bell-away-heading")).toBeNull();
  });

  it("a failed or odd read leaves the bell working (no rows, no crash)", async () => {
    getMock.mockImplementation(async (path: unknown) => {
      if (String(path).startsWith("/audit")) throw new Error("older daemon");
      return {};
    });
    render(<NotificationBell />);
    await openBell();
    await screen.findByText("You're all caught up.");
  });
});

describe("G04: lib/bellAway", () => {
  it("reads an audit timestamp (naive UTC) as UTC and keeps an explicit zone", () => {
    expect(utcStamp("2026-10-18T07:00:04.5")).toBe("2026-10-18T07:00:04.5Z");
    expect(utcStamp("2026-10-18T07:00:04+00:00")).toBe("2026-10-18T07:00:04+00:00");
    expect(utcStamp("2026-10-18T07:00:04Z")).toBe("2026-10-18T07:00:04Z");
  });

  it("maps entries to events, dropping untyped (older daemon), unknown and repeated rows", () => {
    const out = auditEntriesToEvents(
      [
        entry("a", "session.completed", "2026-10-18T07:00:00", { origin: "schedule:x" }, "s1"),
        { id: "b", ts: "2026-10-18T07:00:00", payload: {} }, // no type
        entry("c", "provider.routed", "2026-10-18T07:00:00", {}),
        entry("a", "session.completed", "2026-10-18T07:00:00", {}, "s1"),
      ],
      BELL_ACTIVITY_TYPES,
    );
    expect(out).toEqual([
      {
        id: "a",
        type: "session.completed",
        ts: "2026-10-18T07:00:00Z",
        session_id: "s1",
        payload: { origin: "schedule:x" },
      },
    ]);
    expect(auditEntriesToEvents(undefined, BELL_ACTIVITY_TYPES)).toEqual([]);
  });

  it("asks for every type in ONE comma-separated list", () => {
    const path = awayPath(["a.b", "c.d"], "2026-10-18T06:00:00.000Z");
    const q = new URL(path, "http://ij.test").searchParams;
    expect(path.startsWith("/audit?")).toBe(true);
    expect(q.get("type")).toBe("a.b,c.d");
    expect(q.get("limit")).toBe("50");
  });
});
