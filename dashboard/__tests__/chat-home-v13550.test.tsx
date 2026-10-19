/**
 * v1.355.0 (product-readiness G63, and the chat-page half of G32).
 *
 * G63: "Worked for …" appeared only when a reply used tools or thinking; a
 * plain answer showed no duration anywhere. Now WorkLine says "Answered in
 * 2.1 s" above a plain reply whose turn timing was stored (TurnReceipt's
 * secondsText, the folded line's own clock), on the same quiet line, with
 * nothing to open. ACCEPTANCE: a plain text reply shows "Answered in 2.1 s"
 * above it — driven here through the REAL chat page, both a reopened chat
 * and a fresh turn whose stream result carries the timing.
 *
 * G32 (chat page): the home line is drawn ON the chat page, which reads
 * `?ask=` once on mount; its "Ask Jarvis" therefore links to the OTHER
 * address of the same chat ("/chat" → "/?ask=…"), and the chat mounted at
 * "/" (app/page.tsx) fills the box with the question, never sending it.
 *
 * Harness: the chat page's own (work-line-v1326): lib/api, the stream hook
 * and the media hooks are doubles; the page, WorkLine and HomeLine are real.
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
  const stream = {
    bodies: [] as Record<string, unknown>[],
    replies: [] as string[],
    extra: [] as Record<string, unknown>[],
    hold: false,
    streaming: false,
    version: 0,
    live: { tools: [] as unknown[], thinking: "" },
    listeners: new Set<() => void>(),
    settle: null as null | ((r: Record<string, unknown>) => void),
    bump() {
      stream.version += 1;
      for (const l of [...stream.listeners]) l();
    },
  };
  return {
    FakeApiError,
    FakeStreamError,
    stream,
    api: {
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
    },
  };
});

vi.mock("@/lib/api", () => ({
  ApiError: H.FakeApiError,
  API_BASE: "http://127.0.0.1:8787",
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

vi.mock("@/lib/useChatStream", async () => {
  const React = await import("react");
  const subscribe = (cb: () => void) => {
    H.stream.listeners.add(cb);
    return () => {
      H.stream.listeners.delete(cb);
    };
  };
  const run = async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
    H.stream.bodies.push(body);
    H.stream.streaming = true;
    H.stream.bump();
    if (H.stream.hold) {
      return new Promise<Record<string, unknown>>((ok) => {
        H.stream.settle = ok;
      });
    }
    await new Promise<void>((r) => setTimeout(r, 0));
    const reply = H.stream.replies.shift() ?? "done";
    onDelta(reply, reply);
    H.stream.streaming = false;
    H.stream.bump();
    return { reply, ...(H.stream.extra.shift() ?? {}) };
  };
  const abort = () => {
    const s = H.stream.settle;
    H.stream.settle = null;
    H.stream.streaming = false;
    H.stream.bump();
    s?.({ reply: "" });
  };
  return {
    StreamError: H.FakeStreamError,
    useLiveText: (s: { text?: string }) => s?.text ?? "",
    useChatStream: () => {
      React.useSyncExternalStore(subscribe, () => H.stream.version);
      return {
        streaming: H.stream.streaming,
        text: "",
        thinking: H.stream.live.thinking,
        tools: H.stream.live.tools,
        mcpAsks: [],
        approval: null,
        phase: H.stream.streaming ? "working" : null,
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
    supported: true, enabled: false, speaking: false, enable: () => {}, disable: () => {},
    toggle: () => {}, speak: () => {}, resetStream: () => {}, speakMore: () => {}, cancel: () => {},
    readAloud: () => {}, readingKey: null,
  }),
}));
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", async () => await vi.importActual("react-markdown"));

import ChatPage from "@/app/chat/page";
import HomePage from "@/app/page";
import { WorkLine, workSummary } from "@/components/chat/WorkLine";
import { HEALTH_QUESTION } from "@/lib/loopWords";

const AT = "2026-10-19T08:20:00";
const ROUTE = { requested: "", provider: "claude-cli", model: "claude-opus-5-5", reason: "default" };
const PLAIN_TIMING = { startedAt: 1_000, firstTokenAt: 1_400, endedAt: 3_100 };

const THREAD = {
  id: "t1",
  title: "Capitals",
  messages: [
    { role: "user", content: "Capital of France?", at: AT },
    { role: "assistant", content: "Paris is the capital.", at: AT, route: ROUTE, timing: PLAIN_TIMING },
    { role: "user", content: "And an older one?", at: AT },
    { role: "assistant", content: "An answer saved before timing existed.", at: AT, route: ROUTE },
  ],
};

beforeEach(() => {
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
  H.stream.extra.length = 0;
  H.stream.hold = false;
  H.stream.streaming = false;
  H.stream.settle = null;
  H.stream.live = { tools: [], thinking: "" };
  H.api.getResponses = {
    "/models": { models: [{ provider: "claude-cli", model: "claude-opus-5-5", label: "Opus 5.5", available: true }] },
    "/chat/personas": { personas: [] },
    "/chat/threads": { threads: [] },
    "/chat/threads/t1": THREAD,
    "/settings": { settings: {} },
    "/projects": { projects: [] },
    "/agents/mentionable": { agents: [] },
    "/skills": { skills: [] },
    "/workflows": { workflows: [] },
    "/tools": { tools: [] },
    "/undo?session_id=chat": { actions: [] },
    "/chat/approvals/pending": { approvals: [] },
  };
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.history.replaceState({}, "", "/chat");
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const classes = (el: Element | null | undefined) => (el?.getAttribute("class") ?? "").split(/\s+/).filter(Boolean);

/* ------------------------------------------------------------------ G63 */

describe("a plain answer says how long it took (G63)", () => {
  it("the words: 'Answered in' + the receipt's own seconds; work keeps 'Worked for'", () => {
    expect(workSummary({ timing: PLAIN_TIMING })).toBe("Answered in 2.1 s");
    expect(workSummary({ timing: { startedAt: 0, firstTokenAt: null, endedAt: 65_000 } })).toBe(
      "Answered in 1 min 5 s",
    );
    // Tools or thinking: unchanged.
    expect(workSummary({ steps: [{ name: "read_file", ok: true, ms: 10 }], timing: PLAIN_TIMING })).toBe(
      "Worked for 2.1 s · read 1 file",
    );
    expect(workSummary({ thinking: "hmm", thinkingSeconds: 1, timing: PLAIN_TIMING })).toBe(
      "Worked for 2.1 s · thought for 1 s",
    );
    // No timing, or a timing that cannot be true: still nothing.
    expect(workSummary({})).toBeNull();
    expect(workSummary({ timing: { startedAt: 5_000, firstTokenAt: null, endedAt: 1_000 } })).toBeNull();
    expect(workSummary({ timing: { startedAt: Number.NaN, firstTokenAt: null, endedAt: 1_000 } })).toBeNull();
  });

  it("the line: the same quiet row and hairline, nothing to open (not a button, no chevron)", () => {
    render(<WorkLine timing={PLAIN_TIMING} />);
    const line = screen.getByTestId("work-line");
    expect(line.getAttribute("data-kind")).toBe("answered");
    expect(screen.getByTestId("work-summary").textContent).toBe("Answered in 2.1 s");
    expect(screen.queryByRole("button")).toBeNull();
    expect(line.querySelector("[aria-expanded]")).toBeNull();
    expect(screen.getByTestId("work-line-check")).toBeTruthy();
    // One icon only (the check): no chevron.
    expect(line.querySelectorAll("svg")).toHaveLength(1);
    const row = screen.getByTestId("work-summary").parentElement!;
    expect(classes(row)).toEqual(
      expect.arrayContaining(["border-b", "border-white/[0.08]", "text-[13px]", "text-zinc-500"]),
    );
    expect(classes(row).filter((c) => /^(bg-|rounded)/.test(c) || /^border-[0-9xytlr]/.test(c))).toEqual([]);
  });

  it("no timing: still no line at all", () => {
    const { container } = render(<WorkLine />);
    expect(container.innerHTML).toBe("");
  });

  it("on the chat page: a reopened plain reply shows 'Answered in 2.1 s' ABOVE it; an older one shows nothing", async () => {
    window.history.replaceState({}, "", "/chat?thread=t1");
    render(<ChatPage />);
    const text = await screen.findByText("Paris is the capital.");
    const reply = text.closest('[data-testid="reply"]') as HTMLElement;
    const work = within(reply).getByTestId("work-line");
    expect(within(work).getByTestId("work-summary").textContent).toBe("Answered in 2.1 s");
    expect(work.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const older = (await screen.findByText("An answer saved before timing existed.")).closest(
      '[data-testid="reply"]',
    ) as HTMLElement;
    expect(within(older).queryByTestId("work-line")).toBeNull();
  });

  it("on the chat page: a fresh plain turn's own timing reaches the line", async () => {
    render(<ChatPage />);
    const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    H.stream.replies.push("Rome is the capital.");
    H.stream.extra.push({ timing: { startedAt: 10_000, firstTokenAt: 10_600, endedAt: 12_100 } });
    fireEvent.change(el, { target: { value: "Capital of Italy?" } });
    fireEvent.keyDown(el, { key: "Enter" });
    // Wait for the line itself: the reply's text lands before its turn ends.
    await waitFor(() => {
      const text = screen.getByText("Rome is the capital.");
      const reply = text.closest('[data-testid="reply"]') as HTMLElement;
      expect(within(reply).getByTestId("work-summary").textContent).toBe("Answered in 2.1 s");
    });
  });
});

/* ------------------------------------------------- G32 on the chat page */

describe("the home line's Ask Jarvis lands in a chat with the question waiting (G32)", () => {
  it("drawn on /chat, it links to '/?ask=…' (the page reads ?ask= only when it mounts)", async () => {
    H.api.getResponses["/ui/status-line"] = {
      interrupted: 0,
      failing_loops: 1,
      failing_packs: 0,
      running: 0,
      short_schedules: 0,
    };
    render(<ChatPage />);
    const line = await screen.findByTestId("home-line");
    await waitFor(() =>
      expect(within(line).getByRole("link", { name: "Ask Jarvis" }).getAttribute("href")).toBe(
        `/?ask=${encodeURIComponent(HEALTH_QUESTION)}`,
      ),
    );
  });

  it("the chat at that address fills the box with the question and sends nothing", async () => {
    window.history.replaceState({}, "", `/?ask=${encodeURIComponent(HEALTH_QUESTION)}`);
    render(<HomePage />);
    const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    await waitFor(() => expect(el.value).toBe(HEALTH_QUESTION));
    expect(H.stream.bodies).toHaveLength(0);
  });
});
