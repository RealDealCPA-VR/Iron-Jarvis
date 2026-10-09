/**
 * v1.327.0 (calm chat B2) — the chat list grouped under projects, with a
 * status dot and a short age per chat. Pure pieces, not wired into the page:
 *
 *  lib/threadStatus.ts
 *   - unread = `updated_at` newer than this browser's last-viewed stamp for the
 *     chat (ONE localStorage key); a fresh browser starts the clock, so nothing
 *     already saved reads unread; corrupt / throwing storage reads "nothing
 *     unread" and never throws; the daemon's naive UTC times are read as UTC;
 *   - one status per chat: waiting > running > unread > idle; the chat on
 *     screen is never unread;
 *   - ages: "now", "12m", "3h", "2d".
 *
 *  components/chat/ThreadGroups.tsx
 *   - project groups (folder + name), "No project" last, newest first;
 *   - dot per status (accent / amber / violet / none), title, age;
 *   - five per group, then "Show more" (calls onShowMore, opens in place);
 *     the open chat stays visible past the cut;
 *   - rows are buttons (Tab), arrows move between rows, the open chat is
 *     marked aria-current; theme tokens only.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import ThreadGroups, {
  GROUP_LIMIT,
  NO_PROJECT_LABEL,
  groupThreads,
} from "@/components/chat/ThreadGroups";
import {
  LAST_VIEWED_KEY,
  MAX_VIEWED,
  formatAge,
  isUnread,
  markViewed,
  readLastViewed,
  threadStatus,
  threadStatuses,
  threadTime,
  type ThreadSummary,
} from "@/lib/threadStatus";

const NOW = Date.UTC(2026, 9, 9, 12, 0, 0); // 2026-10-09T12:00:00Z

/** The daemon's shape: naive UTC, no zone suffix. */
function naive(ms: number): string {
  return new Date(ms).toISOString().replace("Z", "");
}

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

function thread(id: string, agoMs: number, extra: Partial<ThreadSummary> = {}): ThreadSummary {
  return { id, title: `Chat ${id}`, project_id: null, updated_at: naive(NOW - agoMs), ...extra };
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

// --------------------------------------------------------------------------- //
// lib/threadStatus
// --------------------------------------------------------------------------- //

describe("threadStatus: ages", () => {
  it("says now, minutes, hours, days", () => {
    expect(formatAge(naive(NOW - 20_000), NOW)).toBe("now");
    expect(formatAge(naive(NOW - 12 * MIN), NOW)).toBe("12m");
    expect(formatAge(naive(NOW - 59 * MIN), NOW)).toBe("59m");
    expect(formatAge(naive(NOW - 3 * HOUR), NOW)).toBe("3h");
    expect(formatAge(naive(NOW - 2 * DAY - HOUR), NOW)).toBe("2d");
  });

  it("reads the daemon's zone-less time as UTC, not local", () => {
    // 2026-10-09T11:00:00 with no zone is 11:00 UTC: one hour before NOW.
    expect(threadTime("2026-10-09T11:00:00")).toBe(NOW - HOUR);
    expect(formatAge("2026-10-09T11:00:00", NOW)).toBe("1h");
    expect(formatAge("2026-10-09T11:00:00Z", NOW)).toBe("1h");
  });

  it("a clock that runs ahead reads now; an unreadable time reads nothing", () => {
    expect(formatAge(naive(NOW + 5 * MIN), NOW)).toBe("now");
    expect(formatAge("not a time", NOW)).toBe("");
    expect(formatAge(null, NOW)).toBe("");
  });
});

describe("threadStatus: unread stamps", () => {
  it("a fresh browser starts the clock: nothing already saved is unread", () => {
    const store = readLastViewed(NOW);
    expect(store.since).toBe(NOW);
    expect(JSON.parse(window.localStorage.getItem(LAST_VIEWED_KEY) || "{}").since).toBe(NOW);
    expect(isUnread(thread("a", HOUR), store)).toBe(false);
    // Changed AFTER the clock started, never opened here: unread.
    expect(isUnread({ ...thread("b", 0), updated_at: naive(NOW + MIN) }, store)).toBe(true);
  });

  it("opening a chat stamps it; a later change makes it unread again", () => {
    readLastViewed(NOW - DAY); // the clock started yesterday
    const t = thread("a", HOUR);
    expect(isUnread(t, readLastViewed())).toBe(true);
    markViewed("a", t.updated_at, NOW);
    expect(isUnread(t, readLastViewed())).toBe(false);
    const changed = { ...t, updated_at: naive(NOW + 5 * MIN) };
    expect(isUnread(changed, readLastViewed())).toBe(true);
  });

  it("a save that landed just before the stamp cannot make the open chat unread", () => {
    readLastViewed(NOW - DAY);
    // The daemon's clock is a little ahead of the page's.
    const t = { ...thread("a", 0), updated_at: naive(NOW + 2_000) };
    markViewed("a", t.updated_at, NOW);
    expect(isUnread(t, readLastViewed())).toBe(false);
  });

  it("keeps one key, capped at the most recent stamps", () => {
    readLastViewed(NOW - DAY);
    for (let i = 0; i < MAX_VIEWED + 5; i++) markViewed(`t${i}`, null, NOW + i);
    const raw = JSON.parse(window.localStorage.getItem(LAST_VIEWED_KEY) || "{}");
    const ids = Object.keys(raw.seen);
    expect(ids).toHaveLength(MAX_VIEWED);
    expect(ids).not.toContain("t0");
    expect(ids).toContain(`t${MAX_VIEWED + 4}`);
    expect(Object.keys(window.localStorage)).toEqual([LAST_VIEWED_KEY]);
  });

  it("corrupt storage reads nothing unread and is replaced", () => {
    window.localStorage.setItem(LAST_VIEWED_KEY, "{not json");
    const store = readLastViewed(NOW);
    expect(store.since).toBe(NOW);
    expect(isUnread(thread("a", HOUR), store)).toBe(false);
    window.localStorage.setItem(LAST_VIEWED_KEY, JSON.stringify({ since: "yesterday" }));
    expect(readLastViewed(NOW).since).toBe(NOW);
  });

  it("storage that throws never throws here", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    expect(() => readLastViewed(NOW)).not.toThrow();
    expect(() => markViewed("a", naive(NOW), NOW)).not.toThrow();
    // Unreadable stamps: the clock starts now, so nothing saved reads unread.
    expect(isUnread(thread("a", HOUR), readLastViewed(NOW))).toBe(false);
  });
});

describe("threadStatus: one status per chat", () => {
  it("waiting beats running beats unread beats idle; the open chat is never unread", () => {
    readLastViewed(NOW - DAY);
    const store = readLastViewed();
    expect(threadStatus(thread("a", MIN, { running: true, waiting: true }), store)).toBe("waiting");
    expect(threadStatus(thread("a", MIN, { running: true }), store)).toBe("running");
    expect(threadStatus(thread("a", MIN), store)).toBe("unread");
    expect(threadStatus(thread("a", MIN), store, "a")).toBe("idle");
    markViewed("a", null, NOW);
    expect(threadStatus(thread("a", MIN), readLastViewed())).toBe("idle");
  });

  it("an older daemon's rows (no flags) read as not running", () => {
    const store = readLastViewed(NOW);
    expect(threadStatuses([thread("a", HOUR), thread("b", DAY)], store)).toEqual({
      a: "idle",
      b: "idle",
    });
  });
});

// --------------------------------------------------------------------------- //
// components/chat/ThreadGroups
// --------------------------------------------------------------------------- //

const PROJECTS = [
  { id: "p1", name: "Harbor Street Cafe" },
  { id: "p2", name: "Pier Nine Lease" },
];

function renderGroups(over: Partial<Parameters<typeof ThreadGroups>[0]> = {}) {
  const onOpen = vi.fn();
  const onShowMore = vi.fn();
  const props = {
    threads: [
      thread("c1", 0, { project_id: "p1", title: "Q3 revenue by location" }),
      thread("c2", 12 * MIN, { project_id: "p1", title: "Lease options for Pier 9" }),
      thread("c3", HOUR, { project_id: "p1", title: "Supplier price list" }),
      thread("c4", 3 * DAY, { project_id: "p1", title: "Weekly staff schedule" }),
      thread("n1", 2 * DAY, { title: "Draft a thank-you email" }),
      thread("g1", 5 * HOUR, { project_id: "gone", title: "From a deleted project" }),
      thread("q1", 30 * MIN, { project_id: "p2", title: "Pier lease questions" }),
    ],
    projects: PROJECTS,
    activeId: "c1",
    onOpen,
    onShowMore,
    statuses: { c1: "running", c2: "waiting", c3: "unread", c4: "idle" } as const,
    now: NOW,
    ...over,
  };
  const utils = render(<ThreadGroups {...props} />);
  return { ...utils, onOpen, onShowMore };
}

describe("ThreadGroups", () => {
  it("groups by project, newest first, No project last", () => {
    renderGroups();
    const groups = screen
      .getAllByRole("region")
      .map((s) => s.getAttribute("data-testid"));
    expect(groups).toEqual(["thread-group-p:p1", "thread-group-p:p2", "thread-group-none"]);
    const p1 = screen.getByTestId("thread-group-p:p1");
    expect(within(p1).getByRole("heading").textContent).toBe("Harbor Street Cafe");
    expect(
      within(p1).getAllByRole("button").map((b) => b.getAttribute("data-testid")),
    ).toEqual(["thread-row-c1", "thread-row-c2", "thread-row-c3", "thread-row-c4"]);
    const none = screen.getByTestId("thread-group-none");
    expect(within(none).getByRole("heading").textContent).toBe(NO_PROJECT_LABEL);
    // A chat whose project is gone is under No project, newest first.
    expect(
      within(none).getAllByRole("button").map((b) => b.getAttribute("data-testid")),
    ).toEqual(["thread-row-g1", "thread-row-n1"]);
  });

  it("draws one dot per status in theme colours, and none when idle", () => {
    renderGroups();
    const cls = (id: string) => screen.getByTestId(`thread-dot-${id}`).className;
    expect(screen.getByTestId("thread-dot-c1").getAttribute("data-status")).toBe("running");
    expect(cls("c1")).toContain("bg-accent");
    expect(cls("c2")).toContain("bg-tone-warn");
    expect(cls("c3")).toContain("bg-tone-violet");
    expect(cls("c4")).toContain("bg-transparent");
    // A chat the statuses do not mention has no dot colour either.
    expect(screen.getByTestId("thread-dot-n1").getAttribute("data-status")).toBe("idle");
    // The status is spoken, not only coloured.
    expect(screen.getByTestId("thread-row-c2").textContent).toContain("Waiting on you");
    expect(screen.getByTestId("thread-row-c1").textContent).toContain("Working on it");
  });

  it("shows the title and a short age, and marks the open chat", () => {
    renderGroups();
    const c2 = screen.getByTestId("thread-row-c2");
    expect(c2.textContent).toContain("Lease options for Pier 9");
    expect(within(c2).getByText("12m").tagName).toBe("TIME");
    expect(within(screen.getByTestId("thread-row-c1")).getByText("now")).toBeInTheDocument();
    expect(within(screen.getByTestId("thread-row-c4")).getByText("3d")).toBeInTheDocument();
    expect(screen.getByTestId("thread-row-c1").getAttribute("aria-current")).toBe("page");
    expect(screen.getByTestId("thread-row-c2").getAttribute("aria-current")).toBeNull();
    expect(screen.getByTestId("thread-row-c1").className).toContain("bg-white/[0.07]");
    expect(screen.getByTestId("thread-row-c2").className).not.toContain("bg-white/[0.07]");
  });

  it("opens a chat on press", () => {
    const { onOpen } = renderGroups();
    fireEvent.click(screen.getByTestId("thread-row-q1"));
    expect(onOpen).toHaveBeenCalledWith("q1");
  });

  it("shows five per group, then Show more opens the rest in place", () => {
    const many = Array.from({ length: 8 }, (_, i) =>
      thread(`m${i}`, i * MIN, { project_id: "p1" }),
    );
    const { onShowMore } = renderGroups({ threads: many, activeId: null, statuses: {} });
    const group = screen.getByTestId("thread-group-p:p1");
    expect(within(group).getAllByRole("button", { name: /^Chat m/ })).toHaveLength(GROUP_LIMIT);
    expect(screen.queryByTestId("thread-row-m5")).toBeNull();
    const more = screen.getByTestId("thread-show-more-p:p1");
    expect(more.textContent).toBe("Show more");
    expect(more.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(more);
    expect(onShowMore).toHaveBeenCalledWith("p1");
    expect(within(group).getAllByRole("button", { name: /^Chat m/ })).toHaveLength(8);
    expect(screen.getByTestId("thread-show-more-p:p1").textContent).toBe("Show less");
    fireEvent.click(screen.getByTestId("thread-show-more-p:p1"));
    expect(screen.queryByTestId("thread-row-m7")).toBeNull();
    expect(onShowMore).toHaveBeenCalledTimes(1);
  });

  it("No project's Show more reports null", () => {
    const many = Array.from({ length: 6 }, (_, i) => thread(`n${i}`, i * MIN));
    const { onShowMore } = renderGroups({ threads: many, activeId: null, statuses: {} });
    fireEvent.click(screen.getByTestId("thread-show-more-none"));
    expect(onShowMore).toHaveBeenCalledWith(null);
  });

  it("five or fewer: no Show more", () => {
    renderGroups();
    expect(screen.queryByTestId("thread-show-more-p:p1")).toBeNull();
  });

  it("the open chat stays visible past the cut", () => {
    const many = Array.from({ length: 8 }, (_, i) =>
      thread(`m${i}`, i * MIN, { project_id: "p1" }),
    );
    renderGroups({ threads: many, activeId: "m7", statuses: {} });
    expect(screen.getByTestId("thread-row-m7")).toBeInTheDocument();
    expect(screen.queryByTestId("thread-row-m6")).toBeNull();
    expect(screen.getByTestId("thread-show-more-p:p1")).toBeInTheDocument();
  });

  it("is keyboard reachable: rows are buttons and the arrows move between them", () => {
    renderGroups();
    const rows = screen
      .getAllByRole("button")
      .filter((b) => b.hasAttribute("data-thread-row"));
    expect(rows.every((b) => b.tagName === "BUTTON" && b.getAttribute("type") === "button")).toBe(
      true,
    );
    rows[0].focus();
    fireEvent.keyDown(rows[0], { key: "ArrowDown" });
    expect(document.activeElement).toBe(rows[1]);
    // Across a group boundary too.
    rows[3].focus();
    fireEvent.keyDown(rows[3], { key: "ArrowDown" });
    expect(document.activeElement).toBe(rows[4]);
    fireEvent.keyDown(rows[4], { key: "ArrowUp" });
    expect(document.activeElement).toBe(rows[3]);
    rows[0].focus();
    fireEvent.keyDown(rows[0], { key: "ArrowUp" });
    expect(document.activeElement).toBe(rows[0]);
  });

  it("renders nothing for no chats", () => {
    const { container } = renderGroups({ threads: [] });
    expect(container.innerHTML).toBe("");
  });

  it("groupThreads orders groups by their newest chat", () => {
    const groups = groupThreads(
      [
        thread("a", 3 * HOUR, { project_id: "p1" }),
        thread("b", 1 * HOUR, { project_id: "p2" }),
        thread("c", 2 * HOUR, { project_id: "p1" }),
      ],
      PROJECTS,
    );
    expect(groups.map((g) => g.key)).toEqual(["p:p2", "p:p1"]);
    expect(groups[1].threads.map((t) => t.id)).toEqual(["c", "a"]);
  });
});

describe("theme tokens only", () => {
  const read = (rel: string) =>
    readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8").replace(/\r\n/g, "\n");
  const src = read("../components/chat/ThreadGroups.tsx");

  it("no literal colours and whole-pixel text sizes", () => {
    expect(src).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(src).not.toMatch(/rgba?\(/);
    expect(src).not.toMatch(/\b(?:bg|text|border)-(?:amber|violet|cyan|purple|yellow)-\d/);
    expect(src).not.toMatch(/text-\[\d+\.\d+px\]/);
  });
});
