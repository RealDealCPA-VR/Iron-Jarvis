/**
 * v1.309.0 wave 1, track D — every place OUTSIDE the mission screen leads to
 * the right place, and a finished mission tells the user.
 *
 * Since v1.308.0 bare `/agents` is the New-task composer: Your team is
 * `?view=team` (an agent deep link is `?view=team&agent=<name>`, contract 8)
 * and a mission is `?mission=<id>` (`&project=<pid>` when it has one,
 * contract 9). What is pinned here, each on the REAL component:
 *
 *  1. the bell's agent rows (paused / allowance / blocked / coach / failed
 *     assignment / requeued) open THAT agent on Your team, not the composer;
 *  2. a finished mission (session.completed with payload.origin
 *     "job:mission", contract 2) is a bell row "Your objective is done" that
 *     opens the mission — and it pings the desktop. A teammate
 *     ("job:mission-member"), a chat run and a user-stopped mission stay
 *     quiet (the anti-vacuity controls);
 *  3. the search catalogue stops describing Agents as the round table, and
 *     "objective" / "mission" find it;
 *  4. Continue on a job a restart cut off, when that job was a mission, lands
 *     on the continued mission's screen (a plain job still stays put);
 *  5. Overview's Recent sessions and a project's Activity open a mission on
 *     its mission screen, not the raw session page;
 *  6. a project's own page links to its team & objectives screen and names
 *     the team;
 *  7. chat's "open in Agents →" under an @-mention reply — a live
 *     conversation sent to a read-only "old round table" page — is gone (no
 *     prefilled-objective door exists to replace it with).
 *
 * Harness: ONE file, so every mock below is the union of what the rendered
 * surfaces need. `@/lib/api` is a PARTIAL mock (the real module, with the
 * network verbs replaced), and `useApi` is the REAL hook over it — its module
 * cache is reset before every test.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const W1 = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
  postResponses: {} as Record<string, unknown>,
  posts: [] as { path: string; body: unknown }[],
  gets: [] as string[],
  events: [] as Array<Record<string, unknown>>,
  notify: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
  search: "",
  pathname: "/",
}));

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    W1.gets.push(path);
    const r = W1.responses[path];
    // Unknown reads answer an empty object: these pages carry many optional
    // panels, and this file pins links, not their absence.
    return r === undefined ? {} : r;
  },
  post: async (path: string, body?: unknown) => {
    W1.posts.push({ path, body });
    const r = W1.postResponses[path];
    if (typeof r === "function") return (r as (b: unknown) => unknown)(body);
    return r ?? {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: W1.events, connected: true }),
}));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: W1.notify,
  }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: W1.push,
    replace: W1.replace,
    refresh: () => {},
    prefetch: () => {},
    back: () => {},
  }),
  useSearchParams: () => new URLSearchParams(W1.search),
  usePathname: () => W1.pathname,
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, prefetch: _p, ...rest }: Record<string, unknown>) =>
      createElement("a", { href, ...rest }, children as never),
  };
});
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial",
    "animate",
    "exit",
    "transition",
    "variants",
    "layout",
    "layoutId",
    "whileHover",
    "whileTap",
    "whileFocus",
    "whileInView",
    "viewport",
    "drag",
  ]);
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
    LazyMotion: ({ children }: { children?: unknown }) => createElement(Fragment, null, children as never),
    domAnimation: {},
    domMax: {},
    useReducedMotion: () => true,
    motion: new Proxy({} as Record<string, unknown>, { get: (_t, tag) => tagFor(String(tag)) }),
  };
});
// Chat page seams (the v1.284.0 chat harness): the stream lanes are scripted.
vi.mock("@/lib/useChatStream", () => {
  class StreamError extends Error {
    status = 500;
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
      run: async () => ({
        reply: "Noted.",
        route: { requested: "", provider: "mock", model: "mock", reason: "default" },
        tools_used: [],
        remembered: [],
      }),
      abort: () => {},
    }),
  };
});
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
    stop: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));
// Project page leaves that are not under test.
vi.mock("@/components/kanban/KanbanBoard", () => ({ KanbanBoard: () => null }));
vi.mock("@/components/project/KnowledgePanel", () => ({ KnowledgePanel: () => null }));

import { CheckCircle2 } from "lucide-react";
import { __resetApiCache } from "@/lib/apiCache";
import { NotificationBell, toActivity } from "@/components/NotificationBell";
import { CommandPalette } from "@/components/CommandPalette";
import {
  InterruptedJobRow,
  InterruptedJobsNote,
  INTERRUPTED_PATH,
  parseInterrupted,
} from "@/components/InterruptedJobs";
import { NAV_ENTRIES } from "@/lib/nav";
import type { IJEvent } from "@/lib/types";
import OverviewPage from "@/app/page";
import ProjectWorkspacePage from "@/app/projects/[id]/page";
import ChatPage from "@/app/chat/page";
import {
  OTHERS_GROUP,
  ProjectTasks,
  TEAM_GROUP,
  teamAssigneeChoices,
} from "@/components/project/ProjectTasks";

// ------------------------------------------------------------------ helpers

/** An href as {path, q} so a pin does not depend on query-param ORDER. */
function route(href: string | null | undefined): { path: string; q: Record<string, string> } {
  const u = new URL(href ?? "", "http://ij.test");
  return { path: u.pathname, q: Object.fromEntries(u.searchParams) };
}

const ev = (
  type: string,
  payload: Record<string, unknown>,
  id = "e1",
  session_id: string | null = null,
): IJEvent => ({ id, type, ts: "2026-10-07T10:00:00", session_id, payload }) as IJEvent;

const done = (
  origin: string | null,
  over: Record<string, unknown> = {},
  id = "e-done",
  sid = "m1",
): IJEvent =>
  ev(
    "session.completed",
    // `outcome` rides every completion payload (fix round: the orchestrator's
    // `_completion_payload` sends Session.outcome), and "is done" needs it —
    // the default fixture is a mission that really finished its job.
    {
      status: "completed",
      outcome: "completed",
      summary: "Strategy report written.",
      origin,
      project_id: "p1",
      ...over,
    },
    id,
    sid,
  );

beforeEach(() => {
  __resetApiCache();
  W1.responses = {};
  W1.postResponses = {};
  W1.posts.length = 0;
  W1.gets.length = 0;
  W1.events.length = 0;
  W1.notify.mockReset();
  W1.push.mockReset();
  W1.replace.mockReset();
  W1.search = "";
  W1.pathname = "/";
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
});

// ============================================== 1. the bell's agent rows

describe("the bell's agent notifications open THAT agent on Your team (contract 8)", () => {
  // Every one of these payloads already names the agent (core/events.py).
  const cases: Array<[string, Record<string, unknown>, string]> = [
    ["agent.paused", { name: "tax-reader", reason: "monthly allowance used up" }, "tax-reader"],
    [
      "agent.allowance_warning",
      { name: "tax-reader", pct: 80, spent_tokens: 8000, allowance_tokens: 10000 },
      "tax-reader",
    ],
    ["coach.proposal", { id: "cp1", agent: "tax-reader", categories: [], rationale: "Shorter." }, "tax-reader"],
    [
      "assignment.blocked",
      { id: "a1", assignee: "bookkeeper", title: "Reconcile May", blocked_reason: "needs a login" },
      "bookkeeper",
    ],
    [
      // A failed job with no run behind it: the agent's inbox is the place.
      "assignment.finished",
      { id: "a2", assignee: "bookkeeper", title: "Reconcile June", ok: false, error: "boom" },
      "bookkeeper",
    ],
  ];
  for (const [type, payload, agent] of cases) {
    it(`${type} -> /agents?view=team&agent=${agent}`, () => {
      const item = toActivity(ev(type, payload));
      expect(item).not.toBeNull();
      const r = route(item!.href);
      expect(r.path).toBe("/agents");
      expect(r.q).toMatchObject({ view: "team", agent });
      expect(r.q.mission).toBeUndefined();
    });
  }

  it("assignment.requeued names no agent, so it opens Your team itself", () => {
    const item = toActivity(ev("assignment.requeued", { count: 2, ids: ["a", "b"] }));
    const r = route(item!.href);
    expect(r.path).toBe("/agents");
    expect(r.q.view).toBe("team");
  });

  it("control: a failed job WITH a run still opens that run", () => {
    const item = toActivity(
      ev("assignment.finished", { assignee: "bookkeeper", title: "x", ok: false, session_id: "s9" }),
    );
    expect(item!.href).toBe("/sessions/s9");
  });
});

// ================================== 2. a finished mission is announced

describe("a finished mission rings the bell and opens the mission (contracts 2 + 9)", () => {
  it("session.completed from a mission -> 'Your objective is done' -> /agents?mission=m1&project=p1", () => {
    const item = toActivity(done("job:mission"));
    expect(item).not.toBeNull();
    expect(item!.title).toBe("Your objective is done");
    const r = route(item!.href);
    expect(r.path).toBe("/agents");
    expect(r.q).toEqual({ mission: "m1", project: "p1" });
  });

  it("a mission with no project opens /agents?mission=<id> alone", () => {
    const item = toActivity(done("job:mission", { project_id: null }, "e2", "m2"));
    expect(route(item!.href)).toEqual({ path: "/agents", q: { mission: "m2" } });
  });

  it("a mission that FAILED still rings, but never says it is done", () => {
    const item = toActivity(done("job:mission", { status: "failed", ok: false }, "e3", "m3"));
    expect(item).not.toBeNull();
    expect(item!.title).not.toBe("Your objective is done");
    expect(item!.title).toMatch(/objective/i);
    expect(route(item!.href).q).toMatchObject({ mission: "m3" });
  });

  // Fix round (review): status=completed is the RUN ending cleanly; the
  // payload's `outcome` (Session.outcome) is the JOB's verdict. An ask that
  // timed out ("needs_you") or a mutating step that failed
  // ("completed_with_failures") must never read "is done" — the same false
  // "Task complete" headline Session.outcome exists to stop.
  it("a mission that finished with outcome needs_you says something needs you, not done", () => {
    const item = toActivity(done("job:mission", { outcome: "needs_you" }, "e8", "m8"));
    expect(item).not.toBeNull();
    expect(item!.title).not.toBe("Your objective is done");
    expect(item!.title).toMatch(/objective/i);
    expect(item!.title).toMatch(/needs you/i);
    expect(route(item!.href).q).toMatchObject({ mission: "m8" });
  });

  it("a mission that finished with outcome completed_with_failures says part of it failed", () => {
    const item = toActivity(done("job:mission", { outcome: "completed_with_failures" }, "e9", "m9"));
    expect(item).not.toBeNull();
    expect(item!.title).not.toBe("Your objective is done");
    expect(item!.title).toMatch(/objective/i);
    expect(item!.title).toMatch(/failed/i);
  });

  it("control: outcome completed says done, with the check", () => {
    const item = toActivity(done("job:mission", { outcome: "completed" }, "e10", "m10"));
    expect(item!.title).toBe("Your objective is done");
    expect(item!.icon).toBe(CheckCircle2);
  });

  // Fix round 2 (review, BLOCKING): a payload with NO outcome used to read
  // "is done" — and the orchestrator sent none, so a needs_you mission rang
  // done with a check. "Is done" needs positive proof; no outcome is neutral.
  it("no outcome on the payload reads a neutral 'finished', never 'is done'", () => {
    for (const outcome of [null, undefined]) {
      const item = toActivity(done("job:mission", { outcome }, "e11", "m11"));
      expect(item).not.toBeNull();
      expect(item!.title).toBe("Your objective finished");
      expect(item!.title).not.toMatch(/is done/i);
      // ...and no check mark either: the icon is part of the claim.
      expect(item!.icon).not.toBe(CheckCircle2);
      expect(route(item!.href).q).toMatchObject({ mission: "m11" });
    }
  });

  it("controls: a teammate, a chat run, an untagged run and a user-stopped mission stay quiet", () => {
    // A five-member mission must not buzz six times: teammates carry the
    // child origin (contract 1) and never ring.
    expect(toActivity(done("job:mission-member", {}, "e4", "c1"))).toBeNull();
    expect(toActivity(done("chat", {}, "e5", "s5"))).toBeNull();
    expect(toActivity(done(null, {}, "e6", "s6"))).toBeNull();
    // The user pressed Stop; a "could not finish" ping would be noise.
    expect(toActivity(done("job:mission", { status: "cancelled" }, "e7", "m7"))).toBeNull();
  });

  it("the real bell shows the row, links it to the mission, and pings the desktop", async () => {
    W1.events.push(done("job:mission") as unknown as Record<string, unknown>);
    render(<NotificationBell />);
    await waitFor(() =>
      expect(W1.notify).toHaveBeenCalledWith("Your objective is done", expect.any(String), expect.any(Function)),
    );
    fireEvent.click(screen.getByRole("button", { name: /notifications/i }));
    const title = await screen.findByText("Your objective is done");
    const link = title.closest("a");
    expect(link).not.toBeNull();
    expect(route(link!.getAttribute("href"))).toEqual({ path: "/agents", q: { mission: "m1", project: "p1" } });
  });

  it("control: a chat run finishing pings nothing (same harness)", async () => {
    W1.events.push(done("chat", {}, "e-chat", "s-chat") as unknown as Record<string, unknown>);
    render(<NotificationBell />);
    // Let the effects run, then prove the ping path stayed silent.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(W1.notify).not.toHaveBeenCalled();
  });
});

// ============================ 3. the search catalogue stops saying round table

describe("search finds Agents by what it is now", () => {
  const agents = () => NAV_ENTRIES.find((e) => e.href === "/agents");

  it("the Agents entry no longer promises a panel that talks it out", () => {
    const e = agents();
    expect(e).toBeDefined();
    expect(e!.blurb ?? "").not.toMatch(/talk it out|panel/i);
    expect(e!.aliases ?? []).not.toContain("panel");
    expect(e!.aliases ?? []).toEqual(expect.arrayContaining(["objective", "mission"]));
  });

  it("typing 'objective' in the real palette leads with Agents, and Enter opens /agents", async () => {
    render(<CommandPalette />);
    await act(async () => {
      window.dispatchEvent(new Event("ij:open-palette"));
    });
    const box = screen.getByRole("combobox") as HTMLInputElement;
    await act(async () => {
      fireEvent.change(box, { target: { value: "objective" } });
    });
    const first = screen.queryAllByRole("option")[0];
    expect(first?.textContent ?? "").toMatch(/^Agents/);
    await act(async () => {
      fireEvent.keyDown(box, { key: "Enter" });
    });
    expect(W1.push).toHaveBeenCalledWith("/agents");
  });
});

// ====================== 4. Continue on an interrupted mission lands on it

describe("Continue on a job a restart cut off", () => {
  // Fix round (review): a CONTINUED mission's `task` is the model-facing
  // recap; the row carries `objective`, the user's own words. Show those.
  it("the row names the user's objective, not the recap; an older daemon falls back to task", () => {
    expect(
      parseInterrupted({ id: "s1", task: "Recap: earlier you were asked to…", objective: "Draft the Q3 memo" })!.task,
    ).toBe("Draft the Q3 memo");
    expect(parseInterrupted({ id: "s2", task: "Plain job" })!.task).toBe("Plain job");
    expect(parseInterrupted({ id: "s3", task: "Plain job", objective: "  " })!.task).toBe("Plain job");
  });

  const JOB = { id: "sess_old", task: "Research the three competitors", agentType: "supervisor", interruptedAt: "" };

  // NAVIGATION SEAM: `useRouter().push` from next/navigation (mocked above).
  // NB for the implementer: interrupted-jobs-v1249 and the bell suites render
  // these components WITHOUT a next/navigation mock, and Next 15's useRouter
  // throws outside an App Router — guard the hook or add the mock there.
  it("a mission continues into its NEW mission screen", async () => {
    // The continuation inherits origin + project (orchestrator.continue_session)
    // and _session_row serves both, so the response alone says it is a mission.
    W1.postResponses["/sessions/sess_old/continue"] = {
      id: "sess_new",
      origin: "job:mission",
      project_id: "p1",
      status: "active",
    };
    render(
      <ul>
        <InterruptedJobRow job={JOB} onGone={() => {}} />
      </ul>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(W1.push).toHaveBeenCalled());
    expect(route(W1.push.mock.calls[0][0] as string)).toEqual({
      path: "/agents",
      q: { mission: "sess_new", project: "p1" },
    });
    expect(W1.posts[0].path).toBe("/sessions/sess_old/continue");
  });

  it("the Overview note does the same", async () => {
    W1.responses[INTERRUPTED_PATH] = {
      sessions: [{ id: "sess_old", task: "Research the three competitors", agent_type: "supervisor" }],
    };
    W1.postResponses["/sessions/sess_old/continue"] = { id: "sess_new2", origin: "job:mission", project_id: null };
    render(<InterruptedJobsNote intervalMs={60000} />);
    fireEvent.click(await screen.findByRole("button", { name: "Continue" }));
    await waitFor(() => expect(W1.push).toHaveBeenCalled());
    expect(route(W1.push.mock.calls[0][0] as string)).toEqual({ path: "/agents", q: { mission: "sess_new2" } });
  });

  it("control: a plain job continues in place (no navigation)", async () => {
    W1.postResponses["/sessions/sess_old/continue"] = { id: "sess_new3", origin: "chat", project_id: null };
    let gone = "";
    render(
      <ul>
        <InterruptedJobRow job={JOB} onGone={(id) => (gone = id)} />
      </ul>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(gone).toBe("sess_old"));
    expect(W1.push).not.toHaveBeenCalled();
  });
});

// ======================= 5. Overview + project Activity open a mission

const SESSION_BASE = {
  agent_type: "supervisor",
  provider: "mock",
  model: "mock",
  workspace_path: "",
  summary: "",
  created_at: "2026-10-07T09:00:00",
};
const MISSION_ROW = {
  ...SESSION_BASE,
  id: "m1",
  task: "Research our three competitors",
  status: "completed",
  origin: "job:mission",
  project_id: "p1",
};
const PLAIN_ROW = {
  ...SESSION_BASE,
  id: "s-plain",
  task: "Tidy the downloads folder",
  agent_type: "builder",
  status: "completed",
  origin: null,
  project_id: "p1",
};

function hrefsFor(text: string): string[] {
  return screen
    .getAllByText(text)
    .map((n) => n.closest("a")?.getAttribute("href"))
    .filter((h): h is string => typeof h === "string");
}

describe("Overview opens a mission on its mission screen", () => {
  it("Recent sessions: a mission row -> /agents?mission=m1&project=p1; a plain row -> /sessions/<id>", async () => {
    W1.responses = {
      "/sessions?limit=50": { sessions: [MISSION_ROW, PLAIN_ROW] },
      "/health": { status: "ok", version: "1.309.0", providers: [], default_provider: "mock" },
      "/metrics": { sessions_evaluated: 0, avg_completion: 0, avg_tool_success_rate: 0, avg_latency_s: 0, total_tool_invocations: 0, event_count: 0 },
      "/vault": { providers: [] },
      "/templates": { templates: [] },
      "/reflex/rules": { rules: [] },
    };
    // Recent sessions sits inside "Systems & admin", collapsed by default.
    window.localStorage.setItem("ij_ov_admin", "1");
    render(<OverviewPage />);
    await screen.findAllByText(MISSION_ROW.task, undefined, { timeout: 4000 });
    const mission = hrefsFor(MISSION_ROW.task);
    expect(mission.length).toBeGreaterThan(0);
    for (const h of mission) {
      expect(route(h)).toEqual({ path: "/agents", q: { mission: "m1", project: "p1" } });
    }
    // Anti-vacuity: the same list still sends an ordinary run to its page.
    const plain = hrefsFor(PLAIN_ROW.task);
    expect(plain.length).toBeGreaterThan(0);
    for (const h of plain) expect(h).toBe("/sessions/s-plain");
  });
});

// ============================ 6. a project's page leads to its team

const PROJECT = {
  project: {
    id: "p1",
    name: "Acme Tax 2026",
    brief: "client returns",
    root: "",
    status: "active",
    created_at: "2026-10-01T00:00:00",
  },
  sessions: [],
};

async function renderProject() {
  W1.pathname = "/projects/p1";
  // `use(params)` suspends once on the promise; settle it inside act.
  await act(async () => {
    render(<ProjectWorkspacePage params={Promise.resolve({ id: "p1" })} />);
  });
}

describe("a project's page links to its team & objectives screen", () => {
  it("the header links to /agents?project=p1 and names the team", async () => {
    W1.responses = {
      "/projects/p1": PROJECT,
      "/projects/p1/team": {
        project_id: "p1",
        members: ["researcher"],
        team: [{ name: "researcher", label: "Researcher", kind: "builtin" }],
        suggestions: [],
      },
    };
    await renderProject();
    await screen.findByText(/Open in Chat/);
    const link = await screen.findByTestId("project-team-link");
    expect(route(link.getAttribute("href"))).toEqual({ path: "/agents", q: { project: "p1" } });
    const said = `${link.textContent ?? ""} ${link.getAttribute("aria-label") ?? ""} ${link.getAttribute("title") ?? ""}`;
    expect(said).toMatch(/researcher/i);
  });

  it("with no team picked the link is still there (Jarvis picks from everyone)", async () => {
    W1.responses = {
      "/projects/p1": PROJECT,
      "/projects/p1/team": { project_id: "p1", members: [], team: [], suggestions: [] },
    };
    await renderProject();
    await screen.findByText(/Open in Chat/);
    const link = await screen.findByTestId("project-team-link");
    expect(route(link.getAttribute("href"))).toEqual({ path: "/agents", q: { project: "p1" } });
  });

  it("Activity opens a mission on the project's mission screen; a plain run on its page", async () => {
    W1.search = "tab=activity";
    W1.responses = {
      "/projects/p1": PROJECT,
      "/projects/p1/team": { project_id: "p1", members: [], team: [], suggestions: [] },
      "/sessions?project_id=p1": { sessions: [MISSION_ROW, PLAIN_ROW] },
    };
    await renderProject();
    await screen.findAllByText(MISSION_ROW.task, undefined, { timeout: 4000 });
    for (const h of hrefsFor(MISSION_ROW.task)) {
      expect(route(h)).toEqual({ path: "/agents", q: { mission: "m1", project: "p1" } });
    }
    const plain = hrefsFor(PLAIN_ROW.task);
    expect(plain.length).toBeGreaterThan(0);
    for (const h of plain) expect(h).toBe("/sessions/s-plain");
  });
});

// ============ 6b. the project's Tasks tab offers the project's TEAM first
//
// Fix round 2 (review): v1.308.0 enforces the team on every objective, but
// the project page's "Assign to" still listed every agent flat — no caller
// passed team choices after the world view was deleted. The verifier's
// adjustment: default to the team, list the others under a separator, do
// NOT refuse them (an assignment is a different door from a mission).

const AGENTS = {
  builtin: ["researcher", "builder", "supervisor", "planner"],
  dynamic: [{ name: "analyst" }, { name: "drafter" }],
};
const TEAM_ROWS = [
  { name: "researcher", label: "researcher" },
  { name: "custom:analyst", label: "analyst" },
  // Seats the queue cannot take: a remote, a coordinator, a deleted agent.
  { name: "remote:box", label: "box" },
  { name: "supervisor", label: "supervisor" },
  { name: "custom:ghost", label: "ghost", missing: true },
];

/** The select's options as [group label | "", value], in DOM order. */
function assignTo(): Array<[string, string]> {
  const sel = screen.getByTestId("project-task-assignee") as HTMLSelectElement;
  return Array.from(sel.querySelectorAll("option")).map((o) => [
    o.parentElement?.tagName === "OPTGROUP" ? (o.parentElement.getAttribute("label") ?? "") : "",
    o.value,
  ]);
}

describe("a project's Tasks tab offers its team first in 'Assign to'", () => {
  it("pure: team members first (assignable seats only), every other agent after, none dropped", () => {
    expect(teamAssigneeChoices(TEAM_ROWS, AGENTS as never)).toEqual([
      { value: "researcher", label: "researcher", group: TEAM_GROUP },
      { value: "custom:analyst", label: "analyst — yours", group: TEAM_GROUP },
      { value: "builder", label: "builder", group: OTHERS_GROUP },
      { value: "custom:drafter", label: "drafter — yours", group: OTHERS_GROUP },
    ]);
    // No team, or a team with no seat the queue can take: today's flat list.
    expect(teamAssigneeChoices([], AGENTS as never)).toBeNull();
    expect(teamAssigneeChoices(null, AGENTS as never)).toBeNull();
    expect(teamAssigneeChoices([{ name: "remote:box" }], AGENTS as never)).toBeNull();
  });

  it("the project page: the team is the first heading, the rest under 'Other agents', one team read", async () => {
    W1.responses = {
      "/projects/p1": PROJECT,
      "/projects/p1/team": { project_id: "p1", members: [], team: TEAM_ROWS, suggestions: [] },
      "/agents": AGENTS,
    };
    await renderProject();
    await waitFor(() =>
      expect(assignTo()).toEqual([
        ["", ""],
        [TEAM_GROUP, "researcher"],
        [TEAM_GROUP, "custom:analyst"],
        [OTHERS_GROUP, "builder"],
        [OTHERS_GROUP, "custom:drafter"],
      ]),
    );
    // The header already reads the team; the panel reuses that read.
    expect(W1.gets.filter((p) => p === "/projects/p1/team")).toHaveLength(1);
  });

  it("the panel on its own (chat's project surface) reads the team itself", async () => {
    W1.responses = {
      "/projects/p1/team": { project_id: "p1", members: [], team: TEAM_ROWS, suggestions: [] },
      "/agents": AGENTS,
    };
    render(<ProjectTasks projectId="p1" hasRoot={false} sessions={[]} />);
    await waitFor(() => expect(assignTo()[1]).toEqual([TEAM_GROUP, "researcher"]));
  });

  it("control: no team picked keeps today's flat list (no headings)", async () => {
    W1.responses = {
      "/projects/p1": PROJECT,
      "/projects/p1/team": { project_id: "p1", members: [], team: [], suggestions: [] },
      "/agents": AGENTS,
    };
    await renderProject();
    await waitFor(() =>
      expect(assignTo()).toEqual([
        ["", ""],
        ["", "researcher"],
        ["", "builder"],
        ["", "custom:analyst"],
        ["", "custom:drafter"],
      ]),
    );
  });
});

// ===================== 7. chat keeps an @-mention conversation in chat

const BUILDER = {
  mention: "builder",
  name: "builder",
  kind: "builtin",
  source: "builtin",
  description: "Builds things",
  healthy: true,
  delegable: true,
};

describe("chat: an @-mention reply no longer sends the user to a read-only page", () => {
  it("the reply renders with its agent chip, and nothing links to /agents?thread=", async () => {
    W1.pathname = "/chat";
    window.history.replaceState({}, "", "/chat");
    W1.responses = {
      "/models": { models: [] },
      "/chat/personas": { personas: [] },
      "/chat/threads": { threads: [] },
      "/settings": { settings: {} },
      "/projects": { projects: [] },
      "/agents/mentionable": { agents: [BUILDER] },
      "/skills": { skills: [] },
      "/workflows": { workflows: [] },
      "/tools": { tools: [] },
      "/undo?session_id=chat": { actions: [] },
      "/chat/approvals/pending": { approvals: [] },
    };
    W1.postResponses["/chat/panel"] = () => ({
      mode: "panel",
      thread_id: "athr1",
      entries: [
        { who: "user", content: "(echoed)", at: "2026-10-07T10:00:00Z" },
        { who: "builtin:builder", content: "Here is the brief.", at: "2026-10-07T10:00:03Z" },
      ],
      spoke: ["builtin:builder"],
      skipped: [],
      unknown_mentions: [],
      context: { chat_messages: 0, chat_dropped: 0 },
    });
    render(<ChatPage />);
    const box = () => screen.getByRole("textbox", { name: "Message" }) as HTMLTextAreaElement;
    await waitFor(() => expect(box()).toBeTruthy());
    // The "@" catalog is in hand when the picker lists builder.
    fireEvent.change(box(), { target: { value: "@bu" } });
    await screen.findByRole("option", { name: /builder/ });
    fireEvent.change(box(), { target: { value: "@builder draft the brief" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    const reply = await screen.findByText("Here is the brief.");
    // Control: the panel round really happened and is on screen.
    expect(W1.posts.some((p) => p.path === "/chat/panel")).toBe(true);
    expect(reply).toBeTruthy();
    const dead = Array.from(document.querySelectorAll("a")).filter((a) =>
      (a.getAttribute("href") ?? "").startsWith("/agents?thread="),
    );
    expect(dead).toEqual([]);
    expect(screen.queryByText(/open in Agents/i)).toBeNull();
  });
});

