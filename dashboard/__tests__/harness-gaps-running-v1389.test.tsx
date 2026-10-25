/**
 * Harness gaps, fix 1: a Build coding session's reply that is RUNNING is seen
 * outside Build (the v1.388.0 re-audit, 0g: Running now said "Nothing is
 * running right now." and the orb sat "Idle — nothing running" while a reply
 * ran for an hour).
 *
 *  - lib/runningNow: GET /chat/turns/running's Build rows become "Coding
 *    session · 12 min" rows named by the session's title, opened on that
 *    session in Build; a chat's reply there is NOT listed a second time (the
 *    chat list already has it); a waiting one goes first;
 *  - the REAL RunningNow reads the route while open and lists the row; an
 *    older Iron Jarvis without the route (404) lists the rest as before;
 *  - the REAL MoodOrb turns "working" for a chat's or a coding session's
 *    reply, read from the ONE shared /health poll (no request of its own).
 *
 * Only the network (lib/api), the daemon hook and the /events socket are
 * doubles.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => ({
  gets: [] as string[],
  replies: {} as Record<string, unknown>,
  health: null as unknown,
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

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    health: H.health,
    checking: false,
    epoch: 0,
    refresh: () => {},
    provided: true,
  }),
}));

import { RunningNow } from "@/components/RunningNow";
import { MoodOrb, repliesRunning } from "@/components/MoodOrb";
import { EventsHub, EventsProvider } from "@/lib/useEvents";
import { __resetChatList } from "@/lib/chatList";
import { __resetApiCache } from "@/lib/apiCache";
import { BUILD_SESSION_EVENT } from "@/lib/buildLinks";
import { CODING_SESSION_LABEL } from "@/lib/buildWords";
import {
  RUNNING_REPLIES_PATH,
  RUNNING_SESSIONS_PATH,
  buildReplyItems,
  runningItems,
  type RunningReplyRow,
} from "@/lib/runningNow";

const W = { timeout: 5_000 };
const MIN = 60_000;

class FakeWS {
  static instances: FakeWS[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(public url: string) {
    FakeWS.instances.push(this);
  }
  close() {}
}

/** An ISO UTC stamp `ms` ago, the way the route says one. */
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();

const BUILD: RunningReplyRow = {
  turn_id: "bt_1",
  thread_id: "s-cart",
  surface: "build",
  started_at: ago(12 * MIN + 5_000),
  waiting: false,
  title: "Add a cart page",
};
const CHAT_REPLY: RunningReplyRow = {
  turn_id: "ct_1",
  thread_id: "t-run",
  surface: "chat",
  started_at: ago(MIN),
  waiting: false,
  title: "Draft the memo",
};

function wrap(ui: React.ReactElement) {
  return render(<EventsProvider hub={new EventsHub()}>{ui}</EventsProvider>);
}

beforeEach(() => {
  H.gets.length = 0;
  H.replies = {};
  H.health = null;
  FakeWS.instances = [];
  window.localStorage.clear();
  __resetChatList();
  __resetApiCache();
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
  vi.stubGlobal("WebSocket", FakeWS);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
  delete (document as unknown as Record<string, unknown>).visibilityState;
  delete (document as unknown as Record<string, unknown>).hidden;
});

describe("lib/runningNow: a coding session's reply is a row", () => {
  it("'Coding session · 12 min', named by its title, opened on that session in Build", () => {
    const now = Date.parse("2026-10-25T12:00:00Z");
    const [item] = buildReplyItems([{ ...BUILD, started_at: "2026-10-25T11:47:55Z" }], now);
    expect(item.kind).toBe("build");
    expect(item.line).toBe(`${CODING_SESSION_LABEL} · 12 min`);
    expect(item.line).toBe("Coding session · 12 min");
    expect(item.detail).toBe("Add a cart page");
    expect(item.href).toBe("/terminals?session=s-cart");
  });

  it("a chat's reply is not listed twice (the chat list has it); one with no session is left out", () => {
    const items = runningItems([], [{ id: "t-run", title: "Draft the memo", running: true }], Date.now(), [
      CHAT_REPLY,
      BUILD,
      { ...BUILD, turn_id: "bt_x", thread_id: "" },
    ]);
    expect(items.map((i) => i.key)).toEqual(["c:t-run", "b:bt_1"]);
  });

  it("a coding session parked on a card goes first and says so", () => {
    const items = runningItems([], [], Date.now(), [
      BUILD,
      { ...BUILD, turn_id: "bt_2", thread_id: "s-wait", title: "Fix the login", waiting: true },
    ]);
    expect(items[0].key).toBe("b:bt_2");
    expect(items[0].waiting).toBe(true);
    expect(items[0].line).toMatch(/^Coding session · .* · Waiting on you$/);
  });
});

describe("Running now lists a running coding session", () => {
  function seed(replies: unknown) {
    H.replies[RUNNING_SESSIONS_PATH] = { sessions: [] };
    H.replies["/chat/threads"] = { threads: [] };
    H.replies["/projects"] = { projects: [] };
    if (replies !== undefined) H.replies[RUNNING_REPLIES_PATH] = replies;
  }

  it("reads the running replies and lists the session by its title, with a link that opens it", async () => {
    seed({ replies: [BUILD] });
    wrap(<RunningNow />);
    const row = await screen.findByText(/^Coding session · 12 min$/, {}, W);
    const link = row.closest("a")!;
    expect(link.getAttribute("href")).toBe("/terminals?session=s-cart");
    expect(link.getAttribute("data-kind")).toBe("build");
    expect(within(link).getByText("Add a cart page")).toBeTruthy();
    expect(screen.queryByTestId("running-now-empty")).toBeNull();
    expect(H.gets).toContain(RUNNING_REPLIES_PATH);
  });

  it("pressed while Build is already open, the row announces the session (Next keeps the page on a query change)", async () => {
    seed({ replies: [BUILD] });
    window.history.replaceState(null, "", "/terminals");
    const heard: string[] = [];
    const on = (e: Event) => heard.push(String((e as CustomEvent<{ id: string }>).detail?.id));
    window.addEventListener(BUILD_SESSION_EVENT, on);
    try {
      wrap(<RunningNow />);
      const row = await screen.findByText(/^Coding session · 12 min$/, {}, W);
      fireEvent.click(row.closest("a")!);
      expect(heard).toEqual(["s-cart"]);
    } finally {
      window.removeEventListener(BUILD_SESSION_EVENT, on);
    }
  });

  it("an older Iron Jarvis without the route lists the rest as before and says nothing runs", async () => {
    seed(undefined); // GET /chat/turns/running → 404
    wrap(<RunningNow />);
    expect((await screen.findByTestId("running-now-empty", {}, W)).textContent).toBe("Nothing is running right now.");
  });
});

describe("the status orb turns for a reply being written", () => {
  it("reads the replies from /health: a coding session's or a chat's makes it 'working'", () => {
    expect(repliesRunning(null)).toBe(0);
    expect(repliesRunning({ replies: { chat: 0, build: 1 } } as never)).toBe(1);
    expect(repliesRunning({ replies: { chat: 2 } } as never)).toBe(2);
    expect(repliesRunning({ replies: { chat: "x" } } as never)).toBe(0);
  });

  it("idle with nothing running; working while a coding session answers; working while a chat answers", async () => {
    H.health = { status: "ok", version: "x", default_provider: "mock", default_model: "", providers: [], replies: { chat: 0, build: 0 } };
    const view = wrap(<MoodOrb onPress={() => {}} />);
    const orb = screen.getByTestId("mood-orb");
    expect(orb.getAttribute("data-mood")).toBe("idle");
    expect(orb.getAttribute("aria-label")).toBe("Running now: Idle — nothing running");

    H.health = { ...(H.health as object), replies: { chat: 0, build: 1 } };
    view.rerender(
      <EventsProvider hub={new EventsHub()}>
        <MoodOrb onPress={() => {}} />
      </EventsProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("mood-orb").getAttribute("data-mood")).toBe("thinking"), W);
    expect(screen.getByTestId("mood-orb").getAttribute("aria-label")).toBe("Running now: Working — something is running");

    H.health = { ...(H.health as object), replies: { chat: 1, build: 0 } };
    view.rerender(
      <EventsProvider hub={new EventsHub()}>
        <MoodOrb onPress={() => {}} />
      </EventsProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("mood-orb").getAttribute("data-mood")).toBe("thinking"), W);
    // No request of its own: the one shared /health poll carries the count.
    expect(H.gets).toEqual([]);
  });
});
