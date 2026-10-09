/**
 * Calm chat W1-6 (v1.326.0) — on the real chat page, a question for the user
 * takes the composer's place.
 *
 *  - The approval card (and an app's open question) is drawn in the DOCK,
 *    not in the live reply bubble.
 *  - The composer is hidden (and inert) underneath, never unmounted: the
 *    draft typed before the question is exactly there when it is answered.
 *  - An app question that ended stays in the reply as one quiet line.
 *  - The keys line under the card says what Enter and Esc do.
 *  - Stop is still reachable from the dock.
 *
 * Harness: the v1.312.0 recovery harness (real page, transport mocked, a
 * stream mock the test can change mid-turn).
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
  const stream = {
    bodies: [] as Record<string, unknown>[],
    streaming: false,
    approval: null as null | { id: string; callId: string; tool: string; args?: Record<string, unknown> },
    mcpAsks: [] as unknown[],
    aborts: 0,
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
      stream.approval = null;
      stream.mcpAsks = [];
      stream.bump();
      s?.ok(r);
    },
  };
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      posts: [] as { path: string; body: Record<string, unknown> | undefined }[],
      getResponses: {} as Record<string, unknown>,
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
    return {};
  },
  put: async (path: string) => {
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
        mcpAsks: H.stream.mcpAsks,
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

const SHELL_ASK = { id: "apr_1", callId: "c1", tool: "shell", args: { command: "git status" } };
const CITY_ASK = {
  kind: "elicitation",
  id: "e1",
  callId: "c9",
  pack: "Weather",
  message: "Which city?",
  fields: [{ name: "city", type: "string", title: "City", description: "", required: true }],
};

beforeEach(() => {
  H.api.posts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.streaming = false;
  H.stream.approval = null;
  H.stream.mcpAsks = [];
  H.stream.aborts = 0;
  H.stream.settle = null;
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
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(H.stream.settle).not.toBeNull());
}

const composerBox = () => screen.getByTestId("chat-composer").querySelector("textarea") as HTMLTextAreaElement;

describe("W1-6 — a question for you takes the composer's place", () => {
  it("the approval is drawn in the dock (not the reply), the composer hides under it, and the draft survives", async () => {
    render(<ChatPage />);
    await startTurn("check the repo");
    // The user starts typing their next thought while the reply runs.
    fireEvent.change(composerBox(), { target: { value: "and then the log" } });

    act(() => {
      H.stream.approval = SHELL_ASK;
      H.stream.bump();
    });
    const card = await screen.findByTestId("chat-approval-card");
    expect(card.closest('[data-testid="chat-dock"]'), "in the dock").not.toBeNull();
    expect(card.closest('[data-testid="dock-ask"]')).not.toBeNull();
    const composer = screen.getByTestId("chat-composer");
    expect(composer.className.split(/\s+/)).toContain("hidden");
    expect(composer.hasAttribute("inert")).toBe(true);
    // Still MOUNTED, with the draft.
    expect(composerBox().value).toBe("and then the log");
    // The keys line says what the keys do now.
    expect(screen.getByTestId("composer-meta").textContent).toContain("Enter to allow · Esc to decline");

    // Answered (the daemon's resolve clears it): the composer is back, draft intact.
    act(() => {
      H.stream.approval = null;
      H.stream.bump();
    });
    await waitFor(() => expect(screen.queryByTestId("dock-ask")).toBeNull());
    expect(screen.getByTestId("chat-composer").className.split(/\s+/)).not.toContain("hidden");
    expect(screen.getByTestId("chat-composer").hasAttribute("inert")).toBe(false);
    expect(composerBox().value).toBe("and then the log");
  });

  it("an app's open question is in the dock; once it ends, the reply keeps one quiet line", async () => {
    render(<ChatPage />);
    await startTurn("what's the weather");
    act(() => {
      H.stream.mcpAsks = [CITY_ASK];
      H.stream.bump();
    });
    const card = await screen.findByTestId("mcp-elicitation-card");
    expect(card.closest('[data-testid="dock-ask"]')).not.toBeNull();
    expect(screen.getByTestId("composer-meta").textContent).toContain("Enter to send · Esc to decline");
    expect(screen.queryByTestId("mcp-ask-settled")).toBeNull();

    act(() => {
      H.stream.mcpAsks = [{ ...CITY_ASK, outcome: "decline" }];
      H.stream.bump();
    });
    await waitFor(() => expect(screen.queryByTestId("dock-ask")).toBeNull());
    expect(screen.queryByTestId("mcp-elicitation-card")).toBeNull();
    const line = screen.getByTestId("mcp-ask-settled");
    expect(line.textContent).toBe("You said no. Weather was told.");
    expect(line.closest('[data-testid="chat-dock"]')).toBeNull();
  });

  it("two waiting at once: one card with '1 of 2'", async () => {
    render(<ChatPage />);
    await startTurn("go");
    act(() => {
      H.stream.approval = SHELL_ASK;
      H.stream.mcpAsks = [CITY_ASK];
      H.stream.bump();
    });
    await waitFor(() => expect(screen.getByTestId("dock-ask-count").textContent).toBe("1 of 2"));
    expect(screen.getAllByRole("alertdialog")).toHaveLength(1);
  });

  it("Stop is reachable from the dock while the composer is hidden", async () => {
    render(<ChatPage />);
    await startTurn("go");
    act(() => {
      H.stream.approval = SHELL_ASK;
      H.stream.bump();
    });
    fireEvent.click(await screen.findByTestId("dock-ask-stop"));
    await waitFor(() => expect(H.stream.aborts).toBe(1));
  });
});
