/**
 * v1.499.3 - a steer note the turn never read comes back when the turn FAILS.
 *
 * The daemon returned unread notes only on `done`; a turn that failed answered
 * with `error` and the note vanished. The error frame now carries
 * `unread_steers` too, the hook puts them on the StreamError, and the page
 * puts them back in the box (after the restored message), saying the turn
 * stopped before reading them. Harness: the v1.287.0 steer suite's.
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
    unreadSteers: string[] = [];
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
      fail: null as null | ((e: unknown) => void),
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
      return new Promise<Record<string, unknown>>((res, rej) => {
        H.stream.release = res;
        H.stream.fail = rej;
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

describe("a note the FAILED turn never read goes back in the box (v1.499.3)", () => {
  it("restores the message and the note, and says the turn stopped before reading it", async () => {
    render(<ChatPage />);
    await startTurn("explain the deduction");
    const el = (await screen.findByPlaceholderText(/Steer Jarvis mid-turn/)) as HTMLTextAreaElement;
    type(el, "make it shorter");
    enter(el);
    await waitFor(() => expect(steerPosts()).toHaveLength(1));
    await waitFor(() => expect(el.value).toBe(""));

    const err = new H.FakeStreamError("the model server went away", 0);
    err.unreadSteers = ["make it shorter"];
    H.stream.fail!(err);
    await waitFor(() => expect(screen.queryByPlaceholderText(/Steer Jarvis/)).toBeNull());
    const back = await composer();
    await waitFor(() => expect(back.value).toBe("explain the deduction\nmake it shorter"));
    const note = await screen.findByTestId("steer-unread");
    expect(note.textContent).toBe("Jarvis stopped before reading this. Press Enter to send it.");
  });

  it("a failure with no unread note leaves only the restored message", async () => {
    render(<ChatPage />);
    await startTurn("hello");
    H.stream.fail!(new H.FakeStreamError("down", 0));
    const back = await composer();
    await waitFor(() => expect(back.value).toBe("hello"));
    expect(screen.queryByTestId("steer-unread")).toBeNull();
  });
});

describe("the error frame's unread_steers reaches the StreamError (v1.499.3)", () => {
  async function failOver(error: Record<string, unknown>) {
    const actual = await vi.importActual<typeof import("@/lib/useChatStream")>(
      "@/lib/useChatStream",
    );
    const { renderHook, act } = await import("@testing-library/react");
    const frames =
      `event: round\ndata: ${JSON.stringify({ round: 0 })}\n\n` +
      `event: error\ndata: ${JSON.stringify(error)}\n\n`;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    try {
      const { result } = renderHook(() => actual.useChatStream());
      let caught: unknown = null;
      await act(async () => {
        try {
          await result.current.run({ messages: [] });
        } catch (e) {
          caught = e;
        }
      });
      expect(caught).toBeInstanceOf(actual.StreamError);
      return caught as InstanceType<typeof actual.StreamError>;
    } finally {
      vi.unstubAllGlobals();
    }
  }

  it("carries the notes, whitelisted", async () => {
    const e = await failOver({ detail: "boom", unread_steers: ["shorter", "", 7, "in French"] });
    expect(e.unreadSteers).toEqual(["shorter", "in French"]);
  });

  it("absent or malformed is an empty list", async () => {
    for (const error of [{ detail: "boom" }, { detail: "boom", unread_steers: "shorter" }]) {
      const e = await failOver(error);
      expect(e.unreadSteers).toEqual([]);
    }
  });
});
