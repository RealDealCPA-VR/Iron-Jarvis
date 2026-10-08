/**
 * v1.315.0 — UX wave 3, track T2 agents & sessions: the Agents page itself.
 *
 * Findings pinned here (scratchpad ux_wave3.json, fix_adjustment wins):
 *  - one-ai-identity-split: the front door's Team panel opens on JARVIS — a
 *    coordinator header (Jarvis's face + name) that sits OUTSIDE the roster
 *    list, never the word "supervisor" (agents-mission-v1307 asserts that);
 *    platform-internal teammates (memory, maintainer) are SORTED LAST, not
 *    collapsed (no extra press); descriptions wrap to two lines instead of
 *    truncating; ids keep their text and are title-cased by CSS `capitalize`
 *    (rail rows and the agent's own name).
 *  - mission-rail-second-nav-on-phone: below lg only the two page-local rows
 *    (New task, Agents) show; Chat / Projects / Tools / Files / Settings are
 *    hidden by CSS (`hidden lg:flex`) — still RENDERED (v1307 pins all seven
 *    labels and New task's aria-current).
 *  - team-screen-appearance-before-work: on an agent's screen the work (the
 *    Inbox, and a custom agent's folder + coach) comes BEFORE the Appearance
 *    block; the hero portrait shrinks to ~96px; "Apply face" is quiet
 *    (btn-ghost) until the face has actually changed — still one press.
 *
 * Anti-vacuity: every teammate still listed, every rail row still rendered
 * and still pressable, the face picker and portrait still on the screen, and
 * Apply still writes the face in one press.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const W3M = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: unknown }>,
  puts: [] as Array<{ path: string; body: unknown }>,
  gets: [] as string[],
}));

function apiState(path: string | null) {
  return { data: path ? (W3M.api[path] ?? null) : null, error: null, loading: false, reload: () => {} };
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
      W3M.gets.push(path);
      return Promise.resolve(W3M.api[path] ?? {});
    },
    post: (path: string, body?: unknown) => {
      W3M.posts.push({ path, body });
      return Promise.resolve({});
    },
    put: (path: string, body?: unknown) => {
      W3M.puts.push({ path, body });
      return Promise.resolve({});
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
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "whileHover", "layout"]);
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
  appLabelFor: () => "app",
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));
// A face is a span that says whose face it is (the agents-mission-v1307 shape).
vi.mock("@/components/agents/AgentFace", async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  default: (p: { name: string }) => <span data-testid="face" data-name={p.name} />,
}));
vi.mock("@/components/kanban/KanbanBoard", () => ({ KanbanBoard: () => null }));

import { MissionScreen } from "@/components/agents/mission/MissionScreen";
import { AGENT_HERO_PX, AgentDetail } from "@/components/agents/AgentsModal";
import { RosterStrip, type RosterEntry } from "@/components/agents/RosterStrip";

const classes = (el: Element | null | undefined) => (el?.getAttribute("class") ?? "").split(/\s+/);

/** The first non-blank text a reader meets inside `root`. */
function firstText(root: Element): string {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = (n.textContent ?? "").trim();
    if (t) return t;
  }
  return "";
}

beforeEach(() => {
  W3M.api = {
    "/agents/roster": {
      roster: [
        { name: "memory", kind: "builtin", description: "curates and tidies the platform's stored knowledge", delegable: true, healthy: true },
        { name: "builder", kind: "builtin", description: "builds things end to end", delegable: true, healthy: true },
        { name: "maintainer", kind: "builtin", description: "carefully edits and tests Iron Jarvis's own code", delegable: true, healthy: true },
        {
          name: "researcher",
          kind: "builtin",
          description: "wires up schedules, webhooks, workflows, and integrations so the right thing happens on its own",
          delegable: true,
          healthy: true,
        },
        { name: "supervisor", kind: "builtin", description: "coordinates", delegable: false, healthy: true },
      ],
    },
    "/projects": { projects: [{ id: "p1", name: "Acme", status: "active" }] },
    "/missions": { missions: [] },
    "/agents/worlds": { worlds: [], general: { thread_count: 0 } },
  };
  W3M.posts = [];
  W3M.puts = [];
  W3M.gets = [];
});
afterEach(cleanup);

function frontDoor(over: Partial<React.ComponentProps<typeof MissionScreen>> = {}) {
  const props = {
    missionId: "",
    onOpen: vi.fn(),
    onNew: vi.fn(),
    onTeam: vi.fn(),
    onProject: vi.fn(),
    ...over,
  };
  render(<MissionScreen {...props} />);
  return props;
}

/* ========================================================================== */
/*  The front door's Team panel opens on Jarvis                                */
/* ========================================================================== */

describe("front door Team panel: Jarvis leads, the teammates follow", () => {
  it("a Jarvis header (face + name) sits ABOVE and OUTSIDE the roster list", () => {
    frontDoor();
    const team = screen.getByTestId("mission-available-team");
    const head = within(team).getByTestId("mission-available-jarvis");
    expect(head.textContent ?? "").toContain("Jarvis");
    expect(head.querySelector('[data-testid="face"][data-name="jarvis"]')).not.toBeNull();
    // a header, not a roster row
    expect(head.closest("li")).toBeNull();
    expect(head.closest("ul")).toBeNull();
    const list = team.querySelector("ul") as HTMLElement;
    expect(head.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // never the internal word (agents-mission-v1307 pins this too)
    expect(team.textContent ?? "").not.toMatch(/supervisor/i);
  });

  it("platform-internal teammates (memory, maintainer) are sorted LAST, not hidden", () => {
    frontDoor();
    const team = screen.getByTestId("mission-available-team");
    // each row's first words are its name (the description follows it)
    const names = Array.from(team.querySelectorAll("ul > li")).map((li) => firstText(li));
    expect(names).toEqual(["builder", "researcher", "memory", "maintainer"]);
    // no disclosure was added: nothing in the panel is collapsed
    expect(team.querySelector('[aria-expanded="false"]')).toBeNull();
  });

  it("descriptions wrap to two lines instead of being cut off mid-word", () => {
    frontDoor();
    const team = screen.getByTestId("mission-available-team");
    const desc = within(team).getByText(/wires up schedules, webhooks/);
    expect(classes(desc)).toContain("line-clamp-2");
    expect(classes(desc)).not.toContain("truncate");
  });

  it("anti-vacuity: every delegable teammate is still listed, by its id, title-cased by CSS", () => {
    frontDoor();
    const team = screen.getByTestId("mission-available-team");
    for (const n of ["builder", "researcher", "memory", "maintainer"]) {
      const el = within(team).getByText(n);
      expect(classes(el)).toContain("capitalize");
    }
    expect(within(team).getAllByRole("listitem")).toHaveLength(4);
  });
});

/* ========================================================================== */
/*  The mission rail is not a second nav on a phone                            */
/* ========================================================================== */

describe("mission rail: below lg only New task and Agents show", () => {
  const LINK_ROWS = ["chat", "projects", "tools", "files", "settings"];

  it("the app-wide destinations are hidden below lg by CSS (hidden lg:flex)", () => {
    frontDoor();
    for (const key of LINK_ROWS) {
      const row = screen.getByTestId(`mission-rail-${key}`);
      const cls = classes(row);
      expect(cls, key).toContain("hidden");
      expect(cls, key).toContain("lg:flex");
    }
  });

  it("New task and Agents are never hidden at any width", () => {
    frontDoor();
    for (const key of ["new", "agents"]) {
      expect(classes(screen.getByTestId(`mission-rail-${key}`)), key).not.toContain("hidden");
    }
  });

  it("anti-vacuity: all seven rows are still rendered, New task is current, Agents still opens the team", () => {
    const props = frontDoor();
    const rail = screen.getByTestId("mission-rail");
    for (const label of ["New task", "Chat", "Projects", "Agents", "Tools", "Files", "Settings"]) {
      expect(within(rail).getByText(label)).toBeTruthy();
    }
    expect(screen.getByTestId("mission-rail-new").getAttribute("aria-current")).toBe("page");
    expect(screen.getByTestId("mission-rail-chat").getAttribute("href")).toBe("/chat");
    fireEvent.click(screen.getByTestId("mission-rail-agents"));
    expect(props.onTeam).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("mission-rail-new"));
    expect(props.onNew).toHaveBeenCalledTimes(1);
  });
});

/* ========================================================================== */
/*  An agent's screen leads with the work                                      */
/* ========================================================================== */

const BUILDER: RosterEntry = {
  name: "builder",
  kind: "builtin",
  description: "builds things end to end",
  delegable: true,
  healthy: true,
  stats: null,
};
const ANALYST: RosterEntry = {
  name: "custom:analyst",
  kind: "dynamic",
  description: "numbers",
  delegable: true,
  healthy: true,
  stats: null,
};
const EMPTY_INBOX = { queued: [], claimed: [], running: [], blocked: [], recent: [] };

function detail(entry: RosterEntry) {
  return render(
    <AgentDetail
      entry={entry}
      faces={{}}
      facesSupported
      onAgentsChanged={() => {}}
      onFaceChanged={() => {}}
    />,
  );
}

function follows(a: Element, b: Element): boolean {
  return Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

describe("agent screen: the work first, appearance later", () => {
  it("a built-in agent: the Inbox comes before the portrait and the face picker", () => {
    W3M.api["/agents/builder/inbox"] = { assignee: "builder", inbox: EMPTY_INBOX, health: null };
    detail(BUILDER);
    const inbox = screen.getByTestId("agent-inbox-builder");
    expect(follows(inbox, screen.getByTestId("avatar-row-builder"))).toBe(true);
    expect(follows(inbox, screen.getByTestId("face-picker-builder"))).toBe(true);
  });

  it("a custom agent: Inbox, folder and coach all come before the face picker", () => {
    W3M.api["/agents/custom%3Aanalyst/inbox"] = { assignee: "custom:analyst", inbox: EMPTY_INBOX, health: null };
    W3M.api["/agents/analyst/files"] = {
      name: "analyst",
      instructions: "# Analyst\nCite sources.",
      notes: "",
      revisions: [],
      folder: "C:\\ij\\agents\\analyst",
    };
    W3M.api["/agents/analyst/coach"] = {
      report: { agent: "analyst", runs: [], clusters: [] },
      proposals: [],
      last_reason: "Not enough runs yet.",
    };
    detail(ANALYST);
    const picker = screen.getByTestId("face-picker-analyst");
    for (const id of ["agent-inbox-custom:analyst", "files-analyst", "coach-analyst"]) {
      const el = screen.queryByTestId(id);
      expect(el, id).not.toBeNull();
      expect(follows(el!, picker), id).toBe(true);
    }
  });

  it("the hero portrait is about 96px, not 160", () => {
    expect(AGENT_HERO_PX).toBeLessThanOrEqual(112);
    expect(AGENT_HERO_PX).toBeGreaterThanOrEqual(80);
  });

  it("the agent's own name is title-cased by CSS (text stays the id)", () => {
    W3M.api["/agents/builder/inbox"] = { assignee: "builder", inbox: EMPTY_INBOX, health: null };
    detail(BUILDER);
    const own = within(screen.getByTestId("agent-detail-builder"))
      .getAllByText("builder")
      .filter((el) => el.tagName.toLowerCase() === "span");
    expect(own.some((el) => classes(el).includes("capitalize"))).toBe(true);
  });

  it("'Apply face' is quiet until the face changes, then accent — and one press still saves it", async () => {
    W3M.api["/agents/builder/inbox"] = { assignee: "builder", inbox: EMPTY_INBOX, health: null };
    detail(BUILDER);
    const picker = screen.getByTestId("face-picker-builder");
    const apply = () => within(picker).getByRole("button", { name: "Apply face" });
    expect(classes(apply())).not.toContain("btn-accent");
    // still pressable — quiet is not disabled (Apply with nothing chosen resets)
    expect((apply() as HTMLButtonElement).disabled).toBe(false);
    // The swatches are role="radio" <button>s (FaceRow's radiogroup).
    const shapes = within(screen.getByTestId("face-row-shape-builder")).getAllByRole("radio");
    fireEvent.click(shapes[shapes.length - 1]);
    expect(classes(apply())).toContain("btn-accent");
    fireEvent.click(apply());
    await waitFor(() => expect(W3M.puts.some((p) => p.path === "/agents/builder/face")).toBe(true));
  });

  it("anti-vacuity: the portrait controls, the face picker and Reset are all still on the screen", () => {
    W3M.api["/agents/builder/inbox"] = { assignee: "builder", inbox: EMPTY_INBOX, health: null };
    detail(BUILDER);
    expect(screen.getByTestId("avatar-row-builder")).toBeInTheDocument();
    const picker = screen.getByTestId("face-picker-builder");
    expect(within(picker).getByRole("button", { name: /Reset/ })).toBeInTheDocument();
    expect(screen.getByTestId("agent-hero")).toBeInTheDocument();
  });
});

describe("Your team's roster rail: ids title-cased by CSS", () => {
  it("each rail row's name keeps its id as text and carries `capitalize`", () => {
    render(<RosterStrip entries={[BUILDER]} onSelect={() => {}} selected={null} />);
    const rail = screen.getByTestId("roster-rail");
    const name = within(rail)
      .getAllByText("builder")
      .find((el) => el.tagName.toLowerCase() === "span");
    expect(name).toBeTruthy();
    expect(classes(name)).toContain("capitalize");
  });
});
