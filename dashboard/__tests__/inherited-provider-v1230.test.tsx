/**
 * v1.230.0 (audit Wave 4, U5) — the Connections pill tells the same truth as
 * /health.
 *
 * Live finding: Anthropic's card said "Not connected" while /health said
 * `available: true` and the switcher offered claude-opus undimmed — a keyless
 * provider served through the logged-in `claude` CLI (`_INHERIT_ALIAS`). The
 * daemon's status row now reports `connected: true` with
 * `source: "inherited from claude-cli"`, and this page renders that as
 * "Inherited from claude-cli" — with the Disconnect button gone (there is no
 * key here to drop) and a line saying where the login lives.
 *
 * Harness mirrors envelope-ui-v1201's (the page's data seams mocked).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

const hooks = vi.hoisted(() => ({
  responses: {} as Record<string, unknown>,
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

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: true }),
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
    // v1.250.0 (S-08): components animate with framer's slim `m.*` under the
    // layout's LazyMotion, so this mock must export `m` too — otherwise every
    // mocked surface throws "No \"m\" export is defined on the mock".
    get m() {
      return (this as unknown as { motion: unknown }).motion;
    },
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

function conn(provider: string, over: Record<string, unknown> = {}) {
  return {
    provider,
    display_name: provider,
    method: "api_key",
    connected: false,
    status: "disconnected",
    account: "",
    source: "",
    scopes: [],
    ...over,
  };
}

function seed(connections: unknown[]) {
  hooks.responses["/connections"] = { connections };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  hooks.responses["/fleet"] = { nodes: [] };
  hooks.responses["health"] = {
    default_provider: "ollama",
    providers: [
      { provider: "anthropic", available: true, inherited_from: "claude-cli" },
      { provider: "openai", available: true },
      { provider: "google", available: false },
    ],
  };
}

const cardOf = (provider: string) =>
  document.getElementById(`conn-card-${provider}`) as HTMLElement;

beforeEach(() => {
  hooks.responses = {};
});
afterEach(() => cleanup());

describe("Connections — an inherited login reads as connected", () => {
  it("renders 'Inherited from claude-cli' on the pill, no Disconnect, and says where the login lives", async () => {
    seed([
      conn("anthropic", {
        display_name: "Anthropic",
        connected: true,
        status: "connected",
        source: "inherited from claude-cli",
      }),
      conn("openai", {
        display_name: "OpenAI",
        connected: true,
        status: "connected",
        source: "vault",
        account: "sk-…1234",
      }),
      conn("google", { display_name: "Google" }),
    ]);
    render(<ConnectionsPage />);
    await screen.findByText("Anthropic");

    const anthropic = cardOf("anthropic");
    expect(within(anthropic).getByText("Inherited from claude-cli")).toBeInTheDocument();
    expect(within(anthropic).queryByText("Not connected")).toBeNull();
    expect(within(anthropic).queryByRole("button", { name: /Disconnect/ })).toBeNull();
    expect(anthropic.textContent).toMatch(/signed in through the claude CLI/);
    // Test and Make default are still offered — it IS usable.
    expect(within(anthropic).getByRole("button", { name: /Test/ })).toBeInTheDocument();

    // A vault-connected provider is unchanged: plain "Connected" + Disconnect.
    const openai = cardOf("openai");
    expect(within(openai).getByText("Connected")).toBeInTheDocument();
    expect(within(openai).getByRole("button", { name: /Disconnect/ })).toBeInTheDocument();

    // And a genuinely disconnected one still says so.
    expect(within(cardOf("google")).getByText("Not connected")).toBeInTheDocument();
  });

  it("names the codex CLI for an inherited OpenAI login", async () => {
    seed([
      conn("openai", {
        display_name: "OpenAI",
        connected: true,
        status: "connected",
        source: "inherited from codex-cli",
      }),
    ]);
    render(<ConnectionsPage />);
    await screen.findByText("OpenAI");
    const openai = cardOf("openai");
    expect(within(openai).getByText("Inherited from codex-cli")).toBeInTheDocument();
    expect(openai.textContent).toMatch(/signed in through the codex CLI/);
  });
});
