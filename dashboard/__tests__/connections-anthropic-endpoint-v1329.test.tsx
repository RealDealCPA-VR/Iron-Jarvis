/**
 * v1.329.0 (calm chat wave 6, H3): an Anthropic-compatible server can be
 * ADDED, and the endpoint form says so in plain words.
 *
 * - The form has a quiet two-option "Server type" choice (OpenAI-compatible /
 *   Anthropic-compatible) and sends it as `protocol` on POST /fleet/nodes.
 * - "Fetch available models" asks the CHOSEN way first; when the server
 *   answered the other way, the choice moves to it and one quiet line says so.
 *   The old "a saved endpoint chats the OpenAI way" warning is gone.
 * - Copy: the URL placeholder is the bare example address and the key note is
 *   three plain sentences, no em-dash aside.
 * - A connected card's actions row wraps inside the card, and its auth line
 *   never breaks "API key" in two (the Pixio card at desk width).
 *
 * Harness: the page's data seams are mocked like endpoint-models-anthropic-v1329.
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
    /** protocol -> the probe's answer. */
    answers: {} as Record<string, unknown>,
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
    if (path === "/connections/endpoints/models") {
      const proto = (body as { protocol?: string } | undefined)?.protocol ?? "openai";
      return Promise.resolve(hooks.answers[proto] ?? { models: [], error: "nothing set", reason: "failed" });
    }
    if (path === "/fleet/nodes") return Promise.resolve({ node: { id: "relay", label: "Relay" } });
    if (path.endsWith("/verify")) return Promise.resolve({ tool_use: null, error: "asleep" });
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
    health: hooks.responses["health"] ?? null,
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
vi.mock("@/components/BrandGlyph", () => ({
  ProviderMark: () => null,
  BrandGlyph: () => null,
}));

import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";

const PROBE = "/connections/endpoints/models";
const REFUSED = {
  models: [],
  error: "The server refused this key. Check the key and try again.",
  reason: "refused_key",
};
const CLAUDES = {
  models: ["claude-opus-9", "claude-sonnet-9"],
  error: null,
  reason: null,
  protocol: "anthropic",
  labels: { "claude-opus-9": "Opus 9", "claude-sonnet-9": "Sonnet 9" },
  partial: false,
};
const QWENS = { models: ["qwen3", "llama3"], error: null, reason: null };

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

function seed(extra: unknown[] = [], nodes: unknown[] = []) {
  hooks.responses["/connections"] = { connections: [customCard(), ...extra] };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  hooks.responses["/fleet"] = { nodes };
  hooks.responses["health"] = { default_provider: "mock", providers: [] };
}

beforeEach(() => {
  hooks.responses = {};
  hooks.posts.length = 0;
  hooks.answers = { openai: REFUSED, anthropic: CLAUDES };
});
afterEach(() => {
  cleanup();
});

const probes = () => hooks.posts.filter((p) => p.path === PROBE);
const protocols = () => probes().map((p) => (p.body as { protocol?: string }).protocol);
const saved = () => hooks.posts.find((p) => p.path === "/fleet/nodes")?.body as Record<string, unknown> | undefined;

async function openForm() {
  seed();
  render(<ConnectionsPage />);
  const card = (await screen.findByText("Custom endpoint")).closest("#conn-card-custom") as HTMLElement;
  fireEvent.click(within(card).getByRole("button", { name: /Add an endpoint/ }));
  fireEvent.change(within(card).getByPlaceholderText("http://localhost:1234/v1"), {
    target: { value: "https://relay.test/anthropic" },
  });
  return card;
}

const radio = (card: HTMLElement, p: "openai" | "anthropic") =>
  within(card).getByTestId(`endpoint-protocol-${p}`) as HTMLInputElement;

/* ========================================================================== */

describe("the server type", () => {
  it("is a quiet two-option choice that starts on OpenAI-compatible", async () => {
    const card = await openForm();
    const group = within(card).getByTestId("endpoint-protocol");
    expect(within(group).getAllByRole("radio")).toHaveLength(2);
    expect(within(group).getByLabelText("OpenAI-compatible")).toBe(radio(card, "openai"));
    expect(within(group).getByLabelText("Anthropic-compatible")).toBe(radio(card, "anthropic"));
    expect(radio(card, "openai").checked).toBe(true);
    expect(radio(card, "anthropic").checked).toBe(false);
  });

  it("chosen by hand, it is sent with the endpoint and Fetch asks that way first", async () => {
    hooks.answers.anthropic = CLAUDES;
    const card = await openForm();
    fireEvent.click(radio(card, "anthropic"));
    expect(radio(card, "anthropic").checked).toBe(true);

    fireEvent.click(within(card).getByTestId("endpoint-fetch-models"));
    const panel = await within(card).findByTestId("endpoint-model-picker");
    expect(protocols()).toEqual(["anthropic"]); // its own way first, no second ask
    expect(within(panel).queryByTestId("endpoint-models-protocol-note")).toBeNull();
    fireEvent.click(within(panel).getByText("Sonnet 9"));

    fireEvent.click(within(card).getByRole("button", { name: /Save endpoint/ }));
    await waitFor(() =>
      expect(saved()).toEqual({
        base_url: "https://relay.test/anthropic",
        label: "",
        routable: true,
        default_model: "claude-sonnet-9",
        protocol: "anthropic",
      }),
    );
  });

  it("moves to the way the server answered, says so once, and is saved that way", async () => {
    const card = await openForm();
    fireEvent.click(within(card).getByTestId("endpoint-fetch-models"));
    const panel = await within(card).findByTestId("endpoint-model-picker");
    expect(protocols()).toEqual(["openai", "anthropic"]);
    await waitFor(() => expect(radio(card, "anthropic").checked).toBe(true));
    expect(within(panel).getByTestId("endpoint-models-protocol-note").textContent).toMatch(
      /answered the Anthropic way, so the server type is now Anthropic-compatible\./,
    );
    // The old warning is gone: a saved endpoint now chats its own way.
    expect(card.textContent).not.toMatch(/chats the OpenAI way/);
    expect(card.textContent).not.toMatch(/speaks both/);

    fireEvent.click(within(panel).getByText("Opus 9"));
    fireEvent.click(within(card).getByRole("button", { name: /Save endpoint/ }));
    await waitFor(() => expect(saved()?.protocol).toBe("anthropic"));
  });

  it("chosen Anthropic but the server answers the OpenAI way: it moves back", async () => {
    hooks.answers = { anthropic: { models: [], error: "nope", reason: "not_model_server" }, openai: QWENS };
    const card = await openForm();
    fireEvent.click(radio(card, "anthropic"));
    fireEvent.click(within(card).getByTestId("endpoint-fetch-models"));
    const panel = await within(card).findByTestId("endpoint-model-picker");
    expect(protocols()).toEqual(["anthropic", "openai"]);
    await waitFor(() => expect(radio(card, "openai").checked).toBe(true));
    expect(within(panel).getByTestId("endpoint-models-protocol-note").textContent).toMatch(
      /answered the OpenAI way, so the server type is now OpenAI-compatible\./,
    );
  });

  it("an OpenAI answer to an OpenAI ask changes nothing and says nothing", async () => {
    hooks.answers.openai = QWENS;
    const card = await openForm();
    fireEvent.click(within(card).getByTestId("endpoint-fetch-models"));
    const panel = await within(card).findByTestId("endpoint-model-picker");
    expect(radio(card, "openai").checked).toBe(true);
    expect(within(panel).queryByTestId("endpoint-models-protocol-note")).toBeNull();
    fireEvent.click(within(panel).getByText("qwen3"));
    fireEvent.click(within(card).getByRole("button", { name: /Save endpoint/ }));
    await waitFor(() => expect(saved()?.protocol).toBe("openai"));
  });
});

describe("plain copy", () => {
  it("the address placeholder is the bare example and the key note is three plain sentences", async () => {
    const card = await openForm();
    expect(within(card).getByPlaceholderText("http://localhost:1234/v1")).toBeTruthy();
    expect(
      within(card).getByText(
        "The key is optional. Local servers usually don't need one. If you set it, it is stored encrypted.",
      ),
    ).toBeTruthy();
    const form = card.querySelector("form") as HTMLFormElement;
    const placeholders = Array.from(form.querySelectorAll("input"))
      .map((i) => i.getAttribute("placeholder") ?? "")
      .join(" | ");
    expect(form.textContent ?? "").not.toMatch(/—/);
    expect(placeholders).not.toMatch(/—/);
    expect(placeholders).not.toMatch(/any OpenAI-compatible/);
  });
});

describe("a saved Anthropic endpoint", () => {
  it("its row says Anthropic; an OpenAI row stays unmarked", async () => {
    seed(
      [],
      [
        { node: { id: "relay", label: "Relay", base_url: "https://relay.test", source: "user", routable: true, default_model: "claude-opus-9", protocol: "anthropic" } },
        { node: { id: "box", label: "Box", base_url: "http://box:1234/v1", source: "user", routable: true, default_model: "qwen3" } },
      ],
    );
    render(<ConnectionsPage />);
    const card = (await screen.findByText("Custom endpoint")).closest("#conn-card-custom") as HTMLElement;
    await waitFor(() => expect(within(card).getAllByTestId("endpoint-row-protocol")).toHaveLength(1));
    const tag = within(card).getByTestId("endpoint-row-protocol");
    expect(tag.textContent).toBe("Anthropic");
    expect(tag.closest("div")?.textContent).toContain("Relay");
  });
});

describe("a connected card (the Pixio card at desk width)", () => {
  function pixio() {
    return {
      provider: "pixio",
      display_name: "Pixio (creative media)",
      method: "api_key",
      supports_oauth: false,
      supports_api_key: true,
      oauth_help: "",
      connected: true,
      status: "connected",
      account: "PIXIO_API_KEY (environment)",
      source: "env",
      scopes: [],
    };
  }

  it("its actions row wraps inside the card and no label breaks in two", async () => {
    seed([pixio()]);
    render(<ConnectionsPage />);
    const card = (await screen.findByText("Pixio (creative media)")).closest("#conn-card-pixio") as HTMLElement;
    const row = within(card).getByTestId("conn-card-actions");
    const cls = row.className.split(/\s+/);
    expect(cls).toContain("flex-wrap");
    expect(cls).toContain("min-w-0");
    for (const name of [/Make default/, /Test/, /Disconnect/]) {
      const btn = within(row).getByRole("button", { name });
      expect(btn.className).toMatch(/\bwhitespace-nowrap\b/);
    }
  });

  it("its auth line keeps 'API key' whole and lets the account wrap under it", async () => {
    seed([pixio()]);
    render(<ConnectionsPage />);
    const card = (await screen.findByText("Pixio (creative media)")).closest("#conn-card-pixio") as HTMLElement;
    const auth = within(card).getByTestId("conn-card-auth");
    expect(auth.className.split(/\s+/)).toContain("flex-wrap");
    const chip = within(auth).getByText("API key");
    expect(chip.className).toMatch(/\bwhitespace-nowrap\b/);
    expect(auth.textContent).toContain("PIXIO_API_KEY (environment)");
  });
});
