import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const hooks = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    responses: {} as Record<string, unknown>,
    failing: {} as Record<string, { status: number; message: string }>,
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    dels: [] as string[],
    delAnswers: {} as Record<string, unknown>,
    copied: [] as string[],
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: hooks.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
  ijToken: () => null,
  sseUrl: (p: string) => p,
  wsUrl: (p: string) => p,
  onUnauthorizedChange: () => () => {},
  onRequestErrorChange: () => () => {},
  onNetworkError: () => () => {},
  get: vi.fn((path: string) => {
    hooks.gets.push(path);
    const fail = hooks.failing[path];
    if (fail) return Promise.reject(new hooks.FakeApiError(fail.message, fail.status));
    return Promise.resolve(hooks.responses[path] ?? {});
  }),
  post: vi.fn((path: string, body?: unknown) => {
    hooks.posts.push({ path, body });
    const fail = hooks.failing[path];
    if (fail) return Promise.reject(new hooks.FakeApiError(fail.message, fail.status));
    return Promise.resolve(hooks.responses[`POST ${path}`] ?? { ok: true, tools_loaded: 0, note: null });
  }),
  put: vi.fn(() => Promise.resolve({})),
  patch: vi.fn(() => Promise.resolve({})),
  del: vi.fn((path: string) => {
    hooks.dels.push(path);
    return Promise.resolve(hooks.delAnswers[path] ?? {});
  }),
}));

vi.mock("@/lib/useApi", () => {
  const read = (path: string | null) => ({
    data: path ? (hooks.responses[path] ?? null) : null,
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
    health: hooks.responses["health"] ?? null,
    refresh: () => {},
  }),
}));
vi.mock("@/components/settings/MaintenanceTools", () => ({
  copyPlain: async (text: string) => {
    hooks.copied.push(text);
    return "copied" as const;
  },
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
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
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
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null, BrandGlyph: () => null }));

import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";

/**
 * The review of the slot's Delete (v1.449.0, readiness Wave C item 24):
 * `removeLegacy` now goes through `DELETE /fleet/nodes/{id}` for BOTH
 * settings-seeded slots (the custom card lists `custom` and `ollama`). Only
 * the custom slot has a connection record (its saved key); asking the daemon
 * to disconnect `ollama` was an error on every Delete of that row.
 */

beforeEach(() => {
  hooks.responses = {};
  hooks.failing = {};
  hooks.delAnswers = {};
  hooks.gets.length = 0;
  hooks.posts.length = 0;
  hooks.dels.length = 0;
  hooks.responses["/mcp/import/sources"] = { sources: [] };
  hooks.responses["/mcp/servers"] = { servers: [], auto_approve: false, auto_approve_effective: false };
  hooks.responses["/mcp/catalog"] = { catalog: [] };
  hooks.responses["/tools/custom"] = { tools: [] };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  hooks.responses["health"] = { default_provider: "mock", providers: [] };
  hooks.responses["/connections"] = {
    connections: [
      {
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
      },
    ],
  };
  const seed = (id: string, label: string, port: number) => ({
    node: {
      id,
      label,
      base_url: `http://127.0.0.1:${port}`,
      source: "config",
      routable: true,
      enabled: true,
      default_model: "m",
      api_key_name: "",
      protocol: "openai",
    },
    status: "online",
  });
  hooks.responses["/fleet"] = { nodes: [seed("custom", "LM Studio", 1234), seed("ollama", "Ollama", 11434)] };
});
afterEach(() => cleanup());

const customCard = () => document.getElementById("conn-card-custom") as HTMLElement;

async function deleteRow(label: string) {
  const card = customCard();
  await waitFor(() => expect(within(card).getAllByTestId("endpoint-row")).toHaveLength(2));
  const row = within(card)
    .getAllByTestId("endpoint-row")
    .find((r) => r.textContent?.includes(label)) as HTMLElement;
  expect(row).toBeTruthy();
  fireEvent.click(within(row).getByRole("button", { name: /^Delete$/ }));
  fireEvent.click(within(row).getByRole("button", { name: /Delete\?|Delete the default\?/ }));
}

describe("the slot's Delete goes through the fleet route; only the custom slot has a record to disconnect", () => {
  it("the ollama slot: DELETE /fleet/nodes/ollama and never /connections/ollama", async () => {
    render(<ConnectionsPage />);
    await screen.findByText("Model servers on your network");
    expect(customCard()).not.toBeNull();
    await deleteRow("Ollama");
    await waitFor(() => expect(hooks.dels).toContain("/fleet/nodes/ollama"));
    await new Promise((r) => setTimeout(r, 20));
    expect(hooks.dels.some((p) => p.startsWith("/connections/"))).toBe(false);
  });

  it("the custom slot: DELETE /fleet/nodes/custom, then its saved key's record", async () => {
    render(<ConnectionsPage />);
    await screen.findByText("Model servers on your network");
    await deleteRow("LM Studio");
    await waitFor(() => expect(hooks.dels).toContain("/connections/custom"));
    expect(hooks.dels.indexOf("/fleet/nodes/custom")).toBeLessThan(hooks.dels.indexOf("/connections/custom"));
    // Never the old plain PUT of blank settings: the daemon clears the slot.
  });

  it("the slot's removal answer says where the default landed", async () => {
    hooks.delAnswers["/fleet/nodes/ollama"] = {
      ok: true,
      cleared_settings: ["ollama_base_url"],
      default_replaced: { from: "ollama", to: "mock", demo: true, words: "Nothing else is connected, so Jarvis answers with the demo model." },
    };
    render(<ConnectionsPage />);
    await screen.findByText("Model servers on your network");
    await deleteRow("Ollama");
    expect((await screen.findByTestId("endpoint-default-moved")).textContent).toContain("demo model");
  });
});
