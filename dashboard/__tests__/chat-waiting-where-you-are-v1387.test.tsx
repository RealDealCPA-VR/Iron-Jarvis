/**
 * Readiness 12B (G05, the page half): a chat waiting for your OK is seen
 * WHERE YOU ARE — on another page of the same window too.
 *
 * Before: the chat page kept its stream reading after the user moved to
 * another page of the same window (so the reply still lands), the daemon read
 * the turn as watched, and the bell on the page the user went to listed
 * nothing — only the sidebar's dot said a chat was waiting.
 *
 * Now (lib/chatAway): the REAL chat page names itself on its stream (the
 * `X-IJ-Viewer` header — its address is unchanged) and, unmounting with a
 * turn still running, tells the daemon it
 * left (`POST /chat/turns/{id}/away {viewer}` — the shapes the REAL daemon
 * accepted, fixtures/chat-away-v1387.json); this window's REAL bell then reads
 * the waiting list again at once and shows the daemon's row. A chat page that
 * follows the same turn again here keeps the bell quiet (its card is the one
 * notice).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const FIX = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "chat-away-v1387.json"), "utf8").replace(/\r\n/g, "\n"),
) as {
  stream_header: string;
  away_path: string;
  away_body: Record<string, string>;
  away_answer: Record<string, unknown>;
  row: Record<string, unknown>;
};

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    posts: [] as { path: string; body: Record<string, unknown> }[],
    gets: [] as string[],
    getResponses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
    runs: [] as { body: Record<string, unknown>; opts?: { viewer?: string; rejoin?: string } }[],
    notify: vi.fn(),
    useRealStream: false,
    /** The bell's other polls answer {} (it reads many lists). */
    lenient: false,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://d",
  ijToken: () => "tok",
  flattenDetail: (d: unknown) => String(d),
  get: async (path: string) => {
    H.gets.push(path);
    const r = H.getResponses[path];
    if (r === undefined && H.lenient) return {};
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return typeof r === "function" ? (r as () => unknown)() : r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.posts.push({ path, body });
    const r = H.postResponses[path];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/useChatStream")>();
  return {
    ...real,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () =>
      H.useRealStream
        ? real.useChatStream()
        : {
            streaming: false,
            text: "",
            tools: [],
            approval: null,
            // The turn is parked on its card: the run never ends by itself.
            run: (body: Record<string, unknown>, _d: unknown, _r: unknown, opts?: { viewer?: string }) => {
              H.runs.push({ body, opts });
              return new Promise(() => {});
            },
            abort: () => {},
          },
  };
});
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: H.notify,
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { NotificationBell } from "@/components/NotificationBell";
import { __resetApiCache } from "@/lib/apiCache";
import {
  ANNOUNCE_AFTER_MS,
  CHAT_ASKS_CHANGED_EVENT,
  __resetChatAway,
  awayPath,
  followedHere,
  leaveTurn,
  noteFollowing,
  VIEWER_HEADER,
} from "@/lib/chatAway";
import { useChatStream } from "@/lib/useChatStream";

const PENDING = "/chat/approvals/pending";

beforeEach(() => {
  __resetApiCache();
  __resetChatAway();
  H.posts.length = 0;
  H.gets.length = 0;
  H.runs.length = 0;
  H.notify.mockReset();
  H.useRealStream = false;
  H.lenient = false;
  for (const k of Object.keys(H.postResponses)) delete H.postResponses[k];
  H.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    [PENDING]: { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  document.title = "Iron Jarvis";
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

async function askAndPark(): Promise<{ turnId: string; viewer: string; unmount: () => void }> {
  const { unmount } = render(<ChatPage />);
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: "run the command `echo hi` in the terminal" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(H.runs.length).toBe(1));
  const turnId = String(H.runs[0].body.turn_id ?? "");
  const viewer = String(H.runs[0].opts?.viewer ?? "");
  return { turnId, viewer, unmount };
}

const awayPosts = () => H.posts.filter((p) => /^\/chat\/turns\/.+\/away$/.test(p.path));

describe("the chat page says when it leaves the screen", () => {
  it("names itself on its stream and, unmounted mid-turn, posts the daemon's away shape", async () => {
    const { turnId, viewer, unmount } = await askAndPark();
    expect(turnId).toMatch(/\S/);
    expect(viewer).toMatch(/^view-/);
    H.postResponses[awayPath(turnId)] = FIX.away_answer;
    const announced = vi.fn();
    window.addEventListener(CHAT_ASKS_CHANGED_EVENT, announced);
    try {
      expect(awayPosts()).toEqual([]); // nothing while the card is on screen
      unmount(); // the user went to another page of the same window
      await waitFor(() => expect(awayPosts().length).toBe(1));
      const [post] = awayPosts();
      // The daemon's own route and body (fixtures/chat-away-v1387.json).
      expect(post.path).toBe(FIX.away_path.replace("{turn_id}", encodeURIComponent(turnId)));
      expect(Object.keys(post.body)).toEqual(Object.keys(FIX.away_body));
      expect(post.body).toEqual({ viewer });
      // … and this window's bell is told to read the list.
      await waitFor(() => expect(announced).toHaveBeenCalledTimes(1), { timeout: ANNOUNCE_AFTER_MS + 4000 });
    } finally {
      window.removeEventListener(CHAT_ASKS_CHANGED_EVENT, announced);
    }
  });

  it("a page unmounted with no turn running says nothing", async () => {
    const { unmount } = render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    unmount();
    await new Promise((r) => setTimeout(r, 50));
    expect(awayPosts()).toEqual([]);
  });

  it("a mount is a new viewer", async () => {
    const a = await askAndPark();
    a.unmount();
    H.runs.length = 0;
    const b = await askAndPark();
    expect(b.viewer).toMatch(/^view-/);
    expect(b.viewer).not.toBe(a.viewer);
  });
});

describe("the real stream reader names the viewer", () => {
  function sse(): Response {
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(new TextEncoder().encode('event: done\ndata: {"reply": "ok"}\n\n'));
        c.close();
      },
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }

  it("on a new turn, a rejoin and a tagged rejoin; and only when given", async () => {
    H.useRealStream = true;
    const fetchMock = vi.fn(async () => sse());
    vi.stubGlobal("fetch", fetchMock);
    const { result } = renderHook(() => useChatStream());
    const urls = () => fetchMock.mock.calls.map((c) => String((c as unknown[])[0]));
    const viewers = () =>
      fetchMock.mock.calls.map(
        (c) => ((c as unknown[])[1] as RequestInit).headers as Record<string, string>,
      ).map((h) => h[FIX.stream_header]);

    let during = false;
    fetchMock.mockImplementationOnce(async () => {
      during = followedHere("t1");
      return sse();
    });
    await act(async () => {
      await result.current.run({ turn_id: "t1" }, undefined, undefined, { viewer: "v1" });
    });
    expect(during).toBe(true); // this window knows it follows t1 while it reads
    expect(followedHere("t1")).toBe(false); // … and forgets it after
    await act(async () => {
      await result.current.run(undefined, undefined, undefined, { rejoin: "t2", viewer: "v1" });
    });
    await act(async () => {
      await result.current.run(undefined, undefined, undefined, { rejoin: "t3", ids: true, viewer: "v 1" });
    });
    await act(async () => {
      await result.current.run({ turn_id: "t4" });
    });
    // The addresses are exactly what they were …
    expect(urls()).toEqual([
      "http://d/chat/stream",
      "http://d/chat/turns/t2/stream",
      "http://d/chat/turns/t3/stream?ids=1",
      "http://d/chat/stream",
    ]);
    // … and the viewer rides the daemon's header, only when given.
    expect(VIEWER_HEADER).toBe(FIX.stream_header);
    expect(viewers()).toEqual(["v1", "v1", "v 1", undefined]);
  });
});

describe("the bell, where the user is", () => {
  it("reads the list at once when told, and shows the daemon's own row", async () => {
    H.lenient = true;
    H.getResponses[PENDING] = { approvals: [] };
    render(<NotificationBell />);
    await screen.findByRole("button", { name: /notification/i });
    await waitFor(() => expect(H.gets.filter((g) => g === PENDING).length).toBeGreaterThan(0));
    const before = H.gets.filter((g) => g === PENDING).length;
    expect(screen.queryByRole("button", { name: "1 notifications" })).toBeNull();

    // The page the user left told the daemon; the daemon now lists it.
    H.getResponses[PENDING] = { approvals: [FIX.row] };
    act(() => {
      window.dispatchEvent(new Event(CHAT_ASKS_CHANGED_EVENT));
    });
    await screen.findByRole("button", { name: "1 notifications" });
    expect(H.gets.filter((g) => g === PENDING).length).toBe(before + 1);
    fireEvent.click(screen.getByRole("button", { name: "1 notifications" }));
    const row = await screen.findByTestId("bell-chat-waiting");
    expect(row.textContent).toContain("A chat is waiting for your OK");
    expect(row.querySelector("a")?.getAttribute("href")).toBe(`/chat?thread=${FIX.row.thread_id}`);
    // ONE desktop notice: the bell's own "N pending" ping, naming the chat.
    await waitFor(() => expect(H.notify).toHaveBeenCalledTimes(1));
    expect(String(H.notify.mock.calls[0][1])).toContain("1 chat waiting for your OK");
  });
});

describe("leaveTurn", () => {
  it("tells the bell only when no chat page here follows the turn again", async () => {
    const announced = vi.fn();
    window.addEventListener(CHAT_ASKS_CHANGED_EVENT, announced);
    try {
      const post = vi.fn(async () => FIX.away_answer);
      // A chat page came straight back to the same turn: its card is the notice.
      noteFollowing("t9", "view-new");
      await leaveTurn("t9", "view-old", post, 0);
      await new Promise((r) => setTimeout(r, 20));
      expect(post).toHaveBeenCalledWith("/chat/turns/t9/away", { viewer: "view-old" });
      expect(announced).not.toHaveBeenCalled();
      // Nobody here follows it: told.
      await leaveTurn("t8", "view-old", post, 0);
      await waitFor(() => expect(announced).toHaveBeenCalledTimes(1));
      // An older daemon (404): nothing is announced.
      const refused = vi.fn(async () => {
        throw new Error("Not Found");
      });
      await leaveTurn("t7", "view-old", refused, 0);
      await new Promise((r) => setTimeout(r, 20));
      expect(announced).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(CHAT_ASKS_CHANGED_EVENT, announced);
    }
  });
});
