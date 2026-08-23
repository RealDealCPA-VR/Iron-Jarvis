/**
 * v1.208.0 — Goals live where autonomy already lives.
 *
 * Two surfaces, no new pages:
 *   - the Autonomy page gains a Goals section: one card per goal with the
 *     contract text, state chip, spent-vs-budget rendered HONESTLY
 *     ("$0.41 of $2.00" / "unlimited — by your choice"), and controls that
 *     match the state (Run now / Pause / Resume / Stop; stop confirms;
 *     tripped shows the breaker reason VERBATIM and Resume clears it).
 *   - the Overview gains GoalsStrip, the forgotten-goal killer: a compact
 *     pill per live goal linking to /autonomy — and literally NOTHING when
 *     there is nothing to show (no husk). Tripped/failed wear the warn tone
 *     and sort first: bad news leads.
 *
 * Wire contract — THE BACKEND'S, not a frontend invention. CROSS-SUITE RULE:
 * every fixture below uses the exact keys tests/test_goals_routes_v1208.py
 * pins (TOKENS_BUDGET = {"max_tokens": …}; goal_view serves budget/spent
 * verbatim from goals/models.py BUDGET_BOUNDS) — the two suites must pin THE
 * SAME contract, or a green dashboard suite certifies a UI that renders
 * "no budget set" beside real spend:
 *   GET  /goals -> { goals: [...] }, budget {max_dollars?, max_tokens?,
 *        max_wallclock_s?, unlimited?}, spent {tokens, dollars, wallclock_s,
 *        iterations}
 *   POST /goals/{id}/pause|resume|stop|reopen -> { goal } (409 verbatim on a
 *        guarded transition — hence no Stop button on terminal states)
 *   POST /goals/{id}/run -> the engine result; an honest refusal is a RESULT:
 *        200 {ok:false, refused:true, reason}
 *   events goal.iteration_started / iteration_completed / satisfied / tripped
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";

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
    /** Raw api() calls (the PATCH lane), with method + parsed JSON body. */
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
    if (r === undefined) {
      return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    }
    return Promise.resolve(r);
  },
  post: (path: string, body?: unknown) => {
    api.posts.push({ path, body });
    const r = api.postResponses[path];
    return Promise.resolve(r === undefined ? { goal: {} } : r);
  },
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

const bus = vi.hoisted(() => ({ events: [] as unknown[] }));
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: bus.events, connected: true }),
}));

vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({
      href,
      children,
      ...rest
    }: {
      href: string;
      children?: React.ReactNode;
    }) => createElement("a", { href, ...rest }, children),
  };
});

import AutonomyPage from "@/app/autonomy/page";
import {
  GoalsStrip,
  scheduleWords,
  type GoalRecord,
  type GoalState,
} from "@/components/GoalsStrip";

afterEach(() => {
  cleanup();
  api.calls = [];
  api.posts = [];
  api.fetches = [];
  api.responses = {};
  api.postResponses = {};
  bus.events = [];
  window.localStorage.clear(); // "Not now" dismissals must not leak across tests
});

/* ------------------------------------------------------------------ helpers */

function goal(
  over: Partial<GoalRecord> & { id: string; name: string; state: GoalState },
): GoalRecord {
  return {
    contract_text: "Keep the inbox under 20 unread.",
    schedule: "0 9 * * *",
    // BACKEND keys (see the cross-suite rule in the header): max_* bounds,
    // never bare "dollars"/"tokens".
    budget: { max_dollars: 2 },
    spent: { tokens: 1200, dollars: 0.41, wallclock_s: 60, iterations: 3 },
    last_run_at: new Date(Date.now() - 5 * 60_000).toISOString(),
    project_id: null,
    // The store's own default verifier shape (see the cross-suite rule).
    verifier: { kind: "manual", checks: [] },
    ...over,
  };
}

/** One LEGACY motivation intent goal, in the legacy wire shape (route
 *  reconciliation moved these verbatim to /autonomy/goals). */
const LEGACY_GOAL = {
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

/** Everything the Autonomy page fetches besides /goals. */
function primeAutonomy(
  goals: GoalRecord[],
  legacyGoals: unknown[] = [],
  digestGoals: unknown[] = [],
) {
  api.responses["/goals/digest?hours=24"] = {
    digest: { goals: digestGoals, since: "2026-08-22T15:00:00Z" },
  };
  api.responses["/autonomy"] = {
    enabled: true,
    level: "suggest",
    dry_run: false,
    kill_switch: false,
    tick_seconds: 900,
    max_actions_per_day: 20,
    max_tokens_per_day: 200000,
    used_actions_24h: 1,
    used_tokens_24h: 1000,
    active_goals: goals.filter((g) => g.state === "active").length,
    pending_proposals: 0,
  };
  api.responses["/autonomy/goals"] = { goals: legacyGoals };
  api.responses["/proposals?status=pending"] = { proposals: [] };
  api.responses["/autonomy/briefing"] = {
    text: "quiet morning",
    active_goals: 0,
    recent_actions: 0,
    pending_proposals: 0,
    pushed: null,
  };
  api.responses["/goals"] = { goals };
}

const goalGets = () => api.calls.filter((c) => c === "/goals").length;

function goalEvent(id: string, type: string) {
  return {
    id,
    type,
    session_id: null,
    ts: "2026-08-23T10:00:00Z",
    payload: { goal_id: "g_active" },
  };
}

const TRIP_REASON = "spent $2.11 of $2.00 — weekly dollar cap";

/* ------------------------------------------- Autonomy page: the Goals list */

describe("Autonomy page — Goals section", () => {
  it("renders every state with exactly its controls, and the right chip tones", async () => {
    primeAutonomy([
      goal({ id: "g_active", name: "Inbox shepherd", state: "active" }),
      goal({ id: "g_paused", name: "Nightly recap", state: "paused" }),
      goal({ id: "g_satisfied", name: "Q3 filings", state: "satisfied" }),
      goal({ id: "g_failed", name: "Backlog burner", state: "failed" }),
      goal({ id: "g_stopped", name: "Old crawler", state: "stopped" }),
      goal({
        id: "g_tripped",
        name: "Ad optimizer",
        state: "tripped",
        trip_reason: TRIP_REASON,
        spent: { tokens: 9000, dollars: 2.11, wallclock_s: 300, iterations: 7 },
      }),
    ]);
    render(<AutonomyPage />);

    const active = within(await screen.findByTestId("goal-card-g_active"));
    expect(active.getByRole("button", { name: "Run now" })).toBeInTheDocument();
    expect(active.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(active.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(active.queryByRole("button", { name: "Resume" })).toBeNull();

    const paused = within(screen.getByTestId("goal-card-g_paused"));
    expect(paused.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(paused.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(paused.queryByRole("button", { name: "Run now" })).toBeNull();
    expect(paused.queryByRole("button", { name: "Pause" })).toBeNull();

    const tripped = within(screen.getByTestId("goal-card-g_tripped"));
    expect(tripped.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(tripped.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(tripped.queryByRole("button", { name: "Run now" })).toBeNull();
    expect(tripped.queryByRole("button", { name: "Pause" })).toBeNull();

    // Terminal states (satisfied/failed/stopped): run_iteration refuses every
    // non-active state and the transition table 409s a stop — so the ONLY
    // honest control is the explicit /reopen door. No dead buttons.
    for (const id of ["g_satisfied", "g_failed", "g_stopped"]) {
      const card = within(screen.getByTestId(`goal-card-${id}`));
      expect(card.getByRole("button", { name: "Reopen" })).toBeInTheDocument();
      expect(card.queryByRole("button", { name: "Run now" })).toBeNull();
      expect(card.queryByRole("button", { name: "Pause" })).toBeNull();
      expect(card.queryByRole("button", { name: "Resume" })).toBeNull();
      expect(card.queryByRole("button", { name: "Stop" })).toBeNull();
    }
    // …and Reopen never leaks onto the live states.
    for (const id of ["g_active", "g_paused", "g_tripped"]) {
      const card = within(screen.getByTestId(`goal-card-${id}`));
      expect(card.queryByRole("button", { name: "Reopen" })).toBeNull();
    }

    // Chip tones: active=accent, satisfied=emerald, tripped/failed=rose,
    // paused/stopped=zinc — the semantic scale, not branding.
    const chip = (id: string) =>
      screen
        .getByTestId(`goal-card-${id}`)
        .querySelector("[data-goal-state]")!.className;
    expect(chip("g_active")).toContain("accent");
    expect(chip("g_satisfied")).toContain("emerald");
    expect(chip("g_tripped")).toContain("rose");
    expect(chip("g_failed")).toContain("rose");
    expect(chip("g_paused")).toContain("white/10");
    expect(chip("g_stopped")).toContain("white/10");
  });

  it("renders spent vs budget honestly — every bound as a fraction, unlimited as a choice", async () => {
    primeAutonomy([
      goal({ id: "g_capped", name: "Capped goal", state: "active" }),
      goal({
        id: "g_unlimited",
        name: "Unbounded goal",
        state: "active",
        budget: { unlimited: true },
        spent: { tokens: 500, dollars: 1.23, wallclock_s: 12, iterations: 2 },
      }),
      // The routes suite's own TOKENS_BUDGET shape ({"max_tokens": …}) — the
      // exact fixture the two suites must agree on.
      goal({
        id: "g_tokens",
        name: "Token-capped goal",
        state: "active",
        budget: { max_tokens: 1_000_000 },
      }),
      goal({
        id: "g_clock",
        name: "Clock-capped goal",
        state: "active",
        budget: { max_wallclock_s: 14400 },
        spent: { tokens: 0, dollars: 0, wallclock_s: 7560, iterations: 4 },
      }),
    ]);
    render(<AutonomyPage />);

    const capped = await screen.findByTestId("goal-card-g_capped");
    expect(capped.textContent).toContain("$0.41 of $2.00");
    expect(capped.textContent).not.toContain("unlimited");
    expect(capped.textContent).not.toContain("no budget set");

    const unlimited = screen.getByTestId("goal-card-g_unlimited");
    expect(unlimited.textContent).toContain("$1.23 spent · unlimited — by your choice");

    const tokens = screen.getByTestId("goal-card-g_tokens");
    expect(tokens.textContent).toContain("1,200 of 1,000,000 tokens");
    expect(tokens.textContent).not.toContain("no budget set");

    const clock = screen.getByTestId("goal-card-g_clock");
    expect(clock.textContent).toContain("2.1h of 4h");
    expect(clock.textContent).not.toContain("no budget set");

    // The schedule reads as words, never raw cron.
    expect(capped.textContent).toContain("daily at 09:00");
  });

  it("shows a tripped goal's breaker reason VERBATIM", async () => {
    primeAutonomy([
      goal({
        id: "g_tripped",
        name: "Ad optimizer",
        state: "tripped",
        trip_reason: TRIP_REASON,
      }),
    ]);
    render(<AutonomyPage />);
    const reason = await screen.findByTestId("trip-reason-g_tripped");
    expect(reason.textContent).toBe(TRIP_REASON);
  });

  it("Run now / Pause / Resume POST the contract routes and refetch the list", async () => {
    primeAutonomy([
      goal({ id: "g_active", name: "Inbox shepherd", state: "active" }),
      goal({
        id: "g_tripped",
        name: "Ad optimizer",
        state: "tripped",
        trip_reason: TRIP_REASON,
      }),
    ]);
    render(<AutonomyPage />);
    const active = within(await screen.findByTestId("goal-card-g_active"));
    const before = goalGets(); // the section + the legacy card both GET /goals

    fireEvent.click(active.getByRole("button", { name: "Run now" }));
    await waitFor(() =>
      expect(api.posts.map((p) => p.path)).toContain("/goals/g_active/run"),
    );
    // The action refetches — the card must show the daemon's truth, not a guess.
    await waitFor(() => expect(goalGets()).toBeGreaterThan(before));

    fireEvent.click(active.getByRole("button", { name: "Pause" }));
    await waitFor(() =>
      expect(api.posts.map((p) => p.path)).toContain("/goals/g_active/pause"),
    );

    // Resume on a tripped goal clears the breaker per the API — same POST verb.
    const tripped = within(screen.getByTestId("goal-card-g_tripped"));
    fireEvent.click(tripped.getByRole("button", { name: "Resume" }));
    await waitFor(() =>
      expect(api.posts.map((p) => p.path)).toContain("/goals/g_tripped/resume"),
    );
  });

  it("Stop asks for confirmation — one click arms, only the second POSTs", async () => {
    primeAutonomy([goal({ id: "g_active", name: "Inbox shepherd", state: "active" })]);
    render(<AutonomyPage />);
    const card = within(await screen.findByTestId("goal-card-g_active"));

    fireEvent.click(card.getByRole("button", { name: "Stop" }));
    expect(api.posts).toHaveLength(0); // armed, not executed
    const confirm = await card.findByRole("button", { name: "Confirm?" });

    fireEvent.click(confirm);
    await waitFor(() =>
      expect(api.posts.map((p) => p.path)).toContain("/goals/g_active/stop"),
    );
    expect(api.posts).toHaveLength(1);
  });

  it("an honest run refusal (200 ok:false) renders the reason VERBATIM as a warning, never success", async () => {
    primeAutonomy([goal({ id: "g_active", name: "Inbox shepherd", state: "active" })]);
    const REASON =
      "budget exhausted: tokens spent 1200 has reached max_tokens 1000";
    api.postResponses["/goals/g_active/run"] = {
      ok: false,
      refused: true,
      reason: REASON,
    };
    render(<AutonomyPage />);
    const active = within(await screen.findByTestId("goal-card-g_active"));

    fireEvent.click(active.getByRole("button", { name: "Run now" }));
    const note = await screen.findByTestId("goal-refusal");
    expect(note.textContent).toContain(REASON);
    // A refusal must never read as a green "running now".
    expect(screen.queryByText(/is running now/)).toBeNull();
  });

  it("Reopen POSTs /goals/{id}/reopen, and the refreshed active goal then exposes Run now", async () => {
    const satisfied = goal({ id: "g_1", name: "Q3 filings", state: "satisfied" });
    primeAutonomy([satisfied]);
    render(<AutonomyPage />);
    const card = within(await screen.findByTestId("goal-card-g_1"));
    // No direct run on a non-active state — the engine would refuse it.
    expect(card.queryByRole("button", { name: "Run now" })).toBeNull();

    // The reload after the POST serves the reopened (now active) record.
    api.responses["/goals"] = { goals: [{ ...satisfied, state: "active" }] };
    fireEvent.click(card.getByRole("button", { name: "Reopen" }));
    await waitFor(() =>
      expect(api.posts.map((p) => p.path)).toContain("/goals/g_1/reopen"),
    );
    await waitFor(() =>
      expect(
        within(screen.getByTestId("goal-card-g_1")).getByRole("button", {
          name: "Run now",
        }),
      ).toBeInTheDocument(),
    );
  });

  it("a goal.* event refetches the section's list", async () => {
    primeAutonomy([goal({ id: "g_active", name: "Inbox shepherd", state: "active" })]);
    const { rerender } = render(<AutonomyPage />);
    await screen.findByTestId("goal-card-g_active");
    const before = goalGets();

    bus.events = [goalEvent("ev_1", "goal.tripped")];
    rerender(<AutonomyPage />);
    await waitFor(() => expect(goalGets()).toBeGreaterThan(before));
  });

  it("empty state is one quiet line — no CTA to a creation form that doesn't exist", async () => {
    primeAutonomy([]);
    render(<AutonomyPage />);
    await screen.findByText(/Goals are born in Chat/);
    // The line points at Chat with words, not a fake button.
    expect(screen.queryByRole("button", { name: /create goal/i })).toBeNull();
  });
});

/* ------------------------------------- route reconciliation: no crossed wires */

describe("Autonomy page — contract goals on /goals, legacy goals on /autonomy/goals", () => {
  it("each card fetches ITS route and renders ITS records", async () => {
    primeAutonomy(
      [goal({ id: "g_active", name: "Inbox shepherd", state: "active" })],
      [LEGACY_GOAL],
    );
    render(<AutonomyPage />);

    // New section: the contract goal, from GET /goals.
    await screen.findByTestId("goal-card-g_active");
    // Legacy card: its own record again, from GET /autonomy/goals — no more
    // degrading against the new shape.
    await screen.findByText(LEGACY_GOAL.text);
    expect(api.calls).toContain("/goals");
    expect(api.calls).toContain("/autonomy/goals");
  });

  it("legacy writes go to /autonomy/goals — dial PATCH, new-goal POST, starter POST", async () => {
    primeAutonomy(
      [goal({ id: "g_active", name: "Inbox shepherd", state: "active" })],
      [LEGACY_GOAL],
    );
    render(<AutonomyPage />);
    await screen.findByText(LEGACY_GOAL.text);

    // Status dial on the legacy row → PATCH /autonomy/goals/{id}.
    fireEvent.change(screen.getByTitle("Goal status"), {
      target: { value: "paused" },
    });
    await waitFor(() =>
      expect(api.calls).toContain("/autonomy/goals/lg_1"),
    );

    // The "New goal" form → POST /autonomy/goals.
    fireEvent.change(screen.getByPlaceholderText(/Keep my inbox under 20 unread/), {
      target: { value: "Legacy intent from the form" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add goal" }));
    await waitFor(() =>
      expect(api.posts.map((p) => p.path)).toContain("/autonomy/goals"),
    );

    // A starter recipe → POST /autonomy/goals too.
    const postsBefore = api.posts.length;
    fireEvent.click(screen.getAllByRole("button", { name: "Add" })[0]);
    await waitFor(() => expect(api.posts.length).toBe(postsBefore + 1));
    expect(api.posts[api.posts.length - 1].path).toBe("/autonomy/goals");

    // The wires never cross: nothing legacy ever POSTs the contract
    // collection route, and no contract action was fired here at all.
    expect(api.posts.filter((p) => p.path === "/goals")).toHaveLength(0);
    expect(api.posts.filter((p) => p.path.startsWith("/goals/"))).toHaveLength(0);
    // No per-goal contract call either (the digest GET is the section's own
    // read of the contract surface, not a crossed wire).
    expect(
      api.calls.filter(
        (c) => c.startsWith("/goals/") && !c.startsWith("/goals/digest"),
      ),
    ).toHaveLength(0);
  });

  it("new-section actions still POST the contract routes, never /autonomy/goals/*", async () => {
    primeAutonomy(
      [goal({ id: "g_active", name: "Inbox shepherd", state: "active" })],
      [LEGACY_GOAL],
    );
    render(<AutonomyPage />);
    const active = within(await screen.findByTestId("goal-card-g_active"));

    fireEvent.click(active.getByRole("button", { name: "Run now" }));
    await waitFor(() =>
      expect(api.posts.map((p) => p.path)).toContain("/goals/g_active/run"),
    );
    expect(
      api.posts.filter((p) => p.path.startsWith("/autonomy/goals")),
    ).toHaveLength(0);
  });
});

/* -------------------------------------- G2: grant offers (receipts, never auto) */

describe("Autonomy page — grant offers", () => {
  // The LIST route deliberately carries NO ask_stats (routes/goals.py
  // `_payload`: stats ride only GET /goals/{id}) — the list fixture must not
  // invent the key, and the detail fixture is where the receipts live.
  const OFFERED = () =>
    goal({
      id: "g_active",
      name: "Inbox shepherd",
      state: "active",
      grant_offers: ["web_fetch"],
    });

  function primeDetail(g: GoalRecord, stats: Record<string, unknown>) {
    api.responses[`/goals/${g.id}`] = { goal: { ...g, ask_stats: stats } };
  }

  const detailGets = (id: string) =>
    api.calls.filter((c) => c === `/goals/${id}`).length;

  it("upgrades the receipts wording to the detail's real N, and Allow PATCHes /grants", async () => {
    const offered = OFFERED();
    primeAutonomy([offered]);
    primeDetail(offered, {
      web_fetch: { asked: 3, approved: 3, denied: 0, timed_out: 0 },
    });
    render(<AutonomyPage />);
    const offer = await screen.findByTestId("grant-offer-g_active-web_fetch");
    // The wording settles on the DETAIL's N ("every ask" is only the
    // loading/failed fallback).
    await waitFor(() =>
      expect(offer.textContent).toContain(
        "You approved all 3 asks for web_fetch on this goal — always allow it here?",
      ),
    );
    expect(detailGets("g_active")).toBe(1); // one prefetch, no storm
    const before = goalGets();

    fireEvent.click(within(offer).getByRole("button", { name: "Allow" }));
    await waitFor(() =>
      expect(api.fetches).toContainEqual({
        path: "/goals/g_active/grants",
        method: "PATCH",
        body: { add: ["web_fetch"] },
      }),
    );
    // Allow refetches — the granted state must come back from the server —
    // and the already-fetched detail is NOT re-requested.
    await waitFor(() => expect(goalGets()).toBeGreaterThan(before));
    expect(detailGets("g_active")).toBe(1);
  });

  it("Not now dismisses without a write and persists across remounts (localStorage per goal+tool)", async () => {
    const offered = OFFERED();
    primeAutonomy([offered]);
    primeDetail(offered, {
      web_fetch: { asked: 3, approved: 3, denied: 0, timed_out: 0 },
    });
    const first = render(<AutonomyPage />);
    const offer = await screen.findByTestId("grant-offer-g_active-web_fetch");

    fireEvent.click(within(offer).getByRole("button", { name: "Not now" }));
    await waitFor(() =>
      expect(screen.queryByTestId("grant-offer-g_active-web_fetch")).toBeNull(),
    );
    expect(
      window.localStorage.getItem("ij_goal_offer_dismissed:g_active:web_fetch"),
    ).toBe("1");
    // Dismissal writes NOTHING to the daemon.
    expect(api.fetches.filter((f) => f.path.includes("/grants"))).toHaveLength(0);

    // A fresh mount hydrates the dismissal back from localStorage.
    first.unmount();
    render(<AutonomyPage />);
    await screen.findByTestId("goal-card-g_active");
    await waitFor(() =>
      expect(screen.queryByTestId("grant-offer-g_active-web_fetch")).toBeNull(),
    );
  });

  it("renders ONLY the server's offers — receipts alone never become an offer client-side", async () => {
    // A naive client threshold (>=3 asks, all approved) would offer shell
    // here; the server said NO (deny-floor tool → grant_offers is empty), and
    // the UI must not re-derive the rule. The receipts live on the DETAIL.
    const plain = goal({
      id: "g_active",
      name: "Inbox shepherd",
      state: "active",
      grant_offers: [],
    });
    primeAutonomy([plain]);
    primeDetail(plain, {
      shell: { asked: 5, approved: 5, denied: 0, timed_out: 0 },
    });
    render(<AutonomyPage />);
    await screen.findByTestId("goal-card-g_active");
    expect(screen.queryByText(/always allow it here/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Allow" })).toBeNull();
    // No offer → no prefetch: the detail is not touched until asked for.
    expect(detailGets("g_active")).toBe(0);

    // The receipts stay available behind the expand, which lazily GETs the
    // detail EXACTLY once.
    expect(screen.queryByTestId("ask-history-g_active")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Ask history" }));
    const table = await screen.findByTestId("ask-history-g_active");
    const cells = within(table).getAllByRole("cell").map((c) => c.textContent);
    expect(cells).toEqual(["shell", "5", "5", "0", "0"]);
    expect(detailGets("g_active")).toBe(1);

    // Close and reopen: the cached receipts render, no second GET.
    fireEvent.click(screen.getByRole("button", { name: "Hide ask history" }));
    fireEvent.click(screen.getByRole("button", { name: "Ask history" }));
    await screen.findByTestId("ask-history-g_active");
    expect(detailGets("g_active")).toBe(1);
  });
});

/* --------------------------------------------- G2: verifier honesty in words */

describe("Autonomy page — the verifier kind in words", () => {
  it("four kinds, four different amounts of certainty", async () => {
    const NOTE = "All three sections read complete and grounded.";
    primeAutonomy([
      goal({
        id: "g_checks",
        name: "Checked goal",
        state: "active",
        verifier: { kind: "checks", checks: ["file exists"] },
      }),
      goal({
        id: "g_adv",
        name: "Adversarial goal",
        state: "active",
        verifier: { kind: "adversarial" },
      }),
      goal({
        id: "g_judged",
        name: "Judged goal",
        state: "satisfied",
        verifier: { kind: "judged", judged_note: NOTE },
      }),
      goal({
        id: "g_judged_live",
        name: "Judged live goal",
        state: "active",
        verifier: { kind: "judged", judged_note: NOTE },
      }),
      goal({
        id: "g_manual",
        name: "Manual goal",
        state: "active",
        verifier: { kind: "manual", checks: [] },
      }),
      // goal_view attaches judged_note whenever the judge was the ONLY gate —
      // including "adversarial" with ZERO checks — so the label must follow
      // the evidence, not the kind string.
      goal({
        id: "g_adv_judge_only",
        name: "Adversarial judge-only goal",
        state: "satisfied",
        verifier: { kind: "adversarial", checks: [], judged_note: NOTE },
      }),
    ]);
    render(<AutonomyPage />);

    expect((await screen.findByTestId("verifier-g_checks")).textContent).toBe(
      "verified by checks",
    );
    expect(screen.getByTestId("verifier-g_adv").textContent).toBe(
      "adversarially verified",
    );
    expect(screen.getByTestId("verifier-g_adv_judge_only").textContent).toBe(
      "adversarially verified — judge-only (no deterministic checks)",
    );
    expect(screen.getByTestId("judged-note-g_adv_judge_only").textContent).toBe(
      NOTE,
    );
    expect(screen.getByTestId("verifier-g_judged").textContent).toBe(
      "model-judged — no deterministic checks",
    );
    expect(screen.getByTestId("verifier-g_manual").textContent).toBe(
      "manual — you decide",
    );

    // The judge's own sentence surfaces VERBATIM on the satisfied goal…
    expect(screen.getByTestId("judged-note-g_judged").textContent).toBe(NOTE);
    // …and only there — a note on a still-active goal would claim a verdict
    // that has not been reached.
    expect(screen.queryByTestId("judged-note-g_judged_live")).toBeNull();
  });
});

/* ------------------------------------------------- G2: the Last-24h digest */

describe("Autonomy page — the Last 24h digest", () => {
  it("renders the server-composed digest per goal behind the collapsible", async () => {
    // The SERVER's shape, verbatim from tests/test_goal_digest_v1209.py
    // (compose_digest): results are per-session objects, asks_held is a LIST
    // of held asks, state_changes are {to, reason, at} — the cross-suite
    // rule again: both suites pin THE SAME contract.
    primeAutonomy(
      [goal({ id: "g_active", name: "Inbox shepherd", state: "active" })],
      [],
      [
        {
          id: "g_active",
          name: "Inbox shepherd",
          ran: 3,
          spent: { dollars: 0.41, tokens: 1200 },
          results: [
            {
              session_id: "sess_1",
              summary: "2 replies drafted",
              files: ["draft-a.md", "draft-b.md"],
            },
            { session_id: "sess_2", summary: "inbox at 14 unread", files: [] },
          ],
          asks_held: [
            {
              approval_id: "apr_1",
              tool: "shell",
              decision: "timeout",
              at: "2026-08-23T09:05:00Z",
            },
            {
              approval_id: "apr_2",
              tool: "web_fetch",
              decision: "deny",
              at: "2026-08-23T09:20:00Z",
            },
          ],
          state_changes: [
            {
              to: "tripped",
              reason: "3 failures in 30 minutes",
              at: "2026-08-23T08:00:00Z",
            },
            { to: "satisfied", reason: "", at: "2026-08-23T10:00:00Z" },
          ],
        },
      ],
    );
    render(<AutonomyPage />);
    await screen.findByTestId("goal-card-g_active");

    // Collapsed by default — the digest is a place you go, not a landing.
    expect(screen.queryByTestId("digest-g_active")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Last 24h/ }));
    const row = await screen.findByTestId("digest-g_active");
    expect(row.textContent).toContain("Inbox shepherd");
    expect(row.textContent).toContain("ran 3×");
    expect(row.textContent).toContain("$0.41 spent");
    // Held asks: the count from .length AND which tools waited, how each
    // ask ended.
    expect(row.textContent).toContain(
      "2 asks held: shell (timeout), web_fetch (deny)",
    );
    // State changes in words; a reasonless transition is just its state.
    expect(row.textContent).toContain("tripped — 3 failures in 30 minutes");
    expect(row.textContent).toContain("satisfied");
    // Results: one line per session — summary plus the file-harvest count,
    // and no count suffix when the ledger harvested nothing.
    expect(row.textContent).toContain("2 replies drafted · 2 files");
    expect(row.textContent).toContain("inbox at 14 unread");
    expect(row.textContent).not.toContain("inbox at 14 unread ·");
    expect(api.calls).toContain("/goals/digest?hours=24");
  });

  it("an empty digest is one quiet line", async () => {
    primeAutonomy([goal({ id: "g_active", name: "Inbox shepherd", state: "active" })]);
    render(<AutonomyPage />);
    await screen.findByTestId("goal-card-g_active");
    fireEvent.click(screen.getByRole("button", { name: /Last 24h/ }));
    await screen.findByText("Nothing ran in the last 24 hours.");
  });
});

/* ----------------------------------------------- Overview: the GoalsStrip */

describe("GoalsStrip — the forgotten-goal killer", () => {
  it("renders literally NOTHING when there are no goals", async () => {
    api.responses["/goals"] = { goals: [] };
    const { container } = render(<GoalsStrip />);
    await waitFor(() => expect(api.calls).toContain("/goals"));
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when every goal is at rest (satisfied/stopped/paused)", async () => {
    api.responses["/goals"] = {
      goals: [
        goal({ id: "g_s", name: "Done goal", state: "satisfied" }),
        goal({ id: "g_x", name: "Retired goal", state: "stopped" }),
        goal({ id: "g_p", name: "Sleeping goal", state: "paused" }),
      ],
    };
    const { container } = render(<GoalsStrip />);
    await waitFor(() => expect(api.calls).toContain("/goals"));
    expect(container.firstChild).toBeNull();
  });

  it("bad news leads: tripped/failed pills wear the warn tone and sort first", async () => {
    api.responses["/goals"] = {
      goals: [
        goal({ id: "g_a", name: "Alpha", state: "active" }),
        goal({
          id: "g_t",
          name: "Tango",
          state: "tripped",
          trip_reason: TRIP_REASON,
        }),
        goal({ id: "g_b", name: "Bravo", state: "active" }),
        goal({ id: "g_f", name: "Foxtrot", state: "failed" }),
        goal({ id: "g_done", name: "Done goal", state: "satisfied" }),
        goal({ id: "g_gone", name: "Retired goal", state: "stopped" }),
      ],
    };
    render(<GoalsStrip />);
    const strip = await screen.findByTestId("goals-strip");
    const pills = within(strip).getAllByRole("link");

    // Server order was A, T, B, F — the strip leads with the broken ones,
    // keeping server order inside each group.
    expect(pills.map((p) => p.textContent)).toEqual([
      expect.stringContaining("Tango"),
      expect.stringContaining("Foxtrot"),
      expect.stringContaining("Alpha"),
      expect.stringContaining("Bravo"),
    ]);
    expect(pills.map((p) => p.getAttribute("data-tone"))).toEqual([
      "warn",
      "warn",
      "ok",
      "ok",
    ]);
    // Every pill is a door to the Autonomy page.
    for (const p of pills) expect(p).toHaveAttribute("href", "/autonomy");
    // At-rest goals never rendered.
    expect(strip.textContent).not.toContain("Done goal");
    expect(strip.textContent).not.toContain("Retired goal");
  });

  it("each pill carries the mini spent/budget fraction and last-run age", async () => {
    api.responses["/goals"] = {
      goals: [
        goal({ id: "g_a", name: "Alpha", state: "active" }), // $0.41 of $2, ran 5m ago
        goal({
          id: "g_u",
          name: "Unbounded",
          state: "active",
          budget: { unlimited: true },
          spent: { tokens: 500, dollars: 1.23, wallclock_s: 12, iterations: 2 },
          last_run_at: null,
        }),
      ],
    };
    render(<GoalsStrip />);
    const strip = await screen.findByTestId("goals-strip");
    const [alpha, unbounded] = within(strip).getAllByRole("link");
    expect(alpha.textContent).toContain("$0.41/$2.00");
    expect(alpha.textContent).toContain("5m ago");
    expect(unbounded.textContent).toContain("$1.23/∞");
    expect(unbounded.textContent).toContain("never ran");
  });

  it("refetches on goal.* events and ONLY on goal.* events", async () => {
    api.responses["/goals"] = {
      goals: [goal({ id: "g_a", name: "Alpha", state: "active" })],
    };
    const { rerender } = render(<GoalsStrip />);
    await screen.findByTestId("goals-strip");
    expect(goalGets()).toBe(1);

    bus.events = [goalEvent("ev_1", "goal.iteration_completed")];
    rerender(<GoalsStrip />);
    await waitFor(() => expect(goalGets()).toBe(2));

    // An unrelated frame on top of the already-seen goal event: no refetch.
    bus.events = [
      {
        id: "ev_2",
        type: "tool.executed",
        session_id: "sess_1",
        ts: "2026-08-23T10:01:00Z",
        payload: { tool: "read_file", ok: true, mode: "auto" },
      },
      ...bus.events,
    ];
    rerender(<GoalsStrip />);
    await act(async () => {});
    expect(goalGets()).toBe(2);
  });
});

/* -------------------------------------------------- schedule words (pure) */

describe("scheduleWords — cron becomes words, never a lie", () => {
  it("translates the common shapes and passes words through", () => {
    expect(scheduleWords("0 9 * * *")).toBe("daily at 09:00");
    expect(scheduleWords("*/15 * * * *")).toBe("every 15 minutes");
    expect(scheduleWords("30 * * * *")).toBe("hourly at :30");
    expect(scheduleWords("0 8 * * 1")).toBe("Mondays at 08:00");
    expect(scheduleWords("")).toBe("runs when asked");
    expect(scheduleWords(null)).toBe("runs when asked");
    expect(scheduleWords("every evening after dinner")).toBe(
      "every evening after dinner",
    );
    // A shape it cannot honestly translate is shown raw, quoted — not guessed.
    expect(scheduleWords("5 4 1 * *")).toBe('on cron "5 4 1 * *"');
  });
});

/* --------------------------------------------------- Overview mount shape */

describe("Overview — the strip is mounted below PowerTips, unwrapped", () => {
  it("app/page.tsx mounts <GoalsStrip /> after <PowerTips /> and outside <Reveal>", () => {
    const src = readFileSync(join(__dirname, "..", "app", "page.tsx"), "utf8");
    const tips = src.indexOf("<PowerTips />");
    const strip = src.indexOf("<GoalsStrip />");
    expect(tips).toBeGreaterThan(-1);
    expect(strip).toBeGreaterThan(tips);
    // Null must stay null: a <Reveal> wrapper around the strip would render
    // an empty motion div (a husk) when there are no goals.
    expect(src).not.toMatch(/<Reveal>\s*<GoalsStrip \/>/);
  });
});
