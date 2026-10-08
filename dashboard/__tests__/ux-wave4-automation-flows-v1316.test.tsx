/**
 * v1.316.0 — UX wave 4, track T3 (automation flows): Autonomy and Workflows.
 *
 * WHAT THE USER SAW (fresh__autonomy / tall__autonomy / fresh__workflows):
 *  - Autonomy opened on a "Goals" card that says "Goals are born in Chat",
 *    while FOUR cards further down sat "Starter goals" with Add buttons, a
 *    "New goal" form and a separate "Standing goals" list — two things both
 *    called goals, with contradictory instructions — and the page's on/off
 *    state (the "Autonomy Enabled/Disabled" tile) was the THIRD block, under
 *    Goals and Standing grants. The red Kill switch had equal weight with
 *    "Enable autonomy" even while autonomy was off.
 *  - The New goal form's labels named nothing (no htmlFor/id).
 *  - Workflows opened on a viewport-tall node editor; the plain-language
 *    "describe a workflow" builder and the starters sat below it at every
 *    window size, and the name field looked like a heading, not a field.
 *
 * Verifier's adjustments (binding): the top card shows Chat-born CONTRACT
 * goals (/goals) and the form creates /autonomy/goals — different records, so
 * the words must never imply the form fills the top card; "Goals are born in
 * Chat" stays and the top card gets no create button (goals-surfaces-v1208).
 * The Kill switch stays enabled, labelled "Kill switch", with a rose hover.
 * The quick composer calls INTO the existing builder chat — never a second
 * generator path. An empty name never saves silently under a fallback.
 *
 * INTERFACE DECISIONS (the implementer follows these):
 *  - Top card title "Goals from Chat"; its empty line keeps "Goals are born
 *    in Chat" and points at "Simple goals below".
 *  - The /autonomy/goals list is titled "Simple goals" (NOT "Suggest-only
 *    goals": the per-goal dial offers "Act (low-risk)"/"Act (all)", so that
 *    title would be false) with one line saying how they differ from the
 *    goals made in Chat; "Simple goals", "New goal" and "Starter goals" are
 *    consecutive cards (one place for that kind of goal).
 *  - DOM order: the status tiles, then Proposals, then "Goals from Chat".
 *  - Workflows: with no saved workflows, a <form> ABOVE the canvas holds a
 *    textbox labelled "Describe a workflow" (label or aria-label) and one
 *    button per starter whose name contains the starter's title; submitting
 *    the form sends through WorkflowBuilderChat's own send (one POST
 *    /workflows/generate, the message lands in the "Build with chat" thread).
 *
 * Anti-vacuity: every control stays — Enable autonomy, Check now, Kill
 * switch / Release, Send briefing, Approve / Reject, the goal verbs, the
 * per-goal dial and status selects, Standing grants with Revoke, Add goal,
 * the starter Adds; Templates (Load into editor), Saved workflows, Build with
 * chat (its own box still builds), Run history.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    calls: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    fetches: [] as { path: string; method?: string; body?: unknown }[],
    responses: {} as Record<string, unknown>,
    postResponses: {} as Record<string, unknown>,
    FakeApiError,
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  wsUrl: (p: string) => `ws://api.test${p}`,
  api: (path: string, init?: { method?: string; body?: string }) => {
    api.calls.push(path);
    api.fetches.push({
      path,
      method: init?.method,
      body: typeof init?.body === "string" ? JSON.parse(init.body) : undefined,
    });
    return Promise.resolve({});
  },
  get: (path: string) => {
    api.calls.push(path);
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    const r = api.postResponses[path];
    if (r instanceof api.FakeApiError) return Promise.reject(r);
    return Promise.resolve(r === undefined ? { goal: {} } : r);
  },
  put: () => Promise.resolve({}),
  patch: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

// The page tests pin the Workflows page's ORDER and WIRING; the reactflow
// editor itself is pinned in the -canvas file (jsdom cannot lay it out).
vi.mock("@/components/workflow/WorkflowCanvas", () => ({
  default: () => <div data-testid="canvas-stub" />,
}));

import AutonomyPage from "@/app/autonomy/page";
import WorkflowsPage from "@/app/workflows/page";
import { STARTERS, starterLoadDetail } from "@/components/workflow/starters";
import type { GoalRecord, GoalState } from "@/components/GoalsStrip";

const src = (rel: string): string =>
  readFileSync(join(process.cwd(), rel), "utf8").replace(/\r\n/g, "\n");

/** a comes before b in document order. */
function before(a: Element, b: Element): boolean {
  return !!(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
}

/** The <section> a Card heading belongs to. */
function cardOf(heading: HTMLElement): HTMLElement {
  return (heading.closest("section") ?? heading.parentElement!) as HTMLElement;
}

beforeEach(() => {
  window.scrollTo = vi.fn();
  window.HTMLElement.prototype.scrollTo = vi.fn();
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  api.calls = [];
  api.posts = [];
  api.fetches = [];
  api.responses = {};
  api.postResponses = {};
  window.localStorage.clear();
});

/* ========================================================================== */
/*  Autonomy                                                                   */
/* ========================================================================== */

function goal(
  over: Partial<GoalRecord> & { id: string; name: string; state: GoalState },
): GoalRecord {
  return {
    contract_text: "Keep the inbox under 20 unread.",
    schedule: "0 9 * * *",
    budget: { max_dollars: 2 },
    spent: { tokens: 1200, dollars: 0.41, wallclock_s: 60, iterations: 3 },
    last_run_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    project_id: null,
    verifier: { kind: "manual", checks: [] },
    ...over,
  };
}

const SIMPLE_GOAL = {
  id: "lg_1",
  text: "Watch the active project for meaningful changes.",
  source: "user",
  category: "project",
  priority: 3,
  autonomy_level: "suggest",
  status: "active",
  action_budget: 5,
  spend_budget: 100000,
  actions_taken: 1,
  tokens_spent: 1200,
  last_acted_at: null,
  created_at: "2026-08-20T09:00:00Z",
};

const PROPOSAL = {
  id: "p_1",
  goal_id: "lg_1",
  title: "Summarise the new commits",
  rationale: "Three commits landed since yesterday.",
  action: {},
  risk: "low",
  source: "autonomy",
  status: "pending",
  session_id: null,
  tokens: 900,
  created_at: "2026-10-08T09:00:00Z",
};

const GRANT = {
  id: "gr_1",
  scope_kind: "goal",
  scope_id: "g_active",
  tool: "shell",
  args_hash: "sha256:abc",
  label: "command: git status",
  created_at: "2026-10-01T09:00:00Z",
  expires_at: "2026-10-31T09:00:00Z",
  revoked_at: null,
  uses: 2,
  last_used_at: null,
};

function primeAutonomy({
  enabled = true,
  kill = false,
  pending = 0,
  goals = [goal({ id: "g_active", name: "Inbox shepherd", state: "active" })],
  simple = [SIMPLE_GOAL] as unknown[],
  proposals = [] as unknown[],
}: {
  enabled?: boolean;
  kill?: boolean;
  pending?: number;
  goals?: GoalRecord[];
  simple?: unknown[];
  proposals?: unknown[];
} = {}) {
  api.responses["/autonomy"] = {
    enabled,
    level: "suggest",
    dry_run: false,
    kill_switch: kill,
    tick_seconds: 900,
    max_actions_per_day: 20,
    max_tokens_per_day: 200000,
    used_actions_24h: 1,
    used_tokens_24h: 1000,
    active_goals: 1,
    pending_proposals: pending,
  };
  api.responses["/goals"] = { goals };
  api.responses["/goals/digest?hours=24"] = { digest: { goals: [], since: "2026-10-07T09:00:00Z" } };
  api.responses["/autonomy/goals"] = { goals: simple };
  api.responses["/proposals?status=pending"] = { proposals };
  api.responses["/autonomy/briefing"] = {
    text: "quiet morning",
    active_goals: 1,
    recent_actions: 0,
    pending_proposals: pending,
    pushed: null,
  };
  api.responses["/grants?live=1"] = { grants: [GRANT] };
}

const heading = (name: RegExp) => screen.getByRole("heading", { name });
const findHeading = (name: RegExp) => screen.findByRole("heading", { name });

describe("T3-A1 Autonomy: the on/off state leads, then what waits on you, then goals", () => {
  it("the Autonomy status tile and Proposals come before the 'Goals from Chat' card", async () => {
    primeAutonomy({ proposals: [PROPOSAL], pending: 1 });
    render(<AutonomyPage />);
    const goalsCard = await findHeading(/^Goals from Chat/);
    const stateTile = await screen.findByText("Enabled"); // the "Autonomy" Stat's value
    const proposals = heading(/^Proposals/);
    expect(before(stateTile, goalsCard), "the on/off tile sits above the goals").toBe(true);
    expect(before(proposals, goalsCard), "Proposals (waiting on you) sit above the goals").toBe(true);
    expect(before(stateTile, proposals), "the status tiles lead the page").toBe(true);
  });

  it("anti-vacuity: every control is still on the page, and Standing grants still revoke", async () => {
    primeAutonomy({ proposals: [PROPOSAL], pending: 1 });
    render(<AutonomyPage />);
    // Keyed on records, not on the new titles, so this control is green
    // before AND after the change.
    await screen.findByTestId("goal-card-g_active");
    await screen.findByText(SIMPLE_GOAL.text);
    await screen.findByText(PROPOSAL.title);
    for (const name of [/Autonomy on|Enable autonomy/, /Check now/, /^\s*Kill switch\s*$/, /Send briefing/, /Approve/, /Add goal/]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: /Reject/ })).toBeInTheDocument();
    // The contract goal keeps its verbs.
    const card = within(screen.getByTestId("goal-card-g_active"));
    expect(card.getByRole("button", { name: "Run now" })).toBeInTheDocument();
    expect(card.getByRole("button", { name: /Pause/ })).toBeInTheDocument();
    // The simple goal keeps its dial and its status.
    expect(screen.getByTitle("Per-goal autonomy dial")).toBeInTheDocument();
    expect(screen.getByTitle("Goal status")).toBeInTheDocument();
    // Starter recipes still add with one press.
    expect(screen.getAllByRole("button", { name: "Add" }).length).toBeGreaterThan(0);
    // Standing grants: still on the page, still revocable.
    const grants = cardOf(await findHeading(/^Standing grants/));
    expect(within(grants).getByText(/git status/)).toBeInTheDocument();
    expect(within(grants).getByRole("button", { name: /Revoke/ })).toBeInTheDocument();
  });
});

describe("T3-A2 Autonomy: one name per kind of goal, and the words say which is which", () => {
  it("the top card is 'Goals from Chat'; no card is called 'Standing goals'", async () => {
    primeAutonomy();
    render(<AutonomyPage />);
    await findHeading(/^Goals from Chat/);
    await screen.findByText(SIMPLE_GOAL.text);
    expect(screen.queryByRole("heading", { name: /^Goals( · \d+)?$/ })).toBeNull();
    expect(screen.queryByRole("heading", { name: /Standing goals/ })).toBeNull();
  });

  it("the empty top card keeps 'Goals are born in Chat', points at Simple goals below, and has no create button", async () => {
    primeAutonomy({ goals: [] });
    render(<AutonomyPage />);
    const line = await screen.findByText(/Goals are born in Chat/);
    expect(line.textContent ?? "").toMatch(/Simple goals below/);
    const top = cardOf(heading(/^Goals from Chat/));
    expect(within(top).queryByRole("button", { name: /create goal|add goal|new goal/i })).toBeNull();
  });

  it("the /autonomy/goals list is 'Simple goals', never claims suggest-only, and says how it differs from Chat goals", async () => {
    primeAutonomy();
    render(<AutonomyPage />);
    const h = await findHeading(/^Simple goals/);
    // TRUTH: the per-goal dial offers Act (low-risk)/Act (all), so the list
    // title must not promise these goals only suggest.
    expect(h.textContent ?? "").not.toMatch(/suggest-only/i);
    const card = cardOf(h);
    expect(within(card).getByText(SIMPLE_GOAL.text)).toBeInTheDocument();
    expect(card.textContent ?? "").toMatch(/Chat/);
  });

  it("Simple goals, New goal and Starter goals are consecutive cards — the form sits with its list", async () => {
    primeAutonomy();
    render(<AutonomyPage />);
    await findHeading(/^Simple goals/);
    const h2s = Array.from(document.querySelectorAll("h2")).map((h) => (h.textContent ?? "").trim());
    const idx = (re: RegExp) => h2s.findIndex((t) => re.test(t));
    const at = [idx(/^Simple goals/), idx(/^New goal$/), idx(/^Starter goals$/)];
    expect(at.every((i) => i >= 0), `headings: ${h2s.join(" | ")}`).toBe(true);
    expect(Math.max(...at) - Math.min(...at), `headings: ${h2s.join(" | ")}`).toBe(2);
  });

  it("the empty Simple goals line points the right way at the form", async () => {
    primeAutonomy({ simple: [] });
    render(<AutonomyPage />);
    const list = cardOf(await findHeading(/^Simple goals/));
    const form = cardOf(heading(/^New goal$/));
    const text = list.textContent ?? "";
    if (before(list, form)) expect(text).not.toMatch(/\babove\b/);
    else expect(text).not.toMatch(/\bbelow\b/);
  });
});

describe("T3-A3 Autonomy: the Kill switch rests quietly while autonomy is off", () => {
  const REST_RED = /(^|\s)(bg|text|border)-(rose-|tone-danger)/;
  const HOVER_RED = /(^|\s)hover:(bg|text|border)-(rose-|tone-danger)/;

  it("disabled + nothing pending: neutral at rest, red on hover, still enabled and still halts", async () => {
    primeAutonomy({ enabled: false, pending: 0 });
    render(<AutonomyPage />);
    await screen.findByText("Disabled");
    const btn = await screen.findByRole("button", { name: /^\s*Kill switch\s*$/ });
    await waitFor(() => expect(btn).toBeEnabled());
    expect(btn.className).not.toMatch(REST_RED);
    expect(btn.className).toMatch(HOVER_RED);
    fireEvent.click(btn);
    await waitFor(() =>
      expect(api.posts).toContainEqual({ path: "/autonomy/kill", body: { enabled: true } }),
    );
  });

  // v1.316.0 review: the quiet rest needs BOTH halves — autonomy off AND
  // nothing pending. Proposals waiting while autonomy is off are still a
  // reason to be able to halt at a glance, so the switch stays red.
  it("disabled + proposals pending: the Kill switch is still red at rest (control)", async () => {
    primeAutonomy({ enabled: false, pending: 2 });
    render(<AutonomyPage />);
    await screen.findByText("Disabled");
    const btn = await screen.findByRole("button", { name: /^\s*Kill switch\s*$/ });
    expect(btn.className).toMatch(REST_RED);
  });

  it("enabled: the Kill switch is red at rest (control)", async () => {
    primeAutonomy({ enabled: true });
    render(<AutonomyPage />);
    await screen.findByText("Enabled");
    const btn = screen.getByRole("button", { name: /^\s*Kill switch\s*$/ });
    expect(btn.className).toMatch(REST_RED);
  });

  it("engaged: Release kill switch is offered and the banner says autonomy is halted (unchanged)", async () => {
    primeAutonomy({ enabled: false, kill: true });
    render(<AutonomyPage />);
    expect(await screen.findByRole("button", { name: /Release kill switch/ })).toBeInTheDocument();
    expect(screen.getByText(/Kill switch engaged/)).toBeInTheDocument();
  });
});

describe("T3-A4 Autonomy: the New goal form's labels name their fields", () => {
  it("every label resolves to its control; the existing aria-labels still work", async () => {
    primeAutonomy();
    render(<AutonomyPage />);
    await findHeading(/^New goal$/);
    expect(screen.getByLabelText("What should I keep working toward?").tagName).toBe("TEXTAREA");
    expect(screen.getByLabelText("Category").tagName).toBe("INPUT");
    const freedom = screen.getByLabelText("How much freedom");
    expect(freedom.tagName).toBe("SELECT");
    expect(screen.getByLabelText("Autonomy dial")).toBe(freedom);
    expect(screen.getByLabelText("Priority").tagName).toBe("SELECT");
  });

  it("a real flow: filled through its labels, Add goal POSTs /autonomy/goals", async () => {
    primeAutonomy();
    render(<AutonomyPage />);
    await findHeading(/^New goal$/);
    fireEvent.change(screen.getByLabelText("What should I keep working toward?"), {
      target: { value: "Keep the client folder tidy" },
    });
    fireEvent.change(screen.getByLabelText("Category"), { target: { value: "files" } });
    fireEvent.click(screen.getByRole("button", { name: /Add goal/ }));
    await waitFor(() => expect(api.posts.some((p) => p.path === "/autonomy/goals")).toBe(true));
    const body = api.posts.find((p) => p.path === "/autonomy/goals")!.body as Record<string, unknown>;
    expect(body.text).toBe("Keep the client folder tidy");
    expect(body.category).toBe("files");
    expect(body.autonomy_level).toBe("suggest");
  });

  it("source: every <label> in the autonomy page is tied to a control (htmlFor or <Field>)", () => {
    const s = src("app/autonomy/page.tsx");
    const bare = [...s.matchAll(/<label(?![^>]*htmlFor)[^>]*>/g)].map((m) => m[0]);
    expect(bare, "a <label> with no htmlFor names nothing").toEqual([]);
  });
});

/* ========================================================================== */
/*  Workflows page                                                             */
/* ========================================================================== */

const GENERATED = {
  name: "Research brief",
  description: "research then summarise",
  steps: [{ name: "Research", agent: "planner", task: "Research the topic", tool: null }],
  reply: "Built a 1-step workflow.",
};

function primeWorkflows(saved: unknown[] = []) {
  api.responses["/workflows"] = { workflows: saved };
  api.responses["/workflows/runs?limit=50"] = { runs: [] };
  api.postResponses["/workflows/generate"] = GENERATED;
}

/** The "Describe a workflow" box that sits ABOVE the canvas. */
async function quickComposer(): Promise<HTMLElement> {
  const canvas = await screen.findByTestId("canvas-stub");
  let boxes: HTMLElement[] = [];
  await waitFor(() => {
    boxes = screen.getAllByLabelText(/Describe a workflow/i);
    expect(boxes.some((b) => before(b, canvas))).toBe(true);
  });
  return boxes.find((b) => before(b, canvas))!;
}

describe("T3-W1 Workflows: the easy path comes before the power editor", () => {
  it("no saved workflows: a 'Describe a workflow' box sits above the canvas", async () => {
    primeWorkflows([]);
    render(<WorkflowsPage />);
    const box = await quickComposer();
    expect(["TEXTAREA", "INPUT"]).toContain(box.tagName);
    expect(box.closest("form"), "the quick box is a form, so Enter/Build submits").not.toBeNull();
  });

  it("the quick box sends through the builder chat — one generate POST, the message lands in its thread", async () => {
    primeWorkflows([]);
    const loads: unknown[] = [];
    const onLoad = (e: Event) => loads.push((e as CustomEvent).detail);
    window.addEventListener("ij:load-workflow", onLoad);
    try {
      render(<WorkflowsPage />);
      const box = await quickComposer();
      fireEvent.change(box, { target: { value: "Research a topic and brief me" } });
      fireEvent.submit(box.closest("form")!);
      await waitFor(() =>
        expect(api.posts.filter((p) => p.path === "/workflows/generate")).toHaveLength(1),
      );
      expect(api.posts.find((p) => p.path === "/workflows/generate")!.body).toEqual({
        description: "Research a topic and brief me",
      });
      const chat = cardOf(heading(/^Build with chat/));
      await waitFor(() =>
        expect(within(chat).getByText("Research a topic and brief me")).toBeInTheDocument(),
      );
      await waitFor(() => expect(within(chat).getByText(GENERATED.reply)).toBeInTheDocument());
      expect(loads).toHaveLength(1);
      // The page makes no SEPARATE client save — the daemon's generate route
      // already saved the build (`_build_workflow` → `store.save`); only the
      // starter door is save-free.
      expect(api.posts.filter((p) => p.path === "/workflows")).toHaveLength(0);
    } finally {
      window.removeEventListener("ij:load-workflow", onLoad);
    }
  });

  it("v1.316.0 review: the quick box's reply never claims 'Nothing is saved' — generate already saved the workflow", async () => {
    primeWorkflows([]);
    render(<WorkflowsPage />);
    const box = await quickComposer();
    const quick = box.closest("section")!;
    fireEvent.change(box, { target: { value: "Research a topic and brief me" } });
    fireEvent.submit(box.closest("form")!);
    // Wait for the reply line ITSELF (the thing asserted), not the POST.
    await waitFor(() => expect(quick.textContent).toContain(GENERATED.reply));
    expect(quick.textContent).not.toMatch(/Nothing is saved/i);
  });

  it("no saved workflows: the starters are one press away above the canvas, and load without saving", async () => {
    primeWorkflows([]);
    const loads: unknown[] = [];
    const onLoad = (e: Event) => loads.push((e as CustomEvent).detail);
    window.addEventListener("ij:load-workflow", onLoad);
    try {
      render(<WorkflowsPage />);
      const canvas = await screen.findByTestId("canvas-stub");
      const first = STARTERS[0];
      let chip: HTMLElement | undefined;
      await waitFor(() => {
        chip = screen
          .getAllByRole("button", { name: new RegExp(first.title) })
          .find((b) => before(b, canvas));
        expect(chip, `a "${first.title}" button above the canvas`).toBeTruthy();
      });
      fireEvent.click(chip!);
      expect(loads).toEqual([starterLoadDetail(first)]);
      expect(api.posts.filter((p) => p.path === "/workflows")).toHaveLength(0);
    } finally {
      window.removeEventListener("ij:load-workflow", onLoad);
    }
  });

  it("anti-vacuity: Templates, Saved workflows, Build with chat (its own box still builds) and Run history all remain", async () => {
    primeWorkflows([{ id: 1, name: "client-intake", description: "", steps_json: "[]", project_id: null }]);
    render(<WorkflowsPage />);
    await screen.findByTestId("canvas-stub");
    const templates = cardOf(await findHeading(/^Templates/));
    fireEvent.click(within(templates).getByRole("button", { name: /Show|Hide/ }));
    expect(within(templates).getAllByRole("button", { name: /Load into editor/ }).length).toBe(STARTERS.length);
    expect(await findHeading(/Saved workflows/)).toBeInTheDocument();
    expect(await findHeading(/Run history/)).toBeInTheDocument();
    const chat = cardOf(heading(/^Build with chat/));
    const own = within(chat).getByPlaceholderText(/Describe the workflow you want/);
    fireEvent.change(own, { target: { value: "Pull my open tasks and plan today" } });
    fireEvent.click(within(chat).getByRole("button", { name: /Build/ }));
    await waitFor(() =>
      expect(api.posts.filter((p) => p.path === "/workflows/generate")).toHaveLength(1),
    );
  });

  it("source: the canvas no longer grows with the window (no 100vh-based height)", () => {
    const s = src("components/workflow/WorkflowCanvas.tsx");
    expect(s).not.toMatch(/h-\[calc\(100vh/);
  });
});
