/**
 * v1.287.0 — a steer note the turn never read comes BACK to the composer.
 *
 * "Steer sent — Jarvis reads it at its next step" was a promise the daemon
 * could not always keep: a plain question ends after its first round, and the
 * last round of any tool job has no step after it. The note was accepted,
 * the box cleared, and the words vanished. The stream lane now returns such
 * notes as `done.unread_steers`; the page puts them back in the box (ahead of
 * anything typed since) and says, in one plain sentence, what happened. They
 * are NOT folded into the conversation — the model never saw them.
 *
 * Harness: the v1.278.0 steer suite's (real page, transport mocked, the
 * stream held open until the test releases it), plus the REAL hook over a
 * mocked SSE fetch for the decode.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    stream: {
      bodies: [] as Record<string, unknown>[],
      /** Set by the mock while a run is pending; the test resolves the turn. */
      release: null as null | ((r: Record<string, unknown>) => void),
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
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
    run: (body: Record<string, unknown>) => {
      H.stream.bodies.push(body);
      return new Promise<Record<string, unknown>>((res) => {
        H.stream.release = res;
      });
    },
    abort: () => {},
  }),
}));
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

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.release = null;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
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

const box = () => screen.getByRole("textbox", { name: "" }) as HTMLTextAreaElement;
const composer = async () => (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;

function type(el: HTMLTextAreaElement, text: string) {
  fireEvent.change(el, { target: { value: text } });
}
function enter(el: HTMLTextAreaElement) {
  fireEvent.keyDown(el, { key: "Enter" });
}
const steerPosts = () => H.api.posts.filter((p) => /^\/chat\/turns\/.+\/steer$/.test(p.path));

async function startTurn(text: string) {
  const el = await composer();
  type(el, text);
  enter(el);
  await waitFor(() => expect(H.stream.bodies.length).toBe(1));
  await waitFor(() => expect(H.stream.release).not.toBeNull());
  return H.stream.bodies[0];
}

describe("a note the turn finished before reading goes back in the box (v1.287.0)", () => {
  it("returns the note to the composer, says why, keeps it out of the conversation, and Enter sends it", async () => {
    render(<ChatPage />);
    await startTurn("explain the QBI deduction");
    const el = (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
    type(el, "make it shorter");
    enter(el);
    await waitFor(() => expect(steerPosts()).toHaveLength(1));
    await waitFor(() => expect(el.value).toBe(""));
    expect((await screen.findByTestId("steer-notes")).textContent).toContain("Steer sent: make it shorter");

    // The turn ends WITHOUT reading it: the daemon hands it back.
    H.stream.release!({ reply: "a long answer", unreadSteers: ["make it shorter"] });
    await waitFor(() => expect(screen.queryByPlaceholderText(/Steer Jarvis/)).toBeNull());
    const back = await composer();
    await waitFor(() => expect(back.value).toBe("make it shorter"));
    const note = await screen.findByTestId("steer-unread");
    expect(note.textContent).toBe("Jarvis finished before reading this — press Enter to send it.");
    expect(screen.queryByTestId("steer-notes")).toBeNull();
    // Not in the conversation: the model never saw it.
    expect(screen.queryByTestId("steer-label")).toBeNull();
    const saved = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1)!;
    const msgs = saved.body.messages as { role: string; content: string }[];
    expect(msgs.map((m) => m.content)).toEqual(["explain the QBI deduction", "a long answer"]);

    // Enter sends it as the next message, and the sentence goes with it.
    enter(back);
    await waitFor(() => expect(H.stream.bodies.length).toBe(2));
    const history = H.stream.bodies[1].messages as { role: string; content: string }[];
    expect(history.map((m) => m.content)).toEqual([
      "explain the QBI deduction",
      "a long answer",
      "make it shorter",
    ]);
    await waitFor(() => expect(screen.queryByTestId("steer-unread")).toBeNull());
  });

  it("puts the returned note AHEAD of anything typed since", async () => {
    render(<ChatPage />);
    await startTurn("hello");
    const el = (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
    type(el, "shorter");
    enter(el);
    await waitFor(() => expect(steerPosts()).toHaveLength(1));
    await waitFor(() => expect(el.value).toBe(""));
    type(el, "and in French");
    H.stream.release!({ reply: "done", unreadSteers: ["shorter"] });
    await waitFor(async () => expect((await composer()).value).toBe("shorter\nand in French"));
    expect(await screen.findByTestId("steer-unread")).not.toBeNull();
  });

  it("a turn that read every note says nothing and leaves the box alone", async () => {
    render(<ChatPage />);
    await startTurn("hello");
    H.stream.release!({ reply: "done" });
    await screen.findByText("done");
    expect((await composer()).value).toBe("");
    expect(screen.queryByTestId("steer-unread")).toBeNull();
  });
});

describe("the done frame's unread_steers reaches the result (v1.287.0)", () => {
  async function runOver(done: Record<string, unknown>) {
    const { useChatStream } = await vi.importActual<typeof import("@/lib/useChatStream")>(
      "@/lib/useChatStream",
    );
    const { renderHook, act } = await import("@testing-library/react");
    const frames =
      `event: round\ndata: ${JSON.stringify({ round: 0 })}\n\n` +
      `event: done\ndata: ${JSON.stringify(done)}\n\n`;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    try {
      const { result } = renderHook(() => useChatStream());
      let settled: { reply: string; unreadSteers?: string[] } | null = null;
      await act(async () => {
        settled = (await result.current.run({ messages: [] })) as typeof settled;
      });
      return settled!;
    } finally {
      vi.unstubAllGlobals();
    }
  }

  it("the real hook carries the notes onto the resolved result", async () => {
    const r = await runOver({ reply: "r", unread_steers: ["shorter", "in French"] });
    expect(r.reply).toBe("r");
    expect(r.unreadSteers).toEqual(["shorter", "in French"]);
  });

  it("absent, empty or malformed leaves no unreadSteers field", async () => {
    for (const done of [
      { reply: "r" },
      { reply: "r", unread_steers: [] },
      { reply: "r", unread_steers: "shorter" },
      { reply: "r", unread_steers: [7, "", "  "] },
    ]) {
      const r = await runOver(done);
      expect("unreadSteers" in r).toBe(false);
    }
  });
});
