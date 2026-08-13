/**
 * v1.169.0 P3 — the model report card.
 *
 * Auto-tier silently judges local models on quality stats the user could
 * never see. The Connections page now shows the router's own judgment as a
 * compact report line per LOCAL model, and the TurnReceipt makes the
 * "auto-tier" reason reachable (a quiet link to Connections) without turning
 * it amber.
 *
 * What can fail silently here, and is therefore pinned with VALUES:
 *  - the three states must not blur: below-bar, clears, and
 *    not-enough-evidence each have exact wording (a swapped branch still
 *    renders a plausible line — with the wrong verdict);
 *  - the verdict word comes from the SERVER's `clears`, and the evidence
 *    check from samples < min_samples — both asserted via exact strings;
 *  - cloud providers NEVER get a report line, even if a row leaks into the
 *    payload (the bar judges local models only);
 *  - the auto-tier receipt stays QUIET (no amber) — the link must not
 *    escalate a user-configured automation into a warning — and lives in the
 *    expanded panel, never nested inside the toggle button.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";

const hooks = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError: FakeApiError,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => null,
    get: (path: string) => Promise.resolve(hooks.responses[path] ?? {}),
    post: () => Promise.resolve({}),
    put: () => Promise.resolve({}),
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
  };
});

vi.mock("@/lib/useApi", () => ({
  useApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
  usePolledApi: (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  }),
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    health: hooks.responses["health"] ?? null,
    refresh: () => {},
  }),
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
    "whileTap",
    "whileInView",
    "viewport",
  ]);
  const tagFor =
    (tag: string) => (props: Record<string, unknown>) => {
      const rest: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(props)) {
        if (!MOTION_ONLY.has(k)) rest[k] = v;
      }
      return createElement(tag, rest);
    };
  const cache = new Map<string, unknown>();
  return {
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
    motion: new Proxy({} as Record<string, unknown>, {
      get: (_t, tag) => {
        const key = String(tag);
        if (!cache.has(key)) cache.set(key, tagFor(key));
        return cache.get(key);
      },
    }),
  };
});

// Out of scope for the report-card assertions: REST hookups have their own
// data lifecycle, and the brand marks pull the whole simple-icons set.
vi.mock("@/components/connections/RestHookups", () => ({
  RestHookups: () => null,
}));
vi.mock("@/components/BrandGlyph", () => ({
  ProviderMark: () => null,
}));

import ConnectionsPage from "@/app/connections/page";
import { TurnReceipt } from "@/components/chat/TurnReceipt";

afterEach(() => {
  cleanup();
  hooks.responses = {};
});

/* ------------------------------------------------------------------ fixtures */

interface QRow {
  provider: string;
  model: string;
  task_class: string | null;
  avg: number | null;
  samples: number;
  bar: number;
  min_samples: number;
  clears: boolean;
}

function qrow(over: Partial<QRow>): QRow {
  return {
    provider: "ollama",
    model: "qwen2.5:14b",
    task_class: null,
    avg: null,
    samples: 0,
    bar: 0.75,
    min_samples: 3,
    clears: false,
    ...over,
  };
}

function conn(provider: string, over: Record<string, unknown> = {}) {
  return {
    provider,
    display_name: provider,
    method: "api_key",
    connected: false,
    status: "not_connected",
    account: null,
    ...over,
  };
}

function seed({
  rows = [] as QRow[],
  connections = [conn("anthropic"), conn("custom"), conn("mock")],
  fleetNodes = [] as unknown[],
} = {}) {
  hooks.responses["/connections"] = { connections };
  hooks.responses["/routing/quality"] = {
    bar: 0.75,
    min_samples: 3,
    rows,
  };
  hooks.responses["/fleet"] = { nodes: fleetNodes };
  hooks.responses["health"] = {
    default_provider: "ollama",
    providers: [
      { provider: "ollama", available: true },
      { provider: "claude-cli", available: true },
    ],
  };
}

/* ----------------------------------------------- the three states, verbatim */

describe("Connections — the model report line (local providers)", () => {
  it("below the bar: exact avg, sample count, bar, and the routes-up consequence", () => {
    seed({
      rows: [
        qrow({ avg: 0.6222, samples: 9, clears: false }),
        // A class row AGREEING with the aggregate keeps the categorical
        // line (and never renders as a second line of its own).
        qrow({ task_class: "builder", avg: 0.6, samples: 5, clears: false }),
      ],
    });
    render(<ConnectionsPage />);
    const report = screen.getByTestId("model-report-ollama");
    expect(report.textContent).toContain(
      "avg 0.62 over 9 sessions — below your 0.75 bar, so eligible work routes up",
    );
    // ONE line: the builder task-class row must not render a second one.
    expect(report.querySelectorAll("p")).toHaveLength(1);
    expect(report.textContent).not.toContain("0.60");
  });

  it("clears the bar: the stays-local wording, from the SERVER's clears", () => {
    seed({ rows: [qrow({ avg: 0.81, samples: 9, clears: true })] });
    render(<ConnectionsPage />);
    expect(
      screen.getByTestId("model-report-ollama").textContent,
    ).toContain(
      "avg 0.81 over 9 sessions — clears your 0.75 bar, so eligible work can stay local",
    );
  });

  it("not enough evidence: says N of M sessions and never a verdict", () => {
    seed({ rows: [qrow({ avg: 0.9, samples: 2, clears: false })] });
    render(<ConnectionsPage />);
    const text = screen.getByTestId("model-report-ollama").textContent ?? "";
    expect(text).toContain("not enough evidence yet (2 of 3 sessions)");
    // A stellar avg over too few sessions must not leak a verdict word.
    expect(text).not.toContain("clears");
    expect(text).not.toContain("below");
  });

  it("singularizes the session count (1 of 1 session)", () => {
    seed({ rows: [qrow({ avg: null, samples: 0, min_samples: 1 })] });
    render(<ConnectionsPage />);
    expect(
      screen.getByTestId("model-report-ollama").textContent,
    ).toContain("not enough evidence yet (0 of 1 session)");
  });

  it("no report rows -> no line at all (the row keeps its quiet idiom)", () => {
    seed({ rows: [] });
    render(<ConnectionsPage />);
    expect(screen.queryByTestId("model-report-ollama")).toBeNull();
  });

  it("two models on one provider both render, prefixed by model id", () => {
    seed({
      rows: [
        qrow({ model: "qwen2.5:14b", avg: 0.9, samples: 4, clears: true }),
        qrow({ model: "gpt-oss-120b", avg: 0.5, samples: 4, clears: false }),
      ],
    });
    render(<ConnectionsPage />);
    const report = screen.getByTestId("model-report-ollama");
    expect(report.querySelectorAll("p")).toHaveLength(2);
    expect(report.textContent).toContain("qwen2.5:14b:");
    expect(report.textContent).toContain("gpt-oss-120b:");
    expect(report.textContent).toContain("avg 0.90 over 4 sessions — clears");
    expect(report.textContent).toContain("avg 0.50 over 4 sessions — below");
  });
});

describe("Connections — diverging task classes never collapse into one verdict", () => {
  // The router NEVER judges the aggregate: every live call carries a task
  // class ("chat" or the agent type). When judged classes disagree with the
  // aggregate verdict, a categorical consequence would be false for some of
  // them — the line must qualify per class instead.

  it("aggregate below the bar but a class clears: consequence per class", () => {
    seed({
      rows: [
        qrow({ avg: 0.7, samples: 12, clears: false }),
        qrow({ task_class: "chat", avg: 0.9, samples: 6, clears: true }),
        qrow({ task_class: "builder", avg: 0.5, samples: 6, clears: false }),
      ],
    });
    render(<ConnectionsPage />);
    const text = screen.getByTestId("model-report-ollama").textContent ?? "";
    expect(text).toContain(
      "avg 0.70 over 12 sessions — clears your 0.75 bar for chat work, which can stay local; below it for builder work, which routes up",
    );
    // The categorical single-consequence claims must be GONE.
    expect(text).not.toContain("so eligible work routes up");
    expect(text).not.toContain("so eligible work can stay local");
  });

  it("aggregate clears while the only judged class fails: no stay-local claim", () => {
    seed({
      rows: [
        qrow({ avg: 0.8, samples: 12, clears: true }),
        qrow({ task_class: "builder", avg: 0.5, samples: 6, clears: false }),
      ],
    });
    render(<ConnectionsPage />);
    const text = screen.getByTestId("model-report-ollama").textContent ?? "";
    expect(text).toContain(
      "avg 0.80 over 12 sessions — below your 0.75 bar for builder work, which routes up",
    );
    expect(text).not.toContain("can stay local");
  });

  it("only a clearing class diverges: names it, and routes-up for the rest", () => {
    seed({
      rows: [
        qrow({ avg: 0.6222, samples: 9, clears: false }),
        qrow({ task_class: "builder", avg: 0.9, samples: 5, clears: true }),
      ],
    });
    render(<ConnectionsPage />);
    const report = screen.getByTestId("model-report-ollama");
    expect(report.textContent).toContain(
      "avg 0.62 over 9 sessions — clears your 0.75 bar for builder work, which can stay local; other eligible work routes up",
    );
    expect(report.querySelectorAll("p")).toHaveLength(1);
  });

  it("the tooltip carries every class's verdict, including thin evidence", () => {
    seed({
      rows: [
        qrow({ avg: 0.8, samples: 12, clears: true }),
        qrow({ task_class: "builder", avg: 0.5, samples: 6, clears: false }),
        qrow({ task_class: "researcher", avg: 0.9, samples: 2, clears: false }),
      ],
    });
    render(<ConnectionsPage />);
    const title =
      screen
        .getByTestId("model-report-ollama")
        .querySelector("p")
        ?.getAttribute("title") ?? "";
    expect(title).toContain("builder: avg 0.50 (below the bar)");
    expect(title).toContain("researcher: not enough evidence (2 of 3)");
  });

  it("a class with too little evidence never triggers the qualified line", () => {
    seed({
      rows: [
        qrow({ avg: 0.8, samples: 12, clears: true }),
        // clears=false only because of the evidence gate — not a verdict.
        qrow({ task_class: "researcher", avg: 0.9, samples: 2, clears: false }),
      ],
    });
    render(<ConnectionsPage />);
    expect(
      screen.getByTestId("model-report-ollama").textContent,
    ).toContain(
      "avg 0.80 over 12 sessions — clears your 0.75 bar, so eligible work can stay local",
    );
  });
});

describe("Connections — the displayed avg never contradicts the verdict", () => {
  it("an avg that toFixed(2) would round ONTO the bar gains a decimal", () => {
    // 0.7477 -> "0.75" would read "avg 0.75 … below your 0.75 bar" — the
    // verdict is right (server compares unrounded), the evidence would lie.
    seed({ rows: [qrow({ avg: 0.7477, samples: 5, clears: false })] });
    render(<ConnectionsPage />);
    const text = screen.getByTestId("model-report-ollama").textContent ?? "";
    expect(text).toContain(
      "avg 0.748 over 5 sessions — below your 0.75 bar, so eligible work routes up",
    );
    expect(text).not.toContain("avg 0.75 ");
  });

  it("an avg exactly at the bar keeps the plain 2dp display", () => {
    seed({ rows: [qrow({ avg: 0.75, samples: 3, clears: true })] });
    render(<ConnectionsPage />);
    expect(
      screen.getByTestId("model-report-ollama").textContent,
    ).toContain("avg 0.75 over 3 sessions — clears your 0.75 bar");
  });
});

describe("Connections — cloud providers never get a report line", () => {
  it("a leaked cloud row (claude-cli, openai) renders nowhere", () => {
    seed({
      rows: [
        qrow({ provider: "claude-cli", avg: 0.9, samples: 9, clears: true }),
        qrow({ provider: "openai", avg: 0.9, samples: 9, clears: true }),
      ],
    });
    render(<ConnectionsPage />);
    expect(screen.queryByTestId("model-report-claude-cli")).toBeNull();
    expect(screen.queryByTestId("model-report-openai")).toBeNull();
    expect(screen.queryByText(/clears your 0\.75 bar/)).toBeNull();
  });
});

describe("Connections — fleet endpoints on the custom card", () => {
  it("an endpoint row shows the report for its fleet-<id> provider", async () => {
    seed({
      rows: [
        qrow({
          provider: "fleet-abc",
          model: "gpt-oss-120b",
          avg: 0.66,
          samples: 7,
          clears: false,
        }),
      ],
      fleetNodes: [
        {
          node: {
            id: "abc",
            label: "Spark",
            base_url: "http://spark:11434/v1",
            source: "user",
            routable: true,
            default_model: "gpt-oss-120b",
          },
        },
      ],
    });
    render(<ConnectionsPage />);
    // reloadEndpoints() fetches /fleet asynchronously — wait for the row.
    const report = await screen.findByTestId("model-report-fleet-abc");
    expect(report.textContent).toContain(
      "avg 0.66 over 7 sessions — below your 0.75 bar, so eligible work routes up",
    );
  });

  it("a zero-sample fleet row renders the honest not-enough-evidence state", async () => {
    // The backend seeds zero-sample rows for routable fleet nodes — the
    // endpoint block must render them as "0 of N", never silent absence.
    seed({
      rows: [
        qrow({
          provider: "fleet-abc",
          model: "gpt-oss-120b",
          avg: null,
          samples: 0,
          clears: false,
        }),
      ],
      fleetNodes: [
        {
          node: {
            id: "abc",
            label: "Spark",
            base_url: "http://spark:11434/v1",
            source: "user",
            routable: true,
            default_model: "gpt-oss-120b",
          },
        },
      ],
    });
    render(<ConnectionsPage />);
    const report = await screen.findByTestId("model-report-fleet-abc");
    expect(report.textContent).toContain(
      "not enough evidence yet (0 of 3 sessions)",
    );
  });
});

describe("Connections — the line lives on the provider's own card too", () => {
  it("a local provider's ConnectionCard carries a card-scoped line; cloud cards stay silent", () => {
    seed({
      rows: [
        qrow({
          provider: "custom",
          model: "default",
          avg: 0.81,
          samples: 9,
          clears: true,
        }),
      ],
    });
    render(<ConnectionsPage />);
    expect(
      screen.getByTestId("model-report-card-custom").textContent,
    ).toContain("clears your 0.75 bar");
    // Cloud cards render no line even though they get the same rows prop.
    expect(screen.queryByTestId("model-report-card-anthropic")).toBeNull();
  });

  it("a provider on BOTH surfaces (its card + the CLI-tools row) renders unique testids", () => {
    // No ollama ConnectionCard exists in today's daemon (/connections has no
    // ollama spec) — but if one ever ships, the line must appear on it WITHOUT
    // colliding with the CLI-row instance.
    seed({
      rows: [qrow({ avg: 0.81, samples: 9, clears: true })],
      connections: [conn("ollama"), conn("custom"), conn("mock")],
    });
    render(<ConnectionsPage />);
    // getByTestId throws on duplicates — each surface owns its own id.
    expect(
      screen.getByTestId("model-report-card-ollama").textContent,
    ).toContain("clears your 0.75 bar");
    expect(
      screen.getByTestId("model-report-ollama").textContent,
    ).toContain("clears your 0.75 bar");
  });
});

/* --------------------------------------------- TurnReceipt: auto-tier link */

const AUTO_TIER = {
  requested: "",
  provider: "ollama",
  model: "qwen2.5:14b",
  reason: "auto-tier",
};

describe("TurnReceipt — auto-tier explanation is reachable, quietly", () => {
  it("expanded, the auto-tier reason is a link to /connections", () => {
    render(<TurnReceipt route={AUTO_TIER} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    const link = screen.getByRole("link", { name: "auto-tier" });
    expect(link.getAttribute("href")).toBe("/connections");
    // The pointer to the report card, not a bare unexplained link.
    expect(link.getAttribute("title")).toContain("quality");
    // Quiet idiom preserved: nothing amber about a configured automation.
    expect(link.className).not.toContain("amber");
    expect(document.querySelector(".text-amber-300")).toBeNull();
  });

  it("stays quiet collapsed: no warning, and no link nested in the toggle", () => {
    render(<TurnReceipt route={AUTO_TIER} />);
    const toggle = screen.getByRole("button", { expanded: false });
    expect(toggle.textContent).toContain("ollama");
    expect(screen.queryByText(/answered by/)).toBeNull();
    expect(document.querySelector(".text-amber-300")).toBeNull();
    // Collapsed line lives inside the toggle button — an anchor there would
    // be illegal DOM nesting (and the existing nesting test would miss it,
    // since it never renders an auto-tier route).
    expect(toggle.querySelector("a")).toBeNull();
    expect(document.querySelector("a")).toBeNull();
  });

  it("expanded, the link is OUTSIDE the toggle button", () => {
    render(<TurnReceipt route={AUTO_TIER} />);
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    const toggle = screen.getByRole("button", { expanded: true });
    expect(toggle.querySelector("a")).toBeNull();
    expect(screen.getByRole("link", { name: "auto-tier" })).toBeTruthy();
  });

  it("every other reason renders as plain text, no link", () => {
    for (const reason of ["default", "explicit", "local-oracle"]) {
      const { unmount } = render(
        <TurnReceipt route={{ provider: "ollama", reason }} />,
      );
      fireEvent.click(screen.getByRole("button", { expanded: false }));
      expect(screen.getByText(`(${reason})`)).toBeTruthy();
      expect(document.querySelector("a")).toBeNull();
      unmount();
    }
  });
});
