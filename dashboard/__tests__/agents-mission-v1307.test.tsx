// v1.307.0 — the Agents page's MISSION screen: one objective, Jarvis runs the
// team, the deliverable first and the workforce underneath it.
//
// Pinned here: the routing (bare /agents = missions, ?view=team and every old
// deep link = the team screens), the decoders' honesty (a null pct stays
// null), what the centre shows (final → live → draft → nothing), the cards
// (an uncounted bar draws no number), the activity log, the tabs, the inline
// approval, Stop, and the mission door's request.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const missionHarness1307 = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: Record<string, unknown> | undefined }>,
  postResults: {} as Record<string, unknown>,
  gets: [] as string[],
}));
const H = missionHarness1307;

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
vi.mock("@/components/agents/AgentFace", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  default: (p: { name: string }) => <span data-testid="face" data-name={p.name} />,
}));
vi.mock("@/components/kanban/KanbanBoard", () => ({ KanbanBoard: () => null }));
vi.mock("@/components/agents/RosterStrip", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  RosterStrip: () => null,
}));
vi.mock("@/components/agents/SetupCard", () => ({ SetupCard: () => null }));
vi.mock("@/components/agents/AgentsModal", () => ({
  AgentsPanel: (p: { roster: Array<{ name: string }>; onTalk?: unknown; onAssign?: unknown }) => (
    <div
      data-testid="agents-panel"
      data-roster={p.roster.map((r) => r.name).join(",")}
      data-talk={p.onTalk ? "yes" : "no"}
      data-assign={p.onAssign ? "yes" : "no"}
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

import AgentsPage from "@/app/agents/page";
import {
  agentsPath,
  decodeMission,
  decodeProgress,
  guideChatPath,
  missionPath,
  parseAgentsRoute,
} from "@/lib/mission";
import { deliverableFor } from "@/components/agents/mission/MissionOutput";

const VIEW = {
  found: true,
  session: {
    id: "s1",
    task: "write a market report",
    status: "active",
    outcome: null,
    project_id: null,
    created_at: "2026-10-07T10:00:00",
    finished_at: null,
  },
  coordinator: { name: "Jarvis", status: "working", waiting_on: null, steps: 2 },
  members: [
    {
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
    },
    {
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
    },
  ],
  progress: { done: 1, total: 2 },
  activity: [
    { at: "2026-10-07T10:00:01", who: "Jarvis", session_id: "s1", text: "Jarvis gave Researcher a task: research the three main competitors", tone: "info" },
    { at: "2026-10-07T10:01:00", who: "Researcher", session_id: "c1", text: "Researcher read k1.pdf", tone: "ok" },
  ],
  deliverable: { text: "", documents: ["C:/work/strategy.md"], worklist: null },
};

const DONE_VIEW = {
  ...VIEW,
  session: { ...VIEW.session, status: "completed", outcome: "completed", finished_at: "2026-10-07T10:05:00" },
  coordinator: { ...VIEW.coordinator, status: "done" },
  deliverable: { ...VIEW.deliverable, text: "# Market report\n\n## Findings\n\n- three competitors" },
};

function at(search: string) {
  window.history.replaceState(null, "", `/agents${search}`);
}

beforeEach(() => {
  H.api = {
    "/agents/roster": {
      roster: [
        { name: "researcher", kind: "builtin", description: "gathers findings", delegable: true, healthy: true },
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
  at("");
});
afterEach(cleanup);

/* ------------------------------------------------------------ pure parts --- */

describe("the decoders and routes", () => {
  it("keeps an uncounted bar uncounted, and clamps a counted one", () => {
    expect(decodeProgress({ pct: null, label: "Working", basis: "steps" }).pct).toBeNull();
    expect(decodeProgress({ label: "x" }).pct).toBeNull();
    expect(decodeProgress({ pct: 140 }).pct).toBe(100);
    expect(decodeProgress({ pct: 42.6 }).pct).toBe(43);
  });

  it("decodes a view and drops what it cannot trust", () => {
    const v = decodeMission({
      ...VIEW,
      members: [...VIEW.members, { name: "no agent key" }],
      activity: [...VIEW.activity, { text: "", tone: "ok" }, { text: "odd tone", tone: "shout" }],
    });
    expect(v).not.toBeNull();
    expect(v!.members.map((m) => m.agent)).toEqual(["researcher", "custom:writer"]);
    expect(v!.members[1].progress.pct).toBeNull();
    expect(v!.activity.map((a) => a.tone)).toEqual(["info", "ok", "info"]);
    expect(decodeMission({ found: false })).toBeNull();
  });

  it("routes: missions (in a project too), the team, a read-only room, the Guide in chat", () => {
    expect(parseAgentsRoute("")).toEqual({ kind: "mission", id: "", project: "" });
    expect(parseAgentsRoute("?mission=s%201")).toEqual({ kind: "mission", id: "s 1", project: "" });
    expect(parseAgentsRoute("?project=p1&mission=s1")).toEqual({ kind: "mission", id: "s1", project: "p1" });
    expect(parseAgentsRoute("?view=team")).toEqual({ kind: "team" });
    expect(parseAgentsRoute("?world=general")).toEqual({ kind: "team" });
    expect(parseAgentsRoute("?thread=t1")).toEqual({ kind: "room", thread: "t1" });
    // a room link wins even when it names its project (the old world links)
    expect(parseAgentsRoute("?project=p1&thread=t1")).toEqual({ kind: "room", thread: "t1" });
    expect(parseAgentsRoute("?talk=guide&ask=how%20do%20updates%20work")).toEqual({
      kind: "guide",
      ask: "how do updates work",
    });
    expect(missionPath("s 1")).toBe("/agents?mission=s%201");
    expect(missionPath("s1", "p1")).toBe("/agents?project=p1&mission=s1");
    expect(missionPath("", "p1")).toBe("/agents?project=p1");
    expect(missionPath()).toBe("/agents");
    expect(agentsPath({ kind: "team" })).toBe("/agents?view=team");
    expect(guideChatPath("how do updates work")).toBe("/chat?ask=%40guide%20how%20do%20updates%20work");
    expect(guideChatPath("  ")).toBe("/chat?ask=%40guide");
  });

  it("the centre shows the final answer, else the live answer, else a teammate's draft", () => {
    const live = { coordinator: "", coordinatorPhase: "", members: {}, latest: null };
    const running = decodeMission(VIEW)!;
    const done = decodeMission(DONE_VIEW)!;
    expect(deliverableFor(done, live)).toMatchObject({ source: "final" });
    expect(deliverableFor(running, live).source).toBe("none");
    expect(
      deliverableFor(running, {
        ...live,
        members: { c2: { name: "writer", text: "## Strategy\n", done: false, phase: "" } },
        latest: "c2",
      }),
    ).toMatchObject({ source: "draft", who: "writer" });
    expect(deliverableFor(running, { ...live, coordinator: "# Report" })).toMatchObject({ source: "live" });
    // a FINISHED mission with no answer never passes a teammate's leftover
    // draft off as the result
    const endedEmpty = decodeMission({
      ...DONE_VIEW,
      session: { ...DONE_VIEW.session, status: "failed" },
      deliverable: { ...DONE_VIEW.deliverable, text: "" },
    })!;
    expect(
      deliverableFor(endedEmpty, {
        ...live,
        members: { c2: { name: "writer", text: "half a draft", done: false, phase: "" } },
        latest: "c2",
      }).source,
    ).toBe("none");
  });
});

/* -------------------------------------------------------------- screens --- */

describe("the mission screen", () => {
  it("opens on New task: the rail, the composer, and the team Jarvis can call on", async () => {
    render(<AgentsPage />);
    expect(await screen.findByTestId("mission-screen")).toBeTruthy();
    const rail = screen.getByTestId("mission-rail");
    for (const label of ["New task", "Chat", "Projects", "Agents", "Tools", "Files", "Settings"]) {
      expect(within(rail).getByText(label)).toBeTruthy();
    }
    expect(screen.getByTestId("mission-rail-new").getAttribute("aria-current")).toBe("page");
    expect(screen.getByTestId("mission-composer")).toBeTruthy();
    const team = screen.getByTestId("mission-available-team");
    // a coordinator cannot take delegated work, so it is not offered
    expect(within(team).getByText("researcher")).toBeTruthy();
    expect(within(team).queryByText("supervisor")).toBeNull();
    // no worlds grid on the front door
    expect(screen.queryByTestId("worlds-grid")).toBeNull();
  });

  it("Start posts ONE objective to the mission door and opens that mission", async () => {
    H.postResults["/missions"] = { id: "s1", status: "active" };
    H.api["/sessions/s1/mission"] = VIEW;
    render(<AgentsPage />);
    fireEvent.change(await screen.findByTestId("mission-input"), {
      target: { value: "write a market report" },
    });
    fireEvent.change(screen.getByTestId("mission-project"), { target: { value: "p1" } });
    fireEvent.click(screen.getByTestId("mission-start"));
    // v1.308.0: a mission given a project opens INSIDE that project's screen
    await waitFor(() => expect(window.location.search).toBe("?project=p1&mission=s1"));
    expect(H.posts.find((p) => p.path === "/missions")?.body).toEqual({
      objective: "write a market report",
      project_id: "p1",
    });
    // v1.325.6: the box is drawn before the mission view answers (it holds
    // nothing until then), so wait for the words, not for the element.
    await waitFor(() =>
      expect(screen.getByTestId("mission-objective").textContent).toBe(
        "write a market report",
      ),
    );
  });

  it("a running mission: cards with honest bars, the live log, the draft-free centre", async () => {
    H.api["/sessions/s1/mission"] = VIEW;
    at("?mission=s1");
    render(<AgentsPage />);
    const done = await screen.findByTestId("mission-card-researcher");
    expect(done.getAttribute("data-status")).toBe("done");
    const doneBar = screen.getByTestId("mission-bar-researcher");
    expect(doneBar.getAttribute("aria-valuenow")).toBe("100");
    expect(done.textContent).toContain("100%");
    // an uncounted member draws NO number — a moving stripe and its words
    const writer = screen.getByTestId("mission-card-custom:writer");
    const writerBar = screen.getByTestId("mission-bar-custom:writer");
    expect(writerBar.getAttribute("aria-valuenow")).toBeNull();
    expect(writerBar.getAttribute("aria-valuetext")).toBe("Working · step 3");
    expect(writerBar.querySelector("[data-indeterminate]")).not.toBeNull();
    expect(writer.textContent).not.toMatch(/\d+%/);
    expect(screen.getByTestId("mission-team-count").textContent).toBe("1 of 2 done");
    // the log, in the daemon's words
    const log = screen.getByTestId("mission-activity");
    expect(within(log).getByText("Jarvis gave Researcher a task: research the three main competitors")).toBeTruthy();
    expect(within(log).getByText("Researcher read k1.pdf")).toBeTruthy();
    // nothing written yet → the centre says so instead of inventing a report
    expect(screen.getByTestId("mission-output-empty")).toBeTruthy();
    expect(screen.getByTestId("mission-headline").textContent).toContain("1 of 2 teammates done");
  });

  it("a card expands in place for its detail and collapses back", async () => {
    H.api["/sessions/s1/mission"] = VIEW;
    at("?mission=s1");
    render(<AgentsPage />);
    const card = await screen.findByTestId("mission-card-researcher");
    expect(screen.queryByTestId("mission-card-detail-researcher")).toBeNull();
    fireEvent.click(within(card).getByRole("button"));
    const detail = screen.getByTestId("mission-card-detail-researcher");
    expect(detail.textContent).toContain("found three competitors");
    expect(detail.textContent).toContain("notes.md");
    expect(within(detail).getByText(/Open the full run/).closest("a")?.getAttribute("href")).toBe("/sessions/c1");
    fireEvent.click(within(card).getAllByRole("button")[0]);
    expect(screen.queryByTestId("mission-card-detail-researcher")).toBeNull();
  });

  it("a finished mission: Report renders it, Markdown shows it raw, Preview opens the files", async () => {
    H.api["/sessions/s1/mission"] = DONE_VIEW;
    at("?mission=s1");
    render(<AgentsPage />);
    const report = await screen.findByTestId("mission-report");
    expect(report.getAttribute("data-source")).toBe("final");
    expect(within(report).getByRole("heading", { name: "Market report" })).toBeTruthy();
    expect(screen.queryByTestId("mission-stop")).toBeNull();
    fireEvent.click(screen.getByTestId("mission-tab-markdown"));
    expect(screen.getByTestId("mission-markdown").textContent).toContain("# Market report");
    fireEvent.click(screen.getByTestId("mission-tab-preview"));
    fireEvent.click(within(screen.getByTestId("mission-preview")).getByText("strategy.md"));
    expect(screen.getByTestId("doc-preview").getAttribute("data-path")).toBe("C:/work/strategy.md");
  });

  it("an ask is answered right where the work is, and Stop cancels the coordinator", async () => {
    H.api["/sessions/s1/mission"] = {
      ...VIEW,
      coordinator: { ...VIEW.coordinator, waiting_on: { approval_id: "a1", tool: "web_search" } },
    };
    at("?mission=s1");
    render(<AgentsPage />);
    const card = await screen.findByTestId("mission-approval");
    expect(card.textContent).toContain("web search");
    fireEvent.click(within(card).getByText("Allow once"));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/chat/approvals/a1")?.body).toEqual({ decision: "once" }),
    );
    fireEvent.click(screen.getByTestId("mission-stop"));
    await waitFor(() => expect(H.posts.some((p) => p.path === "/sessions/s1/cancel")).toBe(true));
  });

  it("the rail's Agents row opens Your team, and its New task row leads back", async () => {
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("mission-rail-agents"));
    await waitFor(() => expect(window.location.search).toBe("?view=team"));
    expect(screen.queryByTestId("mission-screen")).toBeNull();
    const panel = await screen.findByTestId("agents-panel");
    // every agent, managed here — but work is never handed out one by one
    expect(panel.getAttribute("data-roster")).toBe("researcher,supervisor");
    expect(panel.getAttribute("data-talk")).toBe("no");
    expect(panel.getAttribute("data-assign")).toBe("no");
    expect(screen.getByTestId("mission-rail-agents").getAttribute("aria-current")).toBe("page");
    fireEvent.click(screen.getByTestId("mission-rail-new"));
    await waitFor(() => expect(window.location.search).toBe(""));
    expect(await screen.findByTestId("mission-composer")).toBeTruthy();
  });

  it("the old General link opens Your team; no round table is drawn anywhere", async () => {
    at("?world=general");
    render(<AgentsPage />);
    expect(await screen.findByTestId("team-screen")).toBeTruthy();
    expect(screen.queryByTestId("mission-screen")).toBeNull();
    expect(screen.queryByTestId("round-table")).toBeNull();
  });

  it("a mission that no longer exists says so", async () => {
    H.api["/sessions/gone/mission"] = { found: false, session_id: "gone" };
    at("?mission=gone");
    render(<AgentsPage />);
    expect(await screen.findByTestId("mission-missing")).toBeTruthy();
  });
});

/* ------------------------------------------- v1.308.0: no round table --- */

const WORLD = {
  project: { id: "p1", name: "Acme", root: "C:/work/acme" },
  team: [
    { name: "reviewer", kind: "builtin", description: "careful second look" },
    { name: "custom:writer", kind: "custom", description: "drafts" },
  ],
  thread_id: null,
  counts: { waiting: 1, running: 0, queued: 0, done_7d: 2 },
  waiting: [{ id: "w1", kind: "ask", title: "Allow shell for the close", link: "/sessions/s9" }],
  completed: [{ id: "c1", title: "Q3 reconciliation", agent: "reviewer" }],
};

describe("a project's mission screen", () => {
  beforeEach(() => {
    H.api["/projects/p1/world"] = WORLD;
    H.api["/missions?project_id=p1"] = {
      missions: [{ id: "s7", objective: "close the books", status: "completed", project_id: "p1" }],
    };
  });

  it("is the project's: its name, its team, its objectives, its work — and no project picker", async () => {
    at("?project=p1");
    render(<AgentsPage />);
    // the header is drawn before the project answers ("…"), so wait for the name
    await waitFor(() =>
      expect(screen.getByTestId("mission-project-header").textContent).toContain("Acme"),
    );
    expect(screen.getByText("What should the Acme team get done?")).toBeTruthy();
    expect(screen.queryByTestId("mission-project")).toBeNull();
    const team = screen.getByTestId("mission-project-team");
    expect(within(team).getByTestId("project-team-reviewer")).toBeTruthy();
    expect(within(team).getByTestId("project-team-custom:writer")).toBeTruthy();
    expect(team.textContent).toContain("Jarvis hands this project's work only to");
    // the project's own objectives, not everyone's
    expect(screen.getByTestId("mission-recent").textContent).toContain("close the books");
    // Board / Waiting on you / Completed — opening on what waits for the user
    const work = screen.getByTestId("mission-project-work");
    expect(within(work).getByTestId("project-tab-waiting").getAttribute("aria-selected")).toBe("true");
    expect(work.textContent).toContain("Allow shell for the close");
    fireEvent.click(within(work).getByTestId("project-tab-board"));
    expect(screen.getByTestId("world-board").getAttribute("data-project")).toBe("p1");
    fireEvent.click(within(work).getByTestId("project-tab-completed"));
    expect(work.textContent).toContain("Q3 reconciliation");
  });

  it("Start sends the project, and the mission opens INSIDE the project", async () => {
    H.postResults["/missions"] = { id: "s8", status: "active" };
    H.api["/sessions/s8/mission"] = { ...VIEW, session: { ...VIEW.session, id: "s8", project_id: "p1" } };
    at("?project=p1");
    render(<AgentsPage />);
    fireEvent.change(await screen.findByTestId("mission-input"), { target: { value: "close the books" } });
    fireEvent.click(screen.getByTestId("mission-start"));
    await waitFor(() => expect(window.location.search).toBe("?project=p1&mission=s8"));
    expect(H.posts.find((p) => p.path === "/missions")?.body).toEqual({
      objective: "close the books",
      project_id: "p1",
    });
    // back goes to the project, then to all objectives
    fireEvent.click(await screen.findByTestId("mission-back"));
    await waitFor(() => expect(window.location.search).toBe("?project=p1"));
    fireEvent.click(await screen.findByTestId("mission-back"));
    await waitFor(() => expect(window.location.search).toBe(""));
  });

  it("Edit team opens the team editor for THIS project", async () => {
    at("?project=p1");
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("mission-edit-team"));
    const editor = await screen.findByTestId("team-editor");
    expect(editor.getAttribute("data-project")).toBe("p1");
    expect(editor.getAttribute("data-mode")).toBe("edit");
  });

  it("with no team yet it says Jarvis picks from everyone, and offers to pick one", async () => {
    H.api["/projects/p1/world"] = { ...WORLD, team: [] };
    at("?project=p1");
    render(<AgentsPage />);
    const team = await screen.findByTestId("mission-project-team");
    expect(team.textContent).toContain("Jarvis chooses from all your agents");
    expect(screen.getByTestId("mission-edit-team").textContent).toContain("Pick a team");
  });

  it("the front door lists the projects, and a click opens that project's screen", async () => {
    H.api["/agents/worlds"] = {
      worlds: [{ project: { id: "p1", name: "Acme" }, team: WORLD.team, thread_id: null, counts: WORLD.counts }],
    };
    render(<AgentsPage />);
    const row = await screen.findByTestId("mission-project-p1");
    expect(row.textContent).toContain("Acme");
    expect(row.textContent).toContain("1 waiting on you");
    fireEvent.click(row);
    await waitFor(() => expect(window.location.search).toBe("?project=p1"));
    expect(await screen.findByTestId("mission-project-header")).toBeTruthy();
  });
});

describe("old links", () => {
  it("a room link shows that conversation read-only, with its speakers", async () => {
    H.api["/agents/threads/t1"] = {
      id: "t1",
      title: "Pricing chat",
      project_id: "p1",
      participants: [{ key: "builtin:reviewer", source: "builtin", name: "reviewer", role: "critic" }],
      messages: [
        { who: "user", content: "is the fee right?", at: "2026-10-01T10:00:00" },
        { who: "builtin:reviewer", content: "It is low for the scope.", at: "2026-10-01T10:00:05" },
        { who: "builtin:reviewer", content: "", error: "reviewer couldn't answer: offline" },
      ],
    };
    at("?thread=t1");
    render(<AgentsPage />);
    const room = await screen.findByTestId("room-transcript");
    await waitFor(() => expect(within(room).getAllByTestId("room-entry")).toHaveLength(3));
    expect(room.textContent).toContain("Pricing chat");
    expect(room.textContent).toContain("It is low for the scope.");
    expect(room.textContent).toContain("reviewer couldn't answer: offline");
    expect(within(room).getByText(/Back to the project/).closest("a")?.getAttribute("href")).toBe(
      "/agents?project=p1",
    );
    // read-only: nothing to type into, nothing to send
    expect(room.querySelector("textarea")).toBeNull();
  });

  it("Ask the Guide hands the question to chat as an @guide mention", async () => {
    at("?talk=guide&ask=how%20do%20updates%20install");
    render(<AgentsPage />);
    const note = await screen.findByTestId("guide-handoff");
    expect(note.querySelector("a")?.getAttribute("href")).toBe(
      "/chat?ask=%40guide%20how%20do%20updates%20install",
    );
  });
});
