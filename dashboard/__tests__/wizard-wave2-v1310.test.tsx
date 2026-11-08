import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

/**
 * Wave 2 (v1.310.0) — THE FIRST FIVE MINUTES, track B (wizard + connect doors).
 *
 * v1.439.0 (Wave 6, D6): the first-run wizard was removed (unmounted since the
 * calm redesign, v1.321.0). Its own pins (the step-3 gate, the first task's
 * provider pin, the celebration) left with it. The doors are the shared
 * `ConnectDoors` the chat's empty state still shows, so the subscription
 * door's three states, the explicit 'Use it for answers' press and the
 * Ollama door below now render ConnectDoors directly.
 *
 * WHAT THESE TESTS GUARD (findings wizard-first-task-claims-real-on-mock,
 * subscription-door-dead-end, mock-default-trap-cli-ollama; the verifier's
 * fix_adjustment wins over the proposal):
 *  - step 3 NEVER runs on the offline demo: with no ready provider it shows
 *    the connect doors instead of the task box, however the user got there
 *    (Continue, or a jump on the stepper);
 *  - the first task carries the provider the user just enabled, by name —
 *    the INHERITED API name when /health lists both `anthropic` and
 *    `claude-cli` (so the quality dial applies) — and never overrides a real
 *    default the user chose themselves;
 *  - the celebration reads the provider that ACTUALLY answered (the session
 *    row's `provider`): mock = an amber "That was the offline demo — no model
 *    ran", never "It works — output is real"; a real run names the provider
 *    and lists the files it wrote, or says it wrote none;
 *  - the subscription door tells the truth in three states read off the
 *    /health claude-cli / codex-cli rows (W2-3): not installed (a web or
 *    desktop subscription cannot be used here + a plain vendor link, no
 *    `irm | iex`), installed-but-signed-out (the CLI's own sign_in_fix + a
 *    Sign in press that opens a Build pane through POST /terminals/launch —
 *    the click is the consent), signed in (green + "Use it for answers");
 *  - "Use it for answers" is the ONE explicit promotion press (W2-1, POST
 *    /onboarding/use-model): offered only while the default is still the
 *    demo, it refreshes /health, and it says what happened — including the
 *    server's "you already chose X" and a 409 sentence;
 *  - the Ollama door, once its URL is saved and Ollama is reachable, offers
 *    the same press;
 *  - lib/onboarding.ts honours contract W2-6 (`useModel`,
 *    `decodeOnboardingModel`).
 *
 * Harness lifted from first-run-wizard-v1197 (production-shaped useApi stub
 * that KEEPS data when the path goes null; `m` on the framer mock).
 */

const SIGN_IN_FIX_CLAUDE =
  "Claude Code isn't signed in. Open a terminal, run `claude`, then `/login`, then click Test on Connections.";
const SIGN_IN_FIX_CODEX =
  "Codex isn't signed in. Open a terminal, run `codex login`, then click Test on Connections.";

const hooks = vi.hoisted(() => ({
  api: {} as Record<string, unknown>,
  posts: [] as Array<{ path: string; body: unknown }>,
  puts: [] as Array<{ path: string; body: unknown }>,
  routerPush: vi.fn(),
  refreshHealth: vi.fn(),
  /** What POST /onboarding/use-model answers: a value, or an Error to reject. */
  useModelAnswer: null as unknown,
}));

vi.mock("@/lib/useApi", async () => {
  const { useRef } = await import("react");
  const useStub = (path: string | null) => {
    const last = useRef<unknown>(null);
    if (path !== null) last.current = hooks.api[path] ?? null;
    return {
      data: last.current ?? null,
      error: null,
      loading: false,
      reload: () => {
        if (path === "/onboarding") {
          hooks.api["/onboarding"] = {
            ...(hooks.api["/onboarding"] as Record<string, unknown>),
            first_run: false,
          };
        }
      },
    };
  };
  return { useApi: useStub, usePolledApi: useStub };
});

vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError,
    API_BASE: "http://test",
    ijToken: () => "tok-1",
    // SessionFiles (the files-handover pattern) reads /sessions/{id}/result
    // through `get`; serve the same fixtures the useApi stub serves.
    get: (path: string) => Promise.resolve(hooks.api[path] ?? {}),
    put: (path: string, body?: unknown) => {
      hooks.puts.push({ path, body });
      return Promise.resolve({});
    },
    patch: () => Promise.resolve({}),
    del: () => Promise.resolve({}),
    post: (path: string, body?: unknown) => {
      hooks.posts.push({ path, body });
      if (path === "/sessions") return Promise.resolve({ id: "s-1", status: "active" });
      if (path === "/onboarding/use-model") {
        const a = hooks.useModelAnswer;
        if (a instanceof Error) return Promise.reject(a);
        return Promise.resolve(a ?? { promoted: null, reason: "" });
      }
      if (path === "/terminals/launch") return Promise.resolve({ id: "term-1" });
      if (path.endsWith("/test")) return Promise.resolve({ ok: true, detail: "model replied" });
      return Promise.resolve({});
    },
  };
});

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    epoch: 0,
    provided: true,
    health: hooks.api["health"] ?? null,
    refresh: hooks.refreshHealth,
  }),
}));

vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false,
    reason: "no mic in tests",
    listening: false,
    processing: false,
    transcript: "",
    interim: "",
    error: null,
    start: () => {},
    stop: () => {},
    reset: () => {},
  }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: hooks.routerPush, replace: hooks.routerPush }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("framer-motion", async () => {
  const { createElement, Fragment } = await import("react");
  const MOTION_ONLY = new Set(["initial", "animate", "exit", "transition", "variants", "whileHover", "layout"]);
  const tagFor = (tag: string) => (props: Record<string, unknown>) => {
    const rest: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(props)) if (!MOTION_ONLY.has(k)) rest[k] = v;
    return createElement(tag, rest);
  };
  const cache = new Map<string, unknown>();
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
    AnimatePresence: ({ children }: { children?: unknown }) =>
      createElement(Fragment, null, children as never),
  };
});

import { ConnectDoors } from "@/components/onboarding/ConnectDoors";

/* ------------------------------------------------------------- fixtures --- */

type Row = Record<string, unknown>;

/** A fresh install on a PC with neither CLI: the REAL /health shape (mock is
 *  hidden by _visible_providers; the CLI rows carry installed/signed_in). */
const NOTHING_CONNECTED: Row[] = [
  { provider: "anthropic", available: false, class: "api", inherited_from: null },
  { provider: "openai", available: false, class: "api", inherited_from: null },
  {
    provider: "claude-cli",
    available: false,
    class: "cli",
    installed: false,
    signed_in: null,
    detail: "not installed",
    sign_in_fix: SIGN_IN_FIX_CLAUDE,
  },
  {
    provider: "codex-cli",
    available: false,
    class: "cli",
    installed: false,
    signed_in: null,
    detail: "not installed",
    sign_in_fix: SIGN_IN_FIX_CODEX,
  },
  { provider: "ollama", available: false, class: "local", inherited_from: null },
];

function withRows(overrides: Record<string, Row>): Row[] {
  return NOTHING_CONNECTED.map((r) =>
    overrides[r.provider as string] ? { ...r, ...overrides[r.provider as string] } : r,
  );
}

/** Claude Code installed AND signed in: /health lists claude-cli AND the
 *  keyless anthropic it serves (`inherited_from`). */
const CLAUDE_SIGNED_IN = withRows({
  "claude-cli": { available: true, installed: true, signed_in: true, detail: "signed in" },
  anthropic: { available: true, inherited_from: "claude-cli" },
});

/** Claude Code installed but signed out; Codex absent. */
const CLAUDE_SIGNED_OUT = withRows({
  "claude-cli": { available: false, installed: true, signed_in: false, detail: "not signed in" },
});

const OLLAMA_UP = withRows({ ollama: { available: true } });

function health(providers: Row[], defaultProvider = "mock", defaultModel = "claude-opus-4-8") {
  return {
    status: "ok",
    version: "1.310.0",
    default_provider: defaultProvider,
    default_model: defaultModel,
    providers,
  };
}

function onboarding(providers: Row[], defaultProvider = "mock") {
  return {
    version: "1.310.0",
    first_run: true,
    doctor: { ok: true, checks: [] },
    // W2-2's block, consistent with /health, in case the wizard reads it.
    model: {
      default_provider: defaultProvider,
      default_model: "claude-opus-4-8",
      is_mock: defaultProvider === "mock",
      usable: providers
        .filter((p) => p.available)
        .map((p) => ({ provider: p.provider as string, label: p.provider as string })),
    },
    checklist: [
      { key: "connect_ai", title: "Connect a model", detail: "", done: false, action: "" },
      {
        key: "set_up_voice",
        title: "Set up voice (optional)",
        detail: "",
        done: false,
        action: "",
        optional: true,
      },
      { key: "first_session", title: "Give it your first task", detail: "", done: false, action: "" },
    ],
    next_step: null,
  };
}

function setState(providers: Row[], defaultProvider = "mock") {
  hooks.api["health"] = health(providers, defaultProvider);
  hooks.api["/onboarding"] = onboarding(providers, defaultProvider);
}

const DOOR_SUB = /I already pay/;
const DOOR_LOCAL = /Free & private/;
const DOOR_KEY = /API key/;

/** The doors render on their own now (v1.439.0); the whole page is their box. */
const dialog = () => document.body;
const useModelPosts = () => hooks.posts.filter((p) => p.path === "/onboarding/use-model");

beforeEach(() => {
  hooks.api = { "/voice/status": { available: false, backend: null, hint: "" } };
  setState(NOTHING_CONNECTED);
  hooks.posts = [];
  hooks.puts = [];
  hooks.routerPush = vi.fn();
  hooks.refreshHealth = vi.fn();
  hooks.useModelAnswer = null;
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

/* =========================== 4. the subscription door's three states === */

describe("the subscription door tells the truth in three states (W2-3 rows)", () => {
  it("NOT INSTALLED: a web/desktop subscription can't be used here + vendor links; no irm|iex; an 'Install … for me' press (v1.449.0)", async () => {
    render(<ConnectDoors />);
    fireEvent.click(screen.getByRole("button", { name: DOOR_SUB }));
    const text = dialog().textContent ?? "";
    expect(text).toContain("Claude Code (the command-line tool)");
    expect(text).not.toMatch(/Claude app\/CLI/);
    // The one honest sentence: the app they pay for can't be shared here.
    expect(text).toMatch(/(desktop|web)/i);
    expect(text).toMatch(/(can.?t|cannot|isn.?t|is not|won.?t)[^.]{0,60}(shared|used|work)/i);
    // A plain link to each vendor's install page, opening outside the app.
    const hrefs = Array.from(dialog().querySelectorAll("a")).map((a) => a.getAttribute("href") ?? "");
    expect(hrefs.some((h) => /^https:\/\/[^/]*(anthropic\.com|claude\.com|claude\.ai)\//.test(h))).toBe(true);
    expect(hrefs.some((h) => /^https:\/\/[^/]*(openai\.com|github\.com\/openai\/codex)/.test(h))).toBe(true);
    // Never a pipe-to-shell command on screen for the user to paste.
    expect(text).not.toMatch(/\birm\b|\biex\b/);
    // v1.449.0 (readiness Wave C item 20, moved on purpose): the press that
    // was refused here now exists — it opens a Build terminal running the
    // vendor's OWN installer in view (never silently), asked for by the
    // daemon's catalog id; the page still sends no command.
    const installs = within(dialog()).getAllByRole("button", { name: /^Install .* for me$/ });
    expect(installs.map((b) => b.textContent)).toEqual(["Install Claude Code for me", "Install Codex for me"]);
    fireEvent.click(installs[0]);
    await waitFor(() =>
      expect(hooks.posts.find((p) => p.path === "/terminals/launch")?.body).toEqual({ cli: "claude-code-install" }),
    );
    // Nothing installed → nothing will "turn green on its own".
    expect(text).not.toMatch(/turns green on its own/i);
  });

  it("INSTALLED, SIGNED OUT: shows the CLI's own sign_in_fix and a Sign in press → POST /terminals/launch {cli: 'claude'}", async () => {
    setState(CLAUDE_SIGNED_OUT);
    render(<ConnectDoors />);
    fireEvent.click(screen.getByRole("button", { name: DOOR_SUB }));
    // v1.353.0 (G33): the door says to press Sign in and what the window it
    // opens asks for — never the CLI's terminal sentence above its own button.
    expect(dialog().textContent).toMatch(
      /Not signed in yet — press Sign in below\. A window opens; type \/login there/,
    );
    expect(dialog().textContent).not.toContain(SIGN_IN_FIX_CLAUDE);
    // v1.449.0 (moved on purpose): the not-installed Codex row beside it
    // names the Build terminal its install press opens; the signed-out ROW
    // itself still never says "terminal".
    expect(screen.getByTestId("signed-out-line-claude").closest("li")!.textContent).not.toMatch(/terminal/i);
    const signIn = within(dialog()).getAllByRole("button", { name: /^Sign in/ });
    expect(signIn).toHaveLength(1); // codex is not installed → no Sign in for it
    fireEvent.click(signIn[0]);
    await waitFor(() =>
      expect(hooks.posts.find((p) => p.path === "/terminals/launch")).toBeTruthy(),
    );
    const launch = hooks.posts.find((p) => p.path === "/terminals/launch")!;
    expect((launch.body as Record<string, unknown>).cli).toBe("claude");
  });

  it("CODEX SIGNED OUT: Sign in posts {cli: 'codex'} and the pane hint is Codex's own sign-in screen, never Claude's /login", async () => {
    /* Review fix (v1.310.0): the first cut told BOTH CLIs to "Type /login" —
       wrong for Codex and contradicting its sign_in_fix right above it. */
    setState(
      withRows({
        "codex-cli": { available: false, installed: true, signed_in: false, detail: "not signed in" },
      }),
    );
    render(<ConnectDoors />);
    fireEvent.click(screen.getByRole("button", { name: DOOR_SUB }));
    // v1.353.0 (G33): Codex's own screen, said before the press.
    expect(dialog().textContent).toMatch(
      /Not signed in yet — press Sign in below\. A window opens; Codex shows its own sign-in screen/,
    );
    expect(dialog().textContent).not.toContain(SIGN_IN_FIX_CODEX);
    const signIn = within(dialog()).getAllByRole("button", { name: /^Sign in/ });
    expect(signIn).toHaveLength(1);
    fireEvent.click(signIn[0]);
    const hint = await screen.findByTestId("pane-hint-codex");
    expect(hint.textContent).toMatch(/Sign in with ChatGPT/);
    expect(hint.textContent).not.toMatch(/\/login/);
    const launch = hooks.posts.find((p) => p.path === "/terminals/launch")!;
    expect((launch.body as Record<string, unknown>).cli).toBe("codex");
  });

  it("INSTALLED, sign-in NOT CHECKED YET (signed_in: null): a neutral 'checking' line, never a guessed 'not signed in'", async () => {
    setState(
      withRows({
        "claude-cli": { available: false, installed: true, signed_in: null, detail: "" },
      }),
    );
    render(<ConnectDoors />);
    fireEvent.click(screen.getByRole("button", { name: DOOR_SUB }));
    const text = dialog().textContent ?? "";
    expect(text).toMatch(/checking whether it.s signed in/i);
    expect(text).not.toMatch(/not signed in/i);
    expect(text).not.toContain(SIGN_IN_FIX_CLAUDE);
    expect(within(dialog()).queryByRole("button", { name: /^Sign in/ })).toBeNull();
    expect(within(dialog()).getByRole("button", { name: /Rescan now/ })).toBeTruthy();
  });

  it("SIGNED IN while the default is still the demo: no 'answers are real' claim, and 'Use it for answers' promotes via W2-1 + refreshes /health", async () => {
    setState(CLAUDE_SIGNED_IN);
    hooks.useModelAnswer = {
      promoted: { provider: "anthropic", model: "claude-opus-4-8" },
      reason: "",
    };
    render(<ConnectDoors />);
    // The mock-default trap: a connected CLI is NOT "answers are real" while
    // the default that chat uses is still the offline demo.
    expect(dialog().textContent).not.toMatch(/answers are real/i);
    hooks.refreshHealth.mockClear();
    fireEvent.click(within(dialog()).getByRole("button", { name: /for answers/i }));
    // v1.353.0 (G35): in the words of the press.
    await waitFor(() =>
      expect(within(dialog()).getByRole("status").textContent).toBe(
        "Done — Claude (your Claude Code sign-in) answers you now.",
      ),
    );
    expect(useModelPosts()).toHaveLength(1);
    expect(["claude-cli", "anthropic"]).toContain(
      (useModelPosts()[0].body as Record<string, unknown>).provider,
    );
    expect(hooks.refreshHealth).toHaveBeenCalled();
  });

  it("the press SAYS the server's no-op reason ('you already chose X') instead of claiming success", async () => {
    setState(CLAUDE_SIGNED_IN);
    const reason = "You already chose OpenAI for answers, so nothing changed.";
    hooks.useModelAnswer = { promoted: null, reason };
    render(<ConnectDoors />);
    fireEvent.click(within(dialog()).getByRole("button", { name: /for answers/i }));
    await waitFor(() => expect(dialog().textContent).toContain(reason));
  });

  it("the press SAYS a 409 refusal sentence (unknown/unavailable provider)", async () => {
    setState(CLAUDE_SIGNED_IN);
    hooks.useModelAnswer = new Error("anthropic is not available right now.");
    render(<ConnectDoors />);
    fireEvent.click(within(dialog()).getByRole("button", { name: /for answers/i }));
    await waitFor(() =>
      expect(within(dialog()).getByRole("alert").textContent).toContain(
        "anthropic is not available right now.",
      ),
    );
  });

  it("CONTROL: when the default is already the user's own real choice, no promotion press is offered", async () => {
    const rows = withRows({
      openai: { available: true },
      "claude-cli": { available: true, installed: true, signed_in: true },
      anthropic: { available: true, inherited_from: "claude-cli" },
    });
    setState(rows, "openai");
    render(<ConnectDoors />);
    expect(within(dialog()).queryByRole("button", { name: /for answers/i })).toBeNull();
  });
});

/* ============================ 4b. ConnectDoors on its own (the guard) === */

describe("ConnectDoors never offers promotion over the user's own choice", () => {
  it("rendered directly with a real default and a ready provider: no 'for answers' press", async () => {
    /* Review fix (v1.310.0, mutation M4): today's mounts only show the doors
       while the default is the demo, so the component's own `demo &&` guard
       was unreachable from the wizard and chat tests. Pin it where it lives. */
    const { ConnectDoors } = await import("@/components/onboarding/ConnectDoors");
    setState(
      withRows({
        openai: { available: true },
        "claude-cli": { available: true, installed: true, signed_in: true },
        anthropic: { available: true, inherited_from: "claude-cli" },
      }),
      "openai",
    );
    render(<ConnectDoors />);
    expect(screen.getByRole("button", { name: DOOR_SUB })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /for answers/i })).toBeNull();
    // CONTROL in the same render shape: the demo default DOES get the press.
    cleanup();
    setState(CLAUDE_SIGNED_IN);
    render(<ConnectDoors />);
    expect(screen.getByRole("button", { name: /for answers/i })).toBeTruthy();
  });
});

/* =========================================== 5. the Ollama door's press === */

describe("the Ollama door ends in the same explicit press", () => {
  it("Ollama reachable → the door's own 'Use Ollama for answers' posts {provider: 'ollama'} (v1.449.0, moved on purpose)", async () => {
    // v1.449.0 (readiness Wave C item 21): step 3 IS the one press — the
    // same use-model press the Ready panel offers (the daemon saves this
    // PC's address as part of it, G34), so "Connect" + "Use it" is one press.
    hooks.useModelAnswer = { promoted: { provider: "ollama", model: "llama3.2" }, reason: "" };
    setState(OLLAMA_UP);
    render(<ConnectDoors />);
    fireEvent.click(screen.getByRole("button", { name: DOOR_LOCAL }));
    expect(screen.queryByRole("button", { name: "Connect to Ollama on this PC" })).toBeNull();
    fireEvent.click(screen.getByTestId("ollama-use"));
    await waitFor(() =>
      expect(within(dialog()).getByRole("status").textContent).toMatch(/ollama|llama3\.2/i),
    );
    expect((useModelPosts()[0].body as Record<string, unknown>).provider).toBe("ollama");
    expect(hooks.puts.some((p) => p.path === "/settings")).toBe(false); // the daemon saves the address
  });
});

/* ===================================== 6. lib/onboarding.ts (W2-6) ====== */

/** lib/onboarding.ts is NEW in this wave. Loaded through a non-literal
 *  specifier so its absence fails THESE two tests (red for the right reason)
 *  instead of failing the whole file at import analysis. */
type OnboardingLib = {
  useModel: (provider: string) => Promise<{
    promoted: { provider: string; model: string } | null;
    reason: string;
  }>;
  decodeOnboardingModel: (raw: unknown) => {
    default_provider: string;
    default_model: string;
    is_mock: boolean;
    usable: { provider: string; label: string }[];
  } | null;
};
const ONBOARDING_LIB = "@/lib/onboarding";
async function loadOnboardingLib(): Promise<OnboardingLib> {
  return (await import(/* @vite-ignore */ ONBOARDING_LIB)) as OnboardingLib;
}

describe("lib/onboarding.ts — contract W2-6", () => {
  it("useModel(provider) POSTs /onboarding/use-model {provider} and resolves the decoded answer", async () => {
    const lib = await loadOnboardingLib();
    hooks.useModelAnswer = {
      promoted: { provider: "anthropic", model: "claude-opus-4-8" },
      reason: "",
      extra: "dropped",
    };
    const out = await lib.useModel("claude-cli");
    expect(useModelPosts()).toEqual([
      { path: "/onboarding/use-model", body: { provider: "claude-cli" } },
    ]);
    expect(out).toEqual({ promoted: { provider: "anthropic", model: "claude-opus-4-8" }, reason: "" });

    hooks.useModelAnswer = { promoted: "garbage", reason: 7 };
    const odd = await lib.useModel("ollama");
    expect(odd.promoted).toBeNull();
    expect(typeof odd.reason).toBe("string");
  });

  it("decodeOnboardingModel decodes W2-2's block and refuses junk", async () => {
    const { decodeOnboardingModel } = await loadOnboardingLib();
    const good = {
      default_provider: "mock",
      default_model: "claude-opus-4-8",
      is_mock: true,
      usable: [{ provider: "claude-cli", label: "Claude Code" }],
    };
    expect(decodeOnboardingModel(good)).toEqual(good);
    expect(decodeOnboardingModel(null)).toBeNull();
    expect(decodeOnboardingModel("model")).toBeNull();
    const messy = decodeOnboardingModel({
      default_provider: "mock",
      default_model: "x",
      is_mock: true,
      usable: [{ provider: "ollama", label: "Ollama" }, { provider: 3 }, "junk"],
    });
    expect(messy?.usable).toEqual([{ provider: "ollama", label: "Ollama" }]);
    const noList = decodeOnboardingModel({
      default_provider: "anthropic",
      default_model: "claude-opus-4-8",
      is_mock: false,
      usable: "nope",
    });
    expect(noList?.usable).toEqual([]);
  });
});
