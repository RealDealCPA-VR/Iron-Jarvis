/**
 * v1.390.0 REVIEW of the Timeline (the long-jobs plan's Phase 1): what the
 * first cut drew wrong, each pinned red on the merged doer branch first.
 *
 *  1. The axis is the reply's OWN length (`timing.ms`, the clock every bar's
 *     offset is on). `timing.startedAt` / `endedAt` are wall-clock stamps
 *     again (the reply's Changes window reads the journal by them), and a
 *     wall clock that stepped mid-reply must not stretch the axis.
 *  2. A reply written since the Timeline that ended before any work started
 *     (an error or a Stop before the model was first asked) is never said to
 *     have been "made before Iron Jarvis kept the time of each step".
 *  3. A check Iron Jarvis ran itself — the finish check's test run, a
 *     project check — is told apart from the model's own work: its row says
 *     so, its card says the model did not ask for it, and the top counts it
 *     apart from the reply's steps.
 *  4. CALM (the v1.250.0 rule, counted in RENDERS, not reads): word frames
 *     never re-run the Timeline tab — the session hands it the SAME props
 *     (its live handle changes only when the reply starts, ends or is
 *     rejoined).
 */
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
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
  return { ApiError, gets: {} as Record<string, unknown> };
});
const renders = vi.hoisted(() => ({ n: 0 }));

// How often the Timeline tab's body RUNS: a memo with the tab's own shallow
// rule, counting each time the session hands it props that changed. (The
// session itself redraws on each frame through useChatStream's
// `lastEventAt` — v1.246.0, outside this phase; the tab must not.)
vi.mock("@/components/harness/HarnessTimeline", async (importOriginal) => {
  const mod = await importOriginal<typeof import("@/components/harness/HarnessTimeline")>();
  const Real = mod.HarnessTimeline;
  const Counted = React.memo(function Counted(props: React.ComponentProps<typeof Real>) {
    renders.n += 1;
    return <Real {...props} />;
  });
  return { ...mod, HarnessTimeline: Counted };
});

vi.mock("@/lib/api", () => {
  const answer = (method: string, p: string) => {
    const hit = method === "GET" ? api.gets[p] : undefined;
    if (hit instanceof Error) return Promise.reject(hit);
    if (hit === undefined && method === "GET") return Promise.reject(new api.ApiError(`unmocked ${p}`, 404));
    return Promise.resolve(hit ?? {});
  };
  return {
    ApiError: api.ApiError,
    API_BASE: "http://d",
    ijToken: () => "",
    flattenDetail: (d: unknown) => String(d),
    get: (p: string) => answer("GET", p),
    post: (p: string) => answer("POST", p),
    put: (p: string) => answer("PUT", p),
    patch: (p: string) => answer("PATCH", p),
    del: (p: string) => answer("DELETE", p),
  };
});
vi.mock("@/lib/daemon", () => ({
  useDaemon: () => ({
    online: true,
    checking: false,
    health: { default_provider: "anthropic", default_model: "claude-sonnet-4-5", providers: [] },
    refresh: () => {},
  }),
}));
// The catalog as useApi holds it once loaded: the SAME list each render.
const catalog = vi.hoisted(() => ({ models: [] as unknown[], usable: [] as unknown[], loading: false }));
vi.mock("@/lib/useModels", () => ({ useModels: () => catalog }));
vi.mock("@/components/terminal/FilesPanel", () => ({ FilesPanel: () => <div data-testid="files-panel" /> }));

import { HarnessSession } from "@/components/harness/HarnessSession";
import { HarnessTimeline } from "@/components/harness/HarnessTimeline";
import { createLiveTimeline, replyTimeline, timelineRows, timelineTotals } from "@/lib/buildTimeline";
import { decodeMessage, type BuildMessage, type BuildSessionView } from "@/lib/buildSessions";

const FIXTURE = JSON.parse(
  readFileSync(path.join(__dirname, "fixtures", "build-timeline-v1390.json"), "utf8").replace(/\r\n/g, "\n"),
) as { question: Record<string, unknown>; line: Record<string, unknown>; frames: [string, Record<string, unknown>][] };

/** The words this review added (kept literal here, so the pins fail on the
 *  first cut by what it drew, not by a missing export). */
const EARLY = "It ended before any work started.";
const BEFORE = "It was made before Iron Jarvis kept the time of each step.";
const CHECK_NOTE = "Iron Jarvis ran this itself, to check the reply's work. The model did not ask for it.";

function viewOf(messages: unknown[]): BuildSessionView {
  return {
    id: "s1",
    messages: messages.map((m) => decodeMessage(m)).filter(Boolean),
    setup: { provider: "anthropic", model: "claude-sonnet-4-5" },
  } as unknown as BuildSessionView;
}

beforeEach(() => {
  api.gets = {};
  renders.n = 0;
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/* ------------------------------------------------------------ 1. the axis */

describe("the axis is the reply's own length", () => {
  it("a wall clock that stepped mid-reply never stretches the axis", () => {
    const line = decodeMessage({
      role: "assistant",
      content: "Done.",
      turnId: "t-step",
      calls: [{ start: 100, ms: 400, provider: "anthropic", model: "claude-sonnet-4-5", usage: { input_tokens: 10, output_tokens: 2 } }],
      steps: [{ name: "read_file", ok: true, start: 520, ms: 300, target: "app.py" }],
      // The wall clock stepped 6 s forward while it worked: its stamps are
      // 7.5 s apart, the reply itself took 1.5 s.
      timing: { startedAt: 1_000, firstTokenAt: 8_000, endedAt: 8_500, ms: 1_500 },
    }) as BuildMessage;
    const tl = replyTimeline(line)!;
    expect(tl.durationMs).toBe(1_500);
    expect(timelineTotals(tl).durationMs).toBe(1_500);
    const rows = timelineRows(tl, new Set());
    expect(rows[0].end).toBe(1_500);
  });
});

/* ------------------------------------------------------------ 2. no timeline: why */

describe("a reply with no timeline says WHY truthfully", () => {
  const question = { role: "user", content: "go", turnId: "t-early" };

  it("a reply written since the Timeline that ended before any work: it ended early", () => {
    const early = {
      role: "assistant",
      content: "",
      turnId: "t-early",
      error: "Iron Jarvis couldn't read the folder.",
      timing: { startedAt: 1_000, firstTokenAt: null, endedAt: 1_040, ms: 40 },
    };
    render(<HarnessTimeline view={viewOf([question, early])} live={null} />);
    const none = screen.getByTestId("timeline-none");
    expect(none).toHaveTextContent(EARLY);
    expect(none).not.toHaveTextContent(BEFORE);
  });

  it("a reply from before the Timeline (no `timing.ms`): made before", () => {
    const old = { role: "assistant", content: "Old.", turnId: "t-early", steps: [{ name: "read_file", ok: true, ms: 5 }], timing: { startedAt: 1, endedAt: 9 } };
    render(<HarnessTimeline view={viewOf([question, old])} live={null} />);
    expect(screen.getByTestId("timeline-none")).toHaveTextContent(BEFORE);
  });
});

/* ------------------------------------------------------------ 3. the checks */

/** The recorded reply plus the finish check's own test run after its write. */
function withFinishCheck(): Record<string, unknown> {
  const line = FIXTURE.line as { steps: Record<string, unknown>[]; timing: { ms: number } };
  const last = line.steps[line.steps.length - 1] as { start: number; ms: number };
  const start = last.start + last.ms + 5;
  return {
    ...FIXTURE.line,
    steps: [
      ...line.steps,
      { name: "shell", ok: false, ms: 120, start, target: "python", check: "finish",
        detail: { input: '{"command": "python -m unittest"}', output: "FAILED (failures=1)" } },
    ],
    timing: { ...line.timing, ms: Math.max(line.timing.ms, start + 130) },
  };
}

describe("a check Iron Jarvis ran is told apart from the model's own work", () => {
  it("its row says it is the finish check; the top counts it apart from the steps", () => {
    const line = withFinishCheck();
    const tl = replyTimeline(decodeMessage(line) as BuildMessage)!;
    const own = (FIXTURE.line.steps as unknown[]).length;
    const totals = timelineTotals(tl) as unknown as Record<string, number>;
    expect(totals.steps).toBe(own);
    render(<HarnessTimeline view={viewOf([FIXTURE.question, line])} live={null} />);
    const labels = screen.getAllByTestId("timeline-row").map((r) => r.querySelector("span[title]")?.getAttribute("title"));
    expect(labels).toContain("Finish check · Ran python");
    expect(labels).not.toContain("Ran python");
    const top = screen.getByTestId("timeline-totals");
    expect(top).toHaveTextContent(`${own} steps · 1 check · 1 failed`);
  });

  it("its card says the model did not ask for it", () => {
    render(<HarnessTimeline view={viewOf([FIXTURE.question, withFinishCheck()])} live={null} />);
    const bar = screen
      .getAllByTestId("timeline-bar")
      .find((b) => (b.getAttribute("aria-label") ?? "").startsWith("Finish check"))!;
    expect(bar).toBeTruthy();
    fireEvent.click(bar);
    const card = screen.getByTestId("timeline-card");
    expect(within(card).getByTestId("timeline-card-title")).toHaveTextContent("Finish check · Ran python");
    expect(card).toHaveTextContent(CHECK_NOTE);
  });

  it("live: a project check's frames fold to a marked step, the same as its saved line", () => {
    const store = createLiveTimeline();
    store.reset("t-live");
    store.fold("tool_call", { id: "ij-check-1", name: "shell", status: "started", start: 10, check: "project", args: { command: "ruff check made.py" } });
    store.fold("tool_call", { id: "ij-check-1", name: "shell", status: "finished", ok: true, start: 10, ms: 50, check: "project" });
    store.fold("tool_call", { id: "c2", name: "shell", status: "started", start: 70, args: { command: "ruff check made.py" } });
    const snap = store.snapshot(200);
    expect(snap.steps.map((s) => (s as unknown as { check: unknown }).check)).toEqual(["project", null]);
    expect((timelineTotals(snap) as unknown as Record<string, number>).steps).toBe(1);
  });
});

/* ------------------------------------------------------------ 4. calm */

describe("a burst of words never re-renders the Timeline tab", () => {
  it("word frames, one at a time, hand the tab its props at most the once-a-second read allows", async () => {
    let ctl!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        ctl = c;
      },
    });
    const enc = new TextEncoder();
    const push = (e: string, d: unknown) => ctl.enqueue(enc.encode(`event: ${e}\ndata: ${JSON.stringify(d)}\n\n`));
    api.gets = {
      "/build/sessions/s1": {
        id: "s1", title: "T", updated_at: "2026-10-25T10:00:00", running: false, waiting: false, archived: false,
        folder: "C:\\w", folder_name: "w", is_project: false, project_id: null, mode: "act", plan: null,
        setup: { provider: "anthropic", model: "claude-sonnet-4-5" }, messages: [], session_id: "build:s1", queued: [], turn_id: "",
      },
      "/build/accounts": new api.ApiError("no such route", 404),
    };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } })));
    render(
      <HarnessSession
        threadId="s1"
        terminal={{ find: () => null, open: async () => null, render: () => <div />, close: () => {} }}
        listRunning={false}
        onChanged={() => {}}
        onGone={() => {}}
      />,
    );
    await screen.findByTestId("harness-empty");
    fireEvent.click(screen.getByTestId("harness-pane-tab-timeline"));
    const box = screen.getByTestId("harness-input") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "go" } });
    fireEvent.keyDown(box, { key: "Enter" });
    await act(async () => {
      for (const [e, d] of FIXTURE.frames.filter(([ev]) => ev !== "done")) push(e, d);
    });
    await waitFor(() => expect(screen.getByTestId("timeline-live")).toBeInTheDocument(), { timeout: 5_000 });
    const before = renders.n;
    const t0 = Date.now();
    // Each word in its own frame (an `act` and a pause each): words that
    // arrive together would be drawn together even by a page that redraws
    // per word, and prove nothing.
    for (let i = 0; i < 50; i += 1) {
      await act(async () => {
        push("token", { text: "word " });
        await new Promise((r) => setTimeout(r, 20));
      });
    }
    const elapsed = Date.now() - t0;
    // The tab is a memo: only its once-a-second read (or a step's own frame)
    // may draw it — never a word.
    expect(renders.n - before).toBeLessThanOrEqual(Math.ceil(elapsed / 1000) + 2);
  });
});
