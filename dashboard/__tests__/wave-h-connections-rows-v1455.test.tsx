/**
 * v1.455.0 — readiness Wave H item 28: Settings › Connections catches up with
 * the first-run door. A not-installed Claude Code / Codex row offers the
 * door's own "Install … for me" (the vendor's installer in a Build terminal,
 * `POST /terminals/launch {cli: "codex-install"}`), and an Ollama that is
 * running with no model says so and offers the starter download (`{cli:
 * "ollama-starter"}`) — never "Not running on this PC".
 *
 * The REAL ConnectionsPage, its data seams mocked (the accounts-signin harness).
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
    puts: [] as { path: string; body: unknown }[],
    launchError: "" as string,
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
    if (path === "/terminals/launch") {
      if (hooks.launchError) return Promise.reject(new hooks.FakeApiError(hooks.launchError, 409));
      return Promise.resolve({ id: "pane-7" });
    }
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
    version: "1.353.0",
    default_provider: "mock",
    default_model: "",
    providers: rows,
  };
}

const CLAUDE_SIGNED_OUT: Row = {
  provider: "claude-cli",
  available: false,
  class: "cli",
  installed: true,
  signed_in: false,
  sign_in_fix: "Claude Code isn't signed in. Open a terminal, run `claude`, then `/login`, then click Test on Connections.",
};
const CODEX_MISSING: Row = {
  provider: "codex-cli",
  available: false,
  class: "cli",
  installed: false,
  signed_in: null,
  sign_in_fix: "",
};
const OLLAMA = (over: Row = {}): Row => ({ provider: "ollama", available: false, class: "local", ...over });

const card = () => document.getElementById("subscription-providers") as HTMLElement;

beforeEach(() => {
  hooks.responses = { "/connections": { connections: [] } };
  hooks.posts.length = 0;
  hooks.puts.length = 0;
  hooks.launchError = "";
  hooks.refresh = vi.fn();
});
afterEach(() => cleanup());

describe("Wave H item 28 — the Connections rows match the first-run door", () => {
  it("a Codex that is not on this PC is one press from installing, in view", async () => {
    health([CLAUDE_SIGNED_OUT, CODEX_MISSING, OLLAMA()]);
    render(<ConnectionsPage />);
    const row = await screen.findByTestId("cli-get-codex-cli");
    fireEvent.click(within(row).getByRole("button", { name: "Install Codex for me" }));
    await waitFor(() =>
      expect(hooks.posts).toContainEqual({ path: "/terminals/launch", body: { cli: "codex-install" } }),
    );
    const pane = await within(row).findByTestId("cli-install-pane-codex");
    expect(pane.textContent).toMatch(/installing Codex/);
    expect(within(pane).getByRole("link", { name: "Open the Build terminal" }).getAttribute("href")).toBe(
      "/terminals?focus=pane-7",
    );
    // The vendor's page is still there to read first.
    expect(within(row).queryByRole("link", { name: "How to get Codex" })).toBeNull(); // replaced by the pane line
  });

  it("I've installed it — check now looks again and says when it is still missing", async () => {
    health([CODEX_MISSING]);
    render(<ConnectionsPage />);
    const row = await screen.findByTestId("cli-get-codex-cli");
    fireEvent.click(within(row).getByRole("button", { name: "Install Codex for me" }));
    fireEvent.click(await within(row).findByTestId("cli-installed-check-codex"));
    await waitFor(() => expect(hooks.posts.map((p) => p.path)).toContain("/providers/rescan"));
    expect((await within(row).findByTestId("cli-still-missing-codex")).textContent).toMatch(
      /Codex still isn't found on this PC/,
    );
  });

  it("an Ollama running with no model offers the starter download, never 'Not running'", async () => {
    health([OLLAMA({ running: true, detected: false, installed: true })]);
    render(<ConnectionsPage />);
    const row = await screen.findByTestId("ollama-no-model-row");
    expect(row.textContent).toMatch(/Ollama is running but has no model yet/);
    expect(screen.queryByTestId("get-ollama-row")).toBeNull();
    fireEvent.click(within(row).getByTestId("ollama-starter-download"));
    await waitFor(() =>
      expect(hooks.posts).toContainEqual({ path: "/terminals/launch", body: { cli: "ollama-starter" } }),
    );
    expect(await within(row).findByTestId("ollama-starter-pane")).toBeTruthy();
  });

  it("CONTROL: an Ollama that is not running still says where to get it", async () => {
    health([OLLAMA({ running: false, detected: false })]);
    render(<ConnectionsPage />);
    expect((await screen.findByTestId("get-ollama-row")).textContent).toMatch(/Not running on this PC/);
    expect(screen.queryByTestId("ollama-no-model-row")).toBeNull();
  });
});
