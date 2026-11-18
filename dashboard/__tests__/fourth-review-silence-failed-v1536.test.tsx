/**
 * Fourth review DT2 — after a minute of silence, a reply that FAILED keeps
 * its whole text and says its own reason.
 *
 * Plain words: the page lost contact with a reply for a minute, and the
 * reply meanwhile ended with an error (or was cut off by a restart). The
 * page used to treat that like a reply Iron Jarvis did not know: it saved
 * only the text that had reached it (that copy then won over Iron Jarvis's
 * fuller record) and said "this reply is no longer running" instead of the
 * reply's own failure sentence. Now an ended "error" or "interrupted" reply
 * is followed once more (a replay runs nothing), so the whole text is saved
 * and the failure line says the reply's own reason, with Retry. The "still
 * working" words after a second silence are for a running reply only.
 *
 * The page for real, transports mocked: the harness of
 * third-plan-g18-reply-home.test.tsx (`GET /chat/turns/{id}` answers with
 * the turn's status; a follow again is a run with `rejoin`).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

type Behaviour =
  | { kind: "reply"; result: Record<string, unknown> }
  | { kind: "fail"; err: Record<string, unknown> };

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
    stalled = false;
    constructor(message: string, status = 0, fields: Record<string, unknown> = {}) {
      super(message, status);
      this.name = "StreamError";
      Object.assign(this, fields);
    }
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
    stream: { live: false, text: "" },
    /** What each run does, in order; empty = reply "streamed reply". */
    plan: [] as Behaviour[],
    runs: [] as { body: Record<string, unknown>; rejoin?: string }[],
    /** The daemon's answer to GET /chat/turns/{id} (null = 404). */
    turnRow: null as null | ((id: string) => unknown),
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    let r = H.api.getResponses[path];
    const named = /^\/chat\/turns\/([^/?]+)$/.exec(path);
    if (r === undefined && named && H.turnRow) r = H.turnRow(decodeURIComponent(named[1]));
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    return {};
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
    streaming: H.stream.live,
    text: H.stream.text,
    textStore: { get: () => H.stream.text, subscribe: () => () => {} },
    tools: [],
    approval: null,
    run: async (
      body: Record<string, unknown>,
      _onDelta: (delta: string, full: string) => void,
      _onReset?: () => void,
      opts?: { rejoin?: string },
    ) => {
      H.runs.push({ body, rejoin: opts?.rejoin });
      const b = H.plan.shift() ?? ({ kind: "reply", result: { reply: "streamed reply" } } as Behaviour);
      await new Promise<void>((r) => setTimeout(r, 0));
      if (b.kind === "fail") {
        throw new H.FakeStreamError(String(b.err.message ?? "failed"), Number(b.err.status ?? 0), b.err);
      }
      return b.result;
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

type Msg = Record<string, unknown>;

beforeEach(() => {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  for (const k of Object.keys(H.api.getResponses)) delete H.api.getResponses[k];
  Object.assign(H.api.getResponses, {
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
  });
  H.plan.length = 0;
  H.runs.length = 0;
  H.stream.live = false;
  H.stream.text = "";
  H.turnRow = null;
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function typeAndSend(text: string) {
  const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}
const threadPuts = () => H.api.puts.filter((p) => /^\/chat\/threads\/[^/]+$/.test(p.path));
const msgsOf = (p: { body: Record<string, unknown> }) => p.body.messages as Msg[];
const lastPut = () => threadPuts().at(-1)!;
const replies = (list: Msg[]) => list.filter((m) => m.role === "assistant");
const cutSaved = () => H.api.puts.some((p) => JSON.stringify(p.body).includes("half of the answ"));

/** The first follow: a minute of silence after part of the reply arrived. */
const stall: Behaviour = {
  kind: "fail",
  err: { message: "Nothing came back", status: 0, stalled: true, partial: "half of the answ", committed: true },
};
/** The follow again: the replay of the record ends in the turn's own error. */
const replayFailed: Behaviour = {
  kind: "fail",
  err: {
    message: "the model service answered 503",
    status: 0,
    partial: "the whole answer so far",
    committed: true,
    title: "The model service turned the request down",
    fix: "Try another model.",
  },
};
const OWN_REASON = "The model service turned the request down. Try another model.";

describe("DT2: an ended reply that failed is followed once more after a silence", () => {
  it.each(["error", "interrupted"])(
    "status %s: followed again, the whole text saved, its own reason shown with Retry",
    async (status) => {
      H.plan.push(stall);
      H.plan.push(replayFailed);
      H.turnRow = (id) => ({ turn_id: id, status, running: false, started_at: null, thread_id: "t1" });
      render(<ChatPage />);
      await typeAndSend("write the summary");
      // Wait for the thing asserted: the failure line in the turn's own words.
      await waitFor(() => expect(screen.getByTestId("chat-turn-error").textContent).toContain(OWN_REASON));
      const turnId = H.runs[0].body.turn_id as string;
      expect(H.runs.length).toBe(2);
      expect(H.runs[1].rejoin).toBe(turnId);
      expect(screen.getByTestId("chat-turn-error").textContent).not.toMatch(/no longer running/);
      expect(screen.getByRole("button", { name: /^Retry/ })).toBeInTheDocument();
      // The whole text is the saved reply; the cut copy is never saved.
      await waitFor(() => expect(replies(msgsOf(lastPut())).length).toBe(1));
      expect(replies(msgsOf(lastPut()))[0]).toMatchObject({ content: "the whole answer so far", turnId });
      expect(cutSaved()).toBe(false);
    },
  );

  it("a second silence over an ended reply never says it is still working, and saves no cut copy", async () => {
    H.plan.push(stall);
    H.plan.push(stall);
    H.turnRow = (id) => ({ turn_id: id, status: "error", running: false, started_at: null, thread_id: "t1" });
    render(<ChatPage />);
    await typeAndSend("write the summary");
    await waitFor(() =>
      expect(screen.getByTestId("chat-notice").textContent).toMatch(/The reply has ended/),
    );
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(H.runs.length).toBe(2);
    expect(screen.getByTestId("chat-notice").textContent).not.toMatch(/still working/);
    // Iron Jarvis writes the ended reply into the chat itself.
    expect(cutSaved()).toBe(false);
  });

  it("control: a reply the daemon does not know still says 'no longer running', with Retry", async () => {
    H.plan.push(stall);
    render(<ChatPage />);
    await typeAndSend("write the summary");
    expect(await screen.findByRole("button", { name: /^Retry/ })).toBeInTheDocument();
    expect(H.runs.length).toBe(1);
    expect(screen.getByTestId("chat-turn-error").textContent).toMatch(/no longer running/);
  });
});
