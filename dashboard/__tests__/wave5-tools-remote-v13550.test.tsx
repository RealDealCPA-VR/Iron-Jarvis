import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * v1.355.0 integrate (lane 5B note 2, G44): the Tools catalog's GitHub row is
 * GitHub's OWN server — an address, not a command. Before this edit, pressing
 * Add on that card posted `{name, command: "", args: [], env}` and the daemon
 * answered 422 ("a stdio pack needs a command"), and the Directory's "Also in
 * Tools" link sent users straight there.
 *
 * Pinned through the REAL ToolsPage + EnableDialog: the remote row posts
 * `{name, transport, url, auth: {header: "Authorization", format:
 * "Bearer {value}", value}}` with NO command/args/env, its "Show command"
 * shows the address, and a command row's body is byte-identical to before.
 */

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
    posts: [] as { path: string; body: unknown }[],
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
  get: vi.fn((path: string) => Promise.resolve(hooks.responses[path] ?? {})),
  post: vi.fn((path: string, body?: unknown) => {
    hooks.posts.push({ path, body });
    return Promise.resolve({ ok: true, tools_loaded: 3, note: null });
  }),
  put: vi.fn(() => Promise.resolve({})),
  patch: vi.fn(() => Promise.resolve({})),
  del: vi.fn(() => Promise.resolve({})),
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
    health: null,
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
  useRouter: () => ({ replace: () => {}, push: () => {}, refresh: () => {} }),
  useSearchParams: () => new URLSearchParams(""),
  usePathname: () => "/tools",
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
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null, BrandGlyph: () => null }));

import ToolsPage from "@/components/settings/pages/ToolsPage";

const GITHUB_URL = "https://api.githubcopilot.com/mcp/";
const TOKEN = "ghp_wave5IntegrateNotARealToken0000000000";

function seed() {
  hooks.responses["/tools/custom"] = { tools: [] };
  hooks.responses["/mcp/servers"] = { servers: [], auto_approve: false, auto_approve_effective: false };
  hooks.responses["/mcp/catalog"] = {
    catalog: [
      {
        // The daemon's real row shape (daemon/app.py _MCP_CATALOG, v1.355.0).
        id: "github",
        name: "GitHub",
        description: "Search repos and read/open issues and pull requests, through GitHub's own server.",
        command: "",
        args: [],
        transport: "http",
        url: GITHUB_URL,
        auth_field: "GITHUB_PERSONAL_ACCESS_TOKEN",
        env_keys: ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        category: "integration",
        needs: "",
        runtime_ready: true,
      },
      {
        id: "fetch",
        name: "Fetch web pages",
        description: "Fetch a page.",
        command: "uvx",
        args: ["mcp-server-fetch"],
        category: "reference",
        needs: "Python (uv)",
        runtime_ready: true,
      },
    ],
  };
}

const packCard = (id: string) => document.querySelector(`[data-pack="${id}"]`) as HTMLElement;

beforeEach(() => {
  hooks.posts.length = 0;
  for (const k of Object.keys(hooks.responses)) delete hooks.responses[k];
  seed();
});
afterEach(cleanup);

describe("v1.355.0 integrate — the Tools catalog adds GitHub's own server", () => {
  it("Add on the GitHub card posts its address and the token as Bearer, never a command", async () => {
    render(<ToolsPage />);
    await waitFor(() => expect(packCard("github")).not.toBeNull());
    fireEvent.click(screen.getByTestId("pack-add-github"));
    const field = await screen.findByTestId("enable-field-env:GITHUB_PERSONAL_ACCESS_TOKEN");
    fireEvent.change(field, { target: { value: TOKEN } });
    fireEvent.click(screen.getByTestId("enable-confirm"));

    await waitFor(() =>
      expect(hooks.posts.find((p) => p.path === "/mcp/servers")).toBeTruthy(),
    );
    const body = hooks.posts.find((p) => p.path === "/mcp/servers")!.body as Record<string, unknown>;
    expect(body).toEqual({
      name: "github",
      transport: "http",
      url: GITHUB_URL,
      auth: { header: "Authorization", format: "Bearer {value}", value: TOKEN },
    });
    expect("command" in body).toBe(false);
    expect("env" in body).toBe(false);
  });

  it("the remote card's 'Show command' shows the address", async () => {
    render(<ToolsPage />);
    await waitFor(() => expect(packCard("github")).not.toBeNull());
    expect(within(packCard("github")).getByText(GITHUB_URL)).toBeInTheDocument();
  });

  it("a command row still posts the v1.343.0 body byte for byte", async () => {
    render(<ToolsPage />);
    await waitFor(() => expect(packCard("fetch")).not.toBeNull());
    fireEvent.click(screen.getByTestId("pack-add-fetch"));
    fireEvent.click(await screen.findByTestId("enable-confirm"));
    await waitFor(() =>
      expect(hooks.posts.find((p) => p.path === "/mcp/servers")).toBeTruthy(),
    );
    expect(hooks.posts.find((p) => p.path === "/mcp/servers")!.body).toEqual({
      name: "fetch",
      command: "uvx",
      args: ["mcp-server-fetch"],
      env: {},
    });
  });
});
