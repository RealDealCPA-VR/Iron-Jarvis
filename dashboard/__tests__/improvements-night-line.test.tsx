// @vitest-environment jsdom
/**
 * 17-a (build-out plan R1.8, idea 17): the night review on Improvements and
 * in the bell.
 *
 *  - the ONE-TIME OFFER (D-17.1) shows while the owner has not answered, with
 *    the daemon's own cost words ("up to 6 requests a night on your plan");
 *    either answer is the setting itself (PUT /settings), and the card goes;
 *  - last night's line shows only when the daemon says one ("From last
 *    night's review: 3" or "Skipped: ..."), never on its own;
 *  - a suggestion the review made says why, under its title;
 *  - the bell's row: ONE a night, the daemon's words, opening Improvements;
 *    a payload that made nothing has no row.
 *
 * The real ImprovementsInbox and the real toActivity; only the network and
 * the event socket are stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => ({
  inbox: { items: [], groups: [], total: 0, unreadable: [] } as unknown,
  night: { offer: null, night: null } as unknown,
  puts: [] as { path: string; body: unknown }[],
  putError: null as Error | null,
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
    get: vi.fn(async (path: string) =>
      JSON.parse(JSON.stringify(path === "/improvements/night" ? H.night : H.inbox)),
    ),
    post: vi.fn(async () => ({ status: "ok" })),
    put: vi.fn(async (path: string, body: unknown) => {
      H.puts.push({ path, body });
      if (H.putError) throw H.putError;
      return { changed: ["night_review"] };
    }),
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
import { ImprovementsInbox } from "@/components/improvements/ImprovementsInbox";
import {
  IMPROVEMENT_EVENT_TYPES,
  NIGHT_OFFER_OFF,
  NIGHT_OFFER_ON,
  NIGHT_OFFER_YES,
  NIGHT_OFFER_NO,
  decodeNightReview,
  nightWhy,
  decodeInbox,
} from "@/lib/improvements";
import { BELL_ACTIVITY_TYPES, toActivity } from "@/components/NotificationBell";

const OFFER = { offer: { requests: 6, words: "up to 6 requests a night on your plan" }, night: null };

function coachItem(extra: Record<string, unknown> = {}) {
  return {
    id: "coach:coach_1",
    kind: "coach",
    ref: "coach_1",
    title: "Change the instructions of research",
    about: "research",
    why: "",
    evidence: [],
    created_at: "2026-11-21T06:00:00",
    detail: null,
    accept: { label: "Accept", method: "POST", path: "/coach/proposals/coach_1/accept", body: {}, note: "", confirm: false, blocked: "" },
    decline: { label: "Decline", method: "POST", path: "/coach/proposals/coach_1/decline", body: {}, note: "", confirm: false, blocked: "" },
    home: null,
    extra,
  };
}

beforeEach(() => {
  H.inbox = { items: [], groups: [], total: 0, unreadable: [] };
  H.night = { offer: null, night: null };
  H.puts = [];
  H.putError = null;
});

afterEach(() => cleanup());

describe("the wire, whitelisted", () => {
  it("an offer needs its words, a line needs its words, anything else is nothing", () => {
    expect(decodeNightReview(null)).toEqual({ offer: null, night: null });
    expect(decodeNightReview({ offer: { requests: 6 } }).offer).toBeNull();
    expect(decodeNightReview(OFFER).offer).toEqual({ requests: 6, words: "up to 6 requests a night on your plan" });
    expect(decodeNightReview({ night: { night: "2026-11-20", made: 3, waiting: 3 } }).night).toBeNull();
    const got = decodeNightReview({
      night: { night: "2026-11-20", state: "done", made: 3, waiting: 2, skipped_why: "", needs_you: false, line: "From last night's review: 2" },
    }).night;
    expect(got).toEqual({
      night: "2026-11-20", state: "done", made: 3, waiting: 2, skippedWhy: "", needsYou: false,
      line: "From last night's review: 2",
    });
  });

  it("a suggestion's reason is read off extra.night", () => {
    const inbox = decodeInbox({
      items: [coachItem({ night: { night: "2026-11-20", why: "Your agent research ran out of steps 4 times yesterday." } })],
      groups: [{ kind: "coach", label: "Agent instructions", count: 1, shown: 1 }],
      total: 1,
      unreadable: [],
    });
    expect(nightWhy(inbox.items[0])).toBe("From the night review: Your agent research ran out of steps 4 times yesterday.");
    expect(nightWhy({ ...inbox.items[0], extra: {} })).toBe("");
  });

  it("the page reads again when the review rings", () => {
    expect(IMPROVEMENT_EVENT_TYPES).toContain("improvement.night_review");
  });
});

describe("the offer", () => {
  it("names its cost and turns the review on through the setting", async () => {
    H.night = OFFER;
    render(<ImprovementsInbox />);
    const card = await screen.findByTestId("improvements-night-offer");
    expect(card.textContent).toContain("up to 6 requests a night on your plan");
    expect(card.textContent).toContain("never a model that bills per use");
    // One main button; the other answer is a quiet one.
    expect(screen.getByTestId("improvements-night-yes").textContent).toBe(NIGHT_OFFER_YES);
    expect(screen.getByTestId("improvements-night-no").textContent).toBe(NIGHT_OFFER_NO);
    H.night = { offer: null, night: null };
    fireEvent.click(screen.getByTestId("improvements-night-yes"));
    await waitFor(() => expect(H.puts).toEqual([{ path: "/settings", body: { values: { night_review: true } } }]));
    await waitFor(() => expect(screen.queryByTestId("improvements-night-offer")).toBeNull());
    expect(screen.getByTestId("improvements-note").textContent).toBe(NIGHT_OFFER_ON);
  });

  it("No thanks is an answer too", async () => {
    H.night = OFFER;
    render(<ImprovementsInbox />);
    fireEvent.click(await screen.findByTestId("improvements-night-no"));
    await waitFor(() => expect(H.puts).toEqual([{ path: "/settings", body: { values: { night_review: false } } }]));
    expect(screen.getByTestId("improvements-note").textContent).toBe(NIGHT_OFFER_OFF);
  });

  it("a refused save says so and keeps the card", async () => {
    H.night = OFFER;
    H.putError = new Error("Night review must be on or off.");
    render(<ImprovementsInbox />);
    fireEvent.click(await screen.findByTestId("improvements-night-yes"));
    expect((await screen.findByTestId("improvements-night-refused")).textContent).toBe("Night review must be on or off.");
    expect(screen.getByTestId("improvements-night-offer")).toBeTruthy();
  });

  it("is not shown once answered", async () => {
    render(<ImprovementsInbox />);
    await screen.findByTestId("improvements-inbox");
    await waitFor(() => expect(screen.queryByTestId("improvements-night-offer")).toBeNull());
  });
});

describe("last night's line and the reasons", () => {
  it("shows the daemon's line and each suggestion's reason", async () => {
    H.night = {
      offer: null,
      night: { night: "2026-11-20", state: "done", made: 1, waiting: 1, skipped_why: "", needs_you: false, line: "From last night's review: 1" },
    };
    H.inbox = {
      items: [coachItem({ night: { night: "2026-11-20", why: "Your agent research ran out of steps 4 times yesterday." } })],
      groups: [{ kind: "coach", label: "Agent instructions", count: 1, shown: 1 }],
      total: 1,
      unreadable: [],
    };
    render(<ImprovementsInbox />);
    expect((await screen.findByTestId("improvements-night-line")).textContent).toBe("From last night's review: 1");
    expect((await screen.findByTestId("improvement-night-why")).textContent).toBe(
      "From the night review: Your agent research ran out of steps 4 times yesterday.",
    );
  });

  it("shows no line when the daemon says none", async () => {
    render(<ImprovementsInbox />);
    await screen.findByTestId("improvements-inbox");
    await waitFor(() => expect(screen.queryByTestId("improvements-night-line")).toBeNull());
    expect(screen.queryByTestId("improvement-night-why")).toBeNull();
  });
});

describe("the bell", () => {
  const event = (made: number) => ({
    id: "evt_nr",
    type: "improvement.night_review",
    session_id: null,
    ts: "2026-11-21T06:10:00",
    payload: { night: "2026-11-20", made, by_kind: { coach: made }, title: `${made} suggestions from yesterday's review`, body: "Accept or decline each one on Improvements." },
  });

  it("ONE counted row a night, in the daemon's words, opening Improvements", () => {
    expect(BELL_ACTIVITY_TYPES).toContain("improvement.night_review");
    const row = toActivity(event(3) as never);
    expect(row).toMatchObject({
      title: "3 suggestions from yesterday's review",
      body: "Accept or decline each one on Improvements.",
      href: "/improvements",
      attention: true,
    });
  });

  it("a night that made nothing has no row, and a payload with no title still reads right", () => {
    expect(toActivity(event(0) as never)).toBeNull();
    const bare = { ...event(1), payload: { night: "2026-11-20", made: 1 } };
    expect(toActivity(bare as never)?.title).toBe("1 suggestion from yesterday's review");
  });
});
