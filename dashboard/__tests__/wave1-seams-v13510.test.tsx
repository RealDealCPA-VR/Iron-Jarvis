/**
 * v1.351.0 integrate — Wave 1's seams, the dashboard half.
 *
 * `__tests__/fixtures/wave1-seams-v13510.json` holds what the REAL daemon
 * sends (tests/test_wave1_seams_v13510.py drives real fires and the real
 * listing and fails when the daemon stops sending it). This file feeds that
 * same fixture through the REAL bell and the real schedule words, so a change
 * to either side's shape fails one of the two:
 *
 *  - `chat_waiting_row` (G05 → G03): the row GET /chat/approvals/pending
 *    lists for a chat parked on the user with nobody watching shows in the
 *    bell as "A chat is waiting for your OK", linked to THAT chat, counted.
 *  - `run_verdicts` (G01 → G02/G03): each real fire's `session.completed`
 *    payload rings the bell exactly when its schedule row is "short" (the
 *    daemon's SHORT_STATUSES, which the home line counts), and the bell's
 *    words say what the row's words say.
 *
 * Plus the integrator's dashboard changes: the legacy "failed" schedule value
 * reads like "error" everywhere; the Tools Edit door's "saved, but it didn't
 * start" is a warning, never the green box; the card footer's Settings link
 * is a client-side link.
 *
 * Only the network (lib/api), the event socket and the desktop notification
 * hook are stubbed.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

const { getMock, notifyMock, eventsRef, H } = vi.hoisted(() => ({
  getMock: vi.fn(),
  notifyMock: vi.fn(),
  eventsRef: { current: [] as unknown[] },
  H: { pending: [] as unknown[] },
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
    post: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
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

import { NotificationBell, parseChatAsk, toActivity } from "@/components/NotificationBell";
import { __resetApiCache } from "@/lib/apiCache";
import { editedButNotStarted, packStartReason } from "@/lib/packStartWords";
import {
  SHORT_SCHEDULE_STATUSES,
  isShortSchedule,
  runNowNote,
  scheduleStatusWords,
} from "@/lib/scheduleOutcome";
import type { IJEvent } from "@/lib/types";

interface Verdict {
  case: string;
  completion: Record<string, unknown>;
  last_status: string;
  short: boolean;
}

const FIXTURE = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "wave1-seams-v13510.json"), "utf-8"),
) as { chat_waiting_row: Record<string, unknown>; run_verdicts: Verdict[] };

beforeEach(() => {
  __resetApiCache();
  getMock.mockReset();
  notifyMock.mockReset();
  eventsRef.current = [];
  H.pending = [];
  window.localStorage.clear();
  getMock.mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p === "/chat/approvals/pending") return { approvals: H.pending };
    if (p.startsWith("/audit")) return { entries: [], next_cursor: null, total: 0 };
    return {};
  });
});

afterEach(() => cleanup());

/* ================================================= (a) G05 -> G03 */

describe("the waiting-chat row the daemon lists is the one the bell shows", () => {
  it("the fixture is the daemon's row (names only, never arguments)", () => {
    const row = FIXTURE.chat_waiting_row;
    expect(row.kind).toBe("chat");
    expect(Object.keys(row).sort()).toEqual(
      ["id", "kind", "mission_id", "project_id", "requested_at", "thread_id", "tool"].sort(),
    );
    expect(parseChatAsk(row)).toEqual({ id: row.id, threadId: row.thread_id });
  });

  it("reads 'A chat is waiting for your OK', opens THAT chat and counts on the badge", async () => {
    H.pending = [FIXTURE.chat_waiting_row];
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    fireEvent.click(screen.getByRole("button", { name: /notification/i }));
    const row = await screen.findByTestId("bell-chat-waiting");
    expect(row.textContent).toContain("A chat is waiting for your OK");
    expect(row.querySelector("a")?.getAttribute("href")).toBe(
      `/chat?thread=${encodeURIComponent(String(FIXTURE.chat_waiting_row.thread_id))}`,
    );
    expect(row.querySelectorAll("button")).toHaveLength(0);
  });
});

/* ================================================= (b) G01 -> G02 / G03 */

/** The words the bell must use for a row whose schedule reads `status`. */
const BELL_WORDS: Record<string, string> = {
  ok: "finished",
  needs_you: "needs you",
  partial: "finished, but part of it failed",
  error: "could not finish",
};

describe("one verdict: the schedule row, the home line's count and the bell agree", () => {
  it("the fixture covers every verdict the scheduler writes for a run that ended", () => {
    expect(new Set(FIXTURE.run_verdicts.map((v) => v.last_status))).toEqual(
      new Set(["ok", "needs_you", "partial", "error"]),
    );
  });

  for (const v of FIXTURE.run_verdicts) {
    it(`${v.case}: row "${v.last_status}" ${v.short ? "rings" : "is a quiet row"}`, () => {
      // The row and the home line: the dashboard's twin of SHORT_STATUSES.
      expect(isShortSchedule(v.last_status)).toBe(v.short);
      const tone = scheduleStatusWords(v.last_status).tone;
      expect(tone === "warn" || tone === "danger").toBe(v.short);
      // Run now never says "Ran ✓" over a short row.
      expect(runNowNote("x", v.last_status, "").tone === "success").toBe(!v.short);

      // The bell, from the SAME run's real session.completed payload.
      const name = String(v.completion.origin).split(":")[1];
      const item = toActivity({
        id: `e-${v.case}`,
        type: "session.completed",
        ts: "2026-10-18T07:00:05+00:00",
        session_id: `sess-${v.case}`,
        payload: { ...v.completion, summary: "x", project_id: null },
      } as IJEvent)!;
      expect(item).not.toBeNull();
      expect(Boolean(item.attention)).toBe(v.short);
      expect(Boolean(item.quiet)).toBe(!v.short);
      expect(item.title).toBe(`Your schedule ${name} ${BELL_WORDS[v.last_status]}`);
      expect(item.href).toBe(`/sessions/sess-${v.case}`);
    });
  }

  it("a legacy 'failed' row reads like 'error' everywhere (the daemon counts it too)", () => {
    expect(SHORT_SCHEDULE_STATUSES).toContain("failed");
    expect(isShortSchedule("failed")).toBe(true);
    expect(scheduleStatusWords("failed")).toEqual(scheduleStatusWords("error"));
    expect(runNowNote("x", "failed", "boom").tone).toBe("danger");
  });
});

/* ================================================= the Tools Edit door (G08) */

describe("an edit whose pack did not reload says so in the warn words", () => {
  it("names the reason once, with the edit route's own wrappers taken off", () => {
    expect(
      editedButNotStarted(
        "saved, but it did not start: npx isn't installed; see its row for the fix",
        '"brave"',
      ),
    ).toBe(`"brave" saved, but it didn't start: npx isn't installed. Open its row below to retry.`);
    expect(packStartReason("saved; it connected but advertised no tools, see its row")).toBe(
      "it connected but advertised no tools",
    );
  });

  it("keeps the auto-approve restart note as its own sentence after it", () => {
    expect(
      editedButNotStarted(
        "saved, but it did not start: boom; see its row for the fix; restart Iron Jarvis for autonomous agents to pick up the auto-approve change",
        '"x"',
      ),
    ).toBe(
      `"x" saved, but it didn't start: boom. Open its row below to retry. Restart Iron Jarvis for autonomous agents to pick up the auto-approve change.`,
    );
  });

  it("with no reason it still says it did not start", () => {
    expect(editedButNotStarted(null, '"x"')).toBe(`"x" saved, but it didn't start. Open its row below to retry.`);
  });
});

/* ================================================= small source pins */

describe("integrator source pins", () => {
  const read = (rel: string) => readFileSync(join(__dirname, "..", rel), "utf-8").replace(/\r\n/g, "\n");

  it("the Tools page sends a not-reloaded edit to the warn note", () => {
    const src = read("components/settings/pages/ToolsPage.tsx");
    const fn = src.slice(src.indexOf("function onPackEdited"), src.indexOf("function parseEnv"));
    expect(fn).toContain("if (res.reloaded === false) {");
    expect(fn).toContain("setMcpNote({ text: editedButNotStarted(res.note, `\"${serverName}\"`), warn: true });");
  });

  it("the config card's Settings footer is a client-side link", () => {
    const src = read("components/chat/ConfigCards.tsx");
    expect(src).toContain('<Link href="/settings" className="text-[12px] text-zinc-500 hover:text-zinc-300">');
    expect(src).not.toContain('<a href="/settings"');
  });

  it("an Undo of a saved app token never turns the app back on", () => {
    const src = read("app/chat/page.tsx");
    expect(src).toContain('next.kind === "secret" && next.saved && !next.undone ? appIdOf(next.name) : ""');
  });

  it("Continue waits while a file uploads or a turn is starting (the task is never dropped)", () => {
    const src = read("app/chat/page.tsx");
    const fn = src.slice(src.indexOf("continueConnected: (index, cardIndex) => {"), src.indexOf("promote: (content) =>"));
    expect(fn).toMatch(/busy \|\|\n\s+uploadingRef\.current \|\|\n\s+sendingRef\.current\n\s+\)\n\s+return;/);
  });
});
