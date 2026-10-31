/**
 * v1.418.0 (fix plan C2.3 + C2.5) — a coding reply reads IN ORDER and has
 * ONE line under it.
 *
 *  C2.3 the reply is the ordered list [text, a run of steps, text, …] the
 *       frames carry — live (each tool card placed where the words were when
 *       its frame came) and settled (the same places, kept by this window
 *       while it ran; the saved words are the last round's). Each run of steps
 *       is one folded line that opens to its rows; "Worked for …" is said ONCE.
 *  C2.5 one collapsed line under a settled reply ("Worked for 2 min 14 s · 7
 *       steps · Checked: tests passed", then the receipt's "answered by …");
 *       the work line, the plan, the changed files and the next-step recap
 *       open from it — every old test id one press away.
 *
 * Driven through the REAL HarnessSession and the REAL chat stream.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

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
const DAEMON = {
  online: true,
  checking: false,
  health: { default_provider: "mock", default_model: "mock-1", providers: [] },
  refresh: () => {},
};
vi.mock("@/lib/daemon", () => ({ useDaemon: () => DAEMON }));
const MODELS = { models: [], usable: [], loading: false };
vi.mock("@/lib/useModels", () => ({ useModels: () => MODELS }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));

import { HarnessSession } from "@/components/harness/HarnessSession";
import type { SessionTerminalApi } from "@/components/harness/HarnessPanes";
import { HarnessMessageRow, replyLineParts, type HarnessRowHandlers } from "@/components/harness/HarnessMessageRow";
import { forgetReplyShapes, keepReplyShape, liveReplyItems, settledReplyItems } from "@/lib/replyItems";
import { runWords } from "@/lib/stepWords";
import type { BuildMessage } from "@/lib/buildSessions";

const terminal: SessionTerminalApi = { find: () => null, open: async () => null, render: () => <div />, close: () => {} };

function view(id: string, over: Record<string, unknown> = {}) {
  return {
    id,
    title: "Fix the cart",
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

function controlledStream() {
  let ctl!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      ctl = c;
    },
  });
  const enc = new TextEncoder();
  return {
    push: (event: string, data: unknown) => ctl.enqueue(enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)),
    end: () => {
      try {
        ctl.close();
      } catch {
        /* closed */
      }
    },
    response: () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
  };
}

let stream: ReturnType<typeof controlledStream>;

beforeEach(() => {
  localStorage.clear();
  forgetReplyShapes();
  api.calls = [];
  api.gets = {
    "/build/sessions/s1": view("s1", { running: true, turn_id: "tr1", messages: [userLine("Fix the cart total", "tr1")] }),
    "/build/accounts": new api.ApiError("no such route", 404),
  };
  stream = controlledStream();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (/\/chat\/turns\/tr1\/stream/.test(String(url))) return stream.response();
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

async function push(event: string, data: unknown) {
  await act(async () => {
    stream.push(event, data);
    await new Promise((r) => setTimeout(r, 20));
  });
}

/** Each element's place among the others (DOM order). */
function inOrder(els: Element[]): boolean {
  for (let i = 1; i < els.length; i++) {
    if (!(els[i - 1].compareDocumentPosition(els[i]) & Node.DOCUMENT_POSITION_FOLLOWING)) return false;
  }
  return true;
}

const READ = { id: "r1", name: "read_file", args: { path: "cart.py" } };
const EDIT = { id: "e1", name: "edit_file", args: { path: "cart.py" } };

/* ---------------------------------------------------------------- C2.3 */

describe("C2.3 the reply in order (pure)", () => {
  it("live: text → read → text → edit is four items in that order", () => {
    const text = "Let me look at the cart. Found the bug, fixing it. ";
    const at: Record<string, number> = { r1: 25, e1: text.length };
    const items = liveReplyItems(text, [READ, EDIT], (c) => at[c.id]);
    expect(items.map((it) => it.kind)).toEqual(["text", "steps", "text", "steps"]);
    expect(items[0]).toEqual({ kind: "text", text: "Let me look at the cart. " });
    expect(items[2]).toEqual({ kind: "text", text: "Found the bug, fixing it. " });
  });

  it("settled: the window's shape places the saved steps between the earlier words, the saved words last", () => {
    const shape = { text: "Let me look. Fixing it. ", cuts: [{ offset: 13, count: 1 }, { offset: 24, count: 1 }] };
    const saved = [
      { name: "read_file", ok: true, start: 10 },
      { name: "edit_file", ok: true, start: 900 },
      { name: "shell", ok: true, check: "finish" },
    ];
    const items = settledReplyItems("Fixed the total.", saved, shape);
    expect(items.map((it) => it.kind)).toEqual(["text", "steps", "text", "steps", "text"]);
    expect((items[1] as unknown as { steps: { name: string }[] }).steps.map((s) => s.name)).toEqual(["read_file"]);
    expect((items[3] as unknown as { steps: { name: string }[] }).steps.map((s) => s.name)).toEqual(["edit_file"]);
    expect(items[4]).toEqual({ kind: "text", text: "Fixed the total." });
  });

  it("settled with no shape (not watched here): the steps, then the saved words — never the finish check's", () => {
    const items = settledReplyItems("Done.", [{ name: "read_file" }, { name: "shell", check: "finish" }], null);
    expect(items.map((it) => it.kind)).toEqual(["steps", "text"]);
    expect((items[0] as { steps: unknown[] }).steps).toHaveLength(1);
  });

  it("a run says one phrase for one step, else its count and what it did", () => {
    expect(runWords([{ name: "read_file", ok: true, target: "cart.py" }])).toBe("Read cart.py");
    expect(runWords([{ name: "read_file", ok: true }, { name: "read_file", ok: true }, { name: "edit_file", ok: true }])).toMatch(
      /^3 steps · /,
    );
  });
});

describe("C2.3 the reply in order (live, then settled, in the session)", () => {
  it("draws text → read → text → edit in order while it runs, and keeps that order once settled", async () => {
    render(<HarnessSession threadId="s1" terminal={terminal} listRunning={false} onChanged={() => {}} onGone={() => {}} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining("/chat/turns/tr1/stream"), expect.anything()), {
      timeout: 10_000,
    });
    await push("resume", { resumed: false, running: true });
    await push("caught_up", { id: "" });
    await push("token", { text: "Let me look at the cart. " });
    await push("tool_call", { ...READ, status: "started" });
    await push("tool_call", { ...READ, status: "finished", ok: true });
    await push("token", { text: "Found the bug, fixing it. " });
    await push("tool_call", { ...EDIT, status: "started" });
    const live = screen.getByTestId("harness-live");
    const part = within(live).getByTestId("harness-live-text-part");
    const earlierRun = within(live).getByTestId("step-run");
    const lastText = within(live).getByTestId("harness-live-text");
    const current = within(live).getByTestId("live-tool-rows");
    expect(part).toHaveTextContent("Let me look at the cart.");
    expect(lastText).toHaveTextContent("Found the bug, fixing it.");
    expect(inOrder([part, earlierRun, lastText, current])).toBe(true);
    // The finished run is ONE folded line; the run it is in shows its rows.
    expect(within(earlierRun).getByTestId("step-run-toggle")).toHaveAttribute("aria-expanded", "false");
    expect(within(earlierRun).getByTestId("step-run-words")).toHaveTextContent("Read");

    // Settled: the saved reply keeps the same order (the last round's words last).
    api.gets["/build/sessions/s1"] = view("s1", {
      messages: [
        userLine("Fix the cart total", "tr1"),
        {
          role: "assistant",
          content: "Fixed the total.",
          turnId: "tr1",
          route: { provider: "mock", model: "mock-1", reason: "mock" },
          steps: [
            { name: "read_file", ok: true, ms: 40, start: 5 },
            { name: "edit_file", ok: true, ms: 60, start: 900 },
          ],
          timing: { startedAt: 1000, firstTokenAt: 1100, endedAt: 135_000 },
        },
      ],
    });
    await push("tool_call", { ...EDIT, status: "finished", ok: true });
    await push("token", { text: "Fixed the total." });
    await act(async () => {
      stream.push("done", { reply: "Fixed the total." });
      stream.end();
      await new Promise((r) => setTimeout(r, 40));
    });
    const reply = await screen.findByTestId("harness-reply", undefined, { timeout: 10_000 });
    const texts = within(reply).getAllByTestId("harness-reply-text");
    const runs = within(reply).getAllByTestId("step-run");
    expect(texts.map((t) => t.textContent?.trim())).toEqual([
      "Let me look at the cart.",
      "Found the bug, fixing it.",
      "Fixed the total.",
    ]);
    expect(runs).toHaveLength(2);
    expect(inOrder([texts[0], runs[0], texts[1], runs[1], texts[2]])).toBe(true);
    // "Worked for …" is said once — on the reply's line — and still once
    // after the line is opened.
    const worked = () => (reply.textContent?.match(/Worked for/g) ?? []).length;
    expect(worked()).toBe(1);
    fireEvent.click(within(reply).getByTestId("reply-line-toggle"));
    expect(within(reply).getByTestId("work-line")).toBeInTheDocument();
    expect(worked()).toBe(1);
  });
});

/* ---------------------------------------------------------------- C2.5 */

const h: HarnessRowHandlers = {
  onRewound: () => {},
  onBranch: () => {},
  onAttemptKept: () => {},
  onKeepGoing: () => {},
  onRetry: () => {},
  onOpenRewind: () => {},
  onOpenDocument: () => {},
};

function row(m: BuildMessage, nextRecap = "") {
  return render(
    <HarnessMessageRow
      m={m}
      index={1}
      threadId="s1"
      sessionId="build:s1"
      archived={false}
      busy={false}
      isPending={false}
      branching={null}
      newest
      carriedOn={false}
      canRewind
      prevUserAt="2026-10-31T10:00:00"
      teamRoster={{ lead: "", workers: [], reviewer: "" } as never}
      models={[]}
      health={null as never}
      provider="mock"
      nextRecap={nextRecap}
      undoFor={() => null}
      onUndo={async () => {}}
      h={h}
    />,
  );
}

const FULL: BuildMessage = {
  role: "assistant",
  content: "Added the cart page.",
  turnId: "t1",
  at: "2026-10-31T10:02:00",
  route: { provider: "mock", model: "mock-1", reason: "mock" },
  steps: [
    { name: "read_file", ok: true, ms: 40 },
    { name: "edit_file", ok: true, ms: 60 },
    { name: "shell", ok: true, ms: 900 },
  ],
  timing: { startedAt: 1000, firstTokenAt: 1100, endedAt: 135_000 },
  documents: ["C:\\work\\shop\\cart.py"],
  finish: { tests: "passed", command: "pytest", removed: 0, kept: 0, points: 2, unmet: 0, after: "" },
  plan: { steps: [{ goal: "read the cart", state: "done" }, { goal: "fix the total", state: "done" }], total: 2 },
} as unknown as BuildMessage;

describe("C2.5 one line under a settled reply", () => {
  it("says the work in one line: how long, how many steps, the check — and the receipt beside it", () => {
    expect(replyLineParts(FULL).map((p) => p.text)).toEqual(["Worked for 2 min 14 s", "3 steps", "Checked: tests passed"]);
    row(FULL, "Changed 1 file.");
    expect(screen.getAllByTestId("reply-line")).toHaveLength(1);
    const line = screen.getByTestId("reply-line");
    expect(within(line).getByTestId("reply-line-words")).toHaveTextContent("Worked for 2 min 14 s · 3 steps · Checked: tests passed");
    expect(within(line).getByTestId("turn-receipt")).toBeInTheDocument();
  });

  it("everything that stacked under the reply is ONE press away (every old id)", () => {
    row(FULL, "Changed 1 file.");
    for (const id of ["work-line", "harness-reply-plan", "reply-changes", "next-step-recap", "work-line-finish"]) {
      expect(screen.queryByTestId(id)).toBeNull();
    }
    fireEvent.click(screen.getByTestId("reply-line-toggle"));
    for (const id of ["work-line", "harness-reply-plan", "reply-changes", "next-step-recap", "work-line-finish"]) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
    expect(screen.getByTestId("next-step-recap")).toHaveTextContent("Changed 1 file.");
    expect(screen.getAllByTestId("reply-line")).toHaveLength(1);
  });

  it("failed tests and a cut-off reply are said in the warn tone on the line", () => {
    const m = {
      ...FULL,
      truncated: true,
      finish: { tests: "failed", command: "pytest", removed: 0, kept: 0, points: 0, unmet: 0, after: "" },
    } as unknown as BuildMessage;
    expect(replyLineParts(m).filter((p) => p.warn).map((p) => p.text)).toEqual(["Checked: tests failed", "cut off"]);
  });

  it("a plain answer says how long it took and nothing else", () => {
    const m = { role: "assistant", content: "Hi.", turnId: "t2", timing: { startedAt: 0, firstTokenAt: 1, endedAt: 2100 } } as unknown as BuildMessage;
    expect(replyLineParts(m).map((p) => p.text)).toEqual(["Answered in 2.1 s"]);
  });

  it("a settled reply placed by a kept shape draws its runs between its words", () => {
    keepReplyShape("t1", { text: "Looking. Editing. ", cuts: [{ offset: 9, count: 2 }, { offset: 18, count: 1 }] });
    row(FULL);
    const reply = screen.getByTestId("harness-reply");
    const items = within(reply).getByTestId("harness-reply-items").children;
    expect(Array.from(items).map((el) => el.getAttribute("data-testid"))).toEqual([
      "harness-reply-text",
      "step-run",
      "harness-reply-text",
      "step-run",
      "harness-reply-text",
    ]);
  });
});
