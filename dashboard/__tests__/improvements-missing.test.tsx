// @vitest-environment jsdom
/**
 * Next leaps 20-a (build-out plan R2.12, idea 20 "Every 'I can't' becomes a
 * suggestion"): the things you asked for that Iron Jarvis can't do yet, on
 * Improvements.
 *
 *  - the group "Things you asked for that Iron Jarvis can't do yet", each
 *    item "Asked 3 times, no tool: send a fax" with one line per ask (its
 *    date and your own words), as the daemon wrote them;
 *  - "Keep as a note for the next version" presses the item's own route
 *    (`/capability/misses/<id>/note`), "Not needed" its `/decline`, and the
 *    page says what the press did (nothing was sent anywhere);
 *  - an address that is not one of those two routes is no decision at all.
 *
 * The real ImprovementsInbox; only the network and the event socket are
 * stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  inbox: { items: [], groups: [], total: 0, unreadable: [], results: [] } as unknown,
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
    get: vi.fn(async (path: string) =>
      JSON.parse(JSON.stringify(path === "/improvements/night" ? { offer: null, night: null } : H.inbox)),
    ),
    post: vi.fn(async (path: string) => {
      H.posts.push(path);
      return { status: "ok" };
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
import { acceptedWords, decodeInbox, isDecisionPath } from "@/lib/improvements";

const GID = "missgroup_0a1b2c3d4e5f";
const LINES = [
  "3 November: “Can you fax this form to my accountant?”",
  "12 November: “I need the signed letter sent by fax to the bank”",
  "20 November: “please fax the 1040 copy to the office”",
];

function missing(extra: Record<string, unknown> = {}) {
  return {
    id: `missing:${GID}`,
    kind: "missing",
    ref: GID,
    title: "Asked 3 times, no tool: send a fax",
    about: "",
    why: "Each time, Iron Jarvis looked for a feature for it and found none.",
    evidence: LINES,
    created_at: "2026-11-20T10:00:00",
    detail: null,
    accept: {
      label: "Keep as a note for the next version",
      method: "POST",
      path: `/capability/misses/${GID}/note`,
      body: {},
      note: "Keeps it on this PC as a note for the next version of Iron Jarvis. Nothing is sent anywhere.",
    },
    decline: { label: "Not needed", method: "POST", path: `/capability/misses/${GID}/decline`, body: {} },
    home: null,
    extra: { count: 3, asked: ["3 November", "12 November", "20 November"] },
    ...extra,
  };
}

function inbox(items: unknown[]) {
  return {
    items,
    groups: items.length
      ? [{ kind: "missing", label: "Things you asked for that Iron Jarvis can't do yet", count: items.length, shown: items.length }]
      : [],
    total: items.length,
    unreadable: [],
    results: [],
  };
}

beforeEach(() => {
  H.posts = [];
});

afterEach(() => {
  cleanup();
});

describe("things you asked for that Iron Jarvis can't do yet", () => {
  it("show the count, the request, and each ask's date and your words", async () => {
    H.inbox = inbox([missing()]);
    render(<ImprovementsInbox />);
    const group = await screen.findByTestId("improvements-group-missing");
    expect(within(group).getByText("Asked 3 times, no tool: send a fax")).toBeTruthy();
    expect(group.textContent).toContain("Things you asked for that Iron Jarvis can't do yet");
    const lines = within(group).getByTestId("improvement-evidence").querySelectorAll("li");
    expect(Array.from(lines).map((l) => l.textContent)).toEqual(LINES);
    expect(within(group).getByTestId("improvement-accept").textContent).toBe("Keep as a note for the next version");
    expect(within(group).getByTestId("improvement-decline").textContent).toBe("Not needed");
    expect(within(group).queryByTestId("improvement-home")).toBeNull();
  });

  it("keep as a note presses its own route and says nothing was sent", async () => {
    H.inbox = inbox([missing()]);
    render(<ImprovementsInbox />);
    const keep = await screen.findByTestId("improvement-accept");
    H.inbox = inbox([]);
    fireEvent.click(keep);
    await waitFor(() => expect(H.posts).toEqual([`/capability/misses/${GID}/note`]));
    await waitFor(() => expect(screen.getByTestId("improvements-note").textContent).toBe(acceptedWords("missing")));
    expect(acceptedWords("missing")).toContain("Nothing was sent anywhere");
  });

  it("not needed presses its decline and takes it off the list", async () => {
    H.inbox = inbox([missing()]);
    render(<ImprovementsInbox />);
    const no = await screen.findByTestId("improvement-decline");
    H.inbox = inbox([]);
    fireEvent.click(no);
    await waitFor(() => expect(H.posts).toEqual([`/capability/misses/${GID}/decline`]));
    await waitFor(() => expect(screen.queryByTestId("improvements-group-missing")).toBeNull());
  });
});

describe("its decisions are its own two routes only", () => {
  it("accepts note and decline, nothing else", () => {
    expect(isDecisionPath(`/capability/misses/${GID}/note`)).toBe(true);
    expect(isDecisionPath(`/capability/misses/${GID}/decline`)).toBe(true);
    expect(isDecisionPath(`/capability/misses/${GID}/delete`)).toBe(false);
    expect(isDecisionPath(`/capability/misses/..%2Fx/note`)).toBe(false);
    expect(isDecisionPath(`/capability/misses/a\\..\\b/note`)).toBe(false);
  });

  it("an item whose address is some other route is shown with no button", () => {
    const got = decodeInbox(inbox([missing({ accept: { label: "Keep", method: "POST", path: "/settings", body: {} } })]));
    expect(got.items).toHaveLength(1);
    expect(got.items[0].accept).toBeNull();
    expect(got.items[0].decline?.path).toBe(`/capability/misses/${GID}/decline`);
  });
});
