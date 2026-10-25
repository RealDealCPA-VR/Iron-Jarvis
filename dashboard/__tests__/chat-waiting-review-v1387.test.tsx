/**
 * Readiness 12B review (G05): one notice per ask — the edges the lane left.
 *
 * Proven red first in the REAL browser (e2e/chat-waiting-where-you-are.spec.ts,
 * Edge against the scratch daemon): a RELOAD of a chat parked on its approval
 * card pinged "1 chat waiting for your OK" (the bell's first read lands before
 * the page rejoins its turn), and a `/chat` → `/` REMOUNT with a slow rejoin
 * pinged the same (the old page already told the daemon it left; the new one
 * had not followed yet). The card was in front of the user both times.
 *
 * The fix (lib/chatAway): the chat page CLAIMS the chat it opens, at once —
 * before any read answers — and this window's bell neither lists nor pings a
 * waiting chat that a chat page here has open (`shownHere`). These pins hold
 * both halves, plus the stream reader's preflight (no `cache` key).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const FIX = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "chat-away-v1387.json"), "utf8").replace(/\r\n/g, "\n"),
) as { row: Record<string, unknown> };
const THREAD = String(FIX.row.thread_id);

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    gets: [] as string[],
    getResponses: {} as Record<string, unknown>,
    hold: new Set<string>(),
    fail: new Set<string>(),
    notify: vi.fn(),
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://d",
  ijToken: () => "tok",
  flattenDetail: (d: unknown) => String(d),
  get: async (path: string) => {
    H.gets.push(path);
    if (H.hold.has(path)) return new Promise(() => {}); // a read that never answers
    if (H.fail.has(path)) throw new H.FakeApiError("Not Found", 404);
    const r = H.getResponses[path];
    if (r === undefined) return {};
    return typeof r === "function" ? (r as () => unknown)() : r;
  },
  post: async () => ({}),
  put: async () => ({ id: "t1", title: "t" }),
  patch: async () => ({}),
  del: async () => ({}),
}));
vi.mock("@/lib/useChatStream", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/useChatStream")>();
  return {
    ...real,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => ({
      streaming: false,
      text: "",
      tools: [],
      approval: null,
      run: () => new Promise(() => {}),
      abort: () => {},
    }),
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
  CHAT_ASKS_CHANGED_EVENT,
  __resetChatAway,
  noteFollowing,
  noteNotFollowing,
  noteNotShowing,
  noteShowing,
  shownHere,
  threadShownHere,
} from "@/lib/chatAway";
import { streamSSE } from "@/lib/useChatStream";

const PENDING = "/chat/approvals/pending";

beforeEach(() => {
  __resetApiCache();
  __resetChatAway();
  H.gets.length = 0;
  H.hold.clear();
  H.fail.clear();
  H.notify.mockReset();
  H.getResponses = { [PENDING]: { approvals: [FIX.row] } };
  window.history.replaceState({}, "", "/everything");
  window.localStorage.clear();
  window.sessionStorage.clear();
  document.title = "Iron Jarvis";
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

const reread = () =>
  act(() => {
    window.dispatchEvent(new Event(CHAT_ASKS_CHANGED_EVENT));
  });

describe("the bell, beside a chat page that has the chat open", () => {
  it("neither lists nor pings a waiting chat a chat page here has open", async () => {
    noteShowing("view-here", THREAD); // the chat page (mounting again) claimed it
    render(<NotificationBell />);
    await waitFor(() => expect(H.gets.filter((g) => g === PENDING).length).toBeGreaterThan(0));
    reread();
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole("button", { name: "1 notifications" })).toBeNull();
    expect(H.notify).not.toHaveBeenCalled();

    // The page goes (the user really left): listed, and pinged ONCE.
    act(() => noteNotShowing("view-here"));
    await screen.findByRole("button", { name: "1 notifications" });
    await waitFor(() => expect(H.notify).toHaveBeenCalledTimes(1));
    reread();
    await new Promise((r) => setTimeout(r, 50));
    expect(H.notify).toHaveBeenCalledTimes(1);
  });

  it("a page showing ANOTHER chat (or a new one) does not hide this one", async () => {
    noteShowing("view-here", "some-other-chat");
    render(<NotificationBell />);
    await screen.findByRole("button", { name: "1 notifications" });
    await waitFor(() => expect(H.notify).toHaveBeenCalledTimes(1));
  });

  it("a new chat's first turn (listed by its turn) is held by the page following it", () => {
    const row = { id: "chat:turn:turn-new", threadId: "" };
    expect(shownHere(row)).toBe(false);
    noteFollowing("turn-new", "view-here");
    expect(shownHere(row)).toBe(true);
    noteNotFollowing("turn-new", "view-here");
    expect(shownHere(row)).toBe(false);
    // A saved chat is never matched by "" — no page "shows" a blank id.
    noteShowing("view-here", "");
    expect(shownHere({ id: "chat:x", threadId: "" })).toBe(false);
  });
});

describe("the chat page claims the chat it opens — before any read answers", () => {
  it("at mount on ?thread=, while the thread's read is still out; and lets go at unmount", async () => {
    H.hold.add(`/chat/threads/${THREAD}`);
    window.history.replaceState({}, "", `/chat?thread=${THREAD}`);
    const { unmount } = render(<ChatPage />);
    await waitFor(() => expect(H.gets).toContain(`/chat/threads/${THREAD}`));
    expect(threadShownHere(THREAD)).toBe(true); // the read never answered
    unmount();
    expect(threadShownHere(THREAD)).toBe(false);
  });

  it("a reopened window (the conversation it had open) claims it the same way", async () => {
    H.hold.add(`/chat/threads/${THREAD}`);
    window.sessionStorage.setItem("ij_chat_open_thread", JSON.stringify({ id: THREAD, project: null }));
    window.history.replaceState({}, "", "/chat");
    render(<ChatPage />);
    await waitFor(() => expect(H.gets).toContain(`/chat/threads/${THREAD}`));
    expect(threadShownHere(THREAD)).toBe(true);
  });

  it("a NEW chat is claimed by the id its first save mints", async () => {
    window.history.replaceState({}, "", "/chat");
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    expect(threadShownHere("t1")).toBe(false);
    act(() => {
      box.focus();
    });
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.change(box, { target: { value: "hello" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(threadShownHere("t1")).toBe(true)); // the mocked save answers id "t1"
  });

  it("a chat that fails to open is not claimed (the page shows none of it)", async () => {
    H.fail.add(`/chat/threads/${THREAD}`);
    window.history.replaceState({}, "", `/chat?thread=${THREAD}`);
    render(<ChatPage />);
    await waitFor(() => expect(H.gets).toContain(`/chat/threads/${THREAD}`));
    await waitFor(() => expect(threadShownHere(THREAD)).toBe(false));
  });

  it("the bell beside it stays quiet for that chat (the reload the browser test drove)", async () => {
    H.hold.add(`/chat/threads/${THREAD}`);
    window.history.replaceState({}, "", `/chat?thread=${THREAD}`);
    render(
      <>
        <NotificationBell />
        <ChatPage />
      </>,
    );
    await waitFor(() => expect(H.gets.filter((g) => g === PENDING).length).toBeGreaterThan(0));
    reread();
    await new Promise((r) => setTimeout(r, 100));
    expect(screen.queryByRole("button", { name: "1 notifications" })).toBeNull();
    expect(H.notify).not.toHaveBeenCalled();
  });
});

describe("the stream reader and the preflight cache", () => {
  it("sends no `cache` key — the daemon's no-store header owns freshness", async () => {
    const calls: RequestInit[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_u: string, init: RequestInit) => {
        calls.push(init);
        return new Response("event: done\ndata: {}\n\n", { status: 200, headers: { "Content-Type": "text/event-stream" } });
      }),
    );
    for await (const _ of streamSSE("/chat/stream", { turn_id: "t" }, undefined, { viewer: "v" })) {
      /* drain */
    }
    for await (const _ of streamSSE("/chat/turns/t/stream", undefined, undefined, { viewer: "v" })) {
      /* drain */
    }
    expect(calls.length).toBe(2);
    for (const init of calls) expect("cache" in init).toBe(false); // measured in Edge: no-store forced an OPTIONS per turn
  });
});
