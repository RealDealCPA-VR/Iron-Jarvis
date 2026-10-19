/**
 * v1.352.0 lane 2A (readiness audit G16, G19, G21): a helper agent's run and
 * every reply say what happened, in the receipt's words.
 *
 * Plain words, as a new user meets them:
 *  - G16: pressing Stop on a helper agent leaves a neutral "Stopped" card
 *    under its reply, never a red "Task failed"; the hand-off row says
 *    "… · stopped". A run that ran out of steps says "Stopped at its step
 *    limit".
 *  - G19: once a hand-off ends the row reads "The researcher worked for 2 min
 *    5 s · 6 steps · part failed" and opens onto plain lines ("Searched your
 *    chats 83 times", "One memory lookup failed", "Ran on Opus 5.5", "in
 *    <folder> · Open folder"); the card under the reply reads "answered by
 *    Opus 5.5 · 2 min 5 s", with no raw tool id on screen. A hand-off the
 *    chat aimed at a REMOTE agent says it runs on this PC and how to reach
 *    the remote.
 *  - G21: every reply says where it ran: "answered by <model> on DGX Spark",
 *    "answered by Opus 5.5 · cloud", "· local"; the model menu never calls a
 *    cloud model just "included".
 *
 * Everything is driven through the real components and helpers; the chat
 * page is rendered for real with only the transport hooks mocked (the
 * agent-run-row-v1349 harness).
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
import { RunResultCard, resultHeadline, type RunResult } from "@/components/chat/RunResultCard";
import { AgentRunSettled, settledRunWords } from "@/components/chat/AgentRunRow";
import { AgentRunReceipt } from "@/components/chat/AgentRunReceipt";
import { TurnReceipt, replyWhere, type TurnRoute } from "@/components/chat/TurnReceipt";
import {
  handOffSentence,
  readAgentRun,
  settleHandOff,
  stampTools,
  stopHandOff,
  STAMP_TOOLS_MAX,
  type AgentRunStamp,
  type HandOffLike,
} from "@/lib/agentRun";
import { runActionLines, stepFailedWords, toolDidWords, toolFailedWords } from "@/lib/runWords";

type Msg = Record<string, unknown>;

/** A naive-UTC ISO stamp (the daemon's own shape) `ago` ms in the past. */
const isoAgo = (ago: number, from = Date.now()) => new Date(from - ago).toISOString().replace("Z", "");

/** The receipt of a hand-off that searched the chats 83 times, read two
 *  files, and had one memory lookup fail: the audit's own example. */
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
    { tool: "read_file", count: 2 },
  ],
  tools_failed: [{ tool: "memory_read", count: 1 }],
  files_created: [],
  files_changed: [],
  errors: [{ tool: "memory_read", error: "missing required: layer — memory_read needs [layer, key]; got [key]" }],
  revertable: 0,
  duration_s: 125,
  outcome: "completed_with_failures",
  provider: "claude-cli",
  model: "claude-opus-5-5",
  agent_name: null,
  agent_type: "researcher",
  route_note: "",
  activity: [],
};

const RAW_IDS = ["history_search", "memory_read", "read_file"];

/** Every text node a sighted user can see in `root`: a CLOSED <details>
 *  shows only its <summary>. */
function visibleText(root: Element): string {
  const out: string[] = [];
  const walk = (n: Node) => {
    if (n.nodeType === Node.TEXT_NODE) {
      out.push(n.textContent ?? "");
      return;
    }
    if (n instanceof HTMLDetailsElement && !n.open) {
      const summary = n.querySelector("summary");
      if (summary) walk(summary);
      return;
    }
    n.childNodes.forEach(walk);
  };
  walk(root);
  return out.join(" ");
}

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
  H.api.getResponses["/chat/threads/t7"] = { id: "t7", title: "saved", updated_at: "S1", messages };
  window.history.replaceState({}, "", "/chat?thread=t7");
}

const WAITING: Msg[] = [
  { role: "user", content: "find what we decided about the S-corp" },
  {
    role: "assistant",
    content: "",
    escalated: "needs the agent",
    escalatedTo: "the researcher",
    awaitingSession: "s1",
  },
];

const putsTo = (p: string) => H.api.puts.filter((x) => x.path === p);

beforeEach(() => {
  resetApi();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});

/* ================================================================ G16 */

describe("G16: a run you stopped reads Stopped, not Task failed", () => {
  const stopped: RunResult = {
    ...RESULT,
    status: "cancelled",
    summary: "Session cancelled by the user.",
    outcome: null,
    tools_used: [],
    tools_failed: [],
    errors: [],
    duration_s: 45,
  };

  it("a stopped run: a neutral 'Stopped' card (neutral border, quiet stop icon), Try again kept", () => {
    const onRetry = vi.fn();
    const { container } = render(<RunResultCard result={stopped} onRetry={onRetry} />);
    expect(screen.getByTestId("run-result-headline").textContent).toBe("Stopped");
    expect(screen.queryByText("Task failed")).toBeNull();
    const card = container.firstElementChild!;
    expect(card.className).not.toMatch(/tone-danger/);
    expect(card.className).toContain("border-white/[0.08]");
    const icon = screen.getByTestId("run-result-icon");
    expect(icon.getAttribute("data-end")).toBe("stopped");
    expect(icon.className).toContain("text-zinc-500");
    expect(icon.className).not.toMatch(/tone-(danger|warn|success)/);
    // A stopped run with no tools is not "a turn that described the work".
    expect(screen.queryByText(/No tools ran/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Try again/ }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("a failed run is still the red 'Task failed'; a run at its step limit says so in the warn tone", () => {
    const failed = { ...stopped, status: "failed", summary: "Session failed: ProviderError: boom" };
    const { container, unmount } = render(<RunResultCard result={failed} />);
    expect(screen.getByTestId("run-result-headline").textContent).toBe("Task failed");
    expect(container.firstElementChild!.className).toContain("border-tone-danger/25");
    expect(screen.getByTestId("run-result-icon").className).toContain("text-tone-danger");
    unmount();

    const limit = { ...stopped, status: "failed", summary: "stopped: reached max steps before completion" };
    const view = render(<RunResultCard result={limit} />);
    expect(screen.getByTestId("run-result-headline").textContent).toBe("Stopped at its step limit");
    expect(view.container.firstElementChild!.className).not.toMatch(/tone-danger/);
    expect(screen.getByTestId("run-result-icon").getAttribute("data-end")).toBe("step_limit");
    expect(screen.getByTestId("run-result-icon").className).toContain("text-tone-warn");
  });

  it("resultHeadline: cancelled before failed; the old words for every other case", () => {
    expect(resultHeadline(stopped)).toBe("Stopped");
    expect(resultHeadline({ ...stopped, unanswered_asks: 3 })).toBe("Stopped");
    expect(resultHeadline({ ...stopped, status: "failed" })).toBe("Task failed");
    expect(resultHeadline({ ...stopped, status: "failed", summary: "  Stopped: reached max steps" })).toBe(
      "Stopped at its step limit",
    );
    expect(resultHeadline({ ...RESULT, outcome: null, tools_failed: [] })).toBe("Task complete");
  });

  it("on the chat page: pressing Stop settles the hand-off as stopped and saves it", async () => {
    storeThread(WAITING);
    H.api.getResponses["/sessions/s1?transcript=0"] = {
      session: { id: "s1", status: "running", created_at: isoAgo(45_000) },
    };
    render(<ChatPage />);
    await screen.findByTestId("agent-run-row");
    // Wait for the THING the settle reads: the run's own start, shown by the
    // live row's clock (0:4x, from the session's created_at).
    await waitFor(() => expect(screen.getByTestId("agent-run-clock").textContent).toMatch(/^4\ds$/));
    fireEvent.click(screen.getByRole("button", { name: /Stop/ }));
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/sessions/s1/cancel")).toBe(true));
    await waitFor(() =>
      expect(screen.getByTestId("agent-run-words").textContent).toMatch(/^The researcher worked for \d+ s · stopped$/),
    );
    expect(screen.getByTestId("agent-run-row").getAttribute("data-state")).toBe("settled");
    await waitFor(() => expect(putsTo("/chat/threads/t7").length).toBeGreaterThan(0));
    const saved = putsTo("/chat/threads/t7").at(-1)!.body.messages as Msg[];
    expect(saved[1].awaitingSession).toBeUndefined();
    expect(readAgentRun(saved[1])).toMatchObject({ session: "s1", status: "cancelled" });
  });

  it("on the chat page: a stopped run comes home under a neutral 'Stopped' card, never a red 'Task failed'", async () => {
    // v1.352.0 review: the card a user meets on the REAL surface. The run was
    // stopped (its card's Stop, Running now, the mission screen) while this
    // chat waited: the daemon's own line arrives carrying the receipt of a
    // CANCELLED run, and the page renders that receipt as the card.
    const end = Date.now();
    const daemonLine = {
      role: "assistant",
      content: "Session cancelled by the user.",
      sessionId: "s1",
      agentType: "researcher",
      receipt: stopped,
    };
    let n = 0;
    H.api.getResponses["/chat/threads/t7"] = () => {
      n += 1;
      return n === 1
        ? { id: "t7", title: "saved", updated_at: "S1", messages: WAITING }
        : { id: "t7", title: "saved", updated_at: "S3", messages: [...WAITING, daemonLine] };
    };
    window.history.replaceState({}, "", "/chat?thread=t7");
    H.api.getResponses["/sessions/s1?transcript=0"] = {
      session: {
        id: "s1",
        status: "cancelled",
        summary: "Session cancelled by the user.",
        created_at: isoAgo(45_000, end),
        finished_at: isoAgo(0, end),
      },
    };
    render(<ChatPage />);
    await waitFor(() => expect(screen.getByTestId("run-result-headline").textContent).toBe("Stopped"));
    expect(screen.queryByText("Task failed")).toBeNull();
    expect(screen.getByTestId("run-result-icon").getAttribute("data-end")).toBe("stopped");
    await waitFor(() =>
      expect(screen.getByTestId("agent-run-words").textContent).toBe(
        "The researcher worked for 45 s · 6 steps · stopped",
      ),
    );
  });
});

/* ================================================================ G19 */

describe("G19: the run's words, from the ledger, never its tool ids", () => {
  it("lib/runWords: counted plain words, failures apart, built-ins by name, others by their own name", () => {
    expect(toolDidWords("history_search", 83)).toBe("Searched your chats 83 times");
    expect(toolDidWords("history_search", 1)).toBe("Searched your chats");
    expect(toolDidWords("read_file", 2)).toBe("Read 2 files"); // lib/stepWords' own words
    expect(toolDidWords("web_search", 3)).toBe("Searched the web 3 times");
    expect(toolDidWords("rename_real_file", 2)).toBe("Used rename real file 2 times");
    expect(toolDidWords("mcp__gmail__search_messages", 1)).toBe("Used gmail: search messages");
    expect(toolDidWords("history_search", 0)).toBeNull();
    expect(toolFailedWords("memory_read", 1)).toBe("One memory lookup failed");
    expect(toolFailedWords("history_search", 3)).toBe("3 chat searches failed");
    expect(toolFailedWords("read_file", 2)).toBe("2 file reads failed");
    expect(stepFailedWords("memory_read")).toBe("Memory lookup failed");
    expect(stepFailedWords("rename_real_file")).toBe("Rename real file call failed");
    // a failed call is counted once, as failed (the G07 rule)
    expect(runActionLines(RESULT.tools_used, RESULT.tools_failed).map((l) => [l.text, l.failed])).toEqual([
      ["Searched your chats 83 times", false],
      ["One memory lookup failed", true],
      ["Read 2 files", false],
    ]);
  });

  it("the card under the reply: 'answered by Opus 5.5 · 2 min 5 s', plain chips, errors in words with the raw text folded", () => {
    const { container } = render(
      <RunResultCard result={{ ...RESULT, route_note: "Opus could not be used (unreachable), so Sonnet answered." }} />,
    );
    expect(screen.getByTestId("run-result-meta").textContent).toBe(
      "answered by Opus 5.5 · 2 min 5 s · 6 steps · 86 tool calls",
    );
    // who: the friendly name; the provider and the raw id are the title
    expect(screen.getByTestId("run-result-model").getAttribute("title")).toBe("Claude Code · claude-opus-5-5");
    const chips = screen.getAllByTestId("run-result-tool");
    expect(chips.map((c) => c.textContent)).toEqual([
      "Searched your chats 83 times",
      "One memory lookup failed",
      "Read 2 files",
    ]);
    expect(chips.map((c) => c.getAttribute("title"))).toEqual(["history_search", "memory_read", "read_file"]);
    expect(chips[1].getAttribute("data-failed")).toBe("true");
    expect(chips[1].className).not.toContain("font-mono");
    // the route note, in the warn tone, under the headline
    expect(screen.getByTestId("run-result-route-note").className).toContain("text-tone-warn");
    // errors: words first, the tool's own output folded
    fireEvent.click(screen.getByRole("button", { name: /1 error/ }));
    const err = screen.getByTestId("run-result-error");
    const details = err.querySelector("details")!;
    expect(details.open).toBe(false);
    expect(details.querySelector("summary")!.textContent).toBe("Memory lookup failed");
    expect(within(err).getByTestId("run-result-error-raw").textContent).toContain("missing required: layer");
    // NOTHING a user can see names a raw tool id, nor the old "125.0s"
    const seen = visibleText(container);
    for (const id of RAW_IDS) expect(seen).not.toContain(id);
    expect(seen).not.toMatch(/\d+\.\ds/);
  });

  it("an older result (no provider, no model) shows no 'answered by', exactly as before", () => {
    render(<RunResultCard result={{ ...RESULT, provider: undefined, model: undefined }} />);
    expect(screen.queryByTestId("run-result-model")).toBeNull();
    expect(screen.getByTestId("run-result-meta").textContent).toBe("2 min 5 s · 6 steps · 86 tool calls");
  });

  it("settled row words: 'The researcher worked for 2 min 5 s · 6 steps · part failed', and every ending", () => {
    const base: AgentRunStamp = { session: "s1", startedAt: 1_000, endedAt: 126_000, steps: 6 };
    const w = (r: Partial<AgentRunStamp>) => settledRunWords("The researcher", { ...base, ...r });
    expect(w({ status: "completed", outcome: "completed_with_failures" })).toBe(
      "The researcher worked for 2 min 5 s · 6 steps · part failed",
    );
    expect(w({ status: "completed", outcome: "needs_you" })).toBe(
      "The researcher worked for 2 min 5 s · 6 steps · needs you",
    );
    expect(w({ status: "cancelled" })).toBe("The researcher worked for 2 min 5 s · 6 steps · stopped");
    expect(w({ status: "failed" })).toBe("The researcher worked for 2 min 5 s · 6 steps · failed");
    expect(w({ status: "failed", stepLimit: true })).toBe(
      "The researcher worked for 2 min 5 s · 6 steps · hit its step limit",
    );
    expect(w({ status: "completed" })).toBe("The researcher worked for 2 min 5 s · 6 steps");
    // no known length: the ending leads, said once
    expect(w({ status: "cancelled", startedAt: null, steps: null })).toBe("The researcher was stopped");
    expect(w({ status: "failed", startedAt: null })).toBe("The researcher could not finish · 6 steps");
    // a stamp from before v1.352.0 reads exactly as it did
    expect(w({})).toBe("The researcher worked for 2 min 5 s · 6 steps");
  });

  it("settleHandOff stamps how the run ended and what it did; read back as untrusted as the rest", () => {
    const hand: HandOffLike & { role: string; content: string } = {
      role: "assistant",
      content: "",
      escalated: "needs the agent",
      awaitingSession: "s1",
    };
    const row = {
      created_at: "2026-10-17T10:00:00",
      finished_at: "2026-10-17T10:02:05",
      status: "completed",
      outcome: "completed_with_failures",
      provider: "claude-cli",
      model: "claude-opus-5-5",
      workspace_path: "C:/Users/me/Documents/Iron Jarvis/2026-10-17 S-corp",
      summary: "Found it.",
    };
    const out = settleHandOff([hand], "s1", row, 6, null, { ...RESULT, route_note: "Sonnet stood in." });
    const run = readAgentRun(out[0])!;
    expect(run).toEqual({
      session: "s1",
      startedAt: Date.UTC(2026, 9, 17, 10, 0, 0),
      endedAt: Date.UTC(2026, 9, 17, 10, 2, 5),
      steps: 6,
      status: "completed",
      outcome: "completed_with_failures",
      provider: "claude-cli",
      model: "claude-opus-5-5",
      routeNote: "Sonnet stood in.",
      workspacePath: "C:/Users/me/Documents/Iron Jarvis/2026-10-17 S-corp",
      tools: [
        { tool: "history_search", count: 83, failed: 0 },
        { tool: "memory_read", count: 1, failed: 1 },
        { tool: "read_file", count: 2, failed: 0 },
      ],
      // v1.352.0 integrate, moved on purpose: WHO ran it (the receipt's
      // agent_name, else agent_type) rides the stamp too. RESULT carries no
      // `on_this_network`, so no `onThisNetwork` is stamped.
      agent: "researcher",
    });
    // a failed run at its step limit is stamped so; a "completed" outcome adds nothing
    const limit = readAgentRun(
      settleHandOff([hand], "s1", { status: "failed", summary: "stopped: reached max steps before completion" })[0],
    )!;
    expect(limit.status).toBe("failed");
    expect(limit.stepLimit).toBe(true);
    expect("outcome" in readAgentRun(settleHandOff([hand], "s1", { status: "completed", outcome: "completed" })[0])!).toBe(
      false,
    );
    // junk on a saved stamp reads as nothing, never as a guess
    const junk = readAgentRun({
      agentRun: {
        session: "s1",
        startedAt: 1,
        endedAt: 2,
        steps: 1,
        status: "exploded",
        outcome: 7,
        stepLimit: true,
        routeNote: { x: 1 },
        workspacePath: "C:/a\u0000b",
        tools: [
          { tool: "../../etc", count: 3, failed: 0 },
          { tool: "read_file", count: 2, failed: 9 },
          { tool: "web_search", count: -1, failed: 0 },
          "nope",
        ],
      },
    })!;
    expect(junk).toEqual({
      session: "s1",
      startedAt: 1,
      endedAt: 2,
      steps: 1,
      tools: [{ tool: "read_file", count: 2, failed: 2 }],
    });
    // and a stamp keeps at most STAMP_TOOLS_MAX tools
    const many = Array.from({ length: 30 }, (_, i) => ({ tool: `tool_${i}`, count: 1 }));
    expect(stampTools(many)).toHaveLength(STAMP_TOOLS_MAX);
  });

  it("stopHandOff: status cancelled from the run's start to now; an unknown start is no length", () => {
    const hand = { role: "assistant", content: "", escalated: "x", awaitingSession: "s1" };
    expect(readAgentRun(stopHandOff([hand], "s1", 10_000, 55_000)[0])).toEqual({
      session: "s1",
      startedAt: 10_000,
      endedAt: 55_000,
      steps: null,
      status: "cancelled",
    });
    expect(readAgentRun(stopHandOff([hand], "s1", null, 55_000)[0])).toMatchObject({
      startedAt: null,
      endedAt: null,
      status: "cancelled",
    });
    const plain: (HandOffLike & { role: string; content: string })[] = [{ role: "assistant", content: "hi" }];
    expect(stopHandOff(plain, "s1", 1, 2)).toBe(plain); // nothing waits on it
  });

  it("the settled row opens onto plain lines, the model and the folder, in WorkLine's quiet rows (no box)", async () => {
    const run: AgentRunStamp = {
      session: "s1",
      startedAt: 1_000,
      endedAt: 126_000,
      steps: 6,
      status: "completed",
      outcome: "completed_with_failures",
      provider: "claude-cli",
      model: "claude-opus-5-5",
      routeNote: "Opus could not be used, so Sonnet answered.",
      workspacePath: "C:/Users/me/Documents/Iron Jarvis/2026-10-17 S-corp",
      tools: [
        { tool: "history_search", count: 83, failed: 0 },
        { tool: "memory_read", count: 1, failed: 1 },
      ],
    };
    render(
      <AgentRunSettled name="The researcher" run={run} why="Handing this to the researcher: needs the agent.">
        <AgentRunReceipt run={run} />
      </AgentRunSettled>,
    );
    expect(screen.getByTestId("agent-run-words").textContent).toBe(
      "The researcher worked for 2 min 5 s · 6 steps · part failed",
    );
    expect(screen.queryByTestId("agent-run-receipt")).toBeNull(); // closed until opened
    const toggle = screen.getByTestId("agent-run-toggle");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const body = screen.getByTestId("agent-run-receipt");
    const rows = within(body).getAllByTestId("work-row");
    expect(rows.map((r) => r.textContent)).toEqual([
      "Searched your chats 83 times",
      "One memory lookup failed",
      "Ran on Opus 5.5",
    ]);
    expect(rows[1].getAttribute("data-state")).toBe("failed");
    expect(rows[0].getAttribute("title")).toBe("history_search"); // the id is the record, on hover
    expect(screen.getByTestId("agent-run-route-note").className).toContain("text-tone-warn");
    expect(screen.getByTestId("agent-run-folder").textContent).toBe("in 2026-10-17 S-corpOpen folder"); // the dot is drawn, not text
    // No raw tool id in the words, and no box drawn by any new element.
    const seen = visibleText(body);
    for (const id of RAW_IDS) expect(seen).not.toContain(id);
    for (const el of [body, ...Array.from(body.querySelectorAll("*"))]) {
      const cls = el.getAttribute("class") ?? "";
      expect(cls).not.toMatch(/(^|\s)(border|rounded-(lg|xl)|shadow|bg-white\/)/);
    }
    // Open folder: POST /documents/open with the run's own folder.
    fireEvent.click(screen.getByRole("button", { name: /Open the folder 2026-10-17 S-corp/ }));
    await waitFor(() =>
      expect(H.api.posts).toContainEqual({
        path: "/documents/open",
        body: { path: "C:/Users/me/Documents/Iron Jarvis/2026-10-17 S-corp" },
      }),
    );
  });

  it("a stamp from before v1.352.0 opens exactly as it did (nothing new in the body)", () => {
    const old: AgentRunStamp = { session: "s1", startedAt: 1_000, endedAt: 126_000, steps: 6 };
    const { container } = render(<AgentRunReceipt run={old} />);
    expect(container.textContent).toBe("");
  });

  it("on the chat page: the hand-off ends, the row reads the receipt's words and opens to plain lines", async () => {
    storeThread(WAITING);
    const end = Date.now();
    H.api.getResponses["/sessions/s1?transcript=0"] = {
      session: {
        id: "s1",
        status: "completed",
        outcome: "completed_with_failures",
        summary: "You chose the S-corp election in March.",
        provider: "claude-cli",
        model: "claude-opus-5-5",
        workspace_path: "C:/Users/me/Documents/Iron Jarvis/2026-10-17 S-corp",
        created_at: isoAgo(125_000, end),
        finished_at: isoAgo(0, end),
      },
    };
    H.api.getResponses["/sessions/s1/result"] = RESULT;
    render(<ChatPage />);
    expect(await screen.findByText("You chose the S-corp election in March.")).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByTestId("agent-run-words").textContent).toBe(
        "The researcher worked for 2 min 5 s · 6 steps · part failed",
      ),
    );
    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    const body = await screen.findByTestId("agent-run-receipt");
    expect(within(body).getByText("Searched your chats 83 times")).toBeTruthy();
    expect(within(body).getByText("Ran on Opus 5.5")).toBeTruthy();
    // The card under the reply (a deferred panel) names the model and the length.
    await waitFor(() =>
      expect(screen.getByTestId("run-result-meta").textContent).toMatch(/^answered by Opus 5\.5 · 2 min 5 s/),
    );
    // Saved with the thread: the stamp carries the ending and the counts.
    await waitFor(() => expect(putsTo("/chat/threads/t7").length).toBeGreaterThan(0));
    const saved = putsTo("/chat/threads/t7").at(-1)!.body.messages as Msg[];
    expect(readAgentRun(saved[1])).toMatchObject({
      status: "completed",
      outcome: "completed_with_failures",
      model: "claude-opus-5-5",
      tools: [
        { tool: "history_search", count: 83, failed: 0 },
        { tool: "memory_read", count: 1, failed: 1 },
        { tool: "read_file", count: 2, failed: 0 },
      ],
    });
  });

  it("on the chat page: the daemon's own line settles the row from ITS receipt (nothing fetched)", async () => {
    const end = Date.now();
    const daemonLine = {
      role: "assistant",
      content: "Found it.",
      sessionId: "s1",
      agentType: "researcher",
      receipt: RESULT,
    };
    let n = 0;
    H.api.getResponses["/chat/threads/t7"] = () => {
      n += 1;
      return n === 1
        ? { id: "t7", title: "saved", updated_at: "S1", messages: WAITING }
        : { id: "t7", title: "saved", updated_at: "S3", messages: [...WAITING, daemonLine] };
    };
    window.history.replaceState({}, "", "/chat?thread=t7");
    H.api.getResponses["/sessions/s1?transcript=0"] = {
      session: {
        id: "s1",
        status: "completed",
        outcome: "completed_with_failures",
        summary: "Found it.",
        created_at: isoAgo(125_000, end),
        finished_at: isoAgo(0, end),
      },
    };
    render(<ChatPage />);
    await waitFor(() =>
      expect(screen.getByTestId("agent-run-words").textContent).toBe(
        "The researcher worked for 2 min 5 s · 6 steps · part failed",
      ),
    );
    await waitFor(() => expect(putsTo("/chat/threads/t7").length).toBeGreaterThan(0));
    const msgs = putsTo("/chat/threads/t7")[0].body.messages as Msg[];
    expect(readAgentRun(msgs[1])!.tools).toEqual([
      { tool: "history_search", count: 83, failed: 0 },
      { tool: "memory_read", count: 1, failed: 1 },
      { tool: "read_file", count: 2, failed: 0 },
    ]);
    // the model came from the daemon line's receipt (the row had none)
    expect(readAgentRun(msgs[1])!.model).toBe("claude-opus-5-5");
    expect(H.api.gets).not.toContain("/sessions/s1/result");
  });

  it("a hand-off the chat aimed at a REMOTE agent says it runs on this PC and how to reach the remote", async () => {
    expect(handOffSentence("needs the agent", null, "remote:hermes")).toBe(
      "Taking this on properly: needs the agent. Running on this PC. Reach hermes with @hermes.",
    );
    expect(handOffSentence("needs the agent", null, "hermes")).toBe(
      "Taking this on properly: needs the agent. Running on this PC. Reach hermes with @hermes.",
    );
    // junk is never echoed into a sentence
    expect(handOffSentence("x", null, "remote:../../<b>")).toBe("Taking this on properly: x.");
    expect(handOffSentence("x", "the researcher")).toBe("Handing this to the researcher: x.");

    H.stream.result = {
      reply: "",
      escalate: true,
      escalateReason: "needs the agent",
      escalateAgent: "remote:hermes",
    };
    H.api.postResponses["/sessions"] = { id: "s9", status: "active", created_at: isoAgo(0) };
    H.api.getResponses["/sessions/s9?transcript=0"] = { session: { id: "s9", status: "active" } };
    render(<ChatPage />);
    const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
    fireEvent.change(box, { target: { value: "ask hermes to check the server" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/sessions")).toBe(true));
    // the run that opens is the default one here, never a remote session
    const sess = H.api.posts.find((p) => p.path === "/sessions")!;
    expect(String(sess.body.agent_type ?? "builder")).not.toMatch(/remote/);
    // the saved hand-off carries the dropped remote, and the line says it
    const handoff = H.api.puts
      .map((p) => (p.body.messages as Msg[]).find((m) => m.escalated))
      .find(Boolean)!;
    expect(handoff.droppedRemote).toBe("hermes");
    expect(
      await screen.findByText(
        "Taking this on properly: needs the agent. Running on this PC. Reach hermes with @hermes.",
      ),
    ).toBeTruthy();
  });
});

/* ================================================================ G21 */

describe("G21: every reply says where it ran", () => {
  const renderLine = (route: TurnRoute, modelName?: string) =>
    render(<TurnReceipt inline route={route} modelName={modelName} />);

  it("a reply from the user's DGX box: 'answered by <model> on DGX Spark'", () => {
    renderLine({ provider: "fleet-dgx", model: "glm-4.6", label: "DGX Spark", reason: "default", on_this_network: true });
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by glm-4.6 on DGX Spark");
    expect(screen.queryByTestId("turn-where")).toBeNull(); // the endpoint's name says it
  });

  it("a reply from Claude: 'answered by Opus 5.5 · cloud'", () => {
    renderLine({ provider: "claude-cli", model: "claude-opus-5-5", reason: "default", on_this_network: false });
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by Opus 5.5");
    const where = screen.getByTestId("turn-where");
    expect(where.textContent).toBe("cloud");
    expect(where.getAttribute("title")).toBe("Ran in the cloud: this message left your network for Claude Code.");
    // right after who answered, as its own quiet part
    const line = screen.getByTestId("turn-receipt");
    expect(line.textContent!.startsWith("answered by Opus 5.5·cloud")).toBe(true);
    // and in words a phone can read once opened
    fireEvent.click(line);
    expect(screen.getByTestId("turn-where-detail").textContent).toBe(
      "Ran in the cloud: this message left your network for Claude Code.",
    );
  });

  it("a local model with no endpoint name: '· local' (never 'this PC' on a guess)", () => {
    renderLine({ provider: "ollama", model: "llama3.2", reason: "explicit", requested: "ollama", on_this_network: true });
    expect(screen.getByTestId("turn-where").textContent).toBe("local");
    expect(screen.getByTestId("turn-where").getAttribute("title")).toMatch(/this PC or a machine on your network/);
  });

  it("a named endpoint off the user's network says both: 'on Work proxy' and 'cloud'", () => {
    renderLine({ provider: "fleet-work", model: "gpt-x", label: "Work proxy", reason: "default", on_this_network: false });
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by gpt-x on Work proxy");
    expect(screen.getByTestId("turn-where").textContent).toBe("cloud");
  });

  it("a substitute that answered from a named endpoint keeps its amber words and says where", () => {
    renderLine({
      provider: "fleet-dgx",
      model: "glm-4.6",
      label: "DGX Spark",
      reason: "failover",
      from: "fleet-rtx",
      why: "unreachable",
      on_this_network: true,
    });
    expect(screen.getByTestId("turn-route-warning").textContent).toContain("answered by glm-4.6");
    expect(screen.getByTestId("turn-where-on").textContent).toBe("on DGX Spark");
  });

  it("a reply from before the flag existed, and the demo model, show nothing new", () => {
    const { unmount } = renderLine({ provider: "claude-cli", model: "claude-opus-5-5", reason: "default" });
    expect(screen.queryByTestId("turn-where")).toBeNull();
    expect(screen.queryByTestId("turn-where-on")).toBeNull();
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by Opus 5.5");
    unmount();
    renderLine({ provider: "fleet-dgx", model: "glm-4.6", label: "DGX Spark", reason: "default" });
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by glm-4.6");
    unmount();
    expect(replyWhere({ provider: "mock", model: "mock", reason: "mock", on_this_network: true })).toBeNull();
  });

  it("the model menu says where each runs and how it is paid: never just 'included'", async () => {
    H.api.getResponses["/models"] = {
      models: [
        { provider: "anthropic", model: "claude-opus-5-5", name: "Anthropic", kind: "api", available: true, inherited_from: "claude-cli" },
        { provider: "openai", model: "gpt-5.5", name: "OpenAI", kind: "api", available: true, inherited_from: null },
        { provider: "ollama", model: "llama3.2", name: "Ollama", kind: "local", available: true, inherited_from: null },
      ],
    };
    render(<ChatPage />);
    fireEvent.click(await screen.findByTitle("Switch model"));
    const badge = async (name: string) =>
      within((await screen.findByText(name)).closest("button")!).getByTestId("model-kind-badge");
    const plan = await badge("Anthropic");
    expect(plan.textContent).toBe("cloud · on your plan");
    expect(plan.getAttribute("title")).toMatch(/subscription you already have\. No charge per message/);
    const metered = await badge("OpenAI");
    expect(metered.textContent).toBe("cloud · pay per use");
    expect(metered.getAttribute("title")).toMatch(/Each message is billed/);
    const local = await badge("Ollama");
    expect(local.textContent).toBe("local");
    expect(local.getAttribute("title")).toMatch(/your own hardware/);
    expect(document.body.textContent).not.toMatch(/\bincluded\b/);
  });
});
