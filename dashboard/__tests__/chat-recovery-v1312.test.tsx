/**
 * Wave 4 (RECOVERY), v1.312.0 — the chat page always leaves a way forward.
 *
 * Plain words, one behaviour per pin:
 *
 *  W4-2  "Allow for this conversation" is a GRANT the page remembers on its
 *        own (thread setup `granted_tools`, sent on every turn as
 *        `granted_tools`). It still ARMS the tool when there is room — that is
 *        what keeps the tool available next turn — and when the 6-tool cap
 *        blocks arming it SAYS so in plain words instead of doing nothing.
 *        A grant never arms a tool by itself.
 *
 *  W4-3  Stop reaches a turn that is still PREPARING: the page's Stop also
 *        POSTs /chat/turns/{turn_id}/stop (best-effort — a failed POST changes
 *        nothing the user sees; the local stop already happened).
 *
 *  Retry (chat-retry-only-when-default-down, per its fix_adjustment):
 *        while the effective provider (pick, else default) is in cooldown,
 *        Retry is disabled and reads "Retry in Ns", counting down; when that
 *        provider is KNOWN down, "Choose another model…" opens the existing
 *        model menu. The page suggests no model and never switches.
 *
 * Harness: the v1.278.0 steer harness (real page, transport mocked), with a
 * stream mock that re-renders the page when the test changes its state
 * (`streaming`, `approval`) — the page reads those fields from the hook, so a
 * frozen object could never show a mid-turn approval card.
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
import * as fallback from "@/lib/providerFallback";

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
describe("W4-2 — 'Allow for this conversation' is remembered as a grant", () => {
  it("at the 6-tool cap it still records the grant, sends it next turn, and tells the user in plain words", async () => {
    storeThread({ tools: SIX });
    render(<ChatPage />);
    await screen.findByText("yo");
    const first = await startTurn("check the repo");
    expect(toolsOf(first)).toEqual(SIX); // the thread really is at the cap
    await askFor(SHELL_ASK);
    await allowForConversation();

    // Plain words, not a silent no-op: granted, but not kept armed.
    const note = await screen.findByTestId("grant-cap-note");
    expect(note.textContent).toMatch(/allowed for this conversation/i);
    expect(note.textContent).toMatch(/add it from \+ to keep it available/i);

    await endTurn();
    // The grant is persisted with the thread setup …
    await waitFor(() => expect(lastSetup().granted_tools).toEqual(["shell"]));
    // … and rides the next turn, uncapped, WITHOUT bumping an armed tool.
    const next = await startTurn("and now the log");
    expect(grantsOf(next)).toEqual(["shell"]);
    expect(toolsOf(next)).toEqual(SIX);
  });

  it("with room it keeps arming the tool AND records the grant — and says nothing about the cap", async () => {
    render(<ChatPage />);
    await startTurn("what changed?");
    await askFor(SHELL_ASK);
    await allowForConversation();
    await endTurn();
    const next = await startTurn("again please");
    // Arming kept (that is what keeps shell available next turn) …
    expect(toolsOf(next)).toContain("shell");
    // … and the grant rides alongside it.
    expect(grantsOf(next)).toEqual(["shell"]);
    // Control: the cap note is for the cap only.
    expect(screen.queryByTestId("grant-cap-note")).toBeNull();
  });

  it("a reopened thread sends its saved grants on EVERY turn, and a grant never arms the tool", async () => {
    storeThread({ tools: [], granted_tools: ["shell"] });
    render(<ChatPage />);
    await screen.findByText("yo");
    const a = await startTurn("one");
    expect(grantsOf(a)).toEqual(["shell"]);
    expect(toolsOf(a)).not.toContain("shell"); // a grant is not an arming
    await endTurn("first");
    const b = await startTurn("two");
    expect(grantsOf(b)).toEqual(["shell"]);
    expect(toolsOf(b)).not.toContain("shell");
  });

  it("control: 'Allow once' records no grant", async () => {
    render(<ChatPage />);
    await startTurn("what changed?");
    await askFor(SHELL_ASK);
    fireEvent.click(await screen.findByRole("button", { name: /allow once/i }));
    await waitFor(() =>
      expect(H.api.posts.some((p) => p.path === `/chat/approvals/${SHELL_ASK.id}`)).toBe(true),
    );
    await endTurn();
    const next = await startTurn("again please");
    expect(grantsOf(next)).toEqual([]);
    expect(toolsOf(next)).not.toContain("shell");
  });
});

/* ======================================================================= */
describe("W4-3 — Stop reaches a turn that is still preparing", () => {
  it("Stop POSTs /chat/turns/{turn_id}/stop for the named turn; a failed POST changes nothing the user sees", async () => {
    render(<ChatPage />);
    const body = await startTurn("summarize the scanned pdf");
    const turnId = body.turn_id as string;
    expect(typeof turnId).toBe("string");
    const stopPath = `/chat/turns/${encodeURIComponent(turnId)}/stop`;
    // Best-effort: the daemon may answer 404 (the turn already ended) or be gone.
    H.api.postResponses[stopPath] = new H.FakeApiError("no such turn", 404);

    fireEvent.click(await screen.findByTitle("Stop this turn"));

    await waitFor(() => expect(H.api.posts.filter((p) => p.path === stopPath)).toHaveLength(1));
    // The local stop happened regardless (the stream was aborted) …
    expect(H.stream.aborts).toBeGreaterThan(0);
    // … the turn ended the way a Stop always has …
    expect(await screen.findByText("Stopped.")).toBeInTheDocument();
    // … and the rejected best-effort POST surfaced nothing.
    expect(screen.queryByText(/no such turn/)).toBeNull();
    // Control: exactly one stop POST, and no steer was sent.
    expect(H.api.posts.filter((p) => /\/chat\/turns\/.+\/(stop|steer)$/.test(p.path))).toHaveLength(1);
  });
});

/* ======================================================================= */
describe("chat-retry-only-when-default-down — Retry tells the truth, the user picks", () => {
  async function failTurn(detail: string, status = 503) {
    await startTurn("hello there");
    act(() => H.stream.fail(new H.FakeStreamError(detail, status)));
    // The failed-turn row is up.
    await waitFor(() => expect(screen.queryAllByRole("button", { name: /^Retry/ }).length).toBeGreaterThan(0));
  }
  const retryIn = () => screen.queryByRole("button", { name: /^Retry in \d+s$/ });
  const secondsOf = (el: HTMLElement) => Number(/Retry in (\d+)s/.exec(el.textContent ?? "")?.[1]);

  it("while the default is in cooldown Retry is disabled and counts down 'Retry in Ns'", async () => {
    resetHealth({
      defaultProvider: "claude-cli",
      byProvider: { "claude-cli": true },
      cooldownByProvider: { "claude-cli": 30 },
    });
    render(<ChatPage />);
    await failTurn("claude-cli is in cooldown, retry in 30 s");
    const btn = await waitFor(() => {
      const b = retryIn();
      expect(b).not.toBeNull();
      return b as HTMLElement;
    });
    expect(btn).toBeDisabled();
    const s0 = secondsOf(btn);
    expect(s0).toBeGreaterThanOrEqual(28);
    expect(s0).toBeLessThanOrEqual(30);
    // It COUNTS DOWN on its own between health polls (the mock never changes).
    await waitFor(() => expect(secondsOf(retryIn() as HTMLElement)).toBeLessThan(s0), { timeout: 4_000 });
    // A press on a disabled Retry starts nothing.
    fireEvent.click(retryIn() as HTMLElement);
    expect(H.stream.bodies).toHaveLength(1);
  });

  it("when the countdown runs out, Retry comes back enabled", async () => {
    resetHealth({
      defaultProvider: "claude-cli",
      byProvider: { "claude-cli": true },
      cooldownByProvider: { "claude-cli": 1 },
    });
    render(<ChatPage />);
    await failTurn("claude-cli is in cooldown, retry in 1 s");
    // Red-for-the-right-reason anchor: the cooldown state is shown first.
    expect(retryIn()).not.toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: /^Retry$/ })).toBeEnabled(), {
      timeout: 4_000,
    });
  });

  it("when the default is KNOWN down, 'Choose another model…' opens the model menu — no model suggested, nothing sent", async () => {
    resetHealth({ defaultProvider: "custom", byProvider: { custom: false } });
    render(<ChatPage />);
    await failTurn("custom is not reachable — pick another model for this chat, and retry.");
    expect(screen.queryByTestId("model-menu")).toBeNull();
    fireEvent.click(await screen.findByRole("button", { name: /^Choose another model…$/ }));
    expect(await screen.findByTestId("model-menu")).toBeInTheDocument();
    // The page never switches or re-sends on its own …
    expect(H.stream.bodies).toHaveLength(1);
    // … and offers no model by name (no "Retry on <Model>" chips).
    expect(screen.queryByRole("button", { name: /retry on/i })).toBeNull();
  });

  it("the effective provider is the PICK when there is one: a down pick offers both remedies", async () => {
    storeThread({ tools: [], provider: "openai", model: "gpt-5" });
    resetHealth({ defaultProvider: "custom", byProvider: { openai: false, custom: true } });
    render(<ChatPage />);
    await screen.findByText("yo");
    await failTurn("openai is not reachable");
    expect(screen.getByRole("button", { name: "Retry with the default model" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Choose another model…$/ })).toBeInTheDocument();
  });

  it("control: unknown health leaves the plain Retry, enabled, and no picker button", async () => {
    render(<ChatPage />);
    await failTurn("something went wrong", 500);
    expect(screen.getByRole("button", { name: /^Retry$/ })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Choose another model/ })).toBeNull();
    expect(retryIn()).toBeNull();
  });

  it("control: a reachable pick with a down default offers no picker button (the pick is what failed over nothing)", async () => {
    storeThread({ tools: [], provider: "openai", model: "gpt-5" });
    resetHealth({ defaultProvider: "custom", byProvider: { openai: true, custom: false } });
    render(<ChatPage />);
    await screen.findByText("yo");
    await failTurn("openai had a hiccup", 500);
    expect(screen.queryByRole("button", { name: /Choose another model/ })).toBeNull();
  });
});

/* ======================================================================= */
describe("providerFallback — the pure cooldown / known-down predicate", () => {
  type Trouble = { provider: string; down: boolean; cooldownS: number };
  const trouble = (fallback as unknown as {
    providerTrouble?: (
      choice: string,
      h: { byProvider: Record<string, boolean>; defaultProvider: string; cooldownByProvider?: Record<string, number> },
    ) => Trouble;
  }).providerTrouble;

  it("reads the pick, else the default; down only when KNOWN down; cooldown only when positive", () => {
    expect(typeof trouble).toBe("function");
    const t = trouble!;
    expect(t("", { byProvider: { custom: false }, defaultProvider: "custom" })).toEqual({
      provider: "custom",
      down: true,
      cooldownS: 0,
    });
    expect(
      t("openai::gpt-5", {
        byProvider: { openai: true, custom: false },
        defaultProvider: "custom",
        cooldownByProvider: { openai: 12 },
      }),
    ).toEqual({ provider: "openai", down: false, cooldownS: 12 });
    // Unknown is not down; an absent cooldown map is no cooldown.
    expect(t("", { byProvider: {}, defaultProvider: "custom" })).toEqual({
      provider: "custom",
      down: false,
      cooldownS: 0,
    });
    // Nothing known at all: no provider, nothing to say.
    expect(t("", { byProvider: {}, defaultProvider: "" })).toEqual({ provider: "", down: false, cooldownS: 0 });
  });
});
