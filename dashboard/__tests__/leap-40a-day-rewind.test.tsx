/**
 * Next leaps 40-a (master plan R2.18, idea 40 "Rewind the day"): Your day.
 *
 *  - "Your day" is a TAB on the Activity page (`?view=day`), beside the
 *    Timeline; on it the Timeline is not mounted (nothing of it reads).
 *  - What cannot be undone is listed FIRST, apart, with why and no Undo.
 *  - The day is grouped by where each change came from, then by hour.
 *  - Tick several: the ONE main button is "Undo these (2)"; its ONE
 *    confirmation lists every change by name (D-40.1); then the press sends
 *    those ids and the result names what was not put back.
 *  - A row's Undo takes two presses, like the Timeline's.
 *  - Before and after: a text file's difference; other changes two sides,
 *    Before and Now, that STACK at 390 px (grid-cols-1 sm:grid-cols-2).
 *  - While TODAY is on screen a new change refreshes it after a pause; an
 *    older day never refreshes by itself (G7).
 *  - The words are the canon's (VOCABULARY.md).
 *
 * The REAL page, DayRewind and BeforeAfter; only the transport is mocked.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    day: {} as Record<string, unknown>,
    ba: {} as Record<string, unknown>,
    undo: {} as Record<string, unknown>,
    events: [] as { id: string; type: string; payload: Record<string, unknown>; session_id: null; ts: string }[],
    timelineMounted: 0,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: hooks.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  get: vi.fn((path: string) => {
    hooks.gets.push(path);
    if (path.startsWith("/changes/day")) return Promise.resolve(hooks.day);
    if (path.endsWith("/before-after")) return Promise.resolve(hooks.ba);
    return Promise.resolve({});
  }),
  post: vi.fn((path: string, body?: unknown) => {
    hooks.posts.push({ path, body });
    if (path === "/changes/undo") return Promise.resolve(hooks.undo);
    return Promise.resolve({ ok: true });
  }),
  put: vi.fn(() => Promise.resolve({})),
  patch: vi.fn(() => Promise.resolve({})),
  del: vi.fn(() => Promise.resolve({})),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: hooks.events, connected: true }) }));
vi.mock("@/components/TimeTravelFeed", () => ({
  TimeTravelFeed: () => {
    hooks.timelineMounted += 1;
    return <div data-testid="timeline-feed" />;
  },
}));
vi.mock("@/components/SafetyChecks", () => ({ SafetyChecksCard: () => <div /> }));
vi.mock("@/components/AgentHistory", () => ({ AgentHistoryCard: () => <div /> }));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
// 40-a review: the page follows its address when a link inside the app
// changes it (Next's router updates useSearchParams, with no popstate).
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {}, replace: () => {}, prefetch: () => {}, back: () => {} }),
  usePathname: () => "/activity",
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

import ActivityPage from "@/app/activity/page";
import { CANNOT_LINE, DayRewind } from "@/components/activity/DayRewind";
import { DAY_REFRESH_MS, shiftDay, todayLocal, viewFromSearch } from "@/lib/dayChanges";

const DASH = join(__dirname, "..");
const TODAY = todayLocal();

function seedDay(date = TODAY) {
  hooks.day = {
    date,
    words: "Saturday 22 November",
    today: date === TODAY,
    count: 3,
    more: false,
    cannot: [
      {
        id: "tool_sent", ids: ["tool_sent"], at: "2026-11-22T20:04:00+00:00", clock: "3:04 pm", first_clock: "3:04 pm",
        what: "sent a notification", why: "it left this PC (a message sent)", count: 1,
        source: { kind: "run", name: "Tidy the reports", id: "session_1", href: "/sessions/session_1" },
      },
    ],
    groups: [
      {
        source: { kind: "run", name: "Tidy the reports", id: "session_1", href: "/sessions/session_1", started: null },
        rows: [
          { id: "tool_b", at: "x", hour: 15, clock: "3:03 pm", kind: "file", target: "summary.txt",
            what: "made 1 file: summary.txt", state: "can", why: "", has_before_after: true },
          { id: "tool_a", at: "x", hour: 15, clock: "3:02 pm", kind: "file", target: "report.txt",
            what: "made 1 file: report.txt", state: "can", why: "", has_before_after: true },
        ],
      },
      {
        source: { kind: "settings", name: "On a settings page", id: "settings", href: "/settings", started: null },
        rows: [
          { id: "tool_s", at: "x", hour: 9, clock: "9:10 am", kind: "setting", target: "the setting chat_followups",
            what: "changed the setting chat_followups", state: "cannot", why: "no copy of how it was is kept any more",
            has_before_after: true },
        ],
      },
    ],
  };
}

beforeEach(() => {
  hooks.gets.length = 0;
  hooks.posts.length = 0;
  hooks.events = [];
  hooks.timelineMounted = 0;
  hooks.ba = {};
  hooks.undo = {};
  seedDay();
  window.history.replaceState(null, "", "/activity");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("Your day is a tab on Activity", () => {
  it("opens from ?view=day, and the Timeline is not mounted on it", async () => {
    window.history.replaceState(null, "", "/activity?view=day");
    render(<ActivityPage />);
    const tab = await screen.findByTestId("activity-view-day");
    expect(tab.getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("activity-view-timeline").textContent).toContain("Timeline");
    expect(tab.textContent).toContain("Your day");
    await screen.findByTestId("day-rewind");
    expect(hooks.gets.some((g) => g === `/changes/day?date=${TODAY}`)).toBe(true);
    expect(screen.queryByTestId("timeline-feed")).toBeNull();
    expect(hooks.timelineMounted).toBe(0);
    fireEvent.click(screen.getByTestId("activity-view-timeline"));
    await screen.findByTestId("timeline-feed");
    expect(window.location.search).toBe("");
  });

  it("the Timeline stays the first view, and choosing Your day writes the address", async () => {
    render(<ActivityPage />);
    await screen.findByTestId("timeline-feed");
    fireEvent.click(screen.getByTestId("activity-view-day"));
    await screen.findByTestId("day-rewind");
    expect(window.location.search).toBe("?view=day");
    expect(viewFromSearch("?view=day&date=2026-11-20")).toEqual({ view: "day", date: "2026-11-20" });
    expect(viewFromSearch("?view=day&date=nope").date).toBeNull();
  });

  it("a link inside the app to ?view=day switches the tab, as Ctrl K \"your day\" does from the Timeline", async () => {
    const { rerender } = render(<ActivityPage />);
    await screen.findByTestId("timeline-feed");
    // A router push: the address changes with no popstate and no new mount.
    act(() => window.history.pushState(null, "", "/activity?view=day"));
    rerender(<ActivityPage />);
    await screen.findByTestId("day-rewind");
    expect(screen.getByTestId("activity-view-day").getAttribute("aria-selected")).toBe("true");
    // Ctrl K's own row for the tab opens it by its address; the Activity
    // entry keeps its own words.
    const palette = readFileSync(join(DASH, "components", "CommandPalette.tsx"), "utf-8");
    const row = palette.slice(palette.indexOf('id: "deep:your-day"'));
    expect(row.slice(0, 600)).toContain('href: "/activity?view=day"');
    expect(row.slice(0, 600)).toContain('"your day"');
    const nav = readFileSync(join(DASH, "lib", "nav.ts"), "utf-8");
    expect(nav).not.toContain('"your day"');
    // The tab's own address is written so Next's router follows it (with
    // Next's state, its patched replaceState skips the router — seen live).
    const page = readFileSync(join(DASH, "app", "activity", "page.tsx"), "utf-8");
    expect(page).toContain('window.history.replaceState(null, "",');
    expect(page).not.toContain("replaceState(window.history.state");
  });
});

describe("the day", () => {
  it("lists what cannot be undone first, then each place by hour", async () => {
    render(<DayRewind date={TODAY} onDate={() => {}} />);
    const cannot = await screen.findByTestId("day-cannot");
    expect(cannot.textContent).toContain("Can't be undone");
    expect(cannot.textContent).toContain("sent a notification");
    expect(cannot.textContent).toContain("it left this PC (a message sent)");
    // 40-a review: the heading is true of every row (not all left this PC).
    expect(cannot.textContent).toContain(CANNOT_LINE);
    expect(CANNOT_LINE).not.toContain("left this PC");
    expect(within(cannot).queryByRole("checkbox")).toBeNull();
    const groups = screen.getAllByTestId("day-group");
    expect(cannot.compareDocumentPosition(groups[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(groups[0].textContent).toContain("Tidy the reports");
    expect(groups[0].textContent).toContain("Agent run");
    expect(groups[0].textContent).toContain("3 pm");
    expect(within(groups[0]).getByText("Tidy the reports").getAttribute("href")).toBe("/sessions/session_1");
    // A change whose copy is gone: said, and no tick and no Undo.
    const gone = within(groups[1]).getByTestId("day-change");
    expect(gone.getAttribute("data-state")).toBe("cannot");
    expect(gone.textContent).toContain("Can't be undone: no copy of how it was is kept any more");
    expect(within(gone).queryByTestId("day-change-tick")).toBeNull();
    expect(within(gone).queryByTestId("day-change-undo")).toBeNull();
  });

  it("Undo these: one confirmation that lists every change by name, then those ids go", async () => {
    hooks.undo = {
      reverted: [{ id: "tool_b", target: "summary.txt", what: "made 1 file: summary.txt", kind: "file" }],
      skipped: [{ id: "tool_a", target: "report.txt", what: "", kind: "file", reason: "it was changed again since" }],
      words: "1 file put back. Not put back: report.txt (it was changed again since).",
    };
    render(<DayRewind date={TODAY} onDate={() => {}} />);
    await screen.findAllByTestId("day-change");
    expect(screen.queryByTestId("day-undo-these")).toBeNull();
    for (const box of screen.getAllByTestId("day-change-tick")) fireEvent.click(box);
    const main = screen.getByTestId("day-undo-these");
    expect(main.textContent).toBe("Undo these (2)");
    fireEvent.click(main);
    const list = await screen.findByTestId("day-confirm-list");
    expect(list.textContent).toContain("summary.txt");
    expect(list.textContent).toContain("report.txt");
    expect(hooks.posts).toEqual([]); // nothing goes before the confirmation
    fireEvent.click(screen.getByTestId("day-confirm-go"));
    await waitFor(() => expect(hooks.posts.map((p) => p.path)).toEqual(["/changes/undo"]));
    expect(hooks.posts[0].body).toEqual({ action_ids: ["tool_b", "tool_a"] });
    const result = await screen.findByTestId("day-result");
    expect(result.textContent).toContain("Not put back: report.txt");
  });

  it("Cancel sends nothing", async () => {
    render(<DayRewind date={TODAY} onDate={() => {}} />);
    await screen.findAllByTestId("day-change");
    fireEvent.click(screen.getAllByTestId("day-change-tick")[0]);
    fireEvent.click(screen.getByTestId("day-undo-these"));
    fireEvent.click(await screen.findByTestId("day-confirm-cancel"));
    expect(hooks.posts).toEqual([]);
  });

  it("a row's Undo takes two presses", async () => {
    hooks.undo = { reverted: [{ id: "tool_b", target: "summary.txt", kind: "file" }], skipped: [], words: "1 file put back." };
    render(<DayRewind date={TODAY} onDate={() => {}} />);
    const undo = (await screen.findAllByTestId("day-change-undo"))[0];
    fireEvent.click(undo);
    expect(undo.textContent).toContain("Confirm undo?");
    expect(hooks.posts).toEqual([]);
    fireEvent.click(undo);
    await waitFor(() => expect(hooks.posts).toEqual([{ path: "/changes/undo", body: { action_ids: ["tool_b"] } }]));
    expect((await screen.findByTestId("day-result")).textContent).toBe("1 file put back.");
  });

  it("the date moves a day at a time, never past today", async () => {
    const onDate = vi.fn();
    render(<DayRewind date={TODAY} onDate={onDate} />);
    await screen.findAllByTestId("day-group");
    expect((screen.getByTestId("day-next") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByTestId("day-previous"));
    expect(onDate).toHaveBeenCalledWith(shiftDay(TODAY, -1));
    expect((screen.getByTestId("day-date") as HTMLInputElement).max).toBe(TODAY);
  });

  it("an empty day says so", async () => {
    hooks.day = { date: TODAY, words: "x", today: true, groups: [], cannot: [], count: 0, more: false };
    render(<DayRewind date={TODAY} onDate={() => {}} />);
    expect((await screen.findByTestId("empty-state")).textContent).toContain("Iron Jarvis changed nothing on this day.");
  });
});

describe("before and after", () => {
  it("a text file shows its difference; a setting two sides that stack at 390 px", async () => {
    hooks.ba = {
      id: "tool_b", kind: "file", what: "made 1 file", note: "",
      items: [{ name: "summary.txt", text: true, cut: false,
        diff: ["--- summary.txt (before)", "+++ summary.txt (now)", "@@ -1 +1 @@", "-old line", "+new line"],
        before: { exists: true, kept: true, size: 9 }, now: { exists: true, kept: true, size: 9, modified: "2026-11-22T20:03:00+00:00" } }],
    };
    render(<DayRewind date={TODAY} onDate={() => {}} />);
    fireEvent.click((await screen.findAllByTestId("day-change-open"))[0]);
    const diff = await screen.findByTestId("before-after-diff");
    expect(diff.textContent).toContain("-old line");
    expect(diff.textContent).toContain("+new line");
    expect(hooks.gets).toContain("/changes/tool_b/before-after");
    expect(screen.getByTestId("before-after-here").textContent).toContain("Never sent anywhere");
    cleanup();

    hooks.ba = {
      id: "tool_s", kind: "setting", what: "changed", note: "",
      items: [{ name: "Suggest follow-up questions", text: false,
        before: { exists: true, kept: true, words: "off" }, now: { exists: true, kept: true, words: "on" } }],
    };
    render(<DayRewind date={TODAY} onDate={() => {}} />);
    fireEvent.click((await screen.findAllByTestId("day-change-open"))[2]);
    const sides = await screen.findByTestId("before-after-sides");
    expect(sides.className).toContain("grid-cols-1");
    expect(sides.className).toContain("sm:grid-cols-2");
    expect(within(sides).getByTestId("before-after-before").textContent).toBe("Beforeoff");
    expect(within(sides).getByTestId("before-after-now").textContent).toBe("Nowon");
  });
});

describe("calm", () => {
  it("today refreshes after a new change, after a pause; an older day never does", async () => {
    vi.useFakeTimers();
    const { rerender } = render(<DayRewind date={TODAY} onDate={() => {}} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const reads = () => hooks.gets.filter((g) => g.startsWith("/changes/day")).length;
    expect(reads()).toBe(1);
    hooks.events = [{ id: "e1", type: "tool.executed", payload: {}, session_id: null, ts: "x" }];
    rerender(<DayRewind date={TODAY} onDate={() => {}} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DAY_REFRESH_MS - 100);
    });
    expect(reads()).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(reads()).toBe(2);

    const old = shiftDay(TODAY, -3);
    seedDay(old);
    rerender(<DayRewind date={old} onDate={() => {}} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const before = reads();
    hooks.events = [{ id: "e2", type: "action.reverted", payload: {}, session_id: null, ts: "x" }];
    rerender(<DayRewind date={old} onDate={() => {}} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(DAY_REFRESH_MS * 3);
    });
    expect(reads()).toBe(before);
  });

  it("at 390 px each row stacks and wraps: nothing has a fixed width", () => {
    const src = readFileSync(join(DASH, "components", "activity", "DayRewind.tsx"), "utf-8");
    expect(src).toContain("flex min-w-0 flex-col gap-1 sm:flex-row sm:items-center");
    expect(src).not.toMatch(/\bw-\[\d{3,}px\]/);
    expect(src).not.toMatch(/\bmin-w-\[\d{3,}px\]/);
  });

  it("the new words are in VOCABULARY.md", () => {
    const vocab = readFileSync(join(DASH, "..", "VOCABULARY.md"), "utf-8");
    for (const w of ["**Your day**", "**Undo these (3)**", "**Before and after**", "**Before**", "**Now**",
      "**Previous day**", "**Next day**", "**Can't be undone**", "**12 files put back**"]) {
      expect(vocab).toContain(w);
    }
  });
});
