/**
 * v1.468.0: the chat PAGE sends what is in front in Excel only while the
 * user switched "Use it in this chat" on. Driven through the real page (the
 * transport mocked, as in wave-d-page-v1325): the body of the message that
 * goes to the daemon is what is asserted.
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
    replies: [] as string[],
    extra: [] as Record<string, unknown>[],
    hold: false,
    reject: null as Error | null,
    streaming: false,
    version: 0,
    live: { tools: [] as unknown[], mcpAsks: [] as unknown[] },
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
      posts: [] as { path: string; body: Record<string, unknown> }[],
      puts: [] as { path: string; body: Record<string, unknown> }[],
      getResponses: {} as Record<string, unknown>,
      postResponses: {} as Record<string, unknown>,
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
  post: async (path: string, body: Record<string, unknown>) => {
    H.api.posts.push({ path, body });
    const r = H.api.postResponses[path];
    if (r instanceof Error) throw r;
    if (typeof r === "function") return (r as (b: Record<string, unknown>) => unknown)(body);
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
  const run = async (body: Record<string, unknown>, onDelta: (d: string, f: string) => void) => {
    H.stream.bodies.push(body);
    if (H.stream.reject) throw H.stream.reject;
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
        tools: H.stream.live.tools,
        mcpAsks: H.stream.live.mcpAsks,
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
vi.mock("@/lib/useProviderHealth", () => ({
  useProviderHealth: () => ({ byProvider: {}, defaultProvider: "", loading: false, stale: false, refresh: () => {} }),
}));
vi.mock("react-markdown", async () => await vi.importActual("react-markdown"));

vi.mock("@/lib/daemon", async () => {
  const actual = await vi.importActual<typeof import("@/lib/daemon")>("@/lib/daemon");
  return {
    ...actual,
    useDaemon: () => ({
      online: true, unauthorized: false, requestError: false, checking: false, starting: false,
      epoch: 0, refresh: () => {}, provided: true,
      health: { status: "ok", version: "1.468.0", default_provider: "", default_model: "", providers: [], excel_open: X.excelOpen },
    }),
  };
});

import ChatPage from "@/app/chat/page";
import { resetExcelNowForTests } from "@/lib/excelNow";

const X = vi.hoisted(() => ({ excelOpen: true }));

beforeEach(() => {
  X.excelOpen = true;
  resetExcelNowForTests();
  H.api.posts.length = 0;
  H.api.puts.length = 0;
  H.stream.bodies.length = 0;
  H.stream.replies.length = 0;
  H.stream.extra.length = 0;
  H.stream.hold = false;
  H.stream.reject = null;
  H.stream.streaming = false;
  H.stream.settle = null;
  H.stream.live = { tools: [], mcpAsks: [] };
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
    "/mcp/prompts": { prompts: [], failed: [] },
    "/mcp/resources": { resources: [], failed: [] },
    "/excel/live/now": { open: true, workbook: "Budget.xlsx", sheet: "Sales", selection: "B4:B9", cell: "B4", unsaved: false, others: 0 },
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

async function box() {
  return (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
}

async function send(el: HTMLTextAreaElement, text: string) {
  fireEvent.change(el, { target: { value: text, selectionStart: text.length, selectionEnd: text.length } });
  fireEvent.keyDown(el, { key: "Enter" });
}

async function answered(text: string | RegExp) {
  await screen.findByText(text);
  await waitFor(() => expect(screen.queryByPlaceholderText(/Steer Jarvis mid-turn/)).toBeNull());
}

describe("what is in front in Excel rides a message only when switched on", () => {
  it("sends nothing about Excel until the user presses Use it in this chat, and nothing again after Stop", async () => {
    H.stream.replies.push("first answer", "second answer", "third answer");
    render(<ChatPage />);
    const el = await box();
    const strip = await screen.findByTestId("excel-now-strip");
    expect(strip).toHaveAttribute("data-on", "false");

    await send(el, "what is this?");
    await answered("first answer");
    expect("excel_now" in H.stream.bodies[0]).toBe(false);

    fireEvent.click(screen.getByTestId("excel-now-use"));
    await waitFor(() => expect(screen.getByTestId("excel-now-strip")).toHaveAttribute("data-on", "true"));
    await send(el, "fix this");
    await answered("second answer");
    expect(H.stream.bodies[1].excel_now).toEqual({ workbook: "Budget.xlsx", sheet: "Sales", selection: "B4:B9" });

    fireEvent.click(screen.getByTestId("excel-now-stop"));
    await send(el, "and now?");
    await answered("third answer");
    expect("excel_now" in H.stream.bodies[2]).toBe(false);
  });

  it("shows no line and sends nothing while Excel is not running", async () => {
    X.excelOpen = false;
    window.sessionStorage.setItem("ij.excel.use", "1"); // switched on earlier in this window
    H.stream.replies.push("plain answer");
    render(<ChatPage />);
    const el = await box();
    await send(el, "hello");
    await answered("plain answer");
    expect(screen.queryByTestId("excel-now-strip")).toBeNull();
    expect("excel_now" in H.stream.bodies[0]).toBe(false);
  });
});
