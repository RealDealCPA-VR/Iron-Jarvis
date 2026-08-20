import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * v1.193.0 — SHOW THE TEAM STATE THE BACKEND NOW KNOWS.
 *
 * Three things the daemon started knowing this release, each with a way to
 * fail silently in the UI:
 *
 *  - LIVENESS. `RosterEntry.activity` ("busy" | "queued" | "idle" | "unknown")
 *    says who is TAKEN. It has to read as busy in FORM (a dot in a pill), not
 *    as a word buried in a stats line — and it has to render NOTHING for
 *    idle/unknown, because the daemon cannot see delegated children and
 *    absence of a marker was never a claim that anyone is free.
 *  - THE STATS SLOT. The daemon packs liveness and the track record into ONE
 *    parenthetical ("busy, 87% over 23 runs"); the strip rendered that whole
 *    string as the stats line. Splitting them must not cost the honesty rule:
 *    a percentage still never appears without its run count.
 *  - REAL HISTORY FOR `custom:` / `remote:` AGENTS. Outcomes are keyed by
 *    roster name now, so these finally have stats. A kind-blind stats path is
 *    the guard: "no runs yet" about an agent with 23 runs is the exact defect
 *    this release exists to remove.
 *
 * ...plus the blackboard's NAME addressing: the board is addressed by name now
 * and `_to_view` serves `author_name`/`to_name`, so the panel must stop
 * showing the agent_run_id — while legacy rows that carry no name still read
 * exactly as they did.
 */

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  calls: [] as string[],
}));

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.api[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
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
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    get: (path: string) => {
      hooks.calls.push(path);
      const r = hooks.api[path];
      return r === undefined
        ? Promise.reject(new ApiError(`unmocked GET ${path}`, 404))
        : Promise.resolve(r);
    },
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
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
    "whileHover",
  ]);
  const cache = new Map<string, unknown>();
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  return {
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

import { RosterStrip, livenessOf, type RosterEntry } from "@/components/agents/RosterStrip";
import { BlackboardPanel } from "@/components/sessions/BlackboardPanel";

/* -------------------------------------------------------------- fixtures */

function entry(over: Partial<RosterEntry> = {}): RosterEntry {
  return {
    name: "builder",
    kind: "builtin",
    description: "hands-on doer",
    delegable: true,
    healthy: true,
    stats: null,
    ...over,
  };
}

/** The shape GET /agents/roster really serves: a composed `line` whose
 *  trailing parenthetical carries liveness AND the track record together. */
function line(name: string, desc: string, suffix: string): string {
  return `${name} — ${desc} ${suffix}`;
}

const pick = () => screen.getByLabelText("Choose an agent") as HTMLSelectElement;

beforeEach(() => {
  hooks.api = {};
  hooks.calls = [];
});
afterEach(() => {
  cleanup();
});

/* ------------------------------------------------ RosterStrip: liveness --- */

describe("livenessOf — the daemon's word, or no claim at all", () => {
  it("reads the explicit activity field", () => {
    expect(livenessOf(entry({ activity: "busy" }))).toBe("busy");
    expect(livenessOf(entry({ activity: "queued" }))).toBe("queued");
  });

  it("falls back to the composed line — what the endpoint actually serves today", () => {
    expect(
      livenessOf(entry({ line: line("builder", "doer", "(busy, 87% over 23 runs)") })),
    ).toBe("busy");
    expect(
      livenessOf(entry({ line: line("builder", "doer", "(queued, no runs yet)") })),
    ).toBe("queued");
  });

  it("makes NO claim for idle, unknown, absent, or a stats-only line", () => {
    expect(livenessOf(entry({ activity: "idle" }))).toBeNull();
    expect(livenessOf(entry({ activity: "unknown" }))).toBeNull();
    expect(livenessOf(entry({}))).toBeNull();
    expect(
      livenessOf(entry({ line: line("builder", "doer", "(87% over 23 runs)") })),
    ).toBeNull();
  });

  it("stays quiet for an OFFLINE remote — health is the more urgent fact", () => {
    expect(
      livenessOf(
        entry({ name: "remote:box", kind: "remote", healthy: false, activity: "busy" }),
      ),
    ).toBeNull();
  });
});

describe("RosterStrip — a busy teammate reads as busy at a glance", () => {
  const RAIL: RosterEntry[] = [
    entry({ name: "builder", activity: "idle" }),
    entry({
      name: "custom:analyst",
      kind: "dynamic",
      description: "your analyst",
      activity: "busy",
      stats: { sessions: 23, success_rate: 0.87, avg_score: 4, trend: "flat" },
      line: line("custom:analyst", "your analyst", "(busy, 87% over 23 runs)"),
    }),
    entry({
      name: "remote:opus-box",
      kind: "remote",
      description: "remote agent (http-task)",
      activity: "queued",
      line: line("remote:opus-box", "remote agent (http-task)", "(queued, 3 runs so far)"),
    }),
  ];

  it("marks every busy/queued ROW in the rail — not only the selected one", () => {
    render(<RosterStrip entries={RAIL} onSelect={() => {}} />);
    const rail = screen.getByTestId("roster-rail");
    const busy = within(rail).getByTestId("roster-activity-analyst");
    expect(busy.getAttribute("data-activity")).toBe("busy");
    expect(busy.textContent).toContain("busy");
    // FORM, not just text: the pill carries a dot, so the state reads without
    // being read. A text-only label would pass the line above and fail here.
    expect(within(busy).getByTestId("roster-activity-analyst-dot")).toBeTruthy();
    expect(
      within(rail).getByTestId("roster-activity-opus-box").getAttribute("data-activity"),
    ).toBe("queued");
    // Nobody has been picked, so nothing is "selected" — the markers are on
    // the rows regardless.
    expect(within(rail).queryByTestId("roster-activity-builder")).toBeNull();
  });

  it("says nothing about an idle agent — absence is not a claim of free", () => {
    render(<RosterStrip entries={RAIL} onSelect={() => {}} />);
    const rail = screen.getByTestId("roster-rail");
    expect(within(rail).queryByTestId("roster-activity-builder")).toBeNull();
    expect(within(rail).queryByText(/idle|unknown|free|available/i)).toBeNull();
  });

  it("marks the selected agent in the standalone (no-rail) composition too", () => {
    render(<RosterStrip entries={RAIL} />);
    fireEvent.change(pick(), { target: { value: "custom:analyst" } });
    const pillNode = screen.getByTestId("roster-activity-analyst");
    expect(pillNode.getAttribute("data-activity")).toBe("busy");
    expect(pillNode.getAttribute("title")).toContain("running a session right now");
  });

  it("keeps liveness OUT of the stats slot, and the run count IN it", () => {
    render(<RosterStrip entries={RAIL} />);
    fireEvent.change(pick(), { target: { value: "custom:analyst" } });
    // The daemon's parenthetical is "busy, 87% over 23 runs" — the stats line
    // must read as stats only...
    expect(screen.getByText("87% over 23 runs")).toBeTruthy();
    expect(screen.queryByText("busy, 87% over 23 runs")).toBeNull();
    // ...and a percentage still never appears without its sample count.
    expect(screen.queryByText(/^\s*87%\s*$/)).toBeNull();
  });

  it("an offline remote shows offline, and no busy pill", () => {
    render(
      <RosterStrip
        entries={[
          entry({
            name: "remote:dead-box",
            kind: "remote",
            description: "remote agent (http-task)",
            healthy: false,
            activity: "busy",
            line: line("remote:dead-box", "remote agent (http-task)", "(offline)"),
          }),
        ]}
      />,
    );
    expect(screen.getByText("offline")).toBeTruthy();
    expect(screen.queryByTestId("roster-activity-dead-box")).toBeNull();
  });
});

/* ------------------------------- RosterStrip: custom/remote track record --- */

describe("RosterStrip — the history custom: and remote: agents finally have", () => {
  const MEASURED: RosterEntry[] = [
    entry({
      name: "custom:analyst",
      kind: "dynamic",
      description: "your analyst",
      stats: { sessions: 23, success_rate: 0.87, avg_score: 4, trend: "up" },
    }),
    entry({
      name: "remote:opus-box",
      kind: "remote",
      description: "remote agent (http-task)",
      stats: { sessions: 1, success_rate: null, avg_score: null, trend: null },
    }),
  ];

  it("renders measured stats for a dynamic agent — never 'no runs yet'", () => {
    render(<RosterStrip entries={MEASURED} />);
    fireEvent.change(pick(), { target: { value: "custom:analyst" } });
    expect(screen.getByText("87% over 23 runs")).toBeTruthy();
    expect(screen.queryByText("no runs yet")).toBeNull();
  });

  it("renders measured stats for a remote agent, run count and all", () => {
    render(<RosterStrip entries={MEASURED} />);
    fireEvent.change(pick(), { target: { value: "remote:opus-box" } });
    expect(screen.getByText("1 run so far")).toBeTruthy();
    expect(screen.queryByText("no runs yet")).toBeNull();
  });

  it("an unmeasured agent still says so, honestly", () => {
    render(<RosterStrip entries={[entry({ name: "custom:new", kind: "dynamic" })]} />);
    expect(screen.getByText("no runs yet")).toBeTruthy();
  });
});

/* --------------------------------------------- BlackboardPanel: identity --- */

describe("BlackboardPanel — the board is addressed by NAME now", () => {
  const NAMED = {
    id: "bb_1",
    author: "run_0123456789abcdef",
    author_name: "researcher",
    kind: "message",
    to_agent: "run_fedcba9876543210",
    to_name: "custom:tax-reader",
    text: "K-1 page 12 is a scan — OCR it before you total.",
    created_at: "2026-08-19T10:05:00Z",
  };
  const LEGACY = {
    id: "bb_2",
    author: "run_alpha",
    kind: "message",
    to_agent: "run_beta",
    text: "Please re-run the failing suite",
    created_at: "2026-08-19T10:06:00Z",
  };

  it("shows the roster names the agents actually addressed each other by", async () => {
    hooks.api["/blackboard/s-root"] = { board_id: "s-root", records: [NAMED] };
    render(<BlackboardPanel sessionId="s-root" active={false} />);
    expect(await screen.findByText("researcher")).toBeTruthy();
    expect(screen.getByText("→ custom:tax-reader")).toBeTruthy();
    // The clipped run id is gone from the text — it survives in the title.
    expect(screen.queryByText(/run_0123456789/)).toBeNull();
    expect(screen.getByText("researcher").getAttribute("title")).toBe(
      "run_0123456789abcdef",
    );
  });

  it("a row written before the columns existed still reads as its run id", async () => {
    hooks.api["/blackboard/s-root"] = { board_id: "s-root", records: [LEGACY] };
    render(<BlackboardPanel sessionId="s-root" active={false} />);
    expect(await screen.findByText("run_alpha")).toBeTruthy();
    expect(screen.getByText("→ run_beta")).toBeTruthy();
  });

  it("still renders nothing for an empty board", async () => {
    hooks.api["/blackboard/s-solo"] = { board_id: "s-solo", records: [] };
    const { container } = render(
      <BlackboardPanel sessionId="s-solo" active={false} />,
    );
    await waitFor(() => expect(hooks.calls).toContain("/blackboard/s-solo"));
    expect(container.firstChild).toBeNull();
  });
});
