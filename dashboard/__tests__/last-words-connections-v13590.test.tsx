/**
 * v1.359.0 (wave 9, lane 9B) — Connections names the subscription rows by
 * the product, as every other surface does.
 *
 * The re-audit found Settings › Connections listing "Claude Code CLI" and
 * "Codex CLI" (and "OpenCode CLI") while the model menu, the chat's connect
 * door and the receipts all said "Claude Code", "Codex" and "OpenCode". The
 * rows now take their name from the one provider table
 * (lib/onboarding.providerDisplay); every id and testid stays.
 *
 * The REAL ConnectionsPage, its data seams mocked (the accounts-signin-v13530
 * harness).
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
    postResponses: {} as Record<string, unknown>,
    posts: [] as { path: string; body: unknown }[],
    refresh: vi.fn(),
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
    return Promise.resolve(hooks.postResponses[path] ?? { ok: true });
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
    refresh: hooks.refresh,
  }),
}));

vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: true }) }));
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
  const strip = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props))
      if (!["initial", "animate", "exit", "transition", "variants", "layout"].includes(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const motion = new Proxy({} as Record<string, unknown>, { get: (_t, tag) => strip(String(tag)) });
  return {
    m: motion,
    motion,
    AnimatePresence: ({ children }: { children?: React.ReactNode }) => createElement(Fragment, null, children),
  };
});
vi.mock("@/components/connections/RestHookups", () => ({ RestHookups: () => null }));
vi.mock("@/components/connections/IronProxyCard", () => ({ IronProxyCard: () => null }));
vi.mock("@/components/BrandGlyph", () => ({ ProviderMark: () => null, BrandGlyph: () => null }));

import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";

type Row = Record<string, unknown>;

function health(rows: Row[]) {
  hooks.responses["health"] = {
    status: "ok",
    version: "1.359.0",
    default_provider: "mock",
    default_model: "",
    providers: rows,
  };
}

const CLAUDE_READY: Row = { provider: "claude-cli", available: true, class: "cli", installed: true, signed_in: true };
const CODEX_SIGNED_OUT: Row = {
  provider: "codex-cli",
  available: false,
  class: "cli",
  installed: true,
  signed_in: false,
  sign_in_fix: "Codex isn't signed in yet. Run `codex login`.",
};
const OPENCODE: Row = { provider: "opencode-cli", available: false, class: "cli" };

const card = () => document.getElementById("subscription-providers") as HTMLElement;

/** The row whose bold name is exactly `name` (the name line of each row). */
function rowNamed(name: string): HTMLElement {
  const el = within(card()).getByText(name, { selector: "div" });
  return el.closest("div.flex") as HTMLElement;
}

beforeEach(() => {
  hooks.responses = { "/connections": { connections: [] } };
  hooks.postResponses = {};
  hooks.posts.length = 0;
  hooks.refresh = vi.fn();
});
afterEach(() => cleanup());

describe("the subscription rows say the product's name", () => {
  it("Claude Code and Codex, never 'Claude Code CLI' / 'Codex CLI'; OpenCode the same", async () => {
    health([CLAUDE_READY, CODEX_SIGNED_OUT, OPENCODE]);
    render(<ConnectionsPage />);
    await screen.findByTestId("cli-sign-in-codex-cli");
    expect(within(card()).getByText("Claude Code", { selector: "div" })).toBeInTheDocument();
    expect(within(card()).getByText("Codex", { selector: "div" })).toBeInTheDocument();
    expect(within(card()).getByText("OpenCode", { selector: "div" })).toBeInTheDocument();
    expect(card().textContent).not.toMatch(/Claude Code CLI|Codex CLI|OpenCode CLI/);
    // The ids and testids are unchanged: the signed-out row keeps its press.
    expect(within(rowNamed("Codex")).getByTestId("cli-sign-in-codex-cli")).toBeInTheDocument();
    expect(within(card()).getByRole("button", { name: "Sign in to Codex" })).toBeInTheDocument();
  });

  it("the ready banner and the rescan note use the same names", async () => {
    health([CLAUDE_READY, CODEX_SIGNED_OUT]);
    hooks.postResponses["/providers/rescan"] = {
      detected: [
        // The daemon's rescan row still names it "Claude Code CLI"; the page
        // never prints that field.
        { provider: "claude-cli", model: "subscription", name: "Claude Code CLI", available: true, source: "cli", base_url: null, exec_path: null, context_window: null, detail: "" },
        { provider: "codex-cli", model: "subscription", name: "Codex CLI", available: false, source: "cli", base_url: null, exec_path: null, context_window: null, detail: "not signed in" },
      ],
    };
    render(<ConnectionsPage />);
    const banner = await screen.findByTestId("connections-ready-banner");
    expect(banner.textContent).toContain("Claude Code (");
    expect(banner.textContent).not.toMatch(/\bCLI\b/);
    fireEvent.click(screen.getByRole("button", { name: /Rescan local CLIs/ }));
    await waitFor(() => expect(document.body.textContent).toContain("Rescan complete: Claude Code ready to use (1 model)"));
    // Readiness 12A (G33, moved on purpose): a signed-out CLI has its own
    // line and its Sign in press, never a place inside the green note.
    expect(screen.getByTestId("rescan-signed-out-codex-cli").textContent).toMatch(
      /^Codex is on this PC but not signed in\./,
    );
    expect(document.body.textContent).not.toContain("Codex found but not usable");
    expect(document.body.textContent).not.toMatch(/Claude Code CLI|Codex CLI/);
  });

  it("an empty rescan names the products too", async () => {
    health([]);
    hooks.postResponses["/providers/rescan"] = { detected: [] };
    render(<ConnectionsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Rescan local CLIs/ }));
    await waitFor(() => expect(document.body.textContent).toContain("Rescan complete. Nothing found on this PC yet."));
    expect(document.body.textContent).toContain("Install Claude Code, Codex or the Grok CLI and sign in");
  });
});
