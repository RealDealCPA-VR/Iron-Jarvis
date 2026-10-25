/**
 * Readiness 12A (G33) — a signed-out Claude Code or Codex is met with the ONE
 * Sign in press, never a typed terminal recipe and never inside a green
 * "complete" note.
 *
 * Before: Settings › Connections' Rescan printed "Codex found but not usable
 * (Not logged in. Codex isn't signed in. Open a terminal, run `codex login`,
 * then click Test on Connections.)" inside its GREEN success note; the chat's
 * warning above the message box said "claude-cli is installed but not signed
 * in. … Run `claude` in a terminal, then /login, then Test on Connections.";
 * the New task door said "Your default model, claude-cli, is installed but not
 * signed in … Sign it in on Connections first." Now each names the program and
 * draws the same press (components/connections/CliSignInPress → POST
 * /terminals/launch {cli, account: "default"}).
 *
 * Fed the REAL daemon's answers (fixtures/sign-in-press-v1387.json, written by
 * tests/test_sign_in_press_v1387.py) through the REAL Connections page, the
 * real PreflightNote and the real New task door. Harness: accounts-signin-
 * v13530's, copied (a test file's mocks are its own), with POST
 * /providers/rescan answering the fixture's rows.
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
    rescan: [] as unknown[],
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
    if (path === "/providers/rescan") return Promise.resolve({ detected: hooks.rescan });
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

import { readFileSync } from "node:fs";
import { join } from "node:path";
import ConnectionsPage from "@/components/settings/pages/ConnectionsPage";
import { PreflightNote } from "@/components/chat/PreflightNote";
import { MissionComposer, preflightProblem } from "@/components/agents/mission/MissionComposer";
import { providerWords, sentenceStart } from "@/lib/onboarding";
import type { Health } from "@/lib/types";

const FIX = JSON.parse(
  readFileSync(join(__dirname, "fixtures", "sign-in-press-v1387.json"), "utf-8").replace(/\r\n/g, "\n"),
);

/** Every shape the typed recipe took ("Open a terminal, run `claude`, then
 *  `/login`", "run `codex login` in a terminal", "then Test on Connections"). */
const RECIPE = [/terminal/i, /`claude`/, /\/login/, /codex login/i, /Test on Connections/i];
function noRecipe(text: string) {
  for (const rx of RECIPE) expect(text, String(rx)).not.toMatch(rx);
}

const healthFrom = (default_provider: string): Health =>
  ({
    status: "ok",
    version: "1.387.0",
    default_provider,
    default_model: "",
    providers: FIX.health,
  }) as unknown as Health;

beforeEach(() => {
  hooks.responses = { "/connections": { connections: [] } };
  hooks.posts.length = 0;
  hooks.puts.length = 0;
  hooks.launchError = "";
  hooks.refresh = vi.fn();
  hooks.rescan = FIX.rescan;
});
afterEach(() => cleanup());

describe("G33: Rescan never puts a signed-out CLI in the green note, and offers the press", () => {
  it("the fixture is the daemon's real rescan: both CLIs signed out, the press's words", () => {
    expect(FIX.rescan.map((r: { provider: string }) => r.provider)).toEqual(["claude-cli", "codex-cli"]);
    for (const r of FIX.rescan) {
      expect(r.available).toBe(false);
      expect(r.signed_in).toBe(false);
      noRecipe(r.detail);
    }
  });

  it("the green note says only what is ready; each signed-out CLI has its own line and its Sign in press", async () => {
    hooks.responses.health = healthFrom("mock");
    render(<ConnectionsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Rescan local CLIs/ }));
    const claude = await screen.findByTestId("rescan-signed-out-claude-cli");
    const codex = screen.getByTestId("rescan-signed-out-codex-cli");
    // The green note: "Rescan complete." and nothing about the signed-out CLIs.
    const ok = screen
      .getAllByRole("status")
      .find((el) => (el.textContent ?? "").startsWith("Rescan complete"));
    expect(ok).toBeDefined();
    expect(ok!.textContent).toBe("Rescan complete.");
    expect(ok!.contains(claude)).toBe(false);
    expect(claude.textContent).toMatch(/^Claude Code is on this PC but not signed in\./);
    expect(codex.textContent).toMatch(/^Codex is on this PC but not signed in\./);
    noRecipe(claude.textContent ?? "");
    noRecipe(codex.textContent ?? "");
    // The press is the one Sign in (POST /terminals/launch on this PC's login).
    fireEvent.click(within(claude).getByRole("button", { name: "Sign in to Claude Code" }));
    await waitFor(() => expect(hooks.posts.some((p) => p.path === "/terminals/launch")).toBe(true));
    expect(hooks.posts.find((p) => p.path === "/terminals/launch")!.body).toEqual({
      cli: "claude",
      account: "default",
    });
    expect(await within(claude).findByRole("link", { name: "Finish signing in there" })).toHaveAttribute(
      "href",
      "/terminals?focus=pane-7",
    );
    expect(hooks.refresh).toHaveBeenCalled();
  });

  it("a ready CLI still reads in the green note (nothing else changed)", async () => {
    hooks.responses.health = healthFrom("mock");
    hooks.rescan = [{ ...FIX.rescan[0], available: true, detail: "", signed_in: true }];
    render(<ConnectionsPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Rescan local CLIs/ }));
    const ok = await waitFor(() => {
      const el = screen
        .getAllByRole("status")
        .find((n) => (n.textContent ?? "").startsWith("Rescan complete"));
      expect(el).toBeDefined();
      return el!;
    });
    expect(ok.textContent).toBe("Rescan complete: Claude Code ready to use (1 model).");
    expect(screen.queryByTestId("rescan-signed-out-claude-cli")).toBeNull();
  });
});

describe("G33: the chat's warning above the message box names the program and offers the press", () => {
  it("a signed-out Claude Code (the real /health row): its name, a press, no recipe", async () => {
    const row = FIX.health.find((r: { provider: string }) => r.provider === "claude-cli");
    render(<PreflightNote provider={row.provider} available={row.available} signedOut={row.signed_in === false} />);
    const note = screen.getByTestId("ij-preflight-note");
    expect(note.getAttribute("data-kind")).toBe("signed-out");
    expect(note.textContent).toMatch(/^Claude Code is installed but not signed in, so this turn will fail\./);
    expect(note.textContent).not.toMatch(/claude-cli/);
    noRecipe(note.textContent ?? "");
    fireEvent.click(within(note).getByRole("button", { name: "Sign in to Claude Code" }));
    await waitFor(() => expect(hooks.posts.some((p) => p.path === "/terminals/launch")).toBe(true));
    expect(hooks.posts.find((p) => p.path === "/terminals/launch")!.body).toEqual({ cli: "claude", account: "default" });
  });

  it("Codex reads Codex", () => {
    render(<PreflightNote provider="codex-cli" available={false} signedOut />);
    const note = screen.getByTestId("ij-preflight-note");
    expect(note.textContent).toMatch(/^Codex is installed but not signed in/);
    expect(within(note).getByRole("button", { name: "Sign in to Codex" })).toBeTruthy();
    noRecipe(note.textContent ?? "");
  });

  it("the user's own model server reads by the machine's name, else 'Your model server' (never fleet-custom)", () => {
    render(
      <PreflightNote
        provider="fleet-custom"
        available={false}
        catalog={[{ provider: "fleet-custom", model: "qwen3", name: "DGX Spark" }]}
      />,
    );
    expect(screen.getByTestId("ij-preflight-note").textContent).toBe(
      "DGX Spark isn't reachable right now. This turn will fail. Pick another model or bring the endpoint back.",
    );
    cleanup();
    render(<PreflightNote provider="fleet-custom" available={true} cooldownS={23} />);
    expect(screen.getByTestId("ij-preflight-note").textContent).toBe(
      "Your model server is in cooldown, retry in 23 s. It failed repeatedly, so a turn sent to it now is refused. Pick another model or wait.",
    );
    // No press where there is nothing to sign in to.
    expect(screen.getByTestId("ij-preflight-note").querySelector("button")).toBeNull();
  });

  it("providerWords / sentenceStart", () => {
    expect(providerWords("claude-cli")).toBe("Claude Code");
    expect(providerWords("fleet-abc")).toBe("your model server");
    expect(providerWords("custom", [{ provider: "fleet-custom", model: "x", name: "Spark B" }])).toBe("Spark B");
    expect(providerWords("")).toBe("");
    expect(sentenceStart("your model server")).toBe("Your model server");
    expect(sentenceStart("llama.cpp")).toBe("llama.cpp");
    expect(sentenceStart("Claude Code")).toBe("Claude Code");
  });
});

describe("G33: the New task door names the default and offers the press", () => {
  it("preflightProblem: the program's name and the press, from the real /health rows", () => {
    const p = preflightProblem(healthFrom("claude-cli"));
    expect(p).not.toBeNull();
    expect(p!.signIn).toBe(true);
    expect(p!.text).toBe(
      "Your default model, Claude Code, is installed but not signed in. A task started now will fail. Sign in first:",
    );
    noRecipe(p!.text);
    const q = preflightProblem({
      ...healthFrom("fleet-custom"),
      providers: [{ provider: "fleet-custom", available: false }],
    } as unknown as Health);
    expect(q!.text).toMatch(/^Your default model, your model server, isn't reachable right now\./);
    expect(q!.signIn).toBeUndefined();
  });

  it("the door draws the press under the words", async () => {
    hooks.responses.health = healthFrom("codex-cli");
    hooks.responses["/missions"] = { missions: [] };
    hooks.responses["/projects"] = { projects: [] };
    render(<MissionComposer onStarted={() => {}} onOpen={() => {}} />);
    const line = await screen.findByTestId("mission-preflight");
    expect(line.textContent).toMatch(/Your default model, Codex, is installed but not signed in/);
    noRecipe(line.textContent ?? "");
    fireEvent.click(within(line).getByRole("button", { name: "Sign in to Codex" }));
    await waitFor(() => expect(hooks.posts.some((p) => p.path === "/terminals/launch")).toBe(true));
    expect(hooks.posts.find((p) => p.path === "/terminals/launch")!.body).toEqual({ cli: "codex", account: "default" });
  });
});
