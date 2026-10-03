/**
 * v1.300.0 review follow-ups — a Claude SUBSCRIPTION's dollars are a
 * list-price VALUE, never cost, on the Activity page and in the goal digest.
 *
 * Server contract (tests/test_subscription_cost_followups_v1300.py):
 *  - GET /audit: an `llm.completed` entry a flat subscription covered carries
 *    `list_price_equivalent: true`, its value in `list_price_equivalent_usd`,
 *    and `cost_usd: 0`. A metered (or older) entry is cost as before.
 *  - GET /goals/digest: `spent.words` is the server's own line — "$1.50
 *    spent", or "≈$0.42 at list price (Claude subscription)".
 *
 * Pinned here: the feed's "Cost in this view" sums METERED dollars only (a
 * flagged entry is never cost, even when a server put its value in
 * `cost_usd`), the row says "~$x list", the Activity tile says the value
 * apart, and the digest row renders the server's words.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";

const api = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return { responses: {} as Record<string, unknown>, calls: [] as string[], FakeApiError };
});

vi.mock("@/lib/api", () => ({
  ApiError: api.FakeApiError,
  API_BASE: "http://api.test",
  ijToken: () => "tok",
  wsUrl: (p: string) => `ws://api.test${p}`,
  api: () => Promise.resolve({}),
  get: (path: string) => {
    api.calls.push(path);
    const r = api.responses[path];
    if (r === undefined) return Promise.reject(new api.FakeApiError(`unmocked GET ${path}`, 0));
    return Promise.resolve(r);
  },
  post: () => Promise.resolve({}),
  put: () => Promise.resolve({}),
  del: () => Promise.resolve({}),
}));

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/components/SafetyChecks", () => ({ SafetyChecksCard: () => null }));
vi.mock("@/components/AgentHistory", () => ({ AgentHistoryCard: () => null }));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});

import { TimeTravelFeed, entryMoney, type FeedStats } from "@/components/TimeTravelFeed";
import type { AuditEntry } from "@/lib/types";

afterEach(() => {
  cleanup();
  api.responses = {};
  api.calls = [];
});

function tokenEntry(id: string, over: Record<string, unknown>): AuditEntry {
  return {
    id,
    ts: "2026-10-02T10:00:00Z",
    kind: "token",
    actor: "llm.completed",
    session_id: "s1",
    input_tokens: 100,
    output_tokens: 10,
    cost_usd: 0,
    summary: "llm.completed",
    ...over,
  } as AuditEntry;
}

const METERED = tokenEntry("e1", { cost_usd: 0.02 });
const SUBSCRIPTION = tokenEntry("e2", {
  cost_usd: 0,
  list_price_equivalent: true,
  list_price_equivalent_usd: 0.0123,
});
// A flagged entry whose value rode in cost_usd: still NOT cost.
const FLAGGED_IN_COST = tokenEntry("e3", { cost_usd: 0.5, list_price_equivalent: true });

describe("entryMoney — a flagged entry is never cost", () => {
  it("splits metered cost from a subscription's list-price value", () => {
    expect(entryMoney(METERED)).toEqual({ cost: 0.02, listPrice: 0 });
    expect(entryMoney(SUBSCRIPTION)).toEqual({ cost: 0, listPrice: 0.0123 });
    expect(entryMoney(FLAGGED_IN_COST)).toEqual({ cost: 0, listPrice: 0.5 });
    // Only a real `true` flags; junk is ignored, older entries are cost.
    expect(entryMoney(tokenEntry("e4", { cost_usd: 0.3, list_price_equivalent: "yes" }))).toEqual({
      cost: 0.3,
      listPrice: 0,
    });
    expect(entryMoney(tokenEntry("e5", { cost_usd: Number.NaN }))).toEqual({ cost: 0, listPrice: 0 });
  });
});

describe("TimeTravelFeed — 'Cost in this view' is metered money only", () => {
  it("reports metered cost and list-price value apart, and says 'list' on the row", async () => {
    api.responses["/audit?limit=50"] = {
      entries: [METERED, SUBSCRIPTION, FLAGGED_IN_COST],
      next_cursor: null,
      total: 3,
    };
    const seen: FeedStats[] = [];
    render(<TimeTravelFeed onStats={(s) => seen.push(s)} />);
    await waitFor(() => expect(seen.at(-1)?.loaded).toBe(3));
    const last = seen.at(-1)!;
    expect(last.costUsd).toBeCloseTo(0.02, 10);
    expect(last.listPriceUsd).toBeCloseTo(0.5123, 10);
    const chips = screen.getAllByTestId("list-price-chip").map((c) => c.textContent);
    expect(chips).toEqual(["~$0.012 list", "~$0.500 list"]);
    for (const chip of screen.getAllByTestId("list-price-chip")) {
      expect(chip.getAttribute("title")).toContain("not billed");
    }
    // The metered row keeps its plain dollar chip; no flagged value is shown as one.
    expect(screen.getByText("$0.020")).toBeInTheDocument();
    expect(screen.queryByText("$0.500")).toBeNull();
  });
});

describe("Activity page — the list-price value is said apart from cost", () => {
  async function renderWith(stats: FeedStats) {
    vi.resetModules();
    vi.doMock("@/components/TimeTravelFeed", () => ({
      TimeTravelFeed: ({ onStats }: { onStats?: (s: FeedStats) => void }) => {
        useEffect(() => {
          onStats?.(stats);
          // eslint-disable-next-line react-hooks/exhaustive-deps
        }, []);
        return null;
      },
    }));
    const { default: ActivityPage } = await import("@/app/activity/page");
    render(<ActivityPage />);
    vi.doUnmock("@/components/TimeTravelFeed");
  }

  const BASE = { total: 2, loaded: 2, undoable: 0, inputTokens: 10, outputTokens: 1 };

  it("shows cost as metered money and the subscription value as a separate line", async () => {
    await renderWith({ ...BASE, costUsd: 0.02, listPriceUsd: 0.42 });
    expect(await screen.findByText("$0.0200")).toBeInTheDocument();
    expect(
      screen.getByText("+ ~$0.4200 list-price value (Claude subscription, not billed)"),
    ).toBeInTheDocument();
  });

  it("says nothing extra when no subscription work is in view", async () => {
    await renderWith({ ...BASE, costUsd: 0.02, listPriceUsd: 0 });
    expect(await screen.findByText("$0.0200")).toBeInTheDocument();
    expect(screen.queryByText(/list-price value/)).toBeNull();
  });
});

describe("Autonomy page — the digest row renders the server's spent words", () => {
  function prime(digestGoals: unknown[]) {
    api.responses["/goals/digest?hours=24"] = {
      digest: { goals: digestGoals, since: "2026-10-01T10:00:00Z" },
    };
    api.responses["/autonomy"] = {
      enabled: true,
      level: "suggest",
      dry_run: false,
      kill_switch: false,
      tick_seconds: 900,
      max_actions_per_day: 20,
      max_tokens_per_day: 200000,
      used_actions_24h: 0,
      used_tokens_24h: 0,
      active_goals: 0,
      pending_proposals: 0,
    };
    api.responses["/autonomy/goals"] = { goals: [] };
    api.responses["/proposals?status=pending"] = { proposals: [] };
    api.responses["/autonomy/briefing"] = {
      text: "quiet",
      active_goals: 0,
      recent_actions: 0,
      pending_proposals: 0,
      pushed: null,
    };
    api.responses["/goals"] = { goals: [] };
  }

  async function openDigest() {
    vi.resetModules();
    const { default: AutonomyPage } = await import("@/app/autonomy/page");
    render(<AutonomyPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Last 24h/ }));
  }

  it("a subscription goal reads as a list-price value, never 'spent'", async () => {
    prime([
      {
        id: "g_sub",
        name: "Inbox shepherd",
        ran: 2,
        spent: {
          tokens: 1000,
          dollars: 0.42,
          list_price_equivalent: true,
          list_price_dollars: 0.42,
          words: "≈$0.42 at list price (Claude subscription)",
        },
        results: [],
        asks_held: [],
        state_changes: [],
      },
    ]);
    await openDigest();
    const row = await screen.findByTestId("digest-g_sub");
    expect(row.textContent).toContain("≈$0.42 at list price (Claude subscription)");
    expect(row.textContent).not.toContain("$0.42 spent");
  });

  it("an older server without words keeps the '$x spent' line", async () => {
    prime([
      {
        id: "g_old",
        name: "Old goal",
        ran: 1,
        spent: { tokens: 10, dollars: 1.5 },
        results: [],
        asks_held: [],
        state_changes: [],
      },
    ]);
    await openDigest();
    const row = await screen.findByTestId("digest-g_old");
    expect(row.textContent).toContain("$1.50 spent");
  });
});
