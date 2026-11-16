/**
 * v1.510.0 (second review, L4) - a reply that FAILED after its steps ran
 * shows what it kept.
 *
 * Round 1 wrote two files; round 2's model call failed. The error frame now
 * carries `kept_note` (the daemon's sentence, from the journal), `tools_used`
 * and `documents`; the hook puts them (and the step cards) on the
 * StreamError. The page:
 *   - says the sentence on the error line, beside the headline;
 *   - keeps the failed reply, with its steps, in the transcript;
 *   - keeps the files the turn made in the files list beside the chat
 *     (before, the error path dropped them: only a finished turn listed them).
 * A failure before any step ran shows nothing new. Harness: the v1.499.3
 * steer-on-error suite's.
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
  class FakeStreamError extends FakeApiError {
    committed = true;
    offline = false;
    partial = "";
    title = "";
    fix = "";
    technical = "";
    fixKind = "";
    fixProvider = "";
    unreadSteers: string[] = [];
    keptNote = "";
    toolsUsed: string[] = [];
    documents: string[] = [];
    steps: { name: string; ok: boolean | null; ms: number | null; target?: string }[] = [];
  }
  return {
    FakeApiError,
    FakeStreamError,
    api: {
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
    stream: {
      bodies: [] as Record<string, unknown>[],
      release: null as null | ((r: Record<string, unknown>) => void),
      fail: null as null | ((e: unknown) => void),
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
  post: async () => ({}),
  put: async (path: string, body: Record<string, unknown>) => {
    H.api.puts.push({ path, body });
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
    run: (body: Record<string, unknown>) => {
      H.stream.bodies.push(body);
      return new Promise<Record<string, unknown>>((res, rej) => {
        H.stream.release = res;
        H.stream.fail = rej;
      });
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

const KEPT =
  "Before it failed, this reply made notes.md and changed plan.md. " +
  "They are kept, and each can be undone on the Activity page.";
const NOTES = "C:\\work\\notes.md";
const PLAN = "C:\\work\\plan.md";

beforeEach(() => {
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.release = null;
  H.stream.fail = null;
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
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function startTurn(text: string) {
  const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
  fireEvent.change(el, { target: { value: text } });
  fireEvent.keyDown(el, { key: "Enter" });
  await waitFor(() => expect(H.stream.bodies.length).toBe(1));
  await waitFor(() => expect(H.stream.fail).not.toBeNull());
}

function failure(kept: boolean) {
  const err = new H.FakeStreamError("the model server went away", 0);
  err.title = "Iron Jarvis couldn't finish this answer";
  err.fix = "Press Retry.";
  if (kept) {
    err.keptNote = KEPT;
    err.toolsUsed = ["write_document", "write_document"];
    err.documents = [NOTES, PLAN];
    err.steps = [
      { name: "write_document", ok: true, ms: 12, target: "notes.md" },
      { name: "write_document", ok: true, ms: 9, target: "plan.md" },
    ];
  }
  return err;
}

describe("a reply that failed after its steps ran shows what it kept (v1.510.0)", () => {
  it("says the sentence on the error line, keeps the reply with its steps and keeps the files", async () => {
    render(<ChatPage />);
    await startTurn("write the notes and update the plan");
    H.stream.fail!(failure(true));

    const line = await screen.findByTestId("chat-turn-error");
    expect(line.textContent).toContain("Iron Jarvis couldn't finish this answer");
    expect((await screen.findByTestId("chat-turn-kept")).textContent?.trim()).toBe(KEPT);

    // The failed reply stays in the transcript, with the steps it ran.
    const reply = await screen.findByTestId("reply");
    expect(reply.textContent).toContain(KEPT);
    expect(reply.querySelector('[data-testid="work-line"]')).not.toBeNull();

    // The files list beside the chat keeps both files.
    await waitFor(() => expect(screen.getByLabelText("Copy path to notes.md")).toBeTruthy());
    expect(screen.getByLabelText("Copy path to plan.md")).toBeTruthy();

    // ...and the saved chat keeps the reply's steps and files.
    await waitFor(() => {
      const saved = H.api.puts
        .map((p) => (p.body.messages as Record<string, unknown>[] | undefined) ?? [])
        .find((ms) => ms.some((m) => m.role === "assistant"));
      expect(saved).toBeTruthy();
      const m = saved!.find((x) => x.role === "assistant")!;
      expect(m.documents).toEqual([NOTES, PLAN]);
      expect(m.toolsUsed).toEqual(["write_document", "write_document"]);
      expect((m.steps as unknown[]).length).toBe(2);
    });
  });

  it("a failure before any step ran shows nothing new", async () => {
    render(<ChatPage />);
    await startTurn("hello");
    H.stream.fail!(failure(false));
    await screen.findByTestId("chat-turn-error");
    expect(screen.queryByTestId("chat-turn-kept")).toBeNull();
    expect(screen.queryByTestId("reply")).toBeNull();
    expect(screen.queryByLabelText("Copy path to notes.md")).toBeNull();
  });
});

describe("the error frame's kept facts reach the StreamError (v1.510.0)", () => {
  async function failOver(error: Record<string, unknown>) {
    const actual = await vi.importActual<typeof import("@/lib/useChatStream")>("@/lib/useChatStream");
    const { renderHook, act } = await import("@testing-library/react");
    const frames =
      `event: round\ndata: ${JSON.stringify({ round: 0 })}\n\n` +
      `event: error\ndata: ${JSON.stringify(error)}\n\n`;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(frames));
        controller.close();
      },
    });
    vi.stubGlobal("fetch", vi.fn(async () => new Response(stream, { status: 200 })));
    try {
      const { result } = renderHook(() => actual.useChatStream());
      let caught: unknown = null;
      await act(async () => {
        try {
          await result.current.run({ messages: [] });
        } catch (e) {
          caught = e;
        }
      });
      expect(caught).toBeInstanceOf(actual.StreamError);
      return caught as InstanceType<typeof actual.StreamError>;
    } finally {
      vi.unstubAllGlobals();
    }
  }

  it("carries the sentence, the steps that ran and the files, whitelisted", async () => {
    const e = await failOver({
      detail: "boom",
      kept_note: KEPT,
      tools_used: ["write_document", "", 3],
      documents: [NOTES, null, PLAN],
    });
    expect(e.keptNote).toBe(KEPT);
    expect(e.toolsUsed).toEqual(["write_document"]);
    expect(e.documents).toEqual([NOTES, PLAN]);
  });

  it("absent is empty", async () => {
    const e = await failOver({ detail: "boom" });
    expect(e.keptNote).toBe("");
    expect(e.toolsUsed).toEqual([]);
    expect(e.documents).toEqual([]);
    expect(e.steps).toEqual([]);
  });
});
