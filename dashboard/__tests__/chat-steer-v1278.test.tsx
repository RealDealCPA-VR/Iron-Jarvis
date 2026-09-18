/**
 * v1.278.0 — steer a running chat turn from the composer.
 *
 * The sidebar could say "shorter" to a turn already working (v1.242.0); the
 * chat page could only Stop and retype. Now every chat turn is NAMED
 * (`turn_id` on the stream body), Enter while it runs posts the box to
 * `POST /chat/turns/{id}/steer` as a note the turn reads at its next step, the
 * note is shown under the live reply until the turn ends, and the notes the
 * turn READ (they ride the `round` frame back as `steered`) join the saved
 * conversation as the user's own messages — the model saw them as such, and
 * the next turn resends the whole history.
 *
 * Pinned on the real page with the transport mocked; the stream mock holds
 * the turn open until the test releases it, so the mid-turn window is real.
 * Header, mocks and helpers are the reasoning-level harness with a held run.
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

describe("a chat turn is steered from the composer (v1.278.0)", () => {
  it("names the turn, posts Enter mid-turn as a note to it, and folds the read notes into the conversation", async () => {
    render(<ChatPage />);
    const body = await startTurn("hello");
    const turnId = body.turn_id;
    expect(typeof turnId).toBe("string");
    expect((turnId as string).length).toBeGreaterThan(8);

    // Mid-turn the box says what Enter does now, and Enter steers.
    const el = await screen.findByPlaceholderText(/Steer Jarvis mid-turn/);
    type(el as HTMLTextAreaElement, "make it shorter");
    enter(el as HTMLTextAreaElement);
    await waitFor(() => expect(steerPosts()).toHaveLength(1));
    expect(steerPosts()[0]).toEqual({
      path: `/chat/turns/${encodeURIComponent(turnId as string)}/steer`,
      body: { text: "make it shorter" },
    });
    // No second turn was started by that Enter.
    expect(H.stream.bodies).toHaveLength(1);
    // The box is cleared and the note is shown under the live reply, honestly worded.
    await waitFor(() => expect((el as HTMLTextAreaElement).value).toBe(""));
    const strip = await screen.findByTestId("steer-notes");
    expect(strip.textContent).toContain("Steer sent: make it shorter");
    expect(strip.textContent).toContain("next step");

    // The turn ends having READ the note (it rode a round frame back).
    H.stream.release!({ reply: "brief answer", steered: ["make it shorter"] });
    await waitFor(() => expect(screen.queryByTestId("steer-notes")).toBeNull());
    // The conversation now holds: hello · [steer] make it shorter · brief answer.
    const label = await screen.findByTestId("steer-label");
    expect(label.parentElement?.textContent).toContain("make it shorter");
    expect(screen.getByText("brief answer")).not.toBeNull();
    const saved = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1)!;
    const msgs = saved.body.messages as { role: string; content: string; steer?: boolean }[];
    expect(msgs.map((m) => [m.role, m.content, m.steer ?? false])).toEqual([
      ["user", "hello", false],
      ["user", "make it shorter", true],
      ["assistant", "brief answer", false],
    ]);
    // A steer note is not editable (it was read inside a turn); the question is.
    expect(screen.getAllByLabelText("Edit and resend")).toHaveLength(1);

    // After the turn, Enter sends again — a NEW named turn, not a steer.
    const again = await composer();
    type(again, "and now?");
    enter(again);
    await waitFor(() => expect(H.stream.bodies.length).toBe(2));
    expect(H.stream.bodies[1].turn_id).not.toBe(turnId);
    expect(steerPosts()).toHaveLength(1);
    // The resend carries the steer note as history — the model saw it.
    const history = H.stream.bodies[1].messages as { role: string; content: string }[];
    expect(history.map((m) => m.content)).toEqual(["hello", "make it shorter", "brief answer", "and now?"]);
  });

  it("a note the daemon refuses (the turn already finished) stays in the box and says so", async () => {
    render(<ChatPage />);
    const body = await startTurn("hello");
    H.api.postResponses[`/chat/turns/${encodeURIComponent(body.turn_id as string)}/steer`] =
      new H.FakeApiError("no such running turn", 404);
    const el = (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
    type(el, "shorter");
    enter(el);
    await waitFor(() => expect(steerPosts()).toHaveLength(1));
    expect(await screen.findByText(/already finished — send it as a new message/)).not.toBeNull();
    expect(el.value).toBe("shorter");
    expect(screen.queryByTestId("steer-notes")).toBeNull();
    H.stream.release!({ reply: "done" });
    await waitFor(() => expect(screen.queryByPlaceholderText(/Steer Jarvis/)).toBeNull());
  });

  it("an empty Enter mid-turn posts nothing", async () => {
    render(<ChatPage />);
    await startTurn("hello");
    const el = (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
    type(el, "   ");
    enter(el);
    await new Promise((r) => setTimeout(r, 20));
    expect(steerPosts()).toHaveLength(0);
    H.stream.release!({ reply: "done" });
    await screen.findByText("done");
  });

  it("a turn that read no note saves nothing extra", async () => {
    render(<ChatPage />);
    await startTurn("hello");
    H.stream.release!({ reply: "done" });
    await screen.findByText("done");
    const saved = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/")).at(-1)!;
    const msgs = saved.body.messages as { role: string; content: string }[];
    expect(msgs.map((m) => m.content)).toEqual(["hello", "done"]);
    expect(screen.queryByTestId("steer-label")).toBeNull();
    void box;
  });
});

describe("the round frame carries the note the turn read (v1.278.0)", () => {
  it("decodes steer when present and leaves it absent otherwise", async () => {
    // The page above needs the hook mocked; the decoder is the real one.
    const { decodeSSE } = await vi.importActual<typeof import("@/lib/useChatStream")>("@/lib/useChatStream");
    expect(decodeSSE("round", JSON.stringify({ round: 2, steer: "make it shorter" }))).toEqual({
      type: "round",
      round: 2,
      steer: "make it shorter",
    });
    expect(decodeSSE("round", JSON.stringify({ round: 1 }))).toEqual({ type: "round", round: 1 });
    expect(decodeSSE("round", JSON.stringify({ round: 1, steer: "" }))).toEqual({ type: "round", round: 1 });
    expect(decodeSSE("round", JSON.stringify({ round: 1, steer: 7 }))).toEqual({ type: "round", round: 1 });
  });

  it("the real hook collects every round's note onto the resolved result, in order", async () => {
    // The decode pin cannot see the hook's own collection (the done-frame
    // lesson of v1.165.0): drive the REAL hook over a mocked SSE fetch.
    const { useChatStream } = await vi.importActual<typeof import("@/lib/useChatStream")>(
      "@/lib/useChatStream",
    );
    const { renderHook, act } = await import("@testing-library/react");
    const frames =
      `event: round\ndata: ${JSON.stringify({ round: 0 })}\n\n` +
      `event: round\ndata: ${JSON.stringify({ round: 1, steer: "shorter" })}\n\n` +
      `event: round\ndata: ${JSON.stringify({ round: 2, steer: "in French" })}\n\n` +
      `event: done\ndata: ${JSON.stringify({ reply: "court" })}\n\n`;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    try {
      const { result } = renderHook(() => useChatStream());
      let settled: { reply: string; steered?: string[] } | null = null;
      await act(async () => {
        settled = (await result.current.run({ messages: [] })) as typeof settled;
      });
      expect(settled!.reply).toBe("court");
      expect(settled!.steered).toEqual(["shorter", "in French"]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("a turn with no note resolves without a steered field", async () => {
    const { useChatStream } = await vi.importActual<typeof import("@/lib/useChatStream")>(
      "@/lib/useChatStream",
    );
    const { renderHook, act } = await import("@testing-library/react");
    const frames = `event: round\ndata: {"round":0}\n\nevent: done\ndata: {"reply":"r"}\n\n`;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    try {
      const { result } = renderHook(() => useChatStream());
      let settled: { reply: string; steered?: string[] } | null = null;
      await act(async () => {
        settled = (await result.current.run({ messages: [] })) as typeof settled;
      });
      expect(settled!.reply).toBe("r");
      expect("steered" in settled!).toBe(false);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
