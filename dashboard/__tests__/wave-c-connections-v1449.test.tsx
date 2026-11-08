/**
 * Readiness Wave C items 22, 23 and 24 on the Directory, Connections and
 * Tools pages (v1.449.0).
 *
 *  22. A Drive / OneDrive / Dropbox card with no OAuth client holds the App
 *      ID / App secret boxes itself (Directory AND Connections), says the
 *      vendor-console step in plain words, gives the redirect address a Copy
 *      button, and Save writes `<provider>_oauth_client_id` /
 *      `_oauth_client_secret` through POST /secrets — nobody types a name.
 *  23. "Needs Node.js" carries a real "Get Node.js (free)" link (Directory,
 *      Tools, and a Node install error); the Microsoft 365 card links "How
 *      your admin sets this up" to the Handbook and labels its GitHub link.
 *  24. The default model's card and endpoint row say, before the press, what
 *      Disconnect / Delete does to the default (the daemon's words), and the
 *      removal's answer says where the default landed.
 *
 * Harness: the two pages' data seams are mocked (the shape of
 * ux-wave4-connections-v1316 and connections-home-v13530).
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
import DirectoryPage from "@/components/settings/pages/DirectoryPage";
import ToolsPage from "@/components/settings/pages/ToolsPage";
import { docsLinkLabel, runtimeDownload, runtimeLinkInError } from "@/lib/directoryLinks";
import { oauthClientIdName, oauthClientSecretName, oauthRedirectAddress } from "@/lib/oauthApp";

beforeEach(() => {
  hooks.responses = {};
  hooks.failing = {};
  hooks.delAnswers = {};
  hooks.gets.length = 0;
  hooks.posts.length = 0;
  hooks.dels.length = 0;
  hooks.copied.length = 0;
  hooks.responses["/mcp/import/sources"] = { sources: [] };
  hooks.responses["/mcp/servers"] = { servers: [], auto_approve: false, auto_approve_effective: false };
  hooks.responses["/mcp/catalog"] = { catalog: [] };
  hooks.responses["/tools/custom"] = { tools: [] };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  hooks.responses["/fleet"] = { nodes: [] };
});
afterEach(() => cleanup());

/* ------------------------------------------------------------ fixtures --- */

function connector(id: string, name: string, over: Record<string, unknown> = {}) {
  return {
    id,
    name,
    category: "Storage",
    glyph: "",
    blurb: `${name} blurb.`,
    unlocks: `${name} unlocks.`,
    connect_via: "oauth",
    scopes: [],
    docs_url: "",
    fields: [],
    provider: id,
    connected: false,
    status: "disconnected",
    tools_loaded: 0,
    tool_names: [],
    needs: "",
    package_note: "",
    href: "",
    action: "",
    ...over,
  };
}

function conn(provider: string, over: Record<string, unknown> = {}) {
  return {
    provider,
    display_name: provider,
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

const card = (id: string) => screen.getByTestId(`connector-card-${id}`);
const connCard = (provider: string) => document.getElementById(`conn-card-${provider}`) as HTMLElement;

/* ========================================================================== */
/*  22. the App ID / App secret boxes                                          */
/* ========================================================================== */

describe("22. a drive's sign-in boxes are on the card (Directory)", () => {
  it("the Directory card holds the form, the vendor step in words, the redirect with Copy, and Save writes the daemon's names", async () => {
    hooks.responses["/connectors"] = {
      connectors: [connector("dropbox", "Dropbox", { needs: "Needs a one-time Dropbox developer setup" })],
      categories: ["Storage"],
    };
    render(<DirectoryPage />);
    await screen.findByTestId("connector-card-dropbox");
    const fold = screen.getByTestId("connector-needs-howto-dropbox");
    expect(fold.tagName).toBe("DETAILS");
    expect(fold.querySelector("summary")?.textContent).toMatch(/Set up the sign-in/);
    const form = within(fold).getByTestId("oauth-app-form-dropbox");
    // The vendor-console step, in plain words (never a vault name to type).
    expect(within(form).getByTestId("oauth-app-form-dropbox-step").textContent).toContain("Dropbox App Console");
    expect(within(form).getByTestId("oauth-app-form-dropbox-step").textContent).toContain("redirect address");
    expect(within(form).getByTestId("oauth-app-form-dropbox-redirect").textContent).toBe(
      oauthRedirectAddress("dropbox"),
    );
    // Copy puts the exact address on the clipboard.
    fireEvent.click(within(form).getByTestId("oauth-app-form-dropbox-copy"));
    await waitFor(() => expect(hooks.copied).toEqual([oauthRedirectAddress("dropbox")]));
    // No "/connections" trip any more.
    expect(within(card("dropbox")).queryByText(/Set it up on the Connections page/)).toBeNull();

    // Save: the id and the secret land under the names the daemon reads.
    fireEvent.change(within(form).getByTestId("oauth-app-form-dropbox-id"), { target: { value: " app-123 " } });
    fireEvent.change(within(form).getByTestId("oauth-app-form-dropbox-secret"), { target: { value: "s3cr3t" } });
    fireEvent.click(within(form).getByTestId("oauth-app-form-dropbox-save"));
    await waitFor(() =>
      expect(within(form).getByTestId("oauth-app-form-dropbox-note").textContent).toContain("Saved"),
    );
    const secrets = hooks.posts.filter((p) => p.path === "/secrets").map((p) => p.body as Record<string, string>);
    expect(secrets.map((s) => [s.name, s.value])).toEqual([
      [oauthClientIdName("dropbox"), "app-123"],
      [oauthClientSecretName("dropbox"), "s3cr3t"],
    ]);
    expect(oauthClientIdName("dropbox")).toBe("dropbox_oauth_client_id");
    // The secret box never keeps the value.
    expect((within(form).getByTestId("oauth-app-form-dropbox-secret") as HTMLInputElement).value).toBe("");
  });

  it("a secret alone is refused in words, and nothing is posted", async () => {
    hooks.responses["/connectors"] = {
      connectors: [connector("onedrive", "OneDrive", { needs: "Needs a one-time Microsoft developer setup" })],
      categories: ["Storage"],
    };
    render(<DirectoryPage />);
    await screen.findByTestId("connector-card-onedrive");
    const form = screen.getByTestId("oauth-app-form-onedrive");
    fireEvent.change(within(form).getByTestId("oauth-app-form-onedrive-secret"), { target: { value: "x" } });
    const save = within(form).getByTestId("oauth-app-form-onedrive-save") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.submit(form);
    await waitFor(() =>
      expect(within(form).getByTestId("oauth-app-form-onedrive-note").textContent).toMatch(/app.s ID first/),
    );
    expect(hooks.posts.filter((p) => p.path === "/secrets")).toEqual([]);
  });

  it("a card whose sign-in can already start shows no form", async () => {
    hooks.responses["/connectors"] = {
      connectors: [connector("google_drive", "Google Drive", { needs: "" })],
      categories: ["Storage"],
    };
    render(<DirectoryPage />);
    await screen.findByTestId("connector-card-google_drive");
    expect(screen.queryByTestId("oauth-app-form-google_drive")).toBeNull();
  });
});

describe("22. the same boxes on the Connections card", () => {
  it("the folded one-time setup holds the form and names the kept secrets quietly", async () => {
    hooks.responses["/connections"] = {
      connections: [
        conn("anthropic", { display_name: "Anthropic" }),
        conn("dropbox", {
          display_name: "Dropbox (memory)",
          method: "oauth",
          supports_oauth: true,
          supports_api_key: false,
          oauth_help: "Connect Dropbox to search + ingest its files into memory.",
          oauth_client_configured: false,
        }),
      ],
    };
    hooks.responses["health"] = { default_provider: "mock", providers: [] };
    render(<ConnectionsPage />);
    await screen.findByText("Anthropic");
    const c = connCard("dropbox");
    const details = c.querySelector("details") as HTMLElement;
    expect(details.textContent).toMatch(/One-time setup/);
    const form = within(details).getByTestId("oauth-app-form-dropbox");
    expect(within(form).getByTestId("oauth-app-form-dropbox-redirect").textContent).toBe(
      "http://localhost:8787/oauth/dropbox/callback",
    );
    // The stored names are said, not typed (a person who opens Secrets knows what they see).
    expect(details.textContent).toContain("dropbox_oauth_client_id");
    // The old instruction is gone.
    expect(details.textContent).not.toMatch(/save the Dropbox app id/i);
    fireEvent.change(within(form).getByTestId("oauth-app-form-dropbox-id"), { target: { value: "id-1" } });
    fireEvent.click(within(form).getByTestId("oauth-app-form-dropbox-save"));
    await waitFor(() => expect(hooks.posts.some((p) => p.path === "/secrets")).toBe(true));
    expect((hooks.posts.find((p) => p.path === "/secrets")!.body as { name: string }).name).toBe(
      "dropbox_oauth_client_id",
    );
  });
});

/* ========================================================================== */
/*  23. Node.js link, admin guide, docs label                                   */
/* ========================================================================== */

describe("23. 'Needs Node.js' gets a real link; Microsoft 365 links its admin steps", () => {
  it("runtimeDownload / runtimeLinkInError / docsLinkLabel (pure)", () => {
    expect(runtimeDownload("Node")).toEqual({ label: "Get Node.js (free)", href: "https://nodejs.org" });
    expect(runtimeDownload("Node.js", "https://nodejs.org/en")).toEqual({
      label: "Get Node.js (free)",
      href: "https://nodejs.org/en",
    });
    expect(runtimeDownload("Python (uv)")?.label).toBe("Get uv (free)");
    expect(runtimeDownload("")).toBeNull();
    expect(runtimeDownload("a helper program")).toBeNull();
    expect(runtimeLinkInError("Node.js isn't installed on this PC … Install Node.js (the LTS version from nodejs.org)")).toEqual(
      runtimeDownload("Node.js"),
    );
    expect(runtimeLinkInError("the token was refused")).toBeNull();
    expect(docsLinkLabel("https://github.com/Softeria/ms-365-mcp-server")).toBe("The program’s own page on GitHub");
    expect(docsLinkLabel("https://docs.stripe.com/mcp")).toBe("Docs");
    expect(docsLinkLabel("")).toBe("Docs");
  });

  it("the Directory card: the link beside the badge, the admin guide, the labelled GitHub link", async () => {
    hooks.responses["/connectors"] = {
      connectors: [
        connector("microsoft_365", "Microsoft 365 (Outlook, Calendar, OneDrive)", {
          category: "Productivity",
          connect_via: "mcp",
          provider: "",
          docs_url: "https://github.com/Softeria/ms-365-mcp-server",
          runtime_ready: false,
          runtime: "Node.js",
          runtime_url: "https://nodejs.org",
        }),
      ],
      categories: ["Productivity"],
    };
    render(<DirectoryPage />);
    await screen.findByTestId("connector-card-microsoft_365");
    const link = screen.getByTestId("connector-get-runtime-microsoft_365") as HTMLAnchorElement;
    expect(link.textContent).toContain("Get Node.js (free)");
    expect(link.getAttribute("href")).toBe("https://nodejs.org");
    expect(link.getAttribute("target")).toBe("_blank");
    const guide = screen.getByTestId("connector-admin-guide-microsoft_365");
    expect(guide.textContent).toContain("How your admin sets this up");
    expect(guide.getAttribute("href")).toBe("/help?guide=handbook&section=microsoft-365-locked-down-for-client-data");
    const docs = screen.getByTestId("connector-docs-microsoft_365");
    expect(docs.textContent).toContain("The program’s own page on GitHub");
    expect(docs.textContent).not.toMatch(/^Docs/);
  });

  it("a connect refused for a missing Node.js gets the link in the error note", async () => {
    hooks.responses["/connectors"] = {
      connectors: [
        connector("microsoft_365", "Microsoft 365", {
          category: "Productivity",
          connect_via: "mcp",
          provider: "",
          fields: [],
        }),
      ],
      categories: ["Productivity"],
    };
    hooks.failing["/connectors/microsoft_365/connect"] = {
      status: 400,
      message: "Node.js isn't installed on this PC, so this app's program can't run. Install Node.js (the LTS version from nodejs.org), then connect again.",
    };
    render(<DirectoryPage />);
    await screen.findByTestId("connector-card-microsoft_365");
    fireEvent.click(within(card("microsoft_365")).getByRole("button", { name: /Connect/ }));
    const form = card("microsoft_365").querySelector("form") as HTMLFormElement;
    fireEvent.submit(form);
    const link = await screen.findByTestId("connector-error-get-runtime-microsoft_365");
    expect(link.getAttribute("href")).toBe("https://nodejs.org");
    expect(link.textContent).toBe("Get Node.js (free)");
  });

  it("the Tools catalog card gets the link only while the program is really missing", async () => {
    const pack = (id: string, needs: string, over: Record<string, unknown> = {}) => ({
      id,
      name: id,
      description: `${id} description.`,
      command: "npx",
      args: ["-y", `pkg-${id}`],
      category: "reference",
      needs,
      ...over,
    });
    hooks.responses["/mcp/catalog"] = {
      catalog: [
        pack("fetch", "Node", { runtime_ready: false }),
        pack("memory", "Node", { runtime_ready: true }),
        pack("box", "Python (uv)", { runtime_ready: false }),
      ],
    };
    render(<ToolsPage />);
    const fetchLink = await screen.findByTestId("catalog-get-runtime-fetch");
    expect(fetchLink.textContent).toBe("Get Node.js (free)");
    expect(fetchLink.getAttribute("href")).toBe("https://nodejs.org");
    expect(screen.queryByTestId("catalog-get-runtime-memory")).toBeNull();
    expect(screen.getByTestId("catalog-get-runtime-box").textContent).toBe("Get uv (free)");
  });
});

/* ========================================================================== */
/*  24. the default is named before it goes                                    */
/* ========================================================================== */

describe("24. the default's card says what Disconnect does, and where the default landed", () => {
  const impact = {
    affected: true,
    current: { provider: "google", label: "Google Gemini" },
    replacement: { provider: "openrouter", model: "openrouter/auto", label: "OpenRouter", demo: false },
    demo: false,
    words: "Google Gemini is what answers your chats right now. Jarvis will use OpenRouter instead.",
  };

  it("the connection card: the daemon's words before the press, and after", async () => {
    hooks.responses["/connections"] = {
      connections: [
        conn("google", { display_name: "Google Gemini", connected: true, status: "connected" }),
        conn("openrouter", { display_name: "OpenRouter", connected: true, status: "connected" }),
      ],
    };
    hooks.responses["health"] = { default_provider: "google", providers: [] };
    hooks.responses["/fleet/default-after-removal?leaving=google"] = impact;
    hooks.delAnswers["/connections/google"] = {
      provider: "google",
      status: "disconnected",
      default_replaced: { from: "google", to: "openrouter", words: "Jarvis now answers with OpenRouter." },
    };
    render(<ConnectionsPage />);
    await screen.findByText("Google Gemini");
    const c = connCard("google");
    const line = await within(c).findByTestId("conn-default-impact");
    expect(line.textContent).toBe(impact.words);
    // The other card — not the default — asks nothing and says nothing.
    expect(within(connCard("openrouter")).queryByTestId("conn-default-impact")).toBeNull();
    expect(hooks.gets.filter((p) => p.startsWith("/fleet/default-after-removal"))).toEqual([
      "/fleet/default-after-removal?leaving=google",
    ]);
    // Two presses, then the daemon's own words about what happened.
    const disconnect = within(c).getByRole("button", { name: /Disconnect/ });
    fireEvent.click(disconnect);
    fireEvent.click(within(c).getByRole("button", { name: /Confirm\?|Disconnect/ }));
    await waitFor(() => expect(hooks.dels).toContain("/connections/google"));
    expect((await within(c).findByTestId("conn-default-moved")).textContent).toBe("Jarvis now answers with OpenRouter.");
  });

  it("a card that is not the default asks the daemon nothing", async () => {
    hooks.responses["/connections"] = {
      connections: [conn("google", { display_name: "Google Gemini", connected: true, status: "connected" })],
    };
    hooks.responses["health"] = { default_provider: "mock", providers: [] };
    render(<ConnectionsPage />);
    await screen.findByText("Google Gemini");
    expect(hooks.gets.some((p) => p.startsWith("/fleet/default-after-removal"))).toBe(false);
    expect(screen.queryByTestId("conn-default-impact")).toBeNull();
  });

  it("the demo fallback is drawn as a warning (data-demo) in the daemon's words", async () => {
    hooks.responses["/connections"] = {
      connections: [conn("google", { display_name: "Google Gemini", connected: true, status: "connected" })],
    };
    hooks.responses["health"] = { default_provider: "google", providers: [] };
    hooks.responses["/fleet/default-after-removal?leaving=google"] = {
      ...impact,
      replacement: { provider: "mock", model: "", label: "the demo model", demo: true },
      demo: true,
      words: "Google Gemini is what answers your chats right now. Nothing else is connected, so Jarvis will answer with the demo model (scripted replies, not real answers) until you connect a model in Settings › Connections.",
    };
    render(<ConnectionsPage />);
    await screen.findByText("Google Gemini");
    const line = await screen.findByTestId("conn-default-impact");
    expect(line.getAttribute("data-demo")).toBe("1");
    expect(line.textContent).toContain("demo model");
  });

  it("an older daemon (404 on the preview) draws nothing and Disconnect still works", async () => {
    hooks.responses["/connections"] = {
      connections: [conn("google", { display_name: "Google Gemini", connected: true, status: "connected" })],
    };
    hooks.responses["health"] = { default_provider: "google", providers: [] };
    hooks.failing["/fleet/default-after-removal?leaving=google"] = { status: 404, message: "Not Found" };
    render(<ConnectionsPage />);
    await screen.findByText("Google Gemini");
    await waitFor(() => expect(hooks.gets).toContain("/fleet/default-after-removal?leaving=google"));
    expect(screen.queryByTestId("conn-default-impact")).toBeNull();
    const c = connCard("google");
    fireEvent.click(within(c).getByRole("button", { name: /Disconnect/ }));
    fireEvent.click(within(c).getByRole("button", { name: /Confirm\?|Disconnect/ }));
    await waitFor(() => expect(hooks.dels).toContain("/connections/google"));
    expect(screen.queryByTestId("conn-default-moved")).toBeNull();
  });
});
