/**
 * v1.378.0 REVIEW — the chat page after a provider's SAFETY REFUSAL, checked
 * again (transport mocked; the harness is provider-refusal-chat-v1378's,
 * copied).
 *
 *  - a refusal AFTER words streamed keeps the words as a cut-off reply — and
 *    that reply offered Continue and "Regenerate reply": both send the same
 *    conversation again to the same model, which declines it again (a Retry
 *    by another name). It is marked `refused` (saved with the thread, so a
 *    reopened chat knows) and offers neither; its note says the rest was
 *    declined.
 *  - "Start a new chat" KEEPS the declined conversation (it was saved; no
 *    delete) and carries no files into the new chat.
 *  - THE NEXT MESSAGE (decided): the failure line IS the note — it says every
 *    new message here will be declined too, and it stays on screen above the
 *    box while the user types, until a new send (sending is not blocked: the
 *    user may have edited).
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
      dels: [] as string[],
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
  del: async (path: string) => {
    H.api.dels.push(path);
    return {};
  },
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
import { NEW_CHAT_WORDS } from "@/lib/failureActions";

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
  H.api.dels.length = 0;
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


/** The daemon's words for a refusal AFTER words streamed (partial). */
const PARTIAL_REFUSAL = {
  title: "Claude Code declined to finish this answer",
  fix:
    "The reply above is incomplete. Its safety check on computer security flagged something in this " +
    "conversation, so every new message here will be declined too. Start a new chat, or edit an earlier " +
    "message to go back before the part it flagged.",
  technical: REFUSAL.technical,
  fixKind: "refusal",
  fixProvider: "claude-cli",
};

function failureWithPartial(words: Record<string, string>, partial: string) {
  const e = failure(words);
  Object.assign(e, { partial, committed: true });
  return e;
}

/** The last PUT of the open conversation. */
function lastSaved(): Record<string, unknown>[] {
  const puts = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/"));
  const last = puts[puts.length - 1];
  return (last?.body.messages ?? []) as Record<string, unknown>[];
}

describe("v1.378.0 review: a reply the provider declined to FINISH", () => {
  it("offers no Continue and no Regenerate (both resend the declined conversation), and is saved as refused", async () => {
    render(<ChatPage />);
    await startTurn("plan the release");
    await failWith(failureWithPartial(PARTIAL_REFUSAL, "Here is the plan: "));
    // The words that streamed stay, as a cut-off reply.
    expect(await screen.findByText("Here is the plan:")).toBeInTheDocument();
    // Its note says the rest was declined — not a generic "cut off".
    const note = await screen.findByTestId("reply-cut-note");
    expect(note.textContent).toBe("The rest of this reply was declined.");
    expect(screen.queryByTestId("continue-reply")).toBeNull();
    expect(screen.queryAllByRole("button", { name: /Regenerate reply/ })).toHaveLength(0);
    expect(screen.queryAllByRole("button", { name: /Retry/ })).toHaveLength(0);
    // Saved with the thread, so a reopened chat knows it too.
    await waitFor(() => {
      const saved = lastSaved();
      const reply = saved[saved.length - 1];
      expect(reply?.role).toBe("assistant");
      expect(reply?.refused).toBe(true);
    });
  });

  it("control: a cut-off reply that was NOT a refusal keeps Continue and Regenerate", async () => {
    render(<ChatPage />);
    await startTurn("plan the release");
    await failWith(
      failureWithPartial({ title: "Claude Code stopped mid-answer", fix: "Press Retry, or pick another model for this chat." }, "Here is the plan: "),
    );
    expect(await screen.findByTestId("continue-reply")).toBeInTheDocument();
    expect(screen.getByTestId("reply-cut-note").textContent).toBe("The reply was cut off.");
    expect(screen.getAllByRole("button", { name: /Regenerate reply/ }).length).toBeGreaterThan(0);
    const saved = lastSaved();
    expect(saved[saved.length - 1]?.refused).toBeUndefined();
  });

  it("a reopened chat whose last reply was declined still offers neither", async () => {
    H.api.getResponses["/chat/threads/t9"] = {
      id: "t9",
      title: "saved",
      messages: [
        { role: "user", content: "plan the release" },
        { role: "assistant", content: "Here is the plan: ", interrupted: true, refused: true },
      ],
      setup: { tools: [] },
    };
    window.history.replaceState({}, "", "/chat?thread=t9");
    render(<ChatPage />);
    const note = await screen.findByTestId("reply-cut-note");
    expect(note.textContent).toBe("The rest of this reply was declined.");
    expect(screen.queryByTestId("continue-reply")).toBeNull();
    expect(screen.queryAllByRole("button", { name: /Regenerate reply/ })).toHaveLength(0);
  });
});

describe("v1.378.0 review: Start a new chat keeps the declined conversation", () => {
  it("never deletes it, and the new chat starts with the words only", async () => {
    render(<ChatPage />);
    await startTurn("hello there");
    await failWith(failure(REFUSAL));
    // The declined conversation was SAVED (its question is on disk).
    await waitFor(() =>
      expect(H.api.puts.some((p) => JSON.stringify(p.body.messages ?? []).includes("hello there"))).toBe(true),
    );
    const savesBefore = H.api.puts.length;
    fireEvent.click(screen.getByTestId("chat-turn-new-chat"));
    await waitFor(() => expect(screen.queryByTestId("chat-turn-error")).toBeNull());
    expect(H.api.dels).toEqual([]);
    // Pressing it saves nothing over the old conversation and sends nothing.
    expect(H.api.puts.length).toBe(savesBefore);
    expect(H.stream.bodies).toHaveLength(1);
    expect(box().value).toBe("hello there");
  });
});

describe("v1.378.0 review: the NEXT message after a refusal (decided: a note, never a block)", () => {
  it("the failure line stays above the box while the user types; sending is not blocked", async () => {
    render(<ChatPage />);
    await startTurn("hello there");
    await failWith(failure(REFUSAL));
    fireEvent.change(box(), { target: { value: "Say hello" } });
    // Still on screen while typing: the line says new messages here are declined too.
    const line = screen.getByTestId("chat-turn-error");
    expect(line.textContent).toContain("every new message here will be declined too");
    expect(screen.getByTestId("chat-turn-new-chat")).toBeInTheDocument();
    // Not blocked: the user may have edited — Enter sends.
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(H.stream.bodies).toHaveLength(2));
  });
});

describe("v1.378.0 review: the words MOVE to the new chat (never copied)", () => {
  it("the declined conversation keeps no draft of the moved words", async () => {
    storeThread({ tools: [] });
    render(<ChatPage />);
    await screen.findByText("yo");
    await startTurn("again");
    await failWith(failure(REFUSAL));
    expect(box().value).toBe("again");
    fireEvent.click(screen.getByTestId("chat-turn-new-chat"));
    await waitFor(() => expect(screen.queryByTestId("chat-turn-error")).toBeNull());
    expect(box().value).toBe("again");
    const drafts = JSON.parse(window.localStorage.getItem("ij.chat.drafts") || "{}") as Record<
      string,
      { text?: string }
    >;
    expect(Object.values(drafts).some((d) => (d.text ?? "").includes("again"))).toBe(false);
  });
});
