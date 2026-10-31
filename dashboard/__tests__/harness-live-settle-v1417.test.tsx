/**
 * v1.417.0 (fix plan wave 1: C2.2, C2.4, C2.7) — one coding session, driven
 * through the REAL chat stream (lib/useChatStream) against a fetch double.
 *
 *  C2.2 the reply never blinks: when the turn ends, its live block stays up
 *       until the saved reply is read back (the settle read held by hand here),
 *       and the streamed reply FOLLOWS the bottom while the reader is there —
 *       never one who scrolled up to read;
 *  C2.4 the finish check's own round (`check: "finish"` frames) never streams
 *       into the reply: its "N. met:" checklist and its test run are ONE quiet
 *       line ("Checking the work…", then the settled reply's "Checked: …");
 *  C2.7 a session keeps its half-typed words when it is left (lib/chatDrafts)
 *       and forgets them when they are sent; the caret is in the box when the
 *       session opens; the box grows with its words, up to eight lines.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const api = vi.hoisted(() => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status = 0) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    gets: {} as Record<string, unknown | ((path: string) => unknown)>,
    calls: [] as { method: string; path: string; body?: unknown }[],
  };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, path: string, body?: unknown) => {
    api.calls.push({ method, path, body });
    const hit = method === "GET" ? api.gets[path] : undefined;
    const value = typeof hit === "function" ? (hit as (p: string) => unknown)(path) : hit;
    if (value instanceof Error) return Promise.reject(value);
    if (value === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${path}`, 404));
    return Promise.resolve(value ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string, b?: unknown) => answer("POST", p, b),
    put: (p: string, b?: unknown) => answer("PUT", p, b),
    patch: (p: string, b?: unknown) => answer("PATCH", p, b),
    del: (p: string) => answer("DELETE", p),
  };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "mock", default_model: "mock-1", providers: [] },
    refresh: () => {},
  }),
}));
vi.mock("@/lib/useModels", () => ({ useModels: () => ({ models: [], usable: [], loading: false }) }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));

import { BOX_MAX_ROWS, HarnessSession, FINISH_CHECKING_WORDS, draftKeyOf } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { readDraft, writeDraft } from "@/lib/chatDrafts";

const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };

function view(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    title: "Add a cart",
    updated_at: "2026-10-31T10:00:00",
    running: false,
    waiting: false,
    archived: false,
    folder: "C:\\work\\shop",
    folder_name: "shop",
    is_project: false,
    project_id: null,
    mode: "act",
    plan: null,
    setup: {},
    messages: [],
    session_id: `build:${id}`,
    queued: [],
    turn_id: "",
    ...over,
  };
}
const userLine = (content: string, turnId: string) => ({ role: "user", content, turnId, at: "2026-10-31T10:00:00" });
const replyLine = (content: string, turnId: string, extra: Record<string, unknown> = {}) => ({
  role: "assistant",
  content,
  turnId,
  route: { provider: "mock", model: "mock-1", reason: "mock" },
  timing: { startedAt: 1, firstTokenAt: 2, endedAt: 3 },
  ...extra,
});

function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  return {
    push: (event: string, data: unknown, id?: string) =>
      ctl.enqueue(enc.encode(`${id ? `id: ${id}\n` : ""}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => {
      try {
        ctl.close();
      } catch {
        /* closed */
      }
    },
    response: (init?: RequestInit) => {
      init?.signal?.addEventListener("abort", () => {
        try {
          ctl.error(new DOMException("The operation was aborted.", "AbortError"));
        } catch {
          /* closed */
        }
      });
      return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
    },
  };
}

let stream: ReturnType<typeof controlledStream>;

beforeEach(() => {
  localStorage.clear();
  api.calls = [];
  api.gets = {
    "/build/sessions/s1": view("s1", { running: true, turn_id: "tr1", messages: [userLine("Add a cart", "tr1")] }),
    "/build/sessions/s2": view("s2"),
    "/build/accounts": new api.ApiError("no such route", 404),
  };
  stream = controlledStream();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (/\/chat\/turns\/tr1\/stream/.test(String(url))) return stream.response(init);
      return new Response("{}", { status: 200 });
    }),
  );
});

afterEach(() => {
  stream.end();
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderSession(id = "s1") {
  return render(
    <HarnessSession key={id} threadId={id} terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />,
  );
}

async function following() {
  await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/chat/turns/tr1/stream"), expect.anything()), {
    timeout: 10_000,
  });
  await act(async () => {
    stream.push("resume", { resumed: false, running: true });
    stream.push("caught_up", { id: "" });
    await new Promise((r) => setTimeout(r, 20));
  });
}

async function push(event: string, data: unknown, wait = 20) {
  await act(async () => {
    stream.push(event, data);
    await new Promise((r) => setTimeout(r, wait));
  });
}

const liveText = () => screen.queryByTestId("harness-live-text")?.textContent ?? "";

/* ---------------------------------------------------------------- C2.2 */

describe("C2.2 the reply never blinks out while its saved copy is read back", () => {
  it("keeps the live reply on screen while the settle read is held, then swaps in the saved one", async () => {
    renderSession();
    await following();
    await push("token", { text: "Added the " });
    await push("token", { text: "cart page." });
    expect(liveText()).toBe("Added the cart page.");
    // The settle read (the session read after the turn) is HELD by the test
    // and released by hand (the v1.251.1 rule: hold the async step, never
    // race a wall clock — a loaded runner answered a 400 ms timer before the
    // checks below ran). The settle loop reads up to four times; every read
    // waits on the same gate.
    let release!: () => void;
    const gate = new Promise<void>((r) => {
      release = r;
    });
    let reads = 0;
    api.gets["/build/sessions/s1"] = () => {
      reads += 1;
      return gate.then(() =>
        view("s1", { messages: [userLine("Add a cart", "tr1"), replyLine("Added the cart page.", "tr1")] }),
      );
    };
    await act(async () => {
      stream.push("done", { reply: "Added the cart page." });
      stream.end();
      await new Promise((r) => setTimeout(r, 30));
    });
    // The read went out — and while it is held the live words stay where
    // they were (checked over ~400 ms).
    await waitFor(() => expect(reads).toBeGreaterThan(0));
    for (let i = 0; i < 4; i += 1) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 100));
      });
      expect(liveText()).toBe("Added the cart page.");
      expect(screen.queryAllByTestId("harness-reply")).toHaveLength(0);
    }
    await act(async () => {
      release();
    });
    // The saved reply lands — and the live block goes in the same render.
    await waitFor(() => expect(screen.getAllByTestId("harness-reply")).toHaveLength(1), { timeout: 5_000 });
    expect(screen.getByTestId("harness-reply")).toHaveTextContent("Added the cart page.");
    expect(screen.queryByTestId("harness-live")).toBeNull();
  });

  // Review: the hold must END when the read finds no saved reply (a turn
  // stopped before its first word writes none) or cannot read the session —
  // a live block left up forever is worse than the blink it replaced.
  for (const [what, answer] of [
    ["finds no saved reply for the turn", () => view("s1", { messages: [userLine("Add a cart", "tr1")] })],
    ["cannot read the session", () => Promise.reject(new api.ApiError("offline", 0))],
  ] as const) {
    it(`lets the live block go when the settle read ${what}`, async () => {
      renderSession();
      await following();
      await push("token", { text: "Half a rep" });
      expect(liveText()).toBe("Half a rep");
      api.gets["/build/sessions/s1"] = answer;
      await act(async () => {
        stream.push("done", { reply: "Half a rep" });
        stream.end();
        await new Promise((r) => setTimeout(r, 30));
      });
      await waitFor(() => expect(screen.queryByTestId("harness-live")).toBeNull(), { timeout: 5_000 });
      expect(screen.queryAllByTestId("harness-reply")).toHaveLength(0);
    });
  }

  it("follows the bottom while the reader is there, never a reader who scrolled up", async () => {
    renderSession();
    await following();
    const el = screen.getByTestId("harness-transcript");
    Object.defineProperty(el, "clientHeight", { configurable: true, get: () => 100 });
    Object.defineProperty(el, "scrollHeight", { configurable: true, get: () => 200 + liveText().length * 2 });
    // The reader sits 40 px from the bottom: following.
    el.scrollTop = 60;
    fireEvent.scroll(el);
    for (let i = 0; i < 50; i += 1) await push("token", { text: `w${i} ` }, 5);
    await waitFor(() => expect(liveText()).toContain("w49"));
    expect(el.scrollTop).toBe(el.scrollHeight);
    // They scroll up to read: more words never drag them down.
    el.scrollTop = 10;
    fireEvent.scroll(el);
    for (let i = 50; i < 60; i += 1) await push("token", { text: `w${i} ` }, 5);
    await waitFor(() => expect(liveText()).toContain("w59"));
    expect(el.scrollTop).toBe(10);
  });
});

/* ---------------------------------------------------------------- C2.4 */

describe("C2.4 the finish check's bookkeeping never shows live", () => {
  it("keeps its checklist out of the live reply and shows one quiet line, then the Checked words", async () => {
    renderSession();
    await following();
    await push("token", { text: "Made the header blue." });
    // The finish check's round: its words, its test run.
    await push("round", { round: 3, check: "finish" });
    await push("token", { text: "\n\n1. met: the header is blue\n2. met: tests pass\n", check: "finish" });
    await push("tool_call", { id: "ij-finish-1", name: "shell", status: "started", args: { command: "pytest" }, check: "finish" });
    expect(liveText()).toBe("Made the header blue.");
    expect(document.body.textContent).not.toContain("met:");
    expect(screen.getByTestId("harness-live-finish-words")).toHaveTextContent(FINISH_CHECKING_WORDS);
    expect(screen.getByTestId("harness-live-finish-test")).toHaveTextContent("Running the tests…");
    // The check's test run is no ordinary step row.
    expect(screen.queryByTestId("live-tool-rows")).toBeNull();
    await push("tool_call", { id: "ij-finish-1", name: "shell", status: "finished", ok: true, output: "2 passed", check: "finish" });
    expect(screen.getByTestId("harness-live-finish-test")).toHaveTextContent("The tests passed");
    // The record arrives with `done`: the settled reply's own sentence, live,
    // while its saved copy is read back (held here).
    api.gets["/build/sessions/s1"] = () => new Promise(() => {});
    await act(async () => {
      stream.push("done", {
        reply: "Made the header blue.",
        finish: { tests: "passed", command: "pytest", removed: 0, kept: 0, points: 2, unmet: 0 },
      });
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(screen.getByTestId("harness-live-finish-words")).toHaveTextContent("Checked: tests passed");
    expect(document.body.textContent).not.toContain("met:");
  });

  it("an ordinary turn (no check frames) shows no finish line", async () => {
    renderSession();
    await following();
    await push("token", { text: "Done." });
    expect(liveText()).toBe("Done.");
    expect(screen.queryByTestId("harness-live-finish")).toBeNull();
  });
});

/* ---------------------------------------------------------------- C2.7 */

describe("C2.7 drafts per session, the caret in the box, a box that grows", () => {
  beforeEach(() => {
    api.gets["/build/sessions/s1"] = view("s1");
  });

  it("keeps the half-typed words when the session is left, restores them on return, forgets them once sent", async () => {
    const { rerender } = renderSession("s1");
    const box = () => screen.getByTestId("harness-input") as HTMLTextAreaElement;
    await screen.findByTestId("harness-empty");
    fireEvent.change(box(), { target: { value: "make the footer" } });
    // To another session and back.
    rerender(<HarnessSession key="s2" threadId="s2" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await waitFor(() => expect(box().value).toBe(""));
    expect(readDraft(draftKeyOf("s1"))).toBe("make the footer");
    rerender(<HarnessSession key="s1" threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await screen.findByTestId("harness-empty");
    expect(box().value).toBe("make the footer");
    // Sent: the draft is gone.
    await act(async () => {
      fireEvent.keyDown(box(), { key: "Enter" });
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(readDraft(draftKeyOf("s1"))).toBe("");
  });

  it("keeps the words when the window closes (pagehide)", async () => {
    renderSession("s1");
    await screen.findByTestId("harness-empty");
    fireEvent.change(screen.getByTestId("harness-input"), { target: { value: "half a thought" } });
    window.dispatchEvent(new Event("pagehide"));
    expect(readDraft(draftKeyOf("s1"))).toBe("half a thought");
  });

  it("opens with the caret in the box", async () => {
    writeDraft(draftKeyOf("s1"), "");
    renderSession("s1");
    await screen.findByTestId("harness-empty");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId("harness-input")));
  });

  it("never takes the caret from a field the user is typing in elsewhere", async () => {
    const other = document.createElement("input");
    document.body.appendChild(other);
    other.focus();
    renderSession("s1");
    await screen.findByTestId("harness-empty");
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(document.activeElement).toBe(other);
    other.remove();
  });

  it("grows with a pasted block, up to eight lines, and shrinks back when emptied", async () => {
    const real = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((el: Element, pseudo?: string | null) => {
      if (el instanceof HTMLTextAreaElement) {
        return { lineHeight: "24px", fontSize: "15px", paddingTop: "12px", paddingBottom: "4px" } as CSSStyleDeclaration;
      }
      return real(el, pseudo);
    });
    const proto = HTMLTextAreaElement.prototype;
    const had = Object.getOwnPropertyDescriptor(proto, "scrollHeight");
    Object.defineProperty(proto, "scrollHeight", {
      configurable: true,
      get(this: HTMLTextAreaElement) {
        return this.value ? this.value.split("\n").length * 24 + 16 : 0;
      },
    });
    try {
      renderSession("s1");
      await screen.findByTestId("harness-empty");
      const box = screen.getByTestId("harness-input") as HTMLTextAreaElement;
      fireEvent.change(box, { target: { value: "one\ntwo\nthree\nfour" } });
      expect(box.style.height).toBe(`${4 * 24 + 16}px`);
      fireEvent.change(box, { target: { value: Array.from({ length: 20 }, (_, i) => `line ${i}`).join("\n") } });
      expect(box.style.height).toBe(`${BOX_MAX_ROWS * 24 + 16}px`);
      fireEvent.change(box, { target: { value: "" } });
      expect(box.style.height).toBe("");
    } finally {
      if (had) Object.defineProperty(proto, "scrollHeight", had);
      else delete (proto as unknown as Record<string, unknown>).scrollHeight;
    }
  });
});
