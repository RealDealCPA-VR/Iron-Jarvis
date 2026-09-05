/**
 * v1.230.0 (audit Wave 4, U5) — the chat model picker calls an inherited login
 * "included", not "metered".
 *
 * Anthropic served through the logged-in `claude` CLI costs nothing per token
 * (the subscription pays), but the picker keyed its badge off `kind: "api"`
 * and labelled it "metered" — the same lie the Connections pill told from the
 * other side. `/models` rows now carry `inherited_from` (the daemon's one
 * answer, shared with /health and /connections); a row that names a CLI ranks
 * and labels as a CLI.
 *
 * The page is rendered for real; transport hooks mocked at the usual seams
 * (harness lifted from chat-durable-turns-v1226).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

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
  post: async () => ({}),
  put: async (path: string) => {
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", () => {
  class StreamError extends Error {
    status = 0;
    committed = false;
    offline = false;
    partial = "";
  }
  return {
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
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({
    byProvider: {},
    defaultProvider: "",
    loading: false,
    stale: false,
    refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => <div>{children}</div>,
}));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

function resetApi() {
  for (const k of Object.keys(H.getResponses)) delete H.getResponses[k];
  H.getResponses["/models"] = {
    models: [
      // Keyless Anthropic, served through the logged-in claude CLI.
      {
        provider: "anthropic",
        model: "claude-opus-4-8",
        name: "Anthropic",
        kind: "api",
        available: true,
        inherited_from: "claude-cli",
      },
      // A metered API with a stored key.
      {
        provider: "openai",
        model: "gpt-5.5",
        name: "OpenAI",
        kind: "api",
        available: true,
        inherited_from: null,
      },
    ],
  };
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

beforeEach(() => {
  resetApi();
  window.history.replaceState({}, "", "/chat");
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("chat model picker — inherited login is 'included'", () => {
  it("labels the inherited provider 'included' and the keyed API 'metered', inherited first", async () => {
    render(<ChatPage />);
    fireEvent.click(await screen.findByTitle("Switch model"));
    const anthropic = (await screen.findByText("Anthropic")).closest("button")!;
    expect(anthropic.textContent).toContain("included");
    expect(anthropic.textContent).not.toContain("metered");
    const openai = screen.getByText("OpenAI").closest("button")!;
    expect(openai.textContent).toContain("metered");
    // Flat-rate ranks above metered: the inherited row comes first.
    expect(
      anthropic.compareDocumentPosition(openai) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
});
