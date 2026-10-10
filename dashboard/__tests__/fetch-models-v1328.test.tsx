/**
 * v1.328.0 (calm chat B6): "Fetch available models" on the custom-endpoint
 * form.
 *
 * Pinned by what the user does: press the link, type to narrow the list,
 * arrow keys + Enter (or a click) fill the Model field, Escape closes, and
 * typing the id by hand still works. A failure is one plain sentence. The
 * server is asked ONLY on the press (the old form probed on every keystroke of
 * the address and the key), and saving is unchanged: the picked id rides
 * POST /fleet/nodes as `default_model` exactly as a typed one did.
 *
 * Harness: the page's data seams are mocked like ux-wave4-connections-v1316.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
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
    /** The answer to the probe: a value, or a function returning a promise. */
    probe: null as unknown,
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
      const p = hooks.probe;
      if (typeof p === "function") return (p as () => Promise<unknown>)();
      return Promise.resolve(p ?? { models: [], error: "nothing set", reason: "failed" });
    }
    if (path === "/fleet/nodes") return Promise.resolve({ node: { id: "n1", label: "Box" } });
    if (path.endsWith("/verify")) return Promise.resolve({ tool_use: true });
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
const MODELS = ["qwen3-coder-30b", "llama3.2:3b", "qwen2.5-7b-instruct", "glm-4.7-flash"];

const DASH = join(__dirname, "..");
const src = (p: string) => readFileSync(join(DASH, p), "utf-8").replace(/\r\n/g, "\n");

beforeEach(() => {
  hooks.responses = {};
  hooks.posts.length = 0;
  hooks.probe = { models: MODELS, error: null, reason: null };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const probes = () => hooks.posts.filter((p) => p.path === PROBE || p.path === "/providers/endpoint-models");

/** The picker inside a form the way the card holds it. */
function Host({
  url = "http://box:1234/v1",
  apiKey = "",
  onSubmit = () => {},
}: {
  url?: string;
  apiKey?: string;
  onSubmit?: () => void;
}) {
  const [base, setBase] = useState(url);
  const [model, setModel] = useState("");
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      <input aria-label="Address" value={base} onChange={(e) => setBase(e.target.value)} />
      <EndpointModelPicker baseUrl={base} apiKey={apiKey} value={model} onChange={setModel} />
    </form>
  );
}

const modelField = () => screen.getByLabelText("Model") as HTMLInputElement;
const link = () => screen.getByTestId("endpoint-fetch-models");

async function openPicker() {
  fireEvent.click(link());
  return screen.findByTestId("endpoint-model-picker");
}

const optionTexts = (panel: HTMLElement) =>
  within(panel)
    .queryAllByRole("option")
    .map((o) => o.textContent?.replace(/current$/, "") ?? "");

/* ========================================================================== */

describe("the link and the list", () => {
  it("sits next to the Model field and asks the daemon only when pressed", async () => {
    render(<Host apiKey="sk-local" />);
    const row = modelField().previousElementSibling as HTMLElement;
    expect(within(row).getByText("Model")).toBeTruthy();
    expect(within(row).getByTestId("endpoint-fetch-models").textContent).toBe("Fetch available models");
    expect(probes()).toHaveLength(0);

    const panel = await openPicker();
    expect(hooks.posts).toEqual([
      { path: PROBE, body: { base_url: "http://box:1234/v1", api_key: "sk-local", protocol: "openai" } },
    ]);
    expect(optionTexts(panel)).toEqual(MODELS);
    expect(within(panel).getByText("4 models on this server")).toBeTruthy();
  });

  it("type to filter, arrow keys move, Enter picks and fills the field", async () => {
    const submitted = vi.fn();
    render(<Host onSubmit={submitted} />);
    const panel = await openPicker();
    const filter = within(panel).getByRole("combobox");
    expect(document.activeElement).toBe(filter);

    fireEvent.change(filter, { target: { value: "qwen" } });
    expect(optionTexts(panel)).toEqual(["qwen3-coder-30b", "qwen2.5-7b-instruct"]);
    expect(within(panel).getByText("2 of 4 models")).toBeTruthy();

    fireEvent.keyDown(filter, { key: "ArrowDown" });
    const second = within(panel).getAllByRole("option")[1];
    expect(second.getAttribute("aria-selected")).toBe("true");
    expect(filter.getAttribute("aria-activedescendant")).toBe(second.id);

    // fireEvent answers false when the handler prevented the default: a
    // browser's implicit form submission on Enter never happens (jsdom does
    // not simulate that submission, so the return value is the proof).
    expect(fireEvent.keyDown(filter, { key: "Enter" })).toBe(false);
    expect(modelField().value).toBe("qwen2.5-7b-instruct");
    expect(screen.queryByTestId("endpoint-model-picker")).toBeNull();
    expect(document.activeElement).toBe(modelField());
    expect(submitted).not.toHaveBeenCalled(); // Enter picked; it did not save the form
  });

  it("ArrowUp stops at the top and several words narrow together", async () => {
    render(<Host />);
    const panel = await openPicker();
    const filter = within(panel).getByRole("combobox");
    fireEvent.keyDown(filter, { key: "ArrowUp" });
    expect(within(panel).getAllByRole("option")[0].getAttribute("aria-selected")).toBe("true");
    fireEvent.change(filter, { target: { value: "QWEN coder" } });
    expect(optionTexts(panel)).toEqual(["qwen3-coder-30b"]);
  });

  it("a click picks too", async () => {
    render(<Host />);
    const panel = await openPicker();
    fireEvent.click(within(panel).getByText("glm-4.7-flash"));
    expect(modelField().value).toBe("glm-4.7-flash");
    expect(screen.queryByTestId("endpoint-model-picker")).toBeNull();
  });

  it("Escape closes the list and leaves the field as it was", async () => {
    render(<Host />);
    fireEvent.change(modelField(), { target: { value: "my-own-id" } });
    const panel = await openPicker();
    // The fetch never overwrites what the user typed.
    expect(modelField().value).toBe("my-own-id");
    const outer = vi.fn();
    document.addEventListener("keydown", outer);
    fireEvent.keyDown(within(panel).getByRole("combobox"), { key: "Escape" });
    document.removeEventListener("keydown", outer);
    expect(screen.queryByTestId("endpoint-model-picker")).toBeNull();
    expect(outer).not.toHaveBeenCalled(); // Escape did not reach whatever holds the form
    expect(modelField().value).toBe("my-own-id");
  });

  it("Enter with no match uses the words as typed", async () => {
    render(<Host />);
    const panel = await openPicker();
    const filter = within(panel).getByRole("combobox");
    fireEvent.change(filter, { target: { value: "custom-finetune" } });
    expect(within(panel).queryAllByRole("option")).toHaveLength(0);
    expect(within(panel).getByText(/No model matches/)).toBeTruthy();
    fireEvent.keyDown(filter, { key: "Enter" });
    expect(modelField().value).toBe("custom-finetune");
  });

  it("typing the id by hand works with no fetch at all", () => {
    render(<Host />);
    fireEvent.change(modelField(), { target: { value: "llama3" } });
    expect(modelField().value).toBe("llama3");
    expect(probes()).toHaveLength(0);
  });
});

describe("failures are one plain sentence", () => {
  it("shows the daemon's own words and keeps the field typeable", async () => {
    hooks.probe = {
      models: [],
      error: "The server refused this key. Check the key and try again.",
      reason: "refused_key",
    };
    render(<Host apiKey="sk-wrong" />);
    fireEvent.click(link());
    const note = await screen.findByText("The server refused this key. Check the key and try again.");
    expect(note.getAttribute("data-testid")).toBe("endpoint-models-error");
    expect(screen.queryByTestId("endpoint-model-picker")).toBeNull();
    fireEvent.change(modelField(), { target: { value: "typed-anyway" } });
    expect(modelField().value).toBe("typed-anyway");
  });

  it("an address that is not a web address is caught before any request", () => {
    render(<Host url="box:1234" />);
    fireEvent.click(link());
    expect(screen.getByTestId("endpoint-models-error").textContent).toMatch(
      /Enter the endpoint address first/,
    );
    expect(probes()).toHaveLength(0);
  });

  it("a daemon that is not answering is said in words", async () => {
    hooks.probe = () => Promise.reject(new hooks.FakeApiError("Failed to fetch", 0));
    render(<Host />);
    fireEvent.click(link());
    expect(await screen.findByText(/Iron Jarvis is not answering right now/)).toBeTruthy();
  });

  it("a list that lands after the address changed is dropped", async () => {
    let release: (v: unknown) => void = () => {};
    hooks.probe = () => new Promise((r) => (release = r));
    render(<Host />);
    fireEvent.click(link());
    await waitFor(() => expect(link().textContent).toBe("Fetching models…"));
    fireEvent.change(screen.getByLabelText("Address"), { target: { value: "http://other:8000/v1" } });
    await act(async () => {
      release({ models: MODELS, error: null, reason: null });
    });
    expect(screen.queryByTestId("endpoint-model-picker")).toBeNull();
    expect(link().textContent).toBe("Fetch available models");
  });
});

describe("on the Connections page", () => {
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

  it("never probes while the address and key are typed; the picked id is saved as before", async () => {
    seed();
    render(<ConnectionsPage />);
    const card = (await screen.findByText("Custom endpoint")).closest("#conn-card-custom") as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: /Add an endpoint/ }));

    vi.useFakeTimers();
    // v1.329.0 (H3): the placeholder lost its em-dash aside; it is the bare example address now.
    fireEvent.change(within(card).getByPlaceholderText("http://localhost:1234/v1"), {
      target: { value: "http://box:1234/v1" },
    });
    fireEvent.change(within(card).getByLabelText("API key (optional)"), {
      target: { value: "sk-local" },
    });
    await act(async () => {
      vi.advanceTimersByTime(3000);
    });
    vi.useRealTimers();
    expect(probes()).toHaveLength(0); // the old form probed here, every keystroke

    fireEvent.click(within(card).getByTestId("endpoint-fetch-models"));
    const panel = await within(card).findByTestId("endpoint-model-picker");
    fireEvent.click(within(panel).getByText("llama3.2:3b"));
    expect((within(card).getByLabelText("Model") as HTMLInputElement).value).toBe("llama3.2:3b");

    fireEvent.click(within(card).getByRole("button", { name: /Save endpoint/ }));
    await waitFor(() =>
      expect(hooks.posts.find((p) => p.path === "/fleet/nodes")?.body).toEqual({
        base_url: "http://box:1234/v1",
        label: "",
        routable: true,
        default_model: "llama3.2:3b",
        // v1.329.0 (H3): the node carries the API it chats in; an OpenAI list keeps "openai".
        protocol: "openai",
      }),
    );
  });
});

describe("pure parts and the look", () => {
  it("filterModels keeps the order and needs every word", () => {
    expect(filterModels(MODELS, "")).toEqual(MODELS);
    expect(filterModels(MODELS, "  ")).toEqual(MODELS);
    expect(filterModels(MODELS, "Q")).toEqual(["qwen3-coder-30b", "qwen2.5-7b-instruct"]);
    expect(filterModels(MODELS, "7b qwen")).toEqual(["qwen2.5-7b-instruct"]);
    expect(filterModels(MODELS, "nope")).toEqual([]);
  });

  it("uses theme tokens and whole-pixel text only", () => {
    const code = src("components/connections/EndpointModelPicker.tsx");
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
    expect(code).not.toMatch(/rgba?\(/);
    expect(code).not.toMatch(/\b(?:text|bg|border)-(?:amber|emerald|red|green|yellow|sky|blue)-\d/);
    expect(code).not.toMatch(/text-\[\d+\.\d+px\]/);
  });
});
