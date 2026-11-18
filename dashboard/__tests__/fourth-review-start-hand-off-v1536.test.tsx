/**
 * Fourth review C-DT-3 — "Start it now" on a hand-off that did not start.
 *
 * Plain words: a reply decided the request needs an agent run, but the run
 * could not start (it was refused, or Iron Jarvis closed first). Iron
 * Jarvis writes that line into the chat with `handOff: "not started"`. The
 * line now carries one press, "Start it now", that starts the run for the
 * same question, under the turn's own name (`handed_from_turn`), so the
 * daemon's one-run-per-hand-off guard gives back a run already made instead
 * of starting a second. The user no longer retypes the question.
 *
 * The page for real, transports mocked: the harness of
 * third-plan-g18-reply-home.test.tsx, with `POST /sessions` answering a run.
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
    stalled = false;
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    runs: [] as Record<string, unknown>[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    H.api.gets.push(path);
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    return H.api.postResponses[path] ?? {};
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
    textStore: { get: () => "", subscribe: () => () => {} },
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>) => {
      H.runs.push(body);
      return { reply: "streamed reply" };
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
    "/sessions/s1?transcript=0": { session: { id: "s1", status: "running" }, transcript: [] },
  });
  H.api.postResponses = { "/sessions": { id: "s1", status: "running" } };
  H.runs.length = 0;
  window.history.replaceState({}, "", "/chat?thread=t7");
  window.localStorage.clear();
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const NOT_STARTED =
  "This needs an agent run, and Iron Jarvis closed before it could start one. Send your message again to start it.";

function storeThread(last: Msg) {
  H.api.getResponses["/chat/threads/t7"] = {
    id: "t7",
    title: "chat t7",
    messages: [
      {
        role: "user",
        content: "make the report from these notes",
        turnId: "chat-h1",
        attachmentNames: ["notes.txt"],
        attachmentPaths: ["C:/work/notes.txt"],
      },
      last,
    ],
  };
}
const postTo = (path: string) => H.api.posts.find((p) => p.path === path);

describe("C-DT-3: a hand-off that did not start offers one press", () => {
  it("'Start it now' starts the run for the same question, under the turn's name", async () => {
    storeThread({
      role: "assistant",
      content: NOT_STARTED,
      turnId: "chat-h1",
      savedBy: "daemon",
      handOff: "not started",
      escalated: "this needs several steps",
    });
    render(<ChatPage />);
    expect(await screen.findByText(NOT_STARTED)).toBeInTheDocument();
    // The line does not claim a run that never started.
    expect(screen.queryByText(/Taking this on properly/)).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: "Start it now" }));
    await waitFor(() => expect(postTo("/sessions")).toBeTruthy());
    const body = postTo("/sessions")!.body;
    // Through the one-run-per-hand-off guard: the turn's own name.
    expect(body.handed_from_turn).toBe("chat-h1");
    expect(String(body.task)).toContain("make the report from these notes");
    expect(String(body.task)).toContain("Attached file: C:/work/notes.txt");
    expect(body.origin).toBe("chat");
    // The line that did not start is not handed on as conversation.
    expect(String(body.task)).not.toContain("closed before it could start");
    // The line becomes the hand-off line, ONE reply for the turn.
    expect(await screen.findByText("Taking this on properly: this needs several steps.")).toBeInTheDocument();
    expect(screen.queryByText(NOT_STARTED)).toBeNull();
    expect(screen.queryByRole("button", { name: "Start it now" })).toBeNull();
    await waitFor(() =>
      expect(
        H.api.puts.some((p) =>
          (p.body.messages as Msg[] | undefined)?.some((m) => m.awaitingSession === "s1"),
        ),
      ).toBe(true),
    );
    const saved = H.api.puts.at(-1)!.body.messages as Msg[];
    const turnReplies = saved.filter((m) => m.role === "assistant" && m.turnId === "chat-h1");
    expect(turnReplies).toHaveLength(1);
    expect(turnReplies[0]).toMatchObject({ escalated: "this needs several steps", awaitingSession: "s1" });
    // Nothing was asked of the chat lane again.
    expect(H.runs).toEqual([]);
  });

  it("control: a line without 'not started' shows no new press", async () => {
    storeThread({ role: "assistant", content: "Here is the report.", turnId: "chat-h1" });
    render(<ChatPage />);
    expect(await screen.findByText("Here is the report.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start it now" })).toBeNull();
    expect(postTo("/sessions")).toBeUndefined();
  });
});
