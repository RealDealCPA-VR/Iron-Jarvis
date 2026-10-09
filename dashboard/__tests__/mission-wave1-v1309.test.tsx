// v1.309.0 (UX/speed wave 1, track C) — the mission screen stops being a
// dead end and starts telling the truth while it works.
//
// Written BEFORE the fix; every test here pins one thing a user sees:
//   * the centre shows the DELIVERABLE, never the coordinator's narration
//     before a tool call ("I'll split this into …");
//   * a long live report is re-parsed in proportion to what CHANGED, and the
//     teammate cards do not re-render on every token flush;
//   * a finished / stopped / failed mission offers Ask for changes, Run it
//     again, Retry the N failed items — each opening the NEW mission;
//   * an interrupted mission offers Continue; a continued one links forward;
//   * the header shows the user's own words (session.objective), not the
//     continuation recap;
//   * the screen says which model did the work (amber when it was the mock)
//     and the daemon's route note;
//   * the composer warns BEFORE Start when the default model is known down,
//     read off the app's shared /health (no new poller), never switching;
//   * the recent list says "Needs you" when a mission is parked on an ask;
//   * a TEAMMATE's ask is answered on the mission screen, and one teammate
//     can be stopped from its card;
//   * /agents?view=team&agent=<roster name> opens Your team on that agent;
//   * the /agents page module does not pull in Your team / the room
//     transcript until they are opened.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const wave1Harness = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: Record<string, unknown> | undefined }>,
  postResults: {} as Record<string, unknown>,
  gets: [] as string[],
  /** Modules whose factory ran (i.e. something imported them). */
  loaded: new Set<string>(),
  /** Every markdown parse: the length of the content handed to it. */
  parsed: [] as number[],
  /** AgentFace renders, by agent name. */
  faces: {} as Record<string, number>,
  daemon: null as unknown,
  es: [] as Array<{
    url: string;
    closed: boolean;
    emit: (name: string, data: unknown) => void;
  }>,
}));
const H = wave1Harness;

function apiState(path: string | null) {
  return { data: path ? (H.api[path] ?? null) : null, error: null, loading: false, reload: () => {} };
}

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => apiState(path),
  usePolledApi: (path: string | null) => apiState(path),
}));

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "",
    ijToken: () => "",
    sseUrl: (p: string) => p,
    get: (path: string) => {
      H.gets.push(path);
      return Promise.resolve(H.api[path] ?? {});
    },
    post: (path: string, body?: Record<string, unknown>) => {
      H.posts.push({ path, body });
      return Promise.resolve(path in H.postResults ? H.postResults[path] : {});
    },
    put: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
  };
});

// The app's ONE shared /health poll. `provided: true` = a DaemonProvider is
// mounted, so nothing on this screen should need a /health request of its own.
vi.mock("@/lib/daemon", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useDaemon: () => H.daemon,
}));

vi.mock("@/lib/useModels", () => ({
  useModels: () => ({ data: { models: [] }, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDocumentVisible", () => ({ useDocumentVisible: () => true }));
vi.mock("@/lib/useReviews", () => ({
  useReviews: () => ({ reviews: {}, loading: false, reload: () => {} }),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "whileHover"]);
  const cache = new Map<string, (props: Record<string, unknown>) => unknown>();
  const tagFor = (tag: string) => {
    let c = cache.get(tag);
    if (!c) {
      c = (props: Record<string, unknown>) => {
        const rest: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
        return createElement(tag, rest);
      };
      cache.set(tag, c);
    }
    return c;
  };
  return {
    // v1.250.0 mock contract: every framer-motion mock exports `m`.
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
    AnimatePresence: ({ children }: { children?: unknown }) => createElement(Fragment, null, children as never),
    useReducedMotion: () => true,
    motion: new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) }),
  };
});
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (prev: string, chunk: string) => prev + chunk,
}));
vi.mock("@/components/chat/DocPreview", () => ({
  DocPreview: (p: { path: string }) => <div data-testid="doc-preview" data-path={p.path} />,
}));
// Markdown is a RECORDER: every parse logs the length of what it was handed.
// MemoMarkdown stays a memo of it, exactly like the real one, so a settled
// block that is not re-parsed records nothing.
vi.mock("@/components/Markdown", async (orig) => {
  const { memo } = await import("react");
  const Recorder = ({ content }: { content: string }) => {
    H.parsed.push(content.length);
    return <div data-md="">{content}</div>;
  };
  return {
    ...(await orig<Record<string, unknown>>()),
    Markdown: Recorder,
    MemoMarkdown: memo(Recorder),
  };
});
vi.mock("@/components/agents/AgentFace", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  default: (p: { name: string }) => {
    H.faces[p.name] = (H.faces[p.name] ?? 0) + 1;
    return <span data-testid="face" data-name={p.name} />;
  },
}));
vi.mock("@/components/kanban/KanbanBoard", () => ({ KanbanBoard: () => null }));
vi.mock("@/components/agents/RosterStrip", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  RosterStrip: () => null,
}));
vi.mock("@/components/agents/SetupCard", () => ({ SetupCard: () => null }));
vi.mock("@/components/agents/AgentsModal", () => ({
  AgentsPanel: (p: { roster: Array<{ name: string }>; selected: { kind: string; name: string } | null }) => (
    <div
      data-testid="agents-panel"
      data-roster={p.roster.map((r) => r.name).join(",")}
      data-selected={p.selected ? `${p.selected.kind}:${p.selected.name}` : ""}
    />
  ),
}));
vi.mock("@/components/agents/world/WorldBoard", () => ({
  WorldBoard: (p: { projectId: string }) => <div data-testid="world-board" data-project={p.projectId} />,
}));
vi.mock("@/components/agents/world/TeamEditor", () => ({
  TeamEditor: (p: { projectId: string; mode: string }) => (
    <div data-testid="team-editor" data-project={p.projectId} data-mode={p.mode} />
  ),
}));
// The two screens the /agents page must not pull in up front. The factory runs
// the moment anything IMPORTS the module, so it records the import itself;
// the real module is returned, so the screens still work when opened.
vi.mock("@/components/agents/mission/TeamScreen", async (orig) => {
  wave1Harness.loaded.add("TeamScreen");
  return await orig<Record<string, unknown>>();
});
vi.mock("@/components/agents/mission/RoomTranscript", async (orig) => {
  wave1Harness.loaded.add("RoomTranscript");
  return await orig<Record<string, unknown>>();
});

// Next's search params, as a store the test can move (v1.309.0 review). Next
// keeps /agents MOUNTED when only the query changes and a Link push fires no
// popstate — the page learns about it only through useSearchParams. `null`
// (the default) is what the hook answers outside an app router, so every test
// above the review block sees exactly what it saw before this mock existed.
const navHarness = vi.hoisted(() => ({
  search: null as string | null,
  listeners: new Set<() => void>(),
}));
vi.mock("next/navigation", async (orig) => {
  const React = await import("react");
  const subscribe = (fn: () => void) => {
    navHarness.listeners.add(fn);
    return () => {
      navHarness.listeners.delete(fn);
    };
  };
  const snap = () => navHarness.search;
  return {
    ...(await orig<Record<string, unknown>>()),
    useSearchParams: () => {
      const s = React.useSyncExternalStore(subscribe, snap, snap);
      return React.useMemo(() => (s === null ? null : new URLSearchParams(s)), [s]);
    },
  };
});

/** A Link push to /agents<search> while the page stays mounted: the URL and
 *  Next's search params change; NO popstate is dispatched. */
function linkPush(search: string) {
  act(() => {
    window.history.pushState(null, "", `/agents${search}`);
    navHarness.search = search.replace(/^\?/, "");
    for (const fn of [...navHarness.listeners]) fn();
  });
}

import AgentsPage from "@/app/agents/page";
import { agentsPath, parseAgentsRoute, type AgentsRoute } from "@/lib/mission";

/** What importing the page (and this file's other imports) loaded. */
const LOADED_AT_IMPORT = new Set(H.loaded);

/* ----------------------------------------------------------- fixtures --- */

const MEMBER_RESEARCHER = {
  session_id: "c1",
  agent: "researcher",
  name: "Researcher",
  kind: "builtin",
  task: "research the three main competitors",
  status: "done",
  progress: { pct: 100, label: "Done", basis: "done" },
  activity: "read k1.pdf",
  waiting_on: null,
  steps: 4,
  result: "found three competitors",
  files: ["C:/work/notes.md"],
  started_at: "2026-10-07T10:00:01",
  finished_at: "2026-10-07T10:02:00",
  provider: "fleet-custom",
  model: "glm-5.3-flash",
};
const MEMBER_WRITER = {
  session_id: "c2",
  agent: "custom:writer",
  name: "writer",
  kind: "custom",
  task: "draft the strategy",
  status: "working",
  progress: { pct: null, label: "Working · step 3", basis: "steps" },
  activity: "wrote strategy.md",
  waiting_on: null,
  steps: 3,
  result: "",
  files: [],
  started_at: "2026-10-07T10:02:01",
  finished_at: null,
  provider: "claude-cli",
  model: "claude-sonnet",
};

const VIEW = {
  found: true,
  session: {
    id: "s1",
    task: "write a market report",
    objective: "write a market report",
    status: "active",
    outcome: null,
    project_id: null,
    created_at: "2026-10-07T10:00:00",
    finished_at: null,
    interrupted: false,
    continued_as: null,
    provider: "",
    model: "",
    route_note: "",
  },
  coordinator: { name: "Jarvis", status: "working", waiting_on: null, steps: 2 },
  members: [MEMBER_RESEARCHER, MEMBER_WRITER],
  progress: { done: 1, total: 2 },
  activity: [
    { at: "2026-10-07T10:00:01", who: "Jarvis", session_id: "s1", text: "Jarvis gave Researcher a task", tone: "info" },
  ],
  deliverable: { text: "", documents: [], worklist: null },
};

function ended(
  status: "completed" | "failed" | "cancelled",
  session: Record<string, unknown> = {},
  extra: Record<string, unknown> = {},
) {
  return {
    ...VIEW,
    session: { ...VIEW.session, status, outcome: status, finished_at: "2026-10-07T10:05:00", ...session },
    coordinator: { ...VIEW.coordinator, status: status === "completed" ? "done" : status },
    members: [MEMBER_RESEARCHER, { ...MEMBER_WRITER, status: "done", progress: { pct: 100, label: "Done", basis: "done" } }],
    progress: { done: 2, total: 2 },
    deliverable: {
      text: status === "completed" ? "# Market report\n\nThree competitors." : "",
      documents: [],
      worklist: null,
    },
    ...extra,
  };
}

function at(search: string) {
  window.history.replaceState(null, "", `/agents${search}`);
}

function daemonWith(providers: Array<Record<string, unknown>>, defaultProvider: string) {
  return {
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    epoch: 0,
    refresh: () => {},
    provided: true,
    health: {
      status: "ok",
      version: "1.308.0",
      default_provider: defaultProvider,
      default_model: "",
      providers,
    },
  };
}

class FakeEventSource {
  url: string;
  closed = false;
  onerror: unknown = null;
  onmessage: unknown = null;
  private listeners: Record<string, Array<(ev: MessageEvent) => void>> = {};
  constructor(url: string) {
    this.url = url;
    H.es.push(this);
  }
  addEventListener(name: string, fn: (ev: MessageEvent) => void) {
    (this.listeners[name] ??= []).push(fn);
  }
  removeEventListener(name: string, fn: (ev: MessageEvent) => void) {
    this.listeners[name] = (this.listeners[name] ?? []).filter((f) => f !== fn);
  }
  close() {
    this.closed = true;
  }
  emit(name: string, data: unknown) {
    if (this.closed) return;
    for (const fn of this.listeners[name] ?? []) fn({ data: JSON.stringify(data) } as MessageEvent);
  }
}

beforeEach(() => {
  H.api = {
    "/agents/roster": {
      roster: [
        { name: "researcher", kind: "builtin", description: "gathers findings", delegable: true, healthy: true },
        { name: "custom:writer", kind: "custom", description: "drafts", delegable: true, healthy: true },
        { name: "supervisor", kind: "builtin", description: "coordinates", delegable: false, healthy: true },
      ],
    },
    "/projects": { projects: [{ id: "p1", name: "Acme", status: "active" }] },
    "/missions": { missions: [] },
    "/agents/worlds": { worlds: [], general: { thread_count: 0 } },
    "/agents/threads": { threads: [] },
    "/agents": { builtin: ["builder"], dynamic: [] },
    "/agents/remote": { agents: [] },
  };
  H.posts = [];
  H.postResults = {};
  H.gets = [];
  H.parsed = [];
  H.faces = {};
  H.es = [];
  H.daemon = daemonWith([], "");
  navHarness.search = null;
  at("");
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/* ------------------------------------------------- the /agents bundle --- */

describe("the /agents page loads Your team and the room transcript only when opened", () => {
  it("importing the page pulls in neither TeamScreen nor RoomTranscript", () => {
    // Red today: app/agents/page.tsx imports both statically, so the New-task
    // front door ships the whole Your-team panel (SetupCard, coach, inbox …).
    expect([...LOADED_AT_IMPORT].sort()).toEqual([]);
  });

  it("control: ?view=team still opens Your team once it is asked for", async () => {
    at("?view=team");
    render(<AgentsPage />);
    expect(await screen.findByTestId("team-screen")).toBeTruthy();
    expect(await screen.findByTestId("agents-panel")).toBeTruthy();
  });
});

/* ------------------------------------- contract 8: one agent, by link --- */

describe("a link opens Your team on one agent (contract 8)", () => {
  it("parses and builds ?view=team&agent=<roster name>", () => {
    expect(parseAgentsRoute("?view=team&agent=custom%3Atax-reader")).toEqual({
      kind: "team",
      agent: "custom:tax-reader",
    });
    // the bare team link is unchanged
    expect(parseAgentsRoute("?view=team")).toEqual({ kind: "team" });
    expect(agentsPath({ kind: "team", agent: "custom:tax-reader" } as unknown as AgentsRoute)).toBe(
      "/agents?view=team&agent=custom%3Atax-reader",
    );
    expect(agentsPath({ kind: "team" })).toBe("/agents?view=team");
  });

  it("the named agent is the one selected in the panel; a bare link selects nobody", async () => {
    at("?view=team&agent=custom%3Awriter");
    render(<AgentsPage />);
    const panel = await screen.findByTestId("agents-panel");
    // AgentsPanel's own `selected` shape: the roster KIND and the BARE name.
    await waitFor(() => expect(panel.getAttribute("data-selected")).toBe("custom:writer"));
    cleanup();

    at("?view=team&agent=researcher");
    render(<AgentsPage />);
    const again = await screen.findByTestId("agents-panel");
    await waitFor(() => expect(again.getAttribute("data-selected")).toBe("builtin:researcher"));
    cleanup();

    // anti-vacuity: no agent in the link → no selection is invented
    at("?view=team");
    render(<AgentsPage />);
    expect((await screen.findByTestId("agents-panel")).getAttribute("data-selected")).toBe("");
  });
});

/* -------------------------------------------- the live centre (stream) --- */

/** v1.327.1: flush, check, and flush again while the check fails. Only the
 *  intervals are faked, so a waitFor alone can never fire the next flush: on
 *  a slow runner the report can need one more tick than a single flush gives
 *  (see openStream for the lazy panel this used to be blamed for). Ten ticks = 1.3 s of
 *  fake time, under the 2 s poll, so nothing else is set off. */
async function flushUntil(check: () => void, tries = 10): Promise<void> {
  for (let i = 0; ; i += 1) {
    await act(async () => {
      vi.advanceTimersByTime(130);
      await new Promise((r) => setTimeout(r, 0));
    });
    try {
      check();
      return;
    } catch (e) {
      if (i + 1 >= tries) throw e;
      await new Promise((r) => setTimeout(r, 25));
    }
  }
}

describe("while the team works", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource);
    // Only the INTERVALS are faked (the 120 ms flush, the 2 s poll): the test
    // decides when a flush happens. setTimeout stays real so waitFor works.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    H.api["/sessions/s1/mission"] = VIEW;
    at("?mission=s1");
  });

  async function openStream() {
    render(<AgentsPage />);
    await screen.findByTestId("mission-card-researcher");
    // v1.327.2: the result panel (MissionOutput) is a LAZY module
    // (next/dynamic); on a slow runner it arrives well after the cards, and
    // a report has nowhere to show until it does. Wait for it, generously.
    await screen.findByTestId("mission-tab-report", {}, { timeout: 10_000 });
    await waitFor(() => expect(H.es.filter((e) => !e.closed)).toHaveLength(1));
    return H.es.filter((e) => !e.closed)[0];
  }

  /** One flush window: the interval flush, a real macrotask for anything
   *  frame-based, and the promise queue (a poll's get()). */
  async function flush(ms = 130) {
    await act(async () => {
      vi.advanceTimersByTime(ms);
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  it("the coordinator's narration before a tool call is not the result: the teammate's draft is, then the final text", async () => {
    const es = await openStream();
    act(() => {
      es.emit("token", { text: "I'll split this into research and writing. " });
      es.emit("tool_call", { id: "t1", name: "delegate", status: "started", args: {} });
      es.emit("member", {
        member: { session_id: "c2", agent: "custom:writer" },
        event: "token",
        data: { text: "# Strategy draft\n\nThe first section" },
      });
    });
    await flushUntil(() => expect(screen.getByTestId("mission-report").getAttribute("data-source")).toBe("draft"));
    const report = screen.getByTestId("mission-report");
    expect(report.textContent).toContain("The first section");
    // Red today: data-source "live" with "I'll split this …" as your result.
    expect(report.textContent).not.toContain("I'll split this");

    // The coordinator's last step has no tool call: THAT text is the result.
    act(() => {
      es.emit("tool_call", { id: "t1", name: "delegate", status: "finished", ok: true, output: "" });
      es.emit("round", { round: 2 });
      es.emit("token", { text: "# Final report\n\nAll three competitors" });
    });
    await flushUntil(() => expect(screen.getByTestId("mission-report").getAttribute("data-source")).toBe("live"));
    const final = screen.getByTestId("mission-report");
    expect(final.textContent).toContain("All three competitors");
    expect(final.textContent).not.toContain("I'll split this");
  });

  it("a long live report is parsed in proportion to what changed, not in full on every flush", async () => {
    const es = await openStream();
    const para = (i: number) => `Paragraph ${i}: ` + "the market moves and the team writes it down. ".repeat(4);
    const tokens: string[] = [];
    for (let p = 0; p < 5; p += 1) {
      const text = para(p);
      const step = Math.ceil(text.length / 8);
      for (let k = 0; k < 8; k += 1) tokens.push(text.slice(k * step, (k + 1) * step));
      tokens[tokens.length - 1] += "\n\n";
    }
    tokens[tokens.length - 1] = tokens[tokens.length - 1].replace(/\n\n$/, "");
    const total = tokens.join("").length;
    H.parsed = [];
    let lastFlush = 0;
    for (const t of tokens) {
      const before = H.parsed.reduce((a, b) => a + b, 0);
      act(() => es.emit("token", { text: t }));
      await flush();
      lastFlush = H.parsed.reduce((a, b) => a + b, 0) - before;
    }
    await waitFor(() => expect(screen.getByTestId("mission-report").textContent).toContain("Paragraph 4"));
    const parsedChars = H.parsed.reduce((a, b) => a + b, 0);
    // anti-vacuity: the report IS rendered as markdown (every character
    // parsed at least once) — a plain <pre> would "pass" the cost pin
    expect(parsedChars).toBeGreaterThanOrEqual(total);
    // Today every flush re-parses the WHOLE report: ~20x its length over 40
    // flushes. Parsing only the growing tail (settled blocks memoised, the
    // chat stream's rule) stays near 6x.
    expect(parsedChars).toBeLessThan(10 * total);
    // The last flush only grew the last paragraph — the four before it are
    // settled and must not be parsed again.
    expect(lastFlush).toBeLessThan(total / 2);
  });

  it("a token flush (or a poll that changed nothing) does not re-render the teammate cards", async () => {
    const es = await openStream();
    // let the first view settle
    await flush();
    const before = { r: H.faces["researcher"] ?? 0, w: H.faces["custom:writer"] ?? 0 };
    expect(before.r).toBeGreaterThan(0);
    // 20 flushes ≈ 2.6 s: also crosses one 2 s poll returning the SAME view
    for (let i = 0; i < 20; i += 1) {
      act(() => es.emit("token", { text: `word${i} ` }));
      await flush();
    }
    await waitFor(() => expect(screen.getByTestId("mission-report").textContent).toContain("word19"));
    expect(H.gets.filter((g) => g === "/sessions/s1/mission").length).toBeGreaterThanOrEqual(2);
    // Red today: useMission lives in the screen root, so every flush and every
    // poll re-renders the rail, every card and every face.
    expect(H.faces["researcher"] ?? 0).toBe(before.r);
    expect(H.faces["custom:writer"] ?? 0).toBe(before.w);
  });
});

/* ------------------------------------- a finished mission: what's next --- */

describe("a finished mission is not a dead end", () => {
  it("Ask for changes continues THIS mission with the user's words and opens the new one", async () => {
    H.api["/sessions/s1/mission"] = ended("completed");
    H.postResults["/sessions/s1/continue"] = { id: "s2", status: "active" };
    H.api["/sessions/s2/mission"] = { ...VIEW, session: { ...VIEW.session, id: "s2" } };
    at("?mission=s1");
    render(<AgentsPage />);
    const bar = await screen.findByTestId("mission-followup");
    fireEvent.change(within(bar).getByTestId("mission-followup-input"), { target: { value: "make it shorter" } });
    fireEvent.click(within(bar).getByTestId("mission-followup-send"));
    await waitFor(() => expect(window.location.search).toBe("?mission=s2"));
    expect(H.posts.find((p) => p.path === "/sessions/s1/continue")?.body).toEqual({
      message: "make it shorter",
      wait: false,
    });
  });

  it("Run it again re-runs a stopped mission and opens the new one — inside its project", async () => {
    H.api["/projects/p1/world"] = { project: { id: "p1", name: "Acme" }, team: [], waiting: [], completed: [] };
    H.api["/sessions/s1/mission"] = ended("cancelled", { project_id: "p1" });
    H.postResults["/sessions/s1/rerun?wait=false"] = { id: "s3", status: "active" };
    H.api["/sessions/s3/mission"] = { ...VIEW, session: { ...VIEW.session, id: "s3", project_id: "p1" } };
    at("?project=p1&mission=s1");
    render(<AgentsPage />);
    const again = await screen.findByTestId("mission-rerun");
    expect(again.textContent).toContain("Run it again");
    fireEvent.click(again);
    await waitFor(() => expect(window.location.search).toBe("?project=p1&mission=s3"));
    expect(H.posts.some((p) => p.path === "/sessions/s1/rerun?wait=false")).toBe(true);
  });

  it("Retry the N failed items is one server step, and opens the continuation", async () => {
    H.api["/sessions/s1/mission"] = ended(
      "failed",
      {},
      { deliverable: { text: "", documents: [], worklist: { total: 5, done: 3, failed: 2, pending: 0, doing: 0 } } },
    );
    H.postResults["/missions/s1/retry-failed"] = { id: "s6", status: "active" };
    H.api["/sessions/s6/mission"] = { ...VIEW, session: { ...VIEW.session, id: "s6" } };
    at("?mission=s1");
    render(<AgentsPage />);
    const retry = await screen.findByTestId("mission-retry-failed");
    expect(retry.textContent).toContain("Retry the 2 failed items");
    fireEvent.click(retry);
    await waitFor(() => expect(window.location.search).toBe("?mission=s6"));
    // ONE request: never a client-side reset-failed + continue pair
    expect(H.posts.map((p) => p.path)).toEqual(["/missions/s1/retry-failed"]);
  });

  it("control: no retry offer when nothing failed, and no follow-up bar while the team still works", async () => {
    H.api["/sessions/s1/mission"] = ended(
      "completed",
      {},
      { deliverable: { text: "# Done", documents: [], worklist: { total: 3, done: 3, failed: 0, pending: 0, doing: 0 } } },
    );
    at("?mission=s1");
    render(<AgentsPage />);
    await screen.findByTestId("mission-report");
    expect(screen.queryByTestId("mission-retry-failed")).toBeNull();
    cleanup();

    H.api["/sessions/s1/mission"] = VIEW;
    at("?mission=s1");
    render(<AgentsPage />);
    await screen.findByTestId("mission-card-researcher");
    expect(screen.queryByTestId("mission-followup")).toBeNull();
    expect(screen.queryByTestId("mission-rerun")).toBeNull();
    expect(screen.getByTestId("mission-stop")).toBeTruthy();
  });
});

describe("an interrupted mission", () => {
  it("offers Continue on its own screen, and opens the continuation", async () => {
    H.api["/sessions/s1/mission"] = ended("failed", { interrupted: true });
    H.postResults["/sessions/s1/continue"] = { id: "s4", status: "active" };
    H.api["/sessions/s4/mission"] = { ...VIEW, session: { ...VIEW.session, id: "s4" } };
    at("?mission=s1");
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("mission-continue"));
    await waitFor(() => expect(window.location.search).toBe("?mission=s4"));
    const body = H.posts.find((p) => p.path === "/sessions/s1/continue")?.body ?? {};
    expect(body.wait).toBe(false);
    expect(typeof body.message === "string" && body.message.trim().length > 0).toBe(true);
  });

  it("once continued, it links forward instead of offering a second Continue", async () => {
    H.api["/sessions/s1/mission"] = ended("failed", { interrupted: true, continued_as: "s5" });
    at("?mission=s1");
    render(<AgentsPage />);
    const link = await screen.findByTestId("mission-continued-as");
    expect(link.textContent).toMatch(/Continued in/);
    const href = link.getAttribute("href") ?? link.querySelector("a")?.getAttribute("href");
    expect(href).toBe("/agents?mission=s5");
    expect(screen.queryByTestId("mission-continue")).toBeNull();
  });
});

describe("the header shows the user's own words", () => {
  it("a continuation's objective is the follow-up, not the model-facing recap", async () => {
    H.api["/sessions/s1/mission"] = {
      ...VIEW,
      session: {
        ...VIEW.session,
        task: "now make it shorter\n\n[Continuing an earlier session. Original task: 'write a market report'. Prior result: …]",
        objective: "now make it shorter",
      },
    };
    at("?mission=s1");
    render(<AgentsPage />);
    const obj = await screen.findByTestId("mission-objective");
    await waitFor(() => expect(obj.textContent).toBe("now make it shorter"));
  });
});

/* ------------------------------------------- which model did the work --- */

describe("the mission says which model did the work", () => {
  it("names the provider and model, quietly, under a real model's report", async () => {
    H.api["/sessions/s1/mission"] = ended("completed", { provider: "claude-cli", model: "claude-opus" });
    at("?mission=s1");
    render(<AgentsPage />);
    const receipt = await screen.findByTestId("mission-receipt");
    expect(receipt.textContent).toContain("claude-cli");
    expect(receipt.textContent).toContain("claude-opus");
    expect(receipt.hasAttribute("data-mock")).toBe(false);
  });

  it("a mock answer is flagged amber, says no real model ran, and points to Connections", async () => {
    H.api["/sessions/s1/mission"] = ended("completed", { provider: "mock", model: "mock" });
    at("?mission=s1");
    render(<AgentsPage />);
    const receipt = await screen.findByTestId("mission-receipt");
    expect(receipt.hasAttribute("data-mock")).toBe(true);
    expect(receipt.textContent).toMatch(/no real model ran/i);
    expect(receipt.querySelector('a[href="/connections"]')).not.toBeNull();
  });

  it("carries the daemon's route note (a failover touched this mission)", async () => {
    const note = "Claude was rate-limited, so codex-cli answered part of this mission.";
    H.api["/sessions/s1/mission"] = ended("completed", { provider: "claude-cli", model: "claude-opus", route_note: note });
    at("?mission=s1");
    render(<AgentsPage />);
    const receipt = await screen.findByTestId("mission-receipt");
    expect(receipt.textContent).toContain(note);
  });

  it("a teammate's card names the model that teammate ran on", async () => {
    H.api["/sessions/s1/mission"] = VIEW;
    at("?mission=s1");
    render(<AgentsPage />);
    const card = await screen.findByTestId("mission-card-researcher");
    fireEvent.click(within(card).getAllByRole("button")[0]);
    const detail = screen.getByTestId("mission-card-detail-researcher");
    expect(detail.textContent).toContain("glm-5.3-flash");
  });
});

/* -------------------------------------------- preflight on the door --- */

describe("the mission door warns before Start when the default model is down", () => {
  it("says so above Start, points to Connections, and Start still sends no model of its own", async () => {
    H.daemon = daemonWith([{ provider: "fleet-custom", available: false, class: "local" }], "fleet-custom");
    H.postResults["/missions"] = { id: "s1", status: "active" };
    H.api["/sessions/s1/mission"] = VIEW;
    render(<AgentsPage />);
    const composer = await screen.findByTestId("mission-composer");
    const warn = within(composer).getByTestId("mission-preflight");
    expect(warn.textContent).toContain("fleet-custom");
    expect(warn.querySelector('a[href="/connections"]')).not.toBeNull();
    // never a poller of its own: the app's shared /health is the source
    expect(H.gets.filter((g) => g.startsWith("/health"))).toEqual([]);
    // never an auto-switch: the request carries no provider/model
    fireEvent.change(screen.getByTestId("mission-input"), { target: { value: "close the books" } });
    fireEvent.click(screen.getByTestId("mission-start"));
    await waitFor(() => expect(H.posts.some((p) => p.path === "/missions")).toBe(true));
    expect(H.posts.find((p) => p.path === "/missions")?.body).toEqual({ objective: "close the books" });
  });

  it("control: a reachable default draws no warning", async () => {
    H.daemon = daemonWith([{ provider: "claude-cli", available: true, class: "cloud" }], "claude-cli");
    render(<AgentsPage />);
    const composer = await screen.findByTestId("mission-composer");
    expect(within(composer).queryByTestId("mission-preflight")).toBeNull();
  });
});

/* ------------------------------------------------- the recent list --- */

describe("the recent list", () => {
  it("says Needs you for a mission parked on an ask, Working for one that is not", async () => {
    H.api["/missions"] = {
      missions: [
        { id: "s3", objective: "close the books", status: "active", waiting: true, project_id: null },
        { id: "s4", objective: "draft the memo", status: "active", waiting: false, project_id: null },
      ],
    };
    render(<AgentsPage />);
    const recent = await screen.findByTestId("mission-recent");
    const parked = within(recent).getByText("close the books").closest("button")!;
    const busy = within(recent).getByText("draft the memo").closest("button")!;
    expect(parked.textContent).toContain("Needs you");
    expect(parked.textContent).not.toContain("Working");
    expect(busy.textContent).toContain("Working");
    expect(busy.textContent).not.toContain("Needs you");
  });
});

/* ----------------------------------------- a teammate asks, a teammate stops --- */

describe("one teammate", () => {
  it("a teammate's ask is answered right on the mission screen", async () => {
    H.api["/sessions/s1/mission"] = {
      ...VIEW,
      members: [
        MEMBER_RESEARCHER,
        { ...MEMBER_WRITER, status: "waiting_you", waiting_on: { approval_id: "a2", tool: "shell" } },
      ],
    };
    at("?mission=s1");
    render(<AgentsPage />);
    await screen.findByTestId("mission-card-custom:writer");
    // Red today: only the COORDINATOR's ask gets a card.
    const cards = await screen.findAllByTestId("mission-approval");
    const mine = cards.find((c) => c.textContent?.includes("writer"));
    expect(mine).toBeTruthy();
    expect(mine!.textContent).toContain("shell");
    fireEvent.click(within(mine!).getByText("Allow for this task"));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/chat/approvals/a2")?.body).toEqual({ decision: "conversation" }),
    );
  });

  it("a working teammate can be stopped from its card; a finished one offers no Stop", async () => {
    H.api["/sessions/s1/mission"] = VIEW;
    at("?mission=s1");
    render(<AgentsPage />);
    const writer = await screen.findByTestId("mission-card-custom:writer");
    fireEvent.click(within(writer).getAllByRole("button")[0]);
    fireEvent.click(await screen.findByTestId("mission-card-stop-custom:writer"));
    await waitFor(() => expect(H.posts.some((p) => p.path === "/sessions/c2/cancel")).toBe(true));
    // only THAT teammate — never the whole mission
    expect(H.posts.some((p) => p.path === "/sessions/s1/cancel")).toBe(false);

    const researcher = screen.getByTestId("mission-card-researcher");
    fireEvent.click(within(researcher).getAllByRole("button")[0]);
    expect(screen.getByTestId("mission-card-detail-researcher")).toBeTruthy();
    expect(screen.queryByTestId("mission-card-stop-researcher")).toBeNull();
  });
});

/* ------------------------------------- v1.309.0 review: the fix round --- */

describe("a link pressed while /agents is already open (contracts 8/9, reachability)", () => {
  it("switches the screen when only the query changes — no remount, no popstate", async () => {
    H.api["/sessions/s1/mission"] = ended("completed");
    render(<AgentsPage />);
    await screen.findByTestId("mission-composer");
    // the bell's agent link (track D): Your team, that agent selected
    linkPush("?view=team&agent=custom%3Awriter");
    expect(await screen.findByTestId("team-screen")).toBeTruthy();
    const panel = await screen.findByTestId("agents-panel");
    await waitFor(() => expect(panel.getAttribute("data-selected")).toBe("custom:writer"));
    // the bell's finished-mission link: that mission's own screen
    linkPush("?mission=s1");
    await waitFor(() => expect(screen.getByTestId("mission-objective").textContent).toBe("write a market report"));
    expect(screen.queryByTestId("team-screen")).toBeNull();
  });

  it("before the URL is read the page paints the neutral skeleton, never nothing", async () => {
    // jsdom runs effects inside render(), so the frame BEFORE the URL is read
    // is observed through a server render (what the static HTML holds).
    const { renderToString } = await import("react-dom/server");
    const html = renderToString(<AgentsPage />);
    expect(html).toContain('data-testid="agents-screen-loading"');
    expect(html).not.toContain("mission-composer");
  });
});

describe("a teammate's narration is not its draft (coordinator-narration-hijacks-result, member half)", () => {
  beforeEach(() => {
    vi.stubGlobal("EventSource", FakeEventSource);
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    H.api["/sessions/s1/mission"] = VIEW;
    at("?mission=s1");
  });

  async function openStream() {
    render(<AgentsPage />);
    await screen.findByTestId("mission-card-researcher");
    // v1.327.2: the result panel (MissionOutput) is a LAZY module
    // (next/dynamic); on a slow runner it arrives well after the cards, and
    // a report has nowhere to show until it does. Wait for it, generously.
    await screen.findByTestId("mission-tab-report", {}, { timeout: 10_000 });
    await waitFor(() => expect(H.es.filter((e) => !e.closed)).toHaveLength(1));
    return H.es.filter((e) => !e.closed)[0];
  }
  async function flush(ms = 130) {
    await act(async () => {
      vi.advanceTimersByTime(ms);
      await new Promise((r) => setTimeout(r, 0));
    });
  }
  const frame = (sid: string, agent: string, event: string, data: Record<string, unknown>) => ({
    member: { session_id: sid, agent },
    event,
    data,
  });

  it("text before a teammate's own tool call is dropped; only what follows is the draft", async () => {
    const es = await openStream();
    act(() => {
      es.emit("member", frame("c2", "custom:writer", "token", { text: "I'll look this up" }));
      es.emit("member", frame("c2", "custom:writer", "tool_call", { id: "w1", name: "web_search", status: "started" }));
      es.emit("member", frame("c2", "custom:writer", "token", { text: "# Draft" }));
    });
    await flushUntil(() => expect(screen.getByTestId("mission-report").getAttribute("data-source")).toBe("draft"));
    const report = screen.getByTestId("mission-report");
    expect(report.textContent).toBe("# Draft");
    expect(report.textContent).not.toContain("I'll look this up");
  });

  it("a held writer whose text turned out to be narration lets another teammate have the centre", async () => {
    const es = await openStream();
    act(() => {
      es.emit("member", frame("c2", "custom:writer", "token", { text: "I'll look this up" }));
    });
    await flushUntil(() => expect(screen.getByTestId("mission-report").textContent).toBe("I'll look this up"));
    act(() => {
      // the writer's words were narration (its own tool call follows) …
      es.emit("member", frame("c2", "custom:writer", "tool_call", { id: "w1", name: "web_search", status: "started" }));
      // … and the researcher writes real text meanwhile
      es.emit("member", frame("c1", "researcher", "token", { text: "# Research notes" }));
    });
    await flushUntil(() => expect(screen.getByTestId("mission-report").textContent).toBe("# Research notes"));
    expect(screen.getByTestId("mission-draft-note").textContent).toContain("Researcher");
  });
});

describe("Continue on an interrupted mission keeps the user's objective (review: honest copy)", () => {
  it("posts the mission's own words, and the resumed mission's header reads them", async () => {
    H.api["/sessions/s1/mission"] = ended("failed", { interrupted: true, objective: "close the books for Acme" });
    H.postResults["/sessions/s1/continue"] = { id: "s4", status: "active" };
    // The daemon's half of the contract (orchestrator options["objective"] =
    // message; sessions._stamp_continuation): the continuation's DISPLAY
    // objective is the posted message, its task the model-facing recap. Read
    // lazily, so the view is built from what was actually sent.
    Object.defineProperty(H.api, "/sessions/s4/mission", {
      configurable: true,
      enumerable: true,
      get() {
        const sent = H.posts.find((p) => p.path === "/sessions/s1/continue");
        const message = String(sent?.body?.message ?? "");
        return {
          ...VIEW,
          session: {
            ...VIEW.session,
            id: "s4",
            objective: message,
            task: `${message}\n\n[Continuing an earlier session. Original task: 'close the books for Acme'. Prior result: interrupted by a daemon restart]`,
          },
        };
      },
    });
    at("?mission=s1");
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("mission-continue"));
    await waitFor(() => expect(window.location.search).toBe("?mission=s4"));
    expect(H.posts.find((p) => p.path === "/sessions/s1/continue")?.body).toEqual({
      message: "close the books for Acme",
      wait: false,
    });
    await waitFor(() => expect(screen.getByTestId("mission-objective").textContent).toBe("close the books for Acme"));
  });
});

/* ------------------------------------- review round 2 (v1.309.0) --- */

describe("the receipt says what happened, not 'answered' (review: TurnReceipt rule)", () => {
  it("a FAILED mission tried its model — nothing answered", async () => {
    H.api["/sessions/s1/mission"] = ended("failed", { provider: "fleet-custom", model: "glm-5.3" });
    at("?mission=s1");
    render(<AgentsPage />);
    const receipt = await screen.findByTestId("mission-receipt");
    expect(receipt.textContent).toContain("fleet-custom");
    expect(receipt.textContent).not.toMatch(/answered/i);
    expect(receipt.textContent).toMatch(/^Tried fleet-custom/);
  });

  it("a stopped mission ran on it, a running one is working on it, a completed one was answered by it", async () => {
    H.api["/sessions/s1/mission"] = ended("cancelled", { provider: "claude-cli", model: "claude-opus" });
    at("?mission=s1");
    render(<AgentsPage />);
    expect((await screen.findByTestId("mission-receipt")).textContent).toMatch(/^Ran on claude-cli/);
    cleanup();

    H.api["/sessions/s1/mission"] = { ...VIEW, session: { ...VIEW.session, provider: "claude-cli", model: "claude-opus" } };
    at("?mission=s1");
    render(<AgentsPage />);
    expect((await screen.findByTestId("mission-receipt")).textContent).toMatch(/^Working on claude-cli/);
    cleanup();

    H.api["/sessions/s1/mission"] = ended("completed", { provider: "claude-cli", model: "claude-opus" });
    at("?mission=s1");
    render(<AgentsPage />);
    expect((await screen.findByTestId("mission-receipt")).textContent).toMatch(/^Answered by claude-cli/);
  });
});

describe("Run it again on a FAILED mission takes the current default (review: model-down dead end)", () => {
  it("re-posts the objective through POST /missions — no rerun clone of the dead provider, no model named", async () => {
    H.api["/projects/p1/world"] = { project: { id: "p1", name: "Acme" }, team: [], waiting: [], completed: [] };
    H.api["/sessions/s1/mission"] = ended("failed", { project_id: "p1", provider: "fleet-custom", model: "glm-5.3" });
    H.postResults["/missions"] = { id: "s7", status: "active", project_id: "p1" };
    H.api["/sessions/s7/mission"] = { ...VIEW, session: { ...VIEW.session, id: "s7", project_id: "p1" } };
    at("?project=p1&mission=s1");
    render(<AgentsPage />);
    const again = await screen.findByTestId("mission-rerun");
    expect(again.textContent).toContain("Run it again");
    fireEvent.click(again);
    await waitFor(() => expect(window.location.search).toBe("?project=p1&mission=s7"));
    expect(H.posts.map((p) => p.path)).toEqual(["/missions"]);
    expect(H.posts[0].body).toEqual({ objective: "write a market report", project_id: "p1" });
  });

  it("control: a failed CONTINUATION keeps the rerun (its follow-up words alone are not the job)", async () => {
    H.api["/sessions/s1/mission"] = ended("failed", {
      objective: "make it shorter",
      task: "make it shorter\n\n[Continuing an earlier session. Original task: 'write a market report'. Prior result: …]",
    });
    H.postResults["/sessions/s1/rerun?wait=false"] = { id: "s3", status: "active" };
    H.api["/sessions/s3/mission"] = { ...VIEW, session: { ...VIEW.session, id: "s3" } };
    at("?mission=s1");
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("mission-rerun"));
    await waitFor(() => expect(window.location.search).toBe("?mission=s3"));
    expect(H.posts.map((p) => p.path)).toEqual(["/sessions/s1/rerun?wait=false"]);
  });
});

describe("the coordinator's full run is one press away (review: mission-dead-end-no-followup)", () => {
  it("links the mission's own session page from the header", async () => {
    H.api["/sessions/s1/mission"] = ended("completed");
    at("?mission=s1");
    render(<AgentsPage />);
    const link = await screen.findByTestId("mission-open-run");
    expect(link.getAttribute("href")).toBe("/sessions/s1");
    expect(link.textContent).toMatch(/Open the full run/);
  });
});

describe("the preflight never says 'will fail' on a stale /health (review: chat's stale rule)", () => {
  it("says it could not check when the last /health could not reach the daemon", async () => {
    H.daemon = {
      ...daemonWith([{ provider: "fleet-custom", available: false, class: "local" }], "fleet-custom"),
      online: false,
      checking: false,
    };
    render(<AgentsPage />);
    const warn = within(await screen.findByTestId("mission-composer")).getByTestId("mission-preflight");
    expect(warn.textContent).toMatch(/could not check your default model/i);
    expect(warn.textContent).toContain("fleet-custom");
    expect(warn.textContent).not.toMatch(/will fail/i);
  });

  it("control: a fresh check of a down default still says a task will fail", async () => {
    H.daemon = daemonWith([{ provider: "fleet-custom", available: false, class: "local" }], "fleet-custom");
    render(<AgentsPage />);
    const warn = within(await screen.findByTestId("mission-composer")).getByTestId("mission-preflight");
    expect(warn.textContent).toMatch(/will fail/i);
  });
});
