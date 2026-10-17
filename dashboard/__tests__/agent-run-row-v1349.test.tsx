/**
 * v1.349.0 (polish wave B, lane B2): the helper-agent row.
 *
 * Plain words: when chat hands work to an agent, the chat used to show a
 * saved line ("Handing this to the researcher: …") AND a separate live bubble
 * with the agent's steps. Now it is ONE quiet row: "The researcher is working
 * · Step 2 of 4: draft the summary · 1:24" while it runs, and "The researcher
 * worked for 2 min 5 s · 6 steps · Open run" once it is done. The reply and
 * the run card under it are unchanged, and a chat saved by an older version
 * looks exactly as it did.
 *
 * What carries weight:
 *  - the words, from the real helpers (current step, settled words, name);
 *  - the stamp finalize saves on the hand-off message (session, start, end,
 *    steps), on BOTH finalize paths (composed reply, and the daemon's own
 *    appended line, which is then saved), read back defensively;
 *  - the saved line steps aside ONLY while this window shows the live row for
 *    the same run (another window's mark keeps its line);
 *  - the clock ticks in a leaf (the parent never re-renders per second), and
 *    counts from the session's own start (a resumed run is not "0s");
 *  - one clock at a time; the row is a disclosure (keyboard + aria-expanded);
 *    no box classes; the agent's live words stay out of the row.
 *
 * The page is rendered for real; only the transport hooks are mocked, at the
 * same seams the other chat tests use (agent-stop-partial-v1257's harness).
 */

import { readFileSync } from "node:fs";
import path from "node:path";
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
  return {
    FakeApiError,
    api: {
      gets: [] as string[],
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      /** A value, an Error, or a function of the call count (1-based). */
      getResponses: {} as Record<string, unknown>,
      getCounts: {} as Record<string, number>,
    },
    /** What the run stream shows (useRunStream's fields the page reads). */
    run: {
      text: "",
      tools: [] as unknown[],
      phase: null as null | { phase: string; detail: string },
    },
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
    H.api.posts.push({ path, body });
    return {};
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
      run: async () => ({ reply: "" }),
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
import {
  AgentRunLive,
  AgentRunSettled,
  HandOffLine,
  currentStepWords,
  liveRunWords,
  settledRunWords,
} from "@/components/chat/AgentRunRow";
import {
  agentRunName,
  handOffFor,
  handOffSentence,
  liveRuns,
  makeAgentRunStamp,
  readAgentRun,
  settleHandOff,
  stampHandOff,
  stampMs,
  type AgentRunStamp,
  type HandOffLike,
} from "@/lib/agentRun";
import type { ToolCard } from "@/lib/useChatStream";

type Msg = Record<string, unknown>;

const SENTENCE = "Handing this to the researcher: needs the agent.";

/** A naive-UTC ISO stamp (the daemon's own shape) `ago` ms in the past. */
const isoAgo = (ago: number, from = Date.now()) => new Date(from - ago).toISOString().replace("Z", "");

const RESULT = {
  found: true,
  session_id: "s1",
  status: "completed",
  task: "draft the summary",
  summary: "",
  steps: 6,
  tools_used: [{ tool: "read_file", count: 2 }],
  tools_failed: [],
  files_created: [],
  files_changed: [],
  errors: [],
  revertable: 0,
  duration_s: 125,
};

function resetApi() {
  H.api.gets.length = 0;
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  for (const k of Object.keys(H.api.getResponses)) delete H.api.getResponses[k];
  for (const k of Object.keys(H.api.getCounts)) delete H.api.getCounts[k];
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
}

/** A saved thread the page opens from ?thread=t7. */
function storeThread(messages: Msg[] | ((n: number) => unknown)) {
  H.api.getResponses["/chat/threads/t7"] =
    typeof messages === "function"
      ? messages
      : { id: "t7", title: "saved", updated_at: "S1", messages };
  window.history.replaceState({}, "", "/chat?thread=t7");
}

const WAITING: Msg[] = [
  { role: "user", content: "summarise the ledger" },
  {
    role: "assistant",
    content: "",
    escalated: "needs the agent",
    escalatedTo: "the researcher",
    awaitingSession: "s1",
  },
];

const putsTo = (p: string) => H.api.puts.filter((x) => x.path === p);

/** A row's class tokens. */
const classes = (el: Element | null | undefined) =>
  (el?.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);

/** Box-drawing classes: a border, a fill, rounded corners or a shadow. The
 *  1px dot separator is a round FILLED dot by design, so it is skipped. */
function boxClasses(el: Element): string[] {
  return classes(el).filter(
    (c) =>
      /^(border|border-[0-9])$/.test(c) ||
      /^border-[xytlr]-?[0-9]*$/.test(c) ||
      /^bg-/.test(c) ||
      /^rounded/.test(c) ||
      /^shadow/.test(c),
  );
}

function rowBoxes(row: Element): string[] {
  const out: string[] = [];
  for (const el of [row, ...Array.from(row.querySelectorAll("*"))]) {
    if (el.getAttribute("aria-hidden") === "true" && el.tagName === "SPAN") continue; // the dot
    for (const c of boxClasses(el)) out.push(c);
  }
  return out;
}

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

/* ---------------------------------------------------------------- words */

describe("the row's words (the real helpers)", () => {
  it("names what the run is on now: its own step first, then its phase, then its newest progress line", () => {
    expect(
      currentStepWords({ phaseTitle: "Working…", phaseDetail: "step 2 of 4: draft the summary", progress: ["Using read_file…"] }),
    ).toBe("Step 2 of 4: draft the summary");
    expect(currentStepWords({ phaseTitle: "Planning the work…", phaseDetail: "working out the steps", progress: [] })).toBe(
      "Planning the work",
    );
    // A phase that only says "Working" adds nothing to "is working": the
    // newest progress line speaks instead, and nothing at all when it too is
    // generic.
    expect(currentStepWords({ phaseTitle: "Working…", phaseDetail: "working the task", progress: ["Using read_file…"] })).toBe(
      "Using read_file",
    );
    expect(currentStepWords({ phaseTitle: null, phaseDetail: null, progress: ["Working…"] })).toBeNull();
    expect(currentStepWords({ phaseTitle: null, phaseDetail: null, progress: [] })).toBeNull();
    // One line, however long the model's goal was.
    const long = currentStepWords({ phaseDetail: `step 1 of 2: ${"x ".repeat(80)}` });
    expect(long!.length).toBeLessThanOrEqual(80);
    expect(long!.endsWith("…")).toBe(true);
  });

  it("says the live and the settled row the way the task reads", () => {
    expect(liveRunWords("The researcher", "Step 2 of 4: draft the summary")).toBe(
      "The researcher is working · Step 2 of 4: draft the summary",
    );
    expect(liveRunWords("The agent", null)).toBe("The agent is working");
    const run: AgentRunStamp = { session: "s1", startedAt: 1_000, endedAt: 126_000, steps: 6 };
    expect(settledRunWords("The researcher", run)).toBe("The researcher worked for 2 min 5 s · 6 steps");
    expect(settledRunWords("The agent", { ...run, steps: 1 })).toBe("The agent worked for 2 min 5 s · 1 step");
    // Unknown length: it finished, and nothing is invented about how long.
    expect(settledRunWords("The agent", { session: "s1", startedAt: null, endedAt: null, steps: null })).toBe(
      "The agent finished",
    );
  });

  it("names the agent at the start of a sentence, and the unnamed hand-off as 'The agent'", () => {
    expect(agentRunName("the researcher")).toBe("The researcher");
    expect(agentRunName("your invoice-chaser agent")).toBe("Your invoice-chaser agent");
    expect(agentRunName(undefined)).toBe("The agent");
    expect(agentRunName("   ")).toBe("The agent");
    expect(handOffSentence("needs the agent", "the researcher")).toBe(SENTENCE);
    expect(handOffSentence("needs the agent")).toBe("Taking this on properly: needs the agent.");
  });
});

/* ----------------------------------------------------------------- stamp */

describe("the stamp finalize saves on the hand-off message", () => {
  it("is built from the session's own clock (naive UTC read as UTC) and the ledger's step count", () => {
    const s = makeAgentRunStamp({
      session: "s1",
      createdAt: "2026-10-17T10:00:00",
      finishedAt: "2026-10-17T10:02:05.500000",
      steps: 6,
    });
    expect(s).toEqual({
      session: "s1",
      startedAt: Date.UTC(2026, 9, 17, 10, 0, 0),
      endedAt: Date.UTC(2026, 9, 17, 10, 2, 5, 500),
      steps: 6,
    });
    expect(stampMs("2026-10-17T10:00:00+00:00")).toBe(Date.UTC(2026, 9, 17, 10, 0, 0));
    // Junk is null, never a guess; an end before the start is no end.
    expect(makeAgentRunStamp({ session: "s1", createdAt: "soon", finishedAt: 5, steps: "6" })).toEqual({
      session: "s1",
      startedAt: null,
      endedAt: null,
      steps: null,
    });
    expect(
      makeAgentRunStamp({ session: "s1", createdAt: "2026-10-17T10:05:00", finishedAt: "2026-10-17T10:00:00" }).endedAt,
    ).toBeNull();
  });

  it("stamps ONLY the hand-off message waiting on that run, and leaves every other message object as it was", () => {
    type Line = HandOffLike & { role: string; content: string };
    const user: Line = { role: "user", content: "q" };
    const older: Line = { role: "assistant", content: "", escalated: "earlier", awaitingSession: "s0" };
    const hand: Line = { role: "assistant", content: "", escalated: "needs the agent", awaitingSession: "s1" };
    const msgs: Line[] = [user, older, hand];
    const stamp = makeAgentRunStamp({ session: "s1", steps: 2 });
    const out = stampHandOff(msgs, "s1", stamp);
    expect(out).not.toBe(msgs);
    expect(out[0]).toBe(user);
    expect(out[1]).toBe(older);
    expect(out[2]).toEqual({ ...hand, agentRun: stamp });
    // No hand-off waits on it (a plain agent turn): the SAME array comes back.
    const plain: Line[] = [user, { role: "user", content: "x", awaitingSession: "s1" }];
    expect(stampHandOff(plain, "s1", stamp)).toBe(plain);
    expect(handOffFor(msgs, "s1")).toBe(hand);
    expect(handOffFor(msgs, null)).toBeNull();
  });

  it("settleHandOff (finalize's one call) reads the session row's own times and the ledger's count", () => {
    type Line = HandOffLike & { role: string; content: string };
    const hand: Line = { role: "assistant", content: "", escalated: "needs the agent", awaitingSession: "s1" };
    const out = settleHandOff([hand], "s1", { created_at: "2026-10-17T10:00:00", finished_at: "2026-10-17T10:01:05" }, 4);
    expect(readAgentRun(out[0])).toEqual({
      session: "s1",
      startedAt: Date.UTC(2026, 9, 17, 10, 0, 0),
      endedAt: Date.UTC(2026, 9, 17, 10, 1, 5),
      steps: 4,
    });
    // An unreadable session row still settles the line (the link is known).
    expect(readAgentRun(settleHandOff([hand], "s1", null)[0])).toEqual({
      session: "s1",
      startedAt: null,
      endedAt: null,
      steps: null,
    });
  });

  it("is read back defensively: a saved thread is untrusted, and anything malformed is no stamp", () => {
    const good = { session: "session_3f9a1c2b8d04", startedAt: 1_000, endedAt: 5_000, steps: 3 };
    expect(readAgentRun({ agentRun: good })).toEqual(good);
    expect(readAgentRun({})).toBeNull(); // a message from before v1.349.0
    expect(readAgentRun(null)).toBeNull();
    expect(readAgentRun({ agentRun: "s1" })).toBeNull();
    expect(readAgentRun({ agentRun: [good] })).toBeNull();
    expect(readAgentRun({ agentRun: { ...good, session: "../settings" } })).toBeNull();
    expect(readAgentRun({ agentRun: { ...good, session: "" } })).toBeNull();
    expect(readAgentRun({ agentRun: { ...good, startedAt: -5, endedAt: "x", steps: 2.5 } })).toEqual({
      session: good.session,
      startedAt: null,
      endedAt: null,
      steps: null,
    });
    expect(readAgentRun({ agentRun: { ...good, startedAt: 9_000, endedAt: 1_000 } })!.endedAt).toBeNull();
  });
});

/* ------------------------------------------------------------ components */

const done = (id: string, name: string, args: Record<string, unknown>, at: number): ToolCard =>
  ({ id, name, args, status: "done", ok: true, output: "", startedAt: at, endedAt: at + 100 }) as unknown as ToolCard;

describe("the live row", () => {
  function live(extra: Partial<Parameters<typeof AgentRunLive>[0]> = {}) {
    return (
      <AgentRunLive
        session="s1"
        handOff={{ escalated: "needs the agent", escalatedTo: "the researcher" }}
        since={Date.now() - 84_000}
        progress={["Step 2 of 4: draft the summary", "Step 1 of 4 done", "Planned 4 steps"]}
        tools={[]}
        phaseTitle="Working…"
        phaseDetail="step 2 of 4: draft the summary"
        {...extra}
      />
    );
  }

  it("reads '<Agent> is working · <step> · 1:24', is a real disclosure, and draws no box", () => {
    render(live());
    const row = screen.getByTestId("agent-run-row");
    expect(row.getAttribute("data-state")).toBe("running");
    expect(screen.getByTestId("agent-run-words").textContent).toBe(
      "The researcher is working · Step 2 of 4: draft the summary",
    );
    expect(screen.getByTestId("agent-run-clock").textContent).toMatch(/^1:2[4-9]$/);
    // The header (words AND the per-second clock) is not a live region: the
    // bubble around it is polite, and a clock read out every second would be
    // noise; the step rows below announce themselves as they arrive.
    const head = screen.getByTestId("agent-run-head");
    expect(head.getAttribute("aria-live")).toBe("off");
    expect(head.contains(screen.getByTestId("agent-run-clock"))).toBe(true);
    expect(head.contains(screen.getByTestId("agent-run-words"))).toBe(true);
    // The running words shimmer like every running step (the stylesheet
    // stills them under reduced motion; pinned in live-work-v1348).
    expect(classes(within(row).getByText("The researcher is working"))).toContain("ij-shimmer");
    // Open as it starts: why it was handed over, then the step rows.
    const toggle = screen.getByTestId("agent-run-toggle");
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    const body = screen.getByTestId("agent-run-body");
    expect(toggle.getAttribute("aria-controls")).toBe(body.id);
    expect(within(body).getByTestId("agent-run-why").textContent).toBe(SENTENCE);
    const rows = within(body).getAllByTestId("work-row");
    // The two finished progress lines (oldest first), then the running step.
    expect(rows.map((r) => r.getAttribute("data-state"))).toEqual(["done", "done", "running"]);
    expect(rows[0].textContent).toBe("Planned 4 steps");
    expect(rows[2].textContent).toContain("step 2 of 4: draft the summary");
    // Calm: nothing in the row is a card.
    expect(rowBoxes(row)).toEqual([]);
    // Closed, it is one line: the body (and its rows) go, the clock stays.
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(toggle.getAttribute("aria-controls")).toBeNull();
    expect(screen.queryByTestId("agent-run-body")).toBeNull();
    expect(screen.queryByTestId("live-work")).toBeNull();
    expect(screen.getByTestId("agent-run-clock")).toBeTruthy();
  });

  it("shows ONE clock at a time: with tool rows open, the live work line counts the run from the same start", () => {
    const now = Date.now();
    render(live({ since: now - 84_000, tools: [done("c1", "read_file", { path: "C:\\work\\a.xlsx" }, now - 2_000)] }));
    // Open with tool rows: the header's clock steps aside for the work line,
    // which counts the WHOLE run (84 s), not from the first call (2 s).
    expect(screen.queryByTestId("agent-run-clock")).toBeNull();
    expect(screen.getByTestId("live-work-words").textContent).toMatch(/^Working for 1 min 2[4-9] s · read 1 file$/);
    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    expect(screen.getByTestId("agent-run-clock").textContent).toMatch(/^1:2[4-9]$/);
  });

  it("an agent turn with no hand-off line still gets the row, unnamed", () => {
    render(live({ handOff: null, phaseTitle: null, phaseDetail: null, progress: [] }));
    expect(screen.getByTestId("agent-run-words").textContent).toBe("The agent is working");
    expect(screen.queryByTestId("agent-run-why")).toBeNull();
    // The running row keeps today's words when nothing has been said yet.
    expect(within(screen.getByTestId("live-work")).getByText("Thinking…")).toBeTruthy();
  });

  it("the clock ticks in its own leaf: the parent renders once while the clock moves", () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.setSystemTime(new Date("2026-10-17T12:00:00Z"));
    let parentRenders = 0;
    const since = Date.now() - 5_000;
    function Parent() {
      parentRenders++;
      return (
        <AgentRunLive session="s1" handOff={null} since={since} progress={[]} tools={[]} phaseTitle={null} phaseDetail={null} />
      );
    }
    render(<Parent />);
    expect(screen.getByTestId("agent-run-clock").textContent).toBe("5s");
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(screen.getByTestId("agent-run-clock").textContent).toBe("8s");
    expect(parentRenders).toBe(1);
  });

  it("every motion in the row stops under reduced motion", () => {
    render(live());
    const row = screen.getByTestId("agent-run-row");
    const moving = [row, ...Array.from(row.querySelectorAll("*"))];
    let transitions = 0;
    for (const el of moving) {
      const c = classes(el);
      if (c.some((x) => /^transition/.test(x))) {
        transitions++;
        expect(c, el.outerHTML.slice(0, 120)).toContain("motion-reduce:transition-none");
      }
      if (c.includes("animate-spin")) expect(c).toContain("motion-reduce:animate-none");
    }
    expect(transitions).toBeGreaterThan(0); // anti-vacuity: the chevron and the toggle move
  });
});

describe("the saved line steps aside only for the live row of the SAME run", () => {
  it("hides while that run's live row is on screen, and comes back when it goes", () => {
    const { rerender } = render(
      <>
        <HandOffLine session="s1">
          <span>{SENTENCE}</span>
        </HandOffLine>
        <AgentRunLive session="s1" handOff={null} progress={[]} tools={[]} />
      </>,
    );
    // Mounted in the same commit as the live row: already gone.
    expect(screen.queryByText(SENTENCE)).toBeNull();
    expect(liveRuns.has("s1")).toBe(true);
    rerender(
      <HandOffLine session="s1">
        <span>{SENTENCE}</span>
      </HandOffLine>,
    );
    expect(screen.getByText(SENTENCE)).toBeTruthy();
    expect(liveRuns.has("s1")).toBe(false);
  });

  it("a line waiting on ANOTHER run, or on nothing, keeps showing", () => {
    render(
      <>
        <HandOffLine session="s0">
          <span>older</span>
        </HandOffLine>
        <HandOffLine>
          <span>none</span>
        </HandOffLine>
        <AgentRunLive session="s1" handOff={null} progress={[]} tools={[]} />
      </>,
    );
    expect(screen.getByText("older")).toBeTruthy();
    expect(screen.getByText("none")).toBeTruthy();
  });
});

describe("the settled row", () => {
  it("reads '<Agent> worked for 2 min 5 s · 6 steps' with an 'Open run' link, and opens to why", () => {
    render(
      <AgentRunSettled
        name="The researcher"
        run={{ session: "s1", startedAt: 1_000, endedAt: 126_000, steps: 6 }}
        why={SENTENCE}
      />,
    );
    const row = screen.getByTestId("agent-run-row");
    expect(row.getAttribute("data-state")).toBe("settled");
    expect(screen.getByTestId("agent-run-words").textContent).toBe("The researcher worked for 2 min 5 s · 6 steps");
    const link = screen.getByRole("link", { name: "Open run" });
    expect(link.getAttribute("href")).toBe("/sessions/s1");
    // Nothing ticks once it is over.
    expect(screen.queryByTestId("agent-run-clock")).toBeNull();
    const toggle = screen.getByTestId("agent-run-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText(SENTENCE)).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByTestId("agent-run-why").textContent).toBe(SENTENCE);
    expect(rowBoxes(row)).toEqual([]);
  });
});

it("the row never reads the agent's streamed words (they stay in AgentLiveText, out of page state: S-02)", () => {
  const src = readFileSync(path.join(__dirname, "..", "components", "chat", "AgentRunRow.tsx"), "utf8").replace(
    /\r\n/g,
    "\n",
  );
  const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
  expect(code).not.toMatch(/useLiveText|textStore|\.text\b/);
  // Anti-vacuity: this IS the row's source.
  expect(code).toContain("export function AgentRunLive");
  // And the page still asks the run stream to keep its words out of state.
  const page = readFileSync(path.join(__dirname, "..", "app", "chat", "page.tsx"), "utf8").replace(/\r\n/g, "\n");
  expect(page).toContain("useRunStream({ textInState: false })");
  expect(page).toMatch(/<AgentRunLive[\s\S]{0,1200}?<AgentLiveText stream=\{runStream\}/);
});

/* -------------------------------------------------------------- the page */

describe("on the chat page", () => {
  it("a resumed run shows ONE row in place of the line and the bubble; its clock counts from the session's start", async () => {
    storeThread(WAITING);
    H.run.phase = { phase: "running", detail: "step 2 of 4: draft the summary" };
    H.run.text = "Drafting the summary now";
    H.api.getResponses["/sessions/s1?transcript=0"] = {
      session: { id: "s1", status: "running", created_at: isoAgo(84_000) },
    };
    render(<ChatPage />);
    const row = await screen.findByTestId("agent-run-row");
    expect(screen.getByTestId("agent-run-words").textContent).toBe(
      "The researcher is working · Step 2 of 4: draft the summary",
    );
    // Wait for the THING asserted: the clock reading the session's own start
    // (84 s ago), not the row's mount ("0s" would be a resumed run's lie).
    await waitFor(() => expect(screen.getByTestId("agent-run-clock").textContent).toMatch(/^1:2\d$/));
    // The saved line is NOT drawn as well: its sentence is said once, inside
    // the row (why it was handed over).
    const said = screen.getAllByText(SENTENCE);
    expect(said).toHaveLength(1);
    expect(row.contains(said[0])).toBe(true);
    // The agent's live words stay where they were, below the row, and stay
    // when the row is closed.
    expect(screen.getByText("Drafting the summary now")).toBeTruthy();
    fireEvent.click(screen.getByTestId("agent-run-toggle"));
    expect(screen.queryByText(SENTENCE)).toBeNull();
    expect(screen.getByText("Drafting the summary now")).toBeTruthy();
    // Stop is still there and still the user's.
    expect(screen.getByRole("button", { name: /Stop/ })).toBeEnabled();
  });

  it("the run finishes: the same row settles, stamped and SAVED on the hand-off message; reply and run card unchanged", async () => {
    storeThread(WAITING);
    const end = Date.now();
    H.api.getResponses["/sessions/s1?transcript=0"] = {
      session: {
        id: "s1",
        status: "completed",
        summary: "The summary is ready.",
        created_at: isoAgo(125_000, end),
        finished_at: isoAgo(0, end),
      },
    };
    H.api.getResponses["/sessions/s1/result"] = RESULT;
    render(<ChatPage />);
    expect(await screen.findByText("The summary is ready.")).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByTestId("agent-run-words").textContent).toBe("The researcher worked for 2 min 5 s · 6 steps"),
    );
    expect(screen.getByTestId("agent-run-row").getAttribute("data-state")).toBe("settled");
    expect(screen.getByRole("link", { name: "Open run" }).getAttribute("href")).toBe("/sessions/s1");
    // The ledger card under the reply is exactly what it was (it loads lazily:
    // a deferred panel, v1.258.0).
    expect(await screen.findByTestId("run-result-headline")).toBeTruthy();
    // Saved with the thread: the stamp on the hand-off, the wait mark gone.
    await waitFor(() => expect(putsTo("/chat/threads/t7").length).toBeGreaterThan(0));
    const saved = putsTo("/chat/threads/t7").at(-1)!.body.messages as Msg[];
    expect(saved).toHaveLength(3);
    expect(saved[1]).toMatchObject({ escalated: "needs the agent", escalatedTo: "the researcher" });
    expect(saved[1].awaitingSession).toBeUndefined();
    const run = readAgentRun(saved[1])!;
    expect(run).toMatchObject({ session: "s1", steps: 6 });
    expect(run.endedAt! - run.startedAt!).toBe(125_000);
    expect(saved[2]).toMatchObject({ role: "assistant", content: "The summary is ready.", fromSession: "s1" });
  });

  it("the DAEMON already appended the reply: the hand-off in its array settles, and that array is saved under the adopted stamp", async () => {
    const end = Date.now();
    const daemonLine = {
      role: "assistant",
      content: "Made the summary.",
      sessionId: "s1",
      agentType: "researcher",
      receipt: RESULT,
    };
    // The open reads the waiting copy; finalize's re-read finds the line.
    storeThread((n) =>
      n === 1
        ? { id: "t7", title: "saved", updated_at: "S1", messages: WAITING }
        : { id: "t7", title: "saved", updated_at: "S3", messages: [...WAITING, daemonLine] },
    );
    H.api.getResponses["/sessions/s1?transcript=0"] = {
      session: {
        id: "s1",
        status: "completed",
        summary: "Made the summary.",
        created_at: isoAgo(65_000, end),
        finished_at: isoAgo(0, end),
      },
    };
    render(<ChatPage />);
    expect(await screen.findByText("Made the summary.")).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByTestId("agent-run-words").textContent).toBe("The researcher worked for 1 min 5 s · 6 steps"),
    );
    await waitFor(() => expect(putsTo("/chat/threads/t7").length).toBeGreaterThan(0));
    const put = putsTo("/chat/threads/t7")[0];
    expect(put.body.if_updated_at).toBe("S3");
    const msgs = put.body.messages as Msg[];
    // The daemon's line once (never a second, page-composed reply)...
    expect(msgs.filter((m) => m.content === "Made the summary.")).toHaveLength(1);
    expect(msgs.filter((m) => m.role === "assistant" && !m.escalated)).toHaveLength(1);
    // ...and the hand-off settled and unmarked.
    expect(msgs[1].awaitingSession).toBeUndefined();
    expect(readAgentRun(msgs[1])).toMatchObject({ session: "s1", steps: 6 });
    // Nothing composed: the result was never fetched by the page.
    expect(H.api.gets).not.toContain("/sessions/s1/result");
  });

  it("a hand-off saved by an older version (no stamp) renders exactly as today", async () => {
    storeThread([
      { role: "user", content: "summarise the ledger" },
      { role: "assistant", content: "", escalated: "needs the agent", escalatedTo: "the researcher" },
      { role: "assistant", content: "Done.", fromSession: "s1" },
      { role: "user", content: "and the other one" },
      { role: "assistant", content: "", escalated: "needs the agent" },
      { role: "assistant", content: "Done again.", fromSession: "s2" },
    ]);
    render(<ChatPage />);
    const named = await screen.findByText(SENTENCE);
    expect(named.tagName).toBe("SPAN");
    const line = named.parentElement!;
    expect(classes(line)).toEqual(["flex", "items-start", "gap-2", "text-[13px]", "text-zinc-400"]);
    expect(line.querySelector("svg")).toBeTruthy();
    expect(screen.getByText("Taking this on properly: needs the agent.").tagName).toBe("SPAN");
    expect(screen.queryByTestId("agent-run-row")).toBeNull();
  });

  it("a stamped hand-off reopens as the settled row, read from the thread alone", async () => {
    storeThread([
      { role: "user", content: "summarise the ledger" },
      {
        role: "assistant",
        content: "",
        escalated: "needs the agent",
        escalatedTo: "the researcher",
        agentRun: { session: "s1", startedAt: 1_000, endedAt: 126_000, steps: 6 },
      },
      { role: "assistant", content: "Done.", fromSession: "s1" },
    ]);
    render(<ChatPage />);
    await waitFor(() =>
      expect(screen.getByTestId("agent-run-words").textContent).toBe("The researcher worked for 2 min 5 s · 6 steps"),
    );
    expect(screen.queryByText(SENTENCE)).toBeNull(); // folded into the row
    expect(H.api.gets.filter((g) => g.startsWith("/sessions/"))).toEqual([]);
  });

  it("a wait mark this window is not waiting on (another window's run) keeps its saved line", async () => {
    // The mark is not on the LAST message, so nothing resumes here: no live
    // row is on screen for s1, and the line must not vanish.
    storeThread([...WAITING, { role: "user", content: "and meanwhile?" }, { role: "assistant", content: "Sure." }]);
    render(<ChatPage />);
    expect(await screen.findByText(SENTENCE)).toBeTruthy();
    expect(screen.queryByTestId("agent-run-row")).toBeNull();
  });
});
