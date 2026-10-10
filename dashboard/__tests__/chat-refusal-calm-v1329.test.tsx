/**
 * Calm chat J1 (v1.329.0): a refused turn tells the truth, calmly.
 *
 * The closing audit drove a real refusal (a dead Anthropic-compatible fleet
 * node) and found the turn's error drawn as a rose bordered box with its own
 * icon, unlike every other notice in the tray above the composer. It is now
 * ONE line in the danger tone (still role="alert"), and the actions beside it
 * (Retry, "Choose another model…") are unchanged.
 *
 * Harness: the v1.312.0 recovery harness (real page, transport mocked),
 * copied so this file stands on its own.
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
  }
  type Approval = {
    id: string;
    callId: string;
    tool: string;
    args?: Record<string, unknown>;
  };
  const stream = {
    bodies: [] as Record<string, unknown>[],
    streaming: false,
    approval: null as Approval | null,
    aborts: 0,
    version: 0,
    listeners: new Set<() => void>(),
    settle: null as null | { ok: (r: Record<string, unknown>) => void; fail: (e: unknown) => void },
    bump() {
      stream.version += 1;
      for (const l of [...stream.listeners]) l();
    },
    /** End the pending turn with a reply. */
    release(r: Record<string, unknown>) {
      const s = stream.settle;
      stream.settle = null;
      stream.streaming = false;
      stream.approval = null;
      stream.bump();
      s?.ok(r);
    },
    /** End the pending turn with an error frame. */
    fail(e: unknown) {
      const s = stream.settle;
      stream.settle = null;
      stream.streaming = false;
      stream.approval = null;
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
    H.stream.aborts += 1;
    // The real hook: an abort ends the generator, so `run` resolves with
    // whatever streamed (nothing, here).
    if (H.stream.settle) H.stream.release({ reply: "" });
    else {
      H.stream.streaming = false;
      H.stream.approval = null;
      H.stream.bump();
    }
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

const SIX = ["read_file", "write_file", "list_dir", "web_search", "web_fetch", "calculator"];
const SHELL_ASK = { id: "apr_1", callId: "c1", tool: "shell", args: { command: "git status" } };

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
  H.stream.approval = null;
  H.stream.aborts = 0;
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

/** Open saved thread t7 with this setup (the ?thread= deep link). */
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

async function startTurn(text: string): Promise<Record<string, unknown>> {
  const n = H.stream.bodies.length;
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(H.stream.bodies.length).toBe(n + 1));
  await waitFor(() => expect(H.stream.settle).not.toBeNull());
  return H.stream.bodies[n];
}

/** The turn pauses on an ask-tier tool: the card appears. */
async function askFor(approval: typeof SHELL_ASK) {
  act(() => {
    H.stream.approval = approval;
    H.stream.bump();
  });
  return screen.findByTestId("chat-approval-card");
}

async function allowForConversation() {
  fireEvent.click(await screen.findByRole("button", { name: /allow for this conversation/i }));
  await waitFor(() =>
    expect(H.api.posts.some((p) => p.path === `/chat/approvals/${SHELL_ASK.id}`)).toBe(true),
  );
}

async function endTurn(reply = "done") {
  act(() => H.stream.release({ reply }));
  await screen.findByText(reply);
}

const toolsOf = (b: Record<string, unknown>) => (b.tools as string[] | undefined) ?? [];
const grantsOf = (b: Record<string, unknown>) => (b.granted_tools as string[] | undefined) ?? [];
const lastSetup = () => {
  const saves = H.api.puts.filter((p) => p.path.startsWith("/chat/threads/") && p.body.setup);
  return (saves.at(-1)?.body.setup ?? {}) as Record<string, unknown>;
};


/* ======================================================================= */
// The router's refusal, exactly as v1.329.0 words it for a labelled endpoint.
const REFUSAL =
  "Office Spark isn't connected right now, so this turn was not answered. No substitute was used on purpose. A stand-in answer would look like real work that never happened. Bring that endpoint back up, or pick another model for this chat, and retry.";

async function failTurn(detail: string, status = 503) {
  await startTurn("hello there");
  act(() => H.stream.fail(new H.FakeStreamError(detail, status)));
  await waitFor(() => expect(screen.queryAllByRole("button", { name: /^Retry/ }).length).toBeGreaterThan(0));
}

describe("calm chat J1 — a refused turn is ONE calm line above the composer", () => {
  it("shows the router's words as a single tone-danger alert line, not a bordered box", async () => {
    render(<ChatPage />);
    await failTurn(REFUSAL);
    const line = await screen.findByTestId("chat-turn-error");
    expect(line).toHaveTextContent(REFUSAL);
    expect(line).toHaveAttribute("role", "alert");
    expect(line.tagName).toBe("P");
    const cls = line.className;
    expect(cls).toContain("text-tone-danger");
    // The old ErrorNote card: a border, a tinted fill, a rounded box, an icon.
    expect(cls).not.toMatch(/\bborder\b|border-tone-danger|bg-tone-danger|rounded-xl/);
    expect(line.querySelector("svg")).toBeNull();
    // Nothing else on the page draws the danger-tinted card for this turn.
    expect(document.querySelector('[class*="border-tone-danger"]')).toBeNull();
    // Whole-pixel type, plain words (no dash aside reaches the line).
    expect(cls).toContain("text-[13px]");
    expect(line.textContent).not.toContain("—");
  });

  it("keeps every action the box offered: Retry re-sends the turn", async () => {
    render(<ChatPage />);
    await failTurn(REFUSAL);
    expect(H.stream.bodies).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /^Retry$/ }));
    await waitFor(() => expect(H.stream.bodies).toHaveLength(2));
  });

  it("keeps 'Choose another model…' beside it when the default is known down", async () => {
    resetHealth({ defaultProvider: "fleet-fa2-anth", byProvider: { "fleet-fa2-anth": false } });
    render(<ChatPage />);
    await failTurn(REFUSAL);
    expect(await screen.findByTestId("chat-turn-error")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Choose another model/ })).toBeInTheDocument();
  });
});
