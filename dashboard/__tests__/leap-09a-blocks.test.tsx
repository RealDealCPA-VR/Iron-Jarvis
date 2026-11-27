/**
 * Idea 09, phase 09-a: "If" and "For each", visible on the canvas.
 *
 * A workflow made in chat with blocks loads onto the canvas as BOXES with
 * their steps inside (container nodes with `parentId`): a For each holds
 * one row of steps, an If two lanes, Then and Otherwise. Selecting a box
 * opens its own panel (a three-field check for an If; what to work through
 * and the limits for a For each), Add step puts a step inside, and Save
 * sends the same nested steps back. Run history and the canvas's live strip
 * show "14 of 14 done" and a row per item; an If says the lane it took. The
 * chat's draft card lists the steps inside and, for a For each with an
 * Agent step, says it costs one model run per item (D-09.1).
 *
 * Old workflows load unchanged: every starter goes through the canvas and
 * comes back byte-identical.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import type { Connection, Edge, Node } from "@xyflow/react";

const { getMock, postMock } = vi.hoisted(() => ({ getMock: vi.fn(), postMock: vi.fn() }));

vi.mock("@/lib/api", () => {
  class MockApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
      this.name = "ApiError";
    }
  }
  return {
    ApiError: MockApiError,
    get: getMock,
    post: postMock,
    patch: vi.fn(async () => ({})),
    del: vi.fn(async () => ({})),
    put: vi.fn(async () => ({})),
    API_BASE: "",
    ijToken: () => "",
  };
});
vi.mock("@/lib/useApi", () => ({
  useApi: () => ({ data: null, error: null, loading: false, reload: () => {} }),
}));
vi.mock("@/components/VoiceInput", () => ({
  VoiceInput: () => null,
  appendDictation: (text: string, chunk: string) => (text ? `${text} ${chunk}` : chunk),
}));
vi.mock("@xyflow/react", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@xyflow/react")>();
  const { createElement } = await import("react");
  return {
    ...actual,
    ReactFlow: ({ children }: { children?: ReactNode }) => createElement("div", { "data-testid": "rf-canvas" }, children),
    ReactFlowProvider: ({ children }: { children?: ReactNode }) => createElement("div", null, children),
    Background: () => null,
    Controls: () => null,
    MiniMap: () => null,
    Handle: () => null,
    useReactFlow: () => ({ fitView: async () => false }),
  };
});
vi.mock("next/link", async () => {
  const { createElement } = await import("react");
  return {
    default: ({ href, children, ...rest }: { href: string; children?: ReactNode }) =>
      createElement("a", { href, ...rest }, children),
  };
});
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import WorkflowCanvas, {
  RunProgress,
  addStepInside,
  buildGraph,
  connectionRefusal,
  namesBefore,
  runStepViews,
  stepsFromGraph,
  type CanvasStep,
} from "@/components/workflow/WorkflowCanvas";
import { EachNode } from "@/components/workflow/EachNode";
import { IfNode } from "@/components/workflow/IfNode";
import { BlockInspector } from "@/components/workflow/BlockInspector";
import { BlockRunDetail } from "@/components/workflow/BlockRunDetail";
import { EACH_COST_LINE, describeCondition, describeOver, layoutBlocks } from "@/components/workflow/blocks";
import { STARTERS, stepKindHint } from "@/components/workflow/starters";
import { stepProblemOf } from "@/lib/workflowStepProblems";
import { WorkflowDraftCard, WorkflowRunChip } from "@/components/chat/WorkflowDraftCard";
import type { WorkflowDraft, WorkflowRun } from "@/lib/types";

afterEach(() => {
  cleanup();
  getMock.mockReset();
  postMock.mockReset();
});

const DASH = join(__dirname, "..");

/** The example sentence, as chat writes it (daemon/chat_turn._sanitize_draft). */
const leaf = (name: string, over: Partial<CanvasStep> = {}): CanvasStep => ({
  name,
  agent: "builder",
  task: "",
  tool: null,
  kind: "agent",
  on_failure: "halt",
  group: null,
  args: {},
  message: "",
  ...over,
});
const block = (name: string, over: Partial<CanvasStep>): CanvasStep => ({
  name,
  agent: "builder",
  task: name,
  tool: null,
  on_failure: "halt",
  group: null,
  args: {},
  message: "",
  ...over,
});
const INTAKE: CanvasStep[] = [
  block("Each PDF", {
    kind: "for_each",
    over: { files: { folder: "C:/Intake", glob: "*.pdf" } },
    steps: [
      leaf("Read total", { kind: "tool", tool: "read_document", args: { path: "{{item}}" } }),
      block("Over 5,000?", {
        kind: "if",
        condition: { left: "{{Read total.data.total}}", op: "over", right: "5,000" },
        steps: [leaf("Ask me", { kind: "ask", message: "{{item.name}} totals {{Read total.data.total}}. File it?" })],
        else_steps: [leaf("File it", { task: "File {{item}} in the archive" })],
      }),
    ],
  }),
  leaf("Tell", { kind: "notify", message: "Intake: {{Each PDF}}" }),
];

describe("a workflow with blocks on the canvas", () => {
  it("loads as boxes with their steps inside", () => {
    const { nodes, edges } = buildGraph(INTAKE);
    const each = nodes.find((n) => n.id === "s1")!;
    expect(each.type).toBe("each");
    const inside = nodes.filter((n) => n.parentId === "s1");
    expect(inside.map((n) => (n.data as { name: string }).name)).toEqual(["Read total", "Over 5,000?"]);
    expect(inside.every((n) => n.extent === "parent" && n.draggable === false)).toBe(true);
    const iff = inside.find((n) => n.type === "if")!;
    const lanes = nodes.filter((n) => n.parentId === iff.id);
    expect(lanes.map((n) => [(n.data as { name: string }).name, (n.data as { branch: string }).branch])).toEqual([
      ["Ask me", "then"],
      ["File it", "else"],
    ]);
    // The boxes are sized to hold their steps; the Otherwise lane sits under Then.
    const d = each.data as { w: number; h: number };
    expect(d.w).toBeGreaterThan(2 * 230);
    const [ask, file] = lanes;
    expect(file.position.y).toBeGreaterThan(ask.position.y);
    // Parents come before their children (React Flow needs that).
    const at = (id: string) => nodes.findIndex((n) => n.id === id);
    for (const n of nodes) if (n.parentId) expect(at(n.parentId)).toBeLessThan(at(n.id));
    // The next step starts after the box; inside steps are joined for the eye only.
    const tell = nodes.find((n) => n.id === "s2")!;
    expect(tell.position.x).toBeGreaterThanOrEqual(each.position.x + d.w);
    const inner = edges.filter((e) => (e.data as { inner?: boolean } | undefined)?.inner);
    expect(inner.length).toBe(1);
    expect(inner.every((e) => e.selectable === false && e.deletable === false)).toBe(true);
    expect(edges.filter((e) => !(e.data as { inner?: boolean } | undefined)?.inner).map((e) => `${e.source}->${e.target}`)).toEqual([
      "trigger->s1",
      "s1->s2",
    ]);
  });

  it("saves back exactly the nested steps it loaded", () => {
    const { nodes, edges } = buildGraph(INTAKE);
    expect(stepsFromGraph(nodes, edges)).toEqual(INTAKE);
  });

  it("keeps every starter byte-identical (old workflows load unchanged)", () => {
    for (const s of STARTERS) {
      const { nodes, edges } = buildGraph(s.steps as CanvasStep[]);
      const back = stepsFromGraph(nodes, edges);
      expect(back.length, s.name).toBe(s.steps.length);
      back.forEach((step, i) => {
        const was = s.steps[i];
        expect(step.name).toBe(was.name);
        expect(step.kind).toBe(was.kind ?? "agent");
        expect(step).not.toHaveProperty("steps");
        expect(step).not.toHaveProperty("condition");
      });
      // A plain chain is laid out as before: a step every 280 px.
      const xs = nodes.filter((n) => n.type === "step").map((n) => n.position.x);
      expect(xs).toEqual(s.steps.map((_, i) => 320 + i * 280));
    }
  });

  it("adds a step inside a block, after the one picked or at the end, and the box grows", () => {
    const { nodes, edges } = buildGraph(INTAKE);
    const before = (nodes.find((n) => n.id === "s1")!.data as { w: number }).w;
    const once = addStepInside(nodes, "s1", "body", "step-90");
    expect((once.find((n) => n.id === "s1")!.data as { w: number }).w).toBeGreaterThan(before);
    const twice = addStepInside(once, "s1.b2", "else", "step-91");
    const thrice = addStepInside(twice, "s1", "body", "step-92", "s1.b1");
    const steps = stepsFromGraph(thrice, edges);
    // New steps are named by count, as Add step always named them.
    expect(steps[0].steps!.map((s) => s.name)).toEqual(["Read total", "Step 7", "Over 5,000?", "Step 5"]);
    expect(steps[0].steps![2].else_steps!.map((s) => s.name)).toEqual(["File it", "Step 6"]);
    // The step after the block moved along, clear of the bigger box.
    const box = thrice.find((n) => n.id === "s1")!;
    const tell = thrice.find((n) => n.id === "s2")!;
    expect(tell.position.x).toBeGreaterThanOrEqual(box.position.x + (box.data as { w: number }).w);
  });

  it("never wires a step inside a block by hand, and says how a fork works", () => {
    const { nodes, edges } = buildGraph(INTAKE);
    const conn = (source: string, target: string) =>
      ({ source, target, sourceHandle: null, targetHandle: null }) as Connection;
    expect(connectionRefusal(edges, conn("s1.b1", "s2"), nodes)).toContain("in the order shown in their box");
    const fork = connectionRefusal(edges, conn("s1", "trigger"), nodes);
    expect(fork).toContain("use an If");
    expect(fork).toContain("Parallel group");
    expect(fork).toContain("adjacent");
    expect(fork).not.toMatch(/branch|engine/i);
  });

  it("offers the values a step may read: earlier steps, and inside a For each its item", () => {
    const { nodes, edges } = buildGraph(INTAKE);
    expect(namesBefore(nodes, edges, "s1.b2")).toEqual([
      "{{item}}", "{{item.name}}", "{{index}}", "{{Read total}}", "{{Read total.data}}",
    ]);
    expect(namesBefore(nodes, edges, "s2")).toEqual(["{{Each PDF}}", "{{Each PDF.data}}"]);
  });
});

describe("the boxes and their panel", () => {
  const props = (data: Record<string, unknown>) =>
    ({ id: "x", data, selected: false, type: "each", dragging: false, zIndex: 0, isConnectable: true,
       positionAbsoluteX: 0, positionAbsoluteY: 0 }) as unknown as Parameters<typeof EachNode>[0];

  it("a For each says what it works through and its limit", () => {
    const laid = layoutBlocks(buildGraph(INTAKE).nodes).find((n) => n.id === "s1")!;
    render(<EachNode {...props(laid.data as Record<string, unknown>)} />);
    const box = screen.getByTestId("each-node");
    expect(box.textContent).toContain("Each PDF");
    expect(box.textContent).toContain("For each");
    expect(box.textContent).toContain("every *.pdf in C:/Intake · at most 200 items");
  });

  it("an If says what it checks and shows its two lanes", () => {
    const { nodes } = buildGraph(INTAKE);
    const iff = nodes.find((n) => n.id === "s1.b2")!;
    render(<IfNode {...props(iff.data as Record<string, unknown>)} />);
    const box = screen.getByTestId("if-node");
    expect(box.textContent).toContain("If {{Read total.data.total}} is over 5,000");
    expect(screen.getByTestId("if-lane-then").textContent).toBe("Then");
    expect(screen.getByTestId("if-lane-else").textContent).toBe("Otherwise");
  });

  it("the If's panel is a three-field check", () => {
    const onChange = vi.fn();
    const onAddInside = vi.fn();
    render(
      <BlockInspector
        data={{ name: "Big?", kind: "if", condition: { left: "{{Read}}", op: "over", right: "5000" } }}
        onChange={onChange}
        onDelete={() => {}}
        onClose={() => {}}
        onAddInside={onAddInside}
        suggestions={["{{Read}}"]}
      />,
    );
    const builder = screen.getByTestId("condition-builder");
    fireEvent.change(within(builder).getByLabelText("The value to check"), { target: { value: "{{Read.data.total}}" } });
    expect(onChange).toHaveBeenLastCalledWith({ condition: { left: "{{Read.data.total}}", op: "over", right: "5000" } });
    fireEvent.change(within(builder).getByLabelText("Comparison"), { target: { value: "empty" } });
    // A one-sided comparison keeps no "compared with".
    expect(onChange).toHaveBeenLastCalledWith({ condition: { left: "{{Read}}", op: "empty" } });
    fireEvent.change(within(builder).getByLabelText("Compared with"), { target: { value: "7,500" } });
    expect(onChange).toHaveBeenLastCalledWith({ condition: { left: "{{Read}}", op: "over", right: "7,500" } });
    fireEvent.click(screen.getByRole("button", { name: /Add a step under Otherwise/ }));
    expect(onAddInside).toHaveBeenLastCalledWith("else");
    fireEvent.click(screen.getByRole("button", { name: /Add a step under Then/ }));
    expect(onAddInside).toHaveBeenLastCalledWith("then");
    // A block stops or skips; it never retries (the step inside does).
    const failing = screen.getByLabelText("If it fails") as HTMLSelectElement;
    expect(Array.from(failing.options).map((o) => o.value)).toEqual(["halt", "skip"]);
  });

  it("the For each's panel sets the folder and says what an Agent step inside costs", () => {
    const onChange = vi.fn();
    const onAddInside = vi.fn();
    render(
      <BlockInspector
        data={{ name: "Each PDF", kind: "for_each", over: { files: { folder: "C:/Intake", glob: "" } } }}
        onChange={onChange}
        onDelete={() => {}}
        onClose={() => {}}
        onAddInside={onAddInside}
        hasAgent
      />,
    );
    fireEvent.change(screen.getByLabelText("Only names like"), { target: { value: "*.pdf" } });
    expect(onChange).toHaveBeenLastCalledWith({ over: { files: { folder: "C:/Intake", glob: "*.pdf" } } });
    fireEvent.change(screen.getByLabelText("Items at once"), { target: { value: "3" } });
    expect(onChange).toHaveBeenLastCalledWith({ parallel: 3 });
    expect(screen.getByTestId("each-cost-line").textContent).toBe(EACH_COST_LINE);
    expect((screen.getByLabelText("At most this many items") as HTMLInputElement).value).toBe("200");
    // A folder is listed 1,000 entries at a time, so its limit stops there;
    // a list an earlier step found goes up to 2,000 (review).
    expect((screen.getByLabelText("At most this many items") as HTMLInputElement).max).toBe("1000");
    fireEvent.click(screen.getByRole("button", { name: /Add a step inside/ }));
    expect(onAddInside).toHaveBeenLastCalledWith("body");
  });

  it("a list an earlier step found may go up to 2,000 items", () => {
    render(
      <BlockInspector
        data={{ name: "Each name", kind: "for_each", over: { from: "{{List.data}}" } }}
        onChange={() => {}}
        onDelete={() => {}}
        onClose={() => {}}
        onAddInside={() => {}}
        hasAgent={false}
      />,
    );
    expect((screen.getByLabelText("At most this many items") as HTMLInputElement).max).toBe("2000");
  });
});

describe("loading, editing and saving through the real canvas", () => {
  it("a workflow from chat loads with its boxes and Save sends the same nested steps", async () => {
    getMock.mockImplementation(async () => ({ workflows: [] }));
    postMock.mockImplementation(async () => ({}));
    render(<WorkflowCanvas />);
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("ij:load-workflow", {
          detail: { name: "intake", description: "", steps: INTAKE, project_id: null },
        }),
      );
    });
    await screen.findByDisplayValue("intake");
    // "2 steps": a block counts as one of the workflow's steps.
    expect(screen.getByText("2 steps")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(postMock).toHaveBeenCalled());
    const [path, body] = postMock.mock.calls[0] as [string, { steps: CanvasStep[] }];
    expect(path).toBe("/workflows");
    expect(body.steps).toEqual(INTAKE);
  });
});

describe("a run's blocks, live and in Run history", () => {
  const rows = Array.from({ length: 14 }, (_, i) => ({
    n: i + 1,
    label: `invoice-${String(i + 1).padStart(2, "0")}.pdf`,
    status: i < 6 ? "done" : i === 6 ? "failed" : "stopped",
    summary: i === 6 ? "“Read total” failed: could not read it" : `read ${i + 1}`,
  }));
  const run = (status: string, over: Record<string, unknown> = {}): WorkflowRun =>
    ({
      id: "r1",
      workflow_name: "intake",
      status,
      steps_json: JSON.stringify(INTAKE),
      outputs_json: JSON.stringify({
        // While it works the engine counts only what is settled (review note).
        "Each PDF": { status: "running", kind: "for_each", summary: "6 of 14 done, 1 failed", data: rows.slice(0, 7) },
        ...over,
      }),
    }) as unknown as WorkflowRun;

  it("the live strip says how far a For each got, and opens to a row per item", () => {
    render(<RunProgress run={run("running")} onCancel={() => {}} cancelling={false} />);
    expect(screen.getByTestId("each-progress").textContent).toBe("· 6 of 14 done, 1 failed");
    fireEvent.click(screen.getByRole("button", { name: /Each PDF/ }));
    const list = screen.getByTestId("each-rows");
    expect(within(list).getAllByRole("listitem")).toHaveLength(7);
    expect(screen.getByTestId("each-row-3").textContent).toContain("invoice-03.pdf");
    expect(screen.getByTestId("each-row-3").textContent).toContain("Done");
    expect(screen.getByTestId("each-row-7").textContent).toContain("Failed");
  });

  it("a run parked on an ask inside a block shows that block waiting on you", () => {
    const parked = {
      ...run("waiting"),
      waiting_json: JSON.stringify({ index: 0, step: "Each PDF/5/Over 5,000?/then/Ask me", question: "File it?", at: [0, "steps", 1, "steps", 0] }),
    } as WorkflowRun;
    const views = runStepViews(parked);
    expect(views.map((v) => [v.name, v.status])).toEqual([
      ["Each PDF", "waiting"],
      ["Tell", "pending"],
    ]);
  });

  it("a finished run never shows a block still working", () => {
    const views = runStepViews(run("cancelled"));
    expect(views[0].status).toBe("cancelled");
  });

  it("Run history's rows: 14 of 14 done with each item, and the lane an If took", () => {
    const outs = {
      "Each PDF": { status: "completed", summary: "14 of 14 done", data: rows.map((r) => ({ ...r, status: "done" })) },
      Check: { status: "completed", took: "else", summary: "Otherwise: “4,000” is not over “5000”." },
      "Check/else/File it": { status: "completed", summary: "filed" },
      "Check/then/Ask me": { status: "skipped" },
    };
    const { unmount } = render(<BlockRunDetail name="Each PDF" kind="for_each" outputs={outs} ended />);
    expect(screen.getAllByText("Done")).toHaveLength(14);
    expect(screen.getByTestId("each-row-14").textContent).toContain("invoice-14.pdf");
    unmount();
    render(
      <BlockRunDetail
        name="Check"
        kind="if"
        steps={[{ name: "Ask me" }]}
        elseSteps={[{ name: "File it" }]}
        outputs={outs}
        ended
      />,
    );
    const took = screen.getByTestId("if-took");
    expect(took.textContent).toContain("Took Otherwise");
    expect(took.textContent).toContain("File it");
    expect(took.textContent).toContain("filed");
    expect(took.textContent).not.toContain("Ask me");
  });

  it("the Run history row says what each block is", () => {
    expect(stepKindHint(INTAKE[0])).toBe("For each: every *.pdf in C:/Intake");
    expect(stepKindHint(INTAKE[0].steps![1])).toBe("If {{Read total.data.total}} is over 5,000");
    expect(describeOver({ from: "{{List.data}}" })).toBe("every item of {{List.data}}");
    expect(describeCondition({ any: [{ left: "{{A}}", op: "empty" }, { left: "{{B}}", op: "is", right: "x" }] })).toBe(
      "{{A}} is empty or {{B}} is x",
    );
  });

  it("the page puts each block's rows under its step (source)", () => {
    const page = readFileSync(join(DASH, "app/workflows/page.tsx"), "utf-8").replace(/\r\n/g, "\n");
    expect(page).toContain("<BlockRunDetail");
    expect(page).toContain("blockStatus(o?.status, !isLiveRun(r))");
  });
});

describe("from chat: the draft card", () => {
  it("lists the steps inside each block and says what a For each with an Agent step costs", () => {
    render(<WorkflowDraftCard draft={{ name: "intake", description: "", steps: INTAKE } as unknown as WorkflowDraft} events={[]} />);
    const inner = screen.getAllByTestId("draft-block-steps")[0];
    expect(inner.textContent).toContain("Read total");
    expect(inner.textContent).toContain("Then");
    expect(inner.textContent).toContain("Otherwise");
    expect(inner.textContent).toContain("File it");
    expect(screen.getByText(/For every \*\.pdf in C:\/Intake, at most 200/)).toBeTruthy();
    expect(screen.getByTestId("draft-each-cost").textContent).toContain(EACH_COST_LINE);
  });

  it("a run's card follows the block, not one row per item's step", async () => {
    getMock.mockImplementation(async () => ({ id: "r1", workflow_name: "intake", status: "running" }));
    const ev = (id: string, type: string, payload: Record<string, unknown>) =>
      ({ id, type, session_id: null, ts: "2026-11-26T00:00:00Z", payload });
    const { rerender } = render(<WorkflowRunChip runId="r1" name="intake" events={[]} />);
    rerender(
      <WorkflowRunChip
        runId="r1"
        name="intake"
        events={[
          ev("e3", "workflow.step_completed", { run_id: "r1", step: "Each PDF/2/Read total", block: "Each PDF", status: "completed", summary: "read" }),
          ev("e2", "workflow.step_started", { run_id: "r1", step: "Each PDF/2/Read total", block: "Each PDF" }),
          ev("e1", "workflow.step_started", { run_id: "r1", step: "Each PDF" }),
        ]}
      />,
    );
    expect(await screen.findByText("Each PDF")).toBeTruthy();
    expect(screen.queryByText("Each PDF/2/Read total")).toBeNull();
  });

  it("says nothing about cost when no Agent step is inside", () => {
    const tools: CanvasStep[] = [
      block("Each", { kind: "for_each", over: { from: "[1,2]" }, steps: [leaf("Ping", { kind: "notify", message: "{{item}}" })] }),
    ];
    render(<WorkflowDraftCard draft={{ name: "pings", description: "", steps: tools } as unknown as WorkflowDraft} events={[]} />);
    expect(screen.queryByTestId("draft-each-cost")).toBeNull();
  });

  it("says what it costs when the For each sits inside an If's lane (review)", () => {
    const nested: CanvasStep[] = [
      leaf("Check", { kind: "tool", tool: "list_folder", args: { path: "C:/Intake" } }),
      block("Any?", {
        kind: "if",
        condition: { left: "{{Check}}", op: "not_empty" },
        steps: [
          block("Each PDF", {
            kind: "for_each",
            over: { files: { folder: "C:/Intake", glob: "*.pdf" } },
            steps: [leaf("Summarize", { agent: "researcher", task: "Summarize {{item}}" })],
          }),
        ],
      }),
    ];
    render(<WorkflowDraftCard draft={{ name: "nested", description: "", steps: nested } as unknown as WorkflowDraft} events={[]} />);
    expect(screen.getByTestId("draft-each-cost").textContent).toContain(EACH_COST_LINE);
  });

  it("a step of a kind it does not know is said as one, never shown as an Agent step", () => {
    const odd: CanvasStep[] = [leaf("Each PDF", { kind: "loop" as CanvasStep["kind"], task: "Each PDF" })];
    render(<WorkflowDraftCard draft={{ name: "odd", description: "", steps: odd } as unknown as WorkflowDraft} events={[]} />);
    expect(screen.getByText("Unknown step")).toBeTruthy();
    expect(screen.queryByText("Builder")).toBeNull();
    expect(screen.queryByText("Agent")).toBeNull();
  });
});

describe("words", () => {
  it("a save refused for a step inside a block opens the block and names the step", () => {
    expect(stepProblemOf("steps[2].steps[0] (Read total): a tool step needs a tool name")).toEqual({
      index: 2,
      words: "In “Read total”: a tool step needs a tool name",
    });
    expect(stepProblemOf("steps[0].steps[1].else_steps[0] (Say): an notify step needs a message")?.index).toBe(0);
    // A step of the workflow itself reads as before.
    expect(stepProblemOf("steps[1] (List): no tool named 'x' — pick one")).toEqual({ index: 1, words: "no tool named 'x' — pick one" });
  });

  it("VOCABULARY.md names If, Then, Otherwise, For each and Wait until", () => {
    const canon = readFileSync(join(DASH, "..", "VOCABULARY.md"), "utf-8").replace(/\r\n/g, "\n");
    const row = canon.split("\n").find((l) => l.includes("**For each**"));
    expect(row).toBeTruthy();
    for (const word of ["**If**", "**Then**", "**Otherwise**", "**For each**", "**Wait until**", "**14 of 14 done**"]) {
      expect(row).toContain(word);
    }
    expect(row).toContain("branch, loop, poll");
  });

  it("edges inside a block stay out of the refusal's count", () => {
    const edges: Edge[] = [
      { id: "inner:a->b", source: "a", target: "b", data: { inner: true } },
    ];
    const nodes = [{ id: "a", position: { x: 0, y: 0 }, data: {} }] as Node[];
    expect(connectionRefusal(edges, { source: "a", target: "c", sourceHandle: null, targetHandle: null } as Connection, nodes)).toBeNull();
  });
});
