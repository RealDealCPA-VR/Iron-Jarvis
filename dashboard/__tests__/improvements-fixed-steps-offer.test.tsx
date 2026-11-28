// @vitest-environment jsdom
/**
 * The intelligence plan, release 11: an offer to run a job you repeat as
 * FIXED STEPS (no model), on Improvements.
 *
 *  - "You do this every Monday. Run it by itself?", "It runs the same way
 *    each time, uses no model, and works offline.", the one question
 *    ("Which file? …") among its lines;
 *  - Accept cannot be pressed before the rehearsal (its reason is shown);
 *    once the rehearsal on the card has finished, changing nothing and
 *    needing no answer, ONE press presses the offer's own route;
 *  - a rehearsal that found a step that would ask keeps Accept blocked.
 *
 * The real ImprovementsInbox and RehearsalReport; only the network and the
 * event socket are stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const CLEAN = {
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
  report: null as unknown,
  posts: [] as string[],
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
      if (path === "/improvements/night") return { offer: null, night: null };
      if (path.startsWith("/rehearsals/")) return JSON.parse(JSON.stringify(H.report));
      return JSON.parse(JSON.stringify(H.inbox));
    }),
    post: vi.fn(async (path: string) => {
      H.posts.push(path);
      if (path.endsWith("/rehearse")) return JSON.parse(JSON.stringify(H.report));
      if (path.endsWith("/accept")) return { ok: true, note: "Saved the steps as the workflow “bank-export-summary”." };
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
import { ImprovementsInbox } from "@/components/improvements/ImprovementsInbox";
import { rehearsalLetsAccept } from "@/lib/improvements";

const PATH = "/schedules/offers/offer_1";
const NOT_YET = "Rehearse it first, so you see what it does before it runs by itself.";

function offer() {
  return {
    id: "schedule_offer:offer_1",
    kind: "schedule_offer",
    ref: "offer_1",
    title: "You do this every Monday. Run it by itself?",
    about: "bank-export-summary",
    why: "It runs the same way each time, uses no model, and works offline.",
    evidence: [
      "You asked for this 3 times, on 10, 17 and 24 November.",
      "Each time Iron Jarvis used: read file, convert document.",
      "Which file? Each time: the newest .csv file in “exports” in its folder.",
      "Not rehearsed yet.",
    ],
    created_at: "2026-11-25T01:30:00",
    detail: { mode: "text", before: "", after: "1. read file: the newest file", label: "What it does each time", cut: false },
    accept: {
      label: "Accept",
      method: "POST",
      path: `${PATH}/accept`,
      body: {},
      note: "Accepting saves these steps as a workflow and runs it by itself each Monday at 10am.",
      confirm: false,
      blocked: NOT_YET,
    },
    decline: { label: "Decline", method: "POST", path: `${PATH}/decline`, body: {}, note: "" },
    home: null,
    extra: {
      rehearse: { label: "Rehearse", path: `${PATH}/rehearse`, note: "Runs these steps once now, changing nothing." },
      rehearsal_id: "",
      after_rehearsal: true,
    },
  };
}

function inbox(items: unknown[]) {
  return {
    items,
    groups: items.length ? [{ kind: "schedule_offer", label: "Tasks Iron Jarvis could run on a schedule", count: 1, shown: 1 }] : [],
    total: items.length,
    unreadable: [],
    results: [],
  };
}

beforeEach(() => {
  H.posts = [];
  H.inbox = inbox([offer()]);
  H.report = CLEAN;
});

afterEach(() => {
  cleanup();
});

describe("an offer to run a job as fixed steps", () => {
  it("says what it is, and Accept waits for the rehearsal", async () => {
    render(<ImprovementsInbox />);
    expect(await screen.findByText("You do this every Monday. Run it by itself?")).toBeTruthy();
    expect(screen.getByText("It runs the same way each time, uses no model, and works offline.")).toBeTruthy();
    expect(screen.getByText("Which file? Each time: the newest .csv file in “exports” in its folder.")).toBeTruthy();
    const accept = screen.getByTestId("improvement-accept") as HTMLButtonElement;
    expect(accept.disabled).toBe(true);
    expect(screen.getByTestId("improvement-blocked").textContent).toBe(NOT_YET);
    fireEvent.click(accept);
    expect(H.posts).toEqual([]);
  });

  it("after a clean rehearsal, ONE press accepts", async () => {
    render(<ImprovementsInbox />);
    fireEvent.click(await screen.findByTestId("improvement-rehearse"));
    await screen.findByTestId("rehearsal-report-reh_1");
    const accept = screen.getByTestId("improvement-accept") as HTMLButtonElement;
    await waitFor(() => expect(accept.disabled).toBe(false));
    H.inbox = inbox([]);
    fireEvent.click(accept);
    await waitFor(() => expect(H.posts).toEqual([`${PATH}/rehearse`, `${PATH}/accept`]));
  });

  it("a rehearsal that found a step that would ask keeps Accept blocked", async () => {
    H.report = { ...CLEAN, would: [], refused: [{ kind: "write", words: "write summary.xlsx", would: "refuse" }] };
    render(<ImprovementsInbox />);
    fireEvent.click(await screen.findByTestId("improvement-rehearse"));
    await screen.findByTestId("rehearsal-report-reh_1");
    const accept = screen.getByTestId("improvement-accept") as HTMLButtonElement;
    expect(accept.disabled).toBe(true);
    expect(screen.getByTestId("improvement-blocked").textContent).toBe(NOT_YET);
  });
});

describe("rehearsalLetsAccept", () => {
  it("is true only for a finished rehearsal that changed nothing and needs no answer", () => {
    expect(rehearsalLetsAccept(CLEAN)).toBe(true);
    expect(rehearsalLetsAccept({ ...CLEAN, status: "running" })).toBe(false);
    expect(rehearsalLetsAccept({ ...CLEAN, would: [{ would: "ask" }] })).toBe(false);
    expect(rehearsalLetsAccept({ ...CLEAN, stopped_words: "It stopped where it would ask you." })).toBe(false);
    expect(rehearsalLetsAccept(null)).toBe(false);
  });
});
