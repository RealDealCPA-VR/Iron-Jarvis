/**
 * Wave 2 (v1.310.0) — the chat EMPTY STATE leads somewhere (finding
 * demo-mode-chat-nonsense, as adjusted by the verifier: no scripted demo
 * replies — the cheap honest part only).
 *
 * A new user who skipped the wizard, or connected a CLI/Ollama while the
 * default stayed the offline demo, lands on /chat — the hero surface — and the
 * empty state offered only example chips, every one answered by "Done. Wrote
 * RESULT.md summarizing the task.". Now, when no real provider is available OR
 * the default is still the demo, the shared ConnectDoors render ABOVE the
 * example chips; a signed-in Claude Code there offers the ONE explicit
 * "Use it for answers" press (W2-1). With a real default answering, the empty
 * state is exactly the chips again (anti-vacuity control).
 *
 * The page is rendered for real; transport hooks mocked at the usual seams
 * (harness lifted from chat-picker-inherited-v1230), plus @/lib/daemon so the
 * doors read the same /health the rest of the page reads.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
  put: async (path: string) => {
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
      run: async () => ({ reply: "" }),
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

describe("chat empty state — connect doors when no real model answers", () => {
  it("no model connected: the connect doors render ABOVE the example chips", async () => {
    render(<ChatPage />);
    const chip = await screen.findByRole("button", { name: FIRST_CHIP });
    const sub = await screen.findByRole("button", { name: DOOR_SUB });
    expect(screen.getByRole("button", { name: /Free & private/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /API key/ })).toBeTruthy();
    // Above the chips: the way forward comes before the demo prompts.
    expect(sub.compareDocumentPosition(chip) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("Claude Code signed in but the default is still the demo: doors shown, and 'Use it for answers' posts W2-1", async () => {
    setHealth(CLAUDE_SIGNED_IN, "mock");
    render(<ChatPage />);
    await screen.findByRole("button", { name: FIRST_CHIP });
    const sub = await screen.findByRole("button", { name: DOOR_SUB });
    // The press may sit inside the subscription door: open it when it isn't
    // already on screen.
    if (!screen.queryByRole("button", { name: /for answers/i })) fireEvent.click(sub);
    fireEvent.click(await screen.findByRole("button", { name: /for answers/i }));
    await waitFor(() =>
      expect(H.posts.find((p) => p.path === "/onboarding/use-model")).toBeTruthy(),
    );
    const body = H.posts.find((p) => p.path === "/onboarding/use-model")!.body as Record<string, unknown>;
    expect(["claude-cli", "anthropic"]).toContain(body.provider);
  });

  it("CONTROL: a real default that answers — the empty state is just the chips, no doors", async () => {
    setHealth(CLAUDE_SIGNED_IN, "anthropic");
    render(<ChatPage />);
    await screen.findByRole("button", { name: FIRST_CHIP });
    expect(screen.queryByRole("button", { name: DOOR_SUB })).toBeNull();
    expect(screen.queryByRole("button", { name: /for answers/i })).toBeNull();
  });
});
