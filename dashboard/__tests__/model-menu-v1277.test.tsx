/**
 * v1.277.0 — the composer's model menu: type to find a model, and the last picks first.
 *
 * The daily driver's catalog is 49 models across 12 providers behind a two-level
 * menu (provider → model), so every switch was a hunt. Now the menu opens with a
 * filter box: a typed word lists every model whose id, provider or display name
 * contains it, Enter picks the first, and a row click picks that one. With the
 * box empty the menu opens on the last three picks (remembered in this browser)
 * above the provider tree it always had.
 *
 * Pinned on the real page with only the transport mocked. Header, mocks and
 * helpers are the reasoning-level harness verbatim.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream: { bodies: [] as Record<string, unknown>[] },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "",
  ijToken: () => "",
  get: async (path: string) => {
    const r = H.api.getResponses[path];
    if (r === undefined) throw new H.FakeApiError(`unmocked GET ${path}`, 404);
    return r;
  },
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
    return r ?? {};
  },
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => ({
  StreamError: H.FakeStreamError,
  useLiveText: (s: { text?: string }) => s?.text ?? "",
  useChatStream: () => ({
    streaming: false,
    text: "",
    tools: [],
    approval: null,
    run: async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
      H.stream.bodies.push(body);
      await new Promise<void>((r) => setTimeout(r, 0));
      onDelta("done", "done");
      return { reply: "done" };
    },
    abort: () => {},
  }),
}));
vi.mock("@/lib/useEvents", () => ({ useEvents: () => ({ events: [], connected: false }) }));
vi.mock("@/lib/useRunStream", () => ({
  useRunStream: () => ({ text: "", tools: [], phase: null, active: false, start: () => {}, stop: () => {} }),
}));
vi.mock("@/lib/useDictation", () => ({
  useDictation: () => ({
    supported: false, reason: null, engine: null, listening: false, processing: false,
    transcript: "", interim: "", error: null, start: () => {}, stop: () => {}, reset: () => {},
  }),
}));
vi.mock("@/lib/useTTS", () => ({
  useTTS: () => ({
    supported: false, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import {
  RECENT_MODELS_KEY,
  RECENT_MODELS_MAX,
  matchModels,
  readRecentModels,
  rememberRecentModel,
} from "@/lib/recentModels";

const CATALOG = {
  models: [
    { provider: "custom", model: "fleet", name: "Fleet", available: true, kind: "local" },
    { provider: "anthropic", model: "claude-sonnet-5", name: "Anthropic", available: true, kind: "api" },
    { provider: "anthropic", model: "claude-opus-5", name: "Anthropic", available: true, kind: "api" },
    { provider: "openai", model: "gpt-5", name: "OpenAI", available: true, kind: "api" },
    { provider: "openrouter", model: "anthropic/claude-sonnet-5", name: "OpenRouter", available: true, kind: "api" },
    { provider: "xai", model: "grok-4", name: "xAI", available: false, kind: "api" },
  ],
};

beforeEach(() => {
  H.api.posts.length = 0;
  H.stream.bodies.length = 0;
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": CATALOG,
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function openMenu() {
  await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.click(await screen.findByTitle("Switch model"));
  return screen.findByTestId("model-filter") as Promise<HTMLInputElement>;
}

async function send(text: string) {
  const box = await screen.findByPlaceholderText(/Message Iron Jarvis/);
  fireEvent.change(box, { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(H.stream.bodies.length).toBeGreaterThan(0));
  return H.stream.bodies[H.stream.bodies.length - 1];
}

describe("type to find a model (v1.277.0)", () => {
  it("lists every model containing the word, across providers, and hides the tree", async () => {
    render(<ChatPage />);
    const box = await openMenu();
    const menu = screen.getByTestId("model-menu");
    // The tree is there with an empty box.
    expect(within(menu).getByText("OpenAI").closest("button")).not.toBeNull();
    expect(within(menu).getByText("default model")).not.toBeNull();
    fireEvent.change(box, { target: { value: "sonnet" } });
    const rows = await screen.findAllByTestId("model-match");
    expect(rows.map((r) => r.textContent)).toEqual([
      "claude-sonnet-5Anthropic",
      "anthropic/claude-sonnet-5OpenRouter",
    ]);
    // The provider tree is gone while a filter is typed (one list, not two):
    // no default-model row, no provider row for a provider with no match.
    expect(within(menu).queryByText("default model")).toBeNull();
    expect(within(menu).queryByText("OpenAI")).toBeNull();
    expect(within(menu).queryByText("Fleet")).toBeNull();
  });

  it("Enter picks the first match; the pick rides the next turn and is remembered", async () => {
    render(<ChatPage />);
    const box = await openMenu();
    fireEvent.change(box, { target: { value: "opus" } });
    await screen.findAllByTestId("model-match");
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(screen.queryByTestId("model-filter")).toBeNull());
    expect((await screen.findByTitle("Switch model")).textContent).toContain("claude-opus-5");
    const body = await send("hello");
    expect(body.provider).toBe("anthropic");
    expect(body.model).toBe("claude-opus-5");
    expect(readRecentModels()).toEqual(["anthropic::claude-opus-5"]);
  });

  it("a row click picks that model, and a word nobody has says so", async () => {
    render(<ChatPage />);
    const box = await openMenu();
    fireEvent.change(box, { target: { value: "zzz-nothing" } });
    expect(await screen.findByText("No model matches.")).not.toBeNull();
    fireEvent.change(box, { target: { value: "gpt" } });
    fireEvent.click((await screen.findAllByTestId("model-match"))[0]);
    expect((await screen.findByTitle("Switch model")).textContent).toContain("gpt-5");
  });

  it("a model whose provider is offline is listed but cannot be picked", async () => {
    render(<ChatPage />);
    const box = await openMenu();
    fireEvent.change(box, { target: { value: "grok" } });
    const [row] = await screen.findAllByTestId("model-match");
    expect((row as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("the last picks first (v1.277.0)", () => {
  it("opens on the remembered picks, newest first, and only ones the catalog still has", async () => {
    window.localStorage.setItem(
      RECENT_MODELS_KEY,
      JSON.stringify(["openai::gpt-5", "anthropic::claude-sonnet-5", "gone::model"]),
    );
    render(<ChatPage />);
    await openMenu();
    const recent = await screen.findByTestId("model-recent");
    const names = within(recent)
      .getAllByRole("button")
      .map((b) => b.textContent);
    expect(names).toEqual(["gpt-5OpenAI", "claude-sonnet-5Anthropic"]);
    // The tree still follows.
    expect(within(screen.getByTestId("model-menu")).getByText("default model")).not.toBeNull();
    fireEvent.click(within(recent).getAllByRole("button")[1]);
    expect((await screen.findByTitle("Switch model")).textContent).toContain("claude-sonnet-5");
    expect(readRecentModels()[0]).toBe("anthropic::claude-sonnet-5");
  });

  it("shows no Recent section before anything was picked; the default pick is not remembered", async () => {
    render(<ChatPage />);
    await openMenu();
    expect(screen.queryByTestId("model-recent")).toBeNull();
    fireEvent.click(within(screen.getByTestId("model-menu")).getByText("default model"));
    expect(readRecentModels()).toEqual([]);
  });
});

describe("lib/recentModels", () => {
  it("keeps the newest first, once, bounded, and ignores junk in storage", () => {
    window.localStorage.setItem(RECENT_MODELS_KEY, JSON.stringify(["a::1", 7, "plain", "b::2"]));
    expect(readRecentModels()).toEqual(["a::1", "b::2"]);
    rememberRecentModel("c::3");
    rememberRecentModel("a::1");
    expect(readRecentModels()).toEqual(["a::1", "c::3", "b::2"]);
    rememberRecentModel("d::4");
    expect(readRecentModels()).toHaveLength(RECENT_MODELS_MAX);
    expect(readRecentModels()[0]).toBe("d::4");
    expect(rememberRecentModel("")).toEqual(readRecentModels());
    window.localStorage.setItem(RECENT_MODELS_KEY, "{not json");
    expect(readRecentModels()).toEqual([]);
  });

  it("matches on id, provider and display name, case-insensitively, bounded", () => {
    const rows = CATALOG.models;
    expect(matchModels(rows, "  ", 5)).toEqual([]);
    expect(matchModels(rows, "ANTHROPIC", 5).map((m) => m.model)).toEqual([
      "claude-sonnet-5",
      "claude-opus-5",
      "anthropic/claude-sonnet-5",
    ]);
    expect(matchModels(rows, "fleet", 5).map((m) => m.provider)).toEqual(["custom"]);
    expect(matchModels(rows, "a", 2)).toHaveLength(2);
  });
});
