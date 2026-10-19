/**
 * v1.355.0 lane 5D — model doors (G46, G72, G73), as a new user meets them.
 *
 *  - G46 Gemini takes an API key: the first-run key door lists "Google
 *    Gemini" (its old line pointed to an account login that first needed a
 *    Cloud Console app), says where to get a key, and posts it to the google
 *    connection then tests it. On Settings › Connections the Gemini card shows
 *    the key FIRST, the account login after it; a login-only card (a memory
 *    drive) keeps the login first (control).
 *  - G72 LM Studio and llama.cpp are one press: the chips fill the endpoint
 *    form and press its OWN "Fetch available models", so the server's models
 *    are listed to pick — on Connections' "Add an endpoint" form and in the
 *    key door's Custom endpoint. Nothing is probed before the press.
 *  - G73 Download a model for me: the free-and-private door has no command to
 *    copy; "Download a starter model" asks the daemon for its set-up job BY
 *    NAME (POST /terminals/launch {cli: "ollama-starter"}) and points at the
 *    pane it opened.
 *
 * The REAL ConnectDoors and the REAL ConnectionsPage render here; only the
 * transport seams are mocked (the fetch-models-v1328 harness).
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
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
    puts: [] as { path: string; body: unknown }[],
    /** The model-list probe's answer. */
    probe: null as unknown,
    /** POST /terminals/launch: a pane id, or an error to throw. */
    launch: { id: "t9" } as unknown,
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
    if (path === "/connections/endpoints/models") return Promise.resolve(hooks.probe);
    if (path === "/terminals/launch") {
      return hooks.launch instanceof Error ? Promise.reject(hooks.launch) : Promise.resolve(hooks.launch);
    }
    if (path.endsWith("/test")) {
      return Promise.resolve({ ok: true, detail: "google reachable — credential accepted" });
    }
    if (path === "/fleet/nodes") return Promise.resolve({ node: { id: "n1", label: "LM Studio" } });
    if (path.endsWith("/verify")) return Promise.resolve({ tool_use: true });
    return Promise.resolve({ ok: true });
  }),
  put: vi.fn((path: string, body?: unknown) => {
    hooks.puts.push({ path, body });
    return Promise.resolve({});
  }),
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

import { ConnectDoors } from "@/components/onboarding/ConnectDoors";
import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";

const PROBE = "/connections/endpoints/models";
const MODELS = ["qwen2.5-7b-instruct", "llama-3.2-3b-instruct", "gemma-3-4b"];
const KEY = "AIzaSyD-dashboard-test-key-0123456789";

const DASH = join(__dirname, "..");
const src = (p: string) => readFileSync(join(DASH, p), "utf-8").replace(/\r\n/g, "\n");

beforeEach(() => {
  hooks.responses = { health: { default_provider: "mock", providers: [] } };
  hooks.posts.length = 0;
  hooks.puts.length = 0;
  hooks.probe = { models: MODELS, error: null, reason: null };
  hooks.launch = { id: "t9" };
});
afterEach(cleanup);

const probes = () => hooks.posts.filter((p) => p.path === PROBE);
const optionTexts = (panel: HTMLElement) =>
  within(panel)
    .queryAllByRole("option")
    .map((o) => o.textContent?.replace(/current$/, "") ?? "");

function openDoor(name: RegExp) {
  fireEvent.click(screen.getByRole("button", { name }));
}

/* ========================================================================== */
/*  G46 — the key door takes a Gemini key                                      */
/* ========================================================================== */

describe("G46: the first-run key door takes a Google Gemini key", () => {
  it("lists Google Gemini, says where to get a key, posts the key and tests it", async () => {
    render(<ConnectDoors />);
    openDoor(/I have an API key/);
    // The old pointer to an account login is gone.
    expect(document.body.textContent).not.toMatch(/connects with account login/);

    fireEvent.click(screen.getByRole("button", { name: "Google Gemini" }));
    const box = screen.getByPlaceholderText("AIza…");
    const where = screen.getByTestId("key-door-get-one");
    const link = within(where).getByRole("link", { name: "Google AI Studio" });
    expect(link.getAttribute("href")).toBe("https://aistudio.google.com/apikey");

    fireEvent.change(box, { target: { value: KEY } });
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    // Wait for the line the press ends on (the test's answer), not the POST.
    expect(await screen.findByText(/Connected — google reachable/)).toBeTruthy();
    expect(hooks.posts.map((p) => p.path)).toEqual(["/connections/google/key", "/connections/google/test"]);
    expect(hooks.posts[0].body).toEqual({ key: KEY });
  });

  it("only the provider that needs it gets the where-to-get-one line", () => {
    render(<ConnectDoors />);
    openDoor(/I have an API key/);
    // Anthropic is the starting pick: no line (its key page is not ours to explain here).
    expect(screen.queryByTestId("key-door-get-one")).toBeNull();
  });
});

/* ========================================================================== */
/*  G73 — Download a model for me, without a terminal                          */
/* ========================================================================== */

describe("G73: the free-and-private door downloads a starter model with one press", () => {
  it("no command to copy; the press asks for the daemon's job by name and points at the pane", async () => {
    render(<ConnectDoors />);
    openDoor(/Free & private on this PC/);
    expect(document.body.textContent).not.toMatch(/ollama pull/);
    expect(document.body.textContent).not.toMatch(/copy this into a terminal/i);
    expect(screen.queryByTestId("starter-model-pane")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Download a starter model/ }));
    const pane = await screen.findByTestId("starter-model-pane");
    expect(pane.textContent).toMatch(/A Build pane opened and is downloading it/);
    expect(within(pane).getByRole("link", { name: "Open the Build pane" }).getAttribute("href")).toBe(
      "/terminals?focus=t9",
    );
    expect(hooks.posts).toEqual([{ path: "/terminals/launch", body: { cli: "ollama-starter" } }]);
    // The press replaced itself with the pane line: it cannot be pressed twice.
    expect(screen.queryByRole("button", { name: /Download a starter model/ })).toBeNull();
    // Step 3 is unchanged: the user still tells Iron Jarvis to use it.
    expect(screen.getByRole("button", { name: /Connect to Ollama on this PC/ })).toBeTruthy();
  });

  it("a refusal is said in the daemon's own words and the press stays", async () => {
    hooks.launch = new hooks.FakeApiError("Too many panes are open. Close one and try again.", 429);
    render(<ConnectDoors />);
    openDoor(/Free & private on this PC/);
    fireEvent.click(screen.getByRole("button", { name: /Download a starter model/ }));
    const alert = await screen.findByText("Too many panes are open. Close one and try again.");
    expect(alert.getAttribute("role")).toBe("alert");
    expect(screen.getByRole("button", { name: /Download a starter model/ })).toBeTruthy();
    expect(screen.queryByTestId("starter-model-pane")).toBeNull();
  });
});

/* ========================================================================== */
/*  G72 — LM Studio and llama.cpp are one press                                */
/* ========================================================================== */

describe("G72: the key door's Custom endpoint", () => {
  it("'LM Studio on this PC' fills the address and lists the server's models to pick", async () => {
    render(<ConnectDoors />);
    openDoor(/I have an API key/);
    fireEvent.click(screen.getByRole("button", { name: "Custom endpoint" }));
    expect(probes()).toHaveLength(0); // nothing is asked before the press

    fireEvent.click(screen.getByRole("button", { name: "LM Studio on this PC" }));
    expect((screen.getByLabelText("Base URL") as HTMLInputElement).value).toBe("http://localhost:1234/v1");
    const panel = await screen.findByTestId("endpoint-model-picker");
    expect(optionTexts(panel)).toEqual(MODELS);
    expect(probes()).toEqual([
      { path: PROBE, body: { base_url: "http://localhost:1234/v1", api_key: "", protocol: "openai" } },
    ]);

    fireEvent.click(within(panel).getByText("gemma-3-4b"));
    expect((screen.getByLabelText("Model") as HTMLInputElement).value).toBe("gemma-3-4b");
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() =>
      expect(hooks.puts).toEqual([
        {
          path: "/settings",
          body: { values: { custom_base_url: "http://localhost:1234/v1", custom_model: "gemma-3-4b" } },
        },
      ]),
    );
  });

  it("'llama.cpp' asks llama-server's own port", async () => {
    render(<ConnectDoors />);
    openDoor(/I have an API key/);
    fireEvent.click(screen.getByRole("button", { name: "Custom endpoint" }));
    fireEvent.click(screen.getByRole("button", { name: "llama.cpp" }));
    await screen.findByTestId("endpoint-model-picker");
    expect(probes().map((p) => (p.body as { base_url: string }).base_url)).toEqual(["http://localhost:8080/v1"]);
  });

  it("a server that is not running says so in the picker's own words", async () => {
    hooks.probe = {
      models: [],
      error: "Could not reach that address. Check the URL and that the server is running.",
      reason: "unreachable",
    };
    render(<ConnectDoors />);
    openDoor(/I have an API key/);
    fireEvent.click(screen.getByRole("button", { name: "Custom endpoint" }));
    fireEvent.click(screen.getByRole("button", { name: "LM Studio on this PC" }));
    const err = await screen.findByTestId("endpoint-models-error");
    expect(err.textContent).toMatch(/server is running/);
  });
});

describe("G72: Settings › Connections' Add an endpoint form", () => {
  function seedCustom() {
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
  }

  async function openForm() {
    seedCustom();
    render(<ConnectionsPage />);
    const card = (await screen.findByText("Model servers on your network")).closest(
      "#conn-card-custom",
    ) as HTMLElement;
    fireEvent.click(within(card).getByRole("button", { name: /Add an endpoint/ }));
    return card;
  }

  it("'LM Studio on this PC' fills name, address and server type, lists the models, and saves the pick", async () => {
    const card = await openForm();
    expect(probes()).toHaveLength(0);
    fireEvent.click(within(card).getByRole("button", { name: "LM Studio on this PC" }));

    expect((within(card).getByPlaceholderText("http://localhost:1234/v1") as HTMLInputElement).value).toBe(
      "http://localhost:1234/v1",
    );
    expect((within(card).getByPlaceholderText("e.g. vLLM box / Ollama Cloud") as HTMLInputElement).value).toBe(
      "LM Studio",
    );
    const panel = await within(card).findByTestId("endpoint-model-picker");
    expect(optionTexts(panel)).toEqual(MODELS);
    expect(probes()).toEqual([
      { path: PROBE, body: { base_url: "http://localhost:1234/v1", api_key: "", protocol: "openai" } },
    ]);

    fireEvent.click(within(panel).getByText("qwen2.5-7b-instruct"));
    fireEvent.click(within(card).getByRole("button", { name: /Save endpoint/ }));
    await waitFor(() =>
      expect(hooks.posts.find((p) => p.path === "/fleet/nodes")?.body).toEqual({
        base_url: "http://localhost:1234/v1",
        label: "LM Studio",
        routable: true,
        default_model: "qwen2.5-7b-instruct",
        protocol: "openai",
      }),
    );
  });

  it("a name the user already typed is kept", async () => {
    const card = await openForm();
    const name = within(card).getByPlaceholderText("e.g. vLLM box / Ollama Cloud") as HTMLInputElement;
    fireEvent.change(name, { target: { value: "Studio box" } });
    fireEvent.click(within(card).getByRole("button", { name: "llama.cpp" }));
    await within(card).findByTestId("endpoint-model-picker");
    expect(name.value).toBe("Studio box");
    expect(probes().map((p) => (p.body as { base_url: string }).base_url)).toEqual(["http://localhost:8080/v1"]);
  });
});

/* ========================================================================== */
/*  G46 — the Gemini card shows the key first                                  */
/* ========================================================================== */

describe("G46: Settings › Connections' Gemini card", () => {
  const base = {
    oauth_help: "Logging in with your Google account needs an OAuth 2.0 Client ID you create in Google Cloud Console first.",
    supports_oauth: true,
    connected: false,
    status: "disconnected",
    account: "",
    source: "",
    scopes: [],
    oauth_client_configured: false,
  };

  function seed() {
    hooks.responses["/connections"] = {
      connections: [
        { ...base, provider: "google", display_name: "Google (Gemini)", method: "api_key", supports_api_key: true },
        {
          ...base,
          provider: "google_drive",
          display_name: "Google Drive (memory)",
          method: "oauth",
          supports_api_key: false,
          oauth_help: "Connect a Google Drive to search + ingest its files into memory.",
        },
      ],
    };
    hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
    hooks.responses["/fleet"] = { nodes: [] };
  }

  const cardOf = (p: string) => document.getElementById(`conn-card-${p}`) as HTMLElement;
  const before = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

  it("the key comes first (the one clear button); the account login follows, quiet", async () => {
    seed();
    render(<ConnectionsPage />);
    await screen.findByText("Google (Gemini)");
    const card = cardOf("google");
    const connect = within(card).getByRole("button", { name: "Connect" });
    const login = within(card).getByRole("button", { name: /Set up & log in/ });
    expect(before(connect, login)).toBe(true);
    expect(connect.className).toMatch(/\bbtn-soft\b/);
    expect(login.className).not.toMatch(/\bbtn-soft\b/);
    const divider = within(card).getByText("or log in with your account");
    expect(before(connect, divider) && before(divider, login)).toBe(true);
    expect(within(card).queryByText("or use an API key")).toBeNull();
    expect(within(card).queryByRole("button", { name: /Use an API key instead/ })).toBeNull();
    expect(within(card).getByText("API key")).toBeTruthy(); // the method chip

    fireEvent.click(connect);
    const box = within(card).getByPlaceholderText("AIza…");
    expect(within(card).getByRole("link", { name: /Google AI Studio/ }).getAttribute("href")).toBe(
      "https://aistudio.google.com/apikey",
    );
    fireEvent.change(box, { target: { value: KEY } });
    fireEvent.click(within(card).getByRole("button", { name: "Connect" }));
    await waitFor(() =>
      expect(hooks.posts.map((p) => p.path)).toEqual(["/connections/google/key", "/connections/google/test"]),
    );
    expect(hooks.posts[0].body).toEqual({ key: KEY });
  });

  it("control: a login-only card keeps the login first, in the outlined style", async () => {
    seed();
    render(<ConnectionsPage />);
    await screen.findByText("Google Drive (memory)");
    const card = cardOf("google_drive");
    const login = within(card).getByRole("button", { name: /Set up & log in/ });
    expect(login.className).toMatch(/\bbtn-soft\b/);
    expect(within(card).queryByText("or log in with your account")).toBeNull();
    expect(within(card).queryByRole("button", { name: "Connect" })).toBeNull();
  });
});

/* ========================================================================== */
/*  The look: theme tokens, whole pixels                                       */
/* ========================================================================== */

describe("the look stays the app's", () => {
  it("the new pieces use theme tokens and whole-pixel text only", () => {
    for (const f of ["components/connections/LocalServerChips.tsx", "components/UnattendedAskNote.tsx"]) {
      const code = src(f);
      expect(code, f).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
      expect(code, f).not.toMatch(/rgba?\(/);
      expect(code, f).not.toMatch(/text-\[\d+\.\d+px\]/);
      expect(code, f).not.toMatch(/shadow-/);
    }
  });
});
