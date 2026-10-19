/**
 * v1.357.0 (product readiness G76, review round): the demo's Connections
 * card never shows a retired name, even in its tooltip.
 *
 * The card's heading has said "Demo model (scripted)" since v1.314.0, but
 * its `title` still carried the daemon's display_name for the row, and the
 * daemon named that row "Mock (offline)". A user who hovered the heading
 * read the one word the canon retires for the demo. GET /connections drops
 * the demo row today, so this card shows only when a daemon sends it; when
 * one does, nothing on it may say "mock".
 *
 * Real page; only the network seams are mocked (the model-connections-v13540
 * harness).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

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
  const strip = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) {
      if (!["initial", "animate", "exit", "transition", "variants", "layout", "whileHover", "whileTap"].includes(k)) rest[k] = v;
    }
    return createElement(tag, rest);
  };
  const motion = new Proxy({} as Record<string, unknown>, { get: (_t, tag) => strip(String(tag)) });
  return {
    m: motion,
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
  };
});
vi.mock("@/components/connections/IronProxyCard", () => ({ IronProxyCard: () => null }));
vi.mock("@/components/connections/EnvelopeCard", () => ({
  EnvelopeRowControls: () => null,
  MeasuredEndpoints: () => null,
}));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null, BrandGlyph: () => null }));

import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";
import { DEMO_MODEL_LABEL, DEMO_REPLIES } from "@/lib/demoWords";
import { MODEL_SERVERS_TITLE } from "@/lib/modelServers";

/* ---------------------------------------------------------------- helpers */

function row(provider: string, displayName: string, over: Record<string, unknown> = {}) {
  return {
    provider,
    display_name: displayName,
    method: "api_key",
    supports_oauth: false,
    supports_api_key: true,
    oauth_help: "",
    connected: false,
    status: "disconnected",
    account: "",
    source: "",
    scopes: [],
    ...over,
  };
}

function seed(rows: unknown[]) {
  hooks.responses["/connections"] = { connections: rows };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  hooks.responses["/fleet"] = { nodes: [] };
  hooks.responses["/integrations"] = { integrations: [] };
}

/** Every string a user can read on an element: its text, its tooltip and
 *  its spoken name, for the element and everything inside it. */
function readable(el: Element): string[] {
  const out = [el.textContent ?? ""];
  for (const node of [el, ...Array.from(el.querySelectorAll("*"))]) {
    for (const attr of ["title", "aria-label", "placeholder", "alt"]) {
      const v = node.getAttribute(attr);
      if (v) out.push(v);
    }
  }
  return out;
}

beforeEach(() => {
  hooks.responses = {};
});
afterEach(() => cleanup());

/* ================================================================== tests */

describe("the demo's Connections card says the canon's words only", () => {
  it("an older daemon's 'Mock (offline)' never reaches the heading, its tooltip or the card", async () => {
    seed([row("mock", "Mock (offline)", { connected: true, status: "connected" })]);
    render(<ConnectionsPage />);
    const heading = await screen.findByText(DEMO_MODEL_LABEL);
    const card = heading.closest("#conn-card-mock") as HTMLElement;
    expect(card).not.toBeNull();
    // The heading is the demo's one label and carries no tooltip naming it
    // any other way.
    expect(heading.getAttribute("title")).toBeNull();
    // The line under it says the demo's one sentence.
    expect(card.textContent).toContain(DEMO_REPLIES);
    for (const words of readable(card)) {
      expect(words).not.toMatch(/\bmock\b|offline mock|offline demo/i);
    }
  });

  it("the daemon's new name for the row reads the same (one label, no echo)", async () => {
    seed([row("mock", DEMO_MODEL_LABEL, { connected: true, status: "connected" })]);
    render(<ConnectionsPage />);
    const heading = await screen.findByText(DEMO_MODEL_LABEL);
    expect(heading.getAttribute("title")).toBeNull();
  });

  it("control: another card still carries the daemon's own name as its tooltip", async () => {
    seed([
      row("mock", "Mock (offline)", { connected: true, status: "connected" }),
      row("custom", "Custom endpoint"),
    ]);
    render(<ConnectionsPage />);
    const servers = await screen.findByText(MODEL_SERVERS_TITLE);
    expect(servers.getAttribute("title")).toBe("Custom endpoint");
  });
});
