/**
 * v1.378.0 — a provider's SAFETY REFUSAL on the REAL chat page (transport
 * mocked; the harness is chat-recovery-v1312's, copied).
 *
 * When Anthropic's classifier declines a conversation (stop_reason
 * "refusal"), it judges the WHOLE conversation: every later message — even
 * "Say hello" — is declined too. The daemon now answers the turn with ONE
 * plain sentence and `fix_kind: "refusal"` (chat_turn.fix_action), and the
 * failure line:
 *
 *  - shows the WHOLE sentence (title and fix — the fix says why and how to
 *    go back), never the headline alone;
 *  - offers NO Retry and no "Retry with the default model" (Retry is judged
 *    again; the default would be sending it elsewhere to get past the check);
 *  - offers "Start a new chat", which leaves this conversation and moves the
 *    message into the new chat's box.
 *
 * Controls: a failure that is not a refusal keeps Retry (and "Retry with the
 * default model" when the pick is down and the default up), and no new-chat
 * press.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
    code = "";
    provider = "";
    model = "";
    title = "";
    fix = "";
    technical = "";
    fixKind = "";
    fixProvider = "";
  }
  const stream = {
    bodies: [] as Record<string, unknown>[],
    streaming: false,
    approval: null as null,
    version: 0,
    listeners: new Set<() => void>(),
    settle: null as null | { ok: (r: Record<string, unknown>) => void; fail: (e: unknown) => void },
    bump() {
      stream.version += 1;
      for (const l of [...stream.listeners]) l();
    },
    release(r: Record<string, unknown>) {
      const s = stream.settle;
      stream.settle = null;
      stream.streaming = false;
      stream.bump();
      s?.ok(r);
    },
    fail(e: unknown) {
      const s = stream.settle;
      stream.settle = null;
      stream.streaming = false;
      stream.bump();
      s?.fail(e);
    },
  };
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> | undefined }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
    },
    stream,
    health: {
      byProvider: {} as Record<string, boolean>,
      cooldownByProvider: {} as Record<string, number>,
      signedOutByProvider: {} as Record<string, boolean>,
      defaultProvider: "",
      loading: false,
      stale: false,
      refresh: () => {},
    },
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
  post: async (path: string, body?: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    return r ?? {};
  },
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
    const m = /^\/chat\/threads\/(.+)$/.exec(path);
    return { id: m && m[1] !== "new" ? m[1] : "t1", title: "t" };
  },
  del: async () => ({}),
}));

vi.mock("@/lib/useChatStream", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  const run = (body: Record<string, unknown>) => {
    H.stream.bodies.push(body);
    H.stream.streaming = true;
    H.stream.bump();
    return new Promise<Record<string, unknown>>((ok, fail) => {
      H.stream.settle = { ok, fail };
    });
  };
  const abort = () => {
    if (H.stream.settle) H.stream.release({ reply: "" });
  };
  return {
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => {
      React.useSyncExternalStore(subscribe, () => H.stream.version);
      return {
        streaming: H.stream.streaming,
        text: "",
        tools: [],
        approval: H.stream.approval,
        phase: H.stream.streaming ? "working" : null,
        withFiles: false,
        startedAt: null,
        lastEventAt: null,
        run,
        abort,
      };
    },
  };
});
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
  useProviderHealth: () => H.health,
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";
import { NEW_CHAT_WORDS, failureAction, isRefusal } from "@/lib/failureActions";

/** Exactly what the daemon sends for the reviewer's refusal (tests/
 *  test_provider_refusal_v1378.py pins the Python side of these words). */
const REFUSAL = {
  title: "Claude Code declined to answer this conversation",
  fix:
    "Its safety check on computer security flagged something in this conversation, so every new " +
    "message here will be declined too. Start a new chat, or edit an earlier message to go back " +
    "before the part it flagged.",
  technical: "claude-cli: Anthropic's safety check declined to answer (stop_reason refusal, category cyber)",
  fixKind: "refusal",
  fixProvider: "claude-cli",
};

function resetHealth(over: Partial<typeof H.health> = {}) {
  H.health.byProvider = {};
  H.health.cooldownByProvider = {};
  H.health.signedOutByProvider = {};
  H.health.defaultProvider = "";
  Object.assign(H.health, over);
}

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.streaming = false;
  H.stream.settle = null;
  resetHealth();
  for (const k of Object.keys(H.api.postResponses)) delete H.api.postResponses[k];
  H.api.getResponses = {
    "/models": { models: [] },
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
  window.sessionStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function storeThread(setup: Record<string, unknown>) {
  H.api.getResponses["/chat/threads/t7"] = {
    id: "t7",
    title: "saved",
    messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: "yo" },
    ],
    setup,
  };
  window.history.replaceState({}, "", "/chat?thread=t7");
}

const box = () => screen.getByPlaceholderText(/Message Iron Jarvis/) as HTMLTextAreaElement;

async function startTurn(text: string) {
  const n = H.stream.bodies.length;
  const b = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(b, { target: { value: text } });
  fireEvent.keyDown(b, { key: "Enter" });
  await waitFor(() => expect(H.stream.bodies.length).toBe(n + 1));
  await waitFor(() => expect(H.stream.settle).not.toBeNull());
}

function failure(words: Record<string, string>) {
  const e = new H.FakeStreamError("the server's own detail", 0);
  Object.assign(e, words);
  return e;
}

/** Fail the turn and wait for its failure LINE (the thing asserted). */
async function failWith(e: unknown) {
  act(() => H.stream.fail(e));
  return screen.findByTestId("chat-turn-error");
}

describe("v1.378.0: a safety refusal's failure line", () => {
  it("is the whole sentence, with no Retry of any kind, and a Start a new chat press", async () => {
    render(<ChatPage />);
    await startTurn("hello there");
    const line = await failWith(failure(REFUSAL));
    expect(line.textContent).toBe(`${REFUSAL.title}. ${REFUSAL.fix}`);
    expect(screen.queryAllByRole("button", { name: /Retry/ })).toHaveLength(0);
    const press = screen.getByTestId("chat-turn-new-chat");
    expect(press).toHaveTextContent(NEW_CHAT_WORDS);
    // The message is back in the box (as for any failed turn).
    expect(box().value).toBe("hello there");
  });

  it("Start a new chat leaves the declined conversation and moves the message into the new box", async () => {
    render(<ChatPage />);
    await startTurn("hello there");
    await failWith(failure(REFUSAL));
    // The declined conversation is on screen (its question bubble — the box
    // itself is not counted).
    const shown = () => screen.queryAllByText("hello there", { ignore: "textarea, script, style" });
    expect(shown().length).toBeGreaterThan(0);
    fireEvent.click(screen.getByTestId("chat-turn-new-chat"));
    await waitFor(() => expect(screen.queryByTestId("chat-turn-error")).toBeNull());
    // A fresh chat: the old conversation is gone, the words are in the box.
    expect(shown()).toHaveLength(0);
    expect(box().value).toBe("hello there");
    // Nothing was sent by the press.
    expect(H.stream.bodies).toHaveLength(1);
  });

  it("never offers Retry with the default model, even when the pick is down and the default up", async () => {
    storeThread({ tools: [], provider: "openai", model: "gpt-5" });
    resetHealth({ defaultProvider: "custom", byProvider: { openai: false, custom: true } });
    render(<ChatPage />);
    await screen.findByText("yo");
    await startTurn("again");
    await failWith(failure({ ...REFUSAL, fixProvider: "openai" }));
    expect(screen.queryByText("Retry with the default model")).toBeNull();
    expect(screen.queryAllByRole("button", { name: /Retry/ })).toHaveLength(0);
    expect(screen.getByTestId("chat-turn-new-chat")).toBeInTheDocument();
  });

  it("control: a failure that is not a refusal keeps Retry, the default's Retry, and no new-chat press", async () => {
    storeThread({ tools: [], provider: "openai", model: "gpt-5" });
    resetHealth({ defaultProvider: "custom", byProvider: { openai: false, custom: true } });
    render(<ChatPage />);
    await screen.findByText("yo");
    await startTurn("again");
    await failWith(failure({ title: "OpenAI is busy", fix: "Try again shortly, or pick another model for this chat." }));
    expect(screen.getByRole("button", { name: /^Retry$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry with the default model" })).toBeInTheDocument();
    expect(screen.queryByTestId("chat-turn-new-chat")).toBeNull();
  });
});

describe("v1.378.0: lib/failureActions reads the refusal from the typed fix_kind only", () => {
  it("a refusal is its own action; the words never decide it", () => {
    expect(failureAction({ fixKind: "refusal", fixProvider: "claude-cli" }, null, "claude-cli")).toEqual({
      kind: "refusal",
    });
    expect(isRefusal({ fixKind: "refusal" })).toBe(true);
    expect(isRefusal({ fixKind: "" })).toBe(false);
    expect(isRefusal(null)).toBe(false);
    // A sentence that merely SAYS "declined" is not a refusal.
    expect(failureAction({ fixKind: "", fixProvider: "" }, null, "")).toBeNull();
  });
});
