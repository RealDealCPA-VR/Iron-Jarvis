/**
 * v1.304.0 — Agents = project WORLDS.
 *
 * "a round table for each set of agents … grouped by the project; when a
 * project is selected you enter the world of that project: still a round
 * table chat at the forefront, but tabs including board, items pending the
 * user, completed tasks, new tasks for the user to give to the group".
 *
 * WHAT THESE TESTS GUARD:
 *  - the landing grid: a card per world (name, LARGE 40px faces, counts, the
 *    waiting badge) plus "General" with its room count;
 *  - entering a world by click (URL becomes ?project=<id>) and by URL; the
 *    in-app back and the browser's back both return to the grid;
 *  - the world's table is RoundTable with projectId; its room is made LAZILY
 *    (one POST, only with a team) and an existing room is never re-made;
 *  - tabs: Board mounts (and polls /sessions) ONLY while open; Waiting on you
 *    has the badge and links where each item is resolved; Completed lists
 *    outcome + file names; New task offers ONLY the team (no remote, no
 *    coordinator) plus "Whole team — Jarvis decides", which posts NO assignee;
 *  - TeamEditor: the empty-team build step, suggestions with their why,
 *    add/remove, PUT /team, and an edit re-seats the room keeping roles;
 *  - an older daemon (404 on /agents/worlds) gets today's page exactly, even
 *    on a ?project= URL; ?talk=/?thread= deep links still open General.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const worldsHarness1304 = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  errors: {} as Record<string, { status: number; message: string }>,
  loading: {} as Record<string, boolean>,
  requested: [] as string[],
  posts: [] as Array<{ path: string; body: Record<string, unknown> | undefined }>,
  puts: [] as Array<{ path: string; body: Record<string, unknown> | undefined }>,
  gets: [] as string[],
  postResults: {} as Record<string, unknown>,
  postErrors: {} as Record<string, string>,
  getErrors: {} as Record<string, number>,
  putResults: {} as Record<string, unknown>,
}));
const H = worldsHarness1304;

function apiState(path: string | null) {
  if (path) H.requested.push(path);
  return {
    data: path ? (H.api[path] ?? null) : null,
    error: path ? (H.errors[path] ?? null) : null,
    loading: path ? Boolean(H.loading[path]) : false,
    reload: () => {},
  };
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
    get: (path: string) => {
      H.gets.push(path);
      if (path in H.getErrors) return Promise.reject(new ApiError("Not Found", H.getErrors[path]));
      return Promise.resolve(H.api[path] ?? {});
    },
    post: (path: string, body?: Record<string, unknown>) => {
      H.posts.push({ path, body });
      if (path in H.postErrors) return Promise.reject(new ApiError(H.postErrors[path], 500));
      return Promise.resolve(path in H.postResults ? H.postResults[path] : {});
    },
    put: (path: string, body?: Record<string, unknown>) => {
      H.puts.push({ path, body });
      return Promise.resolve(path in H.putResults ? H.putResults[path] : {});
    },
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
  // ONE component per tag: a fresh function per access would be a new type on
  // every render, remounting everything under a PageShell — a mock artifact
  // the real app does not have (and one that hid a remount-safety question).
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
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    useReducedMotion: () => true,
    motion: new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) }),
  };
});

vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (prev: string, chunk: string) => prev + chunk,
}));

// The table and the board are live surfaces of their own (and the faces are
// another doer's this release); this file needs to know WHAT they were told.
vi.mock("@/components/agents/RoundTable", () => ({
  RoundTable: (p: {
    threadId: string;
    projectId?: string;
    initialInput?: string;
    reloadNonce?: number;
  }) => (
    <div
      data-testid="round-table"
      data-thread={p.threadId}
      data-nonce={String(p.reloadNonce ?? "")}
      data-project={p.projectId ?? ""}
      data-initial={p.initialInput ?? ""}
    />
  ),
}));
vi.mock("@/components/kanban/KanbanBoard", () => ({
  KanbanBoard: (p: { projectId?: string }) => (
    <div data-testid="kanban-board" data-project={p.projectId ?? ""} />
  ),
}));
vi.mock("@/components/agents/AgentFace", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  default: (p: { name: string; size?: number; avatarUrl?: string | null }) => (
    <span data-testid="face" data-name={p.name} data-size={p.size} data-src={p.avatarUrl ?? ""} />
  ),
}));
vi.mock("@/components/agents/RosterStrip", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  RosterStrip: () => null,
}));
vi.mock("@/components/agents/SetupCard", () => ({ SetupCard: () => null }));
vi.mock("@/components/agents/PanelPicker", () => ({ PanelPicker: () => null }));
vi.mock("@/components/agents/AgentsModal", () => ({ AgentsModal: () => null }));

import AgentsPage from "@/app/agents/page";
import { __resetRoomRequests } from "@/components/agents/world/WorldView";
import {
  countsLine,
  normaliseCounts,
  memberKey,
  memberSource,
  parseWorldRoute,
  roomIdOf,
  teamAssigneeChoices,
  waitingHref,
  worldPath,
} from "@/lib/agentWorlds";

/* ------------------------------------------------------------ fixtures --- */

const ROSTER = [
  { name: "builder", kind: "builtin", description: "Builds", delegable: true, healthy: true, stats: null, avatar: "/agents/builder/avatar" },
  { name: "supervisor", kind: "builtin", description: "Leads", delegable: false, healthy: true, stats: null },
  { name: "researcher", kind: "builtin", description: "Reads", delegable: true, healthy: true, stats: null },
  { name: "custom:analyst", kind: "dynamic", description: "Numbers", delegable: true, healthy: true, stats: null },
  { name: "remote:hermes", kind: "remote", description: "Far away", delegable: true, healthy: true, stats: null },
];

const WORLDS = {
  worlds: [
    {
      project: { id: "p1", name: "Tax season", status: "active" },
      team: [
        { name: "builder", avatar: "/agents/builder/avatar" },
        { name: "custom:analyst" },
      ],
      thread_id: "t-p1",
      counts: { waiting: 3, running: 1, queued: 0, done_7d: 4 },
    },
    {
      project: { id: "p2", name: "Website", status: "active" },
      team: [],
      thread_id: null,
      counts: { waiting: 0, running: 0, queued: 0, done_7d: 0 },
    },
  ],
  general: { thread_count: 5 },
};

const P1_WORLD = {
  project: { id: "p1", name: "Tax season" },
  team: [
    { name: "builder", kind: "builtin", avatar: "/agents/builder/avatar" },
    { name: "custom:analyst", kind: "custom" },
    { name: "remote:hermes", kind: "remote" },
    { name: "supervisor", kind: "builtin" },
  ],
  thread_id: "t-p1",
  counts: { waiting: 4, running: 1, queued: 0, done_7d: 4 },
  waiting: [
    { id: "w1", kind: "ask", title: "May I write to Q3.xlsx?", agent: "builder", since: null, link: "/sessions/s-ask" },
    { id: "w2", kind: "blocked", title: "Reconcile the bank", agent: "custom:analyst", reason: "failed 3 times", link: "/projects/p1?tab=tasks" },
    { id: "w3", kind: "needs_you", title: "Pick the filing status", agent: "builder", link: "/sessions/s-stop" },
    { id: "w4", kind: "interrupted", title: "Import the 1099s", agent: "builder", link: "/sessions/s-cut" },
  ],
  completed: [
    { id: "c1", title: "Draft the engagement letter", agent: "builder", finished_at: null, outcome: "completed", files: ["letter.docx", "notes.md"], link: "/sessions/s-done" },
  ],
};

const P2_WORLD = {
  project: { id: "p2", name: "Website" },
  team: [],
  thread_id: null,
  counts: { waiting: 0, running: 0, queued: 0, done_7d: 0 },
  waiting: [],
  completed: [],
};

const P2_TEAM = {
  team: [],
  suggestions: [
    { name: "researcher", kind: "builtin", why: "worked on 3 tasks here in the last 30 days" },
  ],
};

function at(search: string) {
  window.history.replaceState(null, "", `/agents${search}`);
}

function baseApi() {
  H.api = {
    "/agents/roster": { roster: ROSTER },
    "/agents/threads": {
      threads: [
        { id: "g-1", title: "General chat", participants: [], message_count: 1, updated_at: "2026-10-01T00:00:00Z" },
      ],
    },
    "/agents": { builtin: ["builder", "supervisor", "researcher"], dynamic: [{ name: "analyst" }] },
    "/agents/remote": { agents: [] },
    "/agents/worlds": WORLDS,
    "/projects/p1/world": P1_WORLD,
    "/projects/p2/world": P2_WORLD,
    "/projects/p2/team": P2_TEAM,
    "/projects/p1/team": { team: P1_WORLD.team, suggestions: [] },
    "/projects/p1": { project: { id: "p1", root: "C:/work/tax" }, sessions: [] },
  };
}

beforeEach(() => {
  baseApi();
  H.errors = {};
  H.loading = {};
  H.requested = [];
  H.posts = [];
  H.puts = [];
  H.gets = [];
  H.postResults = {};
  H.putResults = {};
  H.postErrors = {};
  H.getErrors = {};
  __resetRoomRequests();
  try {
    window.localStorage.clear();
  } catch {
    /* no storage */
  }
  at("");
});
afterEach(cleanup);

const requestedSessionsPoll = () =>
  H.requested.some((p) => p.startsWith("/sessions?project_id=p1"));

/* --------------------------------------------------------------- grid --- */

describe("the worlds grid", () => {
  it("shows a card per world with LARGE faces, counts and the waiting badge, plus General", async () => {
    render(<AgentsPage />);
    const card = await screen.findByTestId("world-card-p1");
    expect(card.textContent).toContain("Tax season");
    expect(screen.getByTestId("world-counts-p1").textContent).toBe(
      "3 waiting on you · 1 running · 4 done this week",
    );
    expect(screen.getByTestId("world-waiting-p1").textContent).toBe("3 waiting on you");
    const faces = within(card).getAllByTestId("face");
    expect(faces.map((f) => f.getAttribute("data-name"))).toEqual(["builder", "analyst"]);
    expect(faces.every((f) => f.getAttribute("data-size") === "40")).toBe(true);
    // the uploaded portrait is used where there is one
    expect(faces[0].getAttribute("data-src")).toContain("/agents/builder/avatar");
    // a ROW, not a stack: every face has its own space and its name on hover
    const row = within(card).getByTestId("team-faces");
    expect(row.className).not.toMatch(/(^|\s)-(space-x|ml|mr|mx)-/);
    expect(row.className).toMatch(/(^|\s)gap-/);
    expect(within(card).getByTestId("team-face-builder").getAttribute("title")).toMatch(/^builder/);
    expect(within(card).getByTestId("team-face-custom:analyst").getAttribute("title")).toMatch(/^analyst/);
    // 1 column narrow, 2 medium, 3 wide
    const cls = screen.getByTestId("worlds-grid-list").className.split(/\s+/);
    expect(cls).toEqual(expect.arrayContaining(["grid-cols-1", "md:grid-cols-2", "xl:grid-cols-3"]));
    // an empty world says it has no team; a quiet world says so
    expect(screen.getByTestId("world-card-p2").textContent).toContain("No team yet");
    expect(screen.getByTestId("world-counts-p2").textContent).toBe("Quiet — nothing running");
    expect(screen.queryByTestId("world-waiting-p2")).toBeNull();
    // General: today's rooms, with its count
    expect(screen.getByTestId("world-card-general")).toBeTruthy();
    expect(screen.getByTestId("world-general-count").textContent).toBe("5 rooms");
    // the grid is the landing — not today's room
    expect(screen.queryByTestId("agents-room")).toBeNull();
  });

  it("shows at most 6 faces, then '+N', each with a status dot", async () => {
    const big: Array<Record<string, unknown>> = [
      "builder", "researcher", "custom:a", "custom:b", "custom:c", "custom:d", "custom:e", "custom:f",
    ].map((name) => ({ name, healthy: true }));
    big[0] = { name: "builder", paused: true };
    H.api["/agents/worlds"] = {
      worlds: [{ ...WORLDS.worlds[0], team: big }],
      general: { thread_count: 0 },
    };
    render(<AgentsPage />);
    const card = await screen.findByTestId("world-card-p1");
    expect(within(card).getAllByTestId("face")).toHaveLength(6);
    expect(within(card).getByTestId("team-faces-more").textContent).toBe("+2");
    expect(within(card).getByTestId("team-faces-more").getAttribute("title")).toBe("e, f");
    expect(within(card).getByTestId("team-face-builder").getAttribute("data-status")).toBe("paused");
    expect(within(card).getByTestId("team-face-researcher").getAttribute("data-status")).toBe("ready");
  });

  it("the world components never overlap faces (no negative margins, no stack)", async () => {
    const { readFileSync } = await import("node:fs");
    const read = (f: string) =>
      readFileSync(`${process.cwd()}/components/agents/world/${f}`, "utf8").replace(/\r\n/g, "\n");
    for (const f of ["WorldsGrid.tsx", "WorldView.tsx"]) {
      const src = read(f);
      expect(src).not.toMatch(/-space-x-/);
      expect(src).not.toMatch(/FaceStack/);
    }
  });

  it("goes straight to General when there are no project worlds", async () => {
    H.api["/agents/worlds"] = { worlds: [], general: { thread_count: 1 } };
    render(<AgentsPage />);
    await screen.findByTestId("agents-room");
    expect(screen.queryByTestId("worlds-grid")).toBeNull();
    // and no way "back" to a grid that would hold only General
    expect(screen.queryByTestId("general-world-nav")).toBeNull();
  });

  it("shows a placeholder, not General-then-grid, while the first answer is in flight", async () => {
    delete H.api["/agents/worlds"];
    H.loading["/agents/worlds"] = true;
    render(<AgentsPage />);
    await waitFor(() => expect(H.requested).toContain("/agents/worlds"));
    expect(screen.queryByTestId("agents-room")).toBeNull();
    expect(screen.queryByTestId("worlds-grid")).toBeNull();
  });
});

/* ---------------------------------------------------------- entering --- */

describe("entering a world", () => {
  it("by click: the URL names the project and the table is the project's room", async () => {
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("world-card-p1"));
    const view = await screen.findByTestId("world-view");
    expect(view.getAttribute("data-project")).toBe("p1");
    expect(window.location.search).toBe("?project=p1");
    const table = screen.getByTestId("round-table");
    expect(table.getAttribute("data-thread")).toBe("t-p1");
    expect(table.getAttribute("data-project")).toBe("p1");
    // an existing room is never re-made
    expect(H.posts.some((p) => p.path.endsWith("/world/room"))).toBe(false);
    // header: name, project link, team faces, Edit team
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Tax season");
    expect(screen.getByTestId("world-project-link").getAttribute("href")).toBe("/projects/p1");
    expect(screen.getByTestId("world-edit-team")).toBeTruthy();
    // the header's team: a non-overlapping row of 32px faces, names on hover
    const header = screen.getByRole("heading", { level: 1 }).closest("header")!;
    const row = within(header).getByTestId("team-faces");
    const faces = within(row).getAllByTestId("face");
    expect(faces.length).toBe(4);
    expect(faces.every((f) => f.getAttribute("data-size") === "32")).toBe(true);
    expect(row.className).not.toMatch(/(^|\s)-(space-x|ml|mr|mx)-/);
    expect(within(row).getByTestId("team-face-remote:hermes").getAttribute("title")).toMatch(/^hermes/);
  });

  it("by URL: /agents?project=<id> opens that world directly", async () => {
    at("?project=p1");
    render(<AgentsPage />);
    const view = await screen.findByTestId("world-view");
    expect(view.getAttribute("data-project")).toBe("p1");
    expect(screen.queryByTestId("worlds-grid")).toBeNull();
  });

  it("the in-app back returns to the grid through the browser history", async () => {
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("world-card-p1"));
    await screen.findByTestId("world-view");
    const depth = window.history.length;
    fireEvent.click(screen.getByTestId("world-back"));
    await screen.findByTestId("worlds-grid");
    expect(window.location.search).toBe("");
    // a real step back, not a second forward entry the back button would
    // then walk the user into the world again through
    expect(window.history.length).toBe(depth);
  });

  it("the browser's back button returns to the grid too", async () => {
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("world-card-p1"));
    await screen.findByTestId("world-view");
    act(() => {
      window.history.back();
    });
    await screen.findByTestId("worlds-grid");
    expect(screen.queryByTestId("world-view")).toBeNull();
  });

  it("a shared ?project= link with no grid behind it goes back by pushing the grid", async () => {
    at("?project=p1");
    render(<AgentsPage />);
    await screen.findByTestId("world-view");
    fireEvent.click(screen.getByTestId("world-back"));
    await screen.findByTestId("worlds-grid");
    expect(window.location.search).toBe("");
  });

  it("General is a card: it opens today's rooms, with a way back", async () => {
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("world-card-general"));
    await screen.findByTestId("agents-room");
    expect(window.location.search).toBe("?world=general");
    expect(screen.getByTestId("round-table").getAttribute("data-thread")).toBe("g-1");
    fireEvent.click(screen.getByTestId("general-back"));
    await screen.findByTestId("worlds-grid");
  });
});

/* -------------------------------------------------------------- tabs --- */

describe("the world's tabs", () => {
  it("opens on Waiting on you with the badge, and each item links where it is resolved", async () => {
    at("?project=p1");
    render(<AgentsPage />);
    const tabs = await screen.findByTestId("world-tabs");
    expect(tabs.getAttribute("data-tab")).toBe("waiting");
    expect(screen.getByTestId("world-waiting-badge").textContent).toBe("4");
    // a run that stopped for the user, and one a restart cut off: both link to the session
    const stopped = screen.getByTestId("world-waiting-item-w3");
    expect(stopped.textContent).toContain("Stopped — needs your answer");
    expect(stopped.getAttribute("href")).toBe("/sessions/s-stop");
    const cut = screen.getByTestId("world-waiting-item-w4");
    expect(cut.textContent).toContain("Cut off by a restart — Continue?");
    expect(cut.getAttribute("href")).toBe("/sessions/s-cut");
    expect(screen.getByTestId("world-waiting-item-w1").getAttribute("href")).toBe("/sessions/s-ask");
    expect(screen.getByTestId("world-waiting-item-w2").getAttribute("href")).toBe(
      "/projects/p1?tab=tasks",
    );
    expect(screen.getByTestId("world-waiting-item-w2").textContent).toContain("failed 3 times");
  });

  it("mounts the Board ONLY while its tab is open", async () => {
    at("?project=p1");
    render(<AgentsPage />);
    await screen.findByTestId("world-tabs");
    expect(screen.queryByTestId("kanban-board")).toBeNull();
    expect(screen.queryByTestId("world-board")).toBeNull();
    expect(requestedSessionsPoll()).toBe(false);

    H.api["/sessions?project_id=p1"] = { sessions: [{ id: "s1", project_id: "p1", status: "active" }] };
    fireEvent.click(screen.getByTestId("world-tab-board"));
    const board = await screen.findByTestId("kanban-board");
    expect(board.getAttribute("data-project")).toBe("p1");
    expect(requestedSessionsPoll()).toBe(true);

    fireEvent.click(screen.getByTestId("world-tab-completed"));
    await waitFor(() => expect(screen.queryByTestId("kanban-board")).toBeNull());
  });

  it("Completed lists outcome, file names and a link", async () => {
    at("?project=p1");
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("world-tab-completed"));
    const item = await screen.findByTestId("world-completed-item-c1");
    expect(item.textContent).toContain("Draft the engagement letter");
    expect(item.textContent).toContain("letter.docx");
    expect(item.textContent).toContain("notes.md");
    expect(item.textContent?.toLowerCase()).toContain("completed");
    expect(within(item).getByText(/Open/).closest("a")?.getAttribute("href")).toBe("/sessions/s-done");
  });

  it("New task offers ONLY the team (no remote, no coordinator) and queues for a member", async () => {
    at("?project=p1");
    H.postResults["/projects/p1/task"] = {
      queued: true,
      assignment: { id: "a1", assignee: "builder" },
    };
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("world-tab-new"));
    const select = (await screen.findByTestId("project-task-assignee")) as HTMLSelectElement;
    const options = Array.from(select.options).map((o) => [o.value, o.textContent]);
    expect(options).toEqual([
      ["", "Whole team — Jarvis decides"],
      ["builder", "builder"],
      ["custom:analyst", "analyst — yours"],
    ]);
    fireEvent.change(screen.getByLabelText("Task for an agent in this project"), {
      target: { value: "Reconcile March" },
    });
    fireEvent.change(select, { target: { value: "builder" } });
    fireEvent.click(screen.getByRole("button", { name: /Queue for builder/ }));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/projects/p1/task")?.body).toMatchObject({
        text: "Reconcile March",
        assignee: "builder",
      }),
    );
    await screen.findByTestId("project-task-queued");
  });

  it("'Whole team — Jarvis decides' is the plain project task: NO assignee on the wire", async () => {
    at("?project=p1");
    H.postResults["/projects/p1/task/plan"] = { tools: [] };
    H.postResults["/projects/p1/task"] = { id: "s-new", status: "active", output: "chat" };
    H.api["/sessions/s-new"] = { session: { id: "s-new", status: "active" }, transcript: [] };
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("world-tab-new"));
    await screen.findByTestId("project-task-assignee");
    fireEvent.change(screen.getByLabelText("Task for an agent in this project"), {
      target: { value: "Summarise every PDF" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Run/ }));
    await waitFor(() => expect(H.posts.some((p) => p.path === "/projects/p1/task")).toBe(true));
    const body = H.posts.find((p) => p.path === "/projects/p1/task")!.body!;
    expect(body.text).toBe("Summarise every PDF");
    expect("assignee" in body).toBe(false);
    expect(H.posts.map((p) => p.path)).toContain("/projects/p1/task/plan");
  });
});

/* -------------------------------------------------------------- team --- */

describe("the team", () => {
  it("an empty team shows the build step — no table, no room — with suggestions and why", async () => {
    at("?project=p2");
    render(<AgentsPage />);
    const editor = await screen.findByTestId("team-editor");
    expect(editor.getAttribute("data-mode")).toBe("build");
    expect(screen.queryByTestId("round-table")).toBeNull();
    expect(H.posts.some((p) => p.path.endsWith("/world/room"))).toBe(false);
    const sug = screen.getByTestId("team-suggestion-researcher");
    expect(sug.textContent).toContain("worked on 3 tasks here in the last 30 days");
    // the tabs still stand beside it
    expect(screen.getByTestId("world-tabs")).toBeTruthy();
  });

  it("add a suggestion and a roster agent, remove one, seat the team; the room is made ONCE", async () => {
    at("?project=p2");
    H.putResults["/projects/p2/team"] = {
      team: [
        { name: "researcher", kind: "builtin" },
        { name: "custom:analyst", kind: "custom" },
      ],
      suggestions: [],
    };
    H.postResults["/projects/p2/world/room"] = { thread_id: "t-new" };
    render(<AgentsPage />);
    await screen.findByTestId("team-editor");
    expect((screen.getByTestId("team-save") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByTestId("team-add-researcher"));
    fireEvent.click(screen.getByTestId("team-add-custom:analyst"));
    fireEvent.click(screen.getByTestId("team-add-builder"));
    await screen.findByTestId("team-member-builder");
    fireEvent.click(screen.getByTestId("team-remove-builder"));
    await waitFor(() => expect(screen.queryByTestId("team-member-builder")).toBeNull());
    // a suggestion that joined the team leaves the suggestion list
    expect(screen.queryByTestId("team-suggestion-researcher")).toBeNull();

    fireEvent.click(screen.getByTestId("team-save"));
    await waitFor(() =>
      expect(H.puts.find((p) => p.path === "/projects/p2/team")?.body).toEqual({
        members: ["researcher", "custom:analyst"],
      }),
    );
    // seated: the lazy room is made, once, and the table opens on it
    const table = await screen.findByTestId("round-table");
    expect(table.getAttribute("data-thread")).toBe("t-new");
    expect(table.getAttribute("data-project")).toBe("p2");
    expect(H.posts.filter((p) => p.path === "/projects/p2/world/room")).toHaveLength(1);
  });

  it("a world WITH a team but no room makes one lazily, exactly once", async () => {
    at("?project=p1");
    H.api["/projects/p1/world"] = { ...P1_WORLD, thread_id: null };
    H.postResults["/projects/p1/world/room"] = { thread_id: "t-lazy", thread: { id: "t-lazy" } };
    const { rerender } = render(<AgentsPage />);
    const table = await screen.findByTestId("round-table");
    expect(table.getAttribute("data-thread")).toBe("t-lazy");
    rerender(<AgentsPage />);
    rerender(<AgentsPage />);
    expect(H.posts.filter((p) => p.path === "/projects/p1/world/room")).toHaveLength(1);
  });

  it("a failed room is NOT re-posted by the next poll; Try again is the door", async () => {
    at("?project=p1");
    H.api["/projects/p1/world"] = { ...P1_WORLD, thread_id: null };
    H.postErrors["/projects/p1/world/room"] = "The room could not be made.";
    const { rerender } = render(<AgentsPage />);
    await screen.findByText("The room could not be made.");
    // a poll: a NEW world object, still no room
    H.api["/projects/p1/world"] = { ...P1_WORLD, thread_id: null };
    rerender(<AgentsPage />);
    H.api["/projects/p1/world"] = { ...P1_WORLD, thread_id: null };
    rerender(<AgentsPage />);
    expect(H.posts.filter((p) => p.path === "/projects/p1/world/room")).toHaveLength(1);
    delete H.postErrors["/projects/p1/world/room"];
    H.postResults["/projects/p1/world/room"] = { thread_id: "t-retry" };
    fireEvent.click(screen.getByTestId("world-room-retry"));
    await waitFor(() =>
      expect(screen.getByTestId("round-table").getAttribute("data-thread")).toBe("t-retry"),
    );
    expect(H.posts.filter((p) => p.path === "/projects/p1/world/room")).toHaveLength(2);
  });

  it("leaving and re-entering before the poll catches up does not make a second room", async () => {
    H.api["/projects/p1/world"] = { ...P1_WORLD, thread_id: null };
    H.postResults["/projects/p1/world/room"] = { thread_id: "t-once" };
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("world-card-p1"));
    await waitFor(() =>
      expect(screen.getByTestId("round-table").getAttribute("data-thread")).toBe("t-once"),
    );
    fireEvent.click(screen.getByTestId("world-back"));
    await screen.findByTestId("worlds-grid");
    fireEvent.click(screen.getByTestId("world-card-p1"));
    await waitFor(() =>
      expect(screen.getByTestId("round-table").getAttribute("data-thread")).toBe("t-once"),
    );
    expect(H.posts.filter((p) => p.path === "/projects/p1/world/room")).toHaveLength(1);
  });

  it("Edit team: the PUT is the ONE writer of the panel; the table reloads on the room it names", async () => {
    at("?project=p1");
    H.putResults["/projects/p1/team"] = {
      team: [{ name: "builder", kind: "builtin" }, { name: "researcher", kind: "builtin" }],
      suggestions: [],
      thread_id: "t-p1",
    };
    render(<AgentsPage />);
    const before = (await screen.findByTestId("round-table")).getAttribute("data-nonce");
    fireEvent.click(await screen.findByTestId("world-edit-team"));
    const editor = await screen.findByTestId("team-editor");
    expect(editor.getAttribute("data-mode")).toBe("edit");
    // saving re-seats the table, said plainly before the press
    expect(screen.getByTestId("team-reseat-note").textContent).toMatch(
      /re-seats the round table[\s\S]*not on the team is removed/,
    );
    // a remote member: what it sees, in the daemon's words or ours
    expect(screen.getByTestId("team-sees-remote:hermes").textContent).toContain(
      "Remote agents see only what you type here — never the project's files or the other agents' replies",
    );
    fireEvent.click(screen.getByTestId("team-remove-custom:analyst"));
    fireEvent.click(screen.getByTestId("team-add-researcher"));
    fireEvent.click(screen.getByTestId("team-save"));
    await waitFor(() => expect(screen.queryByTestId("team-editor")).toBeNull());
    expect(H.puts.find((p) => p.path === "/projects/p1/team")?.body).toEqual({
      members: ["builder", "remote:hermes", "supervisor", "researcher"],
    });
    // the daemon re-seated the room: the UI writes no panel and reads no thread
    expect(H.puts.some((p) => p.path.includes("/participants"))).toBe(false);
    expect(H.gets.some((g) => g.startsWith("/agents/threads/"))).toBe(false);
    // ...and the table re-reads the room it was re-seated into
    await waitFor(() =>
      expect(screen.getByTestId("round-table").getAttribute("data-nonce")).not.toBe(before),
    );
    expect(screen.getByTestId("round-table").getAttribute("data-thread")).toBe("t-p1");
  });

  it("a remote member's `sees` from the daemon is shown as sent", async () => {
    at("?project=p1");
    H.api["/projects/p1/team"] = {
      team: [{ name: "remote:hermes", kind: "remote", sees: "Sees only the messages addressed to it" }],
      suggestions: [],
    };
    render(<AgentsPage />);
    fireEvent.click(await screen.findByTestId("world-edit-team"));
    const note = await screen.findByTestId("team-sees-remote:hermes");
    expect(note.textContent).toContain("Sees only the messages addressed to it");
  });

  it("a team saved before any room adopts the room the PUT names (no second POST)", async () => {
    at("?project=p2");
    H.putResults["/projects/p2/team"] = {
      team: [{ name: "researcher", kind: "builtin" }],
      suggestions: [],
      thread_id: "t-from-put",
    };
    render(<AgentsPage />);
    await screen.findByTestId("team-editor");
    fireEvent.click(screen.getByTestId("team-add-researcher"));
    fireEvent.click(screen.getByTestId("team-save"));
    await waitFor(() =>
      expect(screen.getByTestId("round-table").getAttribute("data-thread")).toBe("t-from-put"),
    );
    expect(H.posts.some((p) => p.path.endsWith("/world/room"))).toBe(false);
  });
});

/* ------------------------------------------------- older daemon + links --- */

describe("an older daemon and the old deep links", () => {
  it("404 on /agents/worlds is today's page exactly — even on a ?project= URL", async () => {
    H.errors["/agents/worlds"] = { status: 404, message: "Not Found" };
    delete H.api["/agents/worlds"];
    at("?project=p1");
    render(<AgentsPage />);
    await screen.findByTestId("agents-room");
    expect(screen.queryByTestId("world-view")).toBeNull();
    expect(screen.queryByTestId("worlds-grid")).toBeNull();
    expect(screen.queryByTestId("general-world-nav")).toBeNull();
  });

  it("?talk=guide&ask=… still opens General (not the grid) and prefills the composer", async () => {
    H.postResults["/agents/threads"] = {
      id: "r-guide",
      title: "Talk with guide",
      participants: [{ key: "builtin:guide", source: "builtin", name: "guide", role: "" }],
      message_count: 0,
      updated_at: "2026-10-06T00:00:00Z",
    };
    at("?talk=guide&ask=How%20do%20updates%20install");
    render(<AgentsPage />);
    await screen.findByTestId("agents-room");
    expect(screen.queryByTestId("worlds-grid")).toBeNull();
    await waitFor(() =>
      expect(screen.getByTestId("round-table").getAttribute("data-thread")).toBe("r-guide"),
    );
    expect(screen.getByTestId("round-table").getAttribute("data-initial")).toBe(
      "How do updates install",
    );
    expect(H.posts.find((p) => p.path === "/agents/threads")?.body).toMatchObject({
      title: "Talk with guide",
    });
    // there are worlds, so General offers the way back
    expect(screen.getByTestId("general-world-nav")).toBeTruthy();
  });

  it("?thread=<id> of a PROJECT room opens that project's world on that room — never General", async () => {
    H.api["/agents/threads/t-old"] = { id: "t-old", project_id: "p1", participants: [] };
    at("?thread=t-old");
    render(<AgentsPage />);
    const view = await screen.findByTestId("world-view");
    expect(view.getAttribute("data-project")).toBe("p1");
    expect(screen.getByTestId("round-table").getAttribute("data-thread")).toBe("t-old");
    expect(window.location.search).toBe("?project=p1&thread=t-old");
    expect(screen.queryByTestId("agents-room")).toBeNull();
    // General never mounted on the way (its room list was never polled)
    expect(H.requested).not.toContain("/agents/threads");
    // the pinned room is opened, no room is made
    expect(H.posts.some((p) => p.path.endsWith("/world/room"))).toBe(false);
  });

  it("an unknown room id (404) still opens General, which says so itself", async () => {
    H.getErrors["/agents/threads/t-gone"] = 404;
    at("?thread=t-gone");
    render(<AgentsPage />);
    await waitFor(() =>
      expect(screen.getByTestId("round-table").getAttribute("data-thread")).toBe("t-gone"),
    );
    expect(screen.getByTestId("agents-room")).toBeTruthy();
  });

  it("every producer of a room link speaks /agents?thread= (the page is the one choke point)", async () => {
    const { readFileSync } = await import("node:fs");
    const read = (f: string) => readFileSync(`${process.cwd()}/${f}`, "utf8").replace(/\r\n/g, "\n");
    expect(read("components/CommandPalette.tsx")).toContain('if (kind === "round") return `/agents?thread=${id}`;');
    expect(read("app/chat/page.tsx")).toContain("href={`/agents?thread=${m.panelThreadId}`}");
  });

  it("?thread=<id> still opens that General room", async () => {
    at("?thread=g-older");
    render(<AgentsPage />);
    await waitFor(() =>
      expect(screen.getByTestId("round-table").getAttribute("data-thread")).toBe("g-older"),
    );
    expect(screen.queryByTestId("worlds-grid")).toBeNull();
  });
});

/* ------------------------------------------------------------ helpers --- */

describe("lib/agentWorlds", () => {
  it("routes: project wins, General deep links stay General, nothing = auto", () => {
    expect(parseWorldRoute("?project=p9")).toEqual({ kind: "project", id: "p9" });
    expect(parseWorldRoute("?world=general")).toEqual({ kind: "general" });
    expect(parseWorldRoute("?talk=guide")).toEqual({ kind: "general" });
    expect(parseWorldRoute("?ask=hi")).toEqual({ kind: "general" });
    expect(parseWorldRoute("?thread=t1")).toEqual({ kind: "general", thread: "t1" });
    expect(parseWorldRoute("?project=p1&thread=t1")).toEqual({ kind: "project", id: "p1", thread: "t1" });
    expect(worldPath({ kind: "project", id: "p1", thread: "t 1" })).toBe("/agents?project=p1&thread=t%201");
    expect(worldPath({ kind: "general", thread: "t1" })).toBe("/agents?thread=t1");
    expect(parseWorldRoute("?focus=add")).toEqual({ kind: "auto" });
    expect(worldPath({ kind: "project", id: "a b" })).toBe("/agents?project=a%20b");
    expect(worldPath({ kind: "auto" })).toBe("/agents");
  });

  it("team choices drop remotes and coordinators and name custom agents by wire name", () => {
    expect(
      teamAssigneeChoices([
        { name: "builder" },
        { name: "planner" },
        { name: "supervisor" },
        { name: "remote:x" },
        { name: "analyst", kind: "custom" },
        { name: "builder" },
      ]),
    ).toEqual([
      { value: "builder", label: "builder" },
      { value: "custom:analyst", label: "analyst — yours" },
    ]);
  });

  it("the daemon's kind 'custom' is a custom agent everywhere", () => {
    expect(memberSource({ name: "analyst", kind: "custom" })).toBe("dynamic");
    expect(memberKey({ name: "analyst", kind: "custom" })).toBe("dynamic:analyst");
    expect(memberSource({ name: "custom:analyst" })).toBe("dynamic");
    expect(memberSource({ name: "remote:x", kind: "remote" })).toBe("remote");
  });

  it("links stay inside the app; counts are normalised; the room id is read from any shape", () => {
    expect(waitingHref({ id: "1", kind: "ask", title: "", link: "//evil.example" }, "p1")).toBe("/projects/p1");
    expect(waitingHref({ id: "1", kind: "ask", title: "", link: "https://x.example" }, "p1")).toBe("/projects/p1");
    expect(waitingHref({ id: "1", kind: "ask", title: "", link: "/sessions/s" }, "p1")).toBe("/sessions/s");
    expect(normaliseCounts({ waiting: -2, running: 1.7 } as never)).toEqual({ waiting: 0, running: 1, queued: 0, done_7d: 0 });
    expect(countsLine(normaliseCounts({ queued: 2 }))).toBe("2 queued");
    expect(roomIdOf({ thread_id: "a" })).toBe("a");
    expect(roomIdOf({ thread: { id: "b" } })).toBe("b");
    expect(roomIdOf(null)).toBeNull();
  });
});
