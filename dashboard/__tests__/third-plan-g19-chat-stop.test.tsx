/**
 * Third north-star fix plan, group 19 (CS4) — the Chat page's Stop says what
 * really happened to the reply.
 *
 * Before: the page sent Stop, ignored the answer and saved "Stopped." at
 * once. A Stop that reached the daemon after the reply had FINISHED (or, before
 * group 19, before the turn was known) left "Stopped." saved over a reply the
 * daemon had finished — and that line, named after the turn, kept the
 * finished reply out of the chat for good.
 *
 * Now the line is written once the daemon has answered:
 *  - stopped, or kept for a reply not started yet (`pending`) → "Stopped.";
 *  - 404 with `finished: true` (the reply had already finished) → no
 *    "Stopped."; the page follows that turn once (a replay) and shows and
 *    saves the finished reply.
 *
 * Harness: the real page, transport mocked (the chat-recovery-v1312 harness),
 * with an ApiError that carries the daemon's JSON body as the real one does.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const H = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number;
    cancelled: boolean;
    body?: unknown;
    constructor(message: string, status = 500, cancelled = false, body?: unknown) {
      super(message);
      this.status = status;
      this.cancelled = cancelled;
      this.name = "ApiError";
      if (body !== undefined) this.body = body;
    }
  }
  class FakeStreamError extends FakeApiError {
    committed = false;
    offline = false;
    partial = "";
  }
  const stream = {
    bodies: [] as Record<string, unknown>[],
    opts: [] as (Record<string, unknown> | undefined)[],
    streaming: false,
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
      stream.bump();
      s?.ok(r);
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
  const run = (
    body: Record<string, unknown>,
    _onDelta?: unknown,
    _onReset?: unknown,
    opts?: Record<string, unknown>,
  ) => {
    H.stream.bodies.push(body);
    H.stream.opts.push(opts);
    H.stream.streaming = true;
    H.stream.bump();
    return new Promise<Record<string, unknown>>((ok, fail) => {
      H.stream.settle = { ok, fail };
    });
  };
  const abort = () => {
    H.stream.aborts += 1;
    if (H.stream.settle) H.stream.release({ reply: "" });
    else {
      H.stream.streaming = false;
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
        approval: null,
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
  useProviderHealth: () => ({
    byProvider: {}, cooldownByProvider: {}, signedOutByProvider: {}, defaultProvider: "",
    loading: false, stale: false, refresh: () => {},
  }),
}));
vi.mock("react-markdown", () => ({ default: ({ children }: { children?: string }) => <div>{children}</div> }));
vi.mock("remark-gfm", () => ({ default: () => {} }));

import ChatPage from "@/app/chat/page";

beforeEach(() => {
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.opts.length = 0;
  H.stream.streaming = false;
  H.stream.aborts = 0;
  H.stream.settle = null;
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

async function startTurn(text: string): Promise<string> {
  const box = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(H.stream.bodies.length).toBe(1));
  await waitFor(() => expect(H.stream.settle).not.toBeNull());
  return H.stream.bodies[0].turn_id as string;
}

/** Every assistant line any save carried. */
const savedReplies = () =>
  H.api.puts.flatMap((p) =>
    ((p.body.messages as { role?: string; content?: string }[] | undefined) ?? [])
      .filter((m) => m.role === "assistant")
      .map((m) => m.content),
  );

describe("CS4 — Stop and a reply that had already finished", () => {
  it("a 404 'finished' answer never saves 'Stopped.': the page follows the turn once and saves the finished reply", async () => {
    render(<ChatPage />);
    const turnId = await startTurn("explain the notes");
    const stopPath = `/chat/turns/${encodeURIComponent(turnId)}/stop`;
    H.api.postResponses[stopPath] = new H.FakeApiError("That reply had already finished.", 404, false, {
      detail: "That reply had already finished.",
      finished: true,
    });

    fireEvent.click(await screen.findByTitle("Stop this turn"));

    // The page follows THAT turn again (a replay) instead of writing "Stopped.".
    await waitFor(() => expect(H.stream.bodies.length).toBe(2));
    expect(H.stream.opts[1]?.rejoin).toBe(turnId);
    expect(H.stream.bodies[1].turn_id).toBe(turnId);
    await waitFor(() => expect(H.stream.settle).not.toBeNull());
    act(() => H.stream.release({ reply: "The finished reply." }));

    expect(await screen.findByText("The finished reply.")).toBeInTheDocument();
    await waitFor(() => expect(savedReplies()).toContain("The finished reply."));
    expect(screen.queryByText("Stopped.")).toBeNull();
    expect(savedReplies()).not.toContain("Stopped.");
  });

  it("a kept Stop ('pending': the reply had not started) behaves as stopped", async () => {
    render(<ChatPage />);
    const turnId = await startTurn("explain the notes");
    const stopPath = `/chat/turns/${encodeURIComponent(turnId)}/stop`;
    H.api.postResponses[stopPath] = { ok: true, stopped: true, pending: true };

    fireEvent.click(await screen.findByTitle("Stop this turn"));

    expect(await screen.findByText("Stopped.")).toBeInTheDocument();
    await waitFor(() => expect(savedReplies()).toContain("Stopped."));
    expect(H.stream.bodies.length).toBe(1); // nothing followed again
    expect(H.api.posts.filter((p) => p.path === stopPath)).toHaveLength(1);
  });

  it("control: a 404 from an older Iron Jarvis (no 'finished') still stops here", async () => {
    render(<ChatPage />);
    const turnId = await startTurn("explain the notes");
    const stopPath = `/chat/turns/${encodeURIComponent(turnId)}/stop`;
    H.api.postResponses[stopPath] = new H.FakeApiError("no such running turn", 404);

    fireEvent.click(await screen.findByTitle("Stop this turn"));

    expect(await screen.findByText("Stopped.")).toBeInTheDocument();
    expect(H.stream.bodies.length).toBe(1);
  });
});
