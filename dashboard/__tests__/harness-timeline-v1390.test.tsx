/**
 * v1.390.0 (the long-jobs plan's Phase 1): THE TIMELINE tab of a coding
 * session — components/harness/HarnessTimeline + lib/buildTimeline.
 *
 * Fed with a REAL recorded reply (fixtures/build-timeline-v1390.json, written
 * by tests/test_build_timeline_v1390.py: the session's saved line AND the
 * turn log's stored frames — three calls of the model plus the finish
 * check's, two reads that ran together, a helper, a write):
 *
 *  - the saved line and the live fold of its frames are ONE picture (a
 *    reload draws what the running reply drew);
 *  - honest numbers: the calls' costs add up to the receipt, every bar lies
 *    inside the reply, the reads that ran together overlap;
 *  - the top says time, cost, tokens, model calls, steps, failed steps, and
 *    the helpers' own cost APART ("not in the cost above"); a subscription's
 *    money says "list price";
 *  - rows: the model you talk to, its steps in the run log's words, a helper
 *    with its model and its own steps; rows fold; a press or focus opens the
 *    card (status, start, length, tokens, cost, a step's input and output —
 *    "Not kept for this step" past the first 60);
 *  - a reply from before the Timeline says so in one line; the picker shows
 *    an earlier reply;
 *  - the run log's words, never "trace", "span", "orchestrator", "LLM" or
 *    "round"; rows are windowed (a 2,000-step reply draws a handful).
 */
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { HarnessTimeline, NOT_KEPT, NO_TIMELINE, stepLabel, type LiveTimelineHandle } from "@/components/harness/HarnessTimeline";
import {
  MAX_SEGMENTS,
  callSegments,
  createLiveTimeline,
  replyTimeline,
  timelineRows,
  timelineTotals,
  type TimelineReply,
} from "@/lib/buildTimeline";
import { decodeMessage, type BuildMessage, type BuildSessionView } from "@/lib/buildSessions";
import { secondsText } from "@/components/chat/TurnReceipt";
import { readableCopy } from "./helpers/retiredWords";

const FIXTURE = JSON.parse(
  readFileSync(path.join(__dirname, "fixtures", "build-timeline-v1390.json"), "utf8").replace(/\r\n/g, "\n"),
) as { question: Record<string, unknown>; line: Record<string, unknown>; frames: [string, Record<string, unknown>][] };

const LINE = decodeMessage(FIXTURE.line) as BuildMessage;
const QUESTION = decodeMessage(FIXTURE.question) as BuildMessage;
const TIMING = FIXTURE.line.timing as { startedAt: number; endedAt: number };
const DURATION = TIMING.endedAt - TIMING.startedAt;

function viewOf(messages: BuildMessage[]): BuildSessionView {
  return {
    id: "s1",
    messages,
    setup: { provider: "anthropic", model: "claude-sonnet-4-5" },
  } as unknown as BuildSessionView;
}

/** The live fold of the recorded frames, as of the reply's end. */
function liveOf(frames = FIXTURE.frames): TimelineReply {
  const store = createLiveTimeline();
  store.reset(String(FIXTURE.line.turnId));
  for (const [e, d] of frames) store.fold(e, d);
  return store.snapshot(DURATION);
}

afterEach(() => cleanup());

/* ------------------------------------------------------------ the data */

describe("a saved reply and the live fold of its frames are ONE picture", () => {
  it("the saved line decodes to its calls, steps and helper", () => {
    const tl = replyTimeline(LINE)!;
    expect(tl).not.toBeNull();
    expect(tl.durationMs).toBe(DURATION);
    expect(tl.calls.length).toBeGreaterThanOrEqual(3);
    expect(tl.steps.map((s) => s.name)).toEqual(["read_file", "read_file", "delegate", "write_file"]);
    expect(tl.helpers).toHaveLength(1);
    expect(tl.helpers[0].steps.map((s) => s.name)).toEqual(["read_file"]);
  });

  it("the frames fold to the same calls, steps and helpers (a reload draws what the live view drew)", () => {
    const saved = replyTimeline(LINE)!;
    const live = liveOf();
    const pick = (t: TimelineReply) => ({
      calls: t.calls.map((c) => [c.start, c.ms, c.provider, c.model, c.usage, c.state]),
      steps: t.steps.map((s) => [s.name, s.start, s.ms, s.ok, s.target, s.helper, s.state, s.detail]),
      helpers: t.helpers.map((h) => [
        h.id,
        h.start,
        h.end,
        h.provider,
        h.model,
        h.usage,
        h.state,
        h.steps.map((s) => [s.name, s.start, s.ms, s.ok, s.detail]),
      ]),
    });
    expect(pick(live)).toEqual(pick(saved));
    // ... and the same rows on the axis.
    const rows = (t: TimelineReply) =>
      timelineRows({ ...t, durationMs: DURATION }, new Set()).map((r) => [r.kind, r.start, r.end, r.state]);
    expect(rows(live)).toEqual(rows(saved));
  });

  it("COST: the calls add up to the reply's receipt; the helper's cost is apart", () => {
    const tl = replyTimeline(LINE)!;
    const receipt = FIXTURE.line.usage as { cost_usd: number; input_tokens: number; output_tokens: number };
    const sum = tl.calls.reduce((n, c) => n + (c.usage?.cost_usd ?? 0), 0);
    expect(receipt.cost_usd).toBeGreaterThan(0);
    expect(Math.abs(sum - receipt.cost_usd)).toBeLessThanOrEqual(1e-6);
    const totals = timelineTotals(tl);
    expect(totals.usage?.cost_usd).toBe(receipt.cost_usd);
    expect(totals.usage?.input_tokens).toBe(tl.calls.reduce((n, c) => n + (c.usage?.input_tokens ?? 0), 0));
    // The helper's own usage is NOT in the reply's receipt.
    expect(totals.helperUsage?.cost_usd).toBeGreaterThan(0);
    expect(totals.usage?.input_tokens).toBe(receipt.input_tokens);
    // LIVE (no receipt yet): the top adds the calls up, to the same numbers.
    const live = liveOf().usage!;
    expect(Math.abs((live.cost_usd ?? 0) - receipt.cost_usd)).toBeLessThanOrEqual(1e-6);
    expect(live.input_tokens).toBe(receipt.input_tokens);
    expect(live.output_tokens).toBe(receipt.output_tokens);
  });

  it("TIME: every bar lies inside the reply; the reply bar IS its measured time", () => {
    const tl = replyTimeline(LINE)!;
    for (const r of timelineRows(tl, new Set())) {
      expect(r.start).toBeGreaterThanOrEqual(0);
      expect(r.end).toBeLessThanOrEqual(DURATION);
      expect(r.end).toBeGreaterThanOrEqual(r.start);
    }
    const head = timelineRows(tl, new Set())[0];
    expect(head.kind).toBe("reply");
    expect(head.end - head.start).toBe(DURATION);
    for (const seg of callSegments(tl.calls, DURATION)) {
      expect(seg.start).toBeGreaterThanOrEqual(0);
      expect(seg.end).toBeLessThanOrEqual(DURATION);
    }
  });

  it("PARALLEL: the reads that ran together overlap on the axis, each its own length", () => {
    const reads = replyTimeline(LINE)!.steps.filter((s) => s.name === "read_file");
    expect(reads).toHaveLength(2);
    const [a, b] = reads;
    expect(typeof a.start).toBe("number");
    expect(typeof b.start).toBe("number");
    // Started together (laid end to end, the second would start at the
    // first's end) …
    expect(Math.abs(a.start! - b.start!)).toBeLessThan(100);
    expect(a.start).toBeGreaterThan(0);
    const end = (s: (typeof reads)[number]) => s.start! + (s.ms ?? 0);
    expect(Math.max(a.start!, b.start!)).toBeLessThan(Math.min(end(a), end(b)));
    // … each its own length.
    expect(a.ms).not.toBe(b.ms);
  });

  it("a step that started a helper is drawn as the helper's row, its step under it", () => {
    const rows = timelineRows(replyTimeline(LINE)!, new Set());
    expect(rows.map((r) => r.kind)).toEqual(["reply", "step", "step", "helper", "step", "step"]);
    expect(rows[4]).toMatchObject({ kind: "step", depth: 2 });
    // Folding the helper hides its steps; folding the reply hides the rest.
    expect(timelineRows(replyTimeline(LINE)!, new Set(["helper:a1"])).map((r) => r.kind)).toEqual([
      "reply",
      "step",
      "step",
      "helper",
      "step",
    ]);
    expect(timelineRows(replyTimeline(LINE)!, new Set(["reply"])).map((r) => r.kind)).toEqual(["reply"]);
  });

  it("a reply from before the Timeline has none; junk reads as absent", () => {
    const old = { ...FIXTURE.line } as Record<string, unknown>;
    delete old.calls;
    old.steps = (old.steps as Record<string, unknown>[]).map(({ start: _s, ...rest }) => rest);
    // An older line's helper kept wall-clock stamps only (startedAt/endedAt).
    old.helpers = (old.helpers as Record<string, unknown>[]).map(({ start: _a, end: _b, ...rest }) => ({
      ...rest,
      work: (rest.work as Record<string, unknown>[]).map(({ start: _s, ...w }) => w),
    }));
    expect(replyTimeline(decodeMessage(old))).toBeNull();
    expect(
      replyTimeline(decodeMessage({ ...FIXTURE.line, calls: "junk", steps: [{ name: 3 }], helpers: [{ id: 7 }] })),
    ).toBeNull();
    expect(replyTimeline(null)).toBeNull();
  });

  it("the line decoder keeps `calls` (the daemon's LINE_KEYS)", () => {
    expect((LINE as { calls?: unknown }).calls).toEqual(FIXTURE.line.calls);
  });
});

/* ------------------------------------------------------------ the tab */

function renderSaved(messages: BuildMessage[] = [QUESTION, LINE], live: LiveTimelineHandle | null = null) {
  return render(
    <div style={{ height: 600 }}>
      <HarnessTimeline view={viewOf(messages)} live={live} models={[]} />
    </div>,
  );
}

describe("the Timeline tab draws a saved reply", () => {
  it("the top says time, cost, tokens, model calls, steps, and the helpers apart", () => {
    renderSaved();
    const top = screen.getByTestId("timeline-totals");
    const receipt = FIXTURE.line.usage as { cost_usd: number; input_tokens: number; output_tokens: number };
    expect(top).toHaveTextContent(secondsText(DURATION)!);
    expect(top).toHaveTextContent(`~$${receipt.cost_usd.toFixed(2)}`);
    expect(top).toHaveTextContent(`${receipt.input_tokens.toLocaleString("en-US")} in`);
    const calls = (FIXTURE.line.calls as unknown[]).length;
    expect(top).toHaveTextContent(`${calls} model calls · 4 steps`);
    expect(screen.getByTestId("timeline-helpers-apart")).toHaveTextContent(/1 helper: 1 step · ~\$0\.02.*not in the cost above/);
    // A metered API's money is never "list price".
    expect(top).not.toHaveTextContent("list");
  });

  it("rows: the model, each step in the run log's words, the helper with its model", () => {
    renderSaved();
    const rows = screen.getAllByTestId("timeline-row");
    const labels = rows.map((r) => r.querySelector("span[title]")?.getAttribute("title"));
    expect(labels).toEqual(["Sonnet 4.5", "Read app.py", "Read util.py", "The builder · Sonnet 4.5", "Read util.py", "Made made.py"]);
    // One bar each, and the model's calls as pieces of its bar.
    expect(screen.getAllByTestId("timeline-segment")).toHaveLength((FIXTURE.line.calls as unknown[]).length);
    // The model's bar ends in its time and cost — inside its end (it spans
    // the axis, so after it or before it would be off the axis); a short
    // early step says its time after its bar.
    const end0 = within(rows[0]).getByTestId("timeline-bar-end");
    expect(end0).toHaveTextContent(`${secondsText(DURATION)} · ~$0.04`);
    expect(end0).toHaveAttribute("data-place", "inside");
    expect(within(rows[1]).getByTestId("timeline-bar-end")).toHaveAttribute("data-place", "after");
  });

  it("a press on a step opens its card with what went in and what came out", () => {
    renderSaved();
    const bars = screen.getAllByTestId("timeline-bar");
    fireEvent.click(bars[2]); // Read util.py
    const card = screen.getByTestId("timeline-card");
    expect(within(card).getByTestId("timeline-card-title")).toHaveTextContent("Read util.py");
    expect(card).toHaveTextContent("Done");
    expect(card).toHaveTextContent(/Started.*in/);
    expect(within(card).getByTestId("step-detail-output")).toHaveTextContent("def f():");
    // Escape closes it.
    fireEvent.keyDown(card, { key: "Escape" });
    expect(screen.queryByTestId("timeline-card")).toBeNull();
  });

  it("keyboard focus on a piece of the model's bar opens that call's card", () => {
    renderSaved();
    const [first] = screen.getAllByTestId("timeline-segment");
    act(() => first.focus());
    const card = screen.getByTestId("timeline-card");
    expect(card).toHaveTextContent("Sonnet 4.5: model call 1");
    expect(card).toHaveTextContent("1,200 in · 300 out");
    expect(card).toHaveTextContent("~$0.01");
    expect(card).toHaveTextContent(/Took/);
  });

  it("the helper's card says its model, its steps and its own cost, apart", () => {
    renderSaved();
    fireEvent.click(screen.getAllByTestId("timeline-bar")[3]);
    const card = screen.getByTestId("timeline-card");
    expect(card).toHaveTextContent("The builder");
    expect(card).toHaveTextContent("Sonnet 4.5");
    expect(card).toHaveTextContent("not in the cost above");
    expect(card).toHaveTextContent("util.py returns 2.");
  });

  it("a step past the first 60 says its detail was not kept", () => {
    const line = { ...FIXTURE.line, steps: (FIXTURE.line.steps as Record<string, unknown>[]).map(({ detail: _d, ...s }) => s) };
    renderSaved([QUESTION, decodeMessage(line) as BuildMessage]);
    fireEvent.click(screen.getAllByTestId("timeline-bar")[1]);
    expect(screen.getByTestId("timeline-not-kept")).toHaveTextContent(NOT_KEPT);
  });

  it("a subscription's money says list price, as the receipt does", () => {
    const list = (u: Record<string, unknown>) => ({ ...u, list_price_equivalent: true });
    const line = {
      ...FIXTURE.line,
      usage: list(FIXTURE.line.usage as Record<string, unknown>),
      calls: (FIXTURE.line.calls as Record<string, unknown>[]).map((c) => ({ ...c, usage: list(c.usage as Record<string, unknown>) })),
    };
    renderSaved([QUESTION, decodeMessage(line) as BuildMessage]);
    expect(screen.getByTestId("timeline-totals")).toHaveTextContent(/~\$0\.04 list price/);
  });

  it("a reply from before the Timeline says so in one plain line; the picker shows another", () => {
    const old = { role: "assistant", content: "Old reply.", turnId: "old", steps: [{ name: "read_file", ok: true, ms: 5 }], timing: { startedAt: 1, endedAt: 9 } };
    const q0 = { role: "user", content: "an older question", turnId: "old" };
    renderSaved([decodeMessage(q0)!, decodeMessage(old)!, QUESTION, LINE]);
    const pick = screen.getByTestId("timeline-reply") as HTMLSelectElement;
    expect(pick.options).toHaveLength(2);
    expect(pick.options[0].text).toContain("read both files");
    expect(screen.getByTestId("timeline-totals")).toBeInTheDocument();
    fireEvent.change(pick, { target: { value: pick.options[1].value } });
    expect(screen.getByTestId("timeline-none")).toHaveTextContent(NO_TIMELINE);
    expect(screen.queryByTestId("timeline-totals")).toBeNull();
  });

  it("rows fold", () => {
    renderSaved();
    expect(screen.getAllByTestId("timeline-row")).toHaveLength(6);
    fireEvent.click(screen.getByRole("button", { name: /^Fold The builder/ }));
    expect(screen.getAllByTestId("timeline-row")).toHaveLength(5);
    fireEvent.click(screen.getByRole("button", { name: /^Fold Sonnet 4\.5/ }));
    expect(screen.getAllByTestId("timeline-row")).toHaveLength(1);
  });
});

/* ------------------------------------------------------------ the live view */

describe("the running reply, live", () => {
  it("draws the frames folded so far, says Live, and the model's open call works", () => {
    const store = createLiveTimeline();
    store.reset("tl-live");
    // Up to the second call starting (its round frame came, its result not).
    const cut = FIXTURE.frames.findIndex(([e], i) => e === "round" && i > 3);
    for (const [e, d] of FIXTURE.frames.slice(0, cut + 1)) store.fold(e, d);
    const live: LiveTimelineHandle = { store, running: true, startedAt: Date.now() - 900, turnId: "tl-live" };
    renderSaved([QUESTION], live);
    expect(screen.getByTestId("timeline-live")).toHaveTextContent("Live");
    expect((screen.getByTestId("timeline-reply") as HTMLSelectElement).value).toBe("live");
    const segs = screen.getAllByTestId("timeline-segment");
    expect(segs).toHaveLength(2);
    // The two reads, finished; the model's second call, working.
    expect(screen.getAllByTestId("timeline-row").map((r) => r.getAttribute("data-state"))).toEqual(["working", "done", "done"]);
    fireEvent.click(segs[1]);
    expect(screen.getByTestId("timeline-card")).toHaveTextContent("Working");
  });
});

/* ------------------------------------------------------------ calm and cheap */

function bigLine(n: number): BuildMessage {
  const steps = Array.from({ length: n }, (_, i) => ({
    name: i % 3 ? "read_file" : "shell",
    ok: i % 97 ? true : false,
    ms: 40,
    start: 100 + i * 50,
    target: i % 3 ? `f${i}.py` : "python",
    ...(i < 60 ? { detail: { input: `{"i": ${i}}`, output: `out ${i}` } } : {}),
  }));
  const calls = Array.from({ length: n / 2 }, (_, i) => ({
    start: 100 + i * 100,
    ms: 30,
    provider: "anthropic",
    model: "claude-sonnet-4-5",
    usage: { input_tokens: 100, output_tokens: 10, cost_usd: 0.0005 },
  }));
  return decodeMessage({
    role: "assistant",
    content: "Long work.",
    turnId: "big",
    steps,
    calls,
    usage: { input_tokens: 100 * (n / 2), output_tokens: 10 * (n / 2), cost_usd: 0.0005 * (n / 2) },
    timing: { startedAt: 1_000, endedAt: 1_000 + 200 + n * 50 },
  }) as BuildMessage;
}

describe("a reply with 2,000 steps stays usable", () => {
  it("draws only the rows in view, and the model's 1,000 calls as at most MAX_SEGMENTS pieces", () => {
    const line = bigLine(2000);
    const t0 = performance.now();
    const rows = timelineRows(replyTimeline(line)!, new Set());
    const built = performance.now() - t0;
    expect(rows).toHaveLength(2001);
    const r0 = performance.now();
    renderSaved([QUESTION, line]);
    const rendered = performance.now() - r0;
    const drawn = screen.getAllByTestId("timeline-row").length;
    expect(drawn).toBeLessThanOrEqual(80);
    expect(screen.getAllByTestId("timeline-segment").length).toBeLessThanOrEqual(MAX_SEGMENTS);
    // The numbers (printed for the report; the bound is a hang detector).
    console.log(`[timeline 2000 steps] rows built in ${built.toFixed(1)} ms; first render ${rendered.toFixed(1)} ms; ${drawn} rows drawn`);
    expect(rendered).toBeLessThan(5_000);
    // Scrolling draws the rows further down.
    const box = screen.getByTestId("timeline-rows");
    fireEvent.scroll(box, { target: { scrollTop: 1500 * 28 } });
    const after = screen.getAllByTestId("timeline-row");
    expect(after.length).toBeLessThanOrEqual(80);
    expect(after[0].querySelector("span[title]")?.getAttribute("title")).not.toBe("Sonnet 4.5");
  });

  it("the running reply's once-a-second read stays cheap at 2,000 steps", () => {
    const store = createLiveTimeline();
    store.reset("big-live");
    const f0 = performance.now();
    for (let i = 0; i < 1000; i += 1) {
      store.fold("round", { round: i, start: i * 100 });
      store.fold("model_call", { ms: 30, provider: "anthropic", model: "m", usage: { input_tokens: 1, cost_usd: 0.001 }, start: i * 100 });
      for (const k of [0, 1]) {
        const id = `c${i}-${k}`;
        store.fold("tool_call", { id, name: "read_file", status: "started", args: { path: `f${i}.py` }, start: i * 100 + 40 });
        store.fold("tool_call", { id, name: "read_file", status: "finished", ok: true, start: i * 100 + 40, ms: 20 });
      }
    }
    const folded = performance.now() - f0;
    const t0 = performance.now();
    const snap = store.snapshot(100_500);
    const rows = timelineRows(snap, new Set());
    const segs = callSegments(snap.calls, snap.durationMs);
    const read = performance.now() - t0;
    expect(rows).toHaveLength(2001);
    expect(segs.length).toBeLessThanOrEqual(MAX_SEGMENTS);
    console.log(`[timeline 2000 steps live] 5,000 frames folded in ${folded.toFixed(1)} ms; one read (snapshot + rows + pieces) ${read.toFixed(1)} ms`);
    expect(read).toBeLessThan(1_000);
  });
});

describe("the run log's words", () => {
  const FILES = ["components/harness/HarnessTimeline.tsx", "lib/buildTimeline.ts"];
  const BANNED = /\b(trace|traces|span|spans|orchestrator|orchestrators|llm|llms|round|rounds)\b/i;

  it("never 'trace', 'span', 'orchestrator', 'LLM' or 'round' in what a person reads", () => {
    const hits: string[] = [];
    for (const f of FILES) {
      for (const piece of readableCopy(f)) if (BANNED.test(piece.text)) hits.push(`${f}:${piece.line} ${piece.text}`);
    }
    expect(hits).toEqual([]);
  });

  it("the rendered tab says none of them either", () => {
    renderSaved();
    fireEvent.click(screen.getAllByTestId("timeline-segment")[0]);
    const text = document.body.textContent ?? "";
    const labels = [...document.querySelectorAll("[aria-label],[title]")]
      .map((el) => `${el.getAttribute("aria-label") ?? ""} ${el.getAttribute("title") ?? ""}`)
      .join(" ");
    expect(`${text} ${labels}`).not.toMatch(BANNED);
  });

  it("a working step says what it is doing", () => {
    expect(stepLabel({ name: "read_file", target: "app.py", state: "working" })).toBe("Reading app.py");
    expect(stepLabel({ name: "read_file", target: "app.py", state: "done" })).toBe("Read app.py");
  });
});
