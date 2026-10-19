/**
 * v1.352.0, wave 2 lane D (G26): one click from anywhere to what is running.
 *
 * Background work used to show only as a pulsing orb (role=img, hover text,
 * not clickable) beside a footer that always read "Running · v…" — meaning
 * the APP was up — and Status said "Working on 2 tasks" without which.
 *
 *  - lib/runningNow: the rows, in the ONE origin vocabulary
 *    (OriginChip.originLabel) with elapsed minutes, opened where every list
 *    opens a run (missionLinks.sessionHref) or a chat (/chat?thread=);
 *  - the REAL AppSidebar: the footer reads "Ready"; the orb is a button that
 *    opens "Running now" (reads nothing until opened; Escape closes and gives
 *    focus back; a row closes it; a run starting while open is read at once);
 *  - the REAL Status view: its Running number opens the same list in place,
 *    and the page still reads no session list while it is closed.
 *
 * Only the network (lib/api), the router and the /events socket are doubles.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: [] as string[],
  replies: {} as Record<string, unknown>,
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
    API_BASE: "",
    ApiError,
    ijToken: () => "",
    wsUrl: (p: string) => `ws://daemon${p}`,
    get: async (path: string) => {
      H.gets.push(path);
      if (path in H.replies) {
        const r = H.replies[path];
        if (r instanceof Error) throw r;
        return r;
      }
      throw new ApiError(`unmocked GET ${path}`, 404);
    },
    post: async () => ({}),
    put: async () => ({}),
  };
});

const nav = vi.hoisted(() => ({ pathname: "/usage", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: nav.push, replace: vi.fn(), prefetch: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

import { AppSidebar } from "@/components/AppSidebar";
import { MoodOrb } from "@/components/MoodOrb";
import OverviewPage from "@/components/overview/StatusOverview";
import { EventsHub, EventsProvider } from "@/lib/useEvents";
import { __resetChatList } from "@/lib/chatList";
import { __resetApiCache } from "@/lib/apiCache";
import { missionPath } from "@/lib/mission";
import {
  MAX_RUNNING_ROWS,
  RUNNING_SESSIONS_PATH,
  elapsedWords,
  runningItems,
  sessionWho,
  type RunningSessionRow,
} from "@/lib/runningNow";

/** Every async wait gets 5 s: below the 15 s test budget (so a miss reports
 *  what it waited for), above a loaded Release runner's hops. */
const W = { timeout: 5_000 };

/* ---- fake /events socket (the idle-work-v1333 double) -------------------- */

class FakeWS {
  static instances: FakeWS[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeWS.instances.push(this);
  }
  close() {}
  open() {
    this.onopen?.();
  }
  frame(id: string, type: string, payload: Record<string, unknown> = {}, session_id: string | null = null) {
    this.onmessage?.({ data: JSON.stringify({ id, type, session_id, ts: "2026-10-18T00:00:00Z", payload }) });
  }
}
const socket = () => FakeWS.instances[FakeWS.instances.length - 1];
const count = (path: string) => H.gets.filter((g) => g === path).length;

/** A naive-UTC timestamp `ms` ago, the way the daemon stores one. */
function ago(ms: number): string {
  return new Date(Date.now() - ms).toISOString().replace("Z", "");
}
const MIN = 60_000;

function row(over: Partial<RunningSessionRow> & { id: string }): RunningSessionRow {
  return {
    status: "active",
    task: "Do the thing",
    origin: null,
    project_id: null,
    agent_type: "builder",
    agent_name: null,
    created_at: ago(5 * MIN + 5_000),
    waiting_on: null,
    ...over,
  };
}

const SCHEDULE = row({
  id: "s-sched",
  origin: "schedule:nightly-brief",
  task: "Write the nightly brief\nfrom today's notes",
  created_at: ago(3 * MIN + 5_000),
});
const MISSION = row({ id: "s-miss", origin: "job:mission", agent_type: "supervisor", project_id: "p1", task: "Ship the Q3 report" });
const DONE = row({ id: "s-done", status: "completed", origin: "schedule:old", task: "Finished long ago" });

function wrap(ui: React.ReactElement) {
  return render(<EventsProvider hub={new EventsHub()}>{ui}</EventsProvider>);
}

beforeEach(() => {
  H.gets.length = 0;
  H.replies = {};
  FakeWS.instances = [];
  nav.pathname = "/usage";
  nav.push.mockReset();
  window.localStorage.clear();
  __resetChatList();
  __resetApiCache();
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (document as unknown as Record<string, unknown>).visibilityState;
  delete (document as unknown as Record<string, unknown>).hidden;
});

/* ========================================================================== */
/* the words                                                                  */
/* ========================================================================== */

describe("lib/runningNow: the rows, in words", () => {
  it("elapsed time is minute-grained", () => {
    expect(elapsedWords(5_000)).toBe("under a minute");
    expect(elapsedWords(-30_000)).toBe("under a minute");
    expect(elapsedWords(3 * MIN + 59_000)).toBe("3 min");
    expect(elapsedWords(60 * MIN)).toBe("1 h");
    expect(elapsedWords(125 * MIN)).toBe("2 h 5 min");
    expect(elapsedWords(Number.NaN)).toBe("");
  });

  it("a schedule reads 'Schedule · nightly-brief · 3 min' and opens its run; a mission opens its mission screen", () => {
    const items = runningItems([SCHEDULE, MISSION, DONE], [], Date.now());
    const sched = items.find((i) => i.key === "s:s-sched")!;
    expect(sched.line).toBe("Schedule · nightly-brief · 3 min");
    expect(sched.href).toBe("/sessions/s-sched");
    expect(sched.detail).toBe("Write the nightly brief"); // the first line of the task
    const miss = items.find((i) => i.key === "s:s-miss")!;
    expect(miss.who).toBe("Mission");
    expect(miss.href).toBe(missionPath("s-miss", "p1"));
    // A finished run is not running.
    expect(items.some((i) => i.key === "s:s-done")).toBe(false);
  });

  it("an origin with no words names the agent instead (never a guess); the raw origin stays in the title", () => {
    const chatRun = row({ id: "s-chat", origin: "chat", agent_type: "builder" });
    expect(sessionWho(chatRun)).toBe("Builder");
    const [item] = runningItems([chatRun], []);
    expect(item.title).toBe("Started by: chat");
    // A custom agent keeps the name the user typed.
    expect(sessionWho(row({ id: "c", origin: null, agent_type: "builder", agent_name: "Ledger Lucy" }))).toBe("Ledger Lucy");
    // A supervisor that is not a mission's root is named as the agent it is
    // (only a mission's coordinator is "Jarvis" — and its origin has words).
    expect(sessionWho(row({ id: "m", origin: null, agent_type: "supervisor", agent_name: null }))).toBe("Supervisor");
  });

  it("what waits on you comes first, then what runs (newest first, chats after runs), then what is queued", () => {
    const waiting = row({ id: "w", origin: "schedule:inbox", waiting_on: { approval_id: "a1", tool: "shell" } });
    const older = row({ id: "old", origin: "reflex:watch", created_at: ago(40 * MIN) });
    const newer = row({ id: "new", origin: "reflex:ping", created_at: ago(2 * MIN) });
    const queued = row({ id: "q", status: "queued", origin: "schedule:later" });
    const items = runningItems(
      [queued, older, waiting, newer],
      [
        { id: "t-run", title: "Draft the memo", running: true },
        { id: "t-wait", title: "", waiting: true },
        { id: "t-idle", title: "Quiet one" },
      ],
    );
    expect(items.map((i) => i.key)).toEqual(["s:w", "c:t-wait", "s:new", "s:old", "c:t-run", "s:q"]);
    expect(items[0].line).toBe("Schedule · inbox · 5 min · Waiting on you");
    expect(items[1]).toMatchObject({ line: "Chat · Waiting on you", detail: "Untitled chat", href: "/chat?thread=t-wait" });
    expect(items[4]).toMatchObject({ line: "Chat · Working on it", detail: "Draft the memo", href: "/chat?thread=t-run" });
    expect(items[5].line).toBe("Schedule · later · Queued");
  });
});

/* ========================================================================== */
/* the sidebar footer                                                         */
/* ========================================================================== */

function seedRunning() {
  // The start time is taken NOW, not at file load: "3 min" must not turn
  // into "4 min" on a slow runner (a clock-relative pin, never absolute).
  H.replies[RUNNING_SESSIONS_PATH] = {
    sessions: [{ ...SCHEDULE, created_at: ago(3 * MIN + 5_000) }, MISSION, DONE],
  };
  H.replies["/chat/threads"] = {
    threads: [
      { id: "t-run", title: "Draft the memo", running: true, updated_at: ago(MIN) },
      { id: "t-idle", title: "Quiet one", updated_at: ago(MIN) },
    ],
  };
  H.replies["/projects"] = { projects: [] };
}

const orb = () => screen.getByTestId("mood-orb");

describe("the sidebar footer", () => {
  it("says Ready (the app is up), not Running", () => {
    seedRunning();
    wrap(<AppSidebar />);
    const status = screen.getByTestId("sidebar-status");
    expect(status.textContent).toContain("Ready");
    expect(status.textContent).not.toMatch(/Running/);
    expect(status.getAttribute("title")).toMatch(/^Iron Jarvis is up/);
    // Its door is unchanged.
    expect(status.getAttribute("href")).toBe("/everything#status");
  });

  it("the orb is a button that keeps its look and says what it opens", () => {
    wrap(<AppSidebar />);
    const b = orb();
    expect(b.tagName).toBe("BUTTON");
    expect(b.getAttribute("aria-label")).toMatch(/^Running now: Idle/);
    expect(b.getAttribute("aria-haspopup")).toBe("dialog");
    expect(b.getAttribute("aria-expanded")).toBe("false");
    // Same box as before (the bar's one control height) and the same drawing.
    expect(b.className).toContain("h-9 w-9");
    expect(b.className).toContain("sm:h-8 sm:w-8");
    expect(b.querySelector("svg")).not.toBeNull();
  });

  it("reads nothing until opened; then a schedule reads 'Schedule · nightly-brief · 3 min' and opens its run", async () => {
    seedRunning();
    wrap(<AppSidebar />);
    expect(count(RUNNING_SESSIONS_PATH)).toBe(0);
    fireEvent.click(orb());
    const pop = await screen.findByRole("dialog", { name: "Running now" }, W);
    expect(orb().getAttribute("aria-expanded")).toBe("true");
    expect(orb().getAttribute("aria-controls")).toBe(pop.id);
    const line = await within(pop).findByText("Schedule · nightly-brief · 3 min", {}, W);
    expect(line.closest("a")?.getAttribute("href")).toBe("/sessions/s-sched");
    // The mission opens its mission screen; the running chat opens the chat.
    const rows = within(pop).getAllByTestId("running-row");
    const hrefs = rows.map((r) => r.getAttribute("href"));
    expect(hrefs).toContain(missionPath("s-miss", "p1"));
    expect(hrefs).toContain("/chat?thread=t-run");
    // Finished runs and idle chats are not listed.
    expect(hrefs).not.toContain("/sessions/s-done");
    expect(hrefs).not.toContain("/chat?thread=t-idle");
    expect(count(RUNNING_SESSIONS_PATH)).toBe(1);
  });

  it("Escape closes it and gives focus back to the orb; the Help menu and it are never open together", async () => {
    seedRunning();
    wrap(<AppSidebar />);
    fireEvent.click(orb());
    await screen.findByRole("dialog", { name: "Running now" }, W);
    fireEvent.click(screen.getByRole("button", { name: "Help" }));
    expect(screen.queryByRole("dialog", { name: "Running now" })).toBeNull();
    expect(screen.getByRole("menu", { name: "Help" })).toBeTruthy();
    fireEvent.click(orb());
    expect(screen.queryByRole("menu", { name: "Help" })).toBeNull();
    await screen.findByRole("dialog", { name: "Running now" }, W);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "Running now" })).toBeNull();
    expect(document.activeElement).toBe(orb());
    expect(orb().getAttribute("aria-expanded")).toBe("false");
  });

  it("a press outside closes it; following a row closes it", async () => {
    seedRunning();
    wrap(
      <>
        <AppSidebar />
        <p data-testid="outside">elsewhere</p>
      </>,
    );
    fireEvent.click(orb());
    await screen.findByRole("dialog", { name: "Running now" }, W);
    fireEvent.pointerDown(screen.getByTestId("outside"));
    expect(screen.queryByRole("dialog", { name: "Running now" })).toBeNull();
    fireEvent.click(orb());
    const pop = await screen.findByRole("dialog", { name: "Running now" }, W);
    fireEvent.click(await within(pop).findByText("Schedule · nightly-brief · 3 min", {}, W));
    expect(screen.queryByRole("dialog", { name: "Running now" })).toBeNull();
  });

  it("a run that starts while it is open is read at once (the orb's own in-flight rule)", async () => {
    seedRunning();
    wrap(<AppSidebar />);
    act(() => socket().open());
    fireEvent.click(orb());
    const pop = await screen.findByRole("dialog", { name: "Running now" }, W);
    await within(pop).findByText("Schedule · nightly-brief · 3 min", {}, W);
    expect(count(RUNNING_SESSIONS_PATH)).toBe(1);
    H.replies[RUNNING_SESSIONS_PATH] = {
      sessions: [row({ id: "s-new", origin: "reflex:inbox-watch", created_at: ago(5_000) }), SCHEDULE],
    };
    act(() => socket().frame("e-new", "session.created", {}, "s-new"));
    await within(pop).findByText("Reflex · inbox-watch · under a minute", {}, W);
    expect(count(RUNNING_SESSIONS_PATH)).toBe(2);
  });

  it("nothing running says so; a failed read never says nothing is running", async () => {
    H.replies[RUNNING_SESSIONS_PATH] = { sessions: [DONE] };
    H.replies["/chat/threads"] = { threads: [] };
    H.replies["/projects"] = { projects: [] };
    const first = wrap(<AppSidebar />);
    fireEvent.click(orb());
    const pop = await screen.findByRole("dialog", { name: "Running now" }, W);
    expect((await within(pop).findByTestId("running-now-empty", {}, W)).textContent).toBe("Nothing is running right now.");
    first.unmount();
    __resetApiCache();
    __resetChatList();
    H.replies[RUNNING_SESSIONS_PATH] = new Error("offline");
    wrap(<AppSidebar />);
    fireEvent.click(orb());
    const pop2 = await screen.findByRole("dialog", { name: "Running now" }, W);
    await within(pop2).findByTestId("running-now-failed", {}, W);
    expect(within(pop2).queryByTestId("running-now-empty")).toBeNull();
  });

  it(`more than ${MAX_RUNNING_ROWS} runs: the rest are counted with a way to all runs`, async () => {
    H.replies[RUNNING_SESSIONS_PATH] = {
      sessions: Array.from({ length: MAX_RUNNING_ROWS + 3 }, (_, i) => row({ id: `s${i}`, origin: `schedule:job-${i}` })),
    };
    H.replies["/chat/threads"] = { threads: [] };
    H.replies["/projects"] = { projects: [] };
    wrap(<AppSidebar />);
    fireEvent.click(orb());
    const pop = await screen.findByRole("dialog", { name: "Running now" }, W);
    await waitFor(() => expect(within(pop).getAllByTestId("running-row")).toHaveLength(MAX_RUNNING_ROWS), W);
    const all = within(pop).getByText(/3 more running/);
    expect(all.closest("a")?.getAttribute("href")).toBe("/sessions");
  });

  it("CONTROL: the orb on its own (the system map's reactor) stays the decorative image it was", () => {
    render(<MoodOrb />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(screen.getByRole("img").getAttribute("aria-label")).toMatch(/^Idle/);
  });
});

/* ========================================================================== */
/* Everything › Status                                                        */
/* ========================================================================== */

describe("Everything › Status: the Running number opens the same list", () => {
  function seedStatus() {
    seedRunning();
    H.replies["/ui/system-map"] = { areas: [], heartbeat: [], vitals: {}, status: { running: 2 } };
    H.replies["/onboarding"] = { next_step: null, steps: [] };
  }

  it("closed, the page reads no session list; pressed, it lists which work is running", async () => {
    seedStatus();
    wrap(<OverviewPage embedded />);
    const toggle = await screen.findByTestId("status-running-toggle", {}, W);
    await waitFor(() => expect(toggle.textContent).toContain("2"), W);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // Its value box is `truncate` (overflow hidden): an OUTER focus ring
    // would be clipped away, so the ring is drawn inside the button.
    expect(toggle.parentElement?.className).toContain("truncate");
    expect(toggle.className).toContain("focus-visible:ring-inset");
    expect(H.gets.some((g) => /^\/sessions(\?|$)/.test(g))).toBe(false);
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const list = await screen.findByTestId("status-running-list", {}, W);
    expect(toggle.getAttribute("aria-controls")).toBe(list.id);
    const line = await within(list).findByText("Schedule · nightly-brief · 3 min", {}, W);
    expect(line.closest("a")?.getAttribute("href")).toBe("/sessions/s-sched");
    fireEvent.click(toggle);
    expect(screen.queryByTestId("status-running-list")).toBeNull();
  });
});
