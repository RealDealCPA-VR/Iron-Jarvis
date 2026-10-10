/**
 * v1.329.0 (calm chat wave 5, S4): "Fetch available models" reaches an
 * Anthropic-compatible server too, and a narrow connection card's name no
 * longer runs under its status pill.
 *
 * The picker asks the OpenAI way first. Only a refused or missing key, or
 * "not a model server", earns ONE more ask the Anthropic way (same address,
 * same key). Only a real list from that ask replaces the first answer; every
 * other outcome keeps the first answer's words. A list that came the
 * Anthropic way says so and shows the server's display names, which the
 * filter also searches. (v1.329.0 H3 changed the note: a saved endpoint now
 * chats in its own protocol, so the note says the server type moved to
 * Anthropic-compatible instead of warning that replies go the OpenAI way.)
 *
 * Harness: the page's data seams are mocked like fetch-models-v1328.
 */

import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    /** protocol -> the answer (a value, or a function returning a promise). */
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
      const a = hooks.answers[proto];
      if (typeof a === "function") return (a as () => Promise<unknown>)();
      return Promise.resolve(a ?? { models: [], error: "nothing set", reason: "failed" });
    }
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
import { EndpointModelPicker, filterModels } from "@/components/connections/EndpointModelPicker";

const PROBE = "/connections/endpoints/models";
const REFUSED = {
  models: [],
  error: "The server refused this key. Check the key and try again.",
  reason: "refused_key",
};
const CLAUDES = {
  models: ["claude-opus-9", "claude-sonnet-9", "relay-large-2"],
  error: null,
  reason: null,
  protocol: "anthropic",
  labels: { "claude-opus-9": "Opus 9", "claude-sonnet-9": "Sonnet 9" },
  partial: false,
};

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

function Host({ url = "https://relay.test/anthropic", apiKey = "sk-ant-local" }: { url?: string; apiKey?: string }) {
  const [base, setBase] = useState(url);
  const [model, setModel] = useState("");
  return (
    <form onSubmit={(e) => e.preventDefault()}>
      <input aria-label="Address" value={base} onChange={(e) => setBase(e.target.value)} />
      <EndpointModelPicker baseUrl={base} apiKey={apiKey} value={model} onChange={setModel} />
    </form>
  );
}

const modelField = () => screen.getByLabelText("Model") as HTMLInputElement;
const link = () => screen.getByTestId("endpoint-fetch-models");
const ids = (panel: HTMLElement) =>
  within(panel)
    .queryAllByRole("option")
    .map((o) => o.firstElementChild?.firstChild?.textContent ?? "");

/* ========================================================================== */

describe("an Anthropic-compatible server", () => {
  it("is asked the Anthropic way once after the OpenAI way is refused, with the same address and key", async () => {
    render(<Host />);
    fireEvent.click(link());
    const panel = await screen.findByTestId("endpoint-model-picker");
    expect(probes().map((p) => p.body)).toEqual([
      { base_url: "https://relay.test/anthropic", api_key: "sk-ant-local", protocol: "openai" },
      { base_url: "https://relay.test/anthropic", api_key: "sk-ant-local", protocol: "anthropic" },
    ]);
    expect(ids(panel)).toEqual(["claude-opus-9", "claude-sonnet-9", "relay-large-2"]);
    expect(screen.queryByTestId("endpoint-models-error")).toBeNull();
  });

  it("shows the display names, says the list came the Anthropic way, and a pick fills the field with the id", async () => {
    render(<Host />);
    fireEvent.click(link());
    const panel = await screen.findByTestId("endpoint-model-picker");
    expect(within(panel).getAllByTestId("endpoint-model-label").map((n) => n.textContent)).toEqual([
      "Opus 9",
      "Sonnet 9",
    ]);
    expect(within(panel).getByTestId("endpoint-models-protocol-note").textContent).toMatch(
      /answered the Anthropic way, so the server type is now Anthropic-compatible/s,
    );
    fireEvent.click(within(panel).getByText("Sonnet 9"));
    expect(modelField().value).toBe("claude-sonnet-9");
  });

  it("the filter finds a model by its display name", async () => {
    render(<Host />);
    fireEvent.click(link());
    const panel = await screen.findByTestId("endpoint-model-picker");
    fireEvent.change(within(panel).getByRole("combobox"), { target: { value: "sonnet" } });
    expect(ids(panel)).toEqual(["claude-sonnet-9"]);
    fireEvent.change(within(panel).getByRole("combobox"), { target: { value: "opus 9" } });
    expect(ids(panel)).toEqual(["claude-opus-9"]);
    expect(filterModels(["a-1", "b-2"], "beta", { "b-2": "Beta Two" })).toEqual(["b-2"]);
    expect(filterModels(["a-1", "b-2"], "beta")).toEqual([]); // no names, no match
  });

  it("a partial list says the server has more", async () => {
    hooks.answers.anthropic = { ...CLAUDES, partial: true };
    render(<Host />);
    fireEvent.click(link());
    const panel = await screen.findByTestId("endpoint-model-picker");
    expect(within(panel).getByTestId("endpoint-models-count").textContent).toBe(
      "3 models listed. The server has more, so type the id if yours is not here.",
    );
  });

  it.each(["needs_key", "not_model_server"])("a %s answer also earns the second ask", async (reason) => {
    hooks.answers.openai = { models: [], error: "first words", reason };
    render(<Host />);
    fireEvent.click(link());
    await screen.findByTestId("endpoint-model-picker");
    expect(protocols()).toEqual(["openai", "anthropic"]);
  });
});

describe("the first answer stands", () => {
  it("an OpenAI list is used as it is, with no second ask and no note", async () => {
    hooks.answers.openai = { models: ["qwen3", "llama3"], error: null, reason: null };
    render(<Host />);
    fireEvent.click(link());
    const panel = await screen.findByTestId("endpoint-model-picker");
    expect(protocols()).toEqual(["openai"]);
    expect(ids(panel)).toEqual(["qwen3", "llama3"]);
    expect(within(panel).queryByTestId("endpoint-models-protocol-note")).toBeNull();
    expect(within(panel).queryByTestId("endpoint-model-label")).toBeNull();
  });

  it.each(["unreachable", "timeout", "server_error", "no_models", "bad_address"])(
    "a %s answer is not asked again",
    async (reason) => {
      hooks.answers.openai = { models: [], error: `words for ${reason}`, reason };
      render(<Host />);
      fireEvent.click(link());
      expect(await screen.findByText(`words for ${reason}`)).toBeTruthy();
      expect(protocols()).toEqual(["openai"]);
    },
  );

  it("when the Anthropic way fails too, the OpenAI answer's words are shown", async () => {
    hooks.answers.anthropic = { models: [], error: "anthropic words", reason: "not_model_server" };
    render(<Host />);
    fireEvent.click(link());
    await waitFor(() =>
      expect(screen.getByTestId("endpoint-models-error").textContent).toBe(REFUSED.error),
    );
    expect(protocols()).toEqual(["openai", "anthropic"]);
    expect(screen.queryByTestId("endpoint-model-picker")).toBeNull();
  });

  it("an older daemon that refuses the word keeps the OpenAI answer's words", async () => {
    hooks.answers.anthropic = () =>
      Promise.reject(new hooks.FakeApiError("Only OpenAI-compatible endpoints can be added here.", 400));
    render(<Host />);
    fireEvent.click(link());
    await waitFor(() =>
      expect(screen.getByTestId("endpoint-models-error").textContent).toBe(REFUSED.error),
    );
    expect(link().textContent).toBe("Fetch available models");
  });

  it("a second answer that lands after the address changed is dropped", async () => {
    let release: (v: unknown) => void = () => {};
    hooks.answers.anthropic = () => new Promise((r) => (release = r));
    render(<Host />);
    fireEvent.click(link());
    await waitFor(() => expect(protocols()).toEqual(["openai", "anthropic"]));
    fireEvent.change(screen.getByLabelText("Address"), { target: { value: "http://other:8000/v1" } });
    await act(async () => {
      release(CLAUDES);
    });
    expect(screen.queryByTestId("endpoint-model-picker")).toBeNull();
    expect(screen.queryByTestId("endpoint-models-error")).toBeNull();
    expect(link().textContent).toBe("Fetch available models");
  });
});

describe("the card header", () => {
  function seed() {
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
    hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
    hooks.responses["/fleet"] = { nodes: [] };
    hooks.responses["health"] = { default_provider: "mock", providers: [] };
  }

  it("wraps: the name keeps a floor and the pill moves to its own line instead of covering it", async () => {
    seed();
    render(<ConnectionsPage />);
    const card = (await screen.findByText("Custom endpoint")).closest("#conn-card-custom") as HTMLElement;
    const header = within(card).getByTestId("conn-card-header");
    const cls = header.className.split(/\s+/);
    expect(cls).toContain("flex-wrap");
    expect(cls).toContain("gap-y-2");
    const name = header.firstElementChild as HTMLElement;
    expect(name.textContent).toContain("Custom endpoint");
    const nameCls = name.className.split(/\s+/);
    // A floor on the name block is what makes the row wrap instead of
    // squeezing the name to nothing beside a nowrap pill; capped at the
    // card's width so a very narrow card never overflows.
    expect(nameCls).toContain("min-w-[min(11rem,100%)]");
    expect(nameCls).toContain("flex-1");
    expect(nameCls).not.toContain("min-w-0");
    const pill = within(header).getByTestId("conn-status-pill");
    expect(pill.parentElement).toBe(header);
    expect(pill.className).toMatch(/\bwhitespace-nowrap\b/);
  });
});
