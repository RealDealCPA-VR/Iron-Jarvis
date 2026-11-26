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
 *  - the end says "Start of this chat", but only when the daemon says the
 *    first kept message is the chat's very first (`start_kept`): a chat that
 *    passed 200 messages before v1.499.4 lost its first ones, and its top
 *    says "Earlier messages were not kept before this version";
 *  - a failed page says so and offers Try again, never an empty top;
 *  - older rows are read-only: copy yes, edit and rate no, and an agent
 *    run's reply shows no Revert, Stop or Try again (the live one does);
 *  - the page adopting a newer copy whose count grew fetches just the rows in
 *    between, so nothing the reader saw disappears and nothing shows twice;
 *    when that read fails the held rows stay and a line with Try again marks
 *    where the missing rows go;
 *  - a reader who scrolls up while the line is already in view gets the page
 *    (no new observer report comes then);
 *  - a save merged over another window's copy caches that copy's count.
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
      /** How many of the next thread saves answer 409 (another window saved). */
      conflicts: 0,
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
    if (H.api.conflicts > 0 && /^\/chat\/threads\/t7$/.test(path)) {
      H.api.conflicts -= 1;
      throw new H.FakeApiError("updated from another window", 409);
    }
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
import { RunResultCard, type RunResult } from "@/components/chat/RunResultCard";
import { __resetApiCache, cachedGet } from "@/lib/apiCache";
import {
  atStartOfChat,
  dayKey,
  decodeOlderPage,
  gapRetry,
  gapToFill,
  hasEarlier,
  olderPagePath,
  olderStart,
  startWords,
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
 *  route (the `limit` just before `before`). `startKept`: the route's
 *  `start_kept` (false for a long chat made before v1.499.4). */
function serveOlder(total: number, make: (i: number) => Msg = olderMsg, startKept = true) {
  H.api.older = (before, limit) => {
    const end = Math.max(0, Math.min(before, total));
    const start = Math.max(0, end - limit);
    const messages: Msg[] = [];
    for (let i = start; i < end; i++) messages.push(make(i));
    return { id: "t7", total, offset: 0, first_seq: start, start_kept: startKept, messages };
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
  H.api.conflicts = 0;
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

  it("never says 'Start of this chat' over a chat whose first messages were not kept", async () => {
    // A chat made before v1.499.4 that passed 200 messages then lost its
    // first ones; the daemon says its start is not known (`start_kept`).
    storeThread(60);
    serveOlder(60, olderMsg, false);
    render(<ChatPage />);
    await screen.findByText("live 199");
    fireEvent.click(await screen.findByTestId("older-more"));
    await screen.findByText("older 10");
    fireEvent.click(screen.getByTestId("older-more"));
    await screen.findByText("older 0");
    const top = screen.getByTestId("older-start");
    expect(top).toHaveTextContent("Earlier messages were not kept before this version");
    expect(top).not.toHaveTextContent("Start of this chat");
    expect(screen.queryByText("Start of this chat")).toBeNull();
  });

  it("scrolling up loads when the line was already in view while following the newest message", async () => {
    await openLongChat();
    await screen.findByTestId("older-more");
    // The chat is only a little taller than the window: the line is in view
    // at once, while the reader follows the newest message (ignored then).
    reachTop();
    await act(async () => {});
    expect(olderReads()).toHaveLength(0);
    // The reader scrolls up; the line's view does not change, so no new
    // report comes. Leaving the bottom is the reach.
    scrollToTop();
    expect(await screen.findByText("older 4999")).toBeInTheDocument();
    expect(olderReads()).toEqual(["/chat/threads/t7/older?before=5000&limit=50"]);
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

  it("an earlier message offers no Undo: no Revert, Stop or Try again under a run, no changed-files line", async () => {
    // A run that is still saved as active and has three actions it could undo.
    const RUN = {
      found: true,
      session_id: "s9",
      status: "active",
      task: "tidy the folder",
      summary: "",
      steps: 3,
      tools_used: [{ tool: "move_file", count: 3 }],
      tools_failed: [],
      files_created: [],
      files_changed: [],
      errors: [],
      revertable: 3,
      duration_s: 10,
      outcome: null,
      provider: "claude-cli",
      model: "claude-opus-5-5",
      agent_name: null,
      agent_type: "builder",
      route_note: "",
      activity: [],
    };
    // A reply whose turn wrote a file: its changed-files line (with Undo).
    const WROTE = { documents: ["C:/work/notes.txt"], timing: { startedAt: 1_763_000_000_000, endedAt: 1_763_000_005_000 } };
    const at = "2026-10-06T12:00:00+00:00";
    const make = (i: number): Msg =>
      i === 4999
        ? { role: "assistant", content: "older run reply", runResult: RUN, at }
        : i === 4997
          ? { role: "assistant", content: "older agent reply", panelWho: "remote:hermes", runResult: RUN, at }
          : i === 4995
            ? { role: "assistant", content: "older wrote a file", ...WROTE, at }
            : olderMsg(i);
    const live = Array.from({ length: 200 }, (_, i) =>
      i === 199
        ? { ...liveMsg(i), content: "live run reply", runResult: RUN }
        : i === 197
          ? { ...liveMsg(i), content: "live wrote a file", ...WROTE }
          : liveMsg(i),
    );
    storeThread(5000, live);
    serveOlder(5000, make);
    render(<ChatPage />);
    await screen.findByText("live run reply");
    // The control: the live reply offers them (the card loads lazily).
    const liveRow = document.querySelector('[data-msg-index="199"]') as HTMLElement;
    expect(await within(liveRow).findByRole("button", { name: /Revert \(3\)/ }, { timeout: 10_000 })).toBeInTheDocument();
    expect(within(liveRow).getByRole("button", { name: /Stop/ })).toBeInTheDocument();
    expect(within(liveRow).getByRole("button", { name: /Try again/ })).toBeInTheDocument();
    const liveWrote = document.querySelector('[data-msg-index="197"]') as HTMLElement;
    expect(within(liveWrote).getByTestId("reply-changes")).toBeInTheDocument();
    fireEvent.click(await screen.findByTestId("older-more"));
    await screen.findByText("older run reply");
    const block = screen.getByTestId("older-messages");
    // Both earlier run replies drew their card; neither offers a press on the run.
    expect(await within(block).findAllByTestId("run-result-card", {}, { timeout: 10_000 })).toHaveLength(2);
    expect(within(block).queryAllByRole("button", { name: /Revert|Stop|Undo|Try again|Keep going/ })).toHaveLength(0);
    // Opening the run is reading, so it stays.
    expect(within(block).getAllByTestId("run-result-open")).toHaveLength(2);
    // The earlier reply that wrote a file is there, without its changed-files line.
    expect(within(block).getByText("older wrote a file")).toBeInTheDocument();
    expect(within(block).queryAllByTestId("reply-changes")).toHaveLength(0);
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

  it("a failed read of the rows in between keeps what the reader holds and offers Try again", async () => {
    const view = await openLongChat(100);
    fireEvent.click(await screen.findByTestId("older-more"));
    await screen.findByText("older 50");
    expect(document.querySelectorAll("[data-older-seq]")).toHaveLength(50);
    // Another writer added three lines: the daemon moved three to older, and
    // reading them fails.
    const ok = (i: number) => (i < 100 ? olderMsg(i) : liveMsg(i - 100));
    let fail = true;
    serveOlder(103, ok);
    const serve = H.api.older!;
    H.api.older = (before, limit) => (fail ? new H.FakeApiError("down", 503) : serve(before, limit));
    H.api.getResponses["/chat/threads/t7"] = {
      id: "t7",
      title: "long chat",
      messages: Array.from({ length: 200 }, (_, i) => liveMsg(i, 3)),
      updated_at: "2026-11-19T12:30:00+00:00",
      older: 103,
    };
    H.events.list = [{ id: "ev1", type: "chat.thread_updated", payload: { thread_id: "t7" } }];
    view.rerender(<ChatPage />);
    expect(await screen.findByText("live 202")).toBeInTheDocument();
    const gap = await screen.findByTestId("older-gap-failed");
    expect(gap).toHaveTextContent("Could not load earlier messages.");
    // What the reader held is still there; nothing is asked again by itself.
    expect(document.querySelectorAll("[data-older-seq]")).toHaveLength(50);
    expect(screen.getByText("older 50")).toBeInTheDocument();
    expect(olderReads()).toEqual([
      "/chat/threads/t7/older?before=100&limit=50",
      "/chat/threads/t7/older?before=103&limit=3",
    ]);
    await act(async () => {});
    expect(olderReads()).toHaveLength(2);
    // Try again reads the rows in between, and the line goes.
    fail = false;
    fireEvent.click(within(gap).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(document.querySelectorAll("[data-older-seq]")).toHaveLength(53));
    expect(screen.queryByTestId("older-gap-failed")).toBeNull();
    for (const words of ["live 0", "live 1", "live 2", "older 99"]) {
      expect(screen.getAllByText(words)).toHaveLength(1);
    }
    expect(olderReads()).toHaveLength(3);
  });

  it("a save merged over another window's copy keeps that copy's count with it", async () => {
    await openLongChat(0);
    // Another window saved meanwhile: three more lines, three moved to older.
    serveOlder(3, (i) => liveMsg(i));
    H.api.getResponses["/chat/threads/t7"] = {
      id: "t7",
      title: "long chat",
      messages: Array.from({ length: 200 }, (_, i) => liveMsg(i, 3)),
      updated_at: "2026-11-19T12:30:00+00:00",
      older: 3,
    };
    H.api.conflicts = 1;
    const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
    fireEvent.change(box, { target: { value: "a new question" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(
        H.api.puts.some((p) => (p.body.messages as Msg[]).some((m) => m.content === "streamed reply")),
      ).toBe(true),
    );
    await waitFor(() => expect(H.api.conflicts).toBe(0));
    // The copy a reopen paints from starts where its count says.
    await waitFor(() => expect(cachedGet<{ older?: unknown }>("/chat/threads/t7")?.older).toBe(3));
    const cached = cachedGet<{ messages: Msg[]; older?: unknown }>("/chat/threads/t7");
    expect(cached?.messages[0].content).toBe("live 3");
  });
});

describe("scroll back in a phone chat (48-b)", () => {
  // From 48-b a phone chat keeps its oldest messages too (comm/threads.py
  // moves them, as a long chat's), and the chat page shows it with its
  // banner: the same "Earlier messages" line, read the same way.
  function storePhoneChat(older: number) {
    storeThread(older);
    H.api.getResponses["/chat/threads/t7"] = {
      ...(H.api.getResponses["/chat/threads/t7"] as Record<string, unknown>),
      title: "Telegram · Val",
      owner: "daemon",
      comm_channel: "telegram",
      comm_display: "Val",
    };
  }

  it("scrolls back to the start of a phone chat, read-only, and never saves it", async () => {
    storePhoneChat(60);
    serveOlder(60);
    render(<ChatPage />);
    await screen.findByText("live 199");
    expect(await screen.findByTestId("older-more")).toHaveTextContent("Earlier messages");
    // Opening it read nothing older.
    expect(olderReads()).toHaveLength(0);
    scrollToTop();
    reachTop();
    await screen.findByText("older 10");
    reachTop();
    await screen.findByText("older 0");
    expect(olderReads()).toEqual([
      "/chat/threads/t7/older?before=60&limit=50",
      "/chat/threads/t7/older?before=10&limit=50",
    ]);
    expect(screen.getByTestId("older-start")).toHaveTextContent("Start of this chat");
    // An earlier phone message is read-only, like any chat's.
    const row = document.querySelector('[data-older-seq="0"]') as HTMLElement;
    expect(within(row).queryByRole("button", { name: /edit/i })).toBeNull();
    // A phone chat is the messaging store's to write: the page saved nothing.
    expect(H.api.puts.filter((p) => p.path === "/chat/threads/t7")).toEqual([]);
  });

  it("says its first messages were not kept when the daemon says so", async () => {
    // A phone chat made before 48-b that passed 200 messages lost its first
    // ones then; the daemon says its start is not known.
    storePhoneChat(20);
    serveOlder(20, olderMsg, false);
    render(<ChatPage />);
    await screen.findByText("live 199");
    fireEvent.click(await screen.findByTestId("older-more"));
    await screen.findByText("older 0");
    expect(screen.getByTestId("older-start")).toHaveTextContent(
      "Earlier messages were not kept before this version",
    );
  });
});

describe("RunResultCard readOnly: an earlier message's run card acts on nothing", () => {
  const run = (over: Record<string, unknown>) =>
    ({
      found: true, session_id: "s9", status: "active", task: "tidy the folder", summary: "",
      steps: 3, tools_used: [{ tool: "move_file", count: 3 }], tools_failed: [], files_created: [],
      files_changed: [], errors: [], revertable: 3, duration_s: 10, outcome: null,
      provider: "claude-cli", model: "claude-opus-5-5", agent_name: null, agent_type: "builder",
      route_note: "", activity: [], ...over,
    }) as unknown as RunResult;
  const PRESSES = { name: /Revert|Stop|Try again|Keep going/ };
  const ACTIVE = run({});
  const AT_LIMIT = run({ status: "failed", summary: "stopped: reached max steps before completion" });

  it("draws no Try again, Keep going, Revert or Stop even when handed them, in both shapes", () => {
    for (const withRow of [false, true]) {
      for (const result of [ACTIVE, AT_LIMIT]) {
        // The control: the same card, live, offers them.
        const live = render(<RunResultCard result={result} withRow={withRow} onRetry={() => {}} onKeepGoing={() => {}} />);
        expect(screen.queryAllByRole("button", PRESSES).length).toBeGreaterThanOrEqual(2);
        live.unmount();
        const earlier = render(
          <RunResultCard result={result} withRow={withRow} onRetry={() => {}} onKeepGoing={() => {}} readOnly />,
        );
        expect(screen.queryAllByRole("button", PRESSES)).toHaveLength(0);
        expect(screen.queryByTestId("run-result-actions")).toBeNull();
        earlier.unmount();
      }
    }
  });
});

describe("lib/olderMessages: the held rows are always one run that ends at the live ones", () => {
  const page = (firstSeq: number, n: number, total = 100, startKept = true) => ({
    firstSeq,
    total,
    startKept,
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
      .toEqual({ firstSeq: 2, total: 5, messages: [{ role: "user" }], startKept: false });
    // The start is known only when the daemon says so.
    expect(decodeOlderPage({ first_seq: 0, total: 5, start_kept: true, messages: [] })?.startKept).toBe(true);
    expect(decodeOlderPage({ first_seq: 0, total: 5, start_kept: "yes", messages: [] })?.startKept).toBe(false);
  });

  it("claims the start of a chat only when its first message was kept", () => {
    const kept = withEarlierPage(olderStart<Msg>("t", 30), 30, page(0, 30, 30, true));
    expect(atStartOfChat(kept)).toBe(true);
    expect(startWords(kept)).toBe("Start of this chat");
    const lost = withEarlierPage(olderStart<Msg>("t", 30), 30, page(0, 30, 30, false));
    expect(atStartOfChat(lost)).toBe(true);
    expect(startWords(lost)).toBe("Earlier messages were not kept before this version");
    // Nothing read yet: not known.
    expect(startWords(olderStart<Msg>("t", 30))).toBe("Earlier messages were not kept before this version");
    // The rows in between say it too.
    const grown = withTotal(olderStart<Msg>("t", 0), "t", 3);
    expect(startWords(withRowsBetween(grown, 0, 3, page(0, 3, 3, true)))).toBe("Start of this chat");
    expect(startWords(withRowsBetween(grown, 0, 3, page(0, 3, 3, false)))).not.toBe("Start of this chat");
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
    // A failed fill keeps what is held and marks the gap (Try again reads
    // it); a newer count, or the press, lets the fill run again.
    const failed = withRowsBetween(grown, 100, 103, null);
    expect(failed.rows).toBe(grown.rows);
    expect(failed.loadedFrom).toBe(90);
    expect(failed.gapFailed).toBe(true);
    expect(gapToFill(failed)).toEqual({ from: 100, to: 103 });
    expect(gapRetry(failed).gapFailed).toBe(false);
    expect(gapRetry(grown)).toBe(grown);
    expect(withTotal(failed, "t", 104).gapFailed).toBe(false);
    expect(withRowsBetween(failed, 100, 103, page(100, 3, 103)).gapFailed).toBe(false);
    // With nothing held there is nothing to keep: the line covers it all.
    const bare = withTotal(olderStart<Msg>("t", 100), "t", 103);
    const bareFailed = withRowsBetween(bare, 100, 103, null);
    expect(bareFailed.rows).toEqual([]);
    expect(bareFailed.loadedFrom).toBe(103);
    expect(bareFailed.gapFailed).toBe(false);
    expect(gapToFill(bareFailed)).toBeNull();
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
