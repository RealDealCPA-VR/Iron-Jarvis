/**
 * v1.424.0 (D8): tools and vision are judged PER MODEL, never per gateway.
 *
 * A gateway serves several models and they do not all call tools or see
 * pictures. The daemon now answers per model (`GET /fleet` rows carry
 * `model_capabilities` and each `models[]` entry its own `tool_use` /
 * `vision`): a live check of THAT model, else what its server states, else
 * the endpoint's record for its default model, else not known. Pinned here:
 *  - the words (lib/modelCapability): checked, stated, no, not known — and
 *    true to what really happens (a model that can't call tools is never
 *    "sent to another model": picked by name it answers in text only);
 *  - Settings › Connections: an endpoint row shows its MODEL's own answer, the
 *    gateway's one flag never speaks for a model nobody checked, and the
 *    out-of-date copy (v1.131.0 wrapped tool turns, it never rerouted them)
 *    is gone;
 *  - the Fleet page: each model row says its own answer, "not known"
 *    included, and a proxy's model is said by the proxy's answer for it.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

/* ------------------------------------------------------------------ mocks */
const hooks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return { FakeApiError, responses: {} as Record<string, unknown> };
});

vi.mock("@/lib/api", () => ({
  ApiError: hooks.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  get: vi.fn((p: string) => Promise.resolve(hooks.responses[p] ?? {})),
  post: vi.fn(() => Promise.resolve({ ok: true })),
  put: vi.fn(() => Promise.resolve({})),
  patch: vi.fn(() => Promise.resolve({})),
  del: vi.fn(() => Promise.resolve({})),
}));
vi.mock("@/lib/useApi", () => {
  const read = (p: string | null) => ({
    data: p ? (hooks.responses[p] ?? null) : null,
    error: null,
    loading: false,
    reload: () => {},
  });
  return { useApi: read, usePolledApi: read };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    health: { default_provider: "mock", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
vi.mock("@/lib/useDesktopNotifications", () => ({
  useDesktopNotifications: () => ({
    supported: true,
    permission: "granted" as const,
    requestPermission: async () => "granted" as const,
    notify: () => {},
  }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/",
}));
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: React.ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("@/components/motion", () => ({
  PageShell: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
  Reveal: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set([
    "initial", "animate", "exit", "transition", "variants", "layout",
    "whileHover", "whileTap", "whileInView", "viewport",
  ]);
  const cache = new Map<string, unknown>();
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const motion = new Proxy({} as Record<string, unknown>, {
    get: (_t, tag) => {
      const key = String(tag);
      if (!cache.has(key)) cache.set(key, tagFor(key));
      return cache.get(key);
    },
  });
  return {
    m: motion,
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) =>
      createElement(Fragment, null, children),
  };
});
vi.mock("@/components/connections/RestHookups", () => ({ RestHookups: () => null }));
vi.mock("@/components/connections/IronProxyCard", () => ({ IronProxyCard: () => null }));
vi.mock("@/components/connections/EnvelopeCard", () => ({
  EnvelopeRowControls: () => null,
  MeasuredEndpoints: () => null,
}));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null, BrandGlyph: () => null }));

import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";
import FleetPage from "@/app/fleet/page";
import {
  capabilityOf,
  saveCheckWords,
  toolsChip,
  visionChip,
} from "@/lib/modelCapability";

/* -------------------------------------------------------------- fixtures */

function customCard() {
  return {
    provider: "custom",
    display_name: "Custom endpoint",
    method: "api_key",
    supports_oauth: false,
    supports_api_key: true,
    oauth_help: "",
    connected: false,
    status: "disconnected",
    account: "",
    source: "",
    scopes: [],
  };
}

type Extra = Record<string, unknown>;
const node = (id: string, label: string, extra: Extra = {}) => ({
  id,
  label,
  base_url: `http://${id}.test:8000`,
  kind: "litellm",
  source: "user",
  enabled: true,
  routable: true,
  default_model: "glm",
  api_key_name: "",
  tool_use: null,
  vision: null,
  protocol: "openai",
  ...extra,
});

const row = (n: ReturnType<typeof node>, extra: Extra = {}) => ({
  node: n,
  status: "online",
  evidence: "direct",
  latency_ms: 14,
  error: "",
  metrics_supported: true,
  metrics_reason: "",
  metrics: { requests_running: 0, requests_waiting: 0 },
  rates: null,
  models: [],
  children: [],
  sampled_at: 512.5,
  checked: true,
  ...extra,
});

const UNKNOWN = { tool_use: null, vision: null, tool_use_from: "", vision_from: "" };

function seed(nodes: unknown[]) {
  hooks.responses["/connections"] = { connections: [customCard()] };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  hooks.responses["/fleet"] = { nodes, sampling: { active: true, interval: 2 }, code_route: {}, error: "" };
  hooks.responses["/fleet/usage"] = {};
}

beforeEach(() => {
  hooks.responses = {};
});
afterEach(() => cleanup());

async function endpointCard() {
  render(<ConnectionsPage />);
  const card = (await screen.findByText("Model servers on your network")).closest("#conn-card-custom") as HTMLElement;
  return card;
}

/* ================================================================= words */

describe("one model's own answer, in words", () => {
  it("a checked yes, a stated yes, a no and not known each say what they are", () => {
    expect(toolsChip({ tool_use: true, tool_use_from: "checked" }, "glm").title).toBe(
      "Checked: glm calls tools, so turns that use the web or files run on it.",
    );
    expect(toolsChip({ tool_use: true, tool_use_from: "stated" }, "glm").title).toMatch(
      /^The model's server says glm can call tools \(not checked here yet\)/,
    );
    const no = toolsChip({ tool_use: false, tool_use_from: "checked" }, "glm");
    expect(no.text).toBe("no tools");
    // True to what really happens (v1.131.0): never "goes to another model".
    expect(no.title).toMatch(/Picked by name, it answers in text only/);
    expect(no.title).not.toMatch(/another model/);
    const unknown = toolsChip(UNKNOWN, "glm");
    expect(unknown.known).toBe(false);
    expect(unknown.text).toBe("tools not known");
    expect(unknown.title).toMatch(/^Not known yet whether glm can call tools/);
    const blind = visionChip({ vision: false, vision_from: "stated" }, "glm");
    expect(blind.title).toMatch(/^The model's server says glm can't see pictures\./);
    // D1: a picture is never moved to a key that bills per use for this pick.
    expect(blind.title).toMatch(/never one that bills per use/);
    expect(visionChip({ vision: true, vision_from: "checked" }, "default").title).toMatch(
      /^Checked: this model saw the test picture/,
    );
  });

  it("the save note is about the model it asked", () => {
    expect(saveCheckWords(true, "qwen")).toBe(" qwen calls tools, so turns that use the web or files run on it.");
    expect(saveCheckWords(false, "")).toBe(" This model doesn't call tools itself: picked by name, it answers in text only.");
    expect(saveCheckWords(null, "qwen")).toMatch(/^ Whether qwen calls tools could not be checked yet\./);
  });

  it("a row from a per-model daemon never borrows the node's flag; an older daemon's flag still shows", () => {
    expect(capabilityOf({ model_capabilities: { glm: { tool_use: true, tool_use_from: "checked" } } }, "qwen", { tool_use: true }))
      .toEqual(UNKNOWN);
    expect(capabilityOf({}, "qwen", { tool_use: true, vision: false })).toEqual({
      tool_use: true,
      vision: false,
      tool_use_from: "checked",
      vision_from: "checked",
    });
  });
});

/* =========================================================== Connections */

describe("Settings > Connections: an endpoint row says its MODEL's own answer", () => {
  it("the node's one flag never speaks for a model nobody checked", async () => {
    // The gateway's flag says yes (an old check of another model); the
    // daemon says the row's model (glm) is not known: the row offers Verify.
    seed([
      row(node("gw", "DGX gateway", { tool_use: true, vision: true }), {
        model_capabilities: { glm: UNKNOWN },
      }),
    ]);
    const card = await endpointCard();
    await within(card).findByText("DGX gateway");
    expect(within(card).queryByTestId("endpoint-row-tools")).toBeNull();
    expect(within(card).queryByTestId("endpoint-row-vision")).toBeNull();
    const verify = within(card).getByTestId("endpoint-row-verify");
    expect(verify.getAttribute("title")).toMatch(/^Not known yet whether glm can call tools/);
  });

  it("a model whose server states a yes shows the tick, said as the server's word", async () => {
    seed([
      row(node("gw", "DGX gateway"), {
        model_capabilities: { glm: { tool_use: true, tool_use_from: "stated", vision: false, vision_from: "stated" } },
      }),
    ]);
    const card = await endpointCard();
    const tools = await within(card).findByTestId("endpoint-row-tools");
    expect(tools.textContent).toBe("tools ✓");
    expect(tools.getAttribute("title")).toMatch(/^The model's server says glm can call tools/);
    expect(within(card).getByTestId("endpoint-row-vision").textContent).toBe("no vision");
  });

  it("a node with no model named reads its default model's answer by the empty name", async () => {
    seed([
      row(node("box", "Plain box", { default_model: "", tool_use: true }), {
        model_capabilities: { "": { tool_use: true, tool_use_from: "checked", vision: null, vision_from: "" } },
      }),
    ]);
    const card = await endpointCard();
    const tools = await within(card).findByTestId("endpoint-row-tools");
    expect(tools.textContent).toBe("tools ✓");
    expect(tools.getAttribute("title")).toMatch(/^Checked: this model calls tools/);
  });

  it("an older daemon (no per-model answers) keeps the node's flag", async () => {
    seed([row(node("old", "Old box", { tool_use: true }))]);
    const card = await endpointCard();
    expect((await within(card).findByTestId("endpoint-row-tools")).textContent).toBe("tools ✓");
  });

  it("the out-of-date words are gone from the page", () => {
    const src = readFileSync(path.join(__dirname, "..", "components/settings/pages/ConnectionsPage.tsx"), "utf8");
    for (const stale of [
      "It runs tools, so turns that use the web or files can run here.",
      "This server can't run tools, so turns that use the web or files go to another model.",
      "this server runs tools, so turns that use the web or files stay here.",
      "so tool turns go to another model for now",
      "so image turns go to a model that can",
      "quietly sends every tool-using turn to another provider",
    ]) {
      expect(src, stale).not.toContain(stale);
    }
  });
});

/* ============================================================ Fleet page */

describe("Fleet page: each model behind a gateway says its own answer", () => {
  it("every listed model shows its own tools and vision, not known included", () => {
    seed([
      row(node("gw", "DGX gateway", { tool_use: true, vision: true }), {
        models: [
          { id: "glm", tool_use: true, vision: false, tool_use_from: "checked", vision_from: "stated" },
          { id: "qwen", tool_use: false, vision: null, tool_use_from: "checked", vision_from: "" },
          { id: "fresh", ...UNKNOWN },
        ],
      }),
    ]);
    render(<FleetPage />);
    const sets = screen.getAllByTestId("fleet-model-caps");
    expect(sets.map((s) => s.textContent)).toEqual([
      "tools ✓no vision",
      "no toolsvision not known",
      "tools not knownvision not known",
    ]);
    const unknown = within(sets[2]).getAllByTestId("fleet-model-cap");
    expect(unknown.map((c) => c.getAttribute("data-known"))).toEqual(["no", "no"]);
  });

  it("an older daemon's model rows stay as they were (no invented answer)", () => {
    seed([row(node("gw", "DGX gateway", { tool_use: true }), { models: [{ id: "glm" }] })]);
    render(<FleetPage />);
    expect(screen.queryByTestId("fleet-model-caps")).toBeNull();
  });

  it("a proxy's model is said by the proxy's answer for its name, never the node flag", () => {
    const proxy = node("gw", "DGX gateway");
    const child = node("gw-blind", "blind", { parent_id: "gw", alias: "blind", source: "topology", tool_use: true });
    seed([
      row(proxy, { children: ["gw-blind"] }),
      row(child, {
        evidence: "proxy",
        model_capabilities: { blind: { tool_use: false, tool_use_from: "stated", vision: false, vision_from: "stated" } },
      }),
    ]);
    render(<FleetPage />);
    const caps = screen.getByTestId("fleet-model-caps");
    expect(caps.textContent).toBe("no toolsno vision");
  });
});
