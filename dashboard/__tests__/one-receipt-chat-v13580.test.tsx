/**
 * v1.358.0 (wave 8, lane 8C) — on the REAL chat page: a helper-agent run has
 * ONE receipt, and a cut-off reply's line says it was cut off.
 *
 * ACCEPTANCE, as a new user meets it in a saved chat:
 *  - the settled hand-off row carries the run's facts (how it ended, what it
 *    did, the model, "Open run") and opens to its step-by-step log; the reply
 *    under it shows the agent's words plus only what the row does not say
 *    (the files it made, the errors folded, Try again) — no second boxed
 *    card, no "answered by", no "Full transcript", no "Task complete";
 *  - a run with no such row (a reply composed before v1.352.0's receipt words)
 *    keeps the full card, in the status table's words;
 *  - a reply that was cut off reads "Cut off after N s" above "The reply was
 *    cut off." — never "✓ Answered in N s".
 *
 * The chat page is rendered for real; only the transport hooks are mocked
 * (the agent-runs-in-chat-v13520 harness).
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
import type { RunResult } from "@/components/chat/RunResultCard";
import { statusWords } from "@/lib/statusWords";

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

/** Every text node a sighted user can see (a CLOSED <details> shows only its
 *  summary; a CLOSED fold renders nothing). */
function visibleText(root: Element): string {
  const out: string[] = [];
  const walk = (n: Node) => {
    if (n.nodeType === Node.TEXT_NODE) {
      out.push(n.textContent ?? "");
      return;
    }
    if (n instanceof HTMLDetailsElement && !n.open) {
      const s = n.querySelector("summary");
      if (s) walk(s);
      return;
    }
    n.childNodes.forEach(walk);
  };
  walk(root);
  return out.join(" ");
}

/** The ledger's account of a hand-off that searched the chats, had one memory
 *  lookup fail, and saved a file. */
const RESULT: RunResult = {
  found: true,
  session_id: "s1",
  status: "completed",
  task: "find what we decided",
  summary: "",
  steps: 6,
  tools_used: [
    { tool: "history_search", count: 83 },
    { tool: "memory_read", count: 1 },
    { tool: "write_file", count: 1 },
  ],
  tools_failed: [{ tool: "memory_read", count: 1 }],
  files_created: ["decision.md"],
  files_changed: [],
  errors: [{ tool: "memory_read", error: "missing required: layer" }],
  revertable: 1,
  duration_s: 125,
  outcome: "completed_with_failures",
  provider: "claude-cli",
  model: "claude-opus-5-5",
  agent_name: null,
  agent_type: "researcher",
  route_note: "",
  activity: [
    { at: "2026-10-19T09:00:01", who: "researcher", session_id: "s1", text: "researcher searched your chats", tone: "ok" },
    { at: "2026-10-19T09:00:05", who: "researcher", session_id: "s1", text: "researcher wrote decision.md", tone: "ok" },
  ],
};

/** The hand-off as finalize saved it (lib/agentRun settleHandOff since
 *  v1.352.0: how it ended, the served model, the per-tool counts). */
const SETTLED_HANDOFF: Msg = {
  role: "assistant",
  content: "",
  escalated: "needs the agent",
  escalatedTo: "the researcher",
  agentRun: {
    session: "s1",
    startedAt: 1_000,
    endedAt: 126_000,
    steps: 6,
    status: "completed",
    outcome: "completed_with_failures",
    provider: "claude-cli",
    model: "claude-opus-5-5",
    tools: [
      { tool: "history_search", count: 83, failed: 0 },
      { tool: "memory_read", count: 1, failed: 1 },
      { tool: "write_file", count: 1, failed: 0 },
    ],
  },
};

const REPLY: Msg = {
  role: "assistant",
  content: "You chose the S-corp election in March.",
  fromSession: "s1",
  runResult: RESULT,
};

const QUESTION: Msg = { role: "user", content: "find what we decided about the S-corp" };

describe("one helper-agent run, one receipt (on the real chat page)", () => {
  it("the row keeps the facts; the reply under it carries no second copy of them", async () => {
    storeThread([QUESTION, SETTLED_HANDOFF, REPLY]);
    render(<ChatPage />);
    expect(await screen.findByText("You chose the S-corp election in March.")).toBeTruthy();
    // The reply's part loads lazily (a deferred panel): wait for IT.
    const rest = await screen.findByTestId("run-result-rest", {}, { timeout: 10_000 });
    // ONE row says how the run went, and ONE link opens it.
    const rows = screen.getAllByTestId("agent-run-row");
    expect(rows).toHaveLength(1);
    expect(screen.getByTestId("agent-run-words").textContent).toBe(
      `The researcher worked for 2 min 5 s · 6 steps · ${"part failed"}`,
    );
    expect(screen.getAllByText("Open run")).toHaveLength(1);
    // The reply draws no second headline, no "answered by", no chips, no
    // "Full transcript", no "Task complete", no second "What it did" fold.
    for (const id of ["run-result-headline", "run-result-meta", "run-result-tool", "run-result-steps-toggle"]) {
      expect(screen.queryByTestId(id), id).toBeNull();
    }
    const page = visibleText(document.body);
    for (const words of ["answered by", "Full transcript", "Task complete", "Finished with failures"]) {
      expect(page, words).not.toContain(words);
    }
    // What only the reply knows is still there, in quiet lines.
    expect(within(rest).getByTestId("run-result-files-made").textContent).toBe("Made decision.md");
    expect(within(rest).getByRole("button", { name: /Try again/ })).toBeTruthy();
    expect(within(rest).getByTestId("run-result-errors-toggle")).toBeTruthy();
    // The facts are said ONCE, by the row: open it.
    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    const body = await screen.findByTestId("agent-run-receipt");
    expect(within(body).getByText("Searched your chats 83 times")).toBeTruthy();
    expect(within(body).getByText("Ran on Opus 5.5")).toBeTruthy();
    expect(screen.getAllByText("Searched your chats 83 times")).toHaveLength(1);
    // ...and its step-by-step log, which the reply's card used to fold.
    fireEvent.click(within(body).getByTestId("agent-run-steps-toggle"));
    const steps = within(screen.getByTestId("agent-run-activity")).getAllByRole("listitem");
    expect(steps.map((s) => s.textContent)).toEqual(["researcher searched your chats", "researcher wrote decision.md"]);
  });

  it("an agent-attributed reply (an @agent with hands) under its row: no second card, no second 'from … · time' line", async () => {
    storeThread([QUESTION, SETTLED_HANDOFF, { ...REPLY, panelWho: "builtin:researcher" }]);
    render(<ChatPage />);
    expect(await screen.findByText("You chose the S-corp election in March.")).toBeTruthy();
    await screen.findByTestId("run-result-rest", {}, { timeout: 10_000 });
    // The agent's name still heads its words, once.
    expect(screen.getByTestId("panel-who")).toBeTruthy();
    // The row already says who worked and for how long.
    expect(screen.queryByTestId("panel-receipt")).toBeNull();
    expect(screen.queryByTestId("run-result-headline")).toBeNull();
    expect(screen.getAllByTestId("agent-run-row")).toHaveLength(1);
  });

  it("CONTROL: an agent-attributed reply with NO row above keeps its 'from … · time' line and its full card", async () => {
    storeThread([QUESTION, { ...REPLY, panelWho: "builtin:researcher", at: "2026-10-19T09:02:05Z" }]);
    render(<ChatPage />);
    await screen.findByTestId("run-result-headline", {}, { timeout: 10_000 });
    expect(screen.getByTestId("panel-receipt")).toBeTruthy();
    expect(screen.queryByTestId("run-result-rest")).toBeNull();
  });

  it("CONTROL: a run with no receipt-carrying row keeps its full card, in the status table's words", async () => {
    // A hand-off settled before v1.352.0 (its stamp has only the times).
    const oldHandOff: Msg = {
      ...SETTLED_HANDOFF,
      agentRun: { session: "s1", startedAt: 1_000, endedAt: 126_000, steps: 6 },
    };
    storeThread([QUESTION, oldHandOff, { ...REPLY, runResult: { ...RESULT, outcome: "completed", tools_failed: [], errors: [] } }]);
    render(<ChatPage />);
    const headline = await screen.findByTestId("run-result-headline", {}, { timeout: 10_000 });
    expect(headline.textContent).toBe(statusWords("completed").word);
    expect(headline.textContent).toBe("Done");
    expect(screen.queryByTestId("run-result-rest")).toBeNull();
    expect(screen.getByTestId("run-result-meta").textContent).toMatch(/^answered by Opus 5\.5/);
    expect(screen.queryByText("Task complete")).toBeNull();
  });
});

describe("a cut-off reply's line says it was cut off (on the real chat page)", () => {
  const TIMING = { startedAt: 1_000, firstTokenAt: 1_400, endedAt: 3_100 };

  it("an interrupted plain reply: 'Cut off after 2.1 s' above 'The reply was cut off.', no check mark", async () => {
    storeThread([QUESTION, { role: "assistant", content: "You chose the S-corp elec", interrupted: true, timing: TIMING }]);
    render(<ChatPage />);
    const cut = await screen.findByTestId("reply-cut-note");
    expect(cut.textContent).toBe("The reply was cut off.");
    await waitFor(() => expect(screen.getByTestId("work-summary").textContent).toBe("Cut off after 2.1 s"));
    expect(screen.queryByTestId("work-line-check")).toBeNull();
    expect(screen.getByTestId("work-line-warn")).toBeTruthy();
    expect(screen.queryByText(/Answered in/)).toBeNull();
  });

  it("a reply that ran out of room says the same on its line", async () => {
    storeThread([QUESTION, { role: "assistant", content: "Part one of the answer", truncated: true, timing: TIMING }]);
    render(<ChatPage />);
    expect((await screen.findByTestId("reply-cut-note")).textContent).toBe("The reply ran out of room and stopped.");
    await waitFor(() => expect(screen.getByTestId("work-summary").textContent).toBe("Cut off after 2.1 s"));
    expect(screen.queryByTestId("work-line-check")).toBeNull();
  });

  it("CONTROL: a reply that ended on its own keeps '✓ Answered in 2.1 s'", async () => {
    storeThread([QUESTION, { role: "assistant", content: "You chose the S-corp election in March.", timing: TIMING }]);
    render(<ChatPage />);
    await waitFor(() => expect(screen.getByTestId("work-summary").textContent).toBe("Answered in 2.1 s"));
    expect(screen.getByTestId("work-line-check")).toBeTruthy();
    expect(screen.queryByTestId("reply-cut-note")).toBeNull();
  });
});
