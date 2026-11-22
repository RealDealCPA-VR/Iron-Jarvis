// @vitest-environment jsdom
/**
 * 18-a (build-out plan R2.8, idea 18 "Proof that a change helped"): the
 * results of changes you accepted, on Improvements.
 *
 *  - a second part UNDER the waiting suggestions, "Results of changes you
 *    accepted", from the same read (`results` on GET /improvements): each
 *    change's title and the daemon's OWN words, never a number made here;
 *  - "Not enough work yet: 3 of 5 tasks since …" is shown as it is;
 *  - a row with no words is dropped, a link out of the app is dropped;
 *  - no button: nothing in the part posts anywhere;
 *  - with nothing waiting the results still show; with no results the part
 *    is not drawn at all.
 *
 * The real ImprovementsInbox; only the network and the event socket are
 * stubbed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";

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
import { RESULTS_MAX, RESULTS_TITLE, decodeInbox, decodeResults } from "@/lib/improvements";

const IDEA = "Since you accepted this on 12 March, research finished 9 of 10 tasks without help. Before, it was 6 of 10.";
const NOT_YET = "Not enough work yet: 3 of 5 tasks since you accepted this on 2 April.";

function result(id: string, extra: Record<string, unknown> = {}) {
  return {
    id,
    kind: "coach",
    ref: id.split(":")[1] ?? id,
    target: "custom:research",
    title: "New instructions for research",
    words: IDEA,
    state: "ready",
    verdict: "better",
    accepted_at: "2026-03-12T12:00:00",
    checked_at: "2026-03-30T12:00:00",
    before: { n: 10, good: 6, words: "6 of 10 tasks without help" },
    after: { n: 10, good: 9, words: "9 of 10 tasks without help" },
    home: { href: "/agents?view=team&agent=custom%3Aresearch", label: "Open where it lives" },
    ...extra,
  };
}

const WAITING = {
  id: "skill:s1",
  kind: "skill",
  ref: "s1",
  title: "New skill: weekly-report",
  about: "weekly-report",
  why: "",
  evidence: [],
  created_at: "2026-04-01T00:00:00",
  detail: null,
  accept: { label: "Approve", method: "POST", path: "/skills/proposals/s1/approve", body: {} },
  decline: { label: "Reject", method: "POST", path: "/skills/proposals/s1/reject", body: {} },
  home: { href: "/skills", label: "Open where it lives" },
  extra: {},
};

function inbox(results: unknown[], items: unknown[] = []) {
  return {
    items,
    groups: items.length ? [{ kind: "skill", label: "Skill drafts", count: items.length, shown: items.length }] : [],
    total: items.length,
    unreadable: [],
    results,
  };
}

beforeEach(() => {
  H.posts = [];
});

afterEach(() => {
  cleanup();
});

describe("the results of changes you accepted", () => {
  it("are a second part under the waiting suggestions, in the daemon's own words", async () => {
    H.inbox = inbox(
      [result("coach:c1"), result("skill_refine:r1", { kind: "skill_refine", title: "Updated skill: sort", words: NOT_YET, state: "not_enough", verdict: "" })],
      [WAITING],
    );
    render(<ImprovementsInbox />);
    const part = await screen.findByTestId("improvements-results");
    expect(within(part).getByRole("heading").textContent).toBe(`${RESULTS_TITLE} · 2`);
    const rows = within(part).getAllByTestId("improvement-result-words").map((n) => n.textContent);
    expect(rows).toEqual([IDEA, NOT_YET]);
    // Under the waiting ones: the waiting group comes first in the page.
    const group = screen.getByTestId("improvements-group-skill");
    expect(group.compareDocumentPosition(part) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // Its own door, and no button that could change anything.
    const card = screen.getByTestId("improvement-result-coach:c1");
    expect(card.getAttribute("data-state")).toBe("ready");
    expect(within(card).getByTestId("improvement-result-home").getAttribute("href")).toBe(
      "/agents?view=team&agent=custom%3Aresearch",
    );
    expect(within(part).queryAllByRole("button")).toEqual([]);
    expect(H.posts).toEqual([]);
  });

  it("still show when nothing is waiting", async () => {
    H.inbox = inbox([result("coach:c1")]);
    render(<ImprovementsInbox />);
    await waitFor(() => expect(screen.getByTestId("improvements-results")).toBeTruthy());
    expect(screen.getByText(IDEA)).toBeTruthy();
  });

  it("are not drawn at all when there are none", async () => {
    H.inbox = inbox([], [WAITING]);
    render(<ImprovementsInbox />);
    await screen.findByTestId("improvements-group-skill");
    expect(screen.queryByTestId("improvements-results")).toBeNull();
  });
});

describe("decodeResults", () => {
  it("drops a row with no words or no title, and a link out of the app", () => {
    const out = decodeResults([
      result("coach:a"),
      result("coach:b", { words: "" }),
      result("coach:c", { title: "  " }),
      result("coach:d", { home: { href: "https://evil.example/x", label: "Open" } }),
      "junk",
      null,
    ]);
    expect(out.map((r) => r.id)).toEqual(["coach:a", "coach:d"]);
    expect(out[1].home).toBeNull();
    expect(out[0]).toMatchObject({ kind: "coach", state: "ready", verdict: "better", words: IDEA });
  });

  it("keeps at most thirty, and an older daemon's answer has none", () => {
    expect(decodeResults(Array.from({ length: 40 }, (_, i) => result(`coach:${i}`)))).toHaveLength(RESULTS_MAX);
    expect(decodeInbox({ items: [], groups: [], total: 0, unreadable: [] }).results).toEqual([]);
  });
});
