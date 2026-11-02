/**
 * v1.424.0 (review of D8): a model nothing speaks for keeps the GATEWAY's
 * answer (routing uses it, as before D8), and the page says so in words —
 * "not checked for this model: from the server's check of glm" — never as if
 * that model was checked; and each model row has its own Check press, which
 * asks THAT model (`POST /fleet/nodes/{id}/verify` with its name; a proxy's
 * model through its proxy, by the name the proxy serves).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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


import FleetPage from "@/app/fleet/page";
import { post } from "@/lib/api";
import { borrowedNote, toolsChip, visionChip } from "@/lib/modelCapability";

type Extra = Record<string, unknown>;
const node = (id: string, extra: Extra = {}) => ({
  id,
  label: id,
  base_url: `http://${id}.test:8000`,
  kind: "litellm",
  source: "user",
  enabled: true,
  routable: true,
  default_model: "glm",
  api_key_name: "",
  tool_use: true,
  vision: false,
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
const BORROWED = {
  tool_use: true,
  tool_use_from: "node",
  vision: false,
  vision_from: "node",
  checked_model: "glm",
};

function seed(nodes: unknown[]) {
  hooks.responses["/fleet"] = { nodes, sampling: { active: true, interval: 2 }, code_route: {}, error: "" };
  hooks.responses["/fleet/usage"] = {};
}

beforeEach(() => {
  hooks.responses = {};
  vi.mocked(post).mockClear();
});
afterEach(() => cleanup());

describe("a model nothing speaks for: the gateway's answer, said as such", () => {
  it("the words name where the answer came from, never 'Checked: <model>'", () => {
    const tools = toolsChip(BORROWED, "vision-alias");
    expect(tools.text).toBe("tools");
    expect(tools.yes).toBe(true);
    expect(tools.borrowed).toBe(true);
    expect(tools.title).toMatch(/^Not checked for vision-alias itself: the server's check of glm says it calls tools/);
    expect(tools.title).not.toMatch(/^Checked:/);
    expect(visionChip(BORROWED, "vision-alias").title).toMatch(/the server's check of glm says it can't see pictures/);
    expect(borrowedNote(BORROWED)).toBe("not checked for this model: from the server's check of glm");
    expect(borrowedNote({ tool_use: true, tool_use_from: "checked" })).toBe("");
  });

  it("the Fleet row shows the borrowed answer with its source in words", () => {
    seed([row(node("gw"), { models: [{ id: "vision-alias", ...BORROWED }] })]);
    render(<FleetPage />);
    const caps = screen.getByTestId("fleet-model-caps");
    expect(within(caps).getByTestId("fleet-model-cap-borrowed").textContent).toBe(
      "(not checked for this model: from the server's check of glm)",
    );
    const chips = within(caps).getAllByTestId("fleet-model-cap");
    expect(chips.map((c) => c.getAttribute("data-borrowed"))).toEqual(["yes", "yes"]);
  });
});

describe("the per-model Check press", () => {
  it("asks THAT model through the node that serves it, and reloads", async () => {
    seed([row(node("gw"), { models: [{ id: "glm", tool_use: true, tool_use_from: "checked" }, { id: "vision-alias", ...BORROWED }] })]);
    vi.mocked(post).mockResolvedValueOnce({ tool_use: true, model: "vision-alias" } as never);
    render(<FleetPage />);
    const rows = screen.getAllByTestId("fleet-model-check");
    expect(rows).toHaveLength(2);
    fireEvent.click(rows[1]);
    await waitFor(() =>
      expect(vi.mocked(post)).toHaveBeenCalledWith("/fleet/nodes/gw/verify", { model: "vision-alias" }),
    );
    expect(await screen.findByTestId("fleet-model-check-note")).toHaveTextContent("Checked.");
  });

  it("a check that could not reach the model says so", async () => {
    seed([row(node("gw"), { models: [{ id: "qwen", ...BORROWED }] })]);
    vi.mocked(post).mockResolvedValueOnce({ tool_use: null, error: "connection refused" } as never);
    render(<FleetPage />);
    fireEvent.click(screen.getByTestId("fleet-model-check"));
    expect(await screen.findByTestId("fleet-model-check-note")).toHaveTextContent(
      "Couldn't check qwen: connection refused",
    );
  });

  it("a proxy's model is asked through its proxy, by the name the proxy serves", async () => {
    const proxy = node("gw");
    const child = node("gw-blind", { parent_id: "gw", alias: "blind", source: "topology" });
    seed([
      row(proxy, { children: ["gw-blind"] }),
      row(child, { evidence: "proxy", model_capabilities: { blind: BORROWED } }),
    ]);
    render(<FleetPage />);
    fireEvent.click(screen.getByTestId("fleet-model-check"));
    await waitFor(() =>
      expect(vi.mocked(post)).toHaveBeenCalledWith("/fleet/nodes/gw/verify", { model: "blind" }),
    );
  });

  it("an older daemon (no per-model answers) offers no per-model Check", () => {
    seed([row(node("gw"), { models: [{ id: "glm" }] })]);
    render(<FleetPage />);
    expect(screen.queryByTestId("fleet-model-check")).toBeNull();
  });
});
