/**
 * Calm UI redesign S9 — first run with NO blocking screen (brief T3, AUDIT
 * §4.6 / Q7) and ONE model selector, the composer's (AUDIT Q3).
 *
 * The first-run wizard is not mounted; a model this PC already has is offered
 * in the composer with one tap (a suggestion — nothing changes until the
 * press). The title-bar chip is gone: "Switch model" anywhere opens the
 * composer's menu, which also offers "Make this my default".
 *
 * Harness: the connect-doors page harness (it mocks /health), with replies.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const nav = vi.hoisted(() => ({ pathname: "/chat", push: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: nav.push, replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    FakeApiError,
    getResponses: {} as Record<string, unknown>,
    posts: [] as Array<{ path: string; body: unknown }>,
    puts: [] as Array<{ path: string; body: unknown }>,
    health: null as Record<string, unknown> | null,
    refresh: vi.fn(),
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body?: unknown) => {
    H.posts.push({ path, body });
    if (path === "/onboarding/use-model") {
      return { promoted: { provider: "anthropic", model: "claude-opus-4-8" }, reason: "" };
    }
    return {};
  },
  put: async (path: string, body?: unknown) => {
    H.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  patch: async () => ({}),
  del: async () => ({}),
}));

vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    checking: false,
    epoch: 0,
    provided: true,
    health: H.health,
    refresh: H.refresh,
  }),
}));

vi.mock("@/lib/useChatStream", () => {
  class StreamError extends Error {
    status = 0;
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    StreamError,
    useChatStream: () => ({
      streaming: false,
      text: "",
      tools: [],
      approval: null,
      run: async (_b: unknown, onDelta: (d: string, f: string) => void) => {
        onDelta("answered", "answered");
        return { reply: "answered" };
      },
      abort: () => {},
    }),
  };
});

vi.mock("@/lib/useEvents", () => ({
  useEvents: () => ({ events: [], connected: false }),
}));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({
    text: "",
    tools: [],
    phase: null,
    active: false,
    start: () => {},
    stop: () => {},
  }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false,
    reason: null,
    engine: null,
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
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false,
    enabled: false,
    speaking: false,
    enable: () => {},
    disable: () => {},
    toggle: () => {},
    speak: () => {},
    resetStream: () => {},
    speakMore: () => {},
    cancel: () => {},
  }),
}));
// Same answer as /health, through the page's other health reader.
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => {
    const h = H.health as { default_provider?: string; providers?: Array<Record<string, unknown>> } | null;
    const byProvider: Record<string, boolean> = {};
    for (const p of h?.providers ?? []) byProvider[String(p.provider)] = Boolean(p.available);
    return {
      byProvider,
      signedOutByProvider: {},
      defaultProvider: h?.default_provider ?? "",
      loading: false,
      stale: false,
      refresh: () => {},
    };
  },
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

type Row = Record<string, unknown>;

const NOTHING_CONNECTED: Row[] = [
  { provider: "anthropic", available: false, class: "api", inherited_from: null },
  { provider: "openai", available: false, class: "api", inherited_from: null },
  { provider: "claude-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "codex-cli", available: false, class: "cli", installed: false, signed_in: null, sign_in_fix: "" },
  { provider: "ollama", available: false, class: "local", inherited_from: null },
];

const CLAUDE_SIGNED_IN: Row[] = NOTHING_CONNECTED.map((r) =>
  r.provider === "claude-cli"
    ? { ...r, available: true, installed: true, signed_in: true }
    : r.provider === "anthropic"
      ? { ...r, available: true, inherited_from: "claude-cli" }
      : r,
);

function setHealth(providers: Row[], defaultProvider: string) {
  H.health = {
    status: "ok",
    version: "1.310.0",
    default_provider: defaultProvider,
    default_model: "claude-opus-4-8",
    providers,
  };
  H.getResponses["/onboarding"] = {
    version: "1.310.0",
    first_run: false,
    doctor: { ok: true, checks: [] },
    checklist: [],
    next_step: null,
    model: {
      default_provider: defaultProvider,
      default_model: "claude-opus-4-8",
      is_mock: defaultProvider === "mock",
      usable: providers
        .filter((p) => p.available)
        .map((p) => ({ provider: String(p.provider), label: String(p.provider) })),
    },
  };
}

function resetApi() {
  for (const k of Object.keys(H.getResponses)) delete H.getResponses[k];
  H.getResponses["/models"] = { models: [] };
  H.getResponses["/chat/personas"] = { personas: [] };
  H.getResponses["/chat/threads"] = { threads: [] };
  H.getResponses["/settings"] = { settings: {} };
  H.getResponses["/projects"] = { projects: [] };
  H.getResponses["/agents/mentionable"] = { agents: [] };
  H.getResponses["/skills"] = { skills: [] };
  H.getResponses["/workflows"] = { workflows: [] };
  H.getResponses["/tools"] = { tools: [] };
  H.getResponses["/undo?session_id=chat"] = { actions: [] };
}

const FIRST_CHIP = "What can you do?"; // CHAT_EXAMPLES[0], the pinned anchor
const DOOR_SUB = /I already pay/;

beforeEach(() => {
  resetApi();
  H.posts = [];
  H.puts = [];
  nav.pathname = "/chat";
  nav.push.mockReset();
  H.refresh = vi.fn();
  setHealth(NOTHING_CONNECTED, "mock");
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function sendOne() {
  const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(el, { target: { value: "hello" } });
  fireEvent.keyDown(el, { key: "Enter" });
  await screen.findByText("answered");
}

describe("first run with no blocking screen (redesign S9, AUDIT Q7)", () => {
  it("the first-run wizard is no longer mounted, and the title bar has no model chip", () => {
    const overlays = readFileSync(join(__dirname, "..", "components", "Overlays.tsx"), "utf-8");
    expect(overlays).not.toMatch(/import\("@\/components\/FirstRunWizard"\)/);
    expect(overlays).not.toContain("<FirstRunWizard />");
    const layout = readFileSync(join(__dirname, "..", "app", "layout.tsx"), "utf-8");
    expect(layout).not.toContain("<ModelSwitcher />");
    expect(layout).toContain("<ModelMenuBridge />");
  });

  it("a detected model is offered with ONE tap in the composer — never picked silently", async () => {
    setHealth(CLAUDE_SIGNED_IN, "mock");
    render(<ChatPage />);
    // Before the first message the empty state's doors make the offer (once).
    await screen.findByRole("button", { name: DOOR_SUB });
    expect(screen.queryByTestId("model-suggest")).toBeNull();
    await sendOne();
    const chip = await screen.findByTestId("model-suggest");
    expect(H.posts.find((p) => p.path === "/onboarding/use-model")).toBeUndefined();
    fireEvent.click(within(chip).getByRole("button", { name: /Use Claude Code for answers/ }));
    await screen.findByTestId("model-suggest-done");
    const press = H.posts.find((p) => p.path === "/onboarding/use-model")!;
    expect((press.body as Record<string, unknown>).provider).toBe("claude-cli");
    expect(H.refresh).toHaveBeenCalled();
  });

  it("CONTROL: with a real default there is nothing to suggest", async () => {
    setHealth(CLAUDE_SIGNED_IN, "anthropic");
    render(<ChatPage />);
    await sendOne();
    expect(screen.queryByTestId("model-suggest")).toBeNull();
  });
});

describe("one model selector — the composer's (redesign S9, AUDIT Q3)", () => {
  it("the app-wide 'switch model' door opens the composer's menu", async () => {
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    expect(screen.queryByTestId("model-menu")).toBeNull();
    await act(async () => {
      window.dispatchEvent(new Event("ij:open-switcher"));
    });
    expect(screen.getByTestId("model-menu")).toBeTruthy();
  });

  it("?model=1 (the door from another page) arrives with the menu open, and is stripped", async () => {
    window.history.replaceState({}, "", "/?model=1");
    render(<ChatPage />);
    expect(await screen.findByTestId("model-menu")).toBeTruthy();
    expect(window.location.search).toBe("");
  });

  it("'Make this my default' saves the conversation's pick through the settings writer", async () => {
    H.getResponses["/models"] = {
      models: [{ provider: "anthropic", model: "claude-opus-4-8", available: true, kind: "subscription" }],
    };
    render(<ChatPage />);
    await screen.findByPlaceholderText(/Message Iron Jarvis/);
    await act(async () => {
      window.dispatchEvent(new Event("ij:open-switcher"));
    });
    fireEvent.change(screen.getByTestId("model-filter"), { target: { value: "opus" } });
    fireEvent.keyDown(screen.getByTestId("model-filter"), { key: "Enter" });
    await act(async () => {
      window.dispatchEvent(new Event("ij:open-switcher"));
    });
    fireEvent.click(await screen.findByTestId("model-make-default"));
    await waitFor(() => expect(H.puts.find((p) => p.path === "/settings")).toBeTruthy());
    expect(H.puts.find((p) => p.path === "/settings")!.body).toEqual({
      values: { default_provider: "anthropic", default_model: "claude-opus-4-8" },
    });
    expect(await screen.findByText("Saved as your default.")).toBeTruthy();
  });

  it("off the chat surface, the door takes the user to the home chat with the menu open", async () => {
    const { ModelMenuBridge } = await import("@/components/ModelMenuBridge");
    nav.pathname = "/usage";
    render(<ModelMenuBridge />);
    await act(async () => {
      window.dispatchEvent(new Event("ij:open-switcher"));
    });
    expect(nav.push).toHaveBeenCalledWith("/?model=1");
    cleanup();
    nav.push.mockReset();
    nav.pathname = "/";
    render(<ModelMenuBridge />);
    await act(async () => {
      window.dispatchEvent(new Event("ij:open-switcher"));
    });
    expect(nav.push).not.toHaveBeenCalled();
  });
});
