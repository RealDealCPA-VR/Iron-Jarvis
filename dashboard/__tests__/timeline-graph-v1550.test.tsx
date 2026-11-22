/**
 * v1.550.0: THE GRAPH — the Timeline tab's first view (components/harness/
 * HarnessGraph + lib/buildGraph), built from the Timeline's own data.
 *
 *  - a reply that worked alone is ONE node; helpers that ran together share a
 *    row; a helper that started after the others ended starts a new row,
 *    handed its work by the lead's next node;
 *  - NOTHING is drawn that did not happen: a reply that ended before the lead
 *    took its helpers' reports up draws no return and no report edge;
 *  - a node's state is told in WORDS beside its glow (a dot alone is never
 *    the only cue); the lead waits (yellow, "Waiting for helpers") while its
 *    helpers work;
 *  - hover or focus on a node shows its numbers, on an edge the words that
 *    went across (the task, the report); a press opens the node's steps on
 *    the time axis (only a helper's own, for a helper), "All models" or Esc
 *    brings the graph back, and the graph keeps updating meanwhile;
 *  - a handoff lights its edge for FRESH_MS, live only; nodes never move
 *    while a reply runs;
 *  - the run log's words, never "trace", "span", "orchestrator", "LLM",
 *    "round".
 */
import React from "react";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { HarnessTimeline, type LiveTimelineHandle } from "@/components/harness/HarnessTimeline";
import { HarnessGraph, wherePlace, effortWord } from "@/components/harness/HarnessGraph";
import {
  FRESH_MS,
  edgeFresh,
  graphLayout,
  graphNodes,
  helperWaves,
  replyGraph,
  shortDuration,
  type ReplyGraph,
} from "@/lib/buildGraph";
import {
  createLiveTimeline,
  replyTimeline,
  type BarState,
  type TimelineCall,
  type TimelineHelper,
  type TimelineReply,
  type TimelineStep,
} from "@/lib/buildTimeline";
import { decodeMessage, type BuildMessage, type BuildSessionView } from "@/lib/buildSessions";
import { readableCopy } from "./helpers/retiredWords";

const FIXTURE = JSON.parse(
  readFileSync(path.join(__dirname, "fixtures", "build-timeline-v1390.json"), "utf8").replace(/\r\n/g, "\n"),
) as { question: Record<string, unknown>; line: Record<string, unknown>; frames: [string, Record<string, unknown>][] };
const LINE = decodeMessage(FIXTURE.line) as BuildMessage;
const QUESTION = decodeMessage(FIXTURE.question) as BuildMessage;

afterEach(() => cleanup());

/* ------------------------------------------------------------ builders */

function call(start: number, ms: number, state: BarState = "done", i = 0): TimelineCall {
  return { index: i, start, ms, provider: "anthropic", model: "claude-opus-5-5", state };
}

function step(key: string, name: string, start: number, state: BarState = "done", extra: Partial<TimelineStep> = {}): TimelineStep {
  return { key, index: 0, name, ok: state === "failed" ? false : state === "done" ? true : null, start, ms: 10, target: null, detail: null, helper: false, check: null, state, ...extra };
}

function helper(id: string, start: number | null, end: number | null, state: BarState, extra: Partial<TimelineHelper> = {}): TimelineHelper {
  return {
    id,
    agent: "code-worker",
    task: `Task for ${id}`,
    result: state === "working" ? "" : `Report from ${id}`,
    state,
    start,
    end,
    provider: "openai",
    model: "gpt-5.6",
    reason: "",
    steps: [],
    stepCount: 0,
    session: "",
    ...extra,
  };
}

function reply(over: Partial<TimelineReply>): TimelineReply {
  return { key: "t1", running: false, state: "done", durationMs: 10_000, calls: [], steps: [], helpers: [], ...over };
}

const NAMES = { model: (_p: string, m: string) => (m === "claude-opus-5-5" ? "Opus 5.5" : m === "gpt-5.6" ? "GPT 5.6" : m === "glm-5.3-flash" ? "GLM 5.3 Flash" : m) };

/** Two helpers running together while the lead waits, 14 s in. */
function parallelRunning(now = 14_000): TimelineReply {
  return reply({
    running: true,
    state: "working",
    durationMs: now,
    calls: [call(0, 900, "done", 0)],
    steps: [step("s:0", "delegate", 950, "working", { helper: true }), step("s:1", "delegate", 960, "working", { helper: true })],
    helpers: [
      helper("a", 1000, null, "working", { steps: [step("h:a:0", "read_file", 1200, "working", { target: "app.py" })], stepCount: 1 }),
      helper("b", 1010, null, "working", { provider: "ollama", model: "glm-5.3-flash", agent: "test-worker", task: "Run the tests" }),
    ],
  });
}

/* ------------------------------------------------------------ the data */

describe("the graph is built only from what happened", () => {
  it("a reply that worked alone is ONE node, with no edges", () => {
    const g = replyGraph(reply({ calls: [call(0, 500)], steps: [step("s:0", "read_file", 600)] }));
    expect(graphNodes(g).map((n) => n.key)).toEqual(["lead"]);
    expect(g.edges).toEqual([]);
    expect(g.lead.state).toBe("done");
  });

  it("helpers that ran together share a wave; the lead WAITS; the return is not started yet", () => {
    const g = replyGraph(parallelRunning());
    expect(g.waves).toHaveLength(1);
    expect(g.waves[0].helpers.map((n) => n.key)).toEqual(["helper:a", "helper:b"]);
    expect(g.lead.state).toBe("waiting");
    expect(g.waves[0].back?.state).toBe("queued");
    const handoffs = g.edges.filter((e) => e.kind === "handoff");
    expect(handoffs.map((e) => [e.from, e.to, e.state])).toEqual([
      ["lead", "helper:a", "active"],
      ["lead", "helper:b", "active"],
    ]);
    expect(g.edges.filter((e) => e.kind === "report").map((e) => e.state)).toEqual(["pending", "pending"]);
    // A helper's activity is its running step.
    const a = g.waves[0].helpers[0];
    expect(a.state).toBe("working");
    expect(a.activity?.kind).toBe("step");
  });

  it("a helper started after the others ended is a NEW wave, handed its work by the lead's next node", () => {
    const g = replyGraph(
      reply({
        durationMs: 20_000,
        calls: [call(0, 900, "done", 0), call(5_100, 800, "done", 1), call(12_100, 700, "done", 2)],
        helpers: [helper("a", 1_000, 5_000, "done"), helper("b", 6_000, 12_000, "done")],
      }),
    );
    expect(g.waves.map((w) => w.helpers.map((n) => n.key))).toEqual([["helper:a"], ["helper:b"]]);
    expect(g.waves.map((w) => w.back?.key)).toEqual(["back:0", "back:1"]);
    expect(g.waves[0].back?.state).toBe("done");
    expect(g.waves[0].back?.calls.map((c) => c.start)).toEqual([5_100]);
    const pairs = g.edges.map((e) => `${e.kind}:${e.from}->${e.to}`);
    expect(pairs).toEqual([
      "handoff:lead->helper:a",
      "report:helper:a->back:0",
      "handoff:back:0->helper:b",
      "report:helper:b->back:1",
    ]);
  });

  it("NEVER IMPLIES A HANDOFF: a reply that ended before the lead took the report up draws no return", () => {
    const g = replyGraph(
      reply({ state: "stopped", durationMs: 6_000, calls: [call(0, 900)], helpers: [helper("a", 1_000, 5_000, "done")] }),
    );
    expect(g.waves[0].back).toBeNull();
    expect(g.edges.map((e) => e.kind)).toEqual(["handoff"]);
    expect(g.lead.state).toBe("stopped");
  });

  it("the reports are in and the lead is not asked yet: the return WAITS (receiving the handoff)", () => {
    const g = replyGraph(
      reply({ running: true, state: "working", durationMs: 5_500, calls: [call(0, 900)], helpers: [helper("a", 1_000, 5_000, "done")] }),
    );
    expect(g.waves[0].back?.state).toBe("waiting");
    expect(g.edges.find((e) => e.kind === "report")?.state).toBe("done");
  });

  it("a failed helper says so on its node and on both its edges", () => {
    const g = replyGraph(
      reply({ durationMs: 8_000, calls: [call(0, 900), call(5_050, 500)], helpers: [helper("a", 1_000, 5_000, "failed")] }),
    );
    expect(g.waves[0].helpers[0].state).toBe("failed");
    expect(g.edges.map((e) => e.state)).toEqual(["failed", "failed"]);
  });

  it("a REAL recorded reply (one helper, then the lead again): lead -> helper -> the lead's return", () => {
    const tl = replyTimeline(LINE)!;
    const g = replyGraph(tl);
    expect(g.waves).toHaveLength(1);
    expect(g.waves[0].back?.state).toBe("done");
    expect(g.edges.map((e) => [e.kind, e.state])).toEqual([
      ["handoff", "done"],
      ["report", "done"],
    ]);
    // ... and the live fold of its frames draws the same graph.
    const store = createLiveTimeline();
    store.reset(String(FIXTURE.line.turnId));
    for (const [e, d] of FIXTURE.frames) store.fold(e, d);
    const live = replyGraph(store.snapshot(tl.durationMs));
    const shape = (x: ReplyGraph) => graphNodes(x).map((n) => [n.key, n.state]);
    expect(shape(live)).toEqual(shape(g));
  });

  it("waves group by overlap only (a sequence stays a sequence)", () => {
    const hs = [helper("a", 0, 100, "done"), helper("b", 50, 200, "done"), helper("c", 200, 300, "done"), helper("d", 250, null, "working")];
    expect(helperWaves(hs, 400).map((w) => w.map((h) => h.id))).toEqual([["a", "b"], ["c", "d"]]);
  });
});

describe("a handoff lights its edge for a few seconds, live only", () => {
  it("fresh right after the handoff, not after FRESH_MS, never on a saved reply", () => {
    const soon = replyGraph(parallelRunning(1_000 + FRESH_MS - 1));
    const late = replyGraph(parallelRunning(1_000 + FRESH_MS + 50));
    const a = (g: ReplyGraph) => g.edges.find((e) => e.key === "handoff:a")!;
    expect(edgeFresh(a(soon), soon)).toBe(true);
    expect(edgeFresh(a(late), late)).toBe(false);
    const saved = { ...soon, running: false };
    expect(edgeFresh(a(soon), saved)).toBe(false);
  });
});

describe("the layout is calm", () => {
  it("no node moves while the reply runs (only time passes)", () => {
    const at = (now: number) => graphLayout(replyGraph(parallelRunning(now)), 420).boxes;
    expect(at(14_000)).toEqual(at(90_000));
  });

  it("nodes never overlap, at any width, with a wave wider than one row", () => {
    const many = reply({
      running: true,
      state: "working",
      durationMs: 9_000,
      calls: [call(0, 500)],
      helpers: ["a", "b", "c", "d", "e"].map((id, i) => helper(id, 1_000 + i, null, "working")),
    });
    for (const w of [260, 420, 700]) {
      const boxes = Object.values(graphLayout(replyGraph(many), w).boxes);
      for (let i = 0; i < boxes.length; i += 1)
        for (let j = i + 1; j < boxes.length; j += 1) {
          const p = boxes[i];
          const q = boxes[j];
          const apart = p.x + p.w <= q.x || q.x + q.w <= p.x || p.y + p.h <= q.y || q.y + q.h <= p.y;
          expect(apart, `${p.key} and ${q.key} at ${w}px`).toBe(true);
        }
      for (const b of boxes) expect(b.x + b.w).toBeLessThanOrEqual(w);
    }
  });

  it("helpers that ran together sit SIDE BY SIDE in the panel at its narrowest (~300 px)", () => {
    const boxes = graphLayout(replyGraph(parallelRunning()), 300).boxes;
    expect(boxes["helper:a"].y).toBe(boxes["helper:b"].y);
    expect(boxes["helper:a"].x + boxes["helper:a"].w).toBeLessThan(boxes["helper:b"].x);
  });

  it("short times read as words", () => {
    expect(shortDuration(12_400)).toBe("12 s");
    expect(shortDuration(3 * 60_000 + 5_000)).toBe("3 min");
    expect(shortDuration(72 * 60_000)).toBe("1 h 12 min");
  });
});

describe("where a model runs, and how much it thinks", () => {
  it("Local / Cloud from the model list, nothing when it does not say", () => {
    const models = [
      { provider: "ollama", model: "glm-5.3-flash", on_this_network: true },
      { provider: "openai", model: "gpt-5.6", kind: "api" },
    ] as never[];
    expect(wherePlace("ollama", "glm-5.3-flash", models)).toBe("Local");
    expect(wherePlace("openai", "gpt-5.6", models)).toBe("Cloud");
    expect(wherePlace("mystery", "x", models)).toBeNull();
    expect(wherePlace("mock", "demo", models)).toBeNull();
  });
  it("the level in words", () => {
    expect(effortWord("high")).toBe("High");
    expect(effortWord("")).toBeNull();
    expect(effortWord("auto")).toBeNull();
  });
});

/* ------------------------------------------------------------ the view */

const MODELS = [
  { provider: "anthropic", model: "claude-opus-5-5", label: "Opus 5.5", kind: "cli" },
  { provider: "openai", model: "gpt-5.6", label: "GPT 5.6", kind: "api" },
  { provider: "ollama", model: "glm-5.3-flash", label: "GLM 5.3 Flash", on_this_network: true },
] as never[];

function renderGraph(r: TimelineReply, onOpen = (_k: string) => {}) {
  return render(
    <div style={{ height: 600 }}>
      <HarnessGraph graph={replyGraph(r)} reply={r} names={NAMES} models={MODELS} effort="High" onOpen={onOpen} />
    </div>,
  );
}

describe("the graph view", () => {
  it("each node says its model, role, level, place, state in WORDS and its time", () => {
    renderGraph(parallelRunning());
    const nodes = screen.getAllByTestId("graph-node");
    expect(nodes.map((n) => n.getAttribute("data-key"))).toEqual(["lead", "helper:a", "helper:b", "back:0"]);
    const lead = nodes[0];
    expect(lead.getAttribute("data-state")).toBe("waiting");
    expect(lead.textContent).toContain("Opus 5.5");
    expect(lead.textContent).toContain("Lead · High · Cloud");
    expect(within(lead).getByTestId("graph-node-status").textContent).toBe("Waiting for helpers");
    expect(lead.textContent).toContain("14 s");
    const b = nodes[2];
    expect(b.textContent).toContain("GLM 5.3 Flash");
    expect(b.textContent).toContain("Local");
    expect(within(nodes[1]).getByTestId("graph-node-status").textContent).toBe("Reading app.py");
    expect(within(nodes[3]).getByText(/Waiting for the reports/)).toBeTruthy();
    // The glow is never the only cue: every node's state is in its label.
    for (const n of nodes) expect(n.getAttribute("aria-label")).toMatch(/: .+, \d/);
  });

  it("hover on a node shows its numbers; on an edge, the task handed over", () => {
    renderGraph(parallelRunning());
    const [, a] = screen.getAllByTestId("graph-node");
    fireEvent.mouseEnter(a);
    const card = screen.getByTestId("graph-card");
    expect(card.textContent).toContain("Started");
    expect(card.textContent).toContain("Reading app.py");
    fireEvent.mouseLeave(a);
    expect(screen.queryByTestId("graph-card")).toBeNull();
    const hits = screen.getAllByTestId("graph-edge-hit");
    fireEvent.mouseEnter(hits[1]);
    expect(screen.getByTestId("graph-edge-words").textContent).toBe("Run the tests");
    expect(screen.getByTestId("graph-card").textContent).toContain("Opus 5.5 → GLM 5.3 Flash");
  });

  it("an edge's report says what came back, and nothing before it came", () => {
    const r = reply({ durationMs: 8_000, calls: [call(0, 900), call(5_050, 500)], helpers: [helper("a", 1_000, 5_000, "done")] });
    renderGraph(r);
    const report = screen.getAllByTestId("graph-edge").find((e) => e.getAttribute("data-kind") === "report")!;
    fireEvent.focus(within(report as unknown as HTMLElement).getByTestId("graph-edge-hit"));
    expect(screen.getByTestId("graph-edge-words").textContent).toBe("Report from a");
  });

  it("a fresh handoff's edge is lit; an old one is not", () => {
    renderGraph(parallelRunning(1_500));
    const edges = screen.getAllByTestId("graph-edge").filter((e) => e.getAttribute("data-kind") === "handoff");
    expect(edges.map((e) => e.getAttribute("data-fresh"))).toEqual(["true", "true"]);
    cleanup();
    renderGraph(parallelRunning(60_000));
    const old = screen.getAllByTestId("graph-edge").filter((e) => e.getAttribute("data-kind") === "handoff");
    expect(old.map((e) => e.getAttribute("data-fresh"))).toEqual(["false", "false"]);
  });

  it("a press opens the node", () => {
    const opened: string[] = [];
    renderGraph(parallelRunning(), (k) => opened.push(k));
    fireEvent.click(screen.getAllByTestId("graph-node")[2]);
    expect(opened).toEqual(["helper:b"]);
  });
});

/* ------------------------------------------------------------ in the tab */

function viewOf(messages: BuildMessage[]): BuildSessionView {
  return {
    id: "s1",
    messages,
    setup: { provider: "anthropic", model: "claude-opus-5-5", reasoning: "high" },
  } as unknown as BuildSessionView;
}

function liveHandle(r: TimelineReply): LiveTimelineHandle {
  // A store that answers with this reply (the tab reads it, it never folds).
  const store = createLiveTimeline();
  const fake = { ...store, version: () => 1, turn: () => "t-live", snapshot: () => r };
  return { store: fake, running: r.running, startedAt: Date.now() - r.durationMs, turnId: "t-live" };
}

describe("the Timeline tab opens on the graph; a node opens its steps", () => {
  it("a saved reply: the graph first; the lead's press shows every step; All models and Esc go back", () => {
    render(
      <div style={{ height: 700 }}>
        <HarnessTimeline view={viewOf([QUESTION, LINE])} live={null} models={MODELS} graph />
      </div>,
    );
    expect(screen.getByTestId("timeline-graph")).toBeTruthy();
    expect(screen.queryByTestId("timeline-row")).toBeNull();
    // The header (time, cost, steps) stays above both views.
    expect(screen.getByTestId("timeline-totals")).toBeTruthy();
    fireEvent.click(screen.getAllByTestId("graph-node")[0]);
    expect(screen.queryByTestId("timeline-graph")).toBeNull();
    const kinds = screen.getAllByTestId("timeline-row").map((r) => r.getAttribute("data-kind"));
    expect(kinds).toContain("helper");
    expect(kinds.filter((k) => k === "step").length).toBeGreaterThanOrEqual(3);
    fireEvent.click(screen.getByTestId("timeline-focus-back"));
    expect(screen.getByTestId("timeline-graph")).toBeTruthy();
    fireEvent.click(screen.getAllByTestId("graph-node")[0]);
    fireEvent.keyDown(screen.getByTestId("pane-timeline"), { key: "Escape" });
    expect(screen.getByTestId("timeline-graph")).toBeTruthy();
  });

  it("a helper's press shows ONLY its own bar and steps", () => {
    render(
      <div style={{ height: 700 }}>
        <HarnessTimeline view={viewOf([QUESTION, LINE])} live={null} models={MODELS} graph />
      </div>,
    );
    const helperNode = screen.getAllByTestId("graph-node").find((n) => n.getAttribute("data-kind") === "helper")!;
    fireEvent.click(helperNode);
    const rows = screen.getAllByTestId("timeline-row");
    expect(rows[0].getAttribute("data-kind")).toBe("helper");
    expect(rows.map((r) => r.getAttribute("data-kind")).filter((k) => k === "reply")).toEqual([]);
    expect(rows.length).toBe(1 + replyTimeline(LINE)!.helpers[0].steps.length);
    // The details card still opens on a press, as before.
    fireEvent.click(within(rows[0]).getByTestId("timeline-bar"));
    expect(screen.getByTestId("timeline-card")).toBeTruthy();
    // Esc closes the card first, then the helper's steps.
    fireEvent.keyDown(screen.getByTestId("pane-timeline"), { key: "Escape" });
    expect(screen.queryByTestId("timeline-card")).toBeNull();
    expect(screen.queryByTestId("timeline-graph")).toBeNull();
    fireEvent.keyDown(screen.getByTestId("pane-timeline"), { key: "Escape" });
    expect(screen.getByTestId("timeline-graph")).toBeTruthy();
  });

  it("without `graph` the tab is the v1.390.0 axis (nothing else changes)", () => {
    render(
      <div style={{ height: 700 }}>
        <HarnessTimeline view={viewOf([QUESTION, LINE])} live={null} models={MODELS} />
      </div>,
    );
    expect(screen.queryByTestId("timeline-graph")).toBeNull();
    expect(screen.getAllByTestId("timeline-row").length).toBeGreaterThan(1);
  });

  it("LIVE: a running reply draws the graph, says Live, and a node's steps keep updating underneath", async () => {
    let r = parallelRunning(3_000);
    const handle = liveHandle(r);
    const fake = handle.store as unknown as { snapshot: () => TimelineReply };
    fake.snapshot = () => r;
    const { rerender } = render(
      <div style={{ height: 700 }}>
        <HarnessTimeline view={viewOf([])} live={handle} models={MODELS} graph />
      </div>,
    );
    expect(screen.getByTestId("timeline-live")).toBeTruthy();
    expect(screen.getAllByTestId("graph-node").map((n) => n.getAttribute("data-state"))).toEqual([
      "waiting",
      "working",
      "working",
      "queued",
    ]);
    fireEvent.click(screen.getAllByTestId("graph-node")[1]);
    expect(screen.getByTestId("timeline-focus-title").textContent).toContain("Reading app.py");
    // Helper a finishes while its steps are open: the open view follows.
    r = {
      ...r,
      durationMs: 6_000,
      helpers: [helper("a", 1_000, 5_500, "done", { steps: [step("h:a:0", "read_file", 1_200, "done", { target: "app.py" })], stepCount: 1 }), r.helpers[1]],
    };
    await act(async () => {
      rerender(
        <div style={{ height: 700 }}>
          <HarnessTimeline view={viewOf([])} live={{ ...handle }} models={MODELS} graph />
        </div>,
      );
    });
    expect(screen.getByTestId("timeline-focus-title").textContent).toContain("Done");
    fireEvent.click(screen.getByTestId("timeline-focus-back"));
    expect(screen.getAllByTestId("graph-node").map((n) => n.getAttribute("data-state"))).toEqual([
      "waiting",
      "done",
      "working",
      "queued",
    ]);
  });
});

describe("the run log's words", () => {
  const FILES = ["components/harness/HarnessGraph.tsx", "lib/buildGraph.ts", "lib/timelineWords.ts"];
  const BANNED = /\b(trace|traces|span|spans|orchestrator|orchestrators|orchestration|llm|llms|round|rounds|supervisor|agent|agents|node|nodes|graph)\b/i;

  it("never an engine word in what a person reads", () => {
    const hits: string[] = [];
    for (const f of FILES) {
      for (const piece of readableCopy(f)) if (BANNED.test(piece.text)) hits.push(`${f}:${piece.line} ${piece.text}`);
    }
    expect(hits).toEqual([]);
  });
});
