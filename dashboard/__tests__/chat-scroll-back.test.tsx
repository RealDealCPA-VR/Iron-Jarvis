/**
 * Scroll back in a chat (48-a, master plan R1.6; idea 48 "Scroll back
 * forever", docs/next-leaps/01-comes-to-you-and-scroll-back.md).
 *
 * A long chat keeps its newest 200 messages live and the rest as its older
 * messages (v1.499.3). The chat page now reads them back:
 *  - opening a chat with 5,000 older messages makes ONE thread read and NO
 *    older read, and a quiet "Earlier messages" line sits at the top;
 *  - reaching the top (once the reader has scrolled up) loads the 50 before,
 *    and the reader's place is kept (the row they were looking at stays where
 *    it was on screen);
 *  - a save after that sends ONLY the live messages (the older rows are never
 *    part of `messages`; mutation: put them in and this goes red);
 *  - the end says "Start of this chat";
 *  - a failed page says so and offers Try again, never an empty top;
 *  - older rows are read-only: copy yes, edit and rate no;
 *  - the page adopting a newer copy whose count grew fetches just the rows in
 *    between, so nothing the reader saw disappears and nothing shows twice.
 *
 * Harness: the chat page rendered for real, only the transport hooks mocked
 * (as chat-persistence-v1232).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    api: {
      gets: [] as string[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      /** Answers every `/chat/threads/t7/older?...` read. */
      older: null as null | ((before: number, limit: number) => unknown),
    },
    stream: { result: { reply: "streamed reply" } as Record<string, unknown> },
    events: { list: [] as { id: string; type: string; payload: unknown }[] },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    const older = /^\/chat\/threads\/t7\/older\?before=(-?\d+)&limit=(\d+)$/.exec(path);
    if (older && H.api.older) {
      const r = H.api.older(Number(older[1]), Number(older[2]));
      if (r instanceof Error) throw r;
      return r;
    }
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    if (r instanceof Error) throw r;
    return r;
  },
  post: async () => ({}),
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t7", title: "t", updated_at: `2026-11-20T10:00:0${H.api.puts.length % 10}+00:00` };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (_body: Record<string, unknown>, onDelta: (delta: string, full: string) => void) => {
      await new Promise<void>((r) => setTimeout(r, 0));
      onDelta("streamed", "streamed");
      return H.stream.result;
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: H.events.list, connected: false }) }));
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
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { __resetApiCache } from "@/lib/apiCache";
import {
  atStartOfChat,
  dayKey,
  decodeOlderPage,
  gapToFill,
  hasEarlier,
  olderPagePath,
  olderStart,
  withEarlierPage,
  withRowsBetween,
  withTotal,
} from "@/lib/olderMessages";

type Msg = Record<string, unknown>;

/** Older message `i`: two days in the newest page, so a date line shows. */
function olderMsg(i: number): Msg {
  const day = i < 4975 ? "2026-10-05" : "2026-10-06";
  return { role: i % 2 ? "assistant" : "user", content: `older ${i}`, at: `${day}T12:00:00+00:00` };
}
const liveMsg = (i: number, shift = 0): Msg => ({
  role: (i + shift) % 2 ? "assistant" : "user",
  content: `live ${i + shift}`,
  at: "2026-11-19T12:00:00+00:00",
});

/** The daemon's older messages: `make(i)` for 0..total-1, paged like the
 *  route (the `limit` just before `before`). */
function serveOlder(total: number, make: (i: number) => Msg = olderMsg) {
  H.api.older = (before, limit) => {
    const end = Math.max(0, Math.min(before, total));
    const start = Math.max(0, end - limit);
    const messages: Msg[] = [];
    for (let i = start; i < end; i++) messages.push(make(i));
    return { id: "t7", total, offset: 0, first_seq: start, messages };
  };
}

function storeThread(older: number, live: Msg[] = Array.from({ length: 200 }, (_, i) => liveMsg(i))) {
  H.api.getResponses["/chat/threads/t7"] = {
    id: "t7",
    title: "long chat",
    messages: live,
    updated_at: "2026-11-19T12:00:00+00:00",
    older,
  };
  window.history.replaceState({}, "", "/chat?thread=t7");
}

// IntersectionObserver: jsdom has none. Each observer is kept; `reachTop`
// tells the ones watching the top line that it came into view.
const observers: FakeIO[] = [];
class FakeIO {
  cb: IntersectionObserverCallback;
  targets = new Set<Element>();
  constructor(cb: IntersectionObserverCallback) {
    this.cb = cb;
    observers.push(this);
  }
  observe(el: Element) {
    this.targets.add(el);
  }
  unobserve(el: Element) {
    this.targets.delete(el);
  }
  disconnect() {
    this.targets.clear();
  }
  takeRecords() {
    return [];
  }
}
function reachTop() {
  act(() => {
    for (const o of [...observers]) {
      for (const el of [...o.targets]) {
        if (!el.isConnected || !el.closest('[data-testid="older-top"]')) continue;
        o.cb([{ target: el, isIntersecting: true } as unknown as IntersectionObserverEntry], o as unknown as IntersectionObserver);
      }
    }
  });
}

// Layout: jsdom lays nothing out. Every row is 100px tall, in page order, so
// the scroller's height and each row's place on screen follow the DOM.
const ROW = 100;
let scrollTop = 0;
function rowsIn(box: Element): Element[] {
  return Array.from(box.querySelectorAll("[data-older-seq], [data-msg-index]"));
}
function layOut(box: HTMLElement) {
  Object.defineProperty(box, "scrollTop", { configurable: true, get: () => scrollTop, set: (v: number) => { scrollTop = v; } });
  Object.defineProperty(box, "scrollHeight", { configurable: true, get: () => rowsIn(box).length * ROW });
  Object.defineProperty(box, "clientHeight", { configurable: true, get: () => 600 });
}
function scroller(): HTMLElement {
  return screen.getByTestId("chat-scroll");
}
/** The reader scrolls to the top of what is on screen. */
function scrollToTop() {
  const box = scroller();
  layOut(box);
  scrollTop = 0;
  fireEvent.scroll(box);
}
const olderReads = () => H.api.gets.filter((p) => p.includes("/older"));
const threadReads = () => H.api.gets.filter((p) => p === "/chat/threads/t7");

function resetApi() {
  H.api.gets.length = 0;
  H.api.puts.length = 0;
  H.api.older = null;
  for (const k of Object.keys(H.api.getResponses)) delete H.api.getResponses[k];
  H.api.getResponses["/models"] = { models: [] };
  H.api.getResponses["/chat/personas"] = { personas: [] };
  H.api.getResponses["/chat/threads"] = { threads: [] };
  H.api.getResponses["/settings"] = { settings: {} };
  H.api.getResponses["/projects"] = { projects: [] };
  H.api.getResponses["/agents/mentionable"] = { agents: [] };
  H.api.getResponses["/skills"] = { skills: [] };
  H.api.getResponses["/workflows"] = { workflows: [] };
  H.api.getResponses["/tools"] = { tools: [] };
  H.api.getResponses["/undo?session_id=chat"] = { actions: [] };
  H.api.getResponses["/chat/approvals/pending"] = { approvals: [] };
  H.stream.result = { reply: "streamed reply" };
}

const realRect = Element.prototype.getBoundingClientRect;
beforeEach(() => {
  resetApi();
  __resetApiCache();
  H.events.list = [];
  observers.length = 0;
  scrollTop = 0;
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("IntersectionObserver", FakeIO);
  Element.prototype.getBoundingClientRect = function (this: Element) {
    const box = this.closest('[data-testid="chat-scroll"]');
    const all = box ? rowsIn(box) : [];
    const at = all.indexOf(this);
    if (at < 0) return realRect.call(this);
    const top = at * ROW - scrollTop;
    return { top, bottom: top + ROW, height: ROW, left: 0, right: 0, width: 0, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
  };
});
afterEach(() => {
  cleanup();
  Element.prototype.getBoundingClientRect = realRect;
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

async function openLongChat(older = 5000) {
  storeThread(older);
  serveOlder(older);
  const view = render(<ChatPage />);
  await screen.findByText("live 199");
  return view;
}

describe("scroll back in a chat (48-a)", () => {
  it("opens a chat with 5,000 older messages with one thread read and no older read", async () => {
    await openLongChat();
    expect(await screen.findByTestId("older-more")).toHaveTextContent("Earlier messages");
    // Seen at once, before anything scrolled: still nothing older is read.
    reachTop();
    await act(async () => {});
    expect(threadReads()).toHaveLength(1);
    expect(olderReads()).toHaveLength(0);
    expect(document.querySelectorAll("[data-older-seq]")).toHaveLength(0);
  });

  it("reaching the top loads the 50 before and keeps the reader's place", async () => {
    await openLongChat();
    await screen.findByTestId("older-more");
    scrollToTop();
    // The row at the top of the screen before the load: the first live one.
    const firstLive = document.querySelector('[data-msg-index="0"]') as Element;
    expect(firstLive.getBoundingClientRect().top).toBe(0);
    reachTop();
    expect(await screen.findByText("older 4999")).toBeInTheDocument();
    expect(olderReads()).toEqual(["/chat/threads/t7/older?before=5000&limit=50"]);
    const rows = Array.from(document.querySelectorAll("[data-older-seq]"));
    expect(rows).toHaveLength(50);
    expect(rows[0].getAttribute("data-older-seq")).toBe("4950");
    expect(rows[49].getAttribute("data-older-seq")).toBe("4999");
    expect(screen.getByText("older 4950")).toBeInTheDocument();
    // The reader's place: the row they were looking at did not move.
    await waitFor(() => expect(firstLive.getBoundingClientRect().top).toBe(0));
    expect(scrollTop).toBe(50 * ROW);
    // A date line where the day changes, at the top of what was loaded, and
    // where the older rows meet the live ones.
    expect(screen.getAllByTestId("older-day")).toHaveLength(3);
    // Still more above.
    expect(screen.getByTestId("older-more")).toBeInTheDocument();
  });

  it("a save after loading sends only the live messages", async () => {
    await openLongChat();
    await screen.findByTestId("older-more");
    scrollToTop();
    reachTop();
    await screen.findByText("older 4999");
    const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
    fireEvent.change(box, { target: { value: "a new question" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(
        H.api.puts.some((p) =>
          (p.body.messages as Msg[]).some((m) => m.content === "streamed reply"),
        ),
      ).toBe(true),
    );
    for (const p of H.api.puts) {
      const sent = p.body.messages as Msg[];
      expect(sent.filter((m) => String(m.content).startsWith("older "))).toEqual([]);
      expect(sent[0].content).toBe("live 0");
    }
    const last = H.api.puts[H.api.puts.length - 1].body.messages as Msg[];
    expect(last).toHaveLength(202);
    // The older rows are still on screen above.
    expect(screen.getByText("older 4950")).toBeInTheDocument();
  });

  it("says 'Start of this chat' once the first message is loaded", async () => {
    await openLongChat(60);
    await screen.findByTestId("older-more");
    scrollToTop();
    reachTop();
    await screen.findByText("older 10");
    expect(screen.queryByText("older 9")).toBeNull();
    expect(screen.queryByTestId("older-start")).toBeNull();
    reachTop();
    await screen.findByText("older 0");
    expect(olderReads()).toEqual([
      "/chat/threads/t7/older?before=60&limit=50",
      "/chat/threads/t7/older?before=10&limit=50",
    ]);
    expect(screen.getByTestId("older-start")).toHaveTextContent("Start of this chat");
    expect(screen.queryByTestId("older-more")).toBeNull();
    expect(document.querySelectorAll("[data-older-seq]")).toHaveLength(60);
    // Nothing more is asked at the start.
    reachTop();
    await act(async () => {});
    expect(olderReads()).toHaveLength(2);
  });

  it("a failed page says so and offers Try again, never an empty top", async () => {
    await openLongChat();
    await screen.findByTestId("older-more");
    let fail = true;
    const serve = H.api.older;
    H.api.older = (before, limit) => (fail ? new H.FakeApiError("down", 503) : serve!(before, limit));
    scrollToTop();
    reachTop();
    const failed = await screen.findByTestId("older-failed");
    expect(failed).toHaveTextContent("Could not load earlier messages.");
    expect(document.querySelectorAll("[data-older-seq]")).toHaveLength(0);
    // Reaching the top again does not hammer a failing read; the press does.
    reachTop();
    await act(async () => {});
    expect(olderReads()).toHaveLength(1);
    fail = false;
    fireEvent.click(within(failed).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("older 4999")).toBeInTheDocument();
    expect(screen.queryByTestId("older-failed")).toBeNull();
    expect(olderReads()).toHaveLength(2);
  });

  it("the line itself loads when pressed (a chat too short to scroll)", async () => {
    await openLongChat();
    fireEvent.click(await screen.findByTestId("older-more"));
    expect(await screen.findByText("older 4999")).toBeInTheDocument();
    expect(olderReads()).toEqual(["/chat/threads/t7/older?before=5000&limit=50"]);
  });

  it("older rows can be copied but not edited, rated or tried again", async () => {
    await openLongChat();
    fireEvent.click(await screen.findByTestId("older-more"));
    await screen.findByText("older 4999");
    const block = screen.getByTestId("older-messages");
    expect(within(block).queryAllByRole("button", { name: "Edit and resend" })).toHaveLength(0);
    expect(within(block).queryAllByRole("button", { name: "Regenerate reply" })).toHaveLength(0);
    expect(within(block).queryAllByRole("button", { name: /Read aloud/ })).toHaveLength(0);
    expect(within(block).getAllByRole("button", { name: "Copy message" }).length).toBe(25);
    // The live rows keep theirs.
    expect(screen.getAllByRole("button", { name: "Edit and resend" }).length).toBeGreaterThan(0);
  });

  it("New chat leaves the earlier messages behind, and a read still on its way lands nowhere", async () => {
    await openLongChat();
    fireEvent.click(await screen.findByTestId("older-more"));
    await screen.findByText("older 4999");
    // A second page is asked for and answers only after the reader left.
    let release: (v: unknown) => void = () => {};
    const serve = H.api.older!;
    H.api.older = (before, limit) => new Promise((r) => (release = () => r(serve(before, limit))));
    fireEvent.click(screen.getByTestId("older-more"));
    await waitFor(() => expect(olderReads()).toHaveLength(2));
    fireEvent.click(screen.getAllByRole("button", { name: /New chat/ })[0]);
    const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
    fireEvent.change(box, { target: { value: "a fresh start" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("streamed reply")).toBeInTheDocument();
    await act(async () => release(undefined));
    await act(async () => {});
    expect(screen.queryByTestId("older-messages")).toBeNull();
    expect(document.querySelectorAll("[data-older-seq]")).toHaveLength(0);
    expect(screen.queryByText("older 4999")).toBeNull();
    expect(screen.queryByText("older 4900")).toBeNull();
  });

  it("adopting a newer copy whose count grew fetches just the rows in between", async () => {
    const view = await openLongChat(0);
    expect(screen.queryByTestId("older-more")).toBeNull();
    expect(screen.queryByTestId("older-start")).toBeNull();
    // Another writer added three lines: the daemon moved three to older.
    serveOlder(3, (i) => liveMsg(i));
    H.api.getResponses["/chat/threads/t7"] = {
      id: "t7",
      title: "long chat",
      messages: Array.from({ length: 200 }, (_, i) => liveMsg(i, 3)),
      updated_at: "2026-11-19T12:30:00+00:00",
      older: 3,
    };
    H.events.list = [{ id: "ev1", type: "chat.thread_updated", payload: { thread_id: "t7" } }];
    view.rerender(<ChatPage />);
    expect(await screen.findByText("live 202")).toBeInTheDocument();
    await waitFor(() => expect(olderReads()).toEqual(["/chat/threads/t7/older?before=3&limit=3"]));
    // What the reader saw is still there, once each, and the chat's start is known.
    await waitFor(() => expect(document.querySelectorAll("[data-older-seq]")).toHaveLength(3));
    for (const words of ["live 0", "live 1", "live 2", "live 3"]) {
      expect(screen.getAllByText(words)).toHaveLength(1);
    }
    expect(screen.getByTestId("older-start")).toBeInTheDocument();
  });
});

describe("lib/olderMessages: the held rows are always one run that ends at the live ones", () => {
  const page = (firstSeq: number, n: number, total = 100) => ({
    firstSeq,
    total,
    messages: Array.from({ length: n }, (_, i) => ({ role: "user", content: `m${firstSeq + i}` })),
  });

  it("asks for the page just before a position, capped like the daemon", () => {
    expect(olderPagePath("t 1", 5000)).toBe("/chat/threads/t%201/older?before=5000&limit=50");
    expect(olderPagePath("t", -4, 5000)).toBe("/chat/threads/t/older?before=0&limit=1000");
  });

  it("reads only an answer that says where it starts", () => {
    expect(decodeOlderPage({ total: 5, offset: 0, messages: [] })).toBeNull(); // no first_seq
    expect(decodeOlderPage({ first_seq: 2, total: 5, messages: "x" })).toBeNull();
    expect(decodeOlderPage({ first_seq: 2, total: 5, messages: [{ role: "user" }, 3, null, { x: 1 }] }))
      .toEqual({ firstSeq: 2, total: 5, messages: [{ role: "user" }] });
  });

  it("an earlier page goes on top only when it is for the position held", () => {
    const s = olderStart<Msg>("t", 100);
    expect(hasEarlier(s)).toBe(true);
    const next = withEarlierPage(s, 100, page(50, 50));
    expect(next.loadedFrom).toBe(50);
    expect(next.rows.map((r) => r.seq)).toEqual(Array.from({ length: 50 }, (_, i) => 50 + i));
    // A page for another position (the chat changed meanwhile) is dropped.
    expect(withEarlierPage(next, 100, page(0, 50)).rows).toBe(next.rows);
    // A page that does not start before it gains nothing: it fails, so the
    // line offers Try again instead of asking the same again.
    expect(withEarlierPage(next, 50, page(50, 0)).failed).toBe(true);
    const top = withEarlierPage(next, 50, page(0, 50));
    expect(atStartOfChat(top)).toBe(true);
    expect(hasEarlier(top)).toBe(false);
  });

  it("a grown count is filled just in between; a capped or failed fill never leaves a gap", () => {
    const held = withEarlierPage(olderStart<Msg>("t", 100), 100, page(90, 10));
    const grown = withTotal(held, "t", 103);
    expect(grown.total).toBe(103);
    expect(gapToFill(grown)).toEqual({ from: 100, to: 103 });
    const filled = withRowsBetween(grown, 100, 103, page(100, 3, 103));
    expect(filled.rows.map((r) => r.seq)).toEqual([90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102]);
    expect(gapToFill(filled)).toBeNull();
    // A read capped short of `from`: what was read becomes the whole run.
    const capped = withRowsBetween(grown, 100, 103, page(101, 2, 103));
    expect(capped.loadedFrom).toBe(101);
    expect(capped.rows.map((r) => r.seq)).toEqual([101, 102]);
    // A failed fill: nothing held, the line covers everything above.
    const failed = withRowsBetween(grown, 100, 103, null);
    expect(failed.rows).toEqual([]);
    expect(failed.loadedFrom).toBe(103);
    expect(gapToFill(failed)).toBeNull();
    // A fill for a state that started over is ignored.
    const over = olderStart<Msg>("t", 2);
    expect(withRowsBetween(over, 100, 103, page(100, 3, 103))).toBe(over);
    // The same count changes nothing; another chat or a smaller count starts over.
    expect(withTotal(held, "t", 100)).toBe(held);
    expect(withTotal(held, "u", 100).rows).toEqual([]);
    expect(withTotal(held, "t", 40)).toEqual(olderStart("t", 40));
    expect(withTotal(held, "t", "x")).toBe(held);
  });

  it("tells days apart by local date, and nothing for no time", () => {
    expect(dayKey("2026-10-05T12:00:00+00:00")).toBe(dayKey("2026-10-05T13:00:00+00:00"));
    expect(dayKey("2026-10-05T12:00:00+00:00")).not.toBe(dayKey("2026-10-06T12:00:00+00:00"));
    expect(dayKey(undefined)).toBe("");
    expect(dayKey("not a time")).toBe("");
  });
});
