/**
 * v1.353.0 (wave 3, lane 3A) — Settings › Connections › Accounts & keys, the
 * subscription & local rows.
 *
 *  G33 "Sign in and install from any page that mentions Claude or Codex":
 *    the Accounts row told a signed-out user to "run `claude` in a terminal,
 *    then /login, then Re-detect" (and a missing one to "install / log in via
 *    its CLI") while the chat's connect door already had a Sign in button.
 *    Now the row carries the SAME press (lib/cliSignIn → POST
 *    /terminals/launch {cli, account: "default"}): a Build pane opens with the
 *    CLI running and the row says what to do there; a CLI that is not on this
 *    PC links its vendor's page.
 *  G34 "Ollama: one answer, one press": the Local Ollama row said "appears
 *    automatically" — untrue, its address had to be saved first. A running
 *    Ollama (/health `detected`) now gets "Use Ollama on this PC" (the
 *    connect doors' own PUT), and a missing one links the download.
 *
 * The REAL ConnectionsPage, its data seams mocked (the ux-wave4-connections
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

describe("G33 — a signed-out Claude Code is one press from signing in", () => {
  it("Sign in opens the sign-in pane (POST /terminals/launch on this PC's own login) and says what to do there", async () => {
    health([CLAUDE_SIGNED_OUT, CODEX_MISSING, OLLAMA()]);
    render(<ConnectionsPage />);
    const row = await screen.findByTestId("cli-sign-in-claude-cli");
    // No row tells anyone to open a terminal — before or after the press.
    expect(card().textContent).not.toMatch(/terminal/i);
    expect(card().textContent).not.toMatch(/via its CLI|appears automatically/i);

    fireEvent.click(within(row).getByRole("button", { name: "Sign in to Claude Code" }));
    await waitFor(() => expect(hooks.posts.some((p) => p.path === "/terminals/launch")).toBe(true));
    expect(hooks.posts.find((p) => p.path === "/terminals/launch")!.body).toEqual({
      cli: "claude",
      account: "default",
    });
    const hint = await screen.findByTestId("cli-pane-hint-claude-cli");
    expect(hint.textContent).toMatch(/A Build terminal opened with Claude Code running\. Type \/login there/);
    expect(hint.textContent).toMatch(/turns green by itself/);
    expect(within(hint).getByRole("link", { name: "Open the Build terminal" }).getAttribute("href")).toBe(
      "/terminals?focus=pane-7",
    );
    expect(hooks.refresh).toHaveBeenCalled(); // the row follows /health at once
    expect(card().textContent).not.toMatch(/open a terminal|in a terminal/i);
  });

  it("a refused launch says the daemon's sentence where the button was", async () => {
    health([CLAUDE_SIGNED_OUT]);
    hooks.launchError = "That account can't be used right now.";
    render(<ConnectionsPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Sign in to Claude Code" }));
    expect((await screen.findByRole("alert")).textContent).toBe("That account can't be used right now.");
  });

  it("a CLI that is not on this PC links its vendor's page, never a press that would fail", async () => {
    health([CLAUDE_SIGNED_OUT, CODEX_MISSING]);
    render(<ConnectionsPage />);
    const get = await screen.findByTestId("cli-get-codex-cli");
    const link = within(get).getByRole("link", { name: "How to get Codex" });
    expect(link.getAttribute("href")).toMatch(/^https:\/\/github\.com\/openai\/codex/);
    expect(screen.queryByRole("button", { name: "Sign in to Codex" })).toBeNull();
  });

  it("CONTROL: a signed-in CLI is green with no press and no hint", async () => {
    health([{ ...CLAUDE_SIGNED_OUT, available: true, signed_in: true }]);
    render(<ConnectionsPage />);
    // v1.359.0 (lane 9B, changed on purpose): the row is named by the
    // product, "Claude Code", as every other surface says it.
    await screen.findByText("Claude Code", { selector: "div" });
    expect(screen.queryByTestId("cli-sign-in-claude-cli")).toBeNull();
    expect(screen.queryByRole("button", { name: /^Sign in/ })).toBeNull();
  });
});

describe("G34 — the Local Ollama row tells the truth and offers the one press", () => {
  it("Ollama running on this PC, not connected: 'Use Ollama on this PC' saves this PC's address", async () => {
    health([OLLAMA({ detected: true })]);
    render(<ConnectionsPage />);
    const row = await screen.findByTestId("use-ollama-row");
    expect(row.textContent).toMatch(/Running on this PC, not connected yet/);
    fireEvent.click(within(row).getByRole("button", { name: "Use Ollama on this PC" }));
    await waitFor(() =>
      expect(hooks.puts).toEqual([
        { path: "/settings", body: { values: { ollama_base_url: "http://localhost:11434" } } },
      ]),
    );
    await waitFor(() => expect(hooks.refresh).toHaveBeenCalled());
  });

  it("Ollama not running: where to get it — never 'appears automatically'", async () => {
    health([OLLAMA({ detected: false })]);
    render(<ConnectionsPage />);
    const row = await screen.findByTestId("get-ollama-row");
    expect(within(row).getByRole("link", { name: "Get Ollama" }).getAttribute("href")).toBe(
      "https://ollama.com/download",
    );
    expect(screen.queryByRole("button", { name: "Use Ollama on this PC" })).toBeNull();
    expect(card().textContent).not.toMatch(/appears automatically/);
  });

  it("v1.353.0 integrate: a saved Ollama address that does not answer says so — never 'Get Ollama'", async () => {
    health([OLLAMA({ detected: false, address_saved: true })]);
    render(<ConnectionsPage />);
    const row = await screen.findByTestId("ollama-not-answering");
    expect(row.textContent).toMatch(/Not answering at the address you saved/);
    expect(row.textContent).toMatch(/Rescan local CLIs/);
    // The press it names is on this card.
    expect(within(card()).getByRole("button", { name: /Rescan local CLIs/ })).toBeTruthy();
    expect(screen.queryByTestId("get-ollama-row")).toBeNull();
    expect(screen.queryByRole("button", { name: "Use Ollama on this PC" })).toBeNull();
  });

  it("CONTROL: a connected Ollama is green with no press", async () => {
    health([OLLAMA({ available: true, detected: true })]);
    render(<ConnectionsPage />);
    await screen.findByText("Local Ollama");
    expect(screen.queryByTestId("use-ollama-row")).toBeNull();
    expect(screen.queryByTestId("get-ollama-row")).toBeNull();
  });
});
