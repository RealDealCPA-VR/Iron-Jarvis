/**
 * v1.402.0 ("approve once, then off to the races"), part 3b: a helper-agent
 * run that stopped FOR REAL at its step limit offers "Keep going".
 *
 * An attended run carries on ONCE by itself at its step budget (the SAME run
 * and session: the chat's hand-off simply keeps following it, pinned in
 * tests/test_attended_steps_v1402.py). When it stops at its limit again, the
 * chat's receipt says "Stopped at its step limit" and now offers Keep going
 * beside Try again: the press continues THAT run's session with its
 * conversation kept (POST /sessions/{id}/continue with `resume: true`) and
 * the chat follows the continuation to its result — where Try again only puts
 * the task text back (start over).
 *
 * The chat page is rendered for real; only the transport hooks are mocked
 * (the one-receipt-chat-v13580 harness).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      getCounts: {} as Record<string, number>,
      postResponses: {} as Record<string, unknown>,
    },
    run: {
      text: "",
      tools: [] as unknown[],
      phase: null as null | { phase: string; detail: string },
    },
    stream: { result: { reply: "streamed reply" } as Record<string, unknown> },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  sseUrl: (p: string) => `http://localhost/stub${p}`,
  get: async (path: string) => {
    H.api.gets.push(path);
    H.api.getCounts[path] = (H.api.getCounts[path] ?? 0) + 1;
    let r = H.api.getResponses[path];
    if (typeof r === "function") r = (r as (n: number) => unknown)(H.api.getCounts[path]);
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    if (r instanceof Error) throw r;
    return JSON.parse(JSON.stringify(r));
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body: JSON.parse(JSON.stringify(body ?? {})) });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body: JSON.parse(JSON.stringify(body)) });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => {
  class StreamError extends Error {
    status = 0;
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    StreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => ({
      streaming: false,
      text: "",
      tools: [],
      approval: null,
      run: async () => {
        await new Promise<void>((r) => setTimeout(r, 0));
        return H.stream.result;
      },
      abort: () => {},
    }),
  };
});

vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: H.run.text,
    tools: H.run.tools,
    phase: H.run.phase,
    active: true,
    start: () => {},
    stop: () => {},
  }),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: false }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false,
    reason: null,
    engine: null,
    listening: false,
    processing: false,
    transcript: "",
    interim: "",
    error: null,
    start: () => {},
    stop: () => {},
    reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false,
    enabled: false,
    speaking: false,
    enable: () => {},
    disable: () => {},
    toggle: () => {},
    speak: () => {},
    resetStream: () => {},
    speakMore: () => {},
    cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({
    byProvider: {},
    defaultProvider: "",
    loading: false,
    stale: false,
    refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { RunResultCard, KEEP_GOING_LABEL, type RunResult } from "@/components/chat/RunResultCard";
import { KEEP_GOING_MESSAGE, stoppedAtStepLimit } from "@/lib/agentRun";

type Msg = Record<string, unknown>;

function resetApi() {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  for (const k of Object.keys(H.api.getResponses)) delete H.api.getResponses[k];
  for (const k of Object.keys(H.api.getCounts)) delete H.api.getCounts[k];
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
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
  H.run.text = "";
  H.run.tools = [];
  H.run.phase = null;
  H.stream.result = { reply: "streamed reply" };
}

function storeThread(messages: Msg[]) {
  H.api.getResponses["/chat/threads/t8"] = { id: "t8", title: "saved", updated_at: "S1", messages };
  window.history.replaceState({}, "", "/chat?thread=t8");
}

beforeEach(() => {
  resetApi();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const STEP_LIMIT = "stopped: reached max steps before completion";

const STOPPED = {
  found: true,
  session_id: "s1",
  status: "failed",
  task: "rename the 40 files",
  summary: STEP_LIMIT,
  steps: 120,
  tools_used: [{ tool: "rename_file", count: 30 }],
  tools_failed: [],
  files_created: [],
  files_changed: [],
  errors: [],
  revertable: 0,
  duration_s: 300,
  outcome: null,
  provider: "claude-cli",
  model: "claude-opus-5-5",
  agent_name: null,
  agent_type: "builder",
  route_note: "",
  activity: [],
} as unknown as RunResult;

const QUESTION: Msg = { role: "user", content: "rename the 40 files" };
const STOPPED_REPLY: Msg = { role: "assistant", content: "", fromSession: "s1", runResult: STOPPED };

describe("RunResultCard: Keep going only under a run that stopped at its step limit", () => {
  it("is offered beside Try again, and calls back", () => {
    const keep = vi.fn();
    render(<RunResultCard result={STOPPED} onRetry={() => {}} onKeepGoing={keep} />);
    const button = screen.getByTestId("run-result-keep-going");
    expect(button.textContent).toContain(KEEP_GOING_LABEL);
    expect(screen.getByRole("button", { name: /Try again/ })).toBeTruthy();
    fireEvent.click(button);
    expect(keep).toHaveBeenCalledTimes(1);
  });

  it("in the quiet variant too", () => {
    const keep = vi.fn();
    render(<RunResultCard result={STOPPED} withRow onRetry={() => {}} onKeepGoing={keep} />);
    fireEvent.click(within(screen.getByTestId("run-result-actions")).getByTestId("run-result-keep-going"));
    expect(keep).toHaveBeenCalledTimes(1);
  });

  it("never under a run that failed for another reason, finished, or was stopped", () => {
    for (const r of [
      { ...STOPPED, summary: "The run failed: the model was unreachable." },
      { ...STOPPED, status: "completed", summary: "Renamed all 40." },
      { ...STOPPED, status: "cancelled", summary: "The agent was stopped before it finished." },
    ] as RunResult[]) {
      const { unmount } = render(<RunResultCard result={r} onRetry={() => {}} onKeepGoing={() => {}} />);
      expect(screen.queryByTestId("run-result-keep-going")).toBeNull();
      unmount();
    }
    expect(stoppedAtStepLimit(STOPPED)).toBe(true);
    expect(stoppedAtStepLimit({ status: "completed", summary: STEP_LIMIT })).toBe(false);
  });
});

describe("Keep going on the real chat page", () => {
  it("continues the stopped run's session WITH ITS CONVERSATION and follows the continuation to its result", async () => {
    storeThread([QUESTION, STOPPED_REPLY]);
    H.api.postResponses["/sessions/s1/continue"] = { id: "s2", status: "active", task: KEEP_GOING_MESSAGE };
    H.api.getResponses["/sessions/s2?transcript=0"] = {
      session: { id: "s2", status: "completed", summary: "Renamed the last 10 files.", created_at: "2026-10-28T10:00:00" },
      transcript: null,
    };
    render(<ChatPage />);
    const keep = await screen.findByTestId("run-result-keep-going", {}, { timeout: 10_000 });
    fireEvent.click(keep);
    // The continue resumes the run's own conversation (`resume`).
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/sessions/s1/continue")).toBe(true));
    const body = H.api.posts.find((p) => p.path === "/sessions/s1/continue")!.body;
    expect(body).toMatchObject({ message: KEEP_GOING_MESSAGE, wait: false, resume: true, chat_thread_id: "t8" });
    // The press is the user's words in the chat, and the chat WAITS on the
    // continuation (its mark, saved).
    await waitFor(() =>
      expect(
        H.api.puts.some((p) =>
          ((p.body as { messages?: Msg[] }).messages ?? []).some(
            (m) => m.role === "user" && m.content === KEEP_GOING_MESSAGE && m.awaitingSession === "s2",
          ),
        ),
      ).toBe(true),
    );
    // …and lands the continuation's result.
    expect(await screen.findByText("Renamed the last 10 files.", {}, { timeout: 10_000 })).toBeTruthy();
    expect(H.api.gets).toContain("/sessions/s2?transcript=0");
  });

  it("is offered only under the NEWEST reply", async () => {
    storeThread([QUESTION, STOPPED_REPLY, { role: "user", content: "never mind" }, { role: "assistant", content: "OK." }]);
    render(<ChatPage />);
    expect(await screen.findByText("OK.")).toBeTruthy();
    // The older run's card itself is drawn (a deferred panel): wait for IT.
    await screen.findByTestId("run-result-card", {}, { timeout: 10_000 });
    expect(screen.queryByTestId("run-result-keep-going")).toBeNull();
  });
});
