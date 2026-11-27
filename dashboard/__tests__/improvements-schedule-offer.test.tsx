// @vitest-environment jsdom
/**
 * Next leaps 19-b (build-out plan R4.37, idea 19): an offer to run a
 * repeated task by itself, on Improvements, beside its skill draft.
 *
 *  - the group "Tasks Iron Jarvis could run on a schedule" (never "jobs"),
 *    the item "Run this by itself each Monday at 10am?" with its lines;
 *  - Accept takes TWO presses (it starts work by itself) and presses the
 *    offer's own route; the page then says the route's own note;
 *  - Rehearse is a quiet button: it posts only when pressed, and the report
 *    shows on the card with no Keep it (Accept makes the schedule);
 *  - an offer that was rehearsed shows its report when the page opens;
 *  - only the offer's own addresses are pressed.
 *
 * The real ImprovementsInbox and RehearsalReport; only the network and the
 * event socket are stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const REPORT = {
  id: "reh_1",
  subject: { kind: "offer", id: "offer_1", name: "bank-export-summary" },
  status: "ready",
  sentence: "This would write 1 file. Nothing was changed.",
  would: [{ kind: "write", words: "write summary.xlsx", would: "run" }],
  would_total: 1,
  refused: [],
  reads: 1,
  kept_at: null,
};

const H = vi.hoisted(() => ({
  inbox: { items: [], groups: [], total: 0, unreadable: [], results: [] } as unknown,
  posts: [] as string[],
  gets: [] as string[],
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    get: vi.fn(async (path: string) => {
      H.gets.push(path);
      if (path === "/improvements/night") return { offer: null, night: null };
      if (path.startsWith("/rehearsals/")) return JSON.parse(JSON.stringify(REPORT));
      return JSON.parse(JSON.stringify(H.inbox));
    }),
    post: vi.fn(async (path: string) => {
      H.posts.push(path);
      if (path.endsWith("/rehearse")) return JSON.parse(JSON.stringify(REPORT));
      if (path.endsWith("/accept"))
        return { ok: true, note: "Made the schedule “bank-export-summary”: it runs by itself each Monday at 10am." };
      return { ok: true };
    }),
    put: vi.fn(async () => ({})),
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import type React from "react";
import { ImprovementsInbox, PRESS_AGAIN, SECOND_PRESS_AFTER_MS } from "@/components/improvements/ImprovementsInbox";
import { decodeInbox, isDecisionPath, offerRehearse } from "@/lib/improvements";

const HEADING = "Tasks Iron Jarvis could run on a schedule";
const PATH = "/schedules/offers/offer_1";

function offer(extra: Record<string, unknown> = {}) {
  return {
    id: "schedule_offer:offer_1",
    kind: "schedule_offer",
    ref: "offer_1",
    title: "Run this by itself each Monday at 10am?",
    about: "bank-export-summary",
    why: "The 3 times you asked for this in chat were each on a Monday.",
    evidence: [
      "You asked for this 3 times, on 10, 17 and 24 November.",
      "Each time Iron Jarvis used: excel read, excel edit.",
      "Not rehearsed yet.",
    ],
    created_at: "2026-11-25T01:30:00",
    detail: { mode: "text", before: "", after: "turn the bank export into a summary sheet", label: "", cut: false },
    accept: {
      label: "Accept",
      method: "POST",
      path: `${PATH}/accept`,
      body: {},
      note: "Accepting makes a schedule that runs this by itself each Monday at 10am.",
      confirm: true,
    },
    decline: { label: "Decline", method: "POST", path: `${PATH}/decline`, body: {}, note: "" },
    home: null,
    extra: {
      rehearse: { label: "Rehearse", path: `${PATH}/rehearse`, note: "Runs it once now, changing nothing." },
      rehearsal_id: "",
    },
    ...extra,
  };
}

function inbox(items: unknown[]) {
  return {
    items,
    groups: items.length ? [{ kind: "schedule_offer", label: HEADING, count: items.length, shown: items.length }] : [],
    total: items.length,
    unreadable: [],
    results: [],
  };
}

/** Run with a clock the test moves (`tick(ms)`), then put the real one back. */
async function withClock(run: (tick: (ms: number) => void) => Promise<void>) {
  const realNow = Date.now;
  let now = realNow();
  Date.now = () => now;
  try {
    await run((ms) => {
      now += ms;
    });
  } finally {
    Date.now = realNow;
  }
}

beforeEach(() => {
  H.posts = [];
  H.gets = [];
});

afterEach(() => {
  cleanup();
});

describe("an offer to run a task by itself", () => {
  it("is listed under its heading with its lines, and nothing is pressed", async () => {
    H.inbox = inbox([offer()]);
    render(<ImprovementsInbox />);
    const group = await screen.findByTestId("improvements-group-schedule_offer");
    expect(group.textContent).toContain(HEADING);
    expect(group.textContent).not.toMatch(/\bjobs?\b/i);
    expect(within(group).getByText("Run this by itself each Monday at 10am?")).toBeTruthy();
    expect(within(group).getByTestId("improvement-accept").textContent).toBe("Accept");
    expect(within(group).getByTestId("improvement-rehearse").textContent).toBe("Rehearse");
    expect(within(group).queryByTestId("rehearsal-report-reh_1")).toBeNull();
    expect(H.posts).toEqual([]);
  });

  it("Accept takes two presses, then presses the offer's own route", async () => {
    await withClock(async (tick) => {
      H.inbox = inbox([offer()]);
      render(<ImprovementsInbox />);
      const accept = await screen.findByTestId("improvement-accept");
      fireEvent.click(accept);
      expect(accept.textContent).toBe(PRESS_AGAIN);
      expect(H.posts).toEqual([]);
      tick(SECOND_PRESS_AFTER_MS + 50);
      H.inbox = inbox([]);
      fireEvent.click(accept);
      await waitFor(() => expect(H.posts).toEqual([`${PATH}/accept`]));
      await waitFor(() =>
        expect(screen.getByTestId("improvements-note").textContent).toBe(
          "Made the schedule “bank-export-summary”: it runs by itself each Monday at 10am.",
        ),
      );
    });
  });

  it("Rehearse runs only when pressed, and its report shows with no Keep it", async () => {
    H.inbox = inbox([offer()]);
    render(<ImprovementsInbox />);
    const rehearse = await screen.findByTestId("improvement-rehearse");
    expect(H.posts).toEqual([]);
    fireEvent.click(rehearse);
    await waitFor(() => expect(H.posts).toEqual([`${PATH}/rehearse`]));
    const report = await screen.findByTestId("rehearsal-report-reh_1");
    expect(within(report).getByTestId("rehearsal-sentence-reh_1").textContent).toBe(
      "This would write 1 file. Nothing was changed.",
    );
    expect(within(report).queryByTestId("rehearsal-keep-reh_1")).toBeNull();
    expect(screen.getByTestId("improvement-accept")).toBeTruthy(); // Accept still decides
  });

  it("an offer rehearsed before shows its report when the page opens", async () => {
    H.inbox = inbox([offer({ extra: { ...offer().extra, rehearsal_id: "reh_1" } })]);
    render(<ImprovementsInbox />);
    await screen.findByTestId("rehearsal-sentence-reh_1");
    expect(H.gets).toContain("/rehearsals/reh_1");
    expect(H.posts).toEqual([]);
  });
});

describe("only the offer's own addresses are pressed", () => {
  it("accept and decline are decisions; nothing else under /schedules is", () => {
    expect(isDecisionPath(`${PATH}/accept`)).toBe(true);
    expect(isDecisionPath(`${PATH}/decline`)).toBe(true);
    expect(isDecisionPath(`${PATH}/rehearse`)).toBe(false);
    expect(isDecisionPath("/schedules/offer_1/run")).toBe(false);
    expect(isDecisionPath("/schedules/offers/a\\..\\b/accept")).toBe(false);
  });

  it("a Rehearse whose address is not the offer's own is not drawn", () => {
    const bad = offer({ extra: { rehearse: { label: "Rehearse", path: "/schedules/x/run" }, rehearsal_id: "" } });
    const got = decodeInbox(inbox([bad]));
    expect(offerRehearse(got.items[0])).toBeNull();
    const good = decodeInbox(inbox([offer()]));
    expect(offerRehearse(good.items[0])?.path).toBe(`${PATH}/rehearse`);
  });
});
