/**
 * v1.201.0 A4 — the Capability Envelope UI.
 *
 * The report card gains an ENVELOPE section and Connections' local endpoint
 * rows gain Measure. What can lie silently here, and is therefore pinned:
 *  - provenance next to every value: a SELECTED ladder must be impossible on
 *    an unprobed profile (IronCore's report card once showed a green SELECTED
 *    on one — that class of lie is banned). Seeded shows "seeded" and NEVER
 *    the word "measured"; ladder rows render only for measured sources.
 *  - trusted (frontier) models get ONE quiet line and no scorecard — a fake
 *    scorecard on a model nobody measured is the same lie inverted.
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

/** A judged ollama quality row — makes the report card (and thus the
 *  envelope section under it) render for ollama/qwen2.5:14b. */
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

/* ----------------------------------------------------------- the URL helper */

describe("envelopeUrl", () => {
  it("percent-encodes the model id as a path segment (ollama ids carry ':')", () => {
    expect(envelopeUrl("ollama", "qwen2.5:14b")).toBe("/envelope/ollama/qwen2.5%3A14b");
    expect(envelopeUrl("fleet-abc", "gpt-oss-120b")).toBe("/envelope/fleet-abc/gpt-oss-120b");
  });
});

/* ------------------------------------------------- the measured report card */

describe("report card — measured envelope section", () => {
  it("renders source, honest-context gap, chars/token, and the ladder from GET", async () => {
    seed();
    hooks.responses[OLLAMA_ENV] = { profile: profile(), trusted: false };
    render(<ConnectionsPage />);

    const sec = await screen.findByTestId("envelope-ollama-qwen2.5:14b");
    const text = sec.textContent ?? "";

    // Provenance badge, next to the values it vouches for.
    expect(
      within(sec).getByTestId("envelope-ollama-qwen2.5:14b-source").textContent,
    ).toBe("measured");

    // Honest vs advertised, with the IronCore-sized gap flagged.
    expect(text).toContain("honest context 49,152 of 262,144 advertised (19%)");
    expect(text).toContain("big gap");
    expect(text).toContain("3.6 chars/token");

    // The ladder: score vs its bar, word-first verdicts, SELECTED exactly once.
    const ladder = within(sec).getByTestId("envelope-ollama-qwen2.5:14b-ladder");
    expect(ladder.querySelectorAll("[data-rung]")).toHaveLength(3);
    const lt = ladder.textContent ?? "";
    expect(lt).toContain("native tool calls");
    expect(lt).toContain("0.98");
    expect(lt).toContain("(needs 0.95)");
    expect(lt).toContain("strict JSON (constrained)");
    expect(lt).toContain("0.94");
    expect(lt).toContain("ok, fallback");
    expect(lt).toContain("floor (always works)");
    expect(lt.match(/SELECTED/g)).toHaveLength(1);

    // Measured extras.
    expect(text).toContain("JSON adherence 0.96");
    expect(text).toContain("coherent to ~9 turns");
    expect(text).toContain("vision no");

    // The local CLI row grew its Measure affordance for the same model.
    expect(screen.getByTestId("measure-ollama-qwen2.5:14b")).toBeTruthy();
  });

  it("a rung below its bar says so and by how much; the next rung is SELECTED", async () => {
    seed();
    hooks.responses[OLLAMA_ENV] = {
      profile: profile({ tool_protocols: { native: 0.8, strict_json: 0.93 } }),
      trusted: false,
    };
    render(<ConnectionsPage />);

    const ladder = await screen.findByTestId("envelope-ollama-qwen2.5:14b-ladder");
    const native = ladder.querySelector('[data-rung="native"]');
    const strict = ladder.querySelector('[data-rung="strict_json"]');
    expect(native?.textContent).toContain("below bar (0.15 short)");
    expect(native?.textContent).not.toContain("SELECTED");
    expect(strict?.textContent).toContain("SELECTED");
  });

  it("no rung clears: the text floor is SELECTED and says why", async () => {
    seed();
    hooks.responses[OLLAMA_ENV] = {
      profile: profile({ tool_protocols: { native: 0.4, strict_json: 0.5 } }),
      trusted: false,
    };
    render(<ConnectionsPage />);
    const ladder = await screen.findByTestId("envelope-ollama-qwen2.5:14b-ladder");
    expect(
      ladder.querySelector('[data-rung="text_floor"]')?.textContent,
    ).toContain("SELECTED — no rung cleared its bar");
  });
});

/* ----------------------------------------------------- provenance honesty */

describe("report card — unmeasured provenance never dresses up as evidence", () => {
  it("a seeded profile says 'seeded' and NEVER 'measured' — and gets no ladder", async () => {
    seed();
    hooks.responses[OLLAMA_ENV] = {
      profile: profile({
        source: "seeded",
        probed_at: null,
        honest_context: null,
        context_window: 32768,
        chars_per_token: 4.0,
        tool_protocols: null,
        json_adherence: null,
        coherence_horizon: null,
      }),
      trusted: false,
    };
    render(<ConnectionsPage />);

    const sec = await screen.findByTestId("envelope-ollama-qwen2.5:14b");
    const text = sec.textContent ?? "";
    expect(
      within(sec).getByTestId("envelope-ollama-qwen2.5:14b-source").textContent,
    ).toBe("seeded");
    expect(text).not.toContain("measured");
    expect(text).not.toContain("SELECTED");
    expect(
      within(sec).queryByTestId("envelope-ollama-qwen2.5:14b-ladder"),
    ).toBeNull();
    expect(text).toContain("context 32,768 tokens (reported)");
    expect(text).toContain("4 chars/token (assumed)");
    expect(text).toContain("not verified");
  });

  it("probe_failed says the battery measured nothing and keeps floor defaults", async () => {
    seed();
    hooks.responses[OLLAMA_ENV] = {
      profile: profile({
        source: "probe_failed",
        probed_at: null,
        honest_context: null,
        context_window: 8192,
        chars_per_token: 4.0,
        tool_protocols: null,
      }),
      trusted: false,
    };
    render(<ConnectionsPage />);
    const sec = await screen.findByTestId("envelope-ollama-qwen2.5:14b");
    expect(sec.textContent).toContain("measure failed — keeping floor defaults");
    expect(sec.textContent).not.toContain("SELECTED");
    expect(
      within(sec).queryByTestId("envelope-ollama-qwen2.5:14b-ladder"),
    ).toBeNull();
  });

  it("a trusted model gets the one quiet line — no scorecard, no Measure", async () => {
    seed();
    hooks.responses[OLLAMA_ENV] = { profile: profile(), trusted: true };
    render(<ConnectionsPage />);

    const quiet = await screen.findByTestId("envelope-ollama-qwen2.5:14b-trusted");
    expect(quiet.textContent).toBe("fully capable — no measurement needed");
    // No scorecard: none of the profile's numbers may leak into a display.
    expect(screen.queryByTestId("envelope-ollama-qwen2.5:14b")).toBeNull();
    expect(screen.queryByTestId("envelope-ollama-qwen2.5:14b-ladder")).toBeNull();
    expect(screen.queryByText(/SELECTED/)).toBeNull();
    expect(screen.queryByText(/0\.98/)).toBeNull();
    // And the row-level Measure affordance stays away from trusted profiles.
    await waitFor(() =>
      expect(screen.queryByTestId("measure-ollama-qwen2.5:14b")).toBeNull(),
    );
  });
});

/* --------------------------------------------------- Measure on endpoint rows */

describe("Connections — Measure on a local endpoint row", () => {
  function seedEndpoint(envelope: unknown) {
    seed({ rows: [], fleetNodes: [FLEET_NODE] });
    hooks.responses[FLEET_ENV] = envelope;
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
  // harness), so an envelope surface here would render "fully capable — no
  // measurement needed" beside a quality line that may say the opposite —
  // the exact lie the provenance gating exists to prevent. Until envelope
  // treatment for opencode-cli ships: no section, no chip, no Measure.
  it("renders the quality line but no section, no trusted line, no Measure — and never GETs", async () => {
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
    expect(screen.queryByTestId("envelope-opencode-cli-llama3")).toBeNull();
    expect(screen.queryByTestId("envelope-opencode-cli-llama3-trusted")).toBeNull();
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
