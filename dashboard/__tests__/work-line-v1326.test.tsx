/**
 * Calm chat W1-5 (v1.326.0): the work folds into one quiet line; the receipt
 * folds into "answered by".
 *
 * - While a turn runs, its thinking, each tool call and the step it is on are
 *   grey ONE-LINE rows (14px icon, words, a dot, a short summary; a file name
 *   with a dotted underline). The running step spins and pulses, and both stop
 *   under reduced motion. No row is a box.
 * - When the answer lands, all of it folds under ONE line above the reply:
 *   "Worked for 3.2 s · read 2 files, ran 1 tool", with a chevron. Built from
 *   what the message stores; a reply with no process detail has no line.
 * - The receipt is the END of the reply's action row: "answered by <model>
 *   · 2 files" (the catalog's label for the model), opening onto the full
 *   receipt on its own line. Mock / failover / mismatch / low trust /
 *   "Remembered:" stay visible without a click AND without a hover: a reply
 *   whose receipt carries one keeps its row on screen.
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
import { LiveToolRows, WorkLine, argDetail, toolKind, workSummary } from "@/components/chat/WorkLine";
import { TurnReceipt, receiptWantsAttention } from "@/components/chat/TurnReceipt";
import type { ToolCard } from "@/lib/useChatStream";

const AT = "2026-10-09T08:20:00";
const ROUTE = { requested: "", provider: "claude-cli", model: "claude-opus-5-5", reason: "default" };
const STEPS = [
  { name: "read_file", ok: true, ms: 300 },
  { name: "read_file", ok: true, ms: 200 },
  { name: "excel_query", ok: true, ms: 1200 },
];
const TIMING = { startedAt: 1_000, firstTokenAt: 2_200, endedAt: 15_000 };

const THREAD = {
  id: "t1",
  title: "Q3 revenue",
  messages: [
    { role: "user", content: "Did the demo answer?", at: AT },
    {
      role: "assistant",
      content: "A scripted answer.",
      at: AT,
      route: { requested: "", provider: "mock", model: "mock", reason: "mock" },
    },
    { role: "user", content: "And a quiet one?", at: AT },
    { role: "assistant", content: "A quiet older answer.", at: AT, route: ROUTE },
    { role: "user", content: "How did the two locations do?", at: AT },
    {
      role: "assistant",
      content: "Pier 9 is closing the gap.",
      at: AT,
      route: ROUTE,
      toolsUsed: ["read_file", "excel_query"],
      steps: STEPS,
      timing: TIMING,
      thinking: "First I compare the two sheets.",
      thinkingSeconds: 4,
      documents: ["C:\\work\\summary.xlsx"],
    },
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

/** A frame or a fill: any border width, any background, any rounding. */
function boxClasses(el: Element | null | undefined): string[] {
  return classes(el).filter(
    (c) =>
      /^(border|border-[0-9])$/.test(c) ||
      /^border-[xytlr]-?[0-9]*$/.test(c) ||
      /^bg-/.test(c) ||
      /^rounded/.test(c),
  );
}

/* --------------------------------------------------------------- the words */

describe("the folded line says what the turn did, in plain words", () => {
  it("duration from the stored timing, then the counts in a fixed order", () => {
    expect(workSummary({ steps: STEPS, timing: TIMING })).toBe("Worked for 14 s · read 2 files, ran 1 tool");
  });

  it("no timing: the counts alone, capitalised", () => {
    expect(workSummary({ toolsUsed: ["web_search", "web_fetch", "web_fetch"] })).toBe(
      "Read 2 web pages, searched the web",
    );
  });

  it("stored steps win over the tool names (one per call, not per name)", () => {
    expect(workSummary({ steps: STEPS, toolsUsed: ["read_file"] })).toBe("Read 2 files, ran 1 tool");
  });

  it("a failed step is counted and said", () => {
    expect(workSummary({ steps: [{ name: "write_file", ok: false, ms: 10 }] })).toBe("Made 1 file, 1 failed");
  });

  it("thinking only: how long it thought", () => {
    expect(workSummary({ thinking: "hmm", thinkingSeconds: 4, timing: { startedAt: 0, firstTokenAt: 4000, endedAt: 6000 } })).toBe(
      "Worked for 6.0 s · thought for 4 s",
    );
  });

  it("no process detail: no line (junk entries are not detail)", () => {
    expect(workSummary({})).toBeNull();
    expect(workSummary({ steps: [{ name: " ", ok: true, ms: 1 }], toolsUsed: ["", "  "], thinking: "  " })).toBeNull();
  });

  it("a tool is known only by its EXACT name; anything else is a plain tool", () => {
    expect(toolKind("read_file")).toBe("read");
    expect(toolKind("read_file_backup")).toBe("tool");
    expect(toolKind("Read_file")).toBe("tool");
    expect(toolKind("mcp__files__scan")).toBe("tool");
  });

  it("a call names its file by its name, a search in quotes", () => {
    expect(argDetail({ path: "C:\\work\\sales\\harbor.xlsx" })).toEqual({
      text: "harbor.xlsx",
      full: "C:\\work\\sales\\harbor.xlsx",
      path: true,
    });
    expect(argDetail({ query: "q3 revenue" })?.text).toBe("“q3 revenue”");
    expect(argDetail({ nothing: 1 })).toBeNull();
  });
});

/* ----------------------------------------------------------------- the fold */

describe("one quiet line above a settled reply", () => {
  it("collapsed by default; opening it shows the thinking and one row per step", () => {
    render(
      <WorkLine steps={STEPS} timing={TIMING} thinking="First I compare the two sheets." thinkingSeconds={4} />,
    );
    const btn = screen.getByRole("button", { expanded: false });
    expect(screen.getByTestId("work-summary").textContent).toBe("Worked for 14 s · read 2 files, ran 1 tool");
    expect(screen.queryByTestId("work-rows")).toBeNull();
    // A hairline under the line, never a box.
    expect(classes(btn)).toEqual(expect.arrayContaining(["border-b", "border-white/[0.08]"]));
    expect(boxClasses(btn).filter((c) => c !== "border-b")).toEqual([]);
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    const rows = within(screen.getByTestId("work-rows"));
    expect(rows.getByTestId("thinking-summary").textContent).toBe("Thought for 4 s");
    const steps = rows.getAllByTestId("work-row");
    expect(steps.map((r) => r.textContent)).toEqual([
      "Readread_file0.3 s",
      "Readread_file0.2 s",
      "Ranexcel_query1.2 s",
    ]);
    for (const r of steps) expect(boxClasses(r)).toEqual([]);
  });

  it("a reply with no process detail draws nothing", () => {
    const { container } = render(<WorkLine />);
    expect(container.innerHTML).toBe("");
  });
});

/* ------------------------------------------------------------------ live rows */

describe("live tool calls are grey one-line rows", () => {
  const cards: ToolCard[] = [
    { id: "a", name: "read_file", status: "done", ok: true, args: { path: "C:\\work\\harbor.xlsx" }, output: "6 rows\nmore", startedAt: 1000, endedAt: 1300 },
    { id: "b", name: "write_file", status: "done", ok: false, args: { path: "C:\\work\\out.md" } },
    { id: "c", name: "mcp__files__scan", status: "running", progress: { progress: 2, total: 5, message: "reading" } },
  ];

  it("each call is one row: icon, words, a dot, its file (dotted) or its own id", () => {
    render(<LiveToolRows cards={cards} />);
    const [read, write, scan] = screen.getAllByTestId("work-row");
    expect(read.getAttribute("data-state")).toBe("done");
    expect(within(read).getByText("Read")).toBeTruthy();
    const file = within(read).getByText("harbor.xlsx");
    expect(classes(file)).toEqual(expect.arrayContaining(["decoration-dotted", "underline"]));
    expect(file.getAttribute("title")).toBe("C:\\work\\harbor.xlsx (read_file)");
    expect(within(read).getByTestId("tool-elapsed").textContent).toBe("0.3 s");
    expect(within(read).getByText("6 rows")).toBeTruthy(); // the output's first line
    expect(write.getAttribute("data-state")).toBe("failed");
    expect(within(write).getByText("failed")).toBeTruthy();
    expect(scan.getAttribute("data-state")).toBe("running");
    expect(within(scan).getByText("Running")).toBeTruthy();
    expect(within(scan).getByText("mcp__files__scan")).toBeTruthy();
    expect(within(scan).getByTestId("tool-progress").textContent).toBe("40% · reading");
    for (const r of [read, write, scan]) expect(boxClasses(r)).toEqual([]);
  });

  it("the running step spins and pulses, and both stop under reduced motion", () => {
    render(<LiveToolRows cards={cards} />);
    const scan = screen.getAllByTestId("work-row")[2];
    const spinner = scan.querySelector("svg")!;
    expect(classes(spinner)).toEqual(expect.arrayContaining(["animate-spin", "motion-reduce:animate-none"]));
    const title = within(scan).getByText("Running");
    expect(classes(title)).toEqual(expect.arrayContaining(["animate-pulse", "motion-reduce:animate-none"]));
  });
});

/* ---------------------------------------------------------------- receipt */

describe("the receipt reads 'answered by <model>' inside the action row", () => {
  it("names the model it is given; the provider when it has none", () => {
    const { unmount } = render(<TurnReceipt inline modelName="Opus 5.5" route={ROUTE} documents={["C:\\a.xlsx"]} toolsUsed={["read_file"]} />);
    expect(screen.getByTestId("turn-receipt").textContent).toBe("answered by Opus 5.5·1 tool·1 file");
    unmount();
    render(<TurnReceipt inline route={ROUTE} />);
    expect(screen.getByTestId("turn-answered-by").textContent).toBe("answered by Claude Code");
  });

  it("opens onto the full receipt on a line of its own, behind a hairline", () => {
    render(<TurnReceipt inline modelName="Opus 5.5" route={ROUTE} documents={["C:\\work\\a.xlsx"]} />);
    expect(screen.queryByTestId("turn-receipt-detail")).toBeNull();
    fireEvent.click(screen.getByTestId("turn-receipt"));
    const detail = screen.getByTestId("turn-receipt-detail");
    expect(classes(detail)).toEqual(expect.arrayContaining(["basis-full", "border-l"]));
    expect(within(detail).getByText("a.xlsx")).toBeTruthy();
  });

  it("a mock answer says so on the row, in amber, not 'answered by the demo'", () => {
    render(<TurnReceipt inline modelName="mock" route={{ provider: "mock", model: "mock", reason: "mock" }} />);
    expect(screen.getByTestId("turn-receipt").textContent).toMatch(/mock answer — no real model ran/);
    expect(screen.queryByTestId("turn-answered-by")).toBeNull();
    expect(classes(screen.getByText("mock answer — no real model ran"))).toContain("text-amber-300");
  });

  it("knows which receipts must be seen without a hover", () => {
    expect(receiptWantsAttention({ route: ROUTE })).toBe(false);
    expect(receiptWantsAttention({ route: { provider: "mock" } })).toBe(true);
    expect(receiptWantsAttention({ route: { provider: "codex-cli", reason: "failover", from: "ollama" } })).toBe(true);
    expect(receiptWantsAttention({ route: { provider: "codex-cli", requested: "claude-cli" } })).toBe(true);
    expect(receiptWantsAttention({ route: ROUTE, trust: "low" })).toBe(true);
    expect(receiptWantsAttention({ route: ROUTE, remembered: ["short answers"] })).toBe(true);
    expect(receiptWantsAttention({ route: ROUTE, deniedTools: ["shell"] })).toBe(true);
    expect(receiptWantsAttention({ route: ROUTE, adapted: { changes: ["decomposed"] } })).toBe(true);
    expect(receiptWantsAttention({ route: ROUTE, remembered: [" "], deniedTools: [""] })).toBe(false);
  });
});

/* ------------------------------------------------------------------- the page */

async function openThread() {
  window.history.replaceState({}, "", "/chat?thread=t1");
  render(<ChatPage />);
  return screen.findByText("Pier 9 is closing the gap.");
}

describe("on the chat page (calm chat W1-5)", () => {
  it("the work folds into one line ABOVE the reply; the reply's prose holds no reasoning", async () => {
    const text = await openThread();
    const reply = text.closest('[data-testid="reply"]')!;
    const work = within(reply as HTMLElement).getByTestId("work-line");
    const prose = text.closest('[data-testid="reply-prose"]')!;
    expect(work.compareDocumentPosition(prose) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(work).getByTestId("work-summary").textContent).toBe("Worked for 14 s · read 2 files, ran 1 tool");
    expect(within(prose as HTMLElement).queryByTestId("thinking-disclosure")).toBeNull();
    fireEvent.click(within(work).getByRole("button", { expanded: false }));
    expect(within(work).getByTestId("thinking-summary").textContent).toBe("Thought for 4 s");
    // Replies with no process detail have no line.
    expect(screen.getAllByTestId("work-line")).toHaveLength(1);
  });

  it("the receipt is the end of the action row and names the model by the menu's label", async () => {
    await openThread();
    const rows = screen.getAllByTestId("reply-actions");
    const newest = rows.at(-1)!;
    const receipt = within(newest).getByTestId("turn-receipt");
    // v1.326.1: the label comes from the models list, which lands AFTER the
    // thread; until then the line names the raw id. Wait for the label.
    await waitFor(() =>
      expect(within(receipt).getByTestId("turn-answered-by").textContent).toBe("answered by Opus 5.5"),
    );
    expect(receipt.textContent).toMatch(/2 tools·1 file/);
    // Expanding still gives the full receipt, exactly as before.
    fireEvent.click(receipt);
    const detail = within(newest).getByTestId("turn-receipt-detail");
    expect(within(detail).getAllByTestId("turn-step")).toHaveLength(3);
    expect(within(detail).getByText("summary.xlsx")).toBeTruthy();
  });

  it("an older reply's row hides until hover, unless its receipt carries a warning", async () => {
    await openThread();
    const [mockRow, quietRow] = screen.getAllByTestId("reply-actions");
    // The mock answer's warning is on screen without a hover or a click.
    expect(classes(mockRow)).not.toContain("opacity-0");
    expect(within(mockRow).getByText("mock answer — no real model ran")).toBeTruthy();
    // A quiet older receipt waits for a hover like the rest of its row, and
    // an open receipt keeps the row open.
    expect(classes(quietRow)).toEqual(
      expect.arrayContaining(["opacity-0", "group-hover/msg:opacity-100", "has-[[aria-expanded=true]]:opacity-100"]),
    );
  });

  it("while a turn runs, its thinking, tools and current step are grey rows above the answer", async () => {
    H.stream.hold = true;
    H.stream.live = {
      thinking: "Checking both sheets first.",
      tools: [
        { id: "c1", name: "read_file", status: "done", ok: true, args: { path: "C:\\work\\harbor.xlsx" }, startedAt: 1, endedAt: 301 },
        { id: "c2", name: "web_search", status: "running", args: { query: "pier 9 hours" } },
      ],
    };
    render(<ChatPage />);
    const el = (await screen.findByPlaceholderText(/Message Iron Jarvis/)) as HTMLTextAreaElement;
    fireEvent.change(el, { target: { value: "compare them" } });
    fireEvent.keyDown(el, { key: "Enter" });
    const live = await screen.findByTestId("live-work");
    await waitFor(() => expect(within(live).getAllByTestId("work-row")).toHaveLength(3));
    const rows = within(live).getAllByTestId("work-row");
    expect(rows.map((r) => r.getAttribute("data-state"))).toEqual(["done", "running", "running"]);
    expect(within(rows[0]).getByText("harbor.xlsx")).toBeTruthy();
    expect(within(rows[1]).getByText("Searching the web")).toBeTruthy();
    expect(within(rows[2]).getByText("Thinking…")).toBeTruthy();
    expect(within(live).getByTestId("thinking-summary").textContent).toBe("Thinking…");
    for (const r of rows) expect(boxClasses(r)).toEqual([]);
  });
});
