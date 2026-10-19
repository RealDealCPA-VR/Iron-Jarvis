/**
 * v1.355.0 lane 5A on the REAL chat page (transport mocked, the v1.312.0
 * recovery harness wave4-seams-v13540 uses):
 *
 *  - G29: a failed turn's line is the headline with the press that fixes it
 *    beside it — "Claude Code isn't signed in" + "Sign in to Claude Code"
 *    (which opens the sign-in pane), the server's words folded under
 *    Details, Retry unchanged, and "Your message is back in the box." A
 *    refused key gets "Update the key in Connections". With no press, the
 *    line is the v1.354.0 sentence exactly.
 *  - G55 / G64: a reply whose turn left messages out, or carried approvals,
 *    shows them on its receipt and keeps them on the saved message.
 *  - G62: a remote's "still working" line shows "· waiting 3 min" until a
 *    later line from it arrives; that answer carries "from hermes · …";
 *    message-back off shows no wait.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
    daemon: { health: null as unknown, refreshes: 0 },
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
// The shared /health the Sign in press reads (the real hook, its rows set
// per test). Everything else in the module is the real one.
vi.mock("@/lib/daemon", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/daemon")>()),
  useDaemon: () => ({
    online: true,
    unauthorized: false,
    requestError: false,
    health: H.daemon.health,
    checking: false,
    epoch: 0,
    refresh: () => {
      H.daemon.refreshes += 1;
    },
    provided: false,
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
  useProviderHealth: () => H.health,
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.streaming = false;
  H.stream.settle = null;
  H.health.byProvider = {};
  H.health.cooldownByProvider = {};
  H.health.signedOutByProvider = {};
  H.health.defaultProvider = "";
  H.daemon.health = null;
  H.daemon.refreshes = 0;
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

async function startTurn(text: string) {
  const n = H.stream.bodies.length;
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(H.stream.bodies.length).toBe(n + 1));
  await waitFor(() => expect(H.stream.settle).not.toBeNull());
}

function failure(words: {
  title?: string;
  fix?: string;
  technical?: string;
  fixKind?: string;
  fixProvider?: string;
}) {
  const e = new H.FakeStreamError("the server's own detail", 0);
  Object.assign(e, words);
  return e;
}

async function failTurn(e: unknown) {
  await startTurn("hello there");
  act(() => H.stream.fail(e));
  await waitFor(() => expect(screen.queryAllByRole("button", { name: /^Retry/ }).length).toBeGreaterThan(0));
}

const SIGNED_OUT = {
  title: "Claude Code isn't signed in",
  fix: "Press Sign in beside Claude Code in Settings › Connections, then retry.",
  technical: "Not logged in · Please run /login",
};

// --------------------------------------------------------------------------- //
// G29
// --------------------------------------------------------------------------- //
describe("G29: the failure line has a headline, folded details and the fix press", () => {
  it("a signed-out Claude Code: headline, Sign in that opens the pane, details folded, Retry works", async () => {
    H.api.postResponses["/terminals/launch"] = { id: "pane_9" };
    render(<ChatPage />);
    await failTurn(failure({ ...SIGNED_OUT, fixKind: "sign_in", fixProvider: "claude-cli" }));

    const line = await screen.findByTestId("chat-turn-error");
    expect(line.textContent).toBe("Claude Code isn't signed in");
    // The calm line is unchanged: one danger-tone alert, no box.
    expect(line).toHaveAttribute("role", "alert");
    expect(line.className).toContain("text-tone-danger");
    expect(line.className).not.toMatch(/border|rounded-xl/);

    // The details are FOLDED: a toggle, not the server's words.
    const toggle = screen.getByTestId("chat-turn-error-details-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("chat-turn-error-details")).toBeNull();
    expect(document.body.textContent).not.toContain("Please run /login");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("chat-turn-error-details").textContent).toBe(SIGNED_OUT.technical);

    // The message went back in the box, and the line says so.
    expect(screen.getByTestId("chat-turn-draft-back").textContent).toBe("Your message is back in the box.");
    expect((screen.getByPlaceholderText(/Message Iron Jarvis/) as HTMLTextAreaElement).value).toBe("hello there");

    // Sign in opens the sign-in pane (POST /terminals/launch on this PC's
    // own login), then says what to do there and links to it.
    fireEvent.click(screen.getByRole("button", { name: "Sign in to Claude Code" }));
    await waitFor(() =>
      expect(H.api.posts.find((p) => p.path === "/terminals/launch")?.body).toEqual({
        cli: "claude",
        account: "default",
      }),
    );
    const hint = await screen.findByTestId("chat-turn-sign-in-hint");
    expect(hint.textContent).toContain("A Build pane opened with Claude Code running.");
    expect(hint.textContent).toContain("/login");
    expect(within(hint).getByRole("link", { name: "Open the Build pane" })).toHaveAttribute(
      "href",
      "/terminals?focus=pane_9",
    );
    expect(H.daemon.refreshes).toBeGreaterThan(0);

    // Retry still works: it re-sends the turn.
    const before = H.stream.bodies.length;
    fireEvent.click(screen.getByRole("button", { name: /^Retry$/ }));
    await waitFor(() => expect(H.stream.bodies.length).toBe(before + 1));
  });

  it("with no typed press, /health saying the turn's CLI is signed out still offers Sign in", async () => {
    H.health.defaultProvider = "anthropic";
    H.daemon.health = {
      status: "ok",
      version: "1.355.0",
      default_provider: "anthropic",
      default_model: "",
      providers: [
        { provider: "anthropic", available: false, class: "api", inherited_from: "claude-cli" },
        { provider: "claude-cli", available: false, class: "cli", installed: true, signed_in: false },
      ],
    };
    render(<ChatPage />);
    await failTurn(failure({ title: "Claude Code didn't answer in time", fix: "Press Retry." }));
    expect(await screen.findByRole("button", { name: "Sign in to Claude Code" })).toBeTruthy();
    expect(screen.getByTestId("chat-turn-error").textContent).toBe("Claude Code didn't answer in time");
  });

  it("a CLI not checked yet (signed_in null) is never accused of being signed out", async () => {
    H.health.defaultProvider = "claude-cli";
    H.daemon.health = {
      status: "ok", version: "1.355.0", default_provider: "claude-cli", default_model: "",
      providers: [{ provider: "claude-cli", available: false, class: "cli", installed: true, signed_in: null }],
    };
    render(<ChatPage />);
    await failTurn(failure({ title: "Claude Code didn't answer in time", fix: "Press Retry." }));
    expect(screen.getByTestId("chat-turn-error").textContent).toBe(
      "Claude Code didn't answer in time. Press Retry.",
    );
    expect(screen.queryByTestId("chat-turn-sign-in")).toBeNull();
  });

  it("a refused key links to where keys live, beside the headline", async () => {
    render(<ChatPage />);
    await failTurn(
      failure({
        title: "OpenRouter refused your key",
        fix: "Update it in Settings › Connections.",
        technical: "openrouter API error 401: No auth credentials found",
        fixKind: "key",
        fixProvider: "openrouter",
      }),
    );
    expect(screen.getByTestId("chat-turn-error").textContent).toBe("OpenRouter refused your key");
    const link = screen.getByTestId("chat-turn-key-link");
    expect(link.textContent).toBe("Update the key in Connections");
    expect(link).toHaveAttribute("href", "/connections");
    expect(screen.queryByTestId("chat-turn-sign-in")).toBeNull();
  });

  it("with no press the line is the v1.354.0 sentence, exactly", async () => {
    render(<ChatPage />);
    await failTurn(failure({ title: "OpenRouter is busy", fix: "Try again shortly, or pick another model for this chat." }));
    expect(screen.getByTestId("chat-turn-error").textContent).toBe(
      "OpenRouter is busy. Try again shortly, or pick another model for this chat.",
    );
    expect(screen.queryByTestId("chat-turn-sign-in")).toBeNull();
    expect(screen.queryByTestId("chat-turn-key-link")).toBeNull();
    // No technical text, no Details toggle.
    expect(screen.queryByTestId("chat-turn-error-details-toggle")).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// G55 + G64 on the page
// --------------------------------------------------------------------------- //
describe("G55 + G64: the reply keeps what was left out and what was approved", () => {
  it("the reply's receipt says it, and the saved message carries both", async () => {
    render(<ChatPage />);
    await startTurn("summarise the whole thread");
    act(() =>
      H.stream.release({
        reply: "Here is the summary.",
        route: { requested: "", provider: "claude-cli", model: "claude-opus-5-5", reason: "default" },
        toolsUsed: [],
        refit: { dropped: 12 },
        approved: [
          { tool: "write_file", decision: "once" },
          { tool: "shell", decision: "conversation" },
        ],
      }),
    );
    await screen.findByText("Here is the summary.");
    expect((await screen.findByTestId("turn-refit")).textContent).toBe(
      "Left out 12 older messages to fit this model",
    );
    fireEvent.click(screen.getByTestId("turn-receipt"));
    expect(screen.getAllByTestId("turn-approved").map((r) => r.textContent)).toEqual([
      "You approved: save a file (once)",
      "You approved: run a command (for this chat)",
    ]);
    await waitFor(() => {
      const saved = H.api.puts.at(-1)?.body.messages as Record<string, unknown>[] | undefined;
      const reply = saved?.at(-1);
      expect(reply?.refit).toEqual({ dropped: 12 });
      expect(reply?.approved).toEqual([
        { tool: "write_file", decision: "once" },
        { tool: "shell", decision: "conversation" },
      ]);
    });
  });
});

// --------------------------------------------------------------------------- //
// G62 on the page
// --------------------------------------------------------------------------- //
function openThread(messages: Record<string, unknown>[]) {
  H.api.getResponses["/chat/threads/t7"] = { id: "t7", title: "saved", messages, setup: {} };
  window.history.replaceState({}, "", "/chat?thread=t7");
}

describe("G62: a remote's reply has a receipt line and a pending state", () => {
  it("an accepted @remote task shows 'waiting 3 min' until its answer arrives", async () => {
    const asked = new Date(Date.now() - 3 * 60_000 - 5_000).toISOString();
    openThread([
      { role: "user", content: "@hermes research the lease", at: asked },
      {
        role: "assistant",
        content: "hermes has the task and will report back here when it has something.",
        panelWho: "remote:hermes",
        panelKind: "progress",
        at: asked,
      },
    ]);
    render(<ChatPage />);
    const line = await screen.findByTestId("panel-progress");
    expect(within(line).getByTestId("panel-waiting").textContent).toBe("· waiting 3 min");
  });

  it("once the answer is in, the wait is gone and the answer says who and how long", async () => {
    const t0 = Date.now() - 60 * 60_000;
    const at = (ms: number) => new Date(t0 + ms).toISOString();
    openThread([
      { role: "user", content: "@hermes research the lease", at: at(0) },
      {
        role: "assistant",
        content: "hermes has the task and will report back here when it has something.",
        panelWho: "remote:hermes",
        panelKind: "progress",
        at: at(2_000),
      },
      {
        role: "assistant",
        content: "The lease ends in May.",
        panelWho: "remote:hermes",
        panelKind: "done",
        panelAt: at(372_000),
      },
    ]);
    render(<ChatPage />);
    await screen.findByText("The lease ends in May.");
    expect(screen.queryByTestId("panel-waiting")).toBeNull();
    expect(screen.getByTestId("panel-receipt").textContent).toBe("from hermes · 6 min 12 s");
  });

  it("an older daemon's round, composed by the page, waits on the remote the same way", async () => {
    H.api.getResponses["/agents/mentionable"] = {
      agents: [
        { mention: "hermes", name: "remote:hermes", kind: "remote", source: "remote", description: "Research", healthy: true, delegable: false },
      ],
    };
    const took = new Date(Date.now() - 3 * 60_000 - 5_000).toISOString();
    H.api.postResponses["/chat/panel"] = {
      mode: "panel",
      thread_id: "room1",
      spoke: ["remote:hermes"],
      entries: [
        { who: "user", content: "@hermes research the lease" },
        {
          who: "remote:hermes",
          content: "hermes has the task and will report back here when it has something.",
          pending: true,
          at: took,
        },
      ],
    };
    render(<ChatPage />);
    const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "@he" } });
    await screen.findByRole("option", { name: /hermes/ });
    fireEvent.change(box, { target: { value: "@hermes research the lease" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(H.api.posts.some((p) => p.path === "/chat/panel")).toBe(true));
    const line = await screen.findByTestId("panel-progress");
    expect(within(line).getByTestId("panel-waiting").textContent).toBe("· waiting 3 min");
  });

  it("a remote with message-back off says its answer will not come back, and never waits", async () => {
    openThread([
      { role: "user", content: "@hermes research the lease", at: new Date().toISOString() },
      {
        role: "assistant",
        content:
          "hermes took the task. Its answer will not come back here; turn on Let it message back on its card to get it in this chat.",
        panelWho: "remote:hermes",
        panelKind: "progress",
        at: new Date().toISOString(),
      },
    ]);
    render(<ChatPage />);
    const line = await screen.findByTestId("panel-progress");
    expect(line.textContent).toContain("Its answer will not come back here");
    // Said in full (it wraps), never cut off mid-sentence.
    expect(line.textContent).toContain("Let it message back");
    expect(screen.queryByTestId("panel-waiting")).toBeNull();
  });
});
