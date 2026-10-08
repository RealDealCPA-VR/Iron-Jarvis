/**
 * v1.316.0 (UX & aesthetic wave 4, track T5 — connections & catalogs).
 *
 * Six verified findings, each pinned by what the user SEES:
 *
 *  1. connections-model-path-buried — the first screen of Connections was one
 *     alphabetical grid mixing AI models, memory drives and creative media,
 *     and the zero-setup answer (Claude Code / Codex already signed in) sat
 *     below the fold. Now: titled groups (AI models first) and a "ready now"
 *     line built from the SAME /health detection as the Subscription card,
 *     anchored to that card (which keeps its place).
 *  2. oauth-cards-hidden-dev-setup — Dropbox / Drive / OneDrive promised
 *     "Log in with your account" and only after a 400 said the user had to
 *     register an app. Now keyed on the daemon's `oauth_client_configured`:
 *     a folded "One-time setup" BEFORE a "Set up & log in" button that stays
 *     clickable; the 400 fallback note stays.
 *  3. three-overlapping-catalogs — ONE name, "Directory" (never Marketplace —
 *     the 2026-07-25 decision), and cross-links only where two surfaces write
 *     the SAME backend row (same mcp_servers name / same connection id).
 *  4. marketplace-hierarchy — Back to chat is not the page's filled accent;
 *     the four stat tiles become one summary line (all four numbers kept,
 *     tools live = 0 included) with a Connected filter.
 *  5. tools-false-runtime-warning — the catalog's `runtime_ready` (absent →
 *     the old amber "Needs Node"; true → a neutral "Uses Node"; "Needs X"
 *     stays in the title).
 *  6. accent-overuse-no-primary — one filled accent per screen on Connections
 *     and Directory; per-card Connect / Log in are outlined, full width on a
 *     phone and auto width from sm.
 *
 * Anti-vacuity: every card, every connect path, the two-press Disconnect, the
 * Secrets fallback, the Subscription section, the Tools Add button and the
 * launch command all stay present and working.
 *
 * Harness mirrors inherited-provider-v1230 (connections) and
 * mcp-last-error-v1229 (tools): the pages' data seams are mocked.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
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
    /** Paths whose GET rejects with this status (e.g. an OAuth start 400). */
    failing: {} as Record<string, number>,
    gets: [] as string[],
    posts: [] as { path: string; body: unknown }[],
    dels: [] as string[],
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
  get: vi.fn((path: string) => {
    hooks.gets.push(path);
    const status = hooks.failing[path];
    if (status) return Promise.reject(new hooks.FakeApiError("no OAuth client configured", status));
    return Promise.resolve(hooks.responses[path] ?? {});
  }),
  post: vi.fn((path: string, body?: unknown) => {
    hooks.posts.push({ path, body });
    return Promise.resolve({ ok: true, tools_loaded: 0, note: null });
  }),
  put: vi.fn(() => Promise.resolve({})),
  patch: vi.fn(() => Promise.resolve({})),
  del: vi.fn((path: string) => {
    hooks.dels.push(path);
    return Promise.resolve({});
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
// Sections of /connections that are not this track's subject: scoped out so
// the accent count measures the connection grid and the page header.
vi.mock("@/components/connections/RestHookups", () => ({ RestHookups: () => null }));
vi.mock("@/components/connections/IronProxyCard", () => ({ IronProxyCard: () => null }));
vi.mock("@/components/BrandGlyph", () => ({
  ProviderMark: () => null,
  BrandGlyph: () => null,
}));

import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";
import MarketplacePage from "@/components/settings/pages/DirectoryPage";
import ToolsPage from "@/components/settings/pages/ToolsPage";
import { StatusChip } from "@/components/tools/chips";

const DASH = join(__dirname, "..");
const src = (p: string) => readFileSync(join(DASH, p), "utf-8").replace(/\r\n/g, "\n");

beforeEach(() => {
  hooks.responses = {};
  hooks.failing = {};
  hooks.gets.length = 0;
  hooks.posts.length = 0;
  hooks.dels.length = 0;
  try {
    localStorage.clear();
  } catch {
    /* jsdom without storage */
  }
});
afterEach(() => cleanup());

/** `a` comes before `b` in document order. */
const before = (a: Node, b: Node) =>
  Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

/* ========================================================================== */
/*  CONNECTIONS                                                               */
/* ========================================================================== */

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

const DROPBOX_HELP =
  "Connect Dropbox to search + ingest its files into memory. Create an app at dropbox.com/developers/apps with a http://localhost redirect and paste its app key/secret.";

const drive = (provider: string, name: string, over: Record<string, unknown> = {}) =>
  conn(provider, {
    display_name: name,
    method: "oauth",
    supports_oauth: true,
    supports_api_key: false,
    oauth_help: `${name}: register your own OAuth app and paste its client id/secret.`,
    oauth_client_configured: false,
    ...over,
  });

/** The daemon's real provider set, in its real (alphabetical) order. */
function serverOrder() {
  return [
    conn("anthropic", {
      display_name: "Anthropic",
      connected: true,
      status: "connected",
      source: "inherited from claude-cli",
    }),
    conn("custom", { display_name: "Custom endpoint" }),
    drive("dropbox", "Dropbox (memory)", { oauth_help: DROPBOX_HELP }),
    drive("google", "Google (Gemini)", { oauth_help: "" }),
    drive("google_drive", "Google Drive (memory)"),
    drive("onedrive", "OneDrive (memory)"),
    conn("openai", { display_name: "OpenAI" }),
    conn("openrouter", { display_name: "OpenRouter" }),
    conn("pixio", { display_name: "Pixio (creative media)" }),
    conn("xai", {
      display_name: "xAI (Grok)",
      oauth_help:
        "xAI uses an API key today; account login activates once xAI ships a public OAuth client.",
    }),
  ];
}

function seedConnections(
  connections: unknown[],
  providers: Record<string, unknown>[] = [
    { provider: "anthropic", available: true, inherited_from: "claude-cli" },
    { provider: "claude-cli", available: true, installed: true, signed_in: true },
    { provider: "codex-cli", available: true, installed: true, signed_in: true },
    { provider: "grok-cli", available: false, installed: false },
  ],
) {
  hooks.responses["/connections"] = { connections };
  hooks.responses["/routing/quality"] = { bar: 0.75, min_samples: 3, rows: [] };
  hooks.responses["/fleet"] = { nodes: [] };
  hooks.responses["health"] = { default_provider: "mock", providers };
}

const cardOf = (provider: string) =>
  document.getElementById(`conn-card-${provider}`) as HTMLElement;

async function renderConnections() {
  render(<ConnectionsPage />);
  await screen.findByText("Anthropic");
}

describe("Connections — the model path comes first (connections-model-path-buried)", () => {
  it("groups the cards under titled headings with AI models first, drives next, creative media after", async () => {
    seedConnections(serverOrder());
    await renderConnections();

    const ai = screen.getByRole("heading", { name: /AI models/i });
    const drives = screen.getByRole("heading", { name: /drive/i });
    const creative = screen.getByRole("heading", { name: /creative/i });
    expect(before(ai, drives)).toBe(true);

    for (const p of ["anthropic", "openai", "google", "xai", "openrouter", "custom"]) {
      expect(before(ai, cardOf(p)), `${p} after the AI heading`).toBe(true);
      expect(before(cardOf(p), drives), `${p} before the drives heading`).toBe(true);
    }
    for (const p of ["dropbox", "google_drive", "onedrive"]) {
      expect(before(drives, cardOf(p)), `${p} under the drives heading`).toBe(true);
    }
    expect(before(creative, cardOf("pixio"))).toBe(true);
    // The model the user already has is the first card they meet.
    expect(before(cardOf("anthropic"), cardOf("dropbox"))).toBe(true);
  });

  it("drops no card: every provider keeps its conn-card id, an unknown one included", async () => {
    // Deep links and the Fleet page target `conn-card-${provider}`.
    seedConnections([...serverOrder(), conn("acme_llm", { display_name: "Acme LLM" })]);
    await renderConnections();
    for (const p of [
      "anthropic", "custom", "dropbox", "google", "google_drive", "onedrive",
      "openai", "openrouter", "pixio", "xai", "acme_llm",
    ]) {
      expect(cardOf(p), p).not.toBeNull();
    }
  });

  it("says what already works with no key — from the SAME /health detection — and anchors to the Subscription card", async () => {
    seedConnections(serverOrder());
    await renderConnections();

    const banner = screen.getByTestId("connections-ready-banner");
    expect(banner.textContent).toMatch(/Claude Code/);
    expect(banner.textContent).toMatch(/Codex/);
    // Grok is not available: never named as ready.
    expect(banner.textContent).not.toMatch(/Grok/);
    // It leads: above the first card.
    expect(before(banner, cardOf("anthropic"))).toBe(true);

    // An anchor to the Subscription card, which keeps its place below.
    const link = banner.querySelector('a[href="#subscription-providers"]');
    expect(link).not.toBeNull();
    const target = document.getElementById("subscription-providers");
    expect(target).not.toBeNull();
    expect(target!.textContent).toMatch(/Subscription & local providers/);
    expect(before(cardOf("xai"), target!)).toBe(true);
  });

  it("claims nothing when nothing is detected — a signed-out CLI is not 'ready'", async () => {
    seedConnections(serverOrder(), [
      { provider: "claude-cli", available: false, installed: true, signed_in: false },
      { provider: "codex-cli", available: false, installed: false },
    ]);
    await renderConnections();
    expect(screen.queryByTestId("connections-ready-banner")).toBeNull();
    expect(document.body.textContent).not.toMatch(/Ready now/i);
    // The Subscription section and its Rescan are still there.
    expect(screen.getByText("Subscription & local providers")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Rescan local CLIs/ })).toBeInTheDocument();
  });
});

describe("Connections — a drive that needs an app says so BEFORE the click (oauth-cards-hidden-dev-setup)", () => {
  it("folds the one-time setup above a still-clickable 'Set up & log in'", async () => {
    seedConnections(serverOrder());
    await renderConnections();
    const card = cardOf("dropbox");

    const details = card.querySelector("details") as HTMLDetailsElement | null;
    expect(details, "a <details> on the Dropbox card").not.toBeNull();
    const summary = details!.querySelector("summary");
    expect(summary?.textContent).toMatch(/One-time setup/);
    expect(details!.open).toBe(false); // folded by default
    // The developer prose and the Secrets step live in it.
    expect(details!.textContent).toContain(DROPBOX_HELP);
    expect(details!.textContent).toContain("dropbox_oauth_client_id");
    expect(details!.querySelector('a[href="/secrets"]')).not.toBeNull();

    const button = within(card).getByRole("button", { name: /Set up & log in/ });
    expect(details!.contains(button)).toBe(false); // never gated behind opening it
    expect(before(details!, button)).toBe(true);
    expect(within(card).queryByRole("button", { name: /Log in with your account/ })).toBeNull();
  });

  it("the press still starts the real OAuth flow, and the 400 fallback note is still there", async () => {
    seedConnections(serverOrder());
    hooks.failing["/oauth/dropbox/start"] = 400;
    await renderConnections();
    const card = cardOf("dropbox");
    fireEvent.click(within(card).getByRole("button", { name: /Set up & log in/ }));
    await waitFor(() => expect(within(card).getByText(/No OAuth client configured/)).toBeInTheDocument());
    expect(hooks.gets).toContain("/oauth/dropbox/start");
  });

  it("a configured client — or an older daemon that does not say — keeps 'Log in with your account' and no setup fold", async () => {
    seedConnections([
      conn("anthropic", { display_name: "Anthropic" }),
      drive("dropbox", "Dropbox (memory)", { oauth_help: DROPBOX_HELP, oauth_client_configured: true }),
      (() => {
        const row = drive("onedrive", "OneDrive (memory)") as Record<string, unknown>;
        delete row.oauth_client_configured; // pre-v1.316.0 daemon
        return row;
      })(),
    ]);
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    hooks.responses["/oauth/dropbox/start"] = { authorization_url: "https://example.test/auth", state: "s" };
    await renderConnections();
    for (const p of ["dropbox", "onedrive"]) {
      const card = cardOf(p);
      expect(within(card).getByRole("button", { name: /Log in with your account/ })).toBeInTheDocument();
      expect(card.textContent).not.toMatch(/One-time setup/);
      expect(within(card).queryByRole("button", { name: /Set up & log in/ })).toBeNull();
    }
    fireEvent.click(within(cardOf("dropbox")).getByRole("button", { name: /Log in with your account/ }));
    await waitFor(() => expect(open).toHaveBeenCalledWith("https://example.test/auth", expect.anything(), expect.anything()));
    open.mockRestore();
  });

  it("xAI has oauth_help but is API-key only: no setup fold, its Connect key path unchanged", async () => {
    seedConnections(serverOrder());
    await renderConnections();
    const card = cardOf("xai");
    expect(card.textContent).not.toMatch(/One-time setup/);
    expect(within(card).queryByRole("button", { name: /Set up & log in/ })).toBeNull();
    fireEvent.click(within(card).getByRole("button", { name: /^Connect$/ }));
    expect(card.querySelector('input[type="password"]')).not.toBeNull();
  });

  it("a drive card names its Directory twin (same connection id), in Directory words", async () => {
    seedConnections(serverOrder());
    await renderConnections();
    for (const p of ["dropbox", "google_drive", "onedrive"]) {
      const link = cardOf(p).querySelector('a[href="/marketplace"]');
      expect(link, p).not.toBeNull();
      expect(link!.textContent).toMatch(/Directory/);
    }
    expect(document.body.textContent).not.toMatch(/Marketplace/);
  });
});

describe("Connections — one filled accent on the screen (accent-overuse-no-primary)", () => {
  it("only the page's Add connection is btn-accent; per-card Connect / Log in are outlined", async () => {
    seedConnections(serverOrder());
    await renderConnections();

    const accents = Array.from(document.querySelectorAll(".btn-accent"));
    expect(accents.map((a) => a.textContent?.trim())).toEqual([
      expect.stringMatching(/Add connection/),
    ]);

    // Every card keeps its connect action — outlined, not filled.
    const actions: [string, RegExp][] = [
      ["openrouter", /^Connect$/],
      ["xai", /^Connect$/],
      ["pixio", /^Connect$/],
      ["custom", /Add an endpoint/],
      ["dropbox", /Set up & log in/],
      ["google_drive", /Set up & log in/],
    ];
    for (const [p, name] of actions) {
      const b = within(cardOf(p)).getByRole("button", { name });
      expect(b.className, p).toMatch(/\bbtn-(soft|ghost)\b/);
      expect(b.className, p).not.toMatch(/\bbtn-accent\b/);
      // Full width for a thumb on a phone, auto width from sm.
      expect(b.className, p).toMatch(/(^|\s)w-full(\s|$)/);
      expect(b.className, p).toMatch(/(^|\s)sm:w-auto(\s|$)/);
    }
  });

  it("the Add connection menu still lists every unconnected provider and scrolls to its card", async () => {
    seedConnections(serverOrder());
    await renderConnections();
    fireEvent.click(screen.getByRole("button", { name: /Add connection/ }));
    const menu = screen.getByRole("menu");
    for (const name of ["Dropbox (memory)", "OpenRouter", "Pixio (creative media)", "xAI (Grok)"]) {
      expect(within(menu).getByRole("menuitem", { name: new RegExp(name.replace(/[()]/g, "\\$&")) })).toBeInTheDocument();
    }
  });
});

/* ========================================================================== */
/*  DIRECTORY (/marketplace)                                                  */
/* ========================================================================== */

function connector(id: string, name: string, over: Record<string, unknown> = {}) {
  return {
    id,
    name,
    category: "Developer",
    glyph: "",
    blurb: `${name} blurb.`,
    unlocks: `${name} unlocks.`,
    connect_via: "mcp",
    scopes: [],
    docs_url: "",
    fields: [],
    provider: "",
    connected: false,
    status: "disconnected",
    tools_loaded: 0,
    ...over,
  };
}

function seedDirectory() {
  hooks.responses["/connectors"] = {
    categories: ["Developer", "Productivity", "Storage"],
    connectors: [
      connector("github", "GitHub", {
        fields: [
          {
            name: "GITHUB_PERSONAL_ACCESS_TOKEN",
            label: "GitHub personal access token",
            help: "Create a token.",
            kind: "secret",
            optional: false,
          },
        ],
      }),
      connector("puppeteer", "Browser (Puppeteer)"),
      connector("notion", "Notion", {
        category: "Productivity",
        connected: true,
        status: "connected",
        tools_loaded: 0,
      }),
      connector("google_drive", "Google Drive", {
        category: "Storage",
        connect_via: "oauth",
        provider: "google_drive",
      }),
    ],
  };
}

const dirCard = (name: string) =>
  screen.getByRole("heading", { level: 3, name }).closest(".card-surface") as HTMLElement;

describe("Directory — one name, one primary, a summary line (marketplace-hierarchy)", () => {
  it("keeps the title Directory and its door back to chat — but that door is not the filled accent", () => {
    seedDirectory();
    render(<MarketplacePage />);
    expect(screen.getByRole("heading", { level: 1, name: "Directory" })).toBeInTheDocument();
    const back = screen.getByRole("link", { name: /Back to chat/ });
    expect(back.getAttribute("href")).toBe("/chat");
    expect(back.className).not.toMatch(/\bbtn-accent\b/);
  });

  it("states all four numbers in one summary line — tools live 0 included", () => {
    seedDirectory();
    render(<MarketplacePage />);
    const line = screen.getByTestId("directory-summary");
    const t = (line.textContent ?? "").replace(/\s+/g, " ");
    expect(t).toMatch(/\b1 connected\b/i);
    expect(t).toMatch(/\b4 available\b/i);
    expect(t).toMatch(/\b0 tools? live\b/i);
    expect(t).toMatch(/\b3 categor(y|ies)\b/i);
  });

  it("a Connected filter narrows the cards to the connected ones and toggles back", () => {
    seedDirectory();
    render(<MarketplacePage />);
    const filter = screen
      .getAllByRole("button", { name: /connected/i })
      .find((b) => b.hasAttribute("aria-pressed"));
    expect(filter, "a toggle button for Connected").toBeTruthy();
    fireEvent.click(filter!);
    expect(filter!.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("heading", { level: 3, name: "Notion" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 3, name: "GitHub" })).toBeNull();
    expect(screen.queryByRole("heading", { level: 3, name: "Google Drive" })).toBeNull();
    fireEvent.click(filter!);
    expect(screen.getByRole("heading", { level: 3, name: "GitHub" })).toBeInTheDocument();
    // The category chips and search still work beside it.
    fireEvent.click(within(screen.getByRole("group", { name: /Filter by category/ })).getByRole("button", { name: "Storage" }));
    expect(screen.getByRole("heading", { level: 3, name: "Google Drive" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 3, name: "GitHub" })).toBeNull();
    expect(screen.getByRole("searchbox", { name: /Search connectors/ })).toBeInTheDocument();
  });

  it("no card's Connect is the filled accent; the page has at most one at rest", () => {
    seedDirectory();
    render(<MarketplacePage />);
    for (const name of ["GitHub", "Browser (Puppeteer)", "Google Drive"]) {
      const b = within(dirCard(name)).getByRole("button", { name: /Connect/ });
      expect(b.className, name).not.toMatch(/\bbtn-accent\b/);
    }
    expect(document.querySelectorAll(".btn-accent").length).toBeLessThanOrEqual(1);
  });

  it("the connect flow is unchanged: the form opens, collects the token and posts it", async () => {
    seedDirectory();
    render(<MarketplacePage />);
    fireEvent.click(within(dirCard("GitHub")).getByRole("button", { name: /Connect/ }));
    const input = screen.getByLabelText("GitHub personal access token");
    fireEvent.change(input, { target: { value: "ghp_x" } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    await waitFor(() =>
      expect(hooks.posts).toContainEqual({
        path: "/connectors/github/connect",
        body: { values: { GITHUB_PERSONAL_ACCESS_TOKEN: "ghp_x" } },
      }),
    );
  });

  it("Disconnect is still two presses", async () => {
    seedDirectory();
    render(<MarketplacePage />);
    const card = dirCard("Notion");
    await act(async () => {
      fireEvent.click(within(card).getByRole("button", { name: /Disconnect/ }));
    });
    expect(hooks.dels).toEqual([]);
  });
});

describe("Directory ↔ Tools cross-links name ONE place, only where the backend row is shared (three-overlapping-catalogs)", () => {
  it("a Directory card whose id Tools also adds links to Tools; one Tools does not have does not", () => {
    seedDirectory();
    render(<MarketplacePage />);
    expect(dirCard("GitHub").querySelector('a[href="/tools"]')).not.toBeNull();
    // Directory's Puppeteer is not Tools' Playwright — no false twin.
    expect(dirCard("Browser (Puppeteer)").querySelector('a[href="/tools"]')).toBeNull();
    expect(document.body.textContent).not.toMatch(/Marketplace/);
  });

  it("chat's links say Directory, never Marketplace — and still go to the Directory", () => {
    const s = src("app/chat/page.tsx");
    expect(s).not.toMatch(/^\s*Marketplace ↗\s*$/m);
    expect(s).not.toMatch(/^\s*From the marketplace\s*$/im);
    expect(s).toMatch(/^\s*From the Directory\s*$/m);
    expect(s).toMatch(/^\s*[^<>{}=\n]*Directory[^<>{}=\n]*↗\s*$/m);
    // Anti-vacuity: the teasers and the footer link still lead there.
    expect((s.match(/href="\/marketplace"/g) ?? []).length).toBeGreaterThanOrEqual(2);
  });
});

/* ========================================================================== */
/*  TOOLS                                                                     */
/* ========================================================================== */

describe("StatusChip — a runtime this PC has is not a warning (tools-false-runtime-warning)", () => {
  it("'ready' reads 'Uses Node', neutral, no alert icon, the requirement kept in the title", () => {
    const { container } = render(
      <StatusChip status={"ready" as Parameters<typeof StatusChip>[0]["status"]} needs="Node" />,
    );
    const chip = screen.getByTestId("status-ready");
    expect(chip.textContent).toMatch(/Uses Node/);
    expect(chip.textContent).not.toMatch(/Needs/);
    expect(chip.getAttribute("title") ?? "").toMatch(/Needs Node/);
    expect(container.querySelector('[class*="amber"]')).toBeNull();
    expect(container.querySelector('svg[class*="triangle"], svg[class*="alert"]')).toBeNull();
  });

  it("control: 'blocked' still carries the amber alert — so the check above is not vacuous", () => {
    const { container } = render(<StatusChip status="blocked" needs="Node" />);
    expect(screen.getByTestId("status-blocked").textContent).toContain("Needs Node");
    expect(container.querySelector('[class*="amber"]')).not.toBeNull();
    expect(container.querySelector('svg[class*="triangle"], svg[class*="alert"]')).not.toBeNull();
  });
});

const pack = (id: string, name: string, command: string, needs: string, over: Record<string, unknown> = {}) => ({
  id,
  name,
  description: `${name} description.`,
  command,
  args: ["-y", `pkg-${id}`],
  category: "reference",
  needs,
  ...over,
});

function seedTools(servers: unknown[] = []) {
  hooks.responses["/tools/custom"] = { tools: [] };
  hooks.responses["/mcp/servers"] = {
    servers,
    auto_approve: false,
    auto_approve_effective: false,
  };
  hooks.responses["/mcp/catalog"] = {
    catalog: [
      pack("filesystem", "Files & folders", "npx", "Node", { runtime_ready: true }),
      pack("fetch", "Fetch web pages", "uvx", "Python (uv)", { runtime_ready: false }),
      pack("memory", "Long-term memory", "npx", "Node"), // older daemon: no field
      pack("github", "GitHub", "npx", "Node", { runtime_ready: true, category: "integration" }),
      pack("playwright", "Browser control (Playwright)", "npx", "Node", {
        runtime_ready: true,
        category: "integration",
      }),
    ],
  };
}

const packCard = (id: string) => document.querySelector(`[data-pack="${id}"]`) as HTMLElement;

describe("Tools catalog — the chip follows runtime_ready", () => {
  it("true → neutral 'Uses Node'; false → amber 'Needs Python (uv)'; absent → amber 'Needs Node'", async () => {
    seedTools();
    render(<ToolsPage />);
    await waitFor(() => expect(packCard("filesystem")).not.toBeNull());

    const fs = packCard("filesystem");
    expect(within(fs).getByTestId("status-ready").textContent).toMatch(/Uses Node/);
    expect(within(fs).queryByTestId("status-blocked")).toBeNull();

    expect(within(packCard("fetch")).getByTestId("status-blocked").textContent).toContain(
      "Needs Python (uv)",
    );
    // No field (older daemon) keeps the v1.216.0 behaviour exactly.
    expect(within(packCard("memory")).getByTestId("status-blocked").textContent).toContain(
      "Needs Node",
    );
  });

  it("an added pack still reads Enabled, and every card keeps Add and its launch command", async () => {
    seedTools([
      { name: "github", command: "npx", args: [], env: {}, tools_loaded: 2, tool_names: [], last_error: null },
    ]);
    render(<ToolsPage />);
    await waitFor(() => expect(packCard("github")).not.toBeNull());
    expect(within(packCard("github")).getByTestId("status-added")).toBeInTheDocument();
    for (const id of ["filesystem", "fetch", "memory", "playwright"]) {
      expect(screen.getByTestId(`pack-add-${id}`)).toBeInTheDocument();
      expect(packCard(id).textContent).toMatch(/Show command/);
    }
  });

  it("a Tools pack with a Directory twin (same mcp_servers name) links there in Directory words; Playwright does not", async () => {
    seedTools();
    render(<ToolsPage />);
    await waitFor(() => expect(packCard("github")).not.toBeNull());
    for (const id of ["github", "filesystem", "fetch", "memory"]) {
      const link = packCard(id).querySelector('a[href="/marketplace"]');
      expect(link, id).not.toBeNull();
      expect(link!.textContent).toMatch(/Directory/);
    }
    expect(packCard("playwright").querySelector('a[href="/marketplace"]')).toBeNull();
    expect(document.body.textContent).not.toMatch(/Marketplace/);
  });
});
