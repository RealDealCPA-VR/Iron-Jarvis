/**
 * v1.328.0 (calm chat W3-1) — "@ another chat".
 *
 * The composer's "@" menu gains a "Chats" section fed by
 * GET /chat/threads/search-refs (asked at once when the menu opens, again as
 * the query changes). Picking a chat adds a chip inside the composer card and
 * puts NO text in the message; the next turn sends the chips' ids as
 * `thread_refs` on both lanes (the stream body and the POST /chat fallback
 * are one object) and the chips are consumed like attachments. At most 3; a
 * fourth pick says "Up to 3 chats". The open chat is never offered. ↑↓ walk
 * agents and chats as one list, Enter picks, Esc closes, typing reopens.
 *
 * Harness: the chat-turn-reads-v1327 header, with the stream mock recording
 * each body and `get` answering the search route by prefix. Fix round: the
 * stream mock can hold a turn open (`hold`; Stop settles it) and publishes
 * `streaming` like wave-d-page-v1325, so a message can be queued with its own
 * chats and the Edit and resend path can be driven.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  CHAT_REFS_FULL_NOTE,
  addChatRef,
  chatRefIds,
  chatRefSearchPath,
  decodeChatRefRows,
  visibleChatRefRows,
} from "@/lib/chatRefs";
import { createComposerStore } from "@/lib/composerStore";
import { composerKeyHint } from "@/lib/composerChips";

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
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    refs: {
      paths: [] as string[],
      rows: [] as { id: string; title: string; updated_at?: string }[],
    },
    stream: {
      absent: false,
      bodies: [] as Record<string, unknown>[],
      /** Replies handed out in order (else "Noted."). */
      replies: [] as string[],
      /** Hold the next turn open until `settle` (or Stop) ends it. */
      hold: false,
      settle: null as null | ((r: Record<string, unknown>) => void),
      /** True while a held turn is open (the page shows Stop only then). */
      streaming: false,
      version: 0,
      listeners: new Set<() => void>(),
      bump() {
        this.version += 1;
        for (const l of [...this.listeners]) l();
      },
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    if (path.startsWith("/chat/threads/search-refs")) {
      H.refs.paths.push(path);
      const q = new URL(`http://x${path}`).searchParams.get("q") ?? "";
      return {
        threads: H.refs.rows.filter((r) => r.title.toLowerCase().includes(q.toLowerCase())),
      };
    }
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  const run = async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
    H.stream.bodies.push(body);
    if (H.stream.absent) throw new H.FakeStreamError("no stream", 404);
    if (H.stream.hold) {
      H.stream.hold = false;
      H.stream.streaming = true;
      H.stream.bump();
      return new Promise<Record<string, unknown>>((ok) => {
        H.stream.settle = ok;
      });
    }
    await new Promise<void>((r) => setTimeout(r, 0));
    const reply = H.stream.replies.shift() ?? "Noted.";
    onDelta(reply, reply);
    return {
      reply,
      route: { requested: "", provider: "mock", model: "mock", reason: "default" },
    };
  };
  const abort = () => {
    const s = H.stream.settle;
    H.stream.settle = null;
    H.stream.streaming = false;
    H.stream.bump();
    s?.({ reply: "" });
  };
  return {
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => {
      React.useSyncExternalStore(subscribe, () => H.stream.version);
      return {
        streaming: H.stream.streaming,
        text: "",
        tools: [],
        approval: null,
        run,
        abort,
      };
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
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

const CHATS = [
  { id: "c-tax", title: "Tax plan for 2026", updated_at: "2026-10-08T10:00:00" },
  { id: "c-bud", title: "Budget review", updated_at: "2026-10-07T10:00:00" },
  { id: "c-trip", title: "Trip notes", updated_at: "2026-10-06T10:00:00" },
  { id: "c-gift", title: "Gift ideas", updated_at: "2026-10-05T10:00:00" },
];

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.refs.paths.length = 0;
  H.refs.rows = CHATS.map((c) => ({ ...c }));
  H.stream.absent = false;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
  H.stream.hold = false;
  H.stream.settle = null;
  H.stream.streaming = false;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": { models: [] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": {
      agents: [
        { name: "builder", mention: "builder", description: "Builds things", kind: "builtin", healthy: true },
      ],
    },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function box(): Promise<HTMLTextAreaElement> {
  return (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
}

/** A chat row's title (v1.328.0 fix round: the row also shows the chat's
 *  age, so its whole text is no longer the title). Other rows: their text. */
function titleOf(b: Element): string {
  return b.querySelector('[data-testid="chat-ref-title"]')?.textContent ?? b.textContent ?? "";
}

function type(el: HTMLTextAreaElement, value: string) {
  fireEvent.change(el, { target: { value } });
}

/** Open the "@" menu with `value` and wait for the chat row titled `title`. */
async function openAndFind(el: HTMLTextAreaElement, value: string, title: string) {
  type(el, value);
  return waitFor(() => {
    const row = screen
      .queryAllByTestId("chat-ref-option")
      .find((b) => titleOf(b) === title);
    expect(row).toBeDefined();
    return row as HTMLElement;
  });
}

async function pick(el: HTMLTextAreaElement, title: string) {
  fireEvent.click(await openAndFind(el, "@", title));
  await waitFor(() =>
    expect(screen.getAllByTestId("chat-ref-chip").some((c) => c.textContent?.includes(title))).toBe(true),
  );
}

function chipTitles(): string[] {
  // v1.328.0 fix round: a chip also shows the chat's age; read its title.
  return screen
    .queryAllByTestId("chat-ref-chip")
    .map((c) => c.querySelector('[data-testid="chat-ref-chip-title"]')?.textContent ?? "");
}

describe('"@" lists saved chats (v1.328.0)', () => {
  it("shows the chats search-refs answers, under a Chats heading, asked when the menu opens", async () => {
    render(<ChatPage />);
    const el = await box();
    await openAndFind(el, "@", "Tax plan for 2026");
    const titles = screen.getAllByTestId("chat-ref-option").map(titleOf);
    expect(titles).toEqual(CHATS.map((c) => c.title));
    expect(screen.getByTestId("at-menu-chats").textContent).toContain("Chats");
    // A new chat has no saved id: nothing to leave out.
    expect(H.refs.paths[0]).toBe("/chat/threads/search-refs?q=");
    // The agents are still there, above the chats.
    expect(screen.getByRole("listbox", { name: "Agents" })).toBeTruthy();
  });

  it("asks again as you type after the @, and narrows to the match", async () => {
    render(<ChatPage />);
    const el = await box();
    await openAndFind(el, "@", "Budget review");
    type(el, "@tax");
    await waitFor(() => expect(H.refs.paths).toContain("/chat/threads/search-refs?q=tax"));
    await waitFor(() =>
      expect(screen.getAllByTestId("chat-ref-option").map(titleOf)).toEqual([
        "Tax plan for 2026",
      ]),
    );
  });

  it("asks again each time the menu opens", async () => {
    render(<ChatPage />);
    const el = await box();
    await openAndFind(el, "@", "Budget review");
    const before = H.refs.paths.length;
    type(el, "hello");
    await waitFor(() => expect(screen.queryByTestId("at-menu")).toBeNull());
    type(el, "hello @");
    await waitFor(() => expect(H.refs.paths.length).toBe(before + 1));
  });

  it("never offers the chat that is open, even when the search returns it", async () => {
    H.api.getResponses["/chat/threads/t7"] = {
      id: "t7",
      title: "This one",
      messages: [
        { role: "user", content: "first question" },
        { role: "assistant", content: "first answer" },
      ],
    };
    H.refs.rows = [{ id: "t7", title: "This one" }, ...H.refs.rows];
    window.history.replaceState({}, "", "/chat?thread=t7");
    render(<ChatPage />);
    await screen.findByText("first answer");
    const el = (await screen.findByLabelText("Message")) as HTMLTextAreaElement;
    await openAndFind(el, "@", "Tax plan for 2026");
    expect(H.refs.paths.at(-1)).toBe("/chat/threads/search-refs?q=&exclude=t7");
    expect(screen.getAllByTestId("chat-ref-option").map(titleOf)).not.toContain("This one");
  });
});

describe("picking a chat adds a chip, never text (v1.328.0)", () => {
  it("consumes only the @token, keeps the words and shows a chip with the title", async () => {
    render(<ChatPage />);
    const el = await box();
    fireEvent.click(await openAndFind(el, "compare with @bud", "Budget review"));
    await waitFor(() => expect(chipTitles()).toEqual(["Budget review"]));
    expect(el.value).toBe("compare with ");
    expect(el.value).not.toContain("Budget");
    // The picked chat is not offered again.
    await openAndFind(el, "compare with @", "Tax plan for 2026");
    expect(screen.getAllByTestId("chat-ref-option").map(titleOf)).not.toContain("Budget review");
  });

  it("sends the chips as thread_refs, clears them, and keeps them on the user message", async () => {
    render(<ChatPage />);
    const el = await box();
    await pick(el, "Tax plan for 2026");
    await pick(el, "Trip notes");
    type(el, "what did we decide?");
    fireEvent.keyDown(el, { key: "Enter" });
    await screen.findByText("Noted.");
    expect(H.stream.bodies).toHaveLength(1);
    expect(H.stream.bodies[0].thread_refs).toEqual(["c-tax", "c-trip"]);
    await waitFor(() => expect(chipTitles()).toEqual([]));
    // The bubble says which chats went with it.
    expect(screen.getByText("Chat: Tax plan for 2026")).toBeTruthy();
    await waitFor(() => {
      const saves = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/"));
      const msgs = saves.at(-1)?.body.messages as { role: string; chatRefs?: unknown }[];
      expect(msgs?.find((m) => m.role === "user")?.chatRefs).toEqual([
        { id: "c-tax", title: "Tax plan for 2026" },
        { id: "c-trip", title: "Trip notes" },
      ]);
    });
    // The next message carries none.
    type(el, "and now?");
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(H.stream.bodies).toHaveLength(2));
    expect("thread_refs" in H.stream.bodies[1]).toBe(false);
  });

  it("the POST lane carries the same thread_refs", async () => {
    H.stream.absent = true;
    H.api.postResponses["/chat"] = {
      reply: "Posted reply.",
      route: { requested: "", provider: "mock", model: "mock", reason: "default" },
    };
    render(<ChatPage />);
    const el = await box();
    await pick(el, "Gift ideas");
    type(el, "remind me");
    fireEvent.keyDown(el, { key: "Enter" });
    await screen.findByText("Posted reply.");
    const posted = H.api.posts.find((p) => p.path === "/chat");
    expect(posted?.body.thread_refs).toEqual(["c-gift"]);
  });

  it("holds at most 3; a fourth pick says so quietly and changes nothing", async () => {
    render(<ChatPage />);
    const el = await box();
    await pick(el, "Tax plan for 2026");
    await pick(el, "Budget review");
    await pick(el, "Trip notes");
    const row = await openAndFind(el, "@", "Gift ideas");
    expect(screen.getByTestId("at-menu-chats").textContent).toContain("Up to 3 chats");
    fireEvent.click(row);
    await waitFor(() => expect(screen.getByTestId("chat-ref-limit").textContent).toBe(CHAT_REFS_FULL_NOTE));
    expect(chipTitles()).toEqual(["Tax plan for 2026", "Budget review", "Trip notes"]);
    type(el, "go");
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    expect(H.stream.bodies[0].thread_refs).toEqual(["c-tax", "c-bud", "c-trip"]);
  });

  it("× removes a chip, and the removed chat is not sent", async () => {
    render(<ChatPage />);
    const el = await box();
    await pick(el, "Tax plan for 2026");
    await pick(el, "Budget review");
    fireEvent.click(screen.getByRole("button", { name: "Remove chat Tax plan for 2026" }));
    await waitFor(() => expect(chipTitles()).toEqual(["Budget review"]));
    type(el, "go");
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    expect(H.stream.bodies[0].thread_refs).toEqual(["c-bud"]);
  });
});

/** The turn is over: the box stops steering (a reply's words land before the
 *  turn's own finally runs). */
async function idle() {
  await waitFor(() => expect(screen.queryByPlaceholderText(/Steer Jarvis mid-turn/)).toBeNull());
}

function lastUserText(body: Record<string, unknown>): string {
  const msgs = body.messages as { role: string; content: string }[];
  return [...msgs].reverse().find((m) => m.role === "user")?.content ?? "";
}

/** A finished first exchange, then `titles` picked in the box, then a Try
 *  again held open (the chips stay in the box: a Try again re-sends the
 *  question it answers), then "compare them" queued with Ctrl+Enter and the
 *  turn stopped, so the queue waits for "Send now". */
async function queueWithChats(el: HTMLTextAreaElement, titles: string[]) {
  H.stream.replies.push("first answer");
  type(el, "first question");
  fireEvent.keyDown(el, { key: "Enter" });
  await screen.findByText("first answer");
  await idle();
  for (const t of titles) await pick(el, t);
  H.stream.hold = true;
  fireEvent.click(screen.getByRole("button", { name: "Regenerate reply" }));
  const steer = (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
  type(steer, "compare them");
  fireEvent.keyDown(steer, { key: "Enter", ctrlKey: true });
  await waitFor(() => expect(screen.getByTestId("queued-message").textContent).toContain("compare them"));
  // The chips went with the queued message.
  expect(chipTitles()).toEqual([]);
  fireEvent.click(screen.getByTitle("Stop this turn"));
  await waitFor(() =>
    expect(screen.getByTestId("queued-message").textContent).toContain("Waiting to send:"),
  );
  await idle();
}

describe("a queued message keeps its own chats; Edit and resend brings them back (v1.328.0)", () => {
  it("the queued turn sends only the chats it was queued with; a chat picked since waits for the next send", async () => {
    render(<ChatPage />);
    const el = await box();
    await queueWithChats(el, ["Tax plan for 2026"]);
    // The Try again re-sent its own question, which pointed at no chat.
    expect(H.stream.bodies).toHaveLength(2);
    expect("thread_refs" in H.stream.bodies[1]).toBe(false);
    // A different chat picked while the message waits.
    await pick(el, "Budget review");
    H.stream.replies.push("queued answer");
    fireEvent.click(screen.getByRole("button", { name: "Send now" }));
    await screen.findByText("queued answer");
    await idle();
    expect(H.stream.bodies).toHaveLength(3);
    expect(lastUserText(H.stream.bodies[2])).toBe("compare them");
    expect(H.stream.bodies[2].thread_refs).toEqual(["c-tax"]);
    // The new pick is still in the box, for the next message only.
    expect(chipTitles()).toEqual(["Budget review"]);
    type(el, "and this one?");
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(H.stream.bodies).toHaveLength(4));
    expect(H.stream.bodies[3].thread_refs).toEqual(["c-bud"]);
    await waitFor(() => expect(chipTitles()).toEqual([]));
  });

  it("Edit on a queued message puts its chats back ahead of those picked since: no repeats, at most 3", async () => {
    render(<ChatPage />);
    const el = await box();
    await queueWithChats(el, ["Tax plan for 2026", "Trip notes"]);
    await pick(el, "Tax plan for 2026");
    await pick(el, "Budget review");
    await pick(el, "Gift ideas");
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    await waitFor(() => expect(screen.queryByTestId("queued-message")).toBeNull());
    expect(el.value).toBe("compare them");
    await waitFor(() =>
      expect(chipTitles()).toEqual(["Tax plan for 2026", "Trip notes", "Budget review"]),
    );
  });

  it("Edit and resend brings the message's chats back as chips, and the resend carries them", async () => {
    render(<ChatPage />);
    const el = await box();
    await pick(el, "Tax plan for 2026");
    await pick(el, "Trip notes");
    type(el, "what did we decide?");
    fireEvent.keyDown(el, { key: "Enter" });
    await screen.findByText("Noted.");
    await idle();
    expect(chipTitles()).toEqual([]);
    fireEvent.click(screen.getByLabelText("Edit and resend"));
    await waitFor(() => expect(chipTitles()).toEqual(["Tax plan for 2026", "Trip notes"]));
    expect(el.value).toBe("what did we decide?");
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(H.stream.bodies).toHaveLength(2));
    expect(H.stream.bodies[1].thread_refs).toEqual(["c-tax", "c-trip"]);
  });
});

describe("keys in the @ menu (v1.328.0)", () => {
  it("↓ walks from the agents into the chats and Enter picks a chat, sending nothing", async () => {
    render(<ChatPage />);
    const el = await box();
    await openAndFind(el, "@", "Budget review");
    const active = () => {
      const row = screen.getByTestId("at-menu").querySelector('[data-active="true"]');
      return row ? titleOf(row) : "";
    };
    expect(active()).toContain("builder"); // the first row is the agent
    fireEvent.keyDown(el, { key: "ArrowDown" });
    await waitFor(() => expect(active()).toBe("Tax plan for 2026"));
    fireEvent.keyDown(el, { key: "ArrowDown" });
    await waitFor(() => expect(active()).toBe("Budget review"));
    fireEvent.keyDown(el, { key: "ArrowUp" });
    await waitFor(() => expect(active()).toBe("Tax plan for 2026"));
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(chipTitles()).toEqual(["Tax plan for 2026"]));
    expect(H.stream.bodies).toHaveLength(0);
    expect(el.value).toBe("");
  });

  it("Enter on the agent row still writes the @mention", async () => {
    render(<ChatPage />);
    const el = await box();
    await openAndFind(el, "@bu", "Budget review");
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(el.value).toBe("@builder "));
    expect(chipTitles()).toEqual([]);
  });

  it("Esc closes the menu and typing opens it again", async () => {
    render(<ChatPage />);
    const el = await box();
    await openAndFind(el, "@", "Budget review");
    fireEvent.keyDown(el, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("at-menu")).toBeNull());
    type(el, "@t");
    await waitFor(() => expect(screen.getByTestId("at-menu")).toBeTruthy());
  });

  it("with nothing to pick, Enter sends the words as they are", async () => {
    render(<ChatPage />);
    const el = await box();
    type(el, "@zzz");
    await waitFor(() => expect(H.refs.paths).toContain("/chat/threads/search-refs?q=zzz"));
    fireEvent.keyDown(el, { key: "Enter" });
    await waitFor(() => expect(H.stream.bodies).toHaveLength(1));
    const msgs = H.stream.bodies[0].messages as { content: string }[];
    expect(msgs.at(-1)?.content).toBe("@zzz");
  });
});

describe("helpers (v1.328.0)", () => {
  it("the search path leaves out the open chat only when there is one", () => {
    expect(chatRefSearchPath("")).toBe("/chat/threads/search-refs?q=");
    expect(chatRefSearchPath("a b&c", "t 1")).toBe("/chat/threads/search-refs?q=a%20b%26c&exclude=t%201");
  });

  it("decodes rows by whitelist: string ids, no blanks or repeats, a title for the untitled, at most 8", () => {
    const raw = {
      threads: [
        { id: "a", title: "One" },
        { id: "a", title: "dup" },
        { id: " ", title: "blank" },
        { id: 7, title: "number" },
        { id: "b", title: "(untitled)" },
        { id: "c" },
        ...Array.from({ length: 10 }, (_, i) => ({ id: `x${i}`, title: `X${i}` })),
      ],
    };
    const rows = decodeChatRefRows(raw);
    expect(rows.slice(0, 3)).toEqual([
      { id: "a", title: "One" },
      { id: "b", title: "Untitled chat" },
      { id: "c", title: "Untitled chat" },
    ]);
    expect(rows).toHaveLength(8);
    expect(decodeChatRefRows(null)).toEqual([]);
    expect(decodeChatRefRows({ threads: "no" })).toEqual([]);
  });

  it("adds at most 3, never twice, and says when full", () => {
    const one = { id: "1", title: "a" };
    let cur = addChatRef([], one).next;
    expect(addChatRef(cur, one)).toEqual({ next: cur, full: false });
    cur = addChatRef(cur, { id: "2", title: "b" }).next;
    cur = addChatRef(cur, { id: "3", title: "c" }).next;
    const r = addChatRef(cur, { id: "4", title: "d" });
    expect(r.full).toBe(true);
    expect(r.next).toBe(cur);
    expect(chatRefIds([...cur, { id: "4", title: "d" }, one])).toEqual(["1", "2", "3"]);
    expect(chatRefIds(undefined)).toEqual([]);
  });

  it("the visible rows leave out the picked, the open chat and stale non-matches", () => {
    const rows = [
      { id: "a", title: "Tax" },
      { id: "b", title: "Budget" },
      { id: "c", title: "Taxi" },
    ];
    expect(visibleChatRefRows(rows, [{ id: "c", title: "Taxi" }], "b", "tax")).toEqual([{ id: "a", title: "Tax" }]);
    expect(visibleChatRefRows(null, [], null, "")).toEqual([]);
  });

  it("typing reopens a dismissed @ menu (the store)", () => {
    const s = createComposerStore();
    s.setAtDismissed(true);
    s.type("@x", 2);
    expect(s.get().atDismissed).toBe(false);
  });

  it("the key hint names chats and keeps / for skills", () => {
    const hint = composerKeyHint(false, true);
    expect(hint).toContain("@ for agents and chats");
    expect(hint).toContain("/ for skills");
  });
});
