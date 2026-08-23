/**
 * v1.201.0 A4 — the Capability Envelope UI. Restructured in v1.204.0 from
 * LIVE USER FEEDBACK on the shipped surfaces.
 *
 * What can lie silently here, and is therefore pinned:
 *  - PLACEMENT (v1.204.0): measurements live in their OWN section BELOW the
 *    connect cards — the tiles carry the Measure button + provenance chip
 *    only, never the envelope scorecard (it made the custom card enormous).
 *    The section shows ONLY endpoints with a stored profile; nothing
 *    measured -> the section is absent entirely, not an empty husk.
 *  - CONTEXT HONESTY (v1.204.0): the app budgets with `effective_window`,
 *    so THAT renders with its source in words ("from the endpoint" /
 *    "pinned by you" / "measured"). The profile's floor context (8192/4096)
 *    NEVER renders as if it were the operating window — unmeasured context
 *    says "not yet deep-measured" instead.
 *  - FLOORED-RUNG HONESTY (v1.204.0): score 0.0 with the path ABSENT from
 *    measured_fields is a REFUSAL ("the endpoint refused them" + the
 *    probe_notes reason), never a bare 0.00 score bar the user reads as
 *    their model scoring zero. A truly SCORED 0.0 keeps the bar. And the
 *    card LEADS with a plain-language verdict (native / guided JSON /
 *    step-by-step).
 *  - provenance next to every value: a SELECTED ladder must be impossible on
 *    an unprobed profile (IronCore's report card once showed a green SELECTED
 *    on one — that class of lie is banned). Seeded never dresses up as
 *    evidence; ladder rows render only for measured sources.
 *  - trusted (frontier) models get NO card and NO Measure — a scorecard on a
 *    model nobody measured is the same lie inverted.
 *  - Measure POSTs the probe, shows a measuring state, renders 400/409
 *    details VERBATIM (never paraphrased into success), and refetches when
 *    `envelope.probe_completed` arrives on the live stream.
 *  - the URL helper percent-encodes ":" in model ids — ollama ids carry it.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
  events: [] as Array<{
    id: string;
    type: string;
    session_id: string | null;
    ts: string;
    payload: Record<string, unknown>;
  }>,
}));

vi.mock("@/lib/api", () => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: FakeApiError,
    API_BASE: "http://127.0.0.1:8787",
    ijToken: () => null,
    get: vi.fn((path: string) => Promise.resolve(hooks.responses[path] ?? {})),
    post: vi.fn(() => Promise.resolve({ started: true })),
    put: vi.fn(() => Promise.resolve({})),
    patch: vi.fn(() => Promise.resolve({})),
    del: vi.fn(() => Promise.resolve({})),
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

// The live stream, delivered synchronously from the test's mutable buffer —
// push a completion event and rerender to simulate its arrival.
vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: hooks.events, connected: true }),
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

vi.mock("@/components/connections/RestHookups", () => ({
  RestHookups: () => null,
}));
vi.mock("@/components/BrandGlyph", () => ({
  ProviderMark: () => null,
}));

import ConnectionsPage from "@/app/connections/page";
import { envelopeUrl, EnvelopeRowControls } from "@/components/connections/EnvelopeCard";
import { get, post, ApiError } from "@/lib/api";

beforeEach(() => {
  hooks.responses = {};
  hooks.events = [];
  vi.mocked(get).mockClear();
  vi.mocked(post).mockClear();
});

afterEach(() => {
  cleanup();
});

/* ------------------------------------------------------------------ fixtures */

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

/** A judged ollama quality row — puts ollama/qwen2.5:14b on the measurable
 *  list (its report line, its CLI-row Measure, and the measured section). */
function qrow(over: Record<string, unknown> = {}) {
  return {
    provider: "ollama",
    model: "qwen2.5:14b",
    task_class: null,
    avg: 0.81,
    samples: 9,
    bar: 0.75,
    min_samples: 3,
    clears: true,
    ...over,
  };
}

/** Every field a full probe writes — the per-field provenance the floored-
 *  rung distinction keys off. */
const ALL_FIELDS = [
  "context_window",
  "honest_context",
  "chars_per_token",
  "vision",
  "tool_protocols.native",
  "tool_protocols.strict_json",
  "json_adherence",
  "coherence_horizon",
];

function profile(over: Record<string, unknown> = {}) {
  return {
    model_id: "qwen2.5:14b",
    provider: "ollama",
    source: "probed",
    probed_at: "2026-08-22T10:00:00Z",
    context_window: 262144,
    honest_context: 49152,
    chars_per_token: 3.6,
    vision: false,
    tool_protocols: { native: 0.98, strict_json: 0.94 },
    json_adherence: 0.96,
    coherence_horizon: 9,
    measured_fields: ALL_FIELDS,
    probe_notes: {},
    ...over,
  };
}

/** A full GET /envelope answer: stored profile + the window the app REALLY
 *  budgets with. */
function envelope(over: Record<string, unknown> = {}) {
  return {
    profile: profile(),
    trusted: false,
    effective_window: { value: 49152, source: "measured" },
    ...over,
  };
}

function seed({
  rows = [qrow()] as unknown[],
  connections = [conn("anthropic"), conn("custom"), conn("mock")],
  fleetNodes = [] as unknown[],
} = {}) {
  hooks.responses["/connections"] = { connections };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows };
  hooks.responses["/fleet"] = { nodes: fleetNodes };
  hooks.responses["health"] = {
    default_provider: "ollama",
    providers: [
      { provider: "ollama", available: true },
      { provider: "claude-cli", available: true },
    ],
  };
}

const OLLAMA_ENV = "/envelope/ollama/qwen2.5%3A14b";
const FLEET_ENV = "/envelope/fleet-abc/gpt-oss-120b";
const OLLAMA_CARD = "measured-ollama-qwen2.5:14b";

const FLEET_NODE = {
  node: {
    id: "abc",
    label: "Spark",
    base_url: "http://spark:11434/v1",
    source: "user",
    routable: true,
    default_model: "gpt-oss-120b",
  },
};

function completionEvent(payload: Record<string, unknown>, id = "ev-1") {
  return {
    id,
    type: "envelope.probe_completed",
    session_id: null,
    ts: "2026-08-22T10:05:00Z",
    payload,
  };
}

function getCallsFor(path: string): number {
  return vi.mocked(get).mock.calls.filter((c) => c[0] === path).length;
}

/** Render the page with one measured ollama profile and return its card. */
async function renderOllamaCard(env: Record<string, unknown>) {
  seed();
  hooks.responses[OLLAMA_ENV] = env;
  render(<ConnectionsPage />);
  return await screen.findByTestId(OLLAMA_CARD);
}

/* ----------------------------------------------------------- the URL helper */

describe("envelopeUrl", () => {
  it("percent-encodes the model id as a path segment (ollama ids carry ':')", () => {
    expect(envelopeUrl("ollama", "qwen2.5:14b")).toBe("/envelope/ollama/qwen2.5%3A14b");
    expect(envelopeUrl("fleet-abc", "gpt-oss-120b")).toBe("/envelope/fleet-abc/gpt-oss-120b");
  });
});

/* ------------------------------------- the measured section, below the cards */

describe("Measured endpoints — their own section BELOW the connect cards", () => {
  it("renders the card: verdict first, effective window with source, scores behind expand", async () => {
    const card = await renderOllamaCard(envelope());
    const sec = screen.getByTestId("measured-endpoints");

    // BELOW the connect cards: the section follows the custom card in the DOM.
    const connCard = document.getElementById("conn-card-custom");
    expect(connCard).toBeTruthy();
    expect(
      connCard!.compareDocumentPosition(sec) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    // Provenance badge next to the values it vouches for.
    expect(within(card).getByTestId(`${OLLAMA_CARD}-source`).textContent).toBe("measured");

    // The plain-language verdict LEADS.
    expect(within(card).getByTestId(`${OLLAMA_CARD}-verdict`).textContent).toBe(
      "fully usable — native tool calls",
    );

    // The window the app budgets with, source in words — never the floor.
    expect(card.textContent).toContain("context window: 49,152 (measured)");
    expect(card.textContent).not.toContain("not yet deep-measured");

    // Scores are behind the expand.
    expect(within(card).queryByTestId(`${OLLAMA_CARD}-detail`)).toBeNull();
    fireEvent.click(within(card).getByTestId(`${OLLAMA_CARD}-expand`));
    const detail = within(card).getByTestId(`${OLLAMA_CARD}-detail`);
    const lt = detail.textContent ?? "";
    expect(lt).toContain("native tool calls");
    expect(lt).toContain("0.98");
    expect(lt).toContain("(needs 0.95)");
    expect(lt).toContain("strict JSON (constrained)");
    expect(lt).toContain("0.94");
    expect(lt).toContain("ok, fallback");
    expect(lt).toContain("floor (always works)");
    expect(lt.match(/SELECTED/g)).toHaveLength(1);
    expect(lt).toContain("honest context 49,152 of 262,144 advertised (19%)");
    expect(lt).toContain("big gap");
    expect(lt).toContain("3.6 chars/token");
    expect(lt).toContain("JSON adherence 0.96");
    expect(lt).toContain("coherent to ~9 turns");
    expect(lt).toContain("vision no");

    // The Measure affordance STAYS on the endpoint row (small).
    expect(screen.getByTestId("measure-ollama-qwen2.5:14b")).toBeTruthy();
  });

  it("the connect tiles no longer contain the envelope section", async () => {
    await renderOllamaCard(envelope());

    // The old in-tile section is GONE, by testid and by content.
    expect(screen.queryByTestId("envelope-ollama-qwen2.5:14b")).toBeNull();
    const report = screen.getByTestId("model-report-ollama");
    expect(report.querySelector('[data-testid^="envelope-"]')).toBeNull();
    expect(report.querySelector('[data-testid^="measured-"]')).toBeNull();
    expect(report.textContent).not.toContain("envelope");
    // And no measured card leaks INTO a connect card.
    expect(
      document.getElementById("conn-card-custom")!.querySelector('[data-testid^="measured-"]'),
    ).toBeNull();
  });

  it("nothing measured -> the section is absent entirely (no husk)", async () => {
    seed();
    hooks.responses[OLLAMA_ENV] = {}; // no stored profile
    render(<ConnectionsPage />);
    await waitFor(() => expect(getCallsFor(OLLAMA_ENV)).toBeGreaterThan(0));
    expect(screen.queryByTestId("measured-endpoints")).toBeNull();
  });

  it("a floor-default profile does not summon the section", async () => {
    seed();
    hooks.responses[OLLAMA_ENV] = {
      profile: profile({
        source: "default",
        probed_at: null,
        tool_protocols: null,
        measured_fields: [],
      }),
      trusted: false,
      effective_window: { value: 131072, source: "endpoint" },
    };
    render(<ConnectionsPage />);
    await waitFor(() => expect(getCallsFor(OLLAMA_ENV)).toBeGreaterThan(0));
    expect(screen.queryByTestId("measured-endpoints")).toBeNull();
  });

  it("a trusted model gets no card and no Measure", async () => {
    seed();
    hooks.responses[OLLAMA_ENV] = { profile: profile(), trusted: true };
    render(<ConnectionsPage />);
    await waitFor(() => expect(getCallsFor(OLLAMA_ENV)).toBeGreaterThan(0));
    expect(screen.queryByTestId("measured-endpoints")).toBeNull();
    expect(screen.queryByText(/SELECTED/)).toBeNull();
    await waitFor(() =>
      expect(screen.queryByTestId("measure-ollama-qwen2.5:14b")).toBeNull(),
    );
  });

  it("a stored endpoint's card carries its node label and model", async () => {
    seed({ rows: [], fleetNodes: [FLEET_NODE] });
    hooks.responses[FLEET_ENV] = envelope({
      profile: profile({ provider: "fleet-abc", model_id: "gpt-oss-120b" }),
    });
    render(<ConnectionsPage />);
    const card = await screen.findByTestId("measured-fleet-abc-gpt-oss-120b");
    expect(card.textContent).toContain("Spark");
    expect(card.textContent).toContain("gpt-oss-120b");
  });
});

/* -------------------------------------- context honesty: effective window */

describe("Measured endpoints — the effective window, never the floor", () => {
  it("endpoint-sourced window says so; unmeasured context never prints floors", async () => {
    const card = await renderOllamaCard({
      profile: profile({
        source: "seeded",
        probed_at: null,
        context_window: 8192, // the profile's FLOOR context —
        honest_context: 4096, // — must never render as the window
        tool_protocols: null,
        json_adherence: null,
        coherence_horizon: null,
        measured_fields: [],
      }),
      trusted: false,
      effective_window: { value: 131072, source: "endpoint" },
    });
    const text = card.textContent ?? "";
    expect(text).toContain("context window: 131,072 (from the endpoint)");
    expect(text).toContain("not yet deep-measured; the app uses the endpoint's value");
    expect(text).not.toContain("8,192");
    expect(text).not.toContain("4,096");
    expect(within(card).getByTestId(`${OLLAMA_CARD}-source`).textContent).toBe("seeded");
  });

  it("a pinned window says pinned by you", async () => {
    const card = await renderOllamaCard({
      profile: profile({
        source: "seeded",
        probed_at: null,
        context_window: 8192,
        honest_context: 4096,
        tool_protocols: null,
        measured_fields: [],
      }),
      trusted: false,
      effective_window: { value: 200000, source: "pin" },
    });
    const text = card.textContent ?? "";
    expect(text).toContain("context window: 200,000 (pinned by you)");
    expect(text).toContain("not yet deep-measured; the app uses your pinned value");
    expect(text).not.toContain("8,192");
  });

  it("a partial probe that measured tools but NOT context still says not-deep-measured", async () => {
    const card = await renderOllamaCard({
      profile: profile({
        source: "partial",
        context_window: 8192,
        honest_context: 4096,
        measured_fields: ["tool_protocols.native", "tool_protocols.strict_json"],
      }),
      trusted: false,
      effective_window: { value: 131072, source: "endpoint" },
    });
    const text = card.textContent ?? "";
    expect(text).toContain("context window: 131,072 (from the endpoint)");
    expect(text).toContain("not yet deep-measured; the app uses the endpoint's value");
    expect(text).not.toContain("8,192");
  });
});

/* ------------------------------------------- floored rungs vs real scores */

describe("Measured endpoints — a floored rung is a refusal, not a score", () => {
  const FLOORED_NATIVE = {
    profile: profile({
      tool_protocols: { native: 0.0, strict_json: 0.94 },
      // native is ABSENT from measured_fields: the prober FLOORED it.
      measured_fields: ALL_FIELDS.filter((f) => f !== "tool_protocols.native"),
      probe_notes: {
        "tool_protocols.native": "native trials errored: HTTP 400 — tool role unsupported",
      },
    }),
    trusted: false,
    effective_window: { value: 49152, source: "measured" },
  };

  it("floored native: verdict leads with guided JSON; expand says refused + the note, never 0.00", async () => {
    const card = await renderOllamaCard(FLOORED_NATIVE);
    expect(within(card).getByTestId(`${OLLAMA_CARD}-verdict`).textContent).toBe(
      "fully usable — tool calls run as guided JSON",
    );
    fireEvent.click(within(card).getByTestId(`${OLLAMA_CARD}-expand`));
    const detail = within(card).getByTestId(`${OLLAMA_CARD}-detail`);
    const native = detail.querySelector('[data-rung="native"]');
    expect(native?.textContent).toContain("native tool calls: the endpoint refused them");
    expect(native?.textContent).toContain(
      "native trials errored: HTTP 400 — tool role unsupported",
    );
    expect(native?.textContent).not.toContain("0.00");
    expect(detail.textContent).not.toContain("0.00");
    // strict_json is a REAL score and is SELECTED.
    const strict = detail.querySelector('[data-rung="strict_json"]');
    expect(strict?.textContent).toContain("0.94");
    expect(strict?.textContent).toContain("SELECTED");
  });

  it("a truly SCORED native 0.0 (path IN measured_fields) keeps the score bar", async () => {
    const card = await renderOllamaCard({
      profile: profile({
        tool_protocols: { native: 0.0, strict_json: 0.94 },
        measured_fields: ALL_FIELDS, // native was really scored 0.0
      }),
      trusted: false,
      effective_window: { value: 49152, source: "measured" },
    });
    fireEvent.click(within(card).getByTestId(`${OLLAMA_CARD}-expand`));
    const detail = within(card).getByTestId(`${OLLAMA_CARD}-detail`);
    const native = detail.querySelector('[data-rung="native"]');
    expect(native?.textContent).toContain("0.00");
    expect(native?.textContent).toContain("below bar (0.95 short)");
    expect(detail.textContent).not.toContain("refused");
  });
});

/* ------------------------------------------------- the plain verdict line */

describe("Measured endpoints — the verdict, all three ways", () => {
  it("native clears its bar: fully usable — native tool calls", async () => {
    const card = await renderOllamaCard(envelope());
    expect(within(card).getByTestId(`${OLLAMA_CARD}-verdict`).textContent).toBe(
      "fully usable — native tool calls",
    );
  });

  it("only strict_json clears: fully usable — tool calls run as guided JSON", async () => {
    const card = await renderOllamaCard(
      envelope({ profile: profile({ tool_protocols: { native: 0.8, strict_json: 0.93 } }) }),
    );
    expect(within(card).getByTestId(`${OLLAMA_CARD}-verdict`).textContent).toBe(
      "fully usable — tool calls run as guided JSON",
    );
    fireEvent.click(within(card).getByTestId(`${OLLAMA_CARD}-expand`));
    const detail = within(card).getByTestId(`${OLLAMA_CARD}-detail`);
    const native = detail.querySelector('[data-rung="native"]');
    expect(native?.textContent).toContain("below bar (0.15 short)");
    expect(native?.textContent).not.toContain("SELECTED");
    expect(detail.querySelector('[data-rung="strict_json"]')?.textContent).toContain("SELECTED");
  });

  it("nothing clears: limited — runs step-by-step with verification", async () => {
    const card = await renderOllamaCard(
      envelope({ profile: profile({ tool_protocols: { native: 0.4, strict_json: 0.5 } }) }),
    );
    expect(within(card).getByTestId(`${OLLAMA_CARD}-verdict`).textContent).toBe(
      "limited — runs step-by-step with verification",
    );
    fireEvent.click(within(card).getByTestId(`${OLLAMA_CARD}-expand`));
    expect(
      within(card)
        .getByTestId(`${OLLAMA_CARD}-detail`)
        .querySelector('[data-rung="text_floor"]')?.textContent,
    ).toContain("SELECTED — no rung cleared its bar");
  });
});

/* ----------------------------------------------------- provenance honesty */

describe("Measured endpoints — unmeasured provenance never dresses up as evidence", () => {
  it("a seeded profile: 'seeded' badge, no SELECTED, no ladder — expand says not verified", async () => {
    const card = await renderOllamaCard({
      profile: profile({
        source: "seeded",
        probed_at: null,
        honest_context: null,
        context_window: 32768,
        tool_protocols: null,
        json_adherence: null,
        coherence_horizon: null,
        measured_fields: [],
      }),
      trusted: false,
      effective_window: { value: 32768, source: "endpoint" },
    });
    expect(within(card).getByTestId(`${OLLAMA_CARD}-source`).textContent).toBe("seeded");
    expect(within(card).getByTestId(`${OLLAMA_CARD}-verdict`).textContent).toBe(
      "reported by the endpoint — not verified yet",
    );
    expect(card.textContent).not.toContain("SELECTED");
    fireEvent.click(within(card).getByTestId(`${OLLAMA_CARD}-expand`));
    expect(within(card).queryByTestId(`${OLLAMA_CARD}-ladder`)).toBeNull();
    expect(within(card).getByTestId(`${OLLAMA_CARD}-detail`).textContent).toContain(
      "capabilities reported by the endpoint, not verified — Measure runs the real probes",
    );
  });

  it("probe_failed says the battery measured nothing — and never prints the floors", async () => {
    const card = await renderOllamaCard({
      profile: profile({
        source: "probe_failed",
        probed_at: null,
        honest_context: 4096,
        context_window: 8192,
        tool_protocols: null,
        measured_fields: [],
      }),
      trusted: false,
      effective_window: { value: 131072, source: "endpoint" },
    });
    expect(within(card).getByTestId(`${OLLAMA_CARD}-verdict`).textContent).toBe(
      "measure failed — keeping floor defaults",
    );
    expect(card.textContent).toContain("context window: 131,072 (from the endpoint)");
    expect(card.textContent).not.toContain("8,192");
    expect(card.textContent).not.toContain("4,096");
    expect(card.textContent).not.toContain("SELECTED");
    fireEvent.click(within(card).getByTestId(`${OLLAMA_CARD}-expand`));
    expect(within(card).queryByTestId(`${OLLAMA_CARD}-ladder`)).toBeNull();
    expect(within(card).getByTestId(`${OLLAMA_CARD}-detail`).textContent).toContain(
      "the probe battery ran and nothing came back usable — keeping floor defaults",
    );
  });
});

/* --------------------------------------------------- Measure on endpoint rows */

describe("Connections — Measure on a local endpoint row", () => {
  function seedEndpoint(env: unknown) {
    seed({ rows: [], fleetNodes: [FLEET_NODE] });
    hooks.responses[FLEET_ENV] = env;
  }

  it("POSTs the probe, shows the measuring state, and refetches on completion", async () => {
    seedEndpoint({
      profile: profile({ provider: "fleet-abc", model_id: "gpt-oss-120b", source: "seeded" }),
      trusted: false,
    });
    const view = render(<ConnectionsPage />);

    const btn = await screen.findByTestId("measure-fleet-abc-gpt-oss-120b");
    // The chip carries the pre-probe provenance.
    expect(
      (await screen.findByTestId("envelope-chip-fleet-abc-gpt-oss-120b")).textContent,
    ).toBe("seeded");

    fireEvent.click(btn);
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/envelope/fleet-abc/gpt-oss-120b/probe"),
    );
    expect(btn.textContent).toBe("Measuring…");
    expect(btn).toBeDisabled();

    // A completion for a DIFFERENT model must not clear this row's state.
    hooks.events = [
      completionEvent({ provider: "fleet-abc", model: "other-model", source: "probed" }, "ev-0"),
    ];
    view.rerender(<ConnectionsPage />);
    expect(btn.textContent).toBe("Measuring…");

    // The real completion: server truth updated, event lands, row refetches.
    const before = getCallsFor(FLEET_ENV);
    hooks.responses[FLEET_ENV] = {
      profile: profile({ provider: "fleet-abc", model_id: "gpt-oss-120b", source: "probed" }),
      trusted: false,
    };
    hooks.events = [
      completionEvent({ provider: "fleet-abc", model: "gpt-oss-120b", source: "probed" }),
      ...hooks.events,
    ];
    view.rerender(<ConnectionsPage />);

    await waitFor(() => expect(getCallsFor(FLEET_ENV)).toBeGreaterThan(before));
    await waitFor(() =>
      expect(
        screen.getByTestId("envelope-chip-fleet-abc-gpt-oss-120b").textContent,
      ).toBe("measured"),
    );
    expect(btn.textContent).toBe("Measure");
    expect(btn).not.toBeDisabled();

    // And the measurement SURFACES in the section below the cards.
    await waitFor(() =>
      expect(screen.getByTestId("measured-fleet-abc-gpt-oss-120b")).toBeTruthy(),
    );
  });

  it("renders a 400 detail VERBATIM and leaves the button usable", async () => {
    seedEndpoint({});
    render(<ConnectionsPage />);
    const btn = await screen.findByTestId("measure-fleet-abc-gpt-oss-120b");

    vi.mocked(post).mockRejectedValueOnce(
      new ApiError("this provider is trusted — nothing to measure", 400),
    );
    fireEvent.click(btn);

    const err = await screen.findByTestId("measure-error-fleet-abc-gpt-oss-120b");
    expect(err.textContent).toBe("this provider is trusted — nothing to measure");
    expect(btn.textContent).toBe("Measure");
    expect(btn).not.toBeDisabled();
  });

  it("a 409 shows the detail and honestly stays measuring until completion", async () => {
    seedEndpoint({});
    const view = render(<ConnectionsPage />);
    const btn = await screen.findByTestId("measure-fleet-abc-gpt-oss-120b");

    vi.mocked(post).mockRejectedValueOnce(
      new ApiError("a probe for this model is already running", 409),
    );
    fireEvent.click(btn);

    const err = await screen.findByTestId("measure-error-fleet-abc-gpt-oss-120b");
    expect(err.textContent).toBe("a probe for this model is already running");
    // A probe IS running — the state must not pretend otherwise.
    expect(btn.textContent).toBe("Measuring…");

    hooks.events = [
      completionEvent({ provider: "fleet-abc", model: "gpt-oss-120b", source: "probed" }),
    ];
    view.rerender(<ConnectionsPage />);
    await waitFor(() => expect(btn.textContent).toBe("Measure"));
    expect(
      screen.queryByTestId("measure-error-fleet-abc-gpt-oss-120b"),
    ).toBeNull();
  });
});

/* ------------------------------------- config-slot addressing (defect pin) */

describe("Connections — config-seeded slots probe their OWN provider", () => {
  // fleet/registry renders BOTH config slots as source="config" nodes whose
  // node id IS the provider name (id="ollama" for ollama_base_url,
  // id="custom" for custom_base_url). Hardcoding "custom" for seeded rows
  // probed the WRONG server for the ollama slot and filed the measurement
  // under custom__<model>.json — cross-provider profile poisoning.
  it("the ollama config node's Measure posts /envelope/ollama/…, never /envelope/custom/…", async () => {
    seed({
      rows: [], // keep the CLI-row controls away so the row's testid is unique
      fleetNodes: [
        {
          node: {
            id: "ollama",
            label: "Local Ollama",
            base_url: "http://127.0.0.1:11434",
            source: "config",
            routable: true,
            default_model: "qwen2.5:14b",
          },
        },
        {
          node: {
            id: "custom",
            label: "custom",
            base_url: "http://lmstudio:1234/v1",
            source: "config",
            routable: true,
            default_model: "glm-4.7-flash",
          },
        },
      ],
    });
    render(<ConnectionsPage />);

    fireEvent.click(await screen.findByTestId("measure-ollama-qwen2.5:14b"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/envelope/ollama/qwen2.5%3A14b/probe"),
    );

    fireEvent.click(await screen.findByTestId("measure-custom-glm-4.7-flash"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/envelope/custom/glm-4.7-flash/probe"),
    );

    // The ollama slot must never have touched the custom provider's envelope.
    const paths = vi.mocked(post).mock.calls.map((c) => String(c[0]));
    expect(paths.filter((p) => p.startsWith("/envelope/custom/"))).toEqual([
      "/envelope/custom/glm-4.7-flash/probe",
    ]);
  });
});

/* --------------------------------------------- opencode-cli (defect pin) */

describe("Connections — opencode-cli gets NO envelope surfaces", () => {
  // The backend treats every *-cli provider as trusted (the CLI owns its own
  // harness), so an envelope surface here would claim full capability beside
  // a quality line that may say the opposite — the exact lie the provenance
  // gating exists to prevent. Until envelope treatment for opencode-cli
  // ships: no measured card, no chip, no Measure.
  it("renders the quality line but no card, no Measure — and never GETs", async () => {
    seed({
      rows: [
        qrow({ provider: "opencode-cli", model: "llama3", avg: 0.5, samples: 9, clears: false }),
      ],
    });
    hooks.responses["/envelope/opencode-cli/llama3"] = {
      profile: profile({ provider: "opencode-cli", model_id: "llama3" }),
      trusted: true,
    };
    render(<ConnectionsPage />);

    // v1.169 behavior preserved: the router's judgment still shows.
    expect(screen.getByTestId("model-report-opencode-cli").textContent).toContain(
      "below your 0.75 bar",
    );
    expect(screen.queryByTestId("measured-opencode-cli-llama3")).toBeNull();
    expect(screen.queryByText("fully capable — no measurement needed")).toBeNull();
    expect(screen.queryByTestId("measure-opencode-cli-llama3")).toBeNull();
    // Not even fetched — a surface that renders nothing must not probe around.
    await waitFor(() => expect(getCallsFor("/envelope/opencode-cli/llama3")).toBe(0));
  });
});

/* --------------------------------- measuring-state safety net (fix pin) */

describe("EnvelopeRowControls — the WS event is not the only exit from Measuring", () => {
  // The /events WebSocket replays no backlog: a probe_completed fired during
  // a reconnect gap is gone forever. Without the poll + timeout the button
  // wedges on "Measuring…" until a page reload. Timing is injected here;
  // the product defaults are 10s poll / 3min cap.
  it("the poll notices a changed source/probed_at and clears the state", async () => {
    hooks.responses[FLEET_ENV] = {
      profile: profile({
        provider: "fleet-abc",
        model_id: "gpt-oss-120b",
        source: "seeded",
        probed_at: null,
      }),
      trusted: false,
    };
    render(
      <EnvelopeRowControls provider="fleet-abc" model="gpt-oss-120b" pollMs={20} timeoutMs={5000} />,
    );
    const btn = await screen.findByTestId("measure-fleet-abc-gpt-oss-120b");
    // Baseline is taken at click time — wait for the profile to be on screen.
    expect(
      (await screen.findByTestId("envelope-chip-fleet-abc-gpt-oss-120b")).textContent,
    ).toBe("seeded");

    fireEvent.click(btn);
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/envelope/fleet-abc/gpt-oss-120b/probe"),
    );
    expect(btn.textContent).toBe("Measuring…");

    // Server truth updates; NO event ever lands (reconnect gap).
    hooks.responses[FLEET_ENV] = {
      profile: profile({ provider: "fleet-abc", model_id: "gpt-oss-120b", source: "probed" }),
      trusted: false,
    };
    await waitFor(() => expect(btn.textContent).toBe("Measure"));
    await waitFor(() =>
      expect(
        screen.getByTestId("envelope-chip-fleet-abc-gpt-oss-120b").textContent,
      ).toBe("measured"),
    );
  });

  it("the hard timeout re-enables the button with an honest note", async () => {
    hooks.responses[FLEET_ENV] = {}; // no profile ever appears — poll finds nothing
    render(
      <EnvelopeRowControls provider="fleet-abc" model="gpt-oss-120b" pollMs={25} timeoutMs={100} />,
    );
    const btn = await screen.findByTestId("measure-fleet-abc-gpt-oss-120b");
    fireEvent.click(btn);
    await waitFor(() => expect(btn.textContent).toBe("Measuring…"));

    const note = await screen.findByTestId("measure-error-fleet-abc-gpt-oss-120b");
    expect(note.textContent).toBe(
      "measurement finished or timed out — refresh shows the latest",
    );
    expect(btn.textContent).toBe("Measure");
    expect(btn).not.toBeDisabled();
  });
});
