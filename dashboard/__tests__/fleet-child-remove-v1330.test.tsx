/**
 * v1.330.0 — calm chat wave 11, M1: Remove on a model a proxy reports tells
 * the truth.
 *
 * The wave-10 audit: removing a LiteLLM proxy left its models on the Fleet
 * page, each drawn as its own card with a Remove button, and pressing that
 * Remove answered {"ok": true} while nothing changed (the proxy names the
 * model again on its next pass). The daemon now removes a proxy's models with
 * the proxy and REFUSES to remove one on its own (409, "Remove the proxy to
 * remove it."). The page matches: a row that belongs to a proxy never offers
 * Remove or Rename; it says where the real control is. A proxy's own card
 * keeps both.
 */

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
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {}, prefetch: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/fleet",
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

import FleetPage from "@/app/fleet/page";

/* -------------------------------------------------------------- fixtures */

type Extra = Record<string, unknown>;
const node = (id: string, label: string, extra: Extra = {}) => ({
  id,
  label,
  base_url: `http://${id}.test:4000`,
  kind: "litellm",
  source: "user",
  parent_id: "",
  alias: "",
  enabled: true,
  routable: false,
  default_model: "glm",
  api_key_name: "",
  tool_use: null,
  vision: null,
  protocol: "anthropic",
  ...extra,
});

const child = (parent: string, alias: string) =>
  node(`${parent}-${alias}`, "", {
    kind: "unknown",
    source: "topology",
    parent_id: parent,
    alias,
    base_url: "http://spark-049d:8888",
    protocol: "openai",
  });

const row = (n: ReturnType<typeof node>, extra: Extra = {}) => ({
  node: n,
  status: "online",
  evidence: n.parent_id ? "proxy" : "direct",
  latency_ms: 14,
  error: "",
  metrics_supported: false,
  metrics_reason: "",
  metrics: null,
  rates: null,
  models: [],
  children: [],
  sampled_at: 512.5,
  checked: true,
  ...extra,
});

function seed(nodes: unknown[]) {
  hooks.responses["/fleet"] = { nodes, sampling: { active: true, interval: 2 }, code_route: {}, error: "" };
  hooks.responses["/fleet/usage"] = {};
}

const removeButtons = (scope: HTMLElement = document.body) =>
  within(scope)
    .queryAllByRole("button")
    .filter((b) => (b.textContent ?? "").trim() === "Remove");

beforeEach(() => {
  hooks.responses = {};
});
afterEach(() => cleanup());

describe("Fleet: Remove is offered only where it can work", () => {
  it("a model whose proxy is not listed says to remove the proxy, with no Remove or Rename", () => {
    // What an older daemon left behind after its proxy was removed.
    seed([row(child("spark", "glm")), row(node("tower", "Tower box", { kind: "vllm", protocol: "openai" }))]);
    render(<FleetPage />);

    const note = screen.getByTestId("fleet-child-note");
    expect(note.textContent).toBe("Remove the proxy to remove this.");
    expect(note.getAttribute("title")).toBe("Reported by the proxy spark");

    // The one real node keeps both controls; the proxy's model has none.
    expect(removeButtons()).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Rename this endpoint" })).toHaveLength(1);
    expect(screen.getAllByTestId("fleet-child-note")).toHaveLength(1);
  });

  it("a proxy's models sit under it with no Remove; only the proxy can be removed", () => {
    const proxy = node("spark", "Spark proxy");
    seed([
      row(proxy, { children: ["spark-glm", "spark-fleet"] }),
      row(child("spark", "glm")),
      row(child("spark", "fleet")),
    ]);
    render(<FleetPage />);

    expect(screen.getByText("Behind this proxy")).toBeInTheDocument();
    expect(removeButtons()).toHaveLength(1);
    expect(screen.queryByTestId("fleet-child-note")).toBeNull();
    expect(screen.getAllByTestId("fleet-node-title")).toHaveLength(1);
  });
});
