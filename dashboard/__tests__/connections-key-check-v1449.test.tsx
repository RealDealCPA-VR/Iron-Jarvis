/**
 * v1.449.0 — readiness Wave C item 25: Settings › Connections tests a key
 * BEFORE saving it, like the chat's connect door (`?check=1`). A key that
 * fails its check is not stored: the card shows the Test's words and
 * "Nothing was saved", and keeps the key in the box for a fix. A passing key
 * is stored; while replies are still the demo it is chosen for answers with
 * the one explicit press (the page used to promote the first key itself).
 *
 * The REAL ConnectionsPage renders; only the transport seams are mocked
 * (the connections-plain-words-v1420 harness).
 */

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
    posts: [] as { path: string; body: unknown }[],
    postResponses: {} as Record<string, unknown>,
    health: { default_provider: "mock", providers: [] } as Record<string, unknown> | null,
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
    if (path in hooks.postResponses) return Promise.resolve(hooks.postResponses[path]);
    return Promise.resolve({ ok: true });
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
    epoch: 0,
    provided: true,
    health: hooks.health,
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
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
  };
});
vi.mock("@/components/connections/RestHookups", () => ({ RestHookups: () => null }));
vi.mock("@/components/connections/IronProxyCard", () => ({ IronProxyCard: () => null }));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null, BrandGlyph: () => null }));

import ConnectionsPage, { KEY_NOT_SAVED_NOTE } from "@/components/settings/pages/ConnectionsPage";

const KEY = "sk-ant-connections-test-0123456789";
const FAILED = {
  provider: "anthropic",
  status: "not_saved",
  saved: false,
  promoted_default: false,
  test: {
    ok: false,
    kind: "refused_key",
    status: 401,
    detail: "anthropic rejected the credential (401)",
    title: "Anthropic refused the key",
    fix: "Check the key on the Anthropic Console and paste it again.",
    technical: "anthropic rejected the credential (401)",
  },
};
const PASSED = {
  provider: "anthropic",
  status: "connected",
  saved: true,
  promoted_default: false,
  test: { ok: true, kind: "ok", detail: "anthropic reachable — credential accepted", title: "Anthropic accepted the key", fix: "" },
};

function seed() {
  hooks.responses["/connections"] = {
    connections: [
      {
        provider: "anthropic",
        display_name: "Anthropic",
        method: "api_key",
        supports_api_key: true,
        supports_oauth: false,
        connected: false,
        status: "disconnected",
        account: "",
        source: "",
        scopes: [],
        oauth_client_configured: false,
      },
    ],
  };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  hooks.responses["/fleet"] = { nodes: [] };
}

beforeEach(() => {
  hooks.responses = {};
  hooks.posts.length = 0;
  hooks.postResponses = {};
  hooks.health = { default_provider: "mock", providers: [] };
  seed();
});
afterEach(cleanup);

const cardOf = (p: string) => document.getElementById(`conn-card-${p}`) as HTMLElement;

async function pasteKey() {
  render(<ConnectionsPage />);
  await screen.findByText("Anthropic");
  const card = cardOf("anthropic");
  fireEvent.click(within(card).getByRole("button", { name: "Connect" }));
  const box = card.querySelector('input[type="password"]') as HTMLInputElement;
  fireEvent.change(box, { target: { value: KEY } });
  fireEvent.click(within(card).getByRole("button", { name: "Connect" }));
  return { card, box };
}

const paths = () => hooks.posts.map((p) => p.path);

describe("Settings › Connections tests a key before saving it", () => {
  it("a key that fails its check: the Test's words, 'Nothing was saved', the key kept, no Test, no choosing", async () => {
    hooks.postResponses["/connections/anthropic/key?check=1"] = FAILED;
    const { card, box } = await pasteKey();
    await within(card).findByText(/Nothing was saved, and what answers you didn't change/);
    expect(within(card).getByText(/Anthropic refused the key\./).textContent).toMatch(/paste it again/);
    expect(within(card).getByText(KEY_NOT_SAVED_NOTE)).toBeTruthy();
    expect(paths()).toEqual(["/connections/anthropic/key?check=1"]);
    expect(hooks.posts[0].body).toEqual({ key: KEY });
    // Kept for a fix: the box still holds the key and the form stays open.
    expect(box.value).toBe(KEY);
  });

  it("a key that passes is stored and, while the demo answers, chosen with the one press", async () => {
    hooks.postResponses["/connections/anthropic/key?check=1"] = PASSED;
    const { card, box } = await pasteKey();
    await waitFor(() => expect(paths()).toEqual(["/connections/anthropic/key?check=1", "/onboarding/use-model"]));
    expect(hooks.posts[1].body).toEqual({ provider: "anthropic" });
    // Wait for the line itself: the request above is recorded when it is
    // SENT, a moment before the page has drawn the Test's words (the
    // v1.476.0 release gate read the card in between).
    expect(await within(card).findByText(/Anthropic accepted the key/, {}, { timeout: 10_000 })).toBeTruthy();
    expect(within(card).queryByText(KEY_NOT_SAVED_NOTE)).toBeNull();
    // The form closed (the key left the box) — the stored key is in the vault.
    await waitFor(() => expect(document.body.contains(box)).toBe(false));
  });

  it("with /health not yet read, a passing key still asks the daemon (isDemoDefault reads no answer as the demo; its rule decides)", async () => {
    hooks.health = null;
    hooks.postResponses["/connections/anthropic/key?check=1"] = PASSED;
    await pasteKey();
    await waitFor(() => expect(paths()).toEqual(["/connections/anthropic/key?check=1", "/onboarding/use-model"]));
  });

  it("CONTROL: with a real default already chosen, a passing key is stored and nothing is chosen", async () => {
    hooks.health = { default_provider: "openai", providers: [] };
    hooks.postResponses["/connections/anthropic/key?check=1"] = PASSED;
    await pasteKey();
    await waitFor(() => expect(paths()).toContain("/connections/anthropic/key?check=1"));
    await new Promise((r) => setTimeout(r, 30));
    expect(paths()).toEqual(["/connections/anthropic/key?check=1"]);
  });

  it("CONTROL: an older daemon (no `saved`, no `test`) is asked the Test route, as before", async () => {
    hooks.postResponses["/connections/anthropic/key?check=1"] = { provider: "anthropic", status: "connected" };
    hooks.postResponses["/connections/anthropic/test"] = { ok: true, detail: "anthropic reachable — credential accepted" };
    await pasteKey();
    await waitFor(() => expect(paths().slice(0, 2)).toEqual(["/connections/anthropic/key?check=1", "/connections/anthropic/test"]));
  });
});
