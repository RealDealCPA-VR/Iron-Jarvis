/**
 * v1.358.0 lane 8A — the bell says what happened, in the app's words and
 * tokens (the re-audit's bell regressions).
 *
 *  - (3) a browser (computer-use) task that stopped to ASK is not announced
 *        as a failure (its own words, the ask mark — never the warning a
 *        failure wears); a real failure reads in plain words; neither shows a
 *        raw status ("awaiting_approval") or the run id in its visible text.
 *        Every terminal status the browser harness writes is covered (read
 *        from the daemon's own list: fixtures/outcomes-v13580.json);
 *  - (4) a run's model-written summary is flattened before it reaches a row
 *        ("# Final Report ## What happened …" read raw before) — by the SAME
 *        helper the Schedules row uses (lib/plainText.summaryLine);
 *  - (5) the bell's rows are drawn with theme tone tokens, never a literal
 *        palette hue (amber/rose/cyan…), so a custom palette re-inks them;
 *  - (7) the approval buttons' tooltips name the tool in words
 *        (lib/toolWords via approvalWords.askPhrase), never the raw tool id.
 *
 * Only the network (lib/api), the event socket and the desktop-notification
 * hook are stubbed; the bell, its mapper and its helpers are the real ones.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { AlertTriangle, MonitorCog, ShieldAlert } from "lucide-react";

const { getMock, postMock, notifyMock, eventsRef, H } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
  notifyMock: vi.fn(),
  eventsRef: { current: [] as unknown[] },
  H: { approvals: [] as unknown[], cuPending: 0 },
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

import { NotificationBell, toActivity } from "@/components/NotificationBell";
import { __resetApiCache } from "@/lib/apiCache";
import { browserRunWords } from "@/lib/browserRunWords";
import { summaryLine } from "@/lib/plainText";
import { scheduleDetail } from "@/lib/scheduleOutcome";
import type { IJEvent } from "@/lib/types";

const FIXTURE = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "outcomes-v13580.json"), "utf-8"),
) as { browser_statuses: string[] };

/** A literal palette hue on a utility class (what a theme cannot re-ink). */
const LITERAL_HUE =
  /\b(?:text|bg|border|ring|from|to|via|fill|stroke|shadow|outline|divide)-(?:amber|rose|red|cyan|emerald|green|yellow|orange|sky|blue|lime|teal|indigo|violet|purple|pink|fuchsia)-\d{2,3}\b/;

beforeEach(() => {
  __resetApiCache();
  getMock.mockReset();
  postMock.mockReset();
  notifyMock.mockReset();
  eventsRef.current = [];
  H.approvals = [];
  H.cuPending = 0;
  window.localStorage.clear();
  getMock.mockImplementation(async (path: unknown) => {
    const p = String(path);
    if (p === "/computeruse") return { pending_approvals: H.cuPending };
    if (p === "/diagnostics") return { pending_reviews: 0 };
    if (p === "/chat/approvals/pending") return { approvals: H.approvals };
    if (p.startsWith("/audit")) return { entries: [], next_cursor: null, total: 0 };
    return {};
  });
  postMock.mockResolvedValue({ ok: true });
});
afterEach(() => cleanup());

function ev(type: string, payload: Record<string, unknown>, session_id: string | null = null, id = `e-${type}`): IJEvent {
  return { id, type, ts: "2026-10-19T07:00:05+00:00", session_id, payload } as unknown as IJEvent;
}

/* ======================================== (3) a browser task's ending */

describe("(3) a browser task that stopped to ask is not a failure; a failure reads in plain words", () => {
  it("the harness's statuses are the ones the bell words (the daemon's own list)", () => {
    expect(FIXTURE.browser_statuses).toEqual(
      expect.arrayContaining(["completed", "failed", "blocked", "awaiting_approval"]),
    );
  });

  for (const status of FIXTURE.browser_statuses) {
    it(`${status}: plain words, no raw status, no run id in the visible text`, () => {
      const item = toActivity(ev("computeruse.run_finished", { run_id: "run_ab12cd34", status, steps: 4 }))!;
      expect(item).not.toBeNull();
      const visible = `${item.title} ${item.body}`;
      expect(visible).not.toContain("run_ab12cd34");
      expect(visible).not.toContain(status === "completed" ? "completed ·" : status);
      expect(visible).not.toMatch(/Computer-use/);
      expect(item.title).toBe(browserRunWords(status, 4).title);
      expect(item.href).toBe("/computeruse");
    });
  }

  it("awaiting_approval reads as waiting for the user's OK, with the ask mark — never 'failed'", () => {
    const item = toActivity(ev("computeruse.run_finished", { run_id: "r1", status: "awaiting_approval", steps: 4 }))!;
    expect(item.title).toBe("Your browser task is waiting for your OK");
    expect(item.title).not.toMatch(/fail/i);
    expect(item.body).not.toMatch(/fail/i);
    expect(item.icon).toBe(ShieldAlert);
  });

  it("a failure says it could not finish, in words, with the warning mark", () => {
    const item = toActivity(ev("computeruse.run_finished", { run_id: "r2", status: "failed", steps: 3 }))!;
    expect(item.title).toBe("Your browser task could not finish");
    expect(item.body).toBe("A step did not work after 3 steps. The Browser page shows which one.");
    expect(item.icon).toBe(AlertTriangle);
    // Anti-vacuity: a finished one keeps the quiet browser mark.
    const ok = toActivity(ev("computeruse.run_finished", { run_id: "r3", status: "completed", steps: 1 }))!;
    expect(ok.title).toBe("Your browser task finished");
    expect(ok.body).toBe("It took 1 step.");
    expect(ok.icon).toBe(MonitorCog);
  });

  it("an unknown or missing status says it stopped — never guesses a failure, never prints the value", () => {
    const item = toActivity(ev("computeruse.run_finished", { run_id: "r4", status: "weird_new_state" }))!;
    expect(item.title).toBe("Your browser task stopped");
    expect(`${item.title} ${item.body}`).not.toMatch(/weird_new_state|r4/);
  });
});

/* =================================== (4) a run's summary, flattened once */

describe("(4) a run's model-written summary reaches a bell row as plain words", () => {
  const REPORT = "# Final Report\n## What happened\nI reviewed **12** conversations and added `3` notes.";

  it("an unattended run's row flattens its summary with the Schedules row's own helper", () => {
    const item = toActivity(
      ev("session.completed", { origin: "schedule:memory-review-weekly", status: "completed", ok: true, outcome: "completed", summary: REPORT }, "sess_1"),
    )!;
    expect(item.body).toBe("Final Report What happened I reviewed 12 conversations and added 3 notes.");
    expect(item.body).toBe(scheduleDetail(REPORT));
    expect(item.body).toBe(summaryLine(REPORT));
    // An older row that flattened the report onto ONE line reads the same way.
    const flat = toActivity(
      ev("session.completed", { origin: "schedule:memory-review-weekly", status: "completed", ok: true, outcome: "completed", summary: "# Final Report ## What happened I reviewed **12** notes." }, "sess_2"),
    )!;
    expect(flat.body).not.toMatch(/[#*`]/);
  });

  it("a mission's row flattens the coordinator's report too", () => {
    const item = toActivity(
      ev("session.completed", { origin: "job:mission", status: "completed", ok: true, outcome: "completed", summary: REPORT }, "m1"),
    )!;
    expect(item.body).not.toMatch(/[#*`]/);
    expect(item.body).toBe(scheduleDetail(REPORT));
  });

  it("the open bell draws the flattened words, never the markdown", async () => {
    eventsRef.current = [
      ev("session.completed", { origin: "schedule:memory-review-weekly", status: "completed", ok: true, outcome: "completed_with_failures", summary: REPORT }, "sess_3", "e-weekly"),
    ];
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
    const title = await screen.findByText("Your schedule memory-review-weekly finished, but part of it failed");
    const rowEl = title.closest("li") as HTMLElement;
    expect(rowEl.textContent).toContain("Final Report What happened I reviewed 12 conversations");
    expect(rowEl.textContent).not.toMatch(/#|\*\*/);
  });

  it("the Schedules row and the bell share ONE flattening helper (no second copy)", () => {
    const read = (rel: string) => readFileSync(join(__dirname, "..", rel), "utf-8").replace(/\r\n/g, "\n");
    const schedule = read("lib/scheduleOutcome.ts");
    const bell = read("components/NotificationBell.tsx");
    expect(schedule).toMatch(/import \{ summaryLine \} from "@\/lib\/plainText";/);
    expect(bell).toMatch(/import \{ summaryLine \} from "@\/lib\/plainText";/);
    // The bell is on every page: it must not pull the chat's Markdown renderer.
    expect(bell).not.toMatch(/from "@\/components\/Markdown"/);
    expect(read("lib/plainText.ts")).not.toMatch(/^import /m);
  });
});

/* ===================================== (5) theme tokens, never hues */

describe("(5) the bell draws its rows with theme tone tokens", () => {
  it("the source carries no literal palette hue class", () => {
    const src = readFileSync(join(__dirname, "..", "components", "NotificationBell.tsx"), "utf-8").replace(/\r\n/g, "\n");
    const hits = src.split("\n").filter((line) => LITERAL_HUE.test(line));
    expect(hits).toEqual([]);
  });

  it("the open bell's ask rows (chat, website action, agent) wear the warn token, no literal hue", async () => {
    H.cuPending = 1;
    H.approvals = [
      { id: "chat:th-1", kind: "chat", thread_id: "th-1", tool: "", requested_at: "2026-10-19T07:00:00Z", mission_id: null, project_id: null },
      { id: "apr_1", tool: "write_file", session_id: "session_7e5621fb449b", requested_at: "2026-10-19T07:00:00Z", can_always: true },
    ];
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
    const chat = await screen.findByTestId("bell-chat-waiting");
    const agent = await screen.findByTestId("bell-agent-approval");
    const website = (await screen.findByTestId("bell-website-action")).closest("li") as HTMLElement;
    for (const rowEl of [chat, agent, website]) {
      const classes = Array.from(rowEl.querySelectorAll("[class]")).map((el) => el.getAttribute("class") ?? "");
      expect(classes.filter((c) => LITERAL_HUE.test(c))).toEqual([]);
      expect(classes.some((c) => /\btext-tone-warn\b/.test(c))).toBe(true);
    }
  });
});

/* ============================ (7) approval tooltips say the tool in words */

describe("(7) the bell's approval buttons name the tool in words, never its raw id", () => {
  it("'Allow for this run' and 'Always allow exactly this' say what the tool does", async () => {
    H.approvals = [
      { id: "apr_1", tool: "write_file", session_id: "session_7e5621fb449b", requested_at: "2026-10-19T07:00:00Z", can_always: true },
    ];
    render(<NotificationBell />);
    fireEvent.click(await screen.findByRole("button", { name: /notification/i }));
    const rowEl = await screen.findByTestId("bell-agent-approval");
    const wide = within(rowEl).getByRole("button", { name: /allow for this run/i });
    const always = within(rowEl).getByTestId("bell-approval-always");
    for (const btn of [wide, always]) {
      const title = btn.getAttribute("title") ?? "";
      expect(title).toContain("save a file");
      expect(title).not.toContain("write_file");
    }
  });
});
